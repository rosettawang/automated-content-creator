"""AI provider switch (spec: ai-provider-switch) — everything verifiable without a key.

The Kimi path can't be exercised against the real API until the owner issues a
MOONSHOT_API_KEY, so these tests pin the parts that don't need one: the setting, the
dispatch decision inside _parse, the Anthropic→OpenAI message translation, the missing-key
error, and the batch-job fallback. No test here reaches any network.
"""
import claude_client as cc
import pytest
from pydantic import BaseModel

from settings import _ai_provider, _set_setting


class Tiny(BaseModel):
    ok: bool
    note: str = ""


# ---- the setting ----

def test_provider_defaults_to_claude(client):
    assert _ai_provider() == "claude"


def test_provider_setting_roundtrips_via_api(client):
    r = client.post("/api/settings", json={"ai_provider": "kimi"})
    assert r.status_code == 200
    assert r.get_json()["ai_provider"] == "kimi"
    assert _ai_provider() == "kimi"
    # and back
    assert client.post("/api/settings", json={"ai_provider": "claude"}).get_json()["ai_provider"] == "claude"


def test_bogus_provider_is_rejected(client):
    r = client.post("/api/settings", json={"ai_provider": "gpt-9"})
    assert r.status_code == 400
    assert _ai_provider() == "claude"      # unchanged


def test_settings_payload_exposes_key_presence(client):
    body = client.get("/api/settings").get_json()
    assert "ai_provider" in body and "kimi_key_present" in body and "kimi_model" in body


# ---- dispatch: _parse must route by the setting, not by call site ----

def test_parse_routes_to_kimi_when_selected(client, monkeypatch):
    _set_setting("ai_provider", "kimi")
    seen = {}

    def fake_kimi(messages, schema, max_tokens, *, system=None):
        seen["called"] = True
        return schema(ok=True, note="from kimi")

    monkeypatch.setattr(cc, "_parse_kimi", fake_kimi)
    # If it wrongly used Anthropic, this would try to build a real client and fail.
    out = cc._parse([{"role": "user", "content": "hi"}], Tiny, 100)
    assert seen.get("called") and out.note == "from kimi"


def test_parse_uses_anthropic_by_default(client, monkeypatch):
    calls = {}

    class _Msgs:
        def parse(self, **kw):
            calls.update(kw)
            class R:  # mimic the SDK's wrapper
                parsed_output = Tiny(ok=True, note="from claude")
            return R()

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(cc, "get_client", lambda: _Client())
    out = cc._parse([{"role": "user", "content": "hi"}], Tiny, 100)
    assert out.note == "from claude"
    assert calls["output_format"] is Tiny      # structured-output contract preserved


# ---- wire translation (the risky pure function) ----

def test_message_translation_text_and_image():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
    ]}]
    out = cc._anthropic_to_openai_messages(msgs, system="be terse")
    assert out[0] == {"role": "system", "content": "be terse"}
    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_message_translation_plain_string_passthrough():
    out = cc._anthropic_to_openai_messages([{"role": "user", "content": "plain"}])
    assert out == [{"role": "user", "content": "plain"}]


# ---- the account-gated edge: no key must fail loudly and actionably ----

def test_missing_key_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(cc, "_kimi_client", None)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as e:
        cc._get_kimi_client()
    msg = str(e.value)
    assert "MOONSHOT_API_KEY" in msg and "platform.kimi.ai" in msg


# ---- decision D2: batch is Claude-only, so Kimi falls back to the sync loop ----

def test_batch_job_falls_back_to_sync_on_kimi(client, monkeypatch):
    import indexing
    _set_setting("ai_provider", "kimi")
    ran = {}
    monkeypatch.setattr(indexing, "_run_deep_index_job",
                        lambda job_id, clip_ids: ran.setdefault("sync", (job_id, clip_ids)))
    indexing._run_deep_index_batch_job("job-1", [1, 2])
    assert ran.get("sync") == ("job-1", [1, 2]), "Kimi must not hit the Anthropic batch API"
