#!/usr/bin/env python3
"""
Rudimentary local video editor.

Usage:
    MEDIA_DIR=/path/to/local/pulled/footage python3 editor/app.py

Then open http://127.0.0.1:5001
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file, render_template

from db import get_conn, init_db

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "")).expanduser()
CLIPS_OUT = Path(__file__).resolve().parent.parent / "clips_out"

app = Flask(__name__)


def find_media_file(file_stem: str) -> Path | None:
    if not MEDIA_DIR.is_dir():
        return None
    matches = list(MEDIA_DIR.glob(f"{file_stem}.*"))
    return matches[0] if matches else None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/clips")
def list_clips():
    q = request.args.get("q", "").strip().lower()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM clips ORDER BY file_stem").fetchall()
    conn.close()
    clips = [dict(r) for r in rows]
    if q:
        clips = [
            c for c in clips
            if q in (c["description"] or "").lower()
            or q in (c["category"] or "").lower()
            or q in c["file_stem"].lower()
        ]
    for c in clips:
        c["available_locally"] = find_media_file(c["file_stem"]) is not None
    return jsonify(clips)


@app.get("/api/clips/<int:clip_id>/media")
def clip_media(clip_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}, 404
    path = find_media_file(row["file_stem"])
    if not path:
        return {"error": f"'{row['file_stem']}' not found in MEDIA_DIR"}, 404
    return send_file(path)


@app.get("/api/projects")
def list_projects():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/projects")
def create_project():
    name = request.json.get("name", "untitled").strip()
    conn = get_conn()
    cur = conn.execute("INSERT INTO projects (name) VALUES (?)", (name,))
    conn.commit()
    project_id = cur.lastrowid
    conn.close()
    return jsonify({"id": project_id, "name": name})


@app.get("/api/projects/<int:project_id>")
def get_project(project_id):
    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return {"error": "not found"}, 404
    items = conn.execute(
        """
        SELECT timeline_items.*, clips.file_stem, clips.description, clips.duration_s AS clip_duration_s
        FROM timeline_items
        JOIN clips ON clips.id = timeline_items.clip_id
        WHERE project_id = ?
        ORDER BY position
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return jsonify({**dict(project), "items": [dict(i) for i in items]})


@app.post("/api/projects/<int:project_id>/items")
def add_item(project_id):
    data = request.json
    clip_id = data["clip_id"]
    in_point = float(data.get("in_point", 0))
    out_point = float(data.get("out_point", 0))
    conn = get_conn()
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM timeline_items WHERE project_id = ?",
        (project_id,),
    ).fetchone()["m"]
    conn.execute(
        """
        INSERT INTO timeline_items (project_id, clip_id, position, in_point, out_point)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, clip_id, max_pos + 1, in_point, out_point),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.put("/api/projects/<int:project_id>/items/<int:item_id>")
def update_item(project_id, item_id):
    data = request.json
    fields, values = [], []
    for key in ("in_point", "out_point", "position"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if not fields:
        return {"ok": True}
    values.append(item_id)
    values.append(project_id)
    conn = get_conn()
    conn.execute(
        f"UPDATE timeline_items SET {', '.join(fields)} WHERE id = ? AND project_id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.delete("/api/projects/<int:project_id>/items/<int:item_id>")
def delete_item(project_id, item_id):
    conn = get_conn()
    conn.execute(
        "DELETE FROM timeline_items WHERE id = ? AND project_id = ?", (item_id, project_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/projects/<int:project_id>/reorder")
def reorder_items(project_id):
    item_ids = request.json["item_ids"]
    conn = get_conn()
    for position, item_id in enumerate(item_ids):
        conn.execute(
            "UPDATE timeline_items SET position = ? WHERE id = ? AND project_id = ?",
            (position, item_id, project_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/projects/<int:project_id>/export")
def export_project(project_id):
    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    items = conn.execute(
        """
        SELECT timeline_items.*, clips.file_stem
        FROM timeline_items
        JOIN clips ON clips.id = timeline_items.clip_id
        WHERE project_id = ?
        ORDER BY position
        """,
        (project_id,),
    ).fetchall()
    conn.close()

    if not items:
        return {"error": "timeline is empty"}, 400

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        segment_paths = []
        for i, item in enumerate(items):
            source = find_media_file(item["file_stem"])
            if not source:
                return {"error": f"'{item['file_stem']}' not found in MEDIA_DIR"}, 404
            segment = tmp / f"segment_{i:03d}.mp4"
            duration = item["out_point"] - item["in_point"]
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(item["in_point"]),
                    "-i", str(source),
                    "-t", str(duration),
                    "-c:v", "libx264", "-c:a", "aac",
                    str(segment),
                ],
                check=True, capture_output=True,
            )
            segment_paths.append(segment)

        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in segment_paths)
        )

        CLIPS_OUT.mkdir(parents=True, exist_ok=True)
        output_path = CLIPS_OUT / f"{project['name'].replace(' ', '_')}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(output_path),
            ],
            check=True, capture_output=True,
        )

    return jsonify({"output": str(output_path)})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)
