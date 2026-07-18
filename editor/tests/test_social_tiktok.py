"""TikTok adapter — pre-publish validation, registration, and dry-run gating.

Never performs a real post: the default env (SOCIAL_DRY_RUN unset => on) routes every
publish through DryRunAdapter, and these tests assert exactly that gate holds.
"""
import pytest

import social.base as base
from social.tiktok import validate, TikTokAdapter, CAPTION_MAX, VIDEO_MAX_SECONDS


def test_adapter_registers_and_reports_platform():
    base.load_adapters()
    assert "tiktok" in base._REGISTRY
    assert base._REGISTRY["tiktok"].platform == "tiktok"


def test_dry_run_is_the_default_so_no_real_post(monkeypatch):
    """With SOCIAL_DRY_RUN unset (or anything but '0'), get_adapter must return the
    dry-run adapter even though a real TikTok adapter is registered."""
    monkeypatch.delenv("SOCIAL_DRY_RUN", raising=False)
    base.load_adapters()
    assert type(base.get_adapter("tiktok")).__name__ == "DryRunAdapter"


def test_validate_passes_on_a_normal_reel():
    validate({"media_path": "cut.mp4", "caption": "hi", "hashtags": "#garden",
              "media_duration_s": 30})


@pytest.mark.parametrize("post, needle", [
    ({}, "need media"),
    ({"media_path": "cut.mp4", "caption": "a" * (CAPTION_MAX + 1)}, "allows"),
    ({"media_path": "cut.txt"}, "Unsupported"),
    ({"media_path": "cut.mp4", "media_duration_s": VIDEO_MAX_SECONDS + 1}, "caps at"),
])
def test_validate_fails_loud(post, needle):
    with pytest.raises(ValueError, match=needle):
        validate(post)
