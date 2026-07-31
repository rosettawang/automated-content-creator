"""TikTok adapter (spec: specs/social-adapters.md — Phase C, Tier 1).

All Composio calls live here, so a Composio change touches only this file. Platform
quirks are enforced as pre-publish validation (fail loud, like the export pre-flight),
NOT swallowed. This adapter is only ever reached when SOCIAL_DRY_RUN=0 AND the campaign
is armed AND it's registered — so by default (dry-run) it never runs.

The spec flags TikTok's key gotcha: the Content Posting API can PULL_FROM_URL (needs a
publicly reachable media URL) OR take a DIRECT FILE UPLOAD. A local single-user app has
no public URL, so this adapter uses the **direct-upload** path — no hosting required.
That path is what MUST be re-verified against your Composio catalog with a test account
before first live use (`list_toolkit_actions('tiktok')` shows the real slugs; APIs drift).
"""
from __future__ import annotations

import json
import logging

from composio_wrapper import execute_action, list_toolkit_actions
from social.base import register

log = logging.getLogger("editor.social.tiktok")

CAPTION_MAX = 2200          # TikTok caption/description hard limit
VIDEO_MAX_SECONDS = 600     # 10 min via the Content Posting API (account-dependent)
_VIDEO_EXTS = (".mp4", ".mov", ".webm")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")  # TikTok photo posts (1–35 images)

# Conventional Composio slugs for the DIRECT-UPLOAD path — VERIFY before live use.
_SLUG_PUBLISH = "TIKTOK_DIRECT_POST_VIDEO"
_SLUG_INSIGHTS = "TIKTOK_GET_VIDEO_METRICS"


def validate(post: dict) -> None:
    """Pre-publish checks. Raise ValueError with an actionable message on any problem
    — the caller turns the post red with this text rather than failing at the API."""
    caption = post.get("caption") or ""
    hashtags = post.get("hashtags") or ""
    # TikTok counts the whole description (caption + hashtags) against the limit.
    combined = len((caption + " " + hashtags).strip())
    if combined > CAPTION_MAX:
        raise ValueError(f"Caption+hashtags are {combined} chars; TikTok allows {CAPTION_MAX}.")
    media = post.get("media_path")
    if not media:
        raise ValueError("TikTok posts need media — export the cut first.")
    low = media.lower()
    if not low.endswith(_VIDEO_EXTS + _IMAGE_EXTS):
        raise ValueError(f"Unsupported media type for TikTok: {media}")
    dur = post.get("media_duration_s")
    if low.endswith(_VIDEO_EXTS) and dur is not None and dur > VIDEO_MAX_SECONDS:
        raise ValueError(
            f"Video is {dur:.0f}s; TikTok's API caps at {VIDEO_MAX_SECONDS}s. Trim it.")


class TikTokAdapter:
    platform = "tiktok"

    def verify_connection(self) -> bool:
        """Connect-UI pre-flight: the toolkit resolves against the account, which
        means an account is connected and usable. Raises with the reason if not."""
        try:
            actions = list_toolkit_actions("tiktok")
        except Exception as e:
            raise RuntimeError(f"TikTok not connected in Composio: {e}") from e
        return len(actions) > 0

    def publish(self, post: dict) -> str:
        validate(post)
        # Direct upload: hand Composio the local media path, not a URL. TikTok returns a
        # publish_id / video id we store as external_id. Our idempotency_key rides along so
        # a retry of the same slot de-dupes instead of double-posting.
        result = execute_action(_SLUG_PUBLISH, {
            "account_ref": post.get("account_ref"),
            "media_path": post.get("media_path"),
            "caption": (post.get("caption") or "") +
                       (("\n\n" + post["hashtags"]) if post.get("hashtags") else ""),
            "idempotency_key": post.get("idempotency_key"),
        })
        data = (result or {}).get("data", {}) if isinstance(result, dict) else {}
        external_id = data.get("id") or data.get("publish_id") or data.get("video_id")
        if not external_id:
            raise RuntimeError(f"TikTok publish returned no post id: {json.dumps(result)[:300]}")
        return str(external_id)

    def fetch_metrics(self, post: dict) -> dict:
        if not post.get("external_id"):
            return {}
        result = execute_action(_SLUG_INSIGHTS, {
            "account_ref": post.get("account_ref"),
            "video_id": post["external_id"],
        })
        data = (result or {}).get("data", {}) if isinstance(result, dict) else {}
        return {
            # TikTok reports views; map to impressions so the shared metrics schema lines up.
            "impressions": data.get("view_count") or data.get("views"),
            "reach": data.get("reach"),
            "likes": data.get("like_count") or data.get("likes"),
            "comments": data.get("comment_count") or data.get("comments"),
            "shares": data.get("share_count") or data.get("shares"),
            "saves": data.get("favorite_count") or data.get("saves"),
            "raw": json.dumps(data)[:4000],
        }


register(TikTokAdapter())
