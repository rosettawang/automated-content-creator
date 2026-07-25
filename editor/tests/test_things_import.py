"""Import-unified: watchlist things created from import chips are stored verbatim.

The unified import box sends each committed chip to POST /api/things as-is. These
guard the server side of "the list sent to /api/things matches the chips exactly":
a chip with internal spaces / a parenthetical must round-trip unmangled, and it must
never be split on its internal comma into spurious extra things.
"""


def _names(client):
    return {t["name"] for t in client.get("/api/things").get_json()}


def test_thing_name_with_spaces_and_parens_kept_verbatim(client):
    r = client.post("/api/things", json={"name": "oil press (the big one)"})
    assert r.status_code in (200, 201)
    assert r.get_json()["name"] == "oil press (the big one)"
    assert "oil press (the big one)" in _names(client)


def test_thing_name_is_trimmed_once_not_split(client):
    # A chip the user typed with stray outer spaces: trimmed once, never split on the
    # internal comma (which would have created a spurious "the big one" thing).
    client.post("/api/things", json={"name": "  oil press, the big one  "})
    names = _names(client)
    assert "oil press, the big one" in names
    assert "the big one" not in names


def test_import_chips_round_trip_exactly(client):
    chips = ["pipevine", "oil press (the big one)", "monarch butterfly"]
    for c in chips:
        client.post("/api/things", json={"name": c})
    names = _names(client)
    for c in chips:
        assert c in names
