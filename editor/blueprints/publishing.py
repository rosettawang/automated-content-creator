"""Publishing endpoints (spec: specs/social-core.md).

Thin HTTP layer over the posts state machine. All the risky bits — claiming,
publishing, idempotency, the arm gate — live in `social/`. Post-now and scheduled
posts share ONE path (claim → publish job); post-now just claims immediately.
"""
from datetime import datetime, timezone
import logging

from flask import Blueprint
from core import *  # jsonify, request, db_conn, …

from social.base import PLATFORMS, dry_run_enabled
from social.scheduler import enqueue_publish

log = logging.getLogger("editor.blueprints.publishing")

bp = Blueprint("publishing", __name__)

# Fields the client may set on create/update (never status/external_id/etc. directly).
_EDITABLE = ("platform", "caption", "hashtags", "edit_id", "account_ref",
             "media_path", "scheduled_at")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_row(conn, post_id):
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


@bp.get("/api/campaigns/<int:campaign_id>/posts")
def list_posts(campaign_id):
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE campaign_id = ? ORDER BY "
            "COALESCE(scheduled_at, created_at) DESC, id DESC",
            (campaign_id,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/api/campaigns/<int:campaign_id>/posts")
def create_post(campaign_id):
    data = request.json or {}
    platform = (data.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        return {"error": f"unknown platform '{platform}'. One of: {', '.join(PLATFORMS)}"}, 400

    publish_now = bool(data.get("publish_now"))
    scheduled_at = (data.get("scheduled_at") or "").strip() or None
    # A post-now post carries no schedule; a scheduled post needs a time; else draft.
    status = "claimed" if publish_now else ("scheduled" if scheduled_at else "draft")

    with db_conn() as conn:
        if not conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone():
            return {"error": "campaign not found"}, 404
        now = _now_iso()
        cur = conn.execute(
            """INSERT INTO posts
                 (campaign_id, edit_id, platform, account_ref, caption, hashtags,
                  media_path, scheduled_at, status, claimed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (campaign_id, data.get("edit_id"), platform, data.get("account_ref"),
             data.get("caption"), data.get("hashtags"), data.get("media_path"),
             None if publish_now else scheduled_at, status,
             now if publish_now else None, now, now),
        )
        post_id = cur.lastrowid
        row = dict(_post_row(conn, post_id))

    if publish_now:
        enqueue_publish(post_id)  # claimed → publishing → published, in a job
    return jsonify({**row, "dry_run": dry_run_enabled()}), 201


@bp.get("/api/posts/<int:post_id>")
def get_post(post_id):
    with db_conn() as conn:
        row = _post_row(conn, post_id)
        if not row:
            return {"error": "not found"}, 404
        metrics = conn.execute(
            "SELECT * FROM post_metrics WHERE post_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (post_id,),
        ).fetchone()
    return jsonify({**dict(row), "latest_metrics": dict(metrics) if metrics else None})


@bp.put("/api/posts/<int:post_id>")
def update_post(post_id):
    """Edit a not-yet-live post (draft/scheduled). Once claimed/publishing/published
    it's immutable from here — you don't rewrite a post that may already be out."""
    data = request.json or {}
    with db_conn() as conn:
        row = _post_row(conn, post_id)
        if not row:
            return {"error": "not found"}, 404
        if row["status"] not in ("draft", "scheduled", "failed", "needs_review"):
            return {"error": f"can't edit a post in status '{row['status']}'"}, 409
        sets, vals = [], []
        for f in _EDITABLE:
            if f in data:
                sets.append(f"{f} = ?")
                vals.append(data[f])
        # Re-deriving status from scheduled_at keeps draft/scheduled coherent.
        if "scheduled_at" in data:
            sets.append("status = ?")
            vals.append("scheduled" if (data.get("scheduled_at") or "").strip() else "draft")
        if not sets:
            return {"error": "nothing to update"}, 400
        sets.append("updated_at = ?"); vals.append(_now_iso())
        vals.append(post_id)
        conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", tuple(vals))
        row = dict(_post_row(conn, post_id))
    return jsonify(row)


@bp.post("/api/posts/<int:post_id>/publish-now")
def publish_now(post_id):
    with db_conn() as conn:
        row = _post_row(conn, post_id)
        if not row:
            return {"error": "not found"}, 404
        if row["status"] not in ("draft", "scheduled", "failed", "needs_review"):
            return {"error": f"can't publish a post in status '{row['status']}'"}, 409
        # Claim it here so the row can only be published once (guarded by status).
        moved = conn.execute(
            "UPDATE posts SET status = 'claimed', claimed_at = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (_now_iso(), _now_iso(), post_id, row["status"]),
        )
        ok = moved.rowcount == 1
    if not ok:
        return {"error": "post changed state; reload and retry"}, 409
    enqueue_publish(post_id)
    return jsonify({"status": "claimed", "dry_run": dry_run_enabled()})


@bp.post("/api/posts/<int:post_id>/cancel")
def cancel_post(post_id):
    """Cancel a not-yet-live post. Published content is never deleted from here."""
    with db_conn() as conn:
        row = _post_row(conn, post_id)
        if not row:
            return {"error": "not found"}, 404
        if row["status"] not in ("draft", "scheduled", "failed", "needs_review"):
            return {"error": f"can't cancel a post in status '{row['status']}'"}, 409
        conn.execute(
            "UPDATE posts SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (_now_iso(), post_id),
        )
    return jsonify({"status": "cancelled"})


@bp.post("/api/campaigns/<int:campaign_id>/arm")
def set_armed(campaign_id):
    """The visible per-campaign arm switch (safety rail #1). Off by default; even when
    on, real posting still needs SOCIAL_DRY_RUN=0 and a registered adapter."""
    armed = 1 if (request.json or {}).get("armed") else 0
    with db_conn() as conn:
        if not conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone():
            return {"error": "campaign not found"}, 404
        conn.execute("UPDATE campaigns SET publishing_armed = ? WHERE id = ?", (armed, campaign_id))
    return jsonify({"campaign_id": campaign_id, "publishing_armed": bool(armed),
                    "dry_run": dry_run_enabled()})
