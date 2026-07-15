"""Framing v2: time-aware regions (Stage 2) + assemble-time subject-tracking
reframe (Stage 3)."""
import types

import export
import indexing


def _seg(t_start, t_end, regions):
    return types.SimpleNamespace(t_start=t_start, t_end=t_end, regions=regions)


def test_store_segment_regions_stamps_time_and_one_primary(conn, make_clip):
    cid = make_clip("SEG", present=True)
    # Two segments; each has a big "subject" box and a tiny one → the big one is primary.
    segs = [
        _seg(0.0, 2.0, [{"label": "a", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
                        {"label": "b", "x": 0.8, "y": 0.8, "w": 0.05, "h": 0.05}]),
        _seg(2.0, 4.0, [{"label": "c", "x": 0.4, "y": 0.4, "w": 0.4, "h": 0.4}]),
    ]
    indexing._store_segment_regions(conn, cid, segs)
    conn.commit()
    rows = conn.execute(
        "SELECT t_frame, is_primary, label FROM clip_regions WHERE clip_id=? ORDER BY t_frame, id",
        (cid,),
    ).fetchall()

    assert len(rows) == 3
    assert {round(r["t_frame"], 1) for r in rows} == {1.0, 3.0}   # segment midpoints, not NULL
    assert [r["label"] for r in rows if r["is_primary"]] == ["a", "c"]  # one primary/segment, the larger box


def test_frame_item_tracks_moving_subject(conn, make_clip):
    cid = make_clip("MOVE", present=True)
    # Subject on the left early, right late (primary boxes at t=0.5 and t=3.5).
    conn.execute("INSERT INTO clip_regions (clip_id, x, y, w, h, t_frame, is_primary) "
                 "VALUES (?, 0.05, 0.4, 0.2, 0.2, 0.5, 1)", (cid,))
    conn.execute("INSERT INTO clip_regions (clip_id, x, y, w, h, t_frame, is_primary) "
                 "VALUES (?, 0.75, 0.4, 0.2, 0.2, 3.5, 1)", (cid,))
    conn.commit()

    fr = export._frame_item_from_regions(conn, cid, 0.0, 4.0, target_ar=9 / 16, source_dims=(1920, 1080))
    assert fr is not None and fr["kb"] is not None, "moving subject should yield a start→end pan"
    cx_start = fr["crop"][0] + fr["crop"][2] / 2
    cx_end = fr["kb"][0] + fr["kb"][2] / 2
    assert cx_start < cx_end, (fr, "window should pan left→right with the subject")


def test_frame_item_static_when_subject_still(conn, make_clip):
    cid = make_clip("STILL", present=True)
    for t in (0.5, 3.5):
        conn.execute("INSERT INTO clip_regions (clip_id, x, y, w, h, t_frame, is_primary) "
                     "VALUES (?, 0.45, 0.45, 0.1, 0.1, ?, 1)", (cid, t))
    conn.commit()
    fr = export._frame_item_from_regions(conn, cid, 0.0, 4.0, target_ar=9 / 16, source_dims=(1920, 1080))
    assert fr is not None and fr["kb"] is None, "still subject should be a static crop, no Ken Burns"


def test_auto_framing_fills_null_only_leaving_existing_crops(conn, make_clip):
    """reset=False (generate/append path): fills items with no crop yet, never touches
    an item that already has one — protects prior/appended items from being reframed."""
    tracked = make_clip("TRK", present=True)
    kept = make_clip("KEEP", present=True)
    eid = conn.execute("INSERT INTO edits (name, aspect) VALUES ('e','9:16')").lastrowid
    conn.execute("INSERT INTO clip_regions (clip_id, x, y, w, h, t_frame, is_primary) "
                 "VALUES (?, 0.7, 0.4, 0.2, 0.2, 0.5, 1)", (tracked,))
    conn.execute("INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
                 "VALUES (?, ?, 0, 0, 1)", (eid, tracked))
    # kept already has a crop → must be left verbatim.
    conn.execute("INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point, "
                 "crop_x, crop_y, crop_w, crop_h) VALUES (?, ?, 1, 0, 1, 0.11, 0.12, 0.3, 0.9)",
                 (eid, kept))
    conn.commit()

    export._apply_auto_framing(conn, eid)

    got = {r["clip_id"]: r for r in conn.execute(
        "SELECT clip_id, crop_x, crop_w FROM timeline_items WHERE edit_id=?", (eid,))}
    assert (round(got[kept]["crop_x"], 2), round(got[kept]["crop_w"], 2)) == (0.11, 0.3)  # untouched
    t = got[tracked]
    assert t["crop_x"] is not None and t["crop_x"] <= 0.8 <= t["crop_x"] + t["crop_w"]  # centered on region


def test_put_aspect_change_reframes_from_regions(client, conn, make_clip):
    """The aspect-change PUT resets and reframes from regions (stored crops are
    aspect-specific, so a new aspect re-derives them)."""
    eid = client.post("/api/edits", json={"name": "reel"}).get_json()["id"]
    tracked = make_clip("TRK2", present=True)
    client.post(f"/api/edits/{eid}/items", json={"clip_id": tracked, "in_point": 0, "out_point": 1})
    conn.execute("INSERT INTO clip_regions (clip_id, x, y, w, h, t_frame, is_primary) "
                 "VALUES (?, 0.7, 0.4, 0.2, 0.2, 0.5, 1)", (tracked,))
    conn.commit()

    client.put(f"/api/edits/{eid}", json={"aspect": "9:16"})

    it = client.get(f"/api/edits/{eid}").get_json()["items"][0]
    assert it["crop_x"] is not None
    assert it["crop_x"] <= 0.8 <= it["crop_x"] + it["crop_w"]
