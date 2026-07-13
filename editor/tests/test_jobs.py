"""Case 7: orphaned running jobs are reconciled at boot."""
import core
import db


def test_orphaned_job_reconciled(client):
    # A job left "running" (finished=0) — as if the process died mid-job.
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO jobs (id, label, unit, phase, finished) VALUES (?, ?, ?, ?, 0)",
        ("orphan1", "Stuck job", "clip", "encoding"),
    )
    conn.commit()
    conn.close()

    core.reconcile_orphaned_jobs()

    conn = db.get_conn()
    row = conn.execute("SELECT finished, error FROM jobs WHERE id = 'orphan1'").fetchone()
    conn.close()
    assert row["finished"] == 1
    assert row["error"] and "restart" in row["error"].lower()

    # The status endpoint now reports it finished (with the interrupted error), so the
    # UI stops waiting on it.
    snap = client.get("/api/import-jobs/orphan1").get_json()
    assert snap["finished"] is True
    assert snap["error"]
