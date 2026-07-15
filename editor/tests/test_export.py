"""Case 6: export — ghost pre-flight 409; success renders a 9:16 file, job finishes."""
import time
from pathlib import Path


def _poll_job(client, job_id, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/import-jobs/{job_id}").get_json()
        if job.get("finished"):
            return job
        time.sleep(0.3)
    raise AssertionError("export job did not finish in time")


def test_export_preflight_fails_on_ghost(client, make_clip):
    eid = client.post("/api/edits", json={"name": "e"}).get_json()["id"]
    ghost = make_clip("GHOST", present=False)
    client.post(f"/api/edits/{eid}/items", json={"clip_id": ghost, "in_point": 0, "out_point": 1})

    r = client.post(f"/api/edits/{eid}/export")
    assert r.status_code == 409
    assert "GHOST" in r.get_json()["error"]


def test_export_empty_timeline_is_400(client):
    eid = client.post("/api/edits", json={"name": "e"}).get_json()["id"]
    assert client.post(f"/api/edits/{eid}/export").status_code == 400


def test_export_success_produces_9_16_file(client, make_clip):
    eid = client.post("/api/edits", json={"name": "reel"}).get_json()["id"]
    for stem in ("A", "B"):
        cid = make_clip(stem, present=True)
        client.post(f"/api/edits/{eid}/items", json={"clip_id": cid, "in_point": 0, "out_point": 1})
    client.put(f"/api/edits/{eid}", json={"aspect": "9:16"})

    r = client.post(f"/api/edits/{eid}/export")
    assert r.status_code == 200
    job = _poll_job(client, r.get_json()["job_id"])

    assert job["finished"] is True
    assert not job.get("error"), job.get("error")
    res = job["results"][0]
    assert (res["width"], res["height"]) == (1080, 1920)
    assert Path(res["output"]).exists()


def test_auto_crop_centers_on_primary_region_not_union(client, make_clip):
    """Framing v2 quick win: with a small watched-thing box on the right and a large
    untied box on the left, the crop centers on the primary (thing-tied) subject —
    not the union midpoint, which would land between them on background."""
    import export
    from db import get_conn

    cid = make_clip("SUBJ", present=True)
    conn = get_conn()
    tid = conn.execute("INSERT INTO things (name, kind, active) VALUES ('bowl','object',1)").lastrowid
    # Untied region fills the left half; the watched thing is a small box on the right.
    conn.execute("INSERT INTO clip_regions (clip_id, thing_id, x, y, w, h) VALUES (?, NULL, 0.0, 0.2, 0.4, 0.6)", (cid,))
    conn.execute("INSERT INTO clip_regions (clip_id, thing_id, x, y, w, h) VALUES (?, ?, 0.80, 0.45, 0.12, 0.12)", (cid, tid))
    conn.commit()

    # 9:16 target from a 16:9 source → a narrow vertical window (cw≈0.316, full height).
    rect = export._auto_crop_from_regions(conn, cid, target_ar=9 / 16, source_dims=(1920, 1080))
    conn.close()
    assert rect is not None
    x, y, w, h = rect
    # The window must contain the thing's center (0.86), i.e. it tracked the subject
    # to the right — not the union center (~0.46) which sits on the left-hand box.
    assert x <= 0.86 <= x + w, rect
    assert x > 0.30, f"window still centered near the union midpoint: {rect}"
