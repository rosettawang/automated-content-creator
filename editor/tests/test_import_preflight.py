"""Import-unified: link-import jobs fail fast on a dead network.

When the server can't reach the internet (e.g. relaunched inside a Claude sandbox
that blocks sockets), a Drive/Photos job must fail the whole run once with one
actionable message -- never hammer the dead network item by item.
"""
import ingest
import jobs_runtime


def _snap(job_id):
    return jobs_runtime._job_snapshot(job_id)


def test_drive_job_aborts_when_network_is_down(monkeypatch):
    # Simulate no network: preflight reports the sandbox message.
    monkeypatch.setattr(ingest, "_network_preflight", lambda: ingest._NO_NETWORK_MSG)
    # If the job wrongly proceeded, this would blow up loudly instead of failing clean.
    monkeypatch.setattr(ingest, "download_drive",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")))

    job_id = jobs_runtime._new_job("Drive", unit="link")
    ingest._run_drive_job(job_id, ["https://drive.google.com/file/d/abc/view"])

    snap = _snap(job_id)
    assert snap["finished"] is True
    assert snap["error"] == ingest._NO_NETWORK_MSG
    assert snap["results"] == []


def test_photos_job_aborts_when_network_is_down(monkeypatch):
    monkeypatch.setattr(ingest, "_network_preflight", lambda: ingest._NO_NETWORK_MSG)
    monkeypatch.setattr(ingest, "fetch_album_bases",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))

    job_id = jobs_runtime._new_job("Photos", unit="file")
    ingest._run_photos_job(job_id, ["https://photos.app.goo.gl/abc"])

    snap = _snap(job_id)
    assert snap["finished"] is True
    assert snap["error"] == ingest._NO_NETWORK_MSG
    assert snap["results"] == []
