"""Adapter Protocol + registry + the dry-run adapter.

The whole point of shipping core first: `SOCIAL_DRY_RUN=1` (the default) routes every
publish through `DryRunAdapter`, which logs the exact payload instead of calling any
real platform. The state machine, scheduler, and UI get exercised for weeks before a
single real post can fire. Real adapters register into `_REGISTRY` from a later spec.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Protocol, runtime_checkable

log = logging.getLogger("editor.social")

# Platforms the UI offers. A platform being listed here does NOT mean a real adapter
# exists — without one (and with dry-run off) publishing raises loudly.
PLATFORMS = ["instagram", "tiktok", "youtube", "facebook"]


def dry_run_enabled() -> bool:
    """Default-ON. Only an explicit SOCIAL_DRY_RUN=0 turns real posting on."""
    return os.environ.get("SOCIAL_DRY_RUN", "1").strip() != "0"


@runtime_checkable
class Adapter(Protocol):
    """One platform's publish path. `publish` takes a post dict (a `posts` row as a
    plain dict) and returns the platform's external post id, or raises on failure.
    Implementations MUST treat a repeat of the same `idempotency_key` as a no-op."""
    platform: str

    def publish(self, post: dict) -> str: ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    _REGISTRY[adapter.platform] = adapter


def real_adapter(platform: str) -> Adapter | None:
    return _REGISTRY.get(platform)


class DryRunAdapter:
    """Logs the payload and returns a deterministic fake id. Never touches the network."""

    def __init__(self, platform: str):
        self.platform = platform

    def publish(self, post: dict) -> str:
        payload = {
            "platform": self.platform,
            "account_ref": post.get("account_ref"),
            "caption": post.get("caption"),
            "hashtags": post.get("hashtags"),
            "media_path": post.get("media_path"),
            "idempotency_key": post.get("idempotency_key"),
        }
        log.info("SOCIAL DRY-RUN publish → %s", json.dumps(payload, ensure_ascii=False))
        return f"dryrun-{self.platform}-{post.get('id')}"


def get_adapter(platform: str) -> Adapter:
    """The single seam that decides real vs dry-run. Dry-run (default) always wins;
    real posting requires SOCIAL_DRY_RUN=0 AND a registered adapter."""
    if dry_run_enabled():
        return DryRunAdapter(platform)
    adapter = real_adapter(platform)
    if adapter is None:
        raise RuntimeError(
            f"No real adapter registered for '{platform}' and SOCIAL_DRY_RUN is off. "
            "Install a platform adapter (social-adapters) before live posting."
        )
    return adapter
