"""Instagram adapter — IG user-id resolution via the IG_USER_ID override.

For the current Composio version the numeric IG Business id isn't in the connection
metadata, so IG_USER_ID (env) is the intended way to supply it. These tests exercise
that override with no network — they never touch Composio or post anything.
"""
import pytest

from social.instagram import _resolve_ig_user_id, InstagramAdapter


def test_env_override_returns_numeric_id(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "17841400000000000")
    # Even with no account_ref (so no metadata lookup) the env override wins.
    assert _resolve_ig_user_id("") == "17841400000000000"


def test_non_numeric_env_is_ignored(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "not-a-number")
    assert _resolve_ig_user_id("") is None


def test_no_env_and_no_ref_is_none(monkeypatch):
    monkeypatch.delenv("IG_USER_ID", raising=False)
    assert _resolve_ig_user_id("") is None


def test_publish_errors_clearly_without_an_id(monkeypatch):
    """Fail loud (not silent) when the id can't be resolved, pointing at the fix."""
    monkeypatch.delenv("IG_USER_ID", raising=False)
    post = {"media_path": "hero.jpg", "caption": "hi", "account_ref": ""}
    with pytest.raises(RuntimeError, match="IG_USER_ID"):
        InstagramAdapter().publish(post)
