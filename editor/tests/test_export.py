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
