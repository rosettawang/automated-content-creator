"""Case 2: media serving — MOV relabel, Range → 206, missing → 404 JSON."""


def test_mov_served_as_mp4(client, make_clip):
    cid = make_clip("MOVCLIP", ext=".mov")
    r = client.get(f"/api/clips/{cid}/media")
    assert r.status_code == 200
    # Chrome refuses video/quicktime, so .MOV must be relabeled to video/mp4.
    assert r.headers["Content-Type"] == "video/mp4"


def test_range_request_returns_206(client, make_clip):
    cid = make_clip("RANGECLIP", ext=".mp4")
    r = client.get(f"/api/clips/{cid}/media", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert "Content-Range" in r.headers
    assert r.headers.get("Accept-Ranges") == "bytes"


def test_missing_media_is_404_json(client, make_clip):
    cid = make_clip("GHOST", present=False)
    r = client.get(f"/api/clips/{cid}/media")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_unknown_clip_is_404(client):
    r = client.get("/api/clips/999999/media")
    assert r.status_code == 404
    assert "error" in r.get_json()
