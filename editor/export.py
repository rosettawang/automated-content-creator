"""Timeline serialization + social-normalized video export (reframe/Ken Burns).

Depends on leaves only (`db`, `config`, `media_files`, `jobs_runtime`); never
imports the feature modules, so it sits below `catalog` in the graph. Reads
`config.CLIPS_OUT` dynamically at call time so tests can redirect the output dir.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
from db import get_conn
from media_files import _source_dims, _has_audio, find_media_file
from jobs_runtime import _update_job, _run_cancellable, _job_is_cancelled, JobCancelled


_EDIT_LIST_COLS = """
    e.*, p.name AS campaign_name,
    COUNT(t.id) AS item_count,
    COALESCE(SUM(t.out_point - t.in_point), 0) AS duration_s,
    (SELECT ti.clip_id FROM timeline_items ti
       WHERE ti.edit_id = e.id ORDER BY ti.position LIMIT 1) AS first_clip_id
"""


def _serialize_timeline(conn, edit_id: int) -> str:
    """JSON of the current timeline_items (order + trims) for snapshotting."""
    rows = conn.execute(
        "SELECT clip_id, position, in_point, out_point FROM timeline_items "
        "WHERE edit_id = ? ORDER BY position",
        (edit_id,),
    ).fetchall()
    return json.dumps([dict(r) for r in rows])


def _snapshot_edit(conn, edit_id: int, label: str) -> None:
    """Push the current timeline onto the edit's undo stack."""
    conn.execute(
        "INSERT INTO edit_snapshots (edit_id, label, data) VALUES (?, ?, ?)",
        (edit_id, label, _serialize_timeline(conn, edit_id)),
    )


def _replace_timeline(conn, edit_id: int, selections) -> None:
    """Replace all timeline_items for an edit with an ordered list of selections
    (objects with clip_id/in_point/out_point)."""
    conn.execute("DELETE FROM timeline_items WHERE edit_id = ?", (edit_id,))
    for i, sel in enumerate(selections):
        conn.execute(
            "INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
            "VALUES (?, ?, ?, ?, ?)",
            (edit_id, sel.clip_id, i, sel.in_point, sel.out_point),
        )


ASPECT_DIMS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}

# Audio treatments an edit can carry (spec: specs/audio-design.md). Phase 1 renders
# 'clean' (strip audio) and everything else as normalized ambient; 'music'/'voiceover'
# keep their stored plan but export as ambient until Phases 2–3 land.
AUDIO_MODES = ("ambient", "speech-led", "music", "voiceover", "clean")


def _segment_audio_args(audio_mode: str, has_audio: bool) -> dict:
    """The per-segment ffmpeg audio pieces for one treatment. Pure + string-only so the
    filter-graph construction is unit-testable without rendering. Returns:
      null_input : add a silent anullsrc input (a still / audioless clip)
      maps       : the -map / -an args
      filt       : the -af args (loudnorm to kill level jumps at cuts)
      codec      : the audio codec args
    Phase 1: 'clean' drops audio entirely; all other modes get loudnorm on real audio
    (silence under stills is left untouched)."""
    if audio_mode == "clean":
        return {"null_input": False, "maps": ["-an"], "filt": [], "codec": []}
    codec = ["-c:a", "aac", "-ar", "48000", "-ac", "2"]
    if not has_audio:  # still/silent clip → silent stereo track, nothing to normalize
        return {"null_input": True, "maps": ["-map", "1:a:0"], "filt": [], "codec": codec}
    return {
        "null_input": False,
        "maps": ["-map", "0:a:0"],
        # EBU R128 loudness normalization → consistent level across cuts.
        "filt": ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"],
        "codec": codec,
    }


def _kb_keys(item) -> tuple | None:
    """Return the Ken Burns END rect if fully set, else None."""
    ex, ey, ew, eh = item["kb_x"], item["kb_y"], item["kb_w"], item["kb_h"]
    return None if None in (ex, ey, ew, eh) else (ex, ey, ew, eh)


def _clamp_rect(x, y, w, h):
    w = max(0.01, min(1.0, w)); h = max(0.01, min(1.0, h))
    x = max(0.0, min(1.0 - w, x)); y = max(0.0, min(1.0 - h, y))
    return x, y, w, h


def _auto_crop_from_regions(conn, clip_id: int, target_ar: float, source_dims):
    """Derive a reframe crop (x,y,w,h fractions) that keeps the detected subject in
    shot: the largest window of the target aspect that fits the frame, centered on
    the PRIMARY subject region. None if no regions or dimensions are known.

    Center on a single primary box, never the union of all regions: a watched-thing
    box if any (largest by area), else the largest box overall. The union of several
    spread-out regions approaches the whole frame and its center lands on background
    — the exact off-center failure this reframe is meant to prevent."""
    if not source_dims:
        return None
    rows = conn.execute(
        """SELECT x, y, w, h, thing_id FROM clip_regions
           WHERE clip_id = ? AND w > 0 AND h > 0""",
        (clip_id,),
    ).fetchall()
    if not rows:
        return None
    pool = [r for r in rows if r["thing_id"] is not None] or list(rows)
    primary = max(pool, key=lambda r: r["w"] * r["h"])
    ccx = primary["x"] + primary["w"] / 2
    ccy = primary["y"] + primary["h"] / 2

    sw, sh = source_dims
    source_ar = (sw / sh) if sh else 1.0
    # crop_w/crop_h (fraction space) so that pixel aspect == target_ar; take the
    # largest that still fits inside the frame.
    ratio = target_ar / source_ar
    if ratio <= 1:
        cw, ch = ratio, 1.0
    else:
        cw, ch = 1.0, 1.0 / ratio
    return _clamp_rect(ccx - cw / 2, ccy - ch / 2, cw, ch)


def _window_dims(target_ar: float, source_dims) -> tuple[float, float]:
    """crop_w/crop_h (fractions of the source) for the largest window of aspect
    `target_ar` that fits inside a `source_dims` frame."""
    sw, sh = source_dims
    source_ar = (sw / sh) if sh else 1.0
    ratio = target_ar / source_ar
    return (ratio, 1.0) if ratio <= 1 else (1.0, 1.0 / ratio)


def _frame_item_from_regions(conn, clip_id, in_point, out_point, target_ar, source_dims):
    """Framing v2: choose a subject-tracking reframe for ONE cut [in_point, out_point]
    from the clip's time-stamped regions. Returns {'crop': rect, 'kb': rect|None} or
    None when there's no usable timed region.

    Uses primary boxes observed inside the cut's own range (the box nearest in_point
    -> start window, nearest out_point -> end window). If the two barely differ it
    returns a static crop (kb=None); otherwise a start->end pair the zoompan export
    path animates so the frame pans with the subject."""
    if not source_dims:
        return None
    rows = conn.execute(
        """SELECT x, y, w, h, is_primary, t_frame FROM clip_regions
           WHERE clip_id = ? AND w > 0 AND h > 0 AND t_frame IS NOT NULL""",
        (clip_id,),
    ).fetchall()
    if not rows:
        return None
    in_range = [r for r in rows if in_point <= r["t_frame"] <= out_point]
    pool = in_range or rows
    primary = [r for r in pool if r["is_primary"]] or pool

    def near(t):
        r = min(primary, key=lambda r: abs(r["t_frame"] - t))
        return (r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)

    cw, ch = _window_dims(target_ar, source_dims)
    scx, scy = near(in_point)
    ecx, ecy = near(out_point)
    crop = _clamp_rect(scx - cw / 2, scy - ch / 2, cw, ch)
    if abs(scx - ecx) < 0.03 and abs(scy - ecy) < 0.03:
        return {"crop": crop, "kb": None}
    return {"crop": crop, "kb": _clamp_rect(ecx - cw / 2, ecy - ch / 2, cw, ch)}


def _apply_auto_framing(conn, edit_id: int, reset: bool = False) -> None:
    """Fill crop_*/kb_* on an edit's timeline items from time-stamped regions, decided
    at assemble time (see Framing v2). No-op unless the edit's aspect differs from
    source. Only fills items whose crop is currently NULL, so it never clobbers a prior
    item's framing or a human override — safe to call after an append. `reset=True`
    (used when the output aspect changes) first clears every item's crop/kb so the
    whole timeline is reframed for the new aspect. Items with no usable region are left
    NULL so the export-time _auto_crop_from_regions / center-crop fallback still runs."""
    erow = conn.execute("SELECT aspect FROM edits WHERE id = ?", (edit_id,)).fetchone()
    aspect = (erow["aspect"] if erow else None) or "source"
    if reset:
        # Recompute AUTO crops for the new aspect, but never wipe a human override.
        conn.execute(
            "UPDATE timeline_items SET crop_x=NULL, crop_y=NULL, crop_w=NULL, crop_h=NULL, "
            "kb_x=NULL, kb_y=NULL, kb_w=NULL, kb_h=NULL "
            "WHERE edit_id = ? AND COALESCE(crop_source, 'auto') <> 'manual'",
            (edit_id,),
        )
    if aspect not in ASPECT_DIMS:   # 'source' or unknown -> no reframe
        conn.commit()
        return
    ow, oh = ASPECT_DIMS[aspect]
    target_ar = ow / oh
    items = conn.execute(
        """SELECT ti.id, ti.clip_id, ti.in_point, ti.out_point, c.file_stem
           FROM timeline_items ti JOIN clips c ON c.id = ti.clip_id
           WHERE ti.edit_id = ? AND ti.crop_x IS NULL ORDER BY ti.position""",
        (edit_id,),
    ).fetchall()
    for it in items:
        p = find_media_file(it["file_stem"])
        if not p:
            continue
        fr = _frame_item_from_regions(
            conn, it["clip_id"], it["in_point"], it["out_point"], target_ar, _source_dims(p)
        )
        if not fr:
            continue
        c, kb = fr["crop"], fr["kb"]
        if kb:
            conn.execute(
                "UPDATE timeline_items SET crop_x=?, crop_y=?, crop_w=?, crop_h=?, "
                "kb_x=?, kb_y=?, kb_w=?, kb_h=?, crop_source='auto' WHERE id=?",
                (*c, *kb, it["id"]),
            )
        else:
            conn.execute(
                "UPDATE timeline_items SET crop_x=?, crop_y=?, crop_w=?, crop_h=?, "
                "kb_x=NULL, kb_y=NULL, kb_w=NULL, kb_h=NULL, crop_source='auto' WHERE id=?",
                (*c, it["id"]),
            )
    conn.commit()


def _apply_framing_edits(conn, edit_id: int, crops) -> None:
    """Apply chat-driven per-item framing (`crops` = objects with .index/.cx/.cy) after
    a timeline replace. Each becomes an exact aspect-correct window centered on (cx,cy),
    tagged `manual` so it's honored on export and preserved across an aspect change.
    No-op when the edit's aspect is 'source' (nothing to crop) or crops is empty."""
    if not crops:
        return
    erow = conn.execute("SELECT aspect FROM edits WHERE id = ?", (edit_id,)).fetchone()
    aspect = (erow["aspect"] if erow else None) or "source"
    if aspect not in ASPECT_DIMS:
        return
    ow, oh = ASPECT_DIMS[aspect]
    target_ar = ow / oh
    items = conn.execute(
        """SELECT ti.id, c.file_stem FROM timeline_items ti
           JOIN clips c ON c.id = ti.clip_id
           WHERE ti.edit_id = ? ORDER BY ti.position""",
        (edit_id,),
    ).fetchall()
    for ce in crops:
        idx = getattr(ce, "index", None)
        if idx is None or idx < 0 or idx >= len(items):
            continue
        it = items[idx]
        p = find_media_file(it["file_stem"])
        if not p:
            continue
        cw, ch = _window_dims(target_ar, _source_dims(p))
        x, y, w, h = _clamp_rect(ce.cx - cw / 2, ce.cy - ch / 2, cw, ch)
        conn.execute(
            "UPDATE timeline_items SET crop_x=?, crop_y=?, crop_w=?, crop_h=?, "
            "kb_x=NULL, kb_y=NULL, kb_w=NULL, kb_h=NULL, crop_source='manual' WHERE id=?",
            (x, y, w, h, it["id"]),
        )
    conn.commit()


def _reframe_filter(item, out_w: int, out_h: int, duration: float, fps: float) -> str:
    """ffmpeg -vf chain that reframes a source clip into out_w x out_h.

    - Static crop: if crop_* is set, select that region, then cover-scale.
    - Ken Burns: if BOTH crop_* (start) and kb_* (end) are set, animate a moving/
      zooming window from start->end across the clip via `zoompan` (a variable-size
      `crop` triggers filter-reinit errors, so zoompan is the reliable tool)."""
    cx, cy, cw, ch = item["crop_x"], item["crop_y"], item["crop_w"], item["crop_h"]
    has_crop = None not in (cx, cy, cw, ch)
    kb = _kb_keys(item)

    if has_crop and kb:
        sx, sy, sw, sh = _clamp_rect(cx, cy, cw, ch)
        ex, ey, ew, eh = _clamp_rect(*kb)
        n = max(1, round(float(duration) * (fps or 30)))
        # Zoom is driven by the window WIDTH fraction (z = 1/width); the window is
        # centered on the interpolated center point. zoompan clamps x/y in range.
        zs, ze = 1.0 / sw, 1.0 / ew
        cxs, cxe = sx + sw / 2, ex + ew / 2   # center x, start/end (fractions)
        cys, cye = sy + sh / 2, ey + eh / 2
        prog = f"(on/{n})"
        z = f"({zs}+({ze}-{zs})*{prog})"
        x = f"({cxs}+({cxe}-{cxs})*{prog})*iw*zoom-ow/2"
        y = f"({cys}+({cye}-{cys})*{prog})*ih*zoom-oh/2"
        # zoompan outputs exactly out_w x out_h already.
        return (f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={out_w}x{out_h}:fps={fps or 30},"
                f"setsar=1")

    chain = []
    if has_crop:
        cx, cy, cw, ch = _clamp_rect(cx, cy, cw, ch)
        chain.append(f"crop=iw*{cw}:ih*{ch}:iw*{cx}:ih*{cy}")
    # Named w=/h= required alongside force_original_aspect_ratio (ffmpeg 8 no
    # longer accepts the abbreviated option name or mixing positional args).
    chain.append(f"scale=w={out_w}:h={out_h}:force_original_aspect_ratio=increase")
    chain.append(f"crop={out_w}:{out_h}")
    chain.append("setsar=1")
    return ",".join(chain)


EXPORT_FPS = 30


def _even(n: int) -> int:
    """libx264 needs even dimensions."""
    return n - (n % 2)


def _social_bitrate_args(dims: tuple[int, int]) -> list[str]:
    """Cap the H.264 bitrate to a social-sane target instead of letting a CRF encode
    of clean footage balloon to ~20 Mbps. ~0.1 bits/px/frame → ≈10 Mbps at 1080×1920,
    which sits above where IG/TikTok re-encode from but well below the wasteful default.
    Clamped to [6, 16] Mbps."""
    w, h = dims
    mbps = max(6.0, min(16.0, (w * h * EXPORT_FPS * 0.10) / 1_000_000))
    return ["-b:v", f"{mbps:.1f}M", "-maxrate", f"{mbps * 1.3:.1f}M",
            "-bufsize", f"{mbps * 2:.1f}M"]


def _slugify(name: str) -> str:
    """ASCII-safe filename stem: drop Unicode (e.g. the '…' in a truncated edit name),
    collapse whitespace/punctuation to underscores."""
    ascii_only = (name or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("._-")
    return slug or "edit"


def _unique_output_path(stem: str, suffix: str = ".mp4") -> Path:
    """A non-colliding path under CLIPS_OUT — never silently overwrite a prior render."""
    config.CLIPS_OUT.mkdir(parents=True, exist_ok=True)
    candidate = config.CLIPS_OUT / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = config.CLIPS_OUT / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


_SEGMENT_CACHE_MAX = 400   # most-recent encoded segments to keep on disk


def _segment_cache_dir() -> Path:
    """Where re-usable encoded segments live — beside CLIPS_OUT (so tests that
    redirect CLIPS_OUT redirect this too)."""
    d = config.CLIPS_OUT.parent / "segment_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _segment_cache_key(source, in_point, out_point, dims, vf, has_audio, audio_treat="ambient") -> str:
    """A content key for one encoded segment. The `vf` string fully encodes the
    crop/Ken-Burns/aspect geometry, and `audio_treat` the audio treatment, so identical
    inputs -> identical bytes -> reuse. A re-export of an unchanged timeline is then a
    series of cache hits + a concat."""
    raw = repr((str(source), round(float(in_point), 4), round(float(out_point), 4),
                tuple(dims), vf, bool(has_audio), audio_treat,
                EXPORT_FPS, _social_bitrate_args(dims)))
    return hashlib.sha256(raw.encode()).hexdigest()


def _prune_segment_cache(keep: int = _SEGMENT_CACHE_MAX) -> None:
    d = config.CLIPS_OUT.parent / "segment_cache"
    if not d.is_dir():
        return
    files = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        p.unlink(missing_ok=True)


def _run_export_job(job_id, name, explicit_aspect, dims, plan, audio_mode="ambient"):
    """Render the timeline to a social-normalized MP4 with live progress. `plan` is a
    list of self-contained item dicts (source path + in/out + crop/kb) so it needs no
    request context. Mirrors the import-job pattern: total = segments + 1 concat step.
    `audio_mode` picks the audio treatment (spec: specs/audio-design.md)."""
    # Phase 1 renders 'clean' (no audio) vs everything-else (normalized ambient).
    audio_treat = "clean" if audio_mode == "clean" else "ambient"
    region_conn = get_conn() if explicit_aspect else None
    output_path = None
    # Probe results are stable per source path for the life of a run; memoize so a
    # clip used in several segments costs one ffprobe pair, not one per segment.
    _dims_memo: dict = {}
    _audio_memo: dict = {}

    def _src_dims(src):
        k = str(src)
        if k not in _dims_memo:
            _dims_memo[k] = _source_dims(src)
        return _dims_memo[k]

    def _src_audio(src):
        k = str(src)
        if k not in _audio_memo:
            _audio_memo[k] = _has_audio(src)
        return _audio_memo[k]

    try:
        _update_job(job_id, total=len(plan) + 1, phase="encoding")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            segment_paths = []
            for i, item in enumerate(plan):
                if _job_is_cancelled(job_id):
                    raise JobCancelled()
                source = Path(item["source"])
                segment = tmp / f"segment_{i:03d}.mp4"
                duration = item["out_point"] - item["in_point"]

                if explicit_aspect:
                    frame_item = item
                    if None in (item["crop_x"], item["crop_y"], item["crop_w"], item["crop_h"]):
                        auto = _auto_crop_from_regions(
                            region_conn, item["clip_id"], dims[0] / dims[1], _src_dims(source)
                        )
                        if auto:
                            frame_item = dict(item)
                            (frame_item["crop_x"], frame_item["crop_y"],
                             frame_item["crop_w"], frame_item["crop_h"]) = auto
                    vf = _reframe_filter(frame_item, dims[0], dims[1], duration, EXPORT_FPS)
                    vf += f",fps={EXPORT_FPS},format=yuv420p"
                else:
                    vf = (
                        f"scale=w={dims[0]}:h={dims[1]}:force_original_aspect_ratio=decrease,"
                        f"pad={dims[0]}:{dims[1]}:(ow-iw)/2:(oh-ih)/2:color=black,"
                        f"setsar=1,fps={EXPORT_FPS},format=yuv420p"
                    )

                has_audio = _src_audio(source)
                aargs = _segment_audio_args(audio_treat, has_audio)

                # Reuse an identically-encoded segment if we've rendered it before
                # (unchanged clip + trim + framing + audio treatment) — the expensive step.
                cached = _segment_cache_dir() / (
                    _segment_cache_key(source, item["in_point"], item["out_point"],
                                       dims, vf, has_audio, audio_treat) + ".mp4")
                if cached.exists():
                    segment_paths.append(cached)
                    _update_job(job_id, current=f"{source.name} ({i + 1}/{len(plan)}, cached)", done=i + 1)
                    continue
                _update_job(job_id, current=f"{source.name} ({i + 1}/{len(plan)})")

                cmd = ["ffmpeg", "-y", "-ss", str(item["in_point"]), "-i", str(source)]
                if aargs["null_input"]:
                    cmd += ["-f", "lavfi", "-t", str(duration),
                            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                cmd += ["-t", str(duration), "-vf", vf,
                        "-map", "0:v:0", *aargs["maps"],
                        "-r", str(EXPORT_FPS), "-vsync", "cfr",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        *_social_bitrate_args(dims),
                        *aargs["filt"], *aargs["codec"],
                        "-video_track_timescale", "90000",
                        str(segment)]
                _run_cancellable(job_id, cmd)
                # Only a fully-encoded segment reaches here (a cancel raises), so it's
                # safe to publish into the cache and concat from there.
                try:
                    shutil.copy2(segment, cached)
                    segment_paths.append(cached)
                except OSError:
                    segment_paths.append(segment)
                _update_job(job_id, done=i + 1)

            if _job_is_cancelled(job_id):
                raise JobCancelled()
            _update_job(job_id, phase="stitching", current="joining clips")
            concat_list = tmp / "concat.txt"
            concat_list.write_text("\n".join(f"file '{p}'" for p in segment_paths))
            # Sanitized, collision-free name (no silent overwrite of a prior render).
            output_path = _unique_output_path(_slugify(name))
            # Segments share codec/geometry/fps/audio params, so stream-copy is valid and
            # lossless; +faststart moves the moov atom up front for instant web playback.
            _run_cancellable(
                job_id,
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                 "-c", "copy", "-movflags", "+faststart", str(output_path)],
            )
            _prune_segment_cache()  # bound the cache to the most-recent N segments
            _update_job(job_id, done=len(plan) + 1, finished=True, current=None, phase="done",
                        results=[{"output": str(output_path),
                                  "width": dims[0], "height": dims[1], "fps": EXPORT_FPS}])
    except JobCancelled:
        # Remove any partial output left by a killed concat step.
        if output_path is not None:
            Path(output_path).unlink(missing_ok=True)
        _update_job(job_id, finished=True, phase="cancelled", current=None,
                    error="Export cancelled.")
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "replace")[-500:] if isinstance(e.stderr, (bytes, bytearray)) else str(e)
        _update_job(job_id, finished=True, phase="error", error=f"ffmpeg failed: {tail}")
    except Exception as e:
        _update_job(job_id, finished=True, phase="error", error=str(e))
    finally:
        if region_conn is not None:
            region_conn.close()


__all__ = [n for n in dir() if not n.startswith("__")]
