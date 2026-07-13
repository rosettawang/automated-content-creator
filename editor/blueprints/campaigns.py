from flask import Blueprint
from core import *

bp = Blueprint("campaigns", __name__)


@bp.get("/api/campaigns")
def list_campaigns():
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, COUNT(pc.clip_id) AS clip_count
           FROM campaigns p
           LEFT JOIN campaign_clips pc ON pc.campaign_id = p.id
           GROUP BY p.id
           ORDER BY p.created_at DESC"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/campaigns")
def create_campaign():
    name = (request.json.get("name") or "untitled").strip() or "untitled"
    description = (request.json.get("description") or "").strip()
    infer = request.json.get("infer_things", True)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO campaigns (name, description) VALUES (?, ?)", (name, description)
    )
    campaign_id = cur.lastrowid
    conn.commit()

    inferred = []
    if infer:
        try:
            result = infer_campaign_things(name, description)
            for t in result.things:
                thing_id = _upsert_thing(conn, t.name, t.kind, t.description)
                if thing_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO campaign_things (campaign_id, thing_id) VALUES (?, ?)",
                        (campaign_id, thing_id),
                    )
                    inferred.append(t.name)
            conn.commit()
        except Exception:
            pass  # inference is best-effort; a campaign still gets created without it

    conn.close()
    return jsonify({"id": campaign_id, "name": name, "description": description,
                    "inferred_things": inferred})


@bp.put("/api/campaigns/<int:campaign_id>")
def update_campaign(campaign_id):
    data = request.json or {}
    fields, values = [], []
    if "name" in data:
        fields.append("name = ?")
        values.append((data.get("name") or "untitled").strip() or "untitled")
    if "description" in data:
        fields.append("description = ?")
        values.append((data.get("description") or "").strip())
    if "context_doc" in data:
        fields.append("context_doc = ?")
        values.append((data.get("context_doc") or "").strip())
    if not fields:
        return {"error": "nothing to update"}, 400
    conn = get_conn()
    conn.execute(f"UPDATE campaigns SET {', '.join(fields)} WHERE id = ?", (*values, campaign_id))
    conn.commit()
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404
    return jsonify(dict(row))


@bp.delete("/api/campaigns/<int:campaign_id>")
def delete_campaign(campaign_id):
    conn = get_conn()
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": campaign_id})


@bp.get("/api/campaigns/<int:campaign_id>/clips")
def campaign_clips(campaign_id):
    """Member clips of a campaign, decorated like /api/clips."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.* FROM clips c
           JOIN campaign_clips pc ON pc.clip_id = c.id
           WHERE pc.campaign_id = ?
           ORDER BY c.file_stem""",
        (campaign_id,),
    ).fetchall()
    membership = _campaign_membership(conn)
    conn.close()
    clips = _decorate_clips([dict(r) for r in rows], membership)
    return jsonify(clips)


@bp.post("/api/campaigns/<int:campaign_id>/clips")
def add_campaign_clips(campaign_id):
    """Add one or more clips to a campaign (idempotent)."""
    clip_ids = request.json.get("clip_ids", [])
    conn = get_conn()
    for cid in clip_ids:
        conn.execute(
            "INSERT OR IGNORE INTO campaign_clips (campaign_id, clip_id) VALUES (?, ?)",
            (campaign_id, cid),
        )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM campaign_clips WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({"added": len(clip_ids), "clip_count": count})


@bp.delete("/api/campaigns/<int:campaign_id>/clips")
def remove_campaign_clips(campaign_id):
    """Remove one or more clips from a campaign."""
    clip_ids = request.json.get("clip_ids", [])
    conn = get_conn()
    for cid in clip_ids:
        conn.execute(
            "DELETE FROM campaign_clips WHERE campaign_id = ? AND clip_id = ?",
            (campaign_id, cid),
        )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM campaign_clips WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({"removed": len(clip_ids), "clip_count": count})


@bp.get("/api/campaigns/<int:campaign_id>")
def get_campaign(campaign_id):
    """A theme campaign with its edits (each edit is one timeline)."""
    conn = get_conn()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not campaign:
        conn.close()
        return {"error": "not found"}, 404
    edits = conn.execute(
        """SELECT e.*, COUNT(t.id) AS item_count
           FROM edits e
           LEFT JOIN timeline_items t ON t.edit_id = e.id
           WHERE e.campaign_id = ?
           GROUP BY e.id
           ORDER BY e.created_at DESC""",
        (campaign_id,),
    ).fetchall()
    clip_count = conn.execute(
        "SELECT COUNT(*) AS n FROM campaign_clips WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()["n"]
    conn.close()
    return jsonify({
        **dict(campaign),
        "clip_count": clip_count,
        "edits": [dict(e) for e in edits],
    })


@bp.get("/api/campaigns/<int:campaign_id>/things")
def campaign_things_list(campaign_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.*, COUNT(ct.clip_id) AS clip_count
           FROM campaign_things pt
           JOIN things t ON t.id = pt.thing_id
           LEFT JOIN clip_things ct ON ct.thing_id = t.id
           WHERE pt.campaign_id = ?
           GROUP BY t.id
           ORDER BY t.name COLLATE NOCASE""",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/campaigns/<int:campaign_id>/things")
def campaign_things_add(campaign_id):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    conn = get_conn()
    thing_id = _upsert_thing(conn, name, data.get("kind", ""), data.get("description", ""))
    conn.execute(
        "INSERT OR IGNORE INTO campaign_things (campaign_id, thing_id) VALUES (?, ?)",
        (campaign_id, thing_id),
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


@bp.delete("/api/campaigns/<int:campaign_id>/things/<int:thing_id>")
def campaign_things_remove(campaign_id, thing_id):
    """Unlink a thing from this campaign. The global thing itself is left intact
    (it may matter to other campaigns / indexing)."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM campaign_things WHERE campaign_id = ? AND thing_id = ?",
        (campaign_id, thing_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"removed": thing_id})


@bp.get("/api/campaigns/<int:campaign_id>/chat")
def campaign_chat_history(campaign_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, created_at FROM campaign_messages WHERE campaign_id = ? ORDER BY id",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/campaigns/<int:campaign_id>/chat")
def campaign_chat_send(campaign_id):
    message = (request.json.get("message") or "").strip()
    if not message:
        return {"error": "empty message"}, 400
    conn = get_conn()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not campaign:
        conn.close()
        return {"error": "not found"}, 404
    things = [dict(r) for r in conn.execute(
        """SELECT t.name, t.kind, t.description FROM campaign_things pt
           JOIN things t ON t.id = pt.thing_id WHERE pt.campaign_id = ?""",
        (campaign_id,),
    ).fetchall()]
    in_campaign = [dict(r) for r in conn.execute(
        """SELECT c.* FROM clips c JOIN campaign_clips pc ON pc.clip_id = c.id
           WHERE pc.campaign_id = ? ORDER BY c.file_stem""",
        (campaign_id,),
    ).fetchall()]
    # The full catalog is what the chat draws GROUP recommendations from.
    catalog = [dict(r) for r in conn.execute(
        "SELECT * FROM clips ORDER BY file_stem"
    ).fetchall()]
    history = [dict(r) for r in conn.execute(
        "SELECT role, content FROM campaign_messages WHERE campaign_id = ? ORDER BY id",
        (campaign_id,),
    ).fetchall()]

    try:
        result = campaign_chat(dict(campaign), things, in_campaign, catalog, history, message)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    conn.execute(
        "INSERT INTO campaign_messages (campaign_id, role, content) VALUES (?, 'user', ?)",
        (campaign_id, message),
    )
    conn.execute(
        "INSERT INTO campaign_messages (campaign_id, role, content) VALUES (?, 'assistant', ?)",
        (campaign_id, result.reply),
    )
    # The chat maintains the living context doc; persist it when it returns a new one.
    if result.context_doc is not None:
        conn.execute("UPDATE campaigns SET context_doc = ? WHERE id = ?",
                     (result.context_doc.strip(), campaign_id))
    # Only recommend clips that exist and aren't already in the campaign.
    in_ids = {c["id"] for c in in_campaign}
    valid_ids = {c["id"] for c in catalog}
    rec_ids = [i for i in (result.recommend_clip_ids or [])
               if i in valid_ids and i not in in_ids]
    rec_clips = [{"id": c["id"], "file_stem": c["file_stem"],
                  "description": c["description"] or "", "category": c["category"] or ""}
                 for c in catalog if c["id"] in rec_ids]
    conn.commit()
    conn.close()
    return jsonify({
        "reply": result.reply,
        "context_doc": result.context_doc,          # null if unchanged this turn
        "recommend": {"clips": rec_clips, "reason": result.recommend_reason or ""},
    })
