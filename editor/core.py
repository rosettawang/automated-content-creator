from __future__ import annotations
"""Shared state + helpers for the editor backend (see app.py create_app()).
Imported by every blueprint via `from core import *`; must not import blueprints."""

"""
Rudimentary local video editor.

Usage:
    MEDIA_DIR=/path/to/local/pulled/footage python3 editor/app.py

Then open http://127.0.0.1:5001
"""


import hashlib


import json


import os


import queue


import re


import shutil


import sqlite3


import subprocess


import tempfile


import threading


import time


import uuid


import zipfile


from datetime import datetime, timezone


from pathlib import Path


from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env")


from flask import Flask, jsonify, request, send_file, render_template, Response


from db import get_conn, init_db, stamp_file_metadata, exiftool_available


from claude_client import (
    generate_rough_cut, suggest_content, analyze_frame, classify_thing_kind,
    infer_campaign_things, campaign_chat, pick_best_frame,
    match_things_in_text, propose_crop, revise_edit, deep_index_clip,
)


from drive_import import download_drive, probe_duration


from photos_import import (
    fetch_album_bases,
    download_one as photos_download_one,
    make_session as photos_make_session,
)


from composio_wrapper import list_toolkit_actions, initiate_connection, execute_action


import semantic


# Shared config/paths/constants live in config (the leaf module). Re-exported so
# `from core import *` in the blueprints keeps seeing these names unchanged.
from config import *  # noqa: F401,F403
from config import (
    MEDIA_DIR_RAW, MEDIA_DIR, ON_DEVICE_VISION_DEFAULT,
    VIDEO_EXTS, IMAGE_EXTS, MEDIA_EXTS, classify_kind,
    REPO_ROOT, CLIPS_OUT, REFERENCE_FRAMES, THUMB_CACHE, FACES_DIR, PROXY_CACHE,
)


def _no_cache_static(resp):
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# Media file ops (probe/proxy/quality/location/frame-sampling) live in media_files
# (leaf: config + db + jobs_runtime only). Re-exported so `from core import *` in
# the blueprints keeps seeing these names unchanged.
from media_files import *  # noqa: F401,F403
from media_files import (
    _proxy_jobs, _proxy_lock, _proxy_path_for, _generate_proxy, _ensure_proxy_async,
    _probe_dims, _laplacian_variance, _measure_quality, find_media_file, _media_roots,
    clip_media_status, _walk_media_files, _set_media, _run_media_verify_job,
    find_reference_frame, _hash_file, _scene_change_times, _motion_times,
    _interest_times, _sample_frames, _frame_at, _source_dims, _probe_fps,
    _has_audio, _display_dims,
)
















# Background-job registry lives in jobs_runtime (leaf module: db + stdlib only).
# Re-exported so `from core import *` in the blueprints keeps seeing these names.
from jobs_runtime import *  # noqa: F401,F403
from jobs_runtime import (  # explicit: underscore names for clarity/linters
    _jobs, _jobs_lock, _JOB_FLUSH_INTERVAL, _JOB_FLUSH_KEYS, _job_flush, _new_job,
    _update_job, _job_row_snapshot, _job_snapshot, _job_set_proc, _job_is_cancelled,
    _run_cancellable,
)


# Settings + provenance memory (on-device toggle, remembered Photos albums) live
# in settings (leaf: db + config). Re-exported for `from core import *`.
from settings import *  # noqa: F401,F403
from settings import (
    _get_setting, _set_setting, _use_on_device, _photos_albums,
    _remember_photos_albums, _read_album_urls_from_xlsx,
)


# Timeline serialization + social export/reframe live in export (leaf: db, config,
# media_files, jobs_runtime). Re-exported for `from core import *`.
from export import *  # noqa: F401,F403
from export import (
    _EDIT_LIST_COLS, _serialize_timeline, _snapshot_edit, _replace_timeline,
    ASPECT_DIMS, _kb_keys, _clamp_rect, _auto_crop_from_regions, _reframe_filter,
    EXPORT_FPS, _even, _social_bitrate_args, _slugify, _unique_output_path,
    _run_export_job,
)


# Content-understanding pipeline (vision/whisper/deep-index, things/regions,
# faces, motion, embeddings) + its job workers live in indexing. Re-exported so
# `from core import *` in the blueprints keeps seeing these names unchanged.
from indexing import *  # noqa: F401,F403
from indexing import (
    _indexing, _indexing_lock, _active_watchlist, _category_vocab, _frame_analysis,
    _norm_thing_name, _record_thing_matches, _record_regions, _pick_thing_thumbnail,
    _keyframe_bytes, _clip_timeline_texts, _thing_matches_text, _run_thing_scan_job,
    _run_region_scan_job, _person_centroids, _recluster_unnamed, _run_face_detect_job,
    _run_motion_job, _apply_hemisphere, _extract_gps, _index_clip_background,
    _clip_search_text, _embed_clip, _embed_worker, enqueue_embed, _run_embed_job,
    _quality_intent, get_whisper_model, _store_scene_segments, _deep_index_one,
    _store_deep_index, _transcript_segs, _run_deep_index_batch_job, _deep_index_todo,
    _run_deep_index_job, _store_speech_segments, _maybe_stamp, _upsert_thing,
    METADATA_FIELDS,
)


def _run_drive_job(job_id: str, urls: list[str]) -> None:
    conn = get_conn()
    results = []
    _update_job(job_id, total=len(urls), phase="downloading")
    for i, url in enumerate(urls):
        _update_job(job_id, current=url)
        try:
            paths = download_drive(url, MEDIA_DIR)
        except Exception as e:
            results.append({"url": url, "status": "error", "error": str(e)})
            _update_job(job_id, done=i + 1)
            continue
        # A folder link yields many files; a file link yields one. Register each,
        # recording the Drive link as its source so it can be re-pulled later.
        for path in paths:
            res = register_clip_file(conn, path, source_kind="drive", source_url=url)
            res["url"] = url
            results.append(res)
        _update_job(job_id, done=i + 1)
    conn.commit()
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done", results=results)


def _run_photos_job(job_id: str, urls: list[str]) -> None:
    conn = get_conn()
    results = []
    session = photos_make_session()

    # Enumerate every album first so we know the true item count before downloading.
    _update_job(job_id, phase="listing")
    items: list[tuple[str, str]] = []  # (album_url, media_base)
    for url in urls:
        try:
            for base in fetch_album_bases(url, session):
                items.append((url, base))
        except Exception as e:
            results.append({"url": url, "status": "error", "error": str(e)})

    # Remember which album(s) this library was pulled from, so a per-clip re-download
    # (Google Photos gives no stable per-file URL) can re-fetch the album and relink.
    if urls:
        _remember_photos_albums(urls)

    _update_job(job_id, total=len(items), phase="downloading")
    for i, (url, base) in enumerate(items):
        try:
            path = photos_download_one(base, MEDIA_DIR, i, session)
            # Store the album URL as the clip's source (per-item bases expire; the
            # album link is durable and, with relink-by-stem, re-download works).
            res = register_clip_file(conn, path, source_kind="photos", source_url=url)
            res["url"] = url
            results.append(res)
            _update_job(job_id, done=i + 1, current=path.name)
        except Exception as e:
            results.append({"url": url, "status": "error", "error": f"item {i}: {e}"})
            _update_job(job_id, done=i + 1)
    conn.commit()
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done", results=results)


def _clips_table_exists(conn) -> bool:
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clips'"
    ).fetchone() is not None


def _ensure_source_columns() -> None:
    """Self-contained provenance-column guarantee (mirrors the other _ensure_* calls),
    so this file works even if run before db.init_db()'s migration."""
    conn = get_conn()
    try:
        if not _clips_table_exists(conn):
            return  # fresh DB: init_db() will create clips with these columns
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(clips)")}
        for c in ("source_kind", "source_url"):
            if c not in cols:
                conn.execute(f"ALTER TABLE clips ADD COLUMN {c} TEXT")
        conn.commit()
    finally:
        conn.close()


def _backfill_clip_sources() -> None:
    """One-time-ish best effort for rows that predate provenance tracking: the seed
    library was all ingested from Google Photos (see README), so any clip with no
    recorded source is marked 'photos' — album-level re-download then works via
    relink-by-stem. Cheap no-op once every row has a source. Also seeds the known
    album list from the intake-log xlsx when we don't have one yet."""
    conn = get_conn()
    try:
        if not _clips_table_exists(conn):
            return
        conn.execute(
            "UPDATE clips SET source_kind='photos' "
            "WHERE source_kind IS NULL OR source_kind=''"
        )
        conn.commit()
    finally:
        conn.close()
    if not _photos_albums():
        albums = _read_album_urls_from_xlsx()
        if albums:
            _remember_photos_albums(albums)


def _can_redownload(source_kind, source_url) -> bool:
    """Is there enough provenance to fetch this clip's file again? Drive needs its
    link; Photos needs the clip's album link or any remembered album."""
    if source_kind == "drive":
        return bool(source_url)
    if source_kind == "photos":
        return bool(source_url) or bool(_photos_albums())
    return False


_ensure_source_columns()


_backfill_clip_sources()
































































STATIC_DIR = Path(__file__).resolve().parent / "static"


PANEL_BUNDLES = {
    "editor": ["app.js", "chat.js", "crop.js"],
    "library": ["library.js", "map.js", "things.js", "faces.js", "motion.js", "cuts.js"],
    "campaigns": ["campaigns.js"],
}


def _decorate_clips(clips: list[dict], membership: dict[int, list[int]] | None = None) -> list[dict]:
    """Attach availability, effective kind, index status, and (optionally) campaign
    membership to a list of clip dicts."""
    with _indexing_lock:
        indexing_now = set(_indexing)

    # Attach the curated "things" (watchlist matches) recorded for each clip, so the
    # UI can surface just what the user tracks rather than every AI tag. One query
    # for the whole batch.
    things_map: dict[int, list[dict]] = {}
    ids = [c["id"] for c in clips]
    if ids:
        conn = get_conn()
        placeholders = ",".join("?" * len(ids))
        for r in conn.execute(
            f"""SELECT ct.clip_id, t.name, t.kind
                FROM clip_things ct JOIN things t ON t.id = ct.thing_id
                WHERE ct.clip_id IN ({placeholders})
                ORDER BY t.name COLLATE NOCASE""",
            ids,
        ):
            things_map.setdefault(r["clip_id"], []).append({"name": r["name"], "kind": r["kind"]})
        conn.close()

    for c in clips:
        status, path = clip_media_status(c)
        c["availability"] = status                 # present | missing | absent
        c["available_locally"] = status == "present"
        c["can_redownload"] = _can_redownload(c.get("source_kind"), c.get("source_url"))
        local = Path(path) if path else None
        # Effective kind: stored value, else infer from the local file, else assume video.
        if not c.get("kind"):
            c["kind"] = classify_kind(local) if local else "video"
        if c["id"] in indexing_now:
            c["index_status"] = "indexing"
        elif c.get("indexed_at"):
            c["index_status"] = "indexed"
        else:
            c["index_status"] = "pending"
        c["things"] = things_map.get(c["id"], [])
        if membership is not None:
            c["campaign_ids"] = membership.get(c["id"], [])
    return clips


def _campaign_membership(conn) -> dict[int, list[int]]:
    """clip_id -> [campaign_id, ...] for every campaign_clips row."""
    membership: dict[int, list[int]] = {}
    for r in conn.execute("SELECT clip_id, campaign_id FROM campaign_clips"):
        membership.setdefault(r["clip_id"], []).append(r["campaign_id"])
    return membership
























def register_clip_file(conn, path: Path, source_kind: str | None = None,
                       source_url: str | None = None) -> dict:
    """Register a freshly-added media file (downloaded or uploaded) as a clip.

    `source_kind`/`source_url` record where the file came from (drive|photos|zip|
    local|upload + link) so it can be re-downloaded later; they're stored on a new
    clip and backfilled onto an existing catalog row that had no provenance yet.

    Dedup order:
      1. Content hash — if identical bytes are already in the library, this is a
         true duplicate: drop the redundant copy we just wrote and don't add a row.
      2. Filename stem — matches a catalog entry (e.g. migrated from the index with
         no local file yet); backfill its hash and mark it available.
      3. Otherwise insert a new clip and kick off background indexing."""
    file_stem = path.stem
    content_hash = _hash_file(path)

    # 1. Same content already known?
    dup = conn.execute(
        "SELECT id, file_stem FROM clips WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if dup:
        # Remove the redundant file we just saved/extracted, unless it happens to be
        # the very file the existing clip already points at.
        existing_path = find_media_file(dup["file_stem"])
        try:
            if existing_path is None or existing_path.resolve() != path.resolve():
                path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"status": "duplicate", "file_stem": file_stem,
                "filename": path.name, "duplicate_of": dup["file_stem"]}

    # 2. Existing catalog row by filename stem?
    existing = conn.execute(
        "SELECT id, content_hash FROM clips WHERE file_stem = ?", (file_stem,)
    ).fetchone()
    if existing:
        # Backfill hash and record where the file now lives (relinks a catalog row
        # whose media had gone missing / moved). Provenance is filled only if the row
        # didn't already have it (COALESCE keeps a known source over a re-import guess).
        conn.execute(
            "UPDATE clips SET content_hash = COALESCE(content_hash, ?), "
            "media_path = ?, media_status = 'present', media_checked_at = ?, "
            "source_kind = COALESCE(source_kind, ?), source_url = COALESCE(source_url, ?) "
            "WHERE id = ?",
            (content_hash, str(path), datetime.now(timezone.utc).isoformat(),
             source_kind, source_url, existing["id"]),
        )
        conn.commit()
        return {"status": "matched_existing", "file_stem": file_stem, "filename": path.name}

    # 3. New clip.
    duration = probe_duration(path)
    conn.execute(
        """
        INSERT INTO clips (file_stem, duration_s, category, description, status, kind,
                           content_hash, media_path, media_status, media_checked_at,
                           source_kind, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, ?)
        """,
        (file_stem, duration, "", "", "imported", classify_kind(path), content_hash,
         str(path), datetime.now(timezone.utc).isoformat(), source_kind, source_url),
    )
    conn.commit()
    clip_id = conn.execute(
        "SELECT id FROM clips WHERE file_stem = ?", (file_stem,)
    ).fetchone()["id"]
    t = threading.Thread(target=_index_clip_background, args=(clip_id, path), daemon=True)
    t.start()
    return {"status": "added_new_clip", "file_stem": file_stem, "filename": path.name}


def _unique_dest(dir_: Path, name: str) -> Path:
    """A non-colliding path in dir_ for `name` (adds ' (2)', ' (3)', … if needed)."""
    dest = dir_ / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while (dir_ / f"{stem} ({n}){suffix}").exists():
        n += 1
    return dir_ / f"{stem} ({n}){suffix}"


def extract_media_from_zip(zip_path: Path, dest_dir: Path) -> tuple[list[Path], list[str]]:
    """Extract media files from a zip straight into dest_dir (flattened).

    Skips directories, junk (__MACOSX, dotfiles like .DS_Store), and non-media
    extensions. Returns (extracted_paths, skipped_names). Guards against Zip Slip
    by taking only the basename of each entry."""
    extracted: list[Path] = []
    skipped: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename).name  # flatten + strip any path traversal
            if not base or base.startswith(".") or "__MACOSX" in info.filename:
                continue
            if Path(base).suffix.lower() not in MEDIA_EXTS:
                skipped.append(base)
                continue
            dest = _unique_dest(dest_dir, base)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            extracted.append(dest)
    return extracted, skipped


def _backfill_proxies(stems: list[str]) -> None:
    """Generate proxies for a list of clip stems one at a time (serial, so we don't
    fan out many heavy transcodes at once). Runs in a daemon thread."""
    for stem in stems:
        p = find_media_file(stem)
        if p and p.suffix.lower() in VIDEO_EXTS:
            _generate_proxy(p)




































def _pool_for_generation(conn, clip_ids: list[int], campaign_id) -> list[dict]:
    """Choose the clip pool for a generation: explicit clip_ids win; else a campaign's
    member clips; else the whole library.

    Only clips whose media is actually downloaded are eligible -- the assembler can
    only trim/concat files that exist, so handing the model catalog-only "ghost"
    clips would produce a timeline that renders but plays black. Non-local clips are
    dropped here so the model can never pick one.

    Photos and sub-second videos are also dropped: a still has no playable duration
    (it sits at 0s) and a fraction-of-a-second video can't carry a shot, so neither
    belongs in a video-generation pool. (Stills as 2-3s inserts are a separate,
    deliberate feature -- not an accidental 0.3s clip.)"""
    if clip_ids:
        ph = ",".join("?" for _ in clip_ids)
        rows = conn.execute(
            f"SELECT * FROM clips WHERE id IN ({ph}) ORDER BY file_stem", clip_ids
        ).fetchall()
    elif campaign_id:
        rows = conn.execute(
            """SELECT c.* FROM clips c
               JOIN campaign_clips pc ON pc.clip_id = c.id
               WHERE pc.campaign_id = ? ORDER BY c.file_stem""",
            (campaign_id,),
        ).fetchall()
        if not rows:  # empty campaign -> fall back to the whole library
            rows = conn.execute("SELECT * FROM clips ORDER BY file_stem").fetchall()
    else:
        rows = conn.execute("SELECT * FROM clips ORDER BY file_stem").fetchall()
    pool = [
        dict(r) for r in rows
        if find_media_file(r["file_stem"]) is not None
        and _usable_for_generation(r)
    ]
    _attach_moments(conn, pool)
    return pool


def _usable_for_generation(row) -> bool:
    """A clip can back a generated video only if it's a real, playable video shot:
    not a still (kind='photo', 0s) and not a sub-second fragment that can't carry a
    shot. Unknown/NULL durations are kept -- the assembler re-probes those."""
    if (row["kind"] or "") == "photo":
        return False
    dur = row["duration_s"]
    if dur is not None and dur < 1.0:
        return False
    return True


def _attach_moments(conn, clips: list[dict]) -> None:
    """Attach each clip's deep-index timeline (scene/action/speech events from
    clip_events) so the model can set in/out points on the best moment instead of
    defaulting to the front of the clip. Clips without events get an empty list."""
    for c in clips:
        rows = conn.execute(
            """SELECT kind, label, text, t_start, t_end FROM clip_events
               WHERE clip_id = ? AND kind IN ('scene', 'action', 'speech')
               ORDER BY t_start""",
            (c["id"],),
        ).fetchall()
        c["moments"] = [dict(r) for r in rows]


def _prompt_with_campaign_context(conn, campaign_id, prompt: str) -> str:
    """Prepend the campaign's saved description so it steers the cut."""
    if not campaign_id:
        return prompt
    row = conn.execute("SELECT name, description FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row and (row["description"] or "").strip():
        return (f"Campaign: {row['name']}\nCampaign context: {row['description'].strip()}\n\n{prompt}")
    return prompt




# Re-export everything (including _underscore helpers/state) so blueprints can
# `from core import *` without rewriting any route references.
__all__ = [n for n in dir() if not n.startswith("__")]
