from flask import Blueprint
from core import *

bp = Blueprint("campaigns", __name__)


@bp.get("/api/projects")
def list_projects():
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, COUNT(pc.clip_id) AS clip_count
           FROM projects p
           LEFT JOIN project_clips pc ON pc.project_id = p.id
           GROUP BY p.id
           ORDER BY p.created_at DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/projects")
def create_project():
    name = (request.json.get("name") or "untitled").strip() or "untitled"
    description = (request.json.get("description") or "").strip()
    infer = request.json.get("infer_things", True)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO projects (name, description) VALUES (?, ?)", (name, description)
    )
    project_id = cur.lastrowid
    conn.commit()

    inferred = []
    if infer:
        try:
            result = infer_campaign_things(name, description)
            for t in result.things:
                thing_id = _upsert_thing(conn, t.name, t.kind, t.description)
                if thing_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO project_things (project_id, thing_id) VALUES (?, ?)",
                        (project_id, thing_id),
                    )
                    inferred.append(t.name)
            conn.commit()
        except Exception:
            pass  # inference is best-effort; a campaign still gets created without it

    conn.close()
    return jsonify({"id": project_id, "name": name, "description": description,
                    "inferred_things": inferred})


@bp.put("/api/projects/<int:project_id>")
def update_project(project_id):
    data = request.json or {}
    fields, values = [], []
    if "name" in data:
        fields.append("name = ?")
        values.append((data.get("name") or "untitled").strip() or "untitled")
    if "description" in data:
        fields.append("description = ?")
        values.append((data.get("description") or "").strip())
    if not fields:
        return {"error": "nothing to update"}, 400
    conn = get_conn()
    conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", (*values, project_id))
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404
    return jsonify(dict(row))


@bp.delete("/api/projects/<int:project_id>")
def delete_project(project_id):
    conn = get_conn()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": project_id})


@bp.get("/api/projects/<int:project_id>/clips")
def project_clips(project_id):
    """Member clips of a project, decorated like /api/clips."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.* FROM clips c
           JOIN project_clips pc ON pc.clip_id = c.id
           WHERE pc.project_id = ?
           ORDER BY c.file_stem""",
        (project_id,),
    ).fetchall()
    membership = _project_membership(conn)
    conn.close()
    clips = _decorate_clips([dict(r) for r in rows], membership)
    return jsonify(clips)


@bp.post("/api/projects/<int:project_id>/clips")
def add_project_clips(project_id):
    """Add one or more clips to a project (idempotent)."""
    clip_ids = request.json.get("clip_ids", [])
    conn = get_conn()
    for cid in clip_ids:
        conn.execute(
            "INSERT OR IGNORE INTO project_clips (project_id, clip_id) VALUES (?, ?)",
            (project_id, cid),
        )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM project_clips WHERE project_id = ?", (project_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({"added": len(clip_ids), "clip_count": count})


@bp.delete("/api/projects/<int:project_id>/clips")
def remove_project_clips(project_id):
    """Remove one or more clips from a project."""
    clip_ids = request.json.get("clip_ids", [])
    conn = get_conn()
    for cid in clip_ids:
        conn.execute(
            "DELETE FROM project_clips WHERE project_id = ? AND clip_id = ?",
            (project_id, cid),
        )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM project_clips WHERE project_id = ?", (project_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({"removed": len(clip_ids), "clip_count": count})


@bp.get("/api/projects/<int:project_id>")
def get_project(project_id):
    """A theme project with its edits (each edit is one timeline)."""
    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return {"error": "not found"}, 404
    edits = conn.execute(
        """SELECT e.*, COUNT(t.id) AS item_count
           FROM edits e
           LEFT JOIN timeline_items t ON t.edit_id = e.id
           WHERE e.project_id = ?
           GROUP BY e.id
           ORDER BY e.created_at DESC""",
        (project_id,),
    ).fetchall()
    clip_count = conn.execute(
        "SELECT COUNT(*) AS n FROM project_clips WHERE project_id = ?", (project_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({
        **dict(project),
        "clip_count": clip_count,
        "edits": [dict(e) for e in edits],
    })


@bp.get("/api/projects/<int:project_id>/things")
def project_things_list(project_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.*, COUNT(ct.clip_id) AS clip_count
           FROM project_things pt
           JOIN things t ON t.id = pt.thing_id
           LEFT JOIN clip_things ct ON ct.thing_id = t.id
           WHERE pt.project_id = ?
           GROUP BY t.id
           ORDER BY t.name COLLATE NOCASE""",
        (project_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/projects/<int:project_id>/things")
def project_things_add(project_id):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    conn = get_conn()
    thing_id = _upsert_thing(conn, name, data.get("kind", ""), data.get("description", ""))
    conn.execute(
        "INSERT OR IGNORE INTO project_things (project_id, thing_id) VALUES (?, ?)",
        (project_id, thing_id),
    )
    conn.commit()
    row = conn.execute(
        """SELECT t.*, COUNT(ct.clip_id) AS clip_count
           FROM things t LEFT JOIN clip_things ct ON ct.thing_id = t.id
           WHERE t.id = ? GROUP BY t.id""",
        (thing_id,),
    ).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@bp.delete("/api/projects/<int:project_id>/things/<int:thing_id>")
def project_things_remove(project_id, thing_id):
    """Unlink a thing from this campaign. The global thing itself is left intact
    (it may matter to other campaigns / indexing)."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM project_things WHERE project_id = ? AND thing_id = ?",
        (project_id, thing_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"removed": thing_id})


@bp.get("/api/projects/<int:project_id>/chat")
def project_chat_history(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, created_at FROM project_messages WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/projects/<int:project_id>/chat")
def project_chat_send(project_id):
    message = (request.json.get("message") or "").strip()
    if not message:
        return {"error": "empty message"}, 400
    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return {"error": "not found"}, 404
    things = [dict(r) for r in conn.execute(
        """SELECT t.name, t.kind, t.description FROM project_things pt
           JOIN things t ON t.id = pt.thing_id WHERE pt.project_id = ?""",
        (project_id,),
    ).fetchall()]
    clips = [dict(r) for r in conn.execute(
        """SELECT c.* FROM clips c JOIN project_clips pc ON pc.clip_id = c.id
           WHERE pc.project_id = ? ORDER BY c.file_stem""",
        (project_id,),
    ).fetchall()]
    history = [dict(r) for r in conn.execute(
        "SELECT role, content FROM project_messages WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()]

    try:
        reply = campaign_chat(dict(project), things, clips, history, message)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    conn.execute(
        "INSERT INTO project_messages (project_id, role, content) VALUES (?, 'user', ?)",
        (project_id, message),
    )
    conn.execute(
        "INSERT INTO project_messages (project_id, role, content) VALUES (?, 'assistant', ?)",
        (project_id, reply),
    )
    conn.commit()
    conn.close()
    return jsonify({"reply": reply})
