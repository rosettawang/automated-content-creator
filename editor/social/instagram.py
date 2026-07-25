"""Instagram adapter (spec: specs/social-adapters.md — Phase C).

All Composio calls live here, so a Composio change touches only this file. Platform
quirks are enforced as pre-publish validation (fail loud, like the export pre-flight),
NOT swallowed. This adapter is only ever reached when SOCIAL_DRY_RUN=0 AND the campaign
is armed AND it's registered — so by default (dry-run) it never runs.

Slugs below were verified against the Composio Instagram toolkit (version
v20260721_00) in the dashboard on 2026-07-18. The earlier guesses were all deprecated;
publishing is really a TWO-STEP flow — create a media container, then publish it by
its creation_id. Re-verify if Composio bumps the toolkit version.
"""
from __future__ import annotations

import json
import logging
import os

from composio_wrapper import execute_action, list_connected_accounts, get_client
from social.base import register

log = logging.getLogger("editor.social.instagram")

# Fields the connected-account metadata might store the IG Business account id under.
# The numeric IG user id is what the publish API needs — NOT the Composio 'ca_...' ref.
_IG_ID_FIELDS = ("ig_id", "instagram_business_account", "instagram_user_id",
                 "ig_user_id", "user_id", "id")


def _resolve_ig_user_id(account_ref: str) -> str | None:
    """The numeric Instagram Business account id the publish API needs (NOT the
    Composio 'ca_...' ref).

    Resolution order:
      1. `IG_USER_ID` env var (set it in editor/.env) — the reliable override. For
         this Composio version the id is NOT in the connection metadata (extra_data is
         empty) and no Instagram action returns your own id, so this is the intended
         path: paste the numeric id once from Meta Business Suite / Graph API Explorer.
      2. Connection metadata search (kept as a best-effort fallback for Composio
         versions that DO expose it).
    Returns None if neither yields a numeric id; the caller then errors clearly."""
    env_id = (os.environ.get("IG_USER_ID") or "").strip()
    if env_id.isdigit():
        return env_id
    if not account_ref:
        return None
    try:
        acct = get_client().connected_accounts.get(account_ref)
    except Exception as e:
        log.warning("couldn't fetch connected account %s: %s", account_ref, e)
        return None
    d = acct if isinstance(acct, dict) else getattr(acct, "__dict__", {}) or {}
    # Search top level and any nested 'metadata'/'params'/'data' dicts.
    candidates = [d]
    for k in ("metadata", "params", "data", "connection_params", "connectionParams"):
        v = d.get(k)
        if isinstance(v, dict):
            candidates.append(v)
    for c in candidates:
        for f in _IG_ID_FIELDS:
            val = c.get(f)
            if val and str(val).isdigit():   # the IG business id is a long number
                return str(val)
    return None

CAPTION_MAX = 2200          # Instagram caption hard limit
REELS_MAX_SECONDS = 90      # Reels length cap
_VIDEO_EXTS = (".mp4", ".mov", ".m4v")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")

# Verified slugs (Composio Instagram toolkit v20260721_00).
_SLUG_CONTAINER = "INSTAGRAM_POST_IG_USER_MEDIA"          # step 1: create media container
_SLUG_PUBLISH = "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH"    # step 2: publish it (by creation_id)
_SLUG_INSIGHTS = "INSTAGRAM_GET_IG_MEDIA_INSIGHTS"


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
        """Connect-UI pre-flight: is an Instagram account actually connected? Checks
        real connected accounts (not the toolkit's action list, which exists regardless).
        Raises with the reason if nothing usable is connected."""
        try:
            accts = list_connected_accounts("instagram")
        except Exception as e:
            raise RuntimeError(f"Couldn't check Instagram connection in Composio: {e}") from e
        active = [a for a in accts if str(a.get("status", "")).upper() in ("ACTIVE", "CONNECTED", "")]
        if not active:
            raise RuntimeError(
                "No Instagram account connected in Composio. Connect the account "
                "(OAuth) before arming this campaign."
            )
        return True

    def publish(self, post: dict) -> str:
        """Two-step publish: create a media container, then publish it by creation_id.
        A local file is passed as image_file/video_file — Composio uploads it to a
        temporary public URL for Instagram to fetch (so the app needs no public host)."""
        validate(post)
        # Prefer an explicit id; otherwise resolve it from the connected account.
        ig_user_id = post.get("ig_user_id") or _resolve_ig_user_id(post.get("account_ref"))
        if not ig_user_id:
            raise RuntimeError(
                "Couldn't determine the Instagram Business account id (ig_user_id). "
                "For this Composio version it isn't in the connection metadata, so set "
                "IG_USER_ID=<numeric id> in editor/.env (from Meta Business Suite / the "
                "Graph API Explorer: me/accounts?fields=instagram_business_account) and "
                "restart, or pass ig_user_id on the post."
            )
        media = post["media_path"]
        low = media.lower()
        caption = (post.get("caption") or "") + \
                  (("\n\n" + post["hashtags"]) if post.get("hashtags") else "")

        # Step 1 — media container. NOTE: confirm the exact file-argument shape on the
        # first live call; Composio may want a path string, a {name,content} object, or
        # a file handle. image_file/video_file = local upload; image_url/video_url would
        # be a public URL instead.
        args: dict = {"ig_user_id": ig_user_id, "caption": caption}
        if low.endswith(_IMAGE_EXTS):
            args["image_file"] = media
        else:
            args["video_file"] = media
            args["media_type"] = "REELS"
        container = execute_action(_SLUG_CONTAINER, args)
        cdata = (container or {}).get("data", {}) if isinstance(container, dict) else {}
        creation_id = cdata.get("id") or cdata.get("creation_id")
        if not creation_id:
            raise RuntimeError(f"Instagram container create returned no id: {json.dumps(container)[:300]}")

        # Step 2 — publish it (auto-waits for the container to finish processing).
        published = execute_action(_SLUG_PUBLISH, {
            "ig_user_id": ig_user_id,
            "creation_id": creation_id,
        })
        pdata = (published or {}).get("data", {}) if isinstance(published, dict) else {}
        external_id = pdata.get("id")
        if not external_id:
            raise RuntimeError(f"Instagram publish returned no post id: {json.dumps(published)[:300]}")
        return str(external_id)

    def fetch_metrics(self, post: dict) -> dict:
        if not post.get("external_id"):
            return {}
        result = execute_action(_SLUG_INSIGHTS, {
            "ig_media_id": post["external_id"],
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
