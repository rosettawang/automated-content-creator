"""Platform status icons + open-on-platform links (spec: specs/platform-links.html).

Covers the backend contract the UI relies on:
  - a published post stores the permalink the adapter returns;
  - dry-run publishes never produce a permalink (a gray icon stays truthful);
  - the cuts list carries a per-cut `posts` summary + `last_export`;
  - that summary is built with a bounded number of queries (no N+1).
"""
import pytest

from social import scheduler
from social.base import PublishResult, split_publish_result


@pytest.fixture()
def campaign(conn):
    cur = conn.execute("INSERT INTO campaigns (name) VALUES ('Links campaign')")
    conn.commit()
    return cur.lastrowid


def _status(conn, post_id):
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


# ---- adapter result normalization ----

def test_split_publish_result_accepts_str_and_result():
    assert split_publish_result("abc") == ("abc", None)
    assert split_publish_result(PublishResult("abc", "https://x/p/1")) == ("abc", "https://x/p/1")


# ---- permalink storage on publish ----

def test_dry_run_publish_has_no_permalink(client, campaign, conn):
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    scheduler.claim_due_posts(conn)
    scheduler.publish_post(pid)
    row = _status(conn, pid)
    assert row["status"] == "published"
    # Dry-run (default) must leave permalink NULL — a colored icon means a REAL post.
    assert row["permalink"] is None


def test_published_post_stores_adapter_permalink(client, campaign, conn, monkeypatch):
    """When an adapter returns a PublishResult with a permalink, it is persisted."""
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    scheduler.claim_due_posts(conn)

    class _FakeAdapter:
        platform = "instagram"
        def publish(self, post):
            return PublishResult("ig-123", "https://www.instagram.com/p/ABC123/")
        def fetch_metrics(self, post): return {}
        def verify_connection(self): return True

    monkeypatch.setattr(scheduler, "get_adapter", lambda platform: _FakeAdapter())
    scheduler.publish_post(pid)
    row = _status(conn, pid)
    assert row["status"] == "published"
    assert row["external_id"] == "ig-123"
    assert row["permalink"] == "https://www.instagram.com/p/ABC123/"


# ---- per-cut summary in the cuts list ----

def test_cuts_list_carries_post_and_export_summary(client, campaign, conn, make_clip):
    clip = make_clip("plat_a")
    edit_id = client.post("/api/edits", json={"name": "Cut A", "campaign_id": campaign}).get_json()["id"]
    conn.execute("INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
                 "VALUES (?, ?, 0, 0, 1)", (edit_id, clip))
    # a published post with a permalink, and an export record
    conn.execute("INSERT INTO posts (campaign_id, edit_id, platform, status, permalink) "
                 "VALUES (?, ?, 'instagram', 'published', 'https://insta/p/1/')", (campaign, edit_id))
    conn.execute("INSERT INTO edit_exports (edit_id, path, width, height, fps) "
                 "VALUES (?, '/out/cutA.mp4', 1080, 1920, 30)", (edit_id,))
    conn.commit()

    edits = client.get("/api/edits").get_json()
    cut = next(e for e in edits if e["id"] == edit_id)
    assert cut["posts"] == [{
        "post_id": pytest.approx(cut["posts"][0]["post_id"]),  # any id
        "platform": "instagram", "status": "published",
        "scheduled_at": None, "permalink": "https://insta/p/1/",
    }]
    assert cut["last_export"] == {"at": cut["last_export"]["at"], "path": "/out/cutA.mp4"}
    assert cut["last_export"]["at"] is not None


def test_cut_without_posts_has_empty_summary(client, campaign, make_clip, conn):
    clip = make_clip("plat_b")
    edit_id = client.post("/api/edits", json={"name": "Cut B", "campaign_id": campaign}).get_json()["id"]
    conn.execute("INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
                 "VALUES (?, ?, 0, 0, 1)", (edit_id, clip))
    conn.commit()
    cut = next(e for e in client.get("/api/edits").get_json() if e["id"] == edit_id)
    assert cut["posts"] == []
    assert cut["last_export"] is None


def test_summary_is_not_n_plus_one(client, campaign, make_clip, conn, monkeypatch):
    """The summary must use a bounded number of queries regardless of cut count —
    not one-per-cut. Assert the total query count doesn't scale with N."""
    import db as db_mod

    # three cuts, each with a post
    for i in range(3):
        clip = make_clip(f"plat_n{i}")
        eid = client.post("/api/edits", json={"name": f"C{i}", "campaign_id": campaign}).get_json()["id"]
        conn.execute("INSERT INTO timeline_items (edit_id, clip_id, position, in_point, out_point) "
                     "VALUES (?, ?, 0, 0, 1)", (eid, clip))
        conn.execute("INSERT INTO posts (campaign_id, edit_id, platform, status) "
                     "VALUES (?, ?, 'instagram', 'draft')", (campaign, eid))
    conn.commit()

    # Count SQL statements executed while serving the list, via sqlite's own trace hook.
    calls = {"n": 0}
    real_get_conn = db_mod.get_conn

    def counting_get_conn(*a, **k):
        c = real_get_conn(*a, **k)
        c.set_trace_callback(lambda _stmt: calls.__setitem__("n", calls["n"] + 1))
        return c

    monkeypatch.setattr(db_mod, "get_conn", counting_get_conn)
    client.get("/api/edits")
    # >0 proves the counter actually intercepted; <=6 (list + posts + last-export, a
    # small constant) still fails a per-cut (N+ queries) regression at 3 cuts.
    assert 0 < calls["n"] <= 6
