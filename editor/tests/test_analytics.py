"""Social analytics — metrics ingestion + summary (spec: specs/social-analytics.md)."""
import pytest

from social import scheduler
from social.analytics import ingest_metrics, summarize_campaign


@pytest.fixture()
def campaign(conn):
    cur = conn.execute("INSERT INTO campaigns (name) VALUES ('Analytics campaign')")
    conn.commit()
    return cur.lastrowid


def _publish_a_post(client, conn, campaign, caption="Caterpillar macro"):
    pid = client.post(f"/api/campaigns/{campaign}/posts", json={
        "platform": "instagram", "caption": caption,
        "scheduled_at": "2000-01-01T00:00:00+00:00"}).get_json()["id"]
    scheduler.claim_due_posts(conn)
    scheduler.publish_post(pid)
    return pid


# ---- summarize_campaign is pure ----
def test_summarize_no_data():
    s = summarize_campaign([])
    assert s["has_data"] is False and "No metrics" in s["headline"]


def test_summarize_ranks_and_totals():
    posts = [
        {"id": 1, "platform": "instagram", "caption": "A", "reach": 1000, "saves": 10, "boost_spend": 20},
        {"id": 2, "platform": "tiktok", "caption": "B", "reach": 5000, "saves": 90, "boost_spend": 0},
        {"id": 3, "platform": "instagram", "caption": "C", "reach": 200, "saves": 2},
    ]
    s = summarize_campaign(posts)
    assert s["has_data"] is True
    assert s["published_count"] == 3
    assert s["total_reach"] == 6200
    assert s["total_spend"] == 20
    assert s["top_posts"][0]["id"] == 2           # highest reach first
    assert set(s["by_platform"]) == {"instagram", "tiktok"}


# ---- ingestion is append-only and feeds the summary ----
def test_ingest_appends_and_summarizes(client, campaign, conn):
    _publish_a_post(client, conn, campaign)
    ingest_metrics(campaign)
    ingest_metrics(campaign)  # twice → two rows (append-only, history preserved)

    n = conn.execute("SELECT COUNT(*) FROM post_metrics").fetchone()[0]
    assert n == 2

    summary = client.get(f"/api/campaigns/{campaign}/summary").get_json()
    assert summary["has_data"] is True
    assert summary["published_count"] == 1
    assert summary["total_reach"] > 0
    # subject falls back to caption when the post has no linked edit
    assert "Caterpillar" in summary["top_posts"][0]["label"]


def test_summary_endpoint_empty_campaign(client, campaign):
    s = client.get(f"/api/campaigns/{campaign}/summary").get_json()
    assert s["has_data"] is False


def test_chat_receives_metrics_summary(client, campaign, conn, monkeypatch):
    """social-analytics E: the campaign chat is handed the performance summary so it
    can ground recommendations in real reach/saves."""
    import blueprints.campaigns as camp
    from claude_client import CampaignChatResult

    captured = {}

    def fake_chat(campaign, things, in_campaign, catalog, history, message, metrics_summary=None):
        captured["summary"] = metrics_summary
        return CampaignChatResult(reply="ok")

    monkeypatch.setattr(camp, "campaign_chat", fake_chat)

    _publish_a_post(client, conn, campaign, caption="Top performer")
    ingest_metrics(campaign)

    r = client.post(f"/api/campaigns/{campaign}/chat", json={"message": "what next?"})
    assert r.status_code == 200
    assert captured["summary"] and captured["summary"]["has_data"] is True
    assert captured["summary"]["published_count"] == 1
