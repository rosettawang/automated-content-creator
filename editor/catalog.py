"""The AI-facing view of the library: clip decoration, campaign membership, the
generation clip pool + moment attachment, and campaign-context prompting.

Sits above indexing in the graph (it reads indexing's `_indexing` status set);
depends otherwise only on leaves (db, config, media_files, settings). Never
imports core. Re-exported by core for `from core import *`.
"""
from __future__ import annotations

from pathlib import Path

from db import get_conn
from config import classify_kind
from media_files import clip_media_status, find_media_file
from settings import _photos_albums
from indexing import _indexing, _indexing_lock


def _can_redownload(source_kind, source_url) -> bool:
    """Is there enough provenance to fetch this clip's file again? Drive needs its
    link; Photos needs the clip's album link or any remembered album."""
    if source_kind == "drive":
        return bool(source_url)
    if source_kind == "photos":
        return bool(source_url) or bool(_photos_albums())
    return False


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
    defaulting to the front of the clip. Clips without events get an empty list.

    One `IN (...)` query for the whole pool (grouped in Python), not one query per
    clip — the pool is every eligible clip in the library, so this was a real N+1."""
    ids = [c["id"] for c in clips]
    moments: dict[int, list] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in conn.execute(
            f"""SELECT clip_id, kind, label, text, t_start, t_end FROM clip_events
                WHERE clip_id IN ({ph}) AND kind IN ('scene', 'action', 'speech')
                ORDER BY clip_id, t_start""",
            ids,
        ):
            moments.setdefault(r["clip_id"], []).append(
                {"kind": r["kind"], "label": r["label"], "text": r["text"],
                 "t_start": r["t_start"], "t_end": r["t_end"]})
    for c in clips:
        c["moments"] = moments.get(c["id"], [])


def _prompt_with_campaign_context(conn, campaign_id, prompt: str) -> str:
    """Prepend the campaign's saved description so it steers the cut."""
    if not campaign_id:
        return prompt
    row = conn.execute("SELECT name, description FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row and (row["description"] or "").strip():
        return (f"Campaign: {row['name']}\nCampaign context: {row['description'].strip()}\n\n{prompt}")
    return prompt

__all__ = [n for n in dir() if not n.startswith("__")]
