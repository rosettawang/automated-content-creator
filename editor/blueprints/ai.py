from flask import Blueprint
from core import *
from blueprints.clips import clip_thumbnail

bp = Blueprint("ai", __name__)


@bp.get("/api/things")
def list_things():
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, COUNT(ct.clip_id) AS clip_count
               FROM things t
               LEFT JOIN clip_things ct ON ct.thing_id = t.id
               GROUP BY t.id
               ORDER BY t.name COLLATE NOCASE"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/things")
def create_thing():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    description = (data.get("description") or "").strip() or None
    # Kind is inferred, not asked for -- the user shouldn't have to categorize.
    kind = (data.get("kind") or "").strip()
    if not kind:
        kind = classify_thing_kind(name, description or "")
    with db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO things (name, kind, description, active) VALUES (?, ?, ?, 1)",
                (name, kind, description),
            )
        except sqlite3.IntegrityError:
            return {"error": f"'{name}' is already in your things list"}, 409
        row = conn.execute("SELECT * FROM things WHERE name = ?", (name,)).fetchone()
    return jsonify(dict(row)), 201


@bp.patch("/api/things/<int:thing_id>")
def update_thing(thing_id):
    data = request.json or {}
    with db_conn() as conn:
        if not conn.execute("SELECT 1 FROM things WHERE id = ?", (thing_id,)).fetchone():
            return {"error": "not found"}, 404
        fields, values = [], []
        for col in ("name", "kind", "description"):
            if col in data:
                fields.append(f"{col} = ?")
                values.append((data[col] or "").strip() or None)
        if "active" in data:
            fields.append("active = ?")
            values.append(1 if data["active"] else 0)
        if fields:
            values.append(thing_id)
            try:
                conn.execute(f"UPDATE things SET {', '.join(fields)} WHERE id = ?", values)
            except sqlite3.IntegrityError:
                return {"error": "another thing already has that name"}, 409
        row = conn.execute("SELECT * FROM things WHERE id = ?", (thing_id,)).fetchone()
    return jsonify(dict(row))


@bp.delete("/api/things/<int:thing_id>")
def delete_thing(thing_id):
    with db_conn() as conn:
        conn.execute("DELETE FROM things WHERE id = ?", (thing_id,))
    return {"status": "deleted"}


@bp.get("/api/things/<int:thing_id>/clips")
def thing_clips(thing_id):
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT c.* FROM clips c
               JOIN clip_things ct ON ct.clip_id = c.id
               WHERE ct.thing_id = ?
               ORDER BY c.id DESC""",
            (thing_id,),
        ).fetchall()
        # Attach this thing's region (where it sits in the frame) per clip, when known.
        boxes = {
            r["clip_id"]: {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
            for r in conn.execute(
                "SELECT clip_id, x, y, w, h FROM clip_regions WHERE thing_id = ?", (thing_id,)
            )
        }
    clips = _decorate_clips([dict(r) for r in rows])
    for c in clips:
        c["region"] = boxes.get(c["id"])
    return jsonify(clips)


@bp.post("/api/things/<int:thing_id>/pick-thumbnail")
def pick_thing_thumbnail(thing_id):
    with db_conn() as conn:
        clip_id = _pick_thing_thumbnail(conn, thing_id)
    if clip_id is None:
        return {"error": "no local clips to choose a cover from"}, 404
    return jsonify({"thing_id": thing_id, "clip_id": clip_id})


@bp.get("/api/things/<int:thing_id>/thumbnail")
def thing_thumbnail(thing_id):
    """Serve the thing's chosen cover keyframe, falling back to its newest matched
    local clip if no explicit pick has been made yet."""
    with db_conn() as conn:
        row = conn.execute("SELECT clip_id FROM thing_thumbs WHERE thing_id = ?", (thing_id,)).fetchone()
        clip_id = row["clip_id"] if row else None
        if clip_id is None:
            m = conn.execute(
                """SELECT c.id FROM clips c
                   JOIN clip_things ct ON ct.clip_id = c.id
                   WHERE ct.thing_id = ? ORDER BY c.id DESC""",
                (thing_id,),
            ).fetchall()
    if clip_id is None:
        for r in m:
            return clip_thumbnail(r["id"])
        return {"error": "no thumbnail"}, 404
    return clip_thumbnail(clip_id)


@bp.post("/api/things/scan")
def scan_things():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400
    data = request.json or {}
    thing_ids = data.get("thing_ids") or ([data["thing_id"]] if data.get("thing_id") else [])
    job_id = _new_job("Scanning clips", unit="clip")
    threading.Thread(target=_run_thing_scan_job, args=(job_id, thing_ids), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/faces/detect")
def faces_detect():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400
    job_id = _new_job("Detecting faces", unit="clip")
    threading.Thread(target=_run_face_detect_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.get("/api/faces/groups")
def faces_groups():
    """Named people + unnamed provisional clusters, each with a representative face."""
    people = []
    clusters = []
    with db_conn() as conn:
        for p in conn.execute("SELECT id, name FROM people ORDER BY name COLLATE NOCASE"):
            agg = conn.execute("SELECT COUNT(*) c FROM faces WHERE person_id = ?", (p["id"],)).fetchone()
            rep = conn.execute(
                "SELECT id FROM faces WHERE person_id = ? ORDER BY prob DESC LIMIT 1", (p["id"],)
            ).fetchone()
            people.append({"id": p["id"], "name": p["name"], "count": agg["c"],
                           "rep_face": rep["id"] if rep else None})
        for row in conn.execute(
            """SELECT cluster_id, COUNT(*) c FROM faces
               WHERE person_id IS NULL AND cluster_id IS NOT NULL
               GROUP BY cluster_id ORDER BY c DESC"""
        ):
            rep = conn.execute(
                """SELECT id FROM faces WHERE person_id IS NULL AND cluster_id = ?
                   ORDER BY prob DESC LIMIT 1""", (row["cluster_id"],)
            ).fetchone()
            clusters.append({"cluster_id": row["cluster_id"], "count": row["c"],
                             "rep_face": rep["id"] if rep else None})
    return jsonify({"people": people, "clusters": clusters})


@bp.get("/api/faces/<int:face_id>/thumb")
def face_thumb(face_id):
    with db_conn() as conn:
        row = conn.execute("SELECT thumb_path FROM faces WHERE id = ?", (face_id,)).fetchone()
    if not row or not row["thumb_path"] or not Path(row["thumb_path"]).exists():
        return {"error": "not found"}, 404
    return send_file(row["thumb_path"])


@bp.post("/api/faces/name")
def faces_name():
    """Assign a name to a cluster (or explicit face_ids). Creates the person if new;
    merges into the existing person if the name already exists."""
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM people WHERE lower(name) = lower(?)", (name,)).fetchone()
        if row:
            pid = row["id"]
        else:
            conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
            pid = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()["id"]

        if data.get("cluster_id") is not None:
            conn.execute(
                "UPDATE faces SET person_id = ?, cluster_id = NULL WHERE cluster_id = ? AND person_id IS NULL",
                (pid, data["cluster_id"]),
            )
        elif data.get("face_ids"):
            ids = data["face_ids"]
            ph = ",".join("?" * len(ids))
            conn.execute(f"UPDATE faces SET person_id = ?, cluster_id = NULL WHERE id IN ({ph})",
                         [pid, *ids])
    return {"status": "ok", "person_id": pid}


@bp.delete("/api/people/<int:person_id>")
def delete_person(person_id):
    """Un-name a person: their faces return to the unnamed pool and re-cluster."""
    with db_conn() as conn:
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))  # faces.person_id -> NULL
        _recluster_unnamed(conn)
    return {"status": "deleted"}


@bp.get("/api/people/<int:person_id>/clips")
def person_clips(person_id):
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT c.* FROM clips c
               JOIN faces f ON f.clip_id = c.id
               WHERE f.person_id = ? ORDER BY c.id DESC""",
            (person_id,),
        ).fetchall()
    return jsonify(_decorate_clips([dict(r) for r in rows]))


@bp.post("/api/motion/detect")
def motion_detect():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set -- restart the app with MEDIA_DIR=/path/to/folder"}, 400
    data = request.json or {}
    labels = [l.strip() for l in data.get("labels", []) if l.strip()]
    job_id = _new_job("Detecting motion", unit="clip")
    threading.Thread(target=_run_motion_job, args=(job_id, labels), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/embeddings/build")
def build_embeddings():
    """Kick off a background job that embeds every clip's text for semantic search."""
    job_id = _new_job("Embeddings", unit="clip")
    threading.Thread(target=_run_embed_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/search-semantic")
def search_semantic():
    """Rank clips by semantic similarity to a natural-language query. Returns the
    same decorated clip dicts as /api/clips, plus a `score`, ordered best-first.

    If the query expresses a quality preference ("crisp", "high quality", "blurry",
    …), the clip's measured quality is blended into the ranking so meaning AND
    quality both shape the order."""
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}, 400
    top_k = int(data.get("top_k") or 40)
    campaign_id = (data.get("campaign") or "").strip()
    quality_intent, query = _quality_intent(query)

    with db_conn() as conn:
        if campaign_id:
            vec_rows = conn.execute(
                """SELECT e.clip_id, e.vector FROM clip_embeddings e
                   JOIN campaign_clips pc ON pc.clip_id = e.clip_id
                   WHERE pc.campaign_id = ?""",
                (campaign_id,),
            ).fetchall()
        else:
            vec_rows = conn.execute("SELECT clip_id, vector FROM clip_embeddings").fetchall()

        if not vec_rows:
            return jsonify({"results": [], "unindexed": True})

        try:
            qvec = semantic.embed(query)
        except Exception as e:
            return {"error": f"embedding failed: {e}"}, 502
        ranked = semantic.rank(qvec, [(r["clip_id"], r["vector"]) for r in vec_rows], top_k)

        scores = {cid: sc for cid, sc in ranked}
        ids = [cid for cid, _ in ranked]
        if not ids:
            return jsonify({"results": []})
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM clips WHERE id IN ({placeholders})", ids
        ).fetchall()
        membership = _campaign_membership(conn)

    clips = _decorate_clips([dict(r) for r in rows], membership)
    for c in clips:
        sem = scores.get(c["id"], 0.0)
        c["relevance"] = round(sem, 4)
        if quality_intent:
            # Blend measured quality into the score. Unmeasured clips get a neutral
            # 0.5 so they're neither boosted nor buried. Quality swings the score by
            # up to ~60%, so strong relevance still wins but quality reorders ties.
            qn = (c["quality"] / 100.0) if c.get("quality") is not None else 0.5
            factor = qn if quality_intent == "high" else (1.0 - qn)
            c["score"] = round(sem * (0.4 + 0.6 * factor), 4)
        else:
            c["score"] = round(sem, 4)
    clips.sort(key=lambda c: c["score"], reverse=True)
    return jsonify({"results": clips, "quality_intent": quality_intent})


@bp.post("/api/clips/<int:clip_id>/deep-index")
def deep_index_endpoint(clip_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            return {"error": "not found"}, 404
        path = find_media_file(row["file_stem"])
        if not path:
            return {"error": f"'{row['file_stem']}' not found in MEDIA_DIR"}, 404
        if path.suffix.lower() in IMAGE_EXTS:
            return {"error": "deep index applies to videos; use Analyze for photos"}, 400
        try:
            n = _deep_index_one(conn, clip_id, path)
        except Exception as e:
            return {"error": str(e)}, 502
    return jsonify({"status": "ok", "segments": n})


@bp.post("/api/deep-index")
def deep_index_all():
    if MEDIA_DIR is None:
        return {"error": "MEDIA_DIR is not set"}, 400
    data = request.json or {}
    clip_ids = data.get("clip_ids") or []
    use_batch = bool(data.get("batch"))
    job_id = _new_job("Deep indexing" + (" (batch, 50% price)" if use_batch else ""), unit="clip")
    target = _run_deep_index_batch_job if use_batch else _run_deep_index_job
    threading.Thread(target=target, args=(job_id, clip_ids), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.post("/api/clips/<int:clip_id>/transcribe")
def transcribe_clip(clip_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            return {"error": "not found"}, 404
        path = find_media_file(row["file_stem"])
        if not path:
            return {"error": f"'{row['file_stem']}' not found in MEDIA_DIR"}, 404

        model = get_whisper_model()
        result = model.transcribe(str(path))
        transcript = result["text"].strip()

        conn.execute("UPDATE clips SET transcript = ? WHERE id = ?", (transcript, clip_id))
        _store_speech_segments(conn, clip_id, result)
    enqueue_embed(clip_id)  # transcript changed -> refresh semantic index
    return jsonify({"transcript": transcript, "segments": len(result.get("segments", []))})


@bp.post("/api/clips/<int:clip_id>/analyze")
def analyze_clip(clip_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            return {"error": "not found"}, 404
        path = find_media_file(row["file_stem"])
        if not path:
            return {"error": f"'{row['file_stem']}' not found in MEDIA_DIR"}, 404

        # Stills have no timeline to seek into; a mid-clip -ss on a single-frame image
        # yields no output (this is the HEIC/photo analyze bug). Only seek for video.
        is_image = path.suffix.lower() in IMAGE_EXTS
        duration = row["duration_s"] or 4.0
        timestamp = min(2.0, duration / 2)
        seek = [] if is_image else ["-ss", str(timestamp)]
        with tempfile.TemporaryDirectory() as tmp:
            frame_path = Path(tmp) / "frame.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    *seek, "-i", str(path),
                    "-frames:v", "1", str(frame_path),
                ],
                check=True, capture_output=True,
            )
            image_bytes = frame_path.read_bytes()

        try:
            analysis = _frame_analysis(image_bytes, _active_watchlist(conn))
        except Exception as e:
            return {"error": str(e)}, 502

        # Merge, don't clobber: preserve any human-authored context and union the tags
        # so a manual "describe" pass and the AI pass reinforce each other.
        existing_tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
        merged_tags, seen = [], set()
        for t in existing_tags + list(analysis.tags):
            key = t.lower()
            if key not in seen:
                seen.add(key)
                merged_tags.append(t)
        tags_str = ", ".join(merged_tags)

        # Keep an existing human description if there is one; otherwise take the AI's.
        description = (row["description"] or "").strip() or analysis.description
        category = analysis.category or (row["category"] or "")

        conn.execute(
            "UPDATE clips SET description = ?, category = ?, tags = ? WHERE id = ?",
            (description, category, tags_str, clip_id),
        )
    enqueue_embed(clip_id)  # description/tags changed -> refresh semantic index

    stamp_result = _maybe_stamp(row["file_stem"], description=description,
                                category=category, tags=tags_str,
                                context=row["context"] or "")
    return jsonify({
        "description": description,
        "category": category,
        "tags": merged_tags,
        "context": row["context"] or "",
        "stamped": stamp_result,
    })


@bp.post("/api/things/infer")
def infer_things():
    """Infer watchlist things from a free-text context blurb and add them (active).
    Lets the import dialog treat "things to look for" and "context" as interchangeable:
    write context alone and the subjects worth watching for are derived from it."""
    context = (request.json or {}).get("context", "").strip()
    if not context:
        return {"error": "context is required"}, 400
    try:
        inferred = infer_campaign_things("imported footage", context)
    except Exception as e:
        return {"error": str(e)}, 502
    created = []
    with db_conn() as conn:
        for t in inferred.things:
            tid = _upsert_thing(conn, t.name, getattr(t, "kind", "") or "", getattr(t, "description", "") or "")
            if tid:
                created.append(t.name)
    return jsonify({"things": created})


@bp.post("/api/suggest-content")
def suggest_content_route():
    with db_conn() as conn:
        clips = [dict(row) for row in conn.execute("SELECT * FROM clips ORDER BY file_stem").fetchall()]
    try:
        suggestions = suggest_content(clips)
    except Exception as e:
        return {"error": str(e)}, 502
    return jsonify({"ideas": [i.model_dump() for i in suggestions.ideas]})


@bp.get("/api/composio/actions")
def composio_actions():
    toolkit = request.args.get("toolkit", "").strip()
    if not toolkit:
        return {"error": "toolkit query param is required, e.g. ?toolkit=instagram"}, 400
    try:
        return jsonify(list_toolkit_actions(toolkit))
    except Exception as e:
        return {"error": str(e)}, 502


@bp.post("/api/composio/connect")
def composio_connect():
    data = request.json
    toolkit = data.get("toolkit", "").strip()
    auth_config_id = data.get("auth_config_id", "").strip()
    if not toolkit or not auth_config_id:
        return {"error": "toolkit and auth_config_id are required"}, 400
    try:
        connection = initiate_connection(
            toolkit, auth_config_id, callback_url=data.get("callback_url")
        )
    except Exception as e:
        return {"error": str(e)}, 502
    return jsonify({"redirect_url": connection.redirect_url})


@bp.post("/api/composio/execute")
def composio_execute():
    data = request.json
    action = data.get("action", "").strip()
    if not action:
        return {"error": "action is required"}, 400
    try:
        result = execute_action(action, data.get("arguments", {}))
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        return jsonify({"result": result})
    except Exception as e:
        return {"error": str(e)}, 502
