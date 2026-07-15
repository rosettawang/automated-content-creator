"""Content-understanding pipeline: vision/whisper/deep-index, things/regions,
faces, motion, embeddings — plus their background-job workers.

Depends on leaves (config, db, media_files, jobs_runtime, settings) and the
model libs (claude_client, semantic, and the lazily-imported vision_lib/
motion_lib/face_lib). Never imports core or catalog, so it sits below them in
the graph. `core` re-exports everything here for `from core import *`.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import semantic
from db import get_conn, stamp_file_metadata, exiftool_available
from config import IMAGE_EXTS, VIDEO_EXTS, FACES_DIR
from settings import _use_on_device
from media_files import (
    find_media_file, _sample_frames, _measure_quality, _ensure_proxy_async,
)
from jobs_runtime import _update_job
from claude_client import (
    analyze_frame, deep_index_clip, pick_best_frame, classify_thing_kind,
)
from drive_import import probe_duration


_indexing: set[int] = set()


_indexing_lock = threading.Lock()


def _active_watchlist(conn=None) -> list[dict]:
    """Active things as dicts for injecting into the analysis prompt."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT name, kind, description FROM things WHERE active = 1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def _category_vocab(conn=None) -> list[dict]:
    """Category label set for CLIP zero-shot: the library's own labels + defaults."""
    import vision_lib
    own = conn is None
    conn = conn or get_conn()
    try:
        db_cats = [r["category"] for r in conn.execute(
            "SELECT DISTINCT category FROM clips WHERE category IS NOT NULL AND category <> ''"
        )]
    finally:
        if own:
            conn.close()
    seen, out = set(), []
    for c in db_cats + vision_lib.DEFAULT_CATEGORIES:
        k = (c or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(c.strip())
    return out


def _frame_analysis(image_bytes: bytes, watchlist: list[dict]):
    """Analyze a frame, on-device (CLIP) by default or via Claude when
    ON_DEVICE_VISION=0. Returns an object with .description/.category/.tags/
    .matched_things."""
    if _use_on_device():
        import vision_lib
        from types import SimpleNamespace
        res = vision_lib.analyze(image_bytes, watchlist=watchlist,
                                 categories=_category_vocab())
        return SimpleNamespace(**res)
    return analyze_frame(image_bytes, watchlist=watchlist)


def _norm_thing_name(s: str) -> str:
    """Normalize a thing name for matching: drop a trailing '(kind)' the model may
    have echoed, collapse case/whitespace."""
    return re.sub(r"\s*\(.*?\)\s*$", "", s or "").strip().lower()


def _record_thing_matches(conn, clip_id: int, matched_names: list[str]) -> None:
    """Link a clip to the things whose names the model reported (tolerant of case and
    a trailing kind suffix)."""
    for name in matched_names:
        norm = _norm_thing_name(name)
        if not norm:
            continue
        row = conn.execute(
            "SELECT id FROM things WHERE lower(name) = ?", (norm,)
        ).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO clip_things (clip_id, thing_id) VALUES (?, ?)",
                (clip_id, row["id"]),
            )


def _clamp01(v: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _record_regions(conn, clip_id: int, regions) -> None:
    """Replace a clip's stored regions. `regions` items may be pydantic Region
    objects or dicts with label/x/y/w/h. A region whose label matches a watched
    thing is linked to that thing so 'ask for a thing' returns its box."""
    conn.execute("DELETE FROM clip_regions WHERE clip_id = ?", (clip_id,))
    for r in regions or []:
        label = (getattr(r, "label", None) if not isinstance(r, dict) else r.get("label")) or ""
        get = (lambda k: getattr(r, k, 0.0)) if not isinstance(r, dict) else (lambda k: r.get(k, 0.0))
        x, y, w, h = _clamp01(get("x")), _clamp01(get("y")), _clamp01(get("w")), _clamp01(get("h"))
        if w <= 0 or h <= 0:
            continue
        row = conn.execute(
            "SELECT id FROM things WHERE lower(name) = ?", (_norm_thing_name(label),)
        ).fetchone()
        conn.execute(
            "INSERT INTO clip_regions (clip_id, thing_id, label, x, y, w, h) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (clip_id, row["id"] if row else None, label.strip(), x, y, w, h),
        )


_THUMB_CANDIDATES = 8


def _pick_thing_thumbnail(conn, thing_id: int, use_api: bool = True) -> int | None:
    """Choose the most flattering matched clip as a thing's cover and remember it.
    Returns the chosen clip id (or None if there's nothing local to choose from)."""
    thing = conn.execute("SELECT name FROM things WHERE id = ?", (thing_id,)).fetchone()
    if not thing:
        return None
    rows = conn.execute(
        """SELECT c.id, c.file_stem FROM clips c
           JOIN clip_things ct ON ct.clip_id = c.id
           WHERE ct.thing_id = ? ORDER BY c.id DESC""",
        (thing_id,),
    ).fetchall()
    candidates = []
    for r in rows:
        local = find_media_file(r["file_stem"])
        if local:
            candidates.append((r["id"], local))
        if len(candidates) >= _THUMB_CANDIDATES:
            break
    if not candidates:
        return None
    if len(candidates) == 1 or not use_api:
        chosen_clip_id = candidates[0][0]
    else:
        try:
            images = [_keyframe_bytes(p) for _, p in candidates]
            best = pick_best_frame(thing["name"], images)
            chosen_clip_id = candidates[best.index][0]
        except Exception:
            chosen_clip_id = candidates[0][0]
    conn.execute(
        """INSERT INTO thing_thumbs (thing_id, clip_id) VALUES (?, ?)
           ON CONFLICT(thing_id) DO UPDATE SET clip_id = excluded.clip_id""",
        (thing_id, chosen_clip_id),
    )
    conn.commit()
    return chosen_clip_id


def _keyframe_bytes(path: Path) -> bytes:
    """A downscaled JPEG keyframe for a photo or video, for vision analysis."""
    is_photo = path.suffix.lower() in IMAGE_EXTS
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.jpg"
        if is_photo:
            cmd = ["ffmpeg", "-y", "-i", str(path),
                   "-vf", "scale=768:-1", "-frames:v", "1", str(frame_path)]
        else:
            duration = probe_duration(path) or 4.0
            timestamp = min(2.0, duration / 2)
            cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(path),
                   "-frames:v", "1", str(frame_path)]
        subprocess.run(cmd, check=True, capture_output=True)
        return frame_path.read_bytes()


_SCAN_BATCH = 25


def _clip_timeline_texts(conn) -> dict[int, str]:
    """Combined lowercased searchable text per clip: its base metadata PLUS the
    deep-index scene timeline (clip_events kind='scene' text + labels). This is the
    stored substrate the "timeline-first" scan matches against before touching pixels."""
    texts: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT id, description, category, tags, transcript FROM clips"
    ):
        parts = [r[k] for k in ("description", "category", "tags", "transcript") if r[k]]
        texts[r["id"]] = list(parts)
    for e in conn.execute(
        "SELECT clip_id, label, text FROM clip_events WHERE kind = 'scene'"
    ):
        bucket = texts.setdefault(e["clip_id"], [])
        if e["text"]:
            bucket.append(e["text"])
        if e["label"]:
            bucket.append(e["label"])
    return {cid: " ".join(parts).lower() for cid, parts in texts.items() if parts}


def _thing_matches_text(thing: dict, text: str) -> bool:
    """Free, literal check: does this thing appear in a clip's stored text? True if the
    thing's full name appears as a phrase, or every significant word of its name is
    present as a whole word (so 'oil press' matches '…using the oil press…' but 'seed'
    does NOT match 'seedling'). Whole-word matching avoids substring false positives."""
    name = _norm_thing_name(thing["name"])
    if not name:
        return False
    if re.search(r"\b" + re.escape(name) + r"\b", text):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", text))
    words = [w for w in re.findall(r"[a-z0-9]+", name) if len(w) > 2]
    return bool(words) and all(w in tokens for w in words)


def _run_thing_scan_job(job_id: str, thing_ids: list[int]) -> None:
    """Re-check existing clips for the given things (or all active things) and record
    matches. TIMELINE-FIRST (the deep-index philosophy): match against each clip's
    stored text + scene timeline first -- free, instant, and covering clips whose
    media isn't local -- then fall back to pixel re-analysis (on-device CLIP) or a
    semantic text pass (cloud) only for the things the timeline didn't already find."""
    conn = get_conn()
    if thing_ids:
        placeholders = ",".join("?" * len(thing_ids))
        rows = conn.execute(
            f"SELECT id, name, kind, description FROM things WHERE id IN ({placeholders})",
            thing_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, kind, description FROM things WHERE active = 1"
        ).fetchall()
    things = [dict(r) for r in rows]
    name_to_id = {t["name"].strip().lower(): t["id"] for t in things}

    if not things:
        conn.close()
        _update_job(job_id, finished=True, phase="done",
                    results=[{"status": "error", "error": "no things to scan for"}])
        return

    hits = 0
    via_timeline = 0
    via_pixels = 0
    via_semantic = 0
    # Per clip, the set of requested things already matched -- so later passes only
    # spend effort on what the timeline missed.
    matched: dict[int, set[int]] = {}

    def _link(cid: int, name: str) -> bool:
        nonlocal hits
        tid = name_to_id.get(_norm_thing_name(name))
        if not tid:
            return False
        cur = conn.execute(
            "INSERT OR IGNORE INTO clip_things (clip_id, thing_id) VALUES (?, ?)", (cid, tid))
        matched.setdefault(cid, set()).add(tid)
        added = cur.rowcount > 0
        if added:
            hits += 1
        return added

    # ---- Pass 1: timeline-first. Free, local, instant; also covers not-local clips. ----
    clip_text = _clip_timeline_texts(conn)
    _update_job(job_id, total=len(clip_text), phase="timeline")
    for i, (cid, text) in enumerate(clip_text.items()):
        for t in things:
            if _thing_matches_text(t, text) and _link(cid, t["name"]):
                via_timeline += 1
        if i % 20 == 0:
            conn.commit()
        _update_job(job_id, done=i + 1)
    conn.commit()

    want_ids = set(name_to_id.values())

    # ---- Pass 2: fallback ONLY for clips still missing one or more requested things. ----
    if _use_on_device():
        # On-device CLIP pixels, local clips only (needs the frame).
        import vision_lib
        clip_rows = conn.execute("SELECT id, file_stem FROM clips").fetchall()
        todo = []
        for r in clip_rows:
            if want_ids <= matched.get(r["id"], set()):
                continue  # timeline already found every requested thing here
            p = find_media_file(r["file_stem"])
            if p is not None:
                todo.append((r["id"], p))
        _update_job(job_id, total=len(todo), phase="pixels", done=0)
        for i, (cid, path) in enumerate(todo):
            try:
                for name in vision_lib.detect_things(_keyframe_bytes(path), things):
                    if _link(cid, name):
                        via_pixels += 1
                conn.commit()
            except Exception:
                pass
            _update_job(job_id, done=i + 1, current=path.name)
    # NOTE: no cloud/API fallback here by design — scanning only re-reads the stored
    # index (plus free on-device CLIP above). New things also join the watchlist, so
    # FUTURE indexing looks out for them; the deep-index pass is where API spend lives.

    # Auto-pick a flattering cover for any scanned thing that now has matches but
    # no cover yet (existing covers are left alone).
    _update_job(job_id, phase="covers", current=None)
    for t in things:
        has_cover = conn.execute(
            "SELECT 1 FROM thing_thumbs WHERE thing_id = ? AND clip_id IS NOT NULL", (t["id"],)
        ).fetchone()
        if not has_cover:
            try:
                # use_api=False: scan must stay free — first candidate is fine; the
                # ★ button offers the Claude-picked "most flattering" cover on demand.
                _pick_thing_thumbnail(conn, t["id"], use_api=False)
            except Exception:
                pass

    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "scanned", "clips": len(clip_text), "new_matches": hits,
                          "via_timeline": via_timeline, "via_pixels": via_pixels,
                          "via_semantic": via_semantic}])


def _run_region_scan_job(job_id: str, only_missing: bool) -> None:
    """Locate notable subjects (and matched things) in each local clip's keyframe and
    store their normalized boxes for reframing. Only local clips can be scanned."""
    conn = get_conn()
    watchlist = _active_watchlist(conn)
    clips = conn.execute("SELECT id, file_stem FROM clips").fetchall()
    todo = []
    for r in clips:
        local = find_media_file(r["file_stem"])
        if not local:
            continue
        if only_missing and conn.execute(
            "SELECT 1 FROM clip_regions WHERE clip_id = ?", (r["id"],)
        ).fetchone():
            continue
        todo.append((r["id"], local))
    _update_job(job_id, total=len(todo), phase="locating")

    located = 0
    for i, (cid, path) in enumerate(todo):
        try:
            analysis = analyze_frame(_keyframe_bytes(path), watchlist=watchlist)
            regions = getattr(analysis, "regions", []) or []
            _record_regions(conn, cid, regions)
            conn.commit()
            located += len(regions)
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=path.name)

    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "located", "clips": len(todo), "regions": located}])


def _person_centroids(conn) -> dict[int, "np.ndarray"]:
    import numpy as np
    import face_lib
    from collections import defaultdict
    groups: dict[int, list] = defaultdict(list)
    for r in conn.execute("SELECT person_id, embedding FROM faces WHERE person_id IS NOT NULL"):
        groups[r["person_id"]].append(face_lib.emb_from_bytes(r["embedding"]))
    return {pid: np.mean(embs, axis=0) for pid, embs in groups.items()}


def _recluster_unnamed(conn) -> None:
    """Re-group all not-yet-named faces into provisional clusters."""
    import face_lib
    rows = conn.execute(
        "SELECT id, embedding FROM faces WHERE person_id IS NULL ORDER BY id"
    ).fetchall()
    # Clear stale cluster ids first (so removed faces don't leave empty groups).
    conn.execute("UPDATE faces SET cluster_id = NULL WHERE person_id IS NULL")
    if rows:
        embs = [face_lib.emb_from_bytes(r["embedding"]) for r in rows]
        labels = face_lib.cluster(embs)
        for r, lab in zip(rows, labels):
            conn.execute("UPDATE faces SET cluster_id = ? WHERE id = ?", (int(lab), r["id"]))
    conn.commit()


def _run_face_detect_job(job_id: str) -> None:
    """Detect faces in local clips not yet processed, embed + crop each, auto-match
    to known people, then re-cluster the rest."""
    import json
    import face_lib

    conn = get_conn()
    clips = conn.execute("SELECT id, file_stem FROM clips").fetchall()
    scannable = [(r["id"], find_media_file(r["file_stem"])) for r in clips]
    scannable = [(cid, p) for cid, p in scannable if p is not None]
    done = {r["clip_id"] for r in conn.execute("SELECT DISTINCT clip_id FROM faces")}
    todo = [(cid, p) for cid, p in scannable if cid not in done]

    _update_job(job_id, total=len(todo), phase="detecting")
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    centroids = _person_centroids(conn)
    face_count = 0
    for i, (cid, path) in enumerate(todo):
        try:
            for f in face_lib.detect_faces(_keyframe_bytes(path)):
                cur = conn.execute(
                    "INSERT INTO faces (clip_id, embedding, box, prob) VALUES (?, ?, ?, ?)",
                    (cid, face_lib.emb_to_bytes(f["embedding"]), json.dumps(f["box"]), f["prob"]),
                )
                fid = cur.lastrowid
                thumb = FACES_DIR / f"{fid}.jpg"
                f["crop"].save(thumb, "JPEG", quality=85)
                # Auto-assign to a known person if close enough to their centroid.
                best_pid, best_sim = None, -1.0
                for pid, c in centroids.items():
                    s = face_lib.cosine(f["embedding"], c)
                    if s > best_sim:
                        best_sim, best_pid = s, pid
                person_id = best_pid if best_sim >= face_lib.SAME_THRESHOLD else None
                conn.execute("UPDATE faces SET thumb_path = ?, person_id = ? WHERE id = ?",
                             (str(thumb), person_id, fid))
                face_count += 1
            conn.commit()
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=path.name)

    _recluster_unnamed(conn)
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "detected", "clips": len(todo), "faces": face_count}])


def _run_motion_job(job_id: str, labels: list[str]) -> None:
    """Detect actions across local video clips and store them as timestamped
    'action' events. Labels default to the active action-kind things; any detected
    label that matches a thing also links the clip to that thing."""
    import motion_lib

    conn = get_conn()
    if not labels:
        labels = [r["name"] for r in conn.execute(
            "SELECT name FROM things WHERE active = 1 AND kind = 'action'"
        )]
    if not labels:
        conn.close()
        _update_job(job_id, finished=True, phase="done", results=[{
            "status": "error",
            "error": "No actions to look for — add a thing of kind 'action' (e.g. 'pouring oil') first.",
        }])
        return

    name_to_id = {r["name"].strip().lower(): r["id"]
                  for r in conn.execute("SELECT id, name FROM things WHERE kind = 'action'")}
    clips = conn.execute("SELECT id, file_stem, duration_s FROM clips").fetchall()
    scannable = []
    for r in clips:
        p = find_media_file(r["file_stem"])
        if p is not None and p.suffix.lower() in VIDEO_EXTS:
            scannable.append((r["id"], p, r["duration_s"]))

    _update_job(job_id, total=len(scannable), phase="detecting")
    event_count = 0
    for i, (cid, path, dur) in enumerate(scannable):
        try:
            events = motion_lib.detect_actions(path, dur or 6.0, labels)
            conn.execute("DELETE FROM clip_events WHERE clip_id = ? AND kind = 'action'", (cid,))
            for e in events:
                conn.execute(
                    """INSERT INTO clip_events (clip_id, kind, label, t_start, t_end, score)
                       VALUES (?, 'action', ?, ?, ?, ?)""",
                    (cid, e["label"], e["t_start"], e["t_end"], e["score"]),
                )
                event_count += 1
                tid = name_to_id.get(e["label"].strip().lower())
                if tid:
                    conn.execute(
                        "INSERT OR IGNORE INTO clip_things (clip_id, thing_id) VALUES (?, ?)",
                        (cid, tid),
                    )
            conn.commit()
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=path.name)

    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "detected", "clips": len(scannable), "action_events": event_count}])


def _apply_hemisphere(value: float, ref: str, neg_letter: str) -> float:
    """Resolve a signed coordinate from exiftool's value + hemisphere ref.

    Two cases must both work:
      - Still images (JPEG EXIF): GPSLatitude/Longitude come back as *unsigned*
        magnitudes with a separate N/S/E/W ref -> apply the ref's sign.
      - QuickTime videos (.MOV from iPhone/Google Photos): the composite value is
        *already signed* (e.g. -122.21) and the ref tags are ABSENT -> trust the
        value's own sign; do NOT abs() it (that flipped West into East).
    """
    r = (ref or "").strip().upper()
    if r == neg_letter:            # explicit negative hemisphere (S or W)
        return -abs(value)
    if r and r != "-":             # explicit positive hemisphere (N or E)
        return abs(value)
    return value                   # no ref -> value is already signed


def _extract_gps(path: Path) -> tuple[float | None, float | None, str]:
    """Extract GPS coordinates from a file's EXIF via exiftool.

    Returns (latitude, longitude, display_string) in signed decimal degrees.
    Handles both unsigned-magnitude-plus-ref (JPEG EXIF) and already-signed
    composite values with no ref (QuickTime .MOV). Returns (None, None, "")
    when there's no GPS data or exiftool is unavailable."""
    if not exiftool_available():
        return None, None, ""
    try:
        result = subprocess.run(
            ["exiftool", "-n", "-T",
             "-GPSLatitude", "-GPSLongitude", "-GPSLatitudeRef", "-GPSLongitudeRef",
             str(path)],
            capture_output=True, text=True, timeout=10,
        )
        # -T -> single tab-separated line; missing tags come back as "-".
        fields = result.stdout.strip().split("\t")
        if len(fields) < 2:
            return None, None, ""
        lat_raw, lon_raw = fields[0], fields[1]
        lat_ref = fields[2] if len(fields) > 2 else ""
        lon_ref = fields[3] if len(fields) > 3 else ""
        if lat_raw in ("", "-") or lon_raw in ("", "-"):
            return None, None, ""
        lat = _apply_hemisphere(float(lat_raw), lat_ref, "S")
        lon = _apply_hemisphere(float(lon_raw), lon_ref, "W")
        return lat, lon, f"{lat:.6f}, {lon:.6f}"
    except Exception:
        return None, None, ""


def _index_clip_background(clip_id: int, path: Path) -> None:
    """Run vision analysis + transcription + GPS extraction for a newly imported clip.
    Runs in a daemon thread; writes directly to the DB when done."""
    with _indexing_lock:
        _indexing.add(clip_id)
    # Normalize to a web-safe proxy in parallel with indexing, so the clip is
    # playable in any engine as soon as (or before) analysis finishes.
    _ensure_proxy_async(path)
    try:
        description = category = tags_str = transcript = location = ""
        matched_things: list[str] = []
        regions = []  # normalized subject boxes for reframing (single-frame path)

        is_photo = path.suffix.lower() in IMAGE_EXTS
        scene_segments: list = []

        # --- Whisper transcription first (the deep index pass uses the transcript) ---
        whisper_result = None
        try:
            model = get_whisper_model()
            whisper_result = model.transcribe(str(path))
            transcript = whisper_result["text"].strip()
        except Exception:
            pass

        # --- Vision analysis ---
        duration_row = get_conn().execute(
            "SELECT duration_s FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        duration = (duration_row["duration_s"] if duration_row else None) or 4.0
        watchlist = _active_watchlist()
        try:
            if not is_photo and not _use_on_device():
                # Deep index: ONE Claude call over sampled frames + transcript ->
                # clip summary + a timestamped scene timeline ("analyze once").
                frames = _sample_frames(path, duration)
                tsegs = [
                    {"t_start": float(s.get("start", 0)), "t_end": float(s.get("end", 0)),
                     "text": (s.get("text") or "").strip()}
                    for s in (whisper_result or {}).get("segments", [])
                ] if whisper_result else []
                idx = deep_index_clip(frames, duration, tsegs, watchlist)
                description = idx.description
                category = idx.category
                tags_str = ", ".join(idx.tags)
                matched_things = list(idx.matched_things)
                scene_segments = idx.segments
                for seg in scene_segments:
                    matched_things.extend(seg.things)
            else:
                # Photos, or on-device mode: single-keyframe analysis (CLIP or Claude).
                with tempfile.TemporaryDirectory() as tmp:
                    frame_path = Path(tmp) / "frame.jpg"
                    seek = [] if is_photo else ["-ss", str(min(2.0, duration / 2))]
                    subprocess.run(
                        ["ffmpeg", "-y", *seek, "-i", str(path),
                         "-vf", "scale=768:-1", "-frames:v", "1", str(frame_path)],
                        check=True, capture_output=True,
                    )
                    image_bytes = frame_path.read_bytes()
                analysis = _frame_analysis(image_bytes, watchlist)
                description = analysis.description
                category = analysis.category
                tags_str = ", ".join(analysis.tags)
                matched_things = analysis.matched_things
                regions = getattr(analysis, "regions", []) or []
        except Exception:
            pass

        # --- GPS location ---
        lat, lon, location = _extract_gps(path)

        # --- Technical quality (resolution / sharpness) ---
        q_w, q_h, sharpness, quality = _measure_quality(path)

        # --- Persist ---
        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn()
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row:
            existing_description = (row["description"] or "").strip() or description
            existing_category = category or (row["category"] or "")
            existing_tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
            new_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
            merged, seen = [], set()
            for t in existing_tags + new_tags:
                k = t.lower()
                if k not in seen:
                    seen.add(k)
                    merged.append(t)
            conn.execute(
                """UPDATE clips
                   SET description=?, category=?, tags=?, transcript=?,
                       location=?, latitude=?, longitude=?, indexed_at=?,
                       width=?, height=?, sharpness=?, quality=?
                   WHERE id=?""",
                (existing_description, existing_category, ", ".join(merged),
                 transcript or row["transcript"] or "", location, lat, lon, now,
                 q_w, q_h, sharpness, quality, clip_id),
            )
            _record_thing_matches(conn, clip_id, matched_things)
            if regions:
                _record_regions(conn, clip_id, regions)
            if whisper_result is not None:
                _store_speech_segments(conn, clip_id, whisper_result)
            if scene_segments:
                _store_scene_segments(conn, clip_id, scene_segments)
            conn.commit()
        conn.close()

        if location or description:
            _maybe_stamp(path.stem, description=description, category=category, tags=tags_str)
        enqueue_embed(clip_id)  # keep semantic index fresh
    finally:
        with _indexing_lock:
            _indexing.discard(clip_id)


def _clip_search_text(conn, row) -> str:
    """The text we embed for a clip: its description/category/tags/transcript plus
    the deep-index scene timeline, so search reasons over the whole clip's meaning."""
    parts = [row["description"] or "", row["category"] or "",
             row["tags"] or "", row["transcript"] or ""]
    scenes = conn.execute(
        "SELECT text FROM clip_events WHERE clip_id = ? AND kind = 'scene' ORDER BY t_start",
        (row["id"],),
    ).fetchall()
    parts.extend(s["text"] or "" for s in scenes)
    return "  ".join(p.strip() for p in parts if p and p.strip())


def _embed_clip(conn, row) -> bool:
    """(Re)compute a clip's embedding if its text changed. Returns True if it wrote
    a fresh vector, False if it was already up to date or had no text to embed."""
    text = _clip_search_text(conn, row)
    if not text:
        return False
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = conn.execute(
        "SELECT text_hash FROM clip_embeddings WHERE clip_id = ?", (row["id"],)
    ).fetchone()
    if existing and existing["text_hash"] == text_hash:
        return False
    vec = semantic.embed(text)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO clip_embeddings (clip_id, dim, vector, text_hash, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
             dim=excluded.dim, vector=excluded.vector,
             text_hash=excluded.text_hash, updated_at=excluded.updated_at""",
        (row["id"], semantic.EMBED_DIM, semantic.vec_to_bytes(vec), text_hash, now),
    )
    return True


_embed_queue: "queue.Queue[int]" = queue.Queue()


_embed_worker_started = False


_embed_worker_lock = threading.Lock()


def _embed_worker() -> None:
    conn = get_conn()
    while True:
        clip_id = _embed_queue.get()
        try:
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
            if row and _embed_clip(conn, row):
                conn.commit()
        except Exception:
            pass
        finally:
            _embed_queue.task_done()


def enqueue_embed(clip_id: int) -> None:
    """Queue a clip for (re)embedding in the background. Safe to call from any
    request/thread; starts the worker lazily on first use."""
    global _embed_worker_started
    if not _embed_worker_started:
        with _embed_worker_lock:
            if not _embed_worker_started:
                threading.Thread(target=_embed_worker, daemon=True).start()
                _embed_worker_started = True
    _embed_queue.put(clip_id)


def _run_embed_job(job_id: str) -> None:
    """Build/refresh embeddings for every clip that has text. Local + free, so it's
    fine to run over the whole library."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM clips ORDER BY id").fetchall()
    _update_job(job_id, total=len(rows), phase="embedding")
    built = 0
    for i, row in enumerate(rows):
        try:
            if _embed_clip(conn, row):
                built += 1
                conn.commit()
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=row["file_stem"])
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "ok", "embedded": built}])


_HQ_RE = re.compile(r"\b(high[\s-]?quality|hq|high[\s-]?res(?:olution)?|sharp(?:est)?|crisp|hd)\b", re.I)


_LQ_RE = re.compile(r"\b(low[\s-]?quality|lq|blurry|soft|low[\s-]?res(?:olution)?)\b", re.I)


def _quality_intent(query: str) -> tuple[str | None, str]:
    """Detect a quality preference in a natural-language query and strip that phrase
    so only the *content* words get embedded. Returns (intent, cleaned_query)."""
    intent = None
    if _HQ_RE.search(query):
        intent = "high"
    elif _LQ_RE.search(query):
        intent = "low"
    cleaned = _LQ_RE.sub("", _HQ_RE.sub("", query)).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return intent, (cleaned or query)


_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def _store_scene_segments(conn, clip_id: int, segments) -> None:
    """Replace a clip's scene-timeline events with the deep-index segments."""
    conn.execute("DELETE FROM clip_events WHERE clip_id = ? AND kind = 'scene'", (clip_id,))
    for s in segments:
        conn.execute(
            """INSERT INTO clip_events (clip_id, kind, label, text, t_start, t_end)
               VALUES (?, 'scene', ?, ?, ?, ?)""",
            (clip_id, ", ".join(s.things) or None, s.description,
             float(s.t_start), float(s.t_end)),
        )


def _deep_index_one(conn, clip_id: int, path: Path) -> int:
    """Run the deep-index pass on one clip and persist everything. Returns the
    number of scene segments stored."""
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    duration = (row["duration_s"] if row else None) or probe_duration(path) or 4.0
    tsegs = [
        {"t_start": e["t_start"], "t_end": e["t_end"], "text": e["text"]}
        for e in conn.execute(
            "SELECT * FROM clip_events WHERE clip_id = ? AND kind='speech' ORDER BY t_start",
            (clip_id,),
        )
    ]
    idx = deep_index_clip(_sample_frames(path, duration), duration, tsegs, _active_watchlist(conn))
    return _store_deep_index(conn, clip_id, idx)


def _store_deep_index(conn, clip_id: int, idx) -> int:
    """Persist a DeepIndex result (summary + scene timeline + thing links)."""
    now = datetime.now(timezone.utc).isoformat()
    matched = list(idx.matched_things)
    for seg in idx.segments:
        matched.extend(seg.things)
    conn.execute(
        "UPDATE clips SET description=?, category=?, tags=?, indexed_at=? WHERE id=?",
        (idx.description, idx.category, ", ".join(idx.tags), now, clip_id),
    )
    _record_thing_matches(conn, clip_id, matched)
    _store_scene_segments(conn, clip_id, idx.segments)
    conn.commit()
    enqueue_embed(clip_id)  # keep semantic index fresh
    return len(idx.segments)


def _transcript_segs(conn, clip_id: int) -> list[dict]:
    return [
        {"t_start": e["t_start"], "t_end": e["t_end"], "text": e["text"]}
        for e in conn.execute(
            "SELECT * FROM clip_events WHERE clip_id=? AND kind='speech' ORDER BY t_start",
            (clip_id,),
        )
    ]


def _run_deep_index_batch_job(job_id: str, clip_ids: list[int]) -> None:
    """Deep-index via the Message Batches API: prepare all requests locally (free),
    submit ONE batch (50% price), poll until it ends, then store every result."""
    from claude_client import get_client, deep_index_batch_request, parse_deep_index_json

    conn = get_conn()
    todo = _deep_index_todo(conn, clip_ids)
    if not todo:
        conn.close()
        _update_job(job_id, finished=True, phase="done",
                    results=[{"status": "indexed", "clips": 0, "segments": 0}])
        return

    watchlist = _active_watchlist(conn)
    requests_list = []
    _update_job(job_id, total=len(todo), phase="preparing")
    for i, (cid, path, duration) in enumerate(todo):
        try:
            requests_list.append(deep_index_batch_request(
                f"clip-{cid}", _sample_frames(path, duration), duration,
                _transcript_segs(conn, cid), watchlist,
            ))
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=path.name)

    batch = get_client().messages.batches.create(requests=requests_list)
    _update_job(job_id, phase="waiting on batch (50% price)", done=0,
                total=len(requests_list), current=batch.id)
    while True:
        time.sleep(30)
        b = get_client().messages.batches.retrieve(batch.id)
        counts = b.request_counts
        _update_job(job_id, done=(counts.succeeded + counts.errored))
        if b.processing_status == "ended":
            break

    stored = segs = 0
    _update_job(job_id, phase="storing results")
    for result in get_client().messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        try:
            cid = int(result.custom_id.split("-")[1])
            text = next(blk.text for blk in result.result.message.content if blk.type == "text")
            idx = parse_deep_index_json(text)
            segs += _store_deep_index(conn, cid, idx)
            stored += 1
        except Exception:
            pass
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "indexed", "clips": stored, "segments": segs,
                          "batch_id": batch.id}])


def _deep_index_todo(conn, clip_ids: list[int]) -> list[tuple[int, Path, float]]:
    """Local video clips to deep-index: the given ids, or all lacking a scene timeline."""
    if clip_ids:
        ph = ",".join("?" * len(clip_ids))
        rows = conn.execute(
            f"SELECT id, file_stem, duration_s FROM clips WHERE id IN ({ph})", clip_ids
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, file_stem, duration_s FROM clips WHERE id NOT IN
               (SELECT DISTINCT clip_id FROM clip_events WHERE kind='scene')"""
        ).fetchall()
    todo = []
    for r in rows:
        p = find_media_file(r["file_stem"])
        if p is not None and p.suffix.lower() in VIDEO_EXTS:
            todo.append((r["id"], p, (r["duration_s"] or probe_duration(p) or 4.0)))
    return todo


def _run_deep_index_job(job_id: str, clip_ids: list[int]) -> None:
    """Deep-index local video clips synchronously (full price, results stream in)."""
    conn = get_conn()
    todo = _deep_index_todo(conn, clip_ids)
    _update_job(job_id, total=len(todo), phase="indexing")
    done_segments = 0
    for i, (cid, path, _dur) in enumerate(todo):
        try:
            done_segments += _deep_index_one(conn, cid, path)
        except Exception:
            pass
        _update_job(job_id, done=i + 1, current=path.name)
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "indexed", "clips": len(todo), "segments": done_segments}])


def _store_speech_segments(conn, clip_id: int, whisper_result: dict) -> None:
    """Replace a clip's stored speech events with the segments from a Whisper result.
    Each segment carries its own start/end (seconds), so dialogue becomes searchable
    and every line is a jump-to point on the clip's timeline."""
    conn.execute("DELETE FROM clip_events WHERE clip_id = ? AND kind = 'speech'", (clip_id,))
    for seg in whisper_result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        conn.execute(
            """INSERT INTO clip_events (clip_id, kind, text, t_start, t_end)
               VALUES (?, 'speech', ?, ?, ?)""",
            (clip_id, text, float(seg.get("start", 0.0)), float(seg.get("end", 0.0))),
        )


def _maybe_stamp(file_stem: str, **fields) -> dict | None:
    """Best-effort: embed metadata into the local file if it exists and exiftool is
    available. Never fatal -- returns a small status dict (or None if no local file)."""
    path = find_media_file(file_stem)
    if not path:
        return None
    try:
        stamp_file_metadata(path, **fields)
        return {"ok": True, "file": path.name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


METADATA_FIELDS = ("description", "category", "tags", "context")


def _upsert_thing(conn, name: str, kind: str = "", description: str = "") -> int | None:
    """Find a thing by name (case-insensitive) or create it; return its id.
    Newly created things are active so they also feed future indexing."""
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM things WHERE lower(name) = lower(?)", (name,)
    ).fetchone()
    if row:
        return row["id"]
    kind = (kind or "").strip() or classify_thing_kind(name, description or "")
    cur = conn.execute(
        "INSERT INTO things (name, kind, description, active) VALUES (?, ?, ?, 1)",
        (name, kind, (description or "").strip() or None),
    )
    return cur.lastrowid

__all__ = [n for n in dir() if not n.startswith("__")]
