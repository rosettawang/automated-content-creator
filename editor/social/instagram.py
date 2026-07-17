"""Instagram adapter (spec: specs/social-adapters.md — Phase C).

All Composio calls live here, so a Composio change touches only this file. Platform
quirks are enforced as pre-publish validation (fail loud, like the export pre-flight),
NOT swallowed. This adapter is only ever reached when SOCIAL_DRY_RUN=0 AND the campaign
is armed AND it's registered — so by default (dry-run) it never runs.

Composio action slugs below are conventional and MUST be re-verified against your
Composio catalog with a test account before first live use (APIs drift — the spec
flags this). Use `list_toolkit_actions('instagram')` to see the real slugs.
"""
from __future__ import annotations

import json
import logging

from composio_wrapper import execute_action, list_toolkit_actions
from social.base import register

log = logging.getLogger("editor.social.instagram")

CAPTION_MAX = 2200          # Instagram caption hard limit
REELS_MAX_SECONDS = 90      # Reels length cap
_VIDEO_EXTS = (".mp4", ".mov", ".m4v")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")

# Conventional Composio slugs — VERIFY before live use.
_SLUG_PUBLISH = "INSTAGRAM_CREATE_POST"
_SLUG_INSIGHTS = "INSTAGRAM_GET_MEDIA_INSIGHTS"


def validate(post: dict) -> None:
    """Pre-publish checks. Raise ValueError with an actionable message on any problem
    — the caller turns the post red with this text rather than failing at the API."""
    caption = post.get("caption") or ""
    if len(caption) > CAPTION_MAX:
        raise ValueError(f"Caption is {len(caption)} chars; Instagram allows {CAPTION_MAX}.")
    media = post.get("media_path")
    if not media:
        raise ValueError("Instagram posts need media — export the cut first.")
    low = media.lower()
    if not low.endswith(_VIDEO_EXTS + _IMAGE_EXTS):
        raise ValueError(f"Unsupported media type for Instagram: {media}")
    dur = post.get("media_duration_s")
    if low.endswith(_VIDEO_EXTS) and dur is not None and dur > REELS_MAX_SECONDS:
        raise ValueError(f"Reel is {dur:.0f}s; Instagram Reels cap at {REELS_MAX_SECONDS}s. Trim it.")


class InstagramAdapter:
    platform = "instagram"

    def verify_connection(self) -> bool:
        """Connect-UI pre-flight: the toolkit resolves against the account, which
        means an account is connected and usable. Raises with the reason if not."""
        try:
            actions = list_toolkit_actions("instagram")
        except Exception as e:
            raise RuntimeError(f"Instagram not connected in Composio: {e}") from e
        return len(actions) > 0

    def publish(self, post: dict) -> str:
        validate(post)
        result = execute_action(_SLUG_PUBLISH, {
            "account_ref": post.get("account_ref"),
            "media_path": post.get("media_path"),
            "caption": (post.get("caption") or "") +
                       (("\n\n" + post["hashtags"]) if post.get("hashtags") else ""),
            # Idempotency: pass our key so a retry of the same slot can be de-duped.
            "idempotency_key": post.get("idempotency_key"),
        })
        external_id = (result or {}).get("data", {}).get("id") if isinstance(result, dict) else None
        if not external_id:
            raise RuntimeError(f"Instagram publish returned no post id: {json.dumps(result)[:300]}")
        return str(external_id)

    def fetch_metrics(self, post: dict) -> dict:
        if not post.get("external_id"):
            return {}
        result = execute_action(_SLUG_INSIGHTS, {
            "account_ref": post.get("account_ref"),
            "media_id": post["external_id"],
        })
        data = (result or {}).get("data", {}) if isinstance(result, dict) else {}
        return {
            "impressions": data.get("impressions"),
            "reach": data.get("reach"),
            "likes": data.get("likes"),
            "comments": data.get("comments"),
            "shares": data.get("shares"),
            "saves": data.get("saved") or data.get("saves"),
            "raw": json.dumps(data)[:4000],
        }


register(InstagramAdapter())
