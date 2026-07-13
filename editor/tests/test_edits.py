"""Case 3: edits — create, rename, add/reorder/trim/delete items, aspect validation."""


def _new_edit(client, name="My edit"):
    return client.post("/api/edits", json={"name": name}).get_json()["id"]


def test_create_and_rename_edit(client):
    eid = _new_edit(client, "First")
    assert client.get(f"/api/edits/{eid}").get_json()["name"] == "First"

    r = client.put(f"/api/edits/{eid}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert client.get(f"/api/edits/{eid}").get_json()["name"] == "Renamed"


def test_add_reorder_trim_delete_items(client, make_clip):
    eid = _new_edit(client)
    a = make_clip("A")
    b = make_clip("B")

    ia = client.post(f"/api/edits/{eid}/items", json={"clip_id": a, "in_point": 0, "out_point": 1}).get_json()
    ib = client.post(f"/api/edits/{eid}/items", json={"clip_id": b, "in_point": 0, "out_point": 1}).get_json()
    item_a = ia.get("id") or ia.get("item_id")
    item_b = ib.get("id") or ib.get("item_id")

    items = client.get(f"/api/edits/{eid}").get_json()["items"]
    assert [i["clip_id"] for i in items] == [a, b]

    # reorder B before A
    client.post(f"/api/edits/{eid}/reorder", json={"item_ids": [item_b, item_a]})
    items = client.get(f"/api/edits/{eid}").get_json()["items"]
    assert [i["clip_id"] for i in items] == [b, a]

    # trim: change A's out_point
    client.put(f"/api/edits/{eid}/items/{item_a}", json={"out_point": 0.5})
    items = {i["id"]: i for i in client.get(f"/api/edits/{eid}").get_json()["items"]}
    assert items[item_a]["out_point"] == 0.5

    # delete B
    client.delete(f"/api/edits/{eid}/items/{item_b}")
    items = client.get(f"/api/edits/{eid}").get_json()["items"]
    assert [i["clip_id"] for i in items] == [a]


def test_aspect_put_validation(client):
    eid = _new_edit(client)
    assert client.put(f"/api/edits/{eid}", json={"aspect": "9:16"}).status_code == 200
    assert client.get(f"/api/edits/{eid}").get_json()["aspect"] == "9:16"

    bad = client.put(f"/api/edits/{eid}", json={"aspect": "banana"})
    assert bad.status_code == 400
    assert "error" in bad.get_json()


def test_get_unknown_edit_404(client):
    assert client.get("/api/edits/999999").status_code == 404
