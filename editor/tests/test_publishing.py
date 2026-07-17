"""Social publishing — core (spec: specs/social-core.md).

Walks the whole dry-run lifecycle end-to-end: draft → scheduled → claimed →
publishing → published, plus cancel, idempotency, the scheduler claim, the arm gate,
and boot reconciliation. Real posting is never exercised (SOCIAL_DRY_RUN default on).
"""
import pytest

from social import scheduler
from social.base import dry_run_enabled


@pytest.fixture()
def campaign(conn):
    cur = conn.execute("INSERT INTO campaigns (name) VALUES ('Test campaign')")
    conn.commit()
    return cur.lastrowid


def _status(conn, post_id):
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def test_dry_run_is_the_default(client):
    assert dry_run_enabled() is True


def test_create_draft_and_list(client, campaign):
    r = client.post(f"/api/campaigns/{campaign}/posts",
                    json={"platform": "instagram", "caption": "hi"})
    assert r.status_code == 201
    post = r.get_json()
    assert post["status"] == "draft" and post["platform"] == "instagram"

    lst = client.get(f"/api/campaigns/{campaign}/posts").get_json()
    assert [p["id"] for p in lst] == [post["id"]]


def test_unknown_platform_rejected(client, campaign):
    r = client.post(f"/api/campaigns/{campaign}/posts", json={"platform": "myspace"})
    assert r.status_code == 400


def test_full_publish_lifecycle_dry_run(client, campaign, conn):
    # create a scheduled post in the past so the scheduler will claim it
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "tiktok", "caption": "c", "scheduled_at": "2000-01-01T00:00:00+00:00",
    }).get_json()["id"]
    assert _status(conn, pid)["status"] == "scheduled"

    # scheduler claims due posts atomically
    claimed = scheduler.claim_due_posts(conn)
    assert pid in claimed
    assert _status(conn, pid)["status"] == "claimed"

    # publish (synchronous form of what the loop runs in a thread)
    scheduler.publish_post(pid)
    row = _status(conn, pid)
    assert row["status"] == "published"
    assert row["external_id"] == f"dryrun-tiktok-{pid}"
    assert row["published_at"] and row["idempotency_key"] == f"post:{pid}:2000-01-01T00:00:00+00:00"


def test_publish_is_idempotent(client, campaign, conn):
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    scheduler.claim_due_posts(conn)
    scheduler.publish_post(pid)
    first = _status(conn, pid)["external_id"]
    # a second call must be a no-op (already has external_id) — never double-post
    scheduler.publish_post(pid)
    assert _status(conn, pid)["external_id"] == first
    assert _status(conn, pid)["status"] == "published"


def test_not_yet_due_is_not_claimed(client, campaign, conn):
    client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2999-01-01T00:00:00+00:00"})
    assert scheduler.claim_due_posts(conn) == []


def test_post_now_endpoint(client, campaign, conn, monkeypatch):
    # make the "publish now" path synchronous for a deterministic assert
    import blueprints.publishing as pub
    monkeypatch.setattr(pub, "enqueue_publish", scheduler.publish_post)
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "caption": "now", "publish_now": True}).get_json()["id"]
    assert _status(conn, pid)["status"] == "published"


def test_cancel_scheduled(client, campaign, conn):
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2999-01-01T00:00:00+00:00"}).get_json()["id"]
    assert client.post(f"/api/posts/{pid}/cancel").status_code == 200
    assert _status(conn, pid)["status"] == "cancelled"
    # a cancelled post can't be published
    assert client.post(f"/api/posts/{pid}/publish-now").status_code == 409


def test_reconcile_marks_interrupted_for_review(client, campaign, conn):
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    conn.execute("UPDATE posts SET status = 'publishing' WHERE id = ?", (pid,))
    conn.commit()
    scheduler.reconcile_orphaned_posts()
    row = _status(conn, pid)
    assert row["status"] == "needs_review"      # NOT auto-retried
    assert "interrupted" in (row["error"] or "")


def test_arm_gate_blocks_live_publish(client, campaign, conn, monkeypatch):
    # turn dry-run OFF; unarmed campaign must fail loudly, not post
    monkeypatch.setenv("SOCIAL_DRY_RUN", "0")
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    scheduler.claim_due_posts(conn)
    scheduler.publish_post(pid)
    row = _status(conn, pid)
    assert row["status"] == "failed"
    assert "armed" in (row["error"] or "").lower()

    # arm it → now the block is "no real adapter" (still no live post fires)
    conn.execute("UPDATE campaigns SET publishing_armed = 1 WHERE id = ?", (campaign,))
    conn.commit()
    conn.execute("UPDATE posts SET status = 'claimed', external_id = NULL, error = NULL WHERE id = ?", (pid,))
    conn.commit()
    scheduler.publish_post(pid)
    row = _status(conn, pid)
    assert row["status"] == "failed"
    assert "adapter" in (row["error"] or "").lower()


def test_arm_endpoint(client, campaign):
    r = client.post(f"/api/campaigns/{campaign}/arm", json={"armed": True})
    assert r.status_code == 200 and r.get_json()["publishing_armed"] is True
