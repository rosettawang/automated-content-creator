"""DB-driven publishing scheduler (spec: specs/social-core.md).

The DB is the schedule — nothing fires from memory, so a restart loses nothing. One
poll loop claims due `scheduled` rows atomically (single UPDATE … WHERE status=…), and
only the claim winner publishes, which is what prevents double-posting. Each publish
runs as a normal job so progress shows in the existing jobs UI.

Safety (non-negotiable, see spec): never auto-retry a publish that may have gone out —
on restart, interrupted `publishing`/`claimed` rows are marked `needs_review`, not
requeued. Real posting is gated three ways: SOCIAL_DRY_RUN, per-campaign arm, and a
registered adapter.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from db import get_conn
from jobs_runtime import _new_job, _update_job
from social.base import get_adapter, dry_run_enabled

log = logging.getLogger("editor.social.scheduler")

POLL_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _idempotency_key(post: dict) -> str:
    # Stable per (post, its schedule slot); a re-fire of the same slot is a no-op.
    return f"post:{post['id']}:{post['scheduled_at'] or 'now'}"


def claim_due_posts(conn) -> list[int]:
    """Atomically move every due `scheduled` post to `claimed`; return the ids WE won.
    The `WHERE status='scheduled'` guard means a racing claimer (or a duplicate loop)
    can't grab the same row twice."""
    now = _now_iso()
    due = conn.execute(
        "SELECT id FROM posts WHERE status = 'scheduled' AND scheduled_at IS NOT NULL "
        "AND scheduled_at <= ? ORDER BY scheduled_at",
        (now,),
    ).fetchall()
    claimed: list[int] = []
    for r in due:
        cur = conn.execute(
            "UPDATE posts SET status = 'claimed', claimed_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'scheduled'",
            (now, now, r["id"]),
        )
        if cur.rowcount == 1:
            claimed.append(r["id"])
    conn.commit()
    return claimed


def publish_post(post_id: int) -> None:
    """Publish one claimed post as a job: claimed → publishing → published | failed.
    Idempotent: a post that already has an external_id is treated as done."""
    conn = get_conn()
    post = conn.execute(
        """SELECT p.*, c.publishing_armed AS _armed
           FROM posts p JOIN campaigns c ON c.id = p.campaign_id
           WHERE p.id = ?""",
        (post_id,),
    ).fetchone()
    if post is None:
        conn.close()
        return
    post = dict(post)

    # Already published → no-op (idempotency at the row level).
    if post.get("external_id"):
        conn.close()
        return

    # Move claimed → publishing (guarded so only one worker proceeds).
    now = _now_iso()
    key = post.get("idempotency_key") or _idempotency_key(post)
    moved = conn.execute(
        "UPDATE posts SET status = 'publishing', idempotency_key = ?, updated_at = ? "
        "WHERE id = ? AND status = 'claimed'",
        (key, now, post_id),
    )
    conn.commit()
    if moved.rowcount != 1:
        conn.close()  # not in 'claimed' (cancelled, already publishing, …) — leave it
        return
    post["idempotency_key"] = key

    job_id = _new_job(f"Publishing to {post['platform']}", unit="post")
    _update_job(job_id, phase="publishing", total=1)
    try:
        # Arm gate: real posting needs SOCIAL_DRY_RUN=0 AND the campaign armed.
        if not dry_run_enabled() and not post.get("_armed"):
            raise RuntimeError(
                "Campaign is not armed for live publishing. Arm it in the campaign "
                "settings (or keep SOCIAL_DRY_RUN=1) to post."
            )
        adapter = get_adapter(post["platform"])
        external_id = adapter.publish(post)
        conn.execute(
            "UPDATE posts SET status = 'published', external_id = ?, published_at = ?, "
            "error = NULL, updated_at = ? WHERE id = ?",
            (external_id, _now_iso(), _now_iso(), post_id),
        )
        conn.commit()
        _update_job(job_id, phase="done", done=1, finished=True)
        log.info("post %s published (external_id=%s, dry_run=%s)",
                 post_id, external_id, dry_run_enabled())
    except Exception as e:  # fail loudly: the row goes red, the error is stored
        conn.execute(
            "UPDATE posts SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (str(e), _now_iso(), post_id),
        )
        conn.commit()
        _update_job(job_id, finished=True, error=str(e))
        log.warning("post %s failed: %s", post_id, e)
    finally:
        conn.close()


def enqueue_publish(post_id: int) -> None:
    """Publish now: caller has already moved the row to `claimed`. Runs in a thread so
    the HTTP request returns immediately (progress shows in the jobs UI)."""
    threading.Thread(target=publish_post, args=(post_id,), daemon=True).start()


def reconcile_orphaned_posts() -> None:
    """On boot, any post left mid-flight (`claimed`/`publishing`) lost its worker when
    the process died. We must NOT auto-retry — the publish may have gone out. Mark it
    for manual review; a human checks the platform, then re-schedules or cancels."""
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE posts SET status = 'needs_review', "
            "error = 'interrupted mid-publish (app restarted) — check the platform "
            "before retrying', updated_at = ? "
            "WHERE status IN ('claimed', 'publishing')",
            (_now_iso(),),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("reconcile_orphaned_posts: %s", e)


_started = False
_start_lock = threading.Lock()


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            ids = claim_due_posts(conn)
            conn.close()
            for pid in ids:
                enqueue_publish(pid)
        except Exception as e:
            log.warning("scheduler loop: %s", e)
        time.sleep(POLL_SECONDS)


def start_scheduler() -> None:
    """Idempotently start the single poll loop (mirrors the embed worker's boot)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, daemon=True).start()
        log.info("social scheduler started (dry_run=%s)", dry_run_enabled())
