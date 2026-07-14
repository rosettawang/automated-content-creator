#!/usr/bin/env python3
"""
Library data-hygiene checks for editor/data/editor.db.

Run the app *stopped* and back up the DB first (this script can write rows):
    cp editor/data/editor.db editor/data/editor.db.bak
    python3 editor/scripts_hygiene.py            # report only
    python3 editor/scripts_hygiene.py --fix      # apply the safe repairs

Checks (each prints a PASS/WARN line):
  1. CORRECTION-notes    editorial "CORRECTION (…)" notes left in descriptions.
                         --fix moves the note into `context` and keeps the
                         description clean (the description feeds the AI catalog
                         and UI verbatim).
  2. generation-pool     stills (kind='photo') and sub-second videos that would
                         pollute the video-generation pool. Reported only -- the
                         pool now filters these in code (_usable_for_generation);
                         this surfaces the underlying rows.
  3. duration/dimensions duration_s + width/height that disagree with ffprobe for
                         clips whose file is local. --fix rewrites them to the
                         ffprobe truth.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from db import get_conn  # noqa: E402

MEDIA_DIR = Path(os.environ["MEDIA_DIR"]).expanduser() if os.environ.get("MEDIA_DIR") else None
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tiff", ".webp"}

# "CORRECTION (keyframe guessed 'gravel path'): the real description…"
_CORRECTION_RE = re.compile(r"^\s*CORRECTION\s*\([^)]*\)\s*:?\s*", re.IGNORECASE)


def _local_file(stem: str) -> Path | None:
    if MEDIA_DIR is None or not MEDIA_DIR.is_dir():
        return None
    matches = list(MEDIA_DIR.glob(f"{stem}.*"))
    return matches[0] if matches else None


def _ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return round(float(out), 1)
    except Exception:
        return None


def _ffprobe_dimensions(path: Path) -> tuple[int, int] | None:
    """Return the *display* (width, height) of the first video stream.

    iPhone footage is encoded landscape with a 90/270 rotation flag, so the raw
    stream dimensions are the transpose of what's shown. We read the rotation (from
    stream tags or the display-matrix side-data, whichever ffmpeg exposes) and swap
    when the picture is turned on its side -- matching the display dims the app
    stores, so we never "fix" a correct portrait clip into a swapped landscape one."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_tags=rotate:side_data=rotation",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
        streams = json.loads(out).get("streams") or []
        if not streams:
            return None
        s = streams[0]  # first video stream only -- ignore thumbnail/data streams
        w, h = int(s["width"]), int(s["height"])
        rot = 0
        if s.get("tags", {}).get("rotate"):
            rot = int(s["tags"]["rotate"])
        for sd in s.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return None


def check_corrections(conn, fix: bool) -> bool:
    rows = conn.execute(
        "SELECT id, file_stem, description, context FROM clips "
        "WHERE description LIKE '%CORRECTION%'"
    ).fetchall()
    hits = [r for r in rows if _CORRECTION_RE.match(r["description"] or "")]
    if not hits:
        print("PASS  correction-notes: no CORRECTION notes in descriptions")
        return True
    print(f"WARN  correction-notes: {len(hits)} description(s) carry a CORRECTION note")
    for r in hits:
        note = _CORRECTION_RE.match(r["description"]).group(0).strip()
        cleaned = _CORRECTION_RE.sub("", r["description"]).strip()
        print(f"      - {r['file_stem']}: {note[:70]}")
        if fix:
            merged_context = "\n".join(p for p in [(r["context"] or "").strip(), note] if p)
            conn.execute(
                "UPDATE clips SET description = ?, context = ? WHERE id = ?",
                (cleaned, merged_context, r["id"]),
            )
    if fix:
        conn.commit()
        print(f"      fixed: stripped {len(hits)} note(s) into context")
    return fix  # a WARN is "resolved" for this run only if we fixed it


def check_generation_pool(conn, fix: bool) -> bool:
    photos = conn.execute("SELECT file_stem FROM clips WHERE kind = 'photo'").fetchall()
    subsec = conn.execute(
        "SELECT file_stem, duration_s FROM clips "
        "WHERE (kind IS NULL OR kind != 'photo') AND duration_s IS NOT NULL AND duration_s < 1.0"
    ).fetchall()
    if not photos and not subsec:
        print("PASS  generation-pool: no stills or sub-second videos")
        return True
    print(f"WARN  generation-pool: {len(photos)} photo(s), {len(subsec)} sub-second video(s) "
          "(excluded from generation by _usable_for_generation)")
    for r in subsec:
        print(f"      - {r['file_stem']}: {r['duration_s']}s video")
    # No auto-fix: exclusion is enforced in code, and deleting content isn't our call.
    return True  # informational; the code filter is the real guard


def check_dimensions(conn, fix: bool) -> bool:
    rows = conn.execute(
        "SELECT id, file_stem, duration_s, width, height, kind FROM clips"
    ).fetchall()
    drift = []
    for r in rows:
        f = _local_file(r["file_stem"])
        if f is None:
            continue
        is_image = f.suffix.lower() in IMAGE_EXTS
        expected_dur = 0.0 if is_image else _ffprobe_duration(f)
        dims = _ffprobe_dimensions(f)
        problems = {}
        if expected_dur is not None and r["duration_s"] != expected_dur:
            problems["duration_s"] = (r["duration_s"], expected_dur)
        if dims is not None:
            if r["width"] != dims[0]:
                problems["width"] = (r["width"], dims[0])
            if r["height"] != dims[1]:
                problems["height"] = (r["height"], dims[1])
        if problems:
            drift.append((r, problems))

    if not drift:
        print("PASS  duration/dimensions: DB matches ffprobe for all local clips")
        return True
    print(f"WARN  duration/dimensions: {len(drift)} local clip(s) drift from ffprobe")
    for r, problems in drift:
        desc = ", ".join(f"{k} {old}->{new}" for k, (old, new) in problems.items())
        print(f"      - {r['file_stem']}: {desc}")
        if fix:
            sets = ", ".join(f"{k} = ?" for k in problems)
            vals = [new for _, new in problems.values()]
            conn.execute(f"UPDATE clips SET {sets} WHERE id = ?", (*vals, r["id"]))
    if fix:
        conn.commit()
        print(f"      fixed: rewrote {len(drift)} clip(s) to ffprobe truth")
    return fix


def main() -> int:
    ap = argparse.ArgumentParser(description="Library data-hygiene checks.")
    ap.add_argument("--fix", action="store_true", help="apply the safe repairs")
    args = ap.parse_args()

    if MEDIA_DIR is None:
        print("note: MEDIA_DIR unset -- duration/dimension check will find no local files.")

    conn = get_conn()
    results = [
        check_corrections(conn, args.fix),
        check_generation_pool(conn, args.fix),
        check_dimensions(conn, args.fix),
    ]
    conn.close()
    # Exit non-zero if anything is still unresolved (useful in CI / pre-commit).
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
