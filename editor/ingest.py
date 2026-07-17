"""Media ingestion: register a downloaded/uploaded file as a clip (dedup by
content hash + filename, kicking off background indexing), unzip archives, and
the Drive/Photos import-job workers. Plus the provenance data-backfill run at
startup.

Depends on leaves + indexing (register_clip_file spawns _index_clip_background);
never imports core. Re-exported by core for `from core import *`.
"""
from __future__ import annotations

import logging
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from db import get_conn
from config import MEDIA_DIR, MEDIA_EXTS, VIDEO_EXTS, classify_kind
from media_files import find_media_file, _hash_file, _generate_proxy
from jobs_runtime import _update_job
from settings import _photos_albums, _remember_photos_albums, _read_album_urls_from_xlsx
from indexing import _index_clip_background
from drive_import import download_drive, probe_duration
from photos_import import (
    fetch_album_bases,
    download_one as photos_download_one,
    make_session as photos_make_session,
)

log = logging.getLogger("editor.ingest")


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


def _backfill_clip_sources() -> None:
    """One-time-ish best effort for rows that predate provenance tracking: the seed
    library was all ingested from Google Photos (see README), so any clip with no
    recorded source is marked 'photos' — album-level re-download then works via
    relink-by-stem. Cheap no-op once every row has a source. Also seeds the known
    album list from the intake-log xlsx when we don't have one yet.

    The clips.source_kind/source_url columns are guaranteed by the migration
    baseline, so this runs at startup *after* init_db() (see app.serve /
    desktop.main), no longer at import time."""
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
        except Exception as _e:
            log.warning("%s: %s", "register_clip_file", _e)
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

__all__ = [n for n in dir() if not n.startswith("__")]
