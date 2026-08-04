"""Framing v2 Stage 5: the export frame-check is opt-in and flags misframed segments.

The check costs ~1 vision call per segment, so the critical guarantee is that it does
NOT run unless the setting is on. Both tests stub the vision call, so no API is hit.
"""
import export


def _plan_item(clip_id):
    return {"clip_id": clip_id, "in_point": 0.0, "out_point": 1.0}


def test_frame_check_off_by_default_makes_no_calls(client, conn, make_clip, monkeypatch):
    cid = make_clip("FC_OFF", present=True)
    conn.execute("INSERT INTO clip_regions (clip_id, label, x, y, w, h, is_primary) "
                 "VALUES (?, 'bowl', 0.1, 0.1, 0.2, 0.2, 1)", (cid,))
    conn.commit()

    calls = []
    monkeypatch.setattr("claude_client.check_frame_subject",
                        lambda *a, **k: calls.append(a) or _Ok())

    # Default: setting unset → check must be skipped entirely.
    assert export._frame_check_export(__file__, [_plan_item(cid)]) == []
    assert calls == [], "frame check ran while disabled — that's a per-export API cost"


def test_frame_check_flags_misframed_segment(client, conn, make_clip, monkeypatch):
    """With the setting on, a segment whose subject isn't in frame is reported."""
    from settings import _set_setting
    cid = make_clip("FC_ON", present=True)
    conn.execute("INSERT INTO clip_regions (clip_id, label, x, y, w, h, is_primary) "
                 "VALUES (?, 'oil press', 0.8, 0.4, 0.1, 0.1, 1)", (cid,))
    conn.commit()
    _set_setting("export_frame_check", "1")

    # Stub the vision verdict (fail) and the frame extraction (no real file needed).
    monkeypatch.setattr("claude_client.check_frame_subject",
                        lambda subject, img, **k: _Bad())
    monkeypatch.setattr(export.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(export.Path, "read_bytes", lambda self: b"jpegbytes", raising=False)

    out = export._frame_check_export("/tmp/whatever.mp4", [_plan_item(cid)])
    assert len(out) == 1, out
    assert out[0]["subject"] == "oil press"
    assert out[0]["segment"] == 0
    assert "cut off" in out[0]["note"]


class _Ok:
    in_frame = True
    note = ""


class _Bad:
    in_frame = False
    note = "cut off on the right"
