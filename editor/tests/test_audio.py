"""Audio design (spec: specs/audio-design.md).

Covers plan→edit storage (generate/chat/PUT), per-mode export filter-graph
construction, and the Phase-4 reference-track + beat-snap compose flow.
"""
import json

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


# ---- beat-snap cut timing (Phase 4) ----
from export import snap_cuts_to_beats
from audio_beats import detect_beats, estimate_bpm


class _Sel:
    def __init__(self, clip_id, in_point, out_point):
        self.clip_id, self.in_point, self.out_point = clip_id, in_point, out_point


def test_snap_rounds_cuts_to_beat_multiples():
    beats = [round(i * 0.5, 3) for i in range(20)]        # 120 BPM grid, period 0.5s
    sels = [_Sel(1, 0.0, 1.3), _Sel(2, 2.0, 2.4), _Sel(3, 0.0, 2.1)]
    out = snap_cuts_to_beats(sels, beats)
    lengths = [round(o - i, 3) for _, i, o in out]
    assert lengths == [1.5, 0.5, 2.0]                     # snapped to nearest whole beats
    # cumulative boundaries land on the beat grid
    t = 0.0
    for L in lengths:
        t += L
        assert any(abs(t - b) < 1e-6 for b in beats)


def test_snap_caps_at_clip_duration():
    beats = [round(i * 0.5, 3) for i in range(20)]
    sels = [_Sel(1, 0.0, 1.9)]                            # wants 2.0, but clip is only 1.2s
    out = snap_cuts_to_beats(sels, beats, clip_durs={1: 1.2})
    _, i, o = out[0]
    assert o - i <= 1.2 and round(o - i, 3) == 1.0        # floored to whole beats within the clip


def test_snap_noop_without_grid():
    sels = [_Sel(1, 0.0, 1.3)]
    assert snap_cuts_to_beats(sels, []) == [(1, 0.0, 1.3)]


def test_detect_beats_click_track(tmp_path):
    import numpy as np, wave
    sr, dur, bpm = 22050, 6.0, 120
    y = np.zeros(int(sr * dur), dtype=np.float32)
    for t in np.arange(0, dur, 60.0 / bpm):
        i = int(t * sr); n = int(0.03 * sr)
        y[i:i+n] += (np.random.randn(n) * np.exp(-np.linspace(0, 6, n))).astype(np.float32) * 0.8
    p = tmp_path / "click.wav"
    with wave.open(str(p), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    beats = detect_beats(str(p))
    assert len(beats) >= 6
    assert abs((estimate_bpm(beats) or 0) - bpm) <= 8     # tolerant of octave-safe detection


def test_generate_snaps_cuts_to_reference_beats(client, make_clip, conn, monkeypatch):
    """generate-into-edit on an edit with a beat grid → item lengths land on beats."""
    a = make_clip("A", present=True, duration_s=10.0)
    b = make_clip("B", present=True, duration_s=10.0)
    eid = client.post("/api/edits", json={"name": "E"}).get_json()["id"]
    # stamp a 0.5s beat grid directly (skip async detection)
    conn.execute("UPDATE edits SET ref_audio_beats = ? WHERE id = ?",
                 (json.dumps([round(i * 0.5, 3) for i in range(40)]), eid))
    conn.commit()

    def fake_generate(prompt, clips):
        return RoughCutPlan(concept="x", selections=[
            ClipSelection(clip_id=a, in_point=0, out_point=1.3, reason="t"),
            ClipSelection(clip_id=b, in_point=0, out_point=2.2, reason="t")])
    import blueprints.edits as edits, json as _json
    monkeypatch.setattr(edits, "generate_rough_cut", fake_generate)

    client.post(f"/api/edits/{eid}/generate", json={"prompt": "cut to the beat"})
    items = client.get(f"/api/edits/{eid}").get_json()["items"]
    lengths = [round(it["out_point"] - it["in_point"], 3) for it in items]
    assert lengths == [1.5, 2.0]     # 1.3→1.5, 2.2→2.0 (nearest whole 0.5s beats)


# ---- music bed (Phase 2) ----
import config as _config
from export import _music_bed_filter


def _make_music_dir(tmp_path, monkeypatch):
    d = tmp_path / "music"; d.mkdir()
    (d / "Upbeat Acoustic.mp3").write_bytes(b"ID3x")
    (d / "Upbeat Acoustic.json").write_text(json.dumps({"mood": "upbeat acoustic", "tags": ["happy"]}))
    (d / "Sad Piano.mp3").write_bytes(b"ID3x")
    (d / "Sad Piano.json").write_text(json.dumps({"mood": "sad slow piano", "tags": ["mellow"]}))
    monkeypatch.setattr(_config, "MUSIC_DIR", d)
    return d


def test_match_track_by_mood(tmp_path, monkeypatch):
    from music_lib import list_tracks, match_track
    _make_music_dir(tmp_path, monkeypatch)
    assert len(list_tracks()) == 2
    assert "Upbeat Acoustic" in match_track("energetic upbeat")
    assert "Sad Piano" in match_track("slow mellow piano")
    assert match_track("anything else")  # non-empty lib always returns a fallback


def test_music_api_lists_tracks(client, tmp_path, monkeypatch):
    _make_music_dir(tmp_path, monkeypatch)
    names = {t["name"] for t in client.get("/api/music").get_json()}
    assert names == {"Upbeat Acoustic", "Sad Piano"}


def test_generate_music_mode_sets_track(client, make_clip, tmp_path, monkeypatch):
    _make_music_dir(tmp_path, monkeypatch)
    make_clip("A", present=True)

    def fake_generate(prompt, clips):
        return RoughCutPlan(concept="x",
            selections=[ClipSelection(clip_id=clips[0]["id"], in_point=0, out_point=1, reason="t")],
            audio_plan=AudioPlan(mode="music", rationale="montage", music_mood="upbeat acoustic"))
    import blueprints.edits as edits
    monkeypatch.setattr(edits, "generate_rough_cut", fake_generate)

    body = client.post("/api/generate-edit", json={"prompt": "upbeat montage"}).get_json()
    assert body["audio_mode"] == "music"
    assert "Upbeat Acoustic" in (_edit(client, body["id"])["music_path"] or "")


def test_music_bed_filter_fades():
    f = _music_bed_filter(15.0)
    assert "afade=t=in" in f and "afade=t=out:st=13.000" in f  # fade out 2s before end
