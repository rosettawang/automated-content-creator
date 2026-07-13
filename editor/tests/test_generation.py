"""Case 4: generation (model mocked) — pool excludes ghosts; empty pool → 400."""


def test_generate_edit_excludes_ghosts(client, make_clip, mock_ai):
    present = make_clip("PRESENT", present=True)
    ghost = make_clip("GHOST", present=False)

    r = client.post("/api/generate-edit", json={"prompt": "make a reel"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "id" in body

    # The pool handed to the model must exclude catalog-only ghosts.
    pool_ids = {c["id"] for c in mock_ai["generate"][-1]["clips"]}
    assert present in pool_ids
    assert ghost not in pool_ids

    # The new edit's timeline references only real clips.
    items = client.get(f"/api/edits/{body['id']}").get_json()["items"]
    assert items and all(i["clip_id"] == present for i in items)


def test_generate_empty_pool_is_400(client, make_clip, mock_ai):
    make_clip("ONLY_GHOST", present=False)   # nothing downloadable
    r = client.post("/api/generate-edit", json={"prompt": "make a reel"})
    assert r.status_code == 400
    assert "error" in r.get_json()
    assert not mock_ai["generate"]           # model never called with an empty pool


def test_generate_requires_prompt(client):
    r = client.post("/api/generate-edit", json={})
    assert r.status_code == 400
