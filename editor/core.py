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


MEDIA_DIR_RAW = os.environ.get("MEDIA_DIR", "").strip()


MEDIA_DIR = Path(MEDIA_DIR_RAW).expanduser() if MEDIA_DIR_RAW else None


ON_DEVICE_VISION_DEFAULT = os.environ.get("ON_DEVICE_VISION", "1") != "0"


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tiff", ".webp"}


MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS


def classify_kind(path: Path) -> str:
    """'photo' for image files, 'video' otherwise (by file extension)."""
    return "photo" if path.suffix.lower() in IMAGE_EXTS else "video"


REPO_ROOT = Path(__file__).resolve().parent.parent


CLIPS_OUT = REPO_ROOT / "clips_out"


REFERENCE_FRAMES = REPO_ROOT / "reference_frames"


THUMB_CACHE = Path(__file__).resolve().parent / "data" / "thumbs"


FACES_DIR = Path(__file__).resolve().parent / "data" / "faces"


PROXY_CACHE = Path(__file__).resolve().parent / "data" / "proxies"


def _no_cache_static(resp):
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


_indexing: set[int] = set()


_indexing_lock = threading.Lock()


_proxy_jobs: set[str] = set()


_proxy_lock = threading.Lock()


def _proxy_path_for(stem: str) -> Path:
    return PROXY_CACHE / f"{stem}.mp4"


def _generate_proxy(path: Path) -> Path | None:
    """Build a web-safe H.264/AAC faststart MP4 for a local video.

    H.264 sources are *remuxed* (`-c:v copy`) — near-instant and lossless; anything
    else (HEVC, etc.) is transcoded to H.264 once. Only the video + first audio track
    are mapped, so timecode/metadata data-streams that MP4 can't hold don't abort the
    copy. Idempotent: skips when a fresh proxy already exists. Returns the proxy path
    or None on failure."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return None
    out = _proxy_path_for(path.stem)
    try:
        if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            return out
    except OSError:
        pass
    PROXY_CACHE.mkdir(parents=True, exist_ok=True)

    try:
        raw = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        # ffmpeg 8's csv writer emits a trailing empty field ("h264,"); take the
        # first token so the codec compare below actually matches.
        codec = raw.split(",")[0].strip().lower()
    except Exception:
        codec = ""

    video_args = ["-c:v", "copy"] if codec == "h264" else \
        ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    tmp = out.with_suffix(".partial.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args, "-c:a", "aac",
        "-movflags", "+faststart", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(out)
        return out
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _ensure_proxy_async(path: Path) -> None:
    """Kick off proxy generation in a daemon thread if one isn't already running and
    a fresh proxy doesn't already exist. Never blocks the caller (request path)."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return
    stem = path.stem
    proxy = _proxy_path_for(stem)
    try:
        if proxy.exists() and proxy.stat().st_mtime >= path.stat().st_mtime:
            return
    except OSError:
        pass
    with _proxy_lock:
        if stem in _proxy_jobs:
            return
        _proxy_jobs.add(stem)

    def _work():
        try:
            _generate_proxy(path)
        finally:
            with _proxy_lock:
                _proxy_jobs.discard(stem)

    threading.Thread(target=_work, daemon=True).start()


def _probe_dims(path: Path) -> tuple[int | None, int | None]:
    """Display resolution (rotation-aware) via ffprobe: (width, height)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_side_data=rotation",
             "-of", "default=nw=1", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        vals = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
        w = int(vals.get("width", 0)) or None
        h = int(vals.get("height", 0)) or None
        rot = abs(int(vals.get("rotation", "0") or 0))
        if rot in (90, 270) and w and h:
            w, h = h, w  # rotated video displays with swapped dims
        return w, h
    except Exception:
        return None, None


def _laplacian_variance(gray) -> float:
    """Variance of the Laplacian (focus measure) — higher = sharper/crisper."""
    import numpy as np
    g = gray.astype("float64")
    lap = (-4 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def _measure_quality(path: Path) -> tuple[int | None, int | None, float | None, int | None]:
    """Measure technical quality on-device: (width, height, sharpness, quality).

    Resolution comes from ffprobe; sharpness is variance-of-Laplacian on a frame
    normalized to 512px wide (so it's comparable across clips). `quality` is a
    0-100 heuristic blending resolution and sharpness — informational only, it
    never auto-excludes anything."""
    import math
    import numpy as np
    from PIL import Image

    w, h = _probe_dims(path)
    sharpness = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "q.jpg"
            # A frame ~1s in (or the still itself), normalized to 512 wide.
            cmd = ["ffmpeg", "-y", "-ss", "1", "-i", str(path),
                   "-frames:v", "1", "-vf", "scale=512:-1", str(fp)]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not fp.exists():
                # stills / very short clips: no seek
                subprocess.run(["ffmpeg", "-y", "-i", str(path),
                                "-frames:v", "1", "-vf", "scale=512:-1", str(fp)],
                               capture_output=True)
            if fp.exists():
                gray = np.asarray(Image.open(fp).convert("L"))
                if gray.size:
                    sharpness = round(_laplacian_variance(gray), 1)
    except Exception:
        pass

    quality = None
    if w and h:
        shortest = min(w, h)
        res_score = 40 if shortest >= 1080 else 30 if shortest >= 720 else 18 if shortest >= 480 else 8
        if sharpness is not None:
            # log-scaled: ~2000 var maps to full marks; clamp 0..1.
            sharp_score = max(0.0, min(1.0, math.log10(sharpness + 1) / math.log10(2000))) * 60
        else:
            sharp_score = 0
        quality = max(0, min(100, round(res_score + sharp_score)))
    return w, h, sharpness, quality


_jobs: dict[str, dict] = {}


_jobs_lock = threading.Lock()


_JOB_FLUSH_INTERVAL = 2.0  # seconds; throttle progress writes to the table


_JOB_FLUSH_KEYS = frozenset({"phase", "finished", "error", "cancelled", "total"})


def _job_flush(job: dict) -> None:
    """Persist a job's current memory state to the `jobs` table (called under lock)."""
    try:
        conn = get_conn()
        conn.execute(
            """INSERT INTO jobs (id, label, unit, phase, total, done, current, error,
                                 cancelled, finished, results, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                 phase=excluded.phase, total=excluded.total, done=excluded.done,
                 current=excluded.current, error=excluded.error,
                 cancelled=excluded.cancelled, finished=excluded.finished,
                 results=excluded.results, updated_at=CURRENT_TIMESTAMP""",
            (job["id"], job["label"], job["unit"], job["phase"], job["total"],
             job["done"], job["current"], job["error"],
             1 if job.get("cancelled") else 0, 1 if job["finished"] else 0,
             json.dumps(job["results"]) if job["finished"] else None,
             job["started_at"]),
        )
        conn.commit()
        conn.close()
        job["_last_flush"] = time.monotonic()
    except Exception:
        pass  # never let a progress-write failure break the running job


def _new_job(label: str, unit: str) -> str:
    """Create a progress job and return its id. `unit` is the thing being counted
    ("file" or "link") so the UI can phrase "7 of 23 files" vs "2 of 3 links"."""
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        # Prune finished jobs older than 10 minutes so the dict doesn't grow forever.
        cutoff = time.monotonic() - 600
        stale = [k for k, j in _jobs.items() if j["finished"] and j["started"] < cutoff]
        for k in stale:
            _jobs.pop(k, None)
        job = {
            "id": job_id, "label": label, "unit": unit, "phase": "starting",
            "total": None, "done": 0, "current": None,
            "started": time.monotonic(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "results": [], "finished": False, "error": None, "_last_flush": 0.0,
        }
        _jobs[job_id] = job
        _job_flush(job)
    # Bound table growth: drop finished job rows older than 24h.
    try:
        conn = get_conn()
        conn.execute(
            "DELETE FROM jobs WHERE finished = 1 AND updated_at < datetime('now', '-1 day')"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return job_id


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        forced = any(k in _JOB_FLUSH_KEYS and job.get(k) != v for k, v in fields.items())
        job.update(fields)
        if forced or (time.monotonic() - job.get("_last_flush", 0.0)) >= _JOB_FLUSH_INTERVAL:
            _job_flush(job)


def _job_row_snapshot(job_id: str) -> dict | None:
    """Build a snapshot from the persisted row (used after a restart, when the job is
    no longer in memory). Elapsed comes from the wall-clock started_at; eta is unknown."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        started = datetime.fromisoformat(row["started_at"])
        updated = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else started
        elapsed = max(0.0, (updated - started).total_seconds())
    except Exception:
        elapsed = 0.0
    return {
        "id": row["id"], "label": row["label"], "unit": row["unit"],
        "phase": row["phase"], "total": row["total"], "done": row["done"],
        "current": row["current"], "elapsed_s": round(elapsed, 1), "eta_s": None,
        "finished": bool(row["finished"]), "error": row["error"],
        "cancelled": bool(row["cancelled"]),
        "results": json.loads(row["results"]) if (row["finished"] and row["results"]) else [],
    }


def _job_snapshot(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            elapsed = time.monotonic() - job["started"]
            done, total = job["done"], job["total"]
            # Extrapolate remaining time from the average time-per-item so far. Only once
            # at least one item is done and we know the total -- otherwise it's a guess.
            eta = None
            if total and done > 0 and not job["finished"]:
                eta = max(0.0, elapsed / done * (total - done))
            return {
                "id": job["id"], "label": job["label"], "unit": job["unit"],
                "phase": job["phase"], "total": total, "done": done,
                "current": job["current"], "elapsed_s": round(elapsed, 1),
                "eta_s": round(eta, 1) if eta is not None else None,
                "finished": job["finished"], "error": job["error"],
                "cancelled": bool(job.get("cancelled")),
                "results": job["results"] if job["finished"] else [],
            }
    # Not in memory (e.g. after a restart) -- fall back to the persisted row.
    return _job_row_snapshot(job_id)


def reconcile_orphaned_jobs() -> None:
    """On startup, any job still marked unfinished lost its in-memory worker thread
    when the process exited -- mark it interrupted so the UI stops waiting on it."""
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE jobs SET finished = 1, error = 'interrupted (app restarted)', "
            "updated_at = CURRENT_TIMESTAMP WHERE finished = 0"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised inside a job worker when the user has requested cancellation."""


def _job_set_proc(job_id: str, proc) -> None:
    """Track the subprocess a job is currently running, so cancel can kill it."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["proc"] = proc


def _job_is_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancelled"))


def _run_cancellable(job_id: str, cmd: list[str]) -> None:
    """Run an ffmpeg command as a killable subprocess tracked on the job. Raises
    JobCancelled if the job was cancelled, or CalledProcessError on a real failure."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _job_set_proc(job_id, proc)
    try:
        _out, err = proc.communicate()
    finally:
        _job_set_proc(job_id, None)
    if _job_is_cancelled(job_id):
        raise JobCancelled()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=_out, stderr=err)


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


def _get_setting(key: str, default: str | None = None) -> str | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def _set_setting(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def _use_on_device() -> bool:
    """Live 'analyze on-device' setting: the stored toggle, or the env default if unset."""
    val = _get_setting("on_device_vision")
    if val is None:
        return ON_DEVICE_VISION_DEFAULT
    return val == "1"


def _photos_albums() -> list[str]:
    try:
        return json.loads(_get_setting("photos_albums") or "[]")
    except Exception:
        return []


def _remember_photos_albums(urls) -> None:
    """Union new album URLs into the stored list (dedup, order-preserving)."""
    merged = list(dict.fromkeys([*_photos_albums(), *[u for u in urls if u]]))
    _set_setting("photos_albums", json.dumps(merged))


def _read_album_urls_from_xlsx() -> list[str]:
    """Best-effort: pull the shared Google Photos album link(s) out of the committed
    intake-log spreadsheet, so a fresh checkout can re-download the seed library."""
    xlsx = REPO_ROOT / "content_intake_log.xlsx"
    if not xlsx.exists():
        return []
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx, read_only=True)
        ws = wb["Intake Log"]
        header = [c.value for c in next(ws.iter_rows(max_row=1))]
        if "Google Photos Link" not in header:
            return []
        idx = header.index("Google Photos Link")
        urls = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            v = row[idx] if idx < len(row) else None
            if v and str(v).startswith("http"):
                urls.append(str(v).strip())
        return list(dict.fromkeys(urls))
    except Exception:
        return []


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


def _ensure_thing_thumbs_table() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS thing_thumbs (
                   thing_id INTEGER PRIMARY KEY REFERENCES things(id) ON DELETE CASCADE,
                   clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
                   chosen_at TEXT DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.commit()
    finally:
        conn.close()


_ensure_thing_thumbs_table()


def _ensure_clip_regions_table() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS clip_regions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                   thing_id INTEGER REFERENCES things(id) ON DELETE SET NULL,
                   label TEXT,
                   x REAL, y REAL, w REAL, h REAL,
                   detected_at TEXT DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_clip_regions_clip ON clip_regions(clip_id)")
        conn.commit()
    finally:
        conn.close()


_ensure_clip_regions_table()


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


def find_media_file(file_stem: str) -> Path | None:
    if MEDIA_DIR is None or not MEDIA_DIR.is_dir():
        return None
    matches = list(MEDIA_DIR.glob(f"{file_stem}.*"))
    return matches[0] if matches else None


def _media_roots() -> list[Path]:
    """Folders to search for media. MEDIA_DIR today; extensible to a list later."""
    return [MEDIA_DIR] if (MEDIA_DIR and MEDIA_DIR.is_dir()) else []


def clip_media_status(row) -> tuple[str, str | None]:
    """Cheap resolve (no hashing): returns (status, path). Prefers the stored
    media_path, falls back to a stem glob. Used on every read, so it stays fast."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)
    mp = get("media_path")
    if mp and Path(mp).exists():
        return "present", mp
    local = find_media_file(row["file_stem"])
    if local:
        return "present", str(local)
    if mp:
        return "missing", None      # had a path once → it moved or was deleted
    return "absent", None           # never local on this machine


def _walk_media_files():
    for root in _media_roots():
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                yield p


def _set_media(conn, clip_id: int, path: str | None, status: str) -> None:
    conn.execute(
        "UPDATE clips SET media_path=?, media_status=?, media_checked_at=? WHERE id=?",
        (path, status, datetime.now(timezone.utc).isoformat(), clip_id),
    )


def _run_media_verify_job(job_id: str) -> None:
    """Reconcile every clip against disk. Broken paths are re-found by content hash
    across the media roots and relinked; the rest are recorded as missing."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, file_stem, content_hash, media_path FROM clips"
    ).fetchall()
    _update_job(job_id, total=len(rows), phase="checking")

    present = relocated = 0
    broken = []  # rows whose file isn't where we expect
    for i, r in enumerate(rows):
        status, path = clip_media_status(r)
        if status == "present":
            _set_media(conn, r["id"], path, "present")
            present += 1
        else:
            broken.append(r)
        _update_job(job_id, done=i + 1, current=r["file_stem"])
    conn.commit()

    # Relink phase: hash files under the roots and match broken clips by content hash.
    # Only runs when something is actually broken, so the common case pays nothing.
    by_hash = {r["content_hash"]: r for r in broken if r["content_hash"]}
    if by_hash:
        _update_job(job_id, phase="relinking", current="scanning for moved files…")
        for f in _walk_media_files():
            if not by_hash:
                break
            try:
                h = _hash_file(f)
            except Exception:
                continue
            r = by_hash.pop(h, None)
            if r:
                _set_media(conn, r["id"], str(f), "relocated")
                relocated += 1
        conn.commit()

    # Anything still unresolved is genuinely missing (or never was local).
    missing = []
    for r in broken:
        cur = conn.execute("SELECT media_status, media_path FROM clips WHERE id=?", (r["id"],)).fetchone()
        if cur["media_status"] == "relocated" and cur["media_path"]:
            continue
        new_status = "missing" if r["media_path"] else "absent"
        _set_media(conn, r["id"], None, new_status)
        if new_status == "missing":
            missing.append({"id": r["id"], "file_stem": r["file_stem"], "last_path": r["media_path"]})
    conn.commit()
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "verified", "present": present,
                          "relocated": relocated, "missing": missing}])


def find_reference_frame(file_stem: str) -> Path | None:
    if not REFERENCE_FRAMES.is_dir():
        return None
    matches = sorted(
        p for p in REFERENCE_FRAMES.iterdir()
        if file_stem in p.name and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    return matches[0] if matches else None


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


def _hash_file(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed so large videos don't load into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def _scene_change_times(path: Path, duration: float, thresh: float = 0.4) -> list[float]:
    """Timestamps of significant visual change, from ffmpeg scene detection — one
    local decode pass, free. This is the 'interesting-ness' signal used to sample
    more densely where the picture changes and sparsely where it's static."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path), "-vf",
             f"select='gt(scene,{thresh})',showinfo", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        times = []
        for m in re.finditer(r"pts_time:([0-9.]+)", r.stderr):
            t = float(m.group(1))
            if 0.0 < t < duration:
                times.append(round(t, 1))
        return sorted(set(times))
    except Exception:
        return []


def _motion_times(path: Path, duration: float, sample_fps: float = 2.0,
                  top_frac: float = 0.35, min_diff: float = 3.0) -> list[float]:
    """High-motion timestamps via frame differencing — the free 'interest' signal
    for continuous footage (where scene-cut detection finds nothing). Extracts tiny
    grayscale frames and returns the times of the largest frame-to-frame changes."""
    import numpy as np
    from PIL import Image
    n = int(duration * sample_fps) + 2
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "m%04d.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(path), "-vf",
                 f"fps={sample_fps},scale=64:36,format=gray", "-frames:v", str(n), str(out)],
                check=True, capture_output=True,
            )
        except Exception:
            return []
        files = sorted(Path(tmp).glob("m*.jpg"))
        if len(files) < 3:
            return []
        arrs = [np.asarray(Image.open(f), dtype="float32") for f in files]
    diffs = [float(np.abs(arrs[i] - arrs[i - 1]).mean()) for i in range(1, len(arrs))]
    times = [i / sample_fps for i in range(1, len(arrs))]
    if not diffs:
        return []
    order = sorted(range(len(diffs)), key=lambda i: -diffs[i])
    k = max(1, int(len(diffs) * top_frac))
    return sorted(round(times[i], 1) for i in order[:k]
                  if diffs[i] >= min_diff and 0.0 < times[i] < duration)


def _interest_times(path: Path, duration: float) -> list[float]:
    """Free, on-device signal for where a clip is 'more interesting': hard cuts
    (scene detection) plus within-shot motion peaks (frame differencing)."""
    try:
        from PIL import Image  # noqa: F401  (import guard: numpy/PIL present)
        return sorted(set(_scene_change_times(path, duration)) |
                      set(_motion_times(path, duration)))
    except Exception:
        return _scene_change_times(path, duration)


def _sample_frames(path: Path, duration: float, baseline_every: float = 6.0,
                   max_frames: int = 20) -> list[tuple[float, bytes]]:
    """Adaptive frame sampling for the deep-index pass: a sparse uniform BASELINE
    (cheap coverage of dull footage) PLUS extra frames at points of visual change
    (denser detail where it's interesting). The 'interest' signal is free ffmpeg
    scene detection, so we don't spend Claude tokens deciding where to spend them.
    Returns [(timestamp_seconds, jpeg_bytes), ...] in time order."""
    # Sparse uniform baseline so even a static clip is covered start-to-end.
    n_base = max(1, int(duration // baseline_every))
    baseline = {round(i * duration / n_base, 1) for i in range(n_base + 1)}
    baseline.add(round(max(0.0, duration - 0.3), 1))
    baseline = {t for t in baseline if 0.0 <= t < duration}

    changes = _interest_times(path, duration)  # free interest signal (cuts + motion)
    times = sorted(baseline | set(changes))

    # Cap total frames: always keep the baseline; thin the change points evenly.
    if len(times) > max_frames:
        extras = [t for t in times if t not in baseline]
        room = max(0, max_frames - len(baseline))
        if room and extras:
            step = len(extras) / room
            kept_extras = {extras[int(i * step)] for i in range(room)}
        else:
            kept_extras = set()
        times = sorted(baseline | kept_extras)[:max_frames]

    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            fp = Path(tmp) / f"f{i:04d}.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(t), "-i", str(path),
                     "-frames:v", "1", "-vf", "scale=768:-2", str(fp)],
                    check=True, capture_output=True,
                )
            except Exception:
                continue
            if fp.exists():
                frames.append((t, fp.read_bytes()))
    if not frames:
        raise RuntimeError("no frames extracted")
    return frames


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


def _pool_for_generation(conn, clip_ids: list[int], campaign_id) -> list[dict]:
    """Choose the clip pool for a generation: explicit clip_ids win; else a campaign's
    member clips; else the whole library.

    Only clips whose media is actually downloaded are eligible -- the assembler can
    only trim/concat files that exist, so handing the model catalog-only "ghost"
    clips would produce a timeline that renders but plays black. Non-local clips are
    dropped here so the model can never pick one."""
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
    pool = [dict(r) for r in rows if find_media_file(r["file_stem"]) is not None]
    _attach_moments(conn, pool)
    return pool


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


_EDIT_LIST_COLS = """
    e.*, p.name AS campaign_name,
    COUNT(t.id) AS item_count,
    COALESCE(SUM(t.out_point - t.in_point), 0) AS duration_s,
    (SELECT ti.clip_id FROM timeline_items ti
       WHERE ti.edit_id = e.id ORDER BY ti.position LIMIT 1) AS first_clip_id
"""


def _serialize_timeline(conn, edit_id: int) -> str:
    """JSON of the current timeline_items (order + trims) for snapshotting."""
    import json
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


def _frame_at(source: Path, ts: float) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(max(0.0, ts)), "-i", str(source),
             "-frames:v", "1", str(frame_path)],
            check=True, capture_output=True,
        )
        return frame_path.read_bytes()


ASPECT_DIMS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}


def _kb_keys(item) -> tuple | None:
    """Return the Ken Burns END rect if fully set, else None."""
    ex, ey, ew, eh = item["kb_x"], item["kb_y"], item["kb_w"], item["kb_h"]
    return None if None in (ex, ey, ew, eh) else (ex, ey, ew, eh)


def _clamp_rect(x, y, w, h):
    w = max(0.01, min(1.0, w)); h = max(0.01, min(1.0, h))
    x = max(0.0, min(1.0 - w, x)); y = max(0.0, min(1.0 - h, y))
    return x, y, w, h


def _source_dims(path: Path) -> tuple[int, int] | None:
    """Source pixel width,height via ffprobe (rotation-aware). None on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip().split("x")
        return int(out[0]), int(out[1])
    except Exception:
        return None


def _auto_crop_from_regions(conn, clip_id: int, target_ar: float, source_dims):
    """Derive a reframe crop (x,y,w,h fractions) that keeps the detected subject in
    shot: the largest window of the target aspect that fits the frame, centered on
    the subject regions. Prefers regions tied to a watched thing. None if no regions
    or dimensions are known."""
    if not source_dims:
        return None
    rows = conn.execute(
        """SELECT x, y, w, h, thing_id FROM clip_regions
           WHERE clip_id = ? AND w > 0 AND h > 0""",
        (clip_id,),
    ).fetchall()
    if not rows:
        return None
    chosen = [r for r in rows if r["thing_id"] is not None] or list(rows)
    minx = min(r["x"] for r in chosen)
    miny = min(r["y"] for r in chosen)
    maxx = max(r["x"] + r["w"] for r in chosen)
    maxy = max(r["y"] + r["h"] for r in chosen)
    ccx, ccy = (minx + maxx) / 2, (miny + maxy) / 2

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


def _probe_fps(path: Path) -> float:
    """Average source fps via ffprobe (falls back to 30)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        num, den = out.split("/")
        return (float(num) / float(den)) if float(den) else 30.0
    except Exception:
        return 30.0


EXPORT_FPS = 30


def _has_audio(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return bool(out)
    except Exception:
        return False


def _display_dims(path: Path) -> tuple[int, int] | None:
    """Rotation-corrected display width,height: if the stream carries a 90/270°
    rotation, the on-screen frame is the coded dimensions swapped. This is why an
    iPhone portrait clip (coded 1920x1080 + rotation) must be treated as 1080x1920."""
    import json as _json
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_tags=rotate:side_data=rotation",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        info = _json.loads(out)["streams"][0]
        w, h = int(info["width"]), int(info["height"])
        rot = 0
        if info.get("tags", {}).get("rotate"):
            rot = int(info["tags"]["rotate"])
        for sd in info.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return None


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
    CLIPS_OUT.mkdir(parents=True, exist_ok=True)
    candidate = CLIPS_OUT / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = CLIPS_OUT / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def _run_export_job(job_id, name, explicit_aspect, dims, plan):
    """Render the timeline to a social-normalized MP4 with live progress. `plan` is a
    list of self-contained item dicts (source path + in/out + crop/kb) so it needs no
    request context. Mirrors the import-job pattern: total = segments + 1 concat step."""
    region_conn = get_conn() if explicit_aspect else None
    output_path = None
    try:
        _update_job(job_id, total=len(plan) + 1, phase="encoding")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            segment_paths = []
            for i, item in enumerate(plan):
                if _job_is_cancelled(job_id):
                    raise JobCancelled()
                source = Path(item["source"])
                _update_job(job_id, current=f"{source.name} ({i + 1}/{len(plan)})")
                segment = tmp / f"segment_{i:03d}.mp4"
                duration = item["out_point"] - item["in_point"]

                if explicit_aspect:
                    frame_item = item
                    if None in (item["crop_x"], item["crop_y"], item["crop_w"], item["crop_h"]):
                        auto = _auto_crop_from_regions(
                            region_conn, item["clip_id"], dims[0] / dims[1], _source_dims(source)
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

                cmd = ["ffmpeg", "-y", "-ss", str(item["in_point"]), "-i", str(source)]
                has_audio = _has_audio(source)
                if not has_audio:
                    cmd += ["-f", "lavfi", "-t", str(duration),
                            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                cmd += ["-t", str(duration), "-vf", vf,
                        "-map", "0:v:0", "-map", ("1:a:0" if not has_audio else "0:a:0"),
                        "-r", str(EXPORT_FPS), "-vsync", "cfr",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        *_social_bitrate_args(dims),
                        "-c:a", "aac", "-ar", "48000", "-ac", "2",
                        "-video_track_timescale", "90000",
                        str(segment)]
                _run_cancellable(job_id, cmd)
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


# Re-export everything (including _underscore helpers/state) so blueprints can
# `from core import *` without rewriting any route references.
__all__ = [n for n in dir() if not n.startswith("__")]
