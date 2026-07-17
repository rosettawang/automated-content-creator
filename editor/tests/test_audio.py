"""Audio design — Phase 1 (spec: specs/audio-design.md).

Covers plan→edit storage (generate/chat/PUT) and the per-mode export filter-graph
construction. No audio analysis — the encode args are asserted as strings.
"""
import pytest

from export import _segment_audio_args
from claude_client import RoughCutPlan, EditChatResult, ClipSelection, AudioPlan


def _edit(client, eid):
    return client.get(f"/api/edits/{eid}").get_json()


# ---- plan → edit storage ----
def test_default_audio_is_ambient(client, make_clip, mock_ai):
    make_clip("A", present=True)
    eid = client.post("/api/generate-edit", json={"prompt": "a montage"}).get_json()["id"]
    assert _edit(client, eid)["audio_mode"] == "ambient"


def test_generate_infers_audio_mode(client, make_clip, monkeypatch):
    make_clip("A", present=True)

    def fake_generate(prompt, clips):
        return RoughCutPlan(
            concept="Silent montage",
            selections=[ClipSelection(clip_id=clips[0]["id"], in_point=0, out_point=1, reason="t")],
            audio_plan=AudioPlan(mode="clean", rationale="montage, add music in-app"),
        )
    import blueprints.edits as edits
    monkeypatch.setattr(edits, "generate_rough_cut", fake_generate)

    body = client.post("/api/generate-edit", json={"prompt": "a silent montage"}).get_json()
    assert body["audio_mode"] == "clean"
    row = _edit(client, body["id"])
    assert row["audio_mode"] == "clean"
    assert "montage" in (row["audio_rationale"] or "")


def test_explicit_audio_mode_wins_over_plan(client, make_clip, monkeypatch):
    make_clip("A", present=True)

    def fake_generate(prompt, clips):
        return RoughCutPlan(concept="x",
            selections=[ClipSelection(clip_id=clips[0]["id"], in_point=0, out_point=1, reason="t")],
            audio_plan=AudioPlan(mode="music", rationale="upbeat"))
    import blueprints.edits as edits
    monkeypatch.setattr(edits, "generate_rough_cut", fake_generate)

    body = client.post("/api/generate-edit",
                       json={"prompt": "montage", "audio_mode": "clean"}).get_json()
    assert body["audio_mode"] == "clean"   # gear choice beats the model


def test_put_audio_mode_persists_and_validates(client, conn):
    eid = client.post("/api/edits", json={"name": "E"}).get_json()["id"]
    assert client.put(f"/api/edits/{eid}", json={"audio_mode": "voiceover"}).status_code == 200
    assert _edit(client, eid)["audio_mode"] == "voiceover"
    bad = client.put(f"/api/edits/{eid}", json={"audio_mode": "surround"})
    assert bad.status_code == 400


def test_chat_changes_audio_mode(client, make_clip, monkeypatch):
    a = make_clip("A", present=True)
    eid = client.post("/api/edits", json={"name": "E"}).get_json()["id"]
    client.post(f"/api/edits/{eid}/items", json={"clip_id": a, "in_point": 0, "out_point": 1})

    def fake_revise(instruction, current_timeline, clips, aspect=None):
        return EditChatResult(reply="Stripped the audio.",
            selections=[ClipSelection(clip_id=a, in_point=0, out_point=1, reason="t")],
            audio_plan=AudioPlan(mode="clean", rationale="platform music"))
    import blueprints.edits as edits
    monkeypatch.setattr(edits, "revise_edit", fake_revise)

    r = client.post(f"/api/edits/{eid}/chat", json={"prompt": "strip the audio"})
    assert r.status_code == 200
    assert _edit(client, eid)["audio_mode"] == "clean"


# ---- export filter-graph construction per mode (pure) ----
def test_clean_strips_audio():
    a = _segment_audio_args("clean", has_audio=True)
    assert a["maps"] == ["-an"] and a["codec"] == [] and not a["null_input"]


def test_ambient_loudnorms_real_audio():
    a = _segment_audio_args("ambient", has_audio=True)
    assert "loudnorm" in " ".join(a["filt"])
    assert a["maps"] == ["-map", "0:a:0"] and "aac" in a["codec"]


def test_still_gets_silent_track_no_loudnorm():
    a = _segment_audio_args("ambient", has_audio=False)
    assert a["null_input"] is True
    assert a["maps"] == ["-map", "1:a:0"] and a["filt"] == []


# ---- trending-audio compose: reference track (Phase 4) ----
import io


def _new_edit(client):
    return client.post("/api/edits", json={"name": "E"}).get_json()["id"]


def test_reference_audio_upload_get_clear(client):
    eid = _new_edit(client)
    # upload a scratch track
    r = client.post(f"/api/edits/{eid}/reference-audio",
                    data={"file": (io.BytesIO(b"ID3fake-mp3-bytes"), "trend.mp3"),
                          "name": "Trending Sound", "start": "3.5"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_reference"] and body["ref_audio_name"] == "Trending Sound"
    assert body["ref_audio_start"] == 3.5

    edit = client.get(f"/api/edits/{eid}").get_json()
    assert edit["ref_audio_name"] == "Trending Sound" and edit["ref_audio_start"] == 3.5
    # the scratch file serves for preview
    assert client.get(f"/api/edits/{eid}/reference-audio/media").status_code == 200

    # adjusting the offset (no re-upload)
    client.put(f"/api/edits/{eid}", json={"ref_audio_start": 5})
    assert client.get(f"/api/edits/{eid}").get_json()["ref_audio_start"] == 5.0

    # clear
    assert client.delete(f"/api/edits/{eid}/reference-audio").status_code == 200
    assert client.get(f"/api/edits/{eid}").get_json()["ref_audio_path"] is None
    assert client.get(f"/api/edits/{eid}/reference-audio/media").status_code == 404


def test_reference_audio_rejects_bad_type(client):
    eid = _new_edit(client)
    r = client.post(f"/api/edits/{eid}/reference-audio",
                    data={"file": (io.BytesIO(b"x"), "notes.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_reference_does_not_change_export_audio_mode(client):
    # a reference track is compose-only; it must not flip the exported audio treatment
    eid = _new_edit(client)
    client.put(f"/api/edits/{eid}", json={"audio_mode": "clean"})
    client.post(f"/api/edits/{eid}/reference-audio",
                data={"file": (io.BytesIO(b"ID3x"), "t.mp3")},
                content_type="multipart/form-data")
    assert client.get(f"/api/edits/{eid}").get_json()["audio_mode"] == "clean"
