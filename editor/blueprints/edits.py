from flask import Blueprint
from core import *

bp = Blueprint("edits", __name__)


@bp.get("/api/edits")
def list_edits():
    """List edits (cuts) with enough to browse them: campaign name, total trimmed
    duration, clip count, and the first clip (for a thumbnail). No campaign filter =>
    every cut, including unassigned ones (so the Cuts view can surface orphans)."""
    campaign_id = request.args.get("campaign", "").strip()
    conn = get_conn()
    if campaign_id:
        rows = conn.execute(
            f"""SELECT {_EDIT_LIST_COLS}
               FROM edits e
               LEFT JOIN timeline_items t ON t.edit_id = e.id
               LEFT JOIN campaigns p ON p.id = e.campaign_id
               WHERE e.campaign_id = ?
               GROUP BY e.id ORDER BY e.created_at DESC""",
            (campaign_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT {_EDIT_LIST_COLS}
               FROM edits e
               LEFT JOIN timeline_items t ON t.edit_id = e.id
               LEFT JOIN campaigns p ON p.id = e.campaign_id
               GROUP BY e.id ORDER BY e.created_at DESC"""
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/edits")
def create_edit():
    data = request.json or {}
    name = (data.get("name") or "Untitled edit").strip() or "Untitled edit"
    campaign_id = data.get("campaign_id")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO edits (name, campaign_id) VALUES (?, ?)", (name, campaign_id)
    )
    edit_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"id": edit_id, "name": name, "campaign_id": campaign_id})


@bp.get("/api/edits/<int:edit_id>")
def get_edit(edit_id):
    conn = get_conn()
    edit = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    if not edit:
        conn.close()
        return {"error": "not found"}, 404
    items = conn.execute(
        """SELECT timeline_items.*, clips.file_stem, clips.description,
                  clips.duration_s AS clip_duration_s,
                  clips.width AS clip_width, clips.height AS clip_height,
                  clips.media_path AS media_path,
                  clips.source_kind AS source_kind, clips.source_url AS source_url
           FROM timeline_items
           JOIN clips ON clips.id = timeline_items.clip_id
           WHERE edit_id = ? ORDER BY position""",
        (edit_id,),
    ).fetchall()
    conn.close()
    # Flag each item's verified media state, so the editor can badge and warn instead
    # of silently failing to play/export a clip whose file isn't local. can_redownload
    # drives the timeline's "Re-download" affordance.
    out_items = []
    for i in items:
        d = dict(i)
        status, _ = clip_media_status(d)
        d["availability"] = status                 # present | missing | absent
        d["available_locally"] = status == "present"
        d["can_redownload"] = _can_redownload(d.get("source_kind"), d.get("source_url"))
        out_items.append(d)
    return jsonify({**dict(edit), "items": out_items})


@bp.put("/api/edits/<int:edit_id>")
def update_edit(edit_id):
    data = request.json or {}
    fields, values = [], []
    if "name" in data:
        fields.append("name = ?")
        values.append((data.get("name") or "Untitled edit").strip() or "Untitled edit")
    if "campaign_id" in data:
        fields.append("campaign_id = ?")
        values.append(data.get("campaign_id"))
    if "aspect" in data:
        aspect = data.get("aspect")
        if aspect not in (None, "", "source", *ASPECT_DIMS.keys()):
            return {"error": f"invalid aspect (use source/{'/'.join(ASPECT_DIMS)})"}, 400
        fields.append("aspect = ?")
        values.append(aspect or "source")
    if not fields:
        return {"error": "nothing to update"}, 400
    conn = get_conn()
    conn.execute(f"UPDATE edits SET {', '.join(fields)} WHERE id = ?", (*values, edit_id))
    conn.commit()
    # Changing the output aspect invalidates any stored (aspect-specific) crops:
    # reset + reframe from regions so the timeline matches the new frame.
    if "aspect" in data:
        _apply_auto_framing(conn, edit_id, reset=True)
    row = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404
    return jsonify(dict(row))


@bp.delete("/api/edits/<int:edit_id>")
def delete_edit(edit_id):
    conn = get_conn()
    conn.execute("DELETE FROM edits WHERE id = ?", (edit_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": edit_id})


@bp.post("/api/edits/<int:edit_id>/generate")
def generate_into_edit(edit_id):
    """Append an AI rough cut to an existing edit, using its campaign's context."""
    prompt = (request.json.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}, 400
    conn = get_conn()
    edit = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    if not edit:
        conn.close()
        return {"error": "not found"}, 404
    clips = _pool_for_generation(conn, [], edit["campaign_id"])
    if not clips:
        conn.close()
        return {"error": "No downloaded clips to assemble from. Import/pull clips into "
                "your media folder first — catalog-only clips can't be cut."}, 400
    full_prompt = _prompt_with_campaign_context(conn, edit["campaign_id"], prompt)
    try:
        plan = generate_rough_cut(full_prompt, clips)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM timeline_items WHERE edit_id = ?",
        (edit_id,),
    ).fetchone()["m"]
    for i, sel in enumerate(plan.selections):
        conn.execute(
            """INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point)
               VALUES (?, ?, ?, ?, ?)""",
            (edit_id, sel.clip_id, max_pos + 1 + i, sel.in_point, sel.out_point),
        )
    # Fill an inferred frame only if this edit doesn't already have one set — never
    # override an explicit aspect on an append.
    plan_aspect = getattr(plan, "aspect", None)
    plan_aspect = plan_aspect if plan_aspect in ASPECT_DIMS else None
    aspect_inferred = False
    if plan_aspect and (edit["aspect"] or "source") == "source":
        conn.execute("UPDATE edits SET aspect = ? WHERE id = ?", (plan_aspect, edit_id))
        aspect_inferred = True
    _apply_auto_framing(conn, edit_id)  # subject-track the newly appended items (NULL-crop only)
    conn.commit()
    conn.close()
    return jsonify({
        "concept": plan.concept, "selections": [s.model_dump() for s in plan.selections],
        "aspect": plan_aspect if aspect_inferred else None, "aspect_inferred": aspect_inferred,
    })


@bp.get("/api/edits/<int:edit_id>/chat")
def get_edit_chat(edit_id):
    """The chat transcript + whether an undo is available."""
    conn = get_conn()
    msgs = conn.execute(
        "SELECT role, content, created_at FROM edit_messages WHERE edit_id = ? ORDER BY id",
        (edit_id,),
    ).fetchall()
    undo = conn.execute(
        "SELECT COUNT(*) AS c FROM edit_snapshots WHERE edit_id = ?", (edit_id,)
    ).fetchone()["c"]
    conn.close()
    return jsonify({"messages": [dict(m) for m in msgs], "can_undo": undo > 0})


@bp.post("/api/edits/<int:edit_id>/chat")
def chat_edit(edit_id):
    """Apply a natural-language edit instruction to the timeline. Snapshots the
    current timeline first (so it can be undone), then replaces it with the revision."""
    prompt = (request.json.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}, 400
    conn = get_conn()
    edit = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    if not edit:
        conn.close()
        return {"error": "not found"}, 404

    current = conn.execute(
        """SELECT timeline_items.clip_id, timeline_items.in_point, timeline_items.out_point,
                  timeline_items.crop_x, timeline_items.crop_y,
                  timeline_items.crop_w, timeline_items.crop_h,
                  clips.file_stem, clips.description, clips.duration_s
           FROM timeline_items JOIN clips ON clips.id = timeline_items.clip_id
           WHERE edit_id = ? ORDER BY position""",
        (edit_id,),
    ).fetchall()
    # Subject regions per clip, so the chat can honor "keep the bowl centered".
    clip_ids = {r["clip_id"] for r in current}
    regions_by_clip: dict = {}
    if clip_ids:
        ph = ",".join("?" * len(clip_ids))
        for rr in conn.execute(
            f"SELECT clip_id, label, x, y, w, h FROM clip_regions "
            f"WHERE clip_id IN ({ph}) AND w > 0 AND h > 0", tuple(clip_ids)
        ):
            regions_by_clip.setdefault(rr["clip_id"], []).append(
                {"label": rr["label"] or "subject", "x": rr["x"], "y": rr["y"],
                 "w": rr["w"], "h": rr["h"]})
    current_timeline = []
    for r in current:
        d = {"clip_id": r["clip_id"], "in_point": r["in_point"], "out_point": r["out_point"],
             "file_stem": r["file_stem"], "description": r["description"],
             "duration_s": r["duration_s"]}
        if r["crop_x"] is not None and r["crop_w"] is not None:
            d["crop"] = {"cx": r["crop_x"] + r["crop_w"] / 2,
                         "cy": (r["crop_y"] or 0) + (r["crop_h"] or 0) / 2}
        regs = regions_by_clip.get(r["clip_id"])
        if regs:
            d["regions"] = regs[:6]   # cap to keep the prompt tight
        current_timeline.append(d)
    pool = _pool_for_generation(conn, [], edit["campaign_id"])

    try:
        result = revise_edit(prompt, current_timeline, pool, aspect=(edit["aspect"] or "source"))
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    # Snapshot BEFORE applying, so undo returns to the pre-prompt version.
    _snapshot_edit(conn, edit_id, prompt)
    _replace_timeline(conn, edit_id, result.selections)
    # The model fills aspect ONLY when the instruction asked to reframe (e.g. "make it
    # square"), so a chat that doesn't mention framing leaves the edit's aspect intact
    # — an explicit gear choice is never clobbered unless the user asks.
    new_aspect = getattr(result, "aspect", None)
    new_aspect = new_aspect if new_aspect in ASPECT_DIMS else None
    if new_aspect and new_aspect != (edit["aspect"] or "source"):
        conn.execute("UPDATE edits SET aspect = ? WHERE id = ?", (new_aspect, edit_id))
    _apply_auto_framing(conn, edit_id)  # timeline was fully replaced; frame the fresh items
    # Then apply any chat-driven framing on top (sticky manual crops), overriding auto.
    _apply_framing_edits(conn, edit_id, getattr(result, "crops", None))
    conn.execute(
        "INSERT INTO edit_messages (edit_id, role, content) VALUES (?, 'user', ?)",
        (edit_id, prompt),
    )
    conn.execute(
        "INSERT INTO edit_messages (edit_id, role, content) VALUES (?, 'assistant', ?)",
        (edit_id, result.reply),
    )
    conn.commit()
    conn.close()
    return jsonify({
        "reply": result.reply, "count": len(result.selections), "can_undo": True,
        "aspect": new_aspect,   # non-null when the chat changed the frame (UI can resync the gear)
    })


@bp.post("/api/edits/<int:edit_id>/undo")
def undo_edit(edit_id):
    """Pop the most recent snapshot and restore the timeline to it."""
    import json
    conn = get_conn()
    snap = conn.execute(
        "SELECT * FROM edit_snapshots WHERE edit_id = ? ORDER BY id DESC LIMIT 1",
        (edit_id,),
    ).fetchone()
    if not snap:
        conn.close()
        return {"error": "nothing to undo"}, 400

    rows = json.loads(snap["data"])
    conn.execute("DELETE FROM timeline_items WHERE edit_id = ?", (edit_id,))
    for r in rows:
        conn.execute(
            "INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
            "VALUES (?, ?, ?, ?, ?)",
            (edit_id, r["clip_id"], r["position"], r["in_point"], r["out_point"]),
        )
    conn.execute("DELETE FROM edit_snapshots WHERE id = ?", (snap["id"],))
    # Drop the last assistant/user message pair so the transcript matches the state.
    last = conn.execute(
        "SELECT id FROM edit_messages WHERE edit_id = ? ORDER BY id DESC LIMIT 2",
        (edit_id,),
    ).fetchall()
    for m in last:
        conn.execute("DELETE FROM edit_messages WHERE id = ?", (m["id"],))
    remaining = conn.execute(
        "SELECT COUNT(*) AS c FROM edit_snapshots WHERE edit_id = ?", (edit_id,)
    ).fetchone()["c"]
    conn.commit()
    conn.close()
    return jsonify({"restored": snap["label"], "can_undo": remaining > 0})


@bp.post("/api/generate-edit")
def generate_edit_from_scratch():
    """One-shot: prompt -> a brand-new edit (optionally inside a campaign). Returns the
    new edit id so the caller can jump into the editor. Runs the model first so a
    failed generation never leaves an empty edit behind."""
    data = request.json or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}, 400

    clip_ids = data.get("clip_ids") or []
    try:
        clip_ids = [int(c) for c in clip_ids]
    except (TypeError, ValueError):
        return {"error": "clip_ids must be a list of integers"}, 400
    campaign_id = data.get("campaign_id")

    # An explicit non-'source' aspect from the editor's Frame gear wins over anything
    # the model infers. 'source' or missing means "not explicitly chosen" — let the
    # inferred plan.aspect fill it in below.
    req_aspect = (data.get("aspect") or "").strip()
    explicit_aspect = req_aspect if req_aspect in ASPECT_DIMS else None

    conn = get_conn()
    clips = _pool_for_generation(conn, clip_ids, campaign_id)
    if not clips:
        conn.close()
        return {"error": "No downloaded clips to assemble from. Import/pull clips into "
                "your media folder first — catalog-only clips can't be cut."}, 400
    full_prompt = _prompt_with_campaign_context(conn, campaign_id, prompt)
    try:
        plan = generate_rough_cut(full_prompt, clips)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    # Precedence: explicit gear choice > model-inferred aspect > 'source'.
    plan_aspect = getattr(plan, "aspect", None)
    plan_aspect = plan_aspect if plan_aspect in ASPECT_DIMS else None  # ignore 'source'/null
    if explicit_aspect:
        aspect, aspect_inferred = explicit_aspect, False
    elif plan_aspect:
        aspect, aspect_inferred = plan_aspect, True
    else:
        aspect, aspect_inferred = "source", False

    # Auto-name: prefer the model's short concept line, else fall back to the prompt.
    concept = (getattr(plan, "concept", "") or "").strip()
    auto = concept or prompt
    name = (data.get("name") or "").strip() or (auto[:57] + ("…" if len(auto) > 57 else ""))
    cur = conn.execute(
        "INSERT INTO edits (name, campaign_id, aspect) VALUES (?, ?, ?)",
        (name, campaign_id, aspect),
    )
    edit_id = cur.lastrowid
    for i, sel in enumerate(plan.selections):
        conn.execute(
            """INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point)
               VALUES (?, ?, ?, ?, ?)""",
            (edit_id, sel.clip_id, i, sel.in_point, sel.out_point),
        )
    _apply_auto_framing(conn, edit_id)  # subject-track framing when aspect != source
    conn.commit()
    conn.close()
    return jsonify({
        "id": edit_id, "name": name, "campaign_id": campaign_id, "aspect": aspect,
        "aspect_inferred": aspect_inferred,
        "concept": plan.concept, "selections": [s.model_dump() for s in plan.selections],
    })


@bp.post("/api/edits/<int:edit_id>/items")
def add_item(edit_id):
    data = request.json
    clip_id = data["clip_id"]
    in_point = float(data.get("in_point", 0))
    out_point = float(data.get("out_point", 0))
    # Optional 0-based index to insert at (e.g. a drag-drop onto the timeline).
    # Omitted -> append to the end.
    position = data.get("position")
    conn = get_conn()
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM timeline_items WHERE edit_id = ?",
        (edit_id,),
    ).fetchone()["m"]
    cur = conn.execute(
        """
        INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point)
        VALUES (?, ?, ?, ?, ?)
        """,
        (edit_id, clip_id, max_pos + 1, in_point, out_point),
    )
    new_id = cur.lastrowid
    # If a drop index was given, splice the new item into that slot and renumber.
    if position is not None:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM timeline_items WHERE edit_id = ? ORDER BY position", (edit_id,)
        )]
        ids.remove(new_id)
        idx = max(0, min(int(position), len(ids)))
        ids.insert(idx, new_id)
        for pos, iid in enumerate(ids):
            conn.execute("UPDATE timeline_items SET position = ? WHERE id = ?", (pos, iid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "item_id": new_id})


@bp.put("/api/edits/<int:edit_id>/items/<int:item_id>")
def update_item(edit_id, item_id):
    data = request.json
    fields, values = [], []
    for key in ("in_point", "out_point", "position",
                "crop_x", "crop_y", "crop_w", "crop_h",
                "kb_x", "kb_y", "kb_w", "kb_h"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])  # crop_*/kb_* may be null to clear
    # A crop/kb write from the client is a human framing decision. Tag it so an aspect
    # change (which recomputes auto framing) won't wipe it. Clearing the crop (Reset to
    # auto: crop_x=null) hands the item back to auto-framing.
    touches_frame = any(k in data for k in ("crop_x", "crop_y", "crop_w", "crop_h",
                                            "kb_x", "kb_y", "kb_w", "kb_h"))
    if touches_frame:
        clearing = "crop_x" in data and data["crop_x"] is None
        fields.append("crop_source = ?")
        values.append(None if clearing else "manual")
    if not fields:
        return {"ok": True}
    values.append(item_id)
    values.append(edit_id)
    conn = get_conn()
    conn.execute(
        f"UPDATE timeline_items SET {', '.join(fields)} WHERE id = ? AND edit_id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.post("/api/edits/<int:edit_id>/items/<int:item_id>/suggest-crop")
def suggest_crop(edit_id, item_id):
    """Have Claude (as director) propose a reframe crop for this item, based on the
    edit's target aspect and a frame sampled at the item's in-point. Saves the crop
    to the item and returns it. No-op error if the edit has no target aspect."""
    conn = get_conn()
    edit = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    item = conn.execute(
        """SELECT timeline_items.*, clips.file_stem
           FROM timeline_items JOIN clips ON clips.id = timeline_items.clip_id
           WHERE timeline_items.id = ? AND edit_id = ?""",
        (item_id, edit_id),
    ).fetchone()
    if not edit or not item:
        conn.close()
        return {"error": "not found"}, 404
    aspect = (edit["aspect"] if "aspect" in edit.keys() else None) or "source"
    if aspect == "source":
        conn.close()
        return {"error": "Set a target frame (e.g. 9:16) before suggesting a crop."}, 400
    source = find_media_file(item["file_stem"])
    if not source:
        conn.close()
        return {"error": f"'{item['file_stem']}' not found in MEDIA_DIR"}, 404

    # Sample a frame at the item's in-point (a moment actually used in the cut).
    ts = max(0.0, float(item["in_point"] or 0))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            frame_path = Path(tmp) / "frame.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(ts), "-i", str(source),
                 "-frames:v", "1", str(frame_path)],
                check=True, capture_output=True,
            )
            image_bytes = frame_path.read_bytes()
        crop = propose_crop(image_bytes, aspect)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    conn.execute(
        """UPDATE timeline_items SET crop_x=?, crop_y=?, crop_w=?, crop_h=?
           WHERE id=? AND edit_id=?""",
        (crop.crop_x, crop.crop_y, crop.crop_w, crop.crop_h, item_id, edit_id),
    )
    conn.commit()
    conn.close()
    return jsonify({
        "crop_x": crop.crop_x, "crop_y": crop.crop_y,
        "crop_w": crop.crop_w, "crop_h": crop.crop_h,
        "reason": crop.reason,
    })


@bp.post("/api/edits/<int:edit_id>/items/<int:item_id>/suggest-follow")
def suggest_follow(edit_id, item_id):
    """Reframe that FOLLOWS a moving subject: locate the subject at the start and end
    of the trimmed span and animate the crop window between them (Ken Burns). Sets
    crop_* (start) and kb_* (end); export interpolates. Falls back to a static crop
    when the subject barely moves."""
    conn = get_conn()
    edit = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    item = conn.execute(
        """SELECT timeline_items.*, clips.file_stem
           FROM timeline_items JOIN clips ON clips.id = timeline_items.clip_id
           WHERE timeline_items.id = ? AND edit_id = ?""",
        (item_id, edit_id),
    ).fetchone()
    if not edit or not item:
        conn.close()
        return {"error": "not found"}, 404
    aspect = (edit["aspect"] if "aspect" in edit.keys() else None) or "source"
    if aspect == "source":
        conn.close()
        return {"error": "Set a target frame (e.g. 9:16) before reframing."}, 400
    source = find_media_file(item["file_stem"])
    if not source:
        conn.close()
        return {"error": f"'{item['file_stem']}' not found in MEDIA_DIR"}, 404

    t_in = max(0.0, float(item["in_point"] or 0))
    t_out = float(item["out_point"] or t_in)
    # Sample just inside each end so we don't land on a black/transition frame.
    span = max(0.0, t_out - t_in)
    t0 = t_in + min(0.15, span * 0.1)
    t1 = t_out - min(0.15, span * 0.1)
    try:
        start = propose_crop(_frame_at(source, t0), aspect)
        end = propose_crop(_frame_at(source, t1), aspect)
    except Exception as e:
        conn.close()
        return {"error": str(e)}, 502

    # If the window hardly moves, keep it static (no kb_*) to avoid needless drift.
    moved = (abs(start.crop_x - end.crop_x) > 0.03 or abs(start.crop_y - end.crop_y) > 0.03
             or abs(start.crop_w - end.crop_w) > 0.03)
    if moved:
        conn.execute(
            """UPDATE timeline_items
               SET crop_x=?, crop_y=?, crop_w=?, crop_h=?, kb_x=?, kb_y=?, kb_w=?, kb_h=?
               WHERE id=? AND edit_id=?""",
            (start.crop_x, start.crop_y, start.crop_w, start.crop_h,
             end.crop_x, end.crop_y, end.crop_w, end.crop_h, item_id, edit_id),
        )
    else:
        conn.execute(
            """UPDATE timeline_items
               SET crop_x=?, crop_y=?, crop_w=?, crop_h=?, kb_x=NULL, kb_y=NULL, kb_w=NULL, kb_h=NULL
               WHERE id=? AND edit_id=?""",
            (start.crop_x, start.crop_y, start.crop_w, start.crop_h, item_id, edit_id),
        )
    conn.commit()
    conn.close()
    return jsonify({
        "motion": bool(moved),
        "start": {"x": start.crop_x, "y": start.crop_y, "w": start.crop_w, "h": start.crop_h},
        "end": {"x": end.crop_x, "y": end.crop_y, "w": end.crop_w, "h": end.crop_h},
        "reason": start.reason,
    })


@bp.delete("/api/edits/<int:edit_id>/items/<int:item_id>")
def delete_item(edit_id, item_id):
    conn = get_conn()
    conn.execute(
        "DELETE FROM timeline_items WHERE id = ? AND edit_id = ?", (item_id, edit_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.post("/api/edits/<int:edit_id>/reorder")
def reorder_items(edit_id):
    item_ids = request.json["item_ids"]
    conn = get_conn()
    for position, item_id in enumerate(item_ids):
        conn.execute(
            "UPDATE timeline_items SET position = ? WHERE id = ? AND edit_id = ?",
            (position, item_id, edit_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.post("/api/edits/<int:edit_id>/export")
def export_campaign(edit_id):
    conn = get_conn()
    campaign = conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    if not campaign:
        conn.close()
        return {"error": "not found"}, 404
    items = conn.execute(
        """
        SELECT timeline_items.*, clips.file_stem, clips.media_path
        FROM timeline_items
        JOIN clips ON clips.id = timeline_items.clip_id
        WHERE edit_id = ?
        ORDER BY position
        """,
        (edit_id,),
    ).fetchall()
    conn.close()

    if not items:
        return {"error": "timeline is empty"}, 400

    # Pre-flight (synchronous, so the user gets an immediate 409): refuse to render if
    # any clip's media is missing, rather than letting ffmpeg fail partway.
    unresolved = []
    plan = []
    for item in items:
        status, path = clip_media_status(item)
        if status != "present":
            unresolved.append(item["file_stem"])
            continue
        d = dict(item)
        d["source"] = path
        plan.append(d)
    if unresolved:
        uniq = ", ".join(dict.fromkeys(unresolved))
        return {"error": f"Can't export — {len(unresolved)} clip(s) have missing media: "
                         f"{uniq}. Run Verify media to relink moved files, or remove them."}, 409

    # Output aspect. 'source'/None => derive ONE common frame from the first clip so
    # mixed-orientation footage still concatenates into a valid file.
    aspect = (campaign["aspect"] if "aspect" in campaign.keys() else None) or "source"
    explicit_aspect = ASPECT_DIMS.get(aspect) is not None
    if explicit_aspect:
        dims = ASPECT_DIMS[aspect]
    else:
        first = _display_dims(Path(plan[0]["source"])) or (1080, 1920)
        dims = (_even(first[0]), _even(first[1]))

    # The N ffmpeg re-encodes + concat can take a while on longer timelines, so run
    # them in a background job (same pattern as imports) and hand back a job_id the UI
    # polls — no request timeout, live progress.
    job_id = _new_job(f"Export · {campaign['name']}", "clip")
    threading.Thread(
        target=_run_export_job,
        args=(job_id, campaign["name"], explicit_aspect, dims, plan),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})
