"""Metrics ingestion + the recommendations summary (spec: specs/social-analytics.md).

Closes the loop: what got posted → how it did → what to make next. Ingestion is a
normal job (one failing post never fails the batch); `summarize_campaign` is a PURE
function over already-fetched rows (no live calls), so it's trivially testable and can
feed the campaign chat / suggest_content prompts.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from db import get_conn
from jobs_runtime import _new_job, _update_job
from social.base import get_adapter

log = logging.getLogger("editor.social.analytics")

_METRIC_COLS = ("impressions", "reach", "likes", "comments", "shares", "saves")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_metrics(campaign_id: int) -> None:
    """For every published post in the campaign, fetch fresh metrics and APPEND a
    `post_metrics` row (append-only — trends need history). Runs as a job; a fetch
    failure for one post is logged and skipped, never failing the whole batch."""
    conn = get_conn()
    posts = conn.execute(
        "SELECT * FROM posts WHERE campaign_id = ? AND status = 'published'",
        (campaign_id,),
    ).fetchall()
    job_id = _new_job(f"Fetching metrics ({len(posts)} posts)", unit="post")
    _update_job(job_id, phase="fetching", total=len(posts))
    done = 0
    for row in posts:
        post = dict(row)
        try:
            m = get_adapter(post["platform"]).fetch_metrics(post)
            conn.execute(
                """INSERT INTO post_metrics
                     (post_id, fetched_at, impressions, reach, likes, comments,
                      shares, saves, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (post["id"], _now_iso(), m.get("impressions"), m.get("reach"),
                 m.get("likes"), m.get("comments"), m.get("shares"), m.get("saves"),
                 m.get("raw")),
            )
            conn.commit()
        except Exception as e:  # non-fatal per spec — keep going
            log.warning("metrics fetch failed for post %s: %s", post["id"], e)
        done += 1
        _update_job(job_id, done=done, current=post.get("caption") or post["platform"])
    conn.close()
    _update_job(job_id, phase="done", finished=True)


def campaign_metrics_summary(conn, campaign_id: int) -> dict:
    """Assemble published posts + their LATEST metrics + a subject label, then
    summarize. Shared by the /summary endpoint, the Learn card, and the chat prompt."""
    rows = conn.execute(
        """SELECT p.*, e.name AS subject,
                  m.impressions, m.reach, m.likes, m.comments, m.shares, m.saves
           FROM posts p
           LEFT JOIN edits e ON e.id = p.edit_id
           LEFT JOIN post_metrics m ON m.id = (
               SELECT id FROM post_metrics WHERE post_id = p.id
               ORDER BY fetched_at DESC, id DESC LIMIT 1)
           WHERE p.campaign_id = ? AND p.status = 'published'""",
        (campaign_id,),
    ).fetchall()
    return summarize_campaign([dict(r) for r in rows])


def summarize_campaign(posts: list[dict], clips: list[dict] | None = None) -> dict:
    """PURE. `posts` = published posts, each already decorated with its latest metrics
    (reach/saves/…) and optional boost_spend + a subject label. Returns patterns the
    hub's Learn card and the recommendation prompts consume. No DB, no network."""
    measured = [p for p in posts if p.get("reach") is not None]
    if not measured:
        return {"has_data": False, "headline": "No metrics yet — publish and fetch metrics to see what's working."}

    def reach(p): return p.get("reach") or 0
    total_reach = sum(reach(p) for p in measured)
    total_spend = sum((p.get("boost_spend") or 0) for p in measured)
    top = sorted(measured, key=reach, reverse=True)[:3]

    # Per-platform average reach — which channel is pulling weight.
    by_platform: dict[str, dict] = {}
    for p in measured:
        b = by_platform.setdefault(p["platform"], {"n": 0, "reach": 0})
        b["n"] += 1
        b["reach"] += reach(p)
    for b in by_platform.values():
        b["avg_reach"] = round(b["reach"] / b["n"]) if b["n"] else 0

    best = top[0]
    best_label = (best.get("subject") or best.get("caption") or best["platform"]).strip()
    headline = (
        f"Top post so far: “{best_label[:60]}” ({reach(best):,} reach). "
        f"{len(measured)} published, {total_reach:,} total reach"
        + (f", ${total_spend:,.0f} ad spend." if total_spend else ".")
    )
    return {
        "has_data": True,
        "headline": headline,
        "published_count": len(measured),
        "total_reach": total_reach,
        "total_spend": total_spend,
        "by_platform": by_platform,
        "top_posts": [
            {"id": p.get("id"), "platform": p["platform"],
             "label": (p.get("subject") or p.get("caption") or "").strip()[:60],
             "reach": reach(p), "saves": p.get("saves") or 0}
            for p in top
        ],
    }
