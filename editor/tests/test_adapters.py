"""Platform adapters (spec: specs/social-adapters.md).

Only the pure, offline-safe parts are exercised: pre-publish validation and registry
wiring. Live publish/metrics need real Composio auth + account and are never run here
(and stay gated behind SOCIAL_DRY_RUN + arm at runtime)."""
import pytest

from social.base import load_adapters, real_adapter, get_adapter, DryRunAdapter
from social.instagram import validate, InstagramAdapter


def test_validate_rejects_long_caption():
    with pytest.raises(ValueError, match="Caption"):
        validate({"caption": "x" * 3000, "media_path": "/tmp/a.mp4"})


def test_validate_requires_media():
    with pytest.raises(ValueError, match="need media"):
        validate({"caption": "ok", "media_path": None})


def test_validate_rejects_overlong_reel():
    with pytest.raises(ValueError, match="Reels cap"):
        validate({"caption": "ok", "media_path": "/tmp/a.mp4", "media_duration_s": 120})


def test_validate_accepts_good_post():
    validate({"caption": "ok", "media_path": "/tmp/a.mp4", "media_duration_s": 30})  # no raise


def test_registration_and_dry_run_gate(monkeypatch):
    load_adapters()
    assert isinstance(real_adapter("instagram"), InstagramAdapter)
    # Dry-run default still routes everything to the DryRunAdapter, never the real one.
    monkeypatch.setenv("SOCIAL_DRY_RUN", "1")
    assert isinstance(get_adapter("instagram"), DryRunAdapter)
    # With dry-run off, the registered real adapter is returned.
    monkeypatch.setenv("SOCIAL_DRY_RUN", "0")
    assert isinstance(get_adapter("instagram"), InstagramAdapter)
