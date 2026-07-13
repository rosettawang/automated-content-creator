from flask import Blueprint
from core import *

bp = Blueprint("media", __name__)


@bp.post("/api/media/verify")
def verify_media():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400
    job_id = _new_job("Verifying media", unit="clip")
    threading.Thread(target=_run_media_verify_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/drive-import")
def drive_import():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400

    urls = [u.strip() for u in request.json.get("urls", []) if u.strip()]
    if not urls:
        return {"error": "no links provided"}, 400

    job_id = _new_job("Google Drive", unit="link")
    threading.Thread(target=_run_drive_job, args=(job_id, urls), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/photos-import")
def photos_import():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400

    urls = [u.strip() for u in request.json.get("urls", []) if u.strip()]
    if not urls:
        return {"error": "no links provided"}, 400

    job_id = _new_job("Google Photos", unit="file")
    threading.Thread(target=_run_photos_job, args=(job_id, urls), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/import-files")
def import_files():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400

    files = request.files.getlist("files")
    if not files:
        return {"error": "no files provided"}, 400

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    results = []
    for f in files:
        name = Path(f.filename or "").name  # strip any directory components (path-traversal guard)
        if not name:
            results.append({"filename": f.filename, "status": "error", "error": "invalid filename"})
            continue

        # A zip is a container: extract its media into MEDIA_DIR and register each,
        # then discard the archive itself rather than treating it as a clip.
        if name.lower().endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_zip = Path(tmp) / name
                try:
                    f.save(str(tmp_zip))
                    media_paths, skipped = extract_media_from_zip(tmp_zip, MEDIA_DIR)
                except zipfile.BadZipFile:
                    results.append({"filename": name, "status": "error", "error": "not a valid zip"})
                    continue
                except Exception as e:
                    results.append({"filename": name, "status": "error", "error": str(e)})
                    continue
            if not media_paths:
                results.append({"filename": name, "status": "error",
                                "error": "no media files found inside the zip"})
                continue
            for path in media_paths:
                results.append(register_clip_file(conn, path, source_kind="zip"))
            continue

        dest = _unique_dest(MEDIA_DIR, name)
        try:
            f.save(str(dest))
        except Exception as e:
            results.append({"filename": name, "status": "error", "error": str(e)})
            continue
        res = register_clip_file(conn, dest, source_kind="upload")
        results.append(res)
    conn.commit()
    conn.close()
    return jsonify({"results": results})


@bp.post("/api/import-local-paths")
def import_local_paths():
    """Import files the user picked with the native desktop file dialog, which --
    unlike a browser upload -- gives us the real on-disk paths. This lets us
    optionally *move* the file in (deleting the freshly-downloaded original)
    rather than leaving a duplicate behind.

    Body: {"paths": [...absolute paths...], "delete_originals": bool}

    We always copy first and only delete the original after the copy + register
    succeeds, so an error can never lose the source file. Only reachable in the
    desktop app; the browser never has real paths to send here."""
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400

    body = request.json or {}
    paths = [str(p) for p in body.get("paths", []) if str(p).strip()]
    delete_originals = bool(body.get("delete_originals"))
    if not paths:
        return {"error": "no paths provided"}, 400

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    results = []
    for raw in paths:
        src = Path(raw).expanduser()
        name = src.name
        if not src.is_file():
            results.append({"filename": name, "status": "error", "error": "file not found"})
            continue

        suffix = src.suffix.lower()

        # A zip is a container: extract its media into MEDIA_DIR, register each,
        # then (on move) delete the source archive.
        if suffix == ".zip":
            try:
                media_paths, _skipped = extract_media_from_zip(src, MEDIA_DIR)
            except zipfile.BadZipFile:
                results.append({"filename": name, "status": "error", "error": "not a valid zip"})
                continue
            except Exception as e:
                results.append({"filename": name, "status": "error", "error": str(e)})
                continue
            if not media_paths:
                results.append({"filename": name, "status": "error",
                                "error": "no media files found inside the zip"})
                continue
            for path in media_paths:
                res = register_clip_file(conn, path, source_kind="zip")
                res["moved"] = delete_originals
                results.append(res)
            if delete_originals:
                try:
                    src.unlink()
                except Exception:
                    pass  # extraction already succeeded; a leftover zip is harmless
            continue

        if suffix not in MEDIA_EXTS:
            results.append({"filename": name, "status": "error",
                            "error": f"unsupported file type ({suffix or 'no extension'})"})
            continue

        dest = _unique_dest(MEDIA_DIR, name)
        try:
            shutil.copy2(src, dest)
        except Exception as e:
            results.append({"filename": name, "status": "error", "error": str(e)})
            continue

        res = register_clip_file(conn, dest, source_kind="local")
        # Only delete the original once the copy is safely in the library. On a
        # content-duplicate, register_clip_file removes our copy but the bytes are
        # already stored under the existing clip -- deleting the original is still safe.
        if delete_originals and res.get("status") != "error":
            try:
                src.unlink()
            except Exception:
                pass
        res["moved"] = delete_originals
        results.append(res)

    conn.commit()
    conn.close()
    return jsonify({"results": results})


@bp.get("/api/clips/<int:clip_id>/media")
def clip_media(clip_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404
    status, resolved = clip_media_status(row)
    if status != "present":
        return {"error": f"'{row['file_stem']}' media is {status} — run Verify media to relink moved files."}, 404
    path = Path(resolved)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        # Prefer the normalized H.264/faststart proxy — it plays in every engine.
        proxy = _proxy_path_for(path.stem)
        try:
            fresh = proxy.exists() and proxy.stat().st_mtime >= path.stat().st_mtime
        except OSError:
            fresh = proxy.exists()
        if fresh:
            return send_file(proxy, mimetype="video/mp4")
        # No proxy yet: build one for next time, and serve the original now. The
        # video/mp4 relabel makes H.264 originals play in Chrome immediately (HEVC
        # will fall in line once the proxy is ready). send_file keeps Range support.
        _ensure_proxy_async(path)
        mimetype = "video/mp4" if suffix in (".mov", ".qt") else None
        return send_file(path, mimetype=mimetype)
    return send_file(path)


@bp.post("/api/clips/<int:clip_id>/pull")
def pull_clip(clip_id):
    """Re-download a clip's media from its recorded source and relink it.

    Returns a job_id to poll (reusing the Drive/Photos import machinery). When the
    job finishes, register_clip_file has relinked this catalog row by filename /
    content hash, so it flips back to available_locally. Google Photos has no stable
    per-file URL, so a photos clip re-fetches its whole album and relinks by stem."""
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400
    conn = get_conn()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404

    status, _ = clip_media_status(row)
    if status == "present":
        return jsonify({"status": "present", "message": "already downloaded"})

    kind = row["source_kind"]
    url = row["source_url"]
    if kind == "drive":
        if not url:
            return {"error": "no Drive link recorded for this clip"}, 400
        job_id = _new_job(f"Re-downloading {row['file_stem']}", unit="link")
        threading.Thread(target=_run_drive_job, args=(job_id, [url]), daemon=True).start()
        return jsonify({"job_id": job_id, "source_kind": "drive"})
    if kind == "photos":
        albums = [a for a in ([url] if url else _photos_albums()) if a]
        if not albums:
            return {"error": "no Google Photos album recorded to re-fetch from"}, 400
        job_id = _new_job(f"Re-fetching album for {row['file_stem']}", unit="file")
        threading.Thread(target=_run_photos_job, args=(job_id, albums), daemon=True).start()
        return jsonify({"job_id": job_id, "source_kind": "photos",
                        "note": "Google Photos has no per-file link, so the album is "
                                "re-fetched and this clip relinks by filename."})
    return {"error": f"clip came from '{kind or 'unknown'}' — no remote source to "
                     "re-download from. Import the file manually."}, 400


@bp.post("/api/normalize-all")
def normalize_all():
    """Backfill web-safe proxies for every local video that doesn't have a fresh one.
    Returns immediately; work proceeds serially in the background."""
    conn = get_conn()
    stems = [r["file_stem"] for r in conn.execute("SELECT file_stem FROM clips").fetchall()]
    conn.close()
    todo = []
    for stem in stems:
        p = find_media_file(stem)
        if not p or p.suffix.lower() not in VIDEO_EXTS:
            continue
        proxy = _proxy_path_for(stem)
        try:
            fresh = proxy.exists() and proxy.stat().st_mtime >= p.stat().st_mtime
        except OSError:
            fresh = proxy.exists()
        if not fresh:
            todo.append(stem)
    if todo:
        threading.Thread(target=_backfill_proxies, args=(todo,), daemon=True).start()
    return jsonify({"queued": len(todo)})


@bp.post("/api/import-finalize")
def import_finalize():
    """Apply batch-level context + campaign membership to freshly-imported clips,
    keyed by file_stem (the import endpoints return these). Called after an import
    so the user's "context for this content" and "add to campaign" choices land on
    exactly the clips that just came in. (Watchlist things are created up front,
    before import, so they can steer indexing.)"""
    data = request.json or {}
    stems = [s for s in (data.get("file_stems") or []) if s]
    context = (data.get("context") or "").strip()
    campaign_id = data.get("campaign_id") or None

    conn = get_conn()
    ids = []
    for s in stems:
        r = conn.execute("SELECT id FROM clips WHERE file_stem = ?", (s,)).fetchone()
        if r:
            ids.append(r["id"])

    context_applied = 0
    if context and ids:
        ph = ",".join("?" * len(ids))
        conn.execute(f"UPDATE clips SET context = ? WHERE id IN ({ph})", (context, *ids))
        context_applied = len(ids)

    added_to_campaign = 0
    if campaign_id and ids:
        for cid in ids:
            cur = conn.execute(
                "INSERT OR IGNORE INTO campaign_clips (campaign_id, clip_id) VALUES (?, ?)",
                (campaign_id, cid),
            )
            added_to_campaign += cur.rowcount

    conn.commit()
    conn.close()
    return jsonify({"clips": len(ids), "context_applied": context_applied,
                    "added_to_campaign": added_to_campaign})
