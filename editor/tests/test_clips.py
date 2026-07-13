"""Case 1: clips list, search, and available_locally correctness."""


def test_list_clips_empty(client):
    r = client.get("/api/clips")
    assert r.status_code == 200
    assert r.get_json() == []


def test_list_and_availability(client, make_clip):
    make_clip("PRESENT_A", present=True, description="a bee on a flower")
    make_clip("GHOST_B", present=False, description="not downloaded")

    clips = client.get("/api/clips").get_json()
    by_stem = {c["file_stem"]: c for c in clips}
    assert len(clips) == 2

    assert by_stem["PRESENT_A"]["available_locally"] is True
    assert by_stem["PRESENT_A"]["availability"] == "present"
    assert by_stem["GHOST_B"]["available_locally"] is False
    assert by_stem["GHOST_B"]["availability"] == "absent"


def test_search_matches_description_tags_category(client, make_clip):
    make_clip("SWALLOWTAIL", description="a pipevine swallowtail butterfly", category="Wildlife")
    make_clip("OILPRESS", description="an oil press machine", category="Machinery", tags="press, oil")

    def stems(q):
        return {c["file_stem"] for c in client.get(f"/api/clips?q={q}").get_json()}

    assert stems("butterfly") == {"SWALLOWTAIL"}        # description
    assert stems("machinery") == {"OILPRESS"}           # category
    assert stems("press") == {"OILPRESS"}               # tags
    assert stems("swallowtail") == {"SWALLOWTAIL"}      # file_stem
    assert stems("zzz-no-match") == set()
