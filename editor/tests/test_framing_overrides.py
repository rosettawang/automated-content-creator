"""Framing v2 / Stage 4: manual crop overrides are sticky across an aspect change,
and Reset-to-auto hands the item back to auto-framing."""


def _item(client, eid):
    return client.get(f"/api/edits/{eid}").get_json()["items"][0]


def _add(client, eid, cid):
    return client.post(f"/api/edits/{eid}/items",
                       json={"clip_id": cid, "in_point": 0, "out_point": 1}).get_json()["item_id"]


def test_manual_crop_survives_aspect_change(client, make_clip):
    eid = client.post("/api/edits", json={"name": "f"}).get_json()["id"]
    iid = _add(client, eid, make_clip("OVR_A", present=True))
    client.put(f"/api/edits/{eid}", json={"aspect": "9:16"})

    # Human drags a crop → tagged manual.
    client.put(f"/api/edits/{eid}/items/{iid}",
               json={"crop_x": 0.1, "crop_y": 0.0, "crop_w": 0.3, "crop_h": 1.0})
    it = _item(client, eid)
    assert it["crop_source"] == "manual" and it["crop_x"] == 0.1

    # Changing the output aspect recomputes AUTO framing — the manual crop must survive.
    client.put(f"/api/edits/{eid}", json={"aspect": "1:1"})
    it = _item(client, eid)
    assert it["crop_source"] == "manual", "aspect change wiped a human crop"
    assert it["crop_x"] == 0.1


def test_reset_to_auto_clears_manual_flag(client, make_clip):
    eid = client.post("/api/edits", json={"name": "f"}).get_json()["id"]
    iid = _add(client, eid, make_clip("OVR_B", present=True))
    client.put(f"/api/edits/{eid}/items/{iid}",
               json={"crop_x": 0.1, "crop_y": 0.0, "crop_w": 0.3, "crop_h": 1.0})
    assert _item(client, eid)["crop_source"] == "manual"

    # Reset to auto (crop_x=null) → back under auto-framing's control.
    client.put(f"/api/edits/{eid}/items/{iid}",
               json={"crop_x": None, "crop_y": None, "crop_w": None, "crop_h": None})
    it = _item(client, eid)
    assert it["crop_x"] is None
    assert it["crop_source"] is None


def test_chat_framing_sets_manual_crop(client, make_clip, monkeypatch):
    """Edit chat that asks to reframe returns a crop center; the server turns it into an
    aspect-correct, sticky (manual) crop window containing that point."""
    import blueprints.edits as edits
    from claude_client import EditChatResult, ClipSelection, CropEdit

    eid = client.post("/api/edits", json={"name": "f"}).get_json()["id"]
    cid = make_clip("OVR_D", present=True)
    _add(client, eid, cid)
    client.put(f"/api/edits/{eid}", json={"aspect": "9:16"})

    def fake_revise(instruction, current_timeline, clips, aspect=None):
        return EditChatResult(
            reply="Framed on the subject.",
            selections=[ClipSelection(clip_id=cid, in_point=0.0, out_point=1.0, reason="t")],
            crops=[CropEdit(index=0, cx=0.8, cy=0.5)],
        )
    monkeypatch.setattr(edits, "revise_edit", fake_revise)

    r = client.post(f"/api/edits/{eid}/chat", json={"prompt": "keep the bowl on the right in frame"})
    assert r.status_code == 200, r.get_json()
    it = _item(client, eid)
    assert it["crop_source"] == "manual"
    assert it["crop_x"] is not None
    assert it["crop_x"] <= 0.8 <= it["crop_x"] + it["crop_w"], it   # window contains the requested center


def test_trim_does_not_flag_crop_source(client, make_clip):
    """A pure trim (in/out only) must not accidentally mark the item as a manual crop."""
    eid = client.post("/api/edits", json={"name": "f"}).get_json()["id"]
    iid = _add(client, eid, make_clip("OVR_C", present=True))
    client.put(f"/api/edits/{eid}/items/{iid}", json={"out_point": 0.5})
    assert _item(client, eid)["crop_source"] is None
