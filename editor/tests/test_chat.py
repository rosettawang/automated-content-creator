"""Case 5: edit chat (model mocked) — snapshot created, undo restores, messages persisted."""


def test_chat_snapshots_and_undo_restores(client, make_clip, mock_ai):
    a = make_clip("A")
    make_clip("B")   # in the pool, so the mocked revise expands the timeline to A+B
    eid = client.post("/api/edits", json={"name": "e"}).get_json()["id"]
    client.post(f"/api/edits/{eid}/items", json={"clip_id": a, "in_point": 0, "out_point": 1})

    before = [i["clip_id"] for i in client.get(f"/api/edits/{eid}").get_json()["items"]]
    assert before == [a]

    r = client.post(f"/api/edits/{eid}/chat", json={"prompt": "add clip B"})
    assert r.status_code == 200
    assert r.get_json()["can_undo"] is True

    after = [i["clip_id"] for i in client.get(f"/api/edits/{eid}").get_json()["items"]]
    assert len(after) == 2 and after != before   # timeline actually changed

    # transcript persisted: one user + one assistant message
    chat = client.get(f"/api/edits/{eid}/chat").get_json()
    roles = [m["role"] for m in chat["messages"]]
    assert roles == ["user", "assistant"]
    assert chat["can_undo"] is True

    # undo restores the pre-chat timeline and trims the message pair
    u = client.post(f"/api/edits/{eid}/undo")
    assert u.status_code == 200
    restored = [i["clip_id"] for i in client.get(f"/api/edits/{eid}").get_json()["items"]]
    assert restored == before
    chat2 = client.get(f"/api/edits/{eid}/chat").get_json()
    assert chat2["messages"] == []
    assert chat2["can_undo"] is False


def test_undo_with_nothing_to_undo_is_400(client):
    eid = client.post("/api/edits", json={"name": "e"}).get_json()["id"]
    assert client.post(f"/api/edits/{eid}/undo").status_code == 400
