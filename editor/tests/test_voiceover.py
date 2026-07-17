"""Audio design — Phase 3 voiceover (spec: specs/audio-design.md).

The full VO export path (real TTS + ffmpeg ducked mix) was verified offline; here we
cover the pieces that matter without network/render: engine detection, the editable
script storage, and the duck/mix filter construction.
"""
import export
import tts


# ---- engine detection (no network) ----
def test_engine_none_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert tts.available_engine() is None


def test_engine_openai_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert tts.available_engine() == "openai"


def test_synthesize_raises_without_engine(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        tts.synthesize("hello", "/tmp/none.mp3")
        assert False, "expected TTSUnavailable"
    except tts.TTSUnavailable:
        pass


# ---- editable script storage (never straight-to-audio) ----
def test_vo_script_persists(client):
    eid = client.post("/api/edits", json={"name": "E"}).get_json()["id"]
    client.put(f"/api/edits/{eid}", json={"audio_mode": "voiceover",
                                          "vo_script": "Egg, caterpillar, butterfly."})
    edit = client.get(f"/api/edits/{eid}").get_json()
    assert edit["audio_mode"] == "voiceover"
    assert edit["vo_script"] == "Egg, caterpillar, butterfly."


# ---- duck/mix filter graph ----
def test_voiceover_mix_filter_ducks_and_caps_to_video():
    f = export._voiceover_mix_filter()
    assert "volume=" in f                    # ambient is ducked
    assert "amix=inputs=2" in f              # VO mixed with ambient
    assert "duration=first" in f            # capped to the video, not stretched
