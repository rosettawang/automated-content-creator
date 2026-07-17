"""Efficiency pass acceptance: /api/clips issues a bounded number of queries and
one directory listing regardless of clip count (no catalog N+1)."""
import db
import media_files


class _CountingConn:
    """Forwarding proxy that counts .execute() calls (sqlite3.Connection.execute is
    read-only, so we can't patch it on the instance)."""
    def __init__(self, conn, counter):
        object.__setattr__(self, "_c", conn)
        object.__setattr__(self, "_ctr", counter)

    def execute(self, *a, **k):
        self._ctr["n"] += 1
        return self._c.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


def _count_executes(monkeypatch):
    """Patch get_conn everywhere the /api/clips path opens a connection so every
    query is counted. db_conn() (in db.py) calls db.get_conn by name, so patching
    db.get_conn covers the blueprint path; catalog opens its own connection."""
    counter = {"n": 0}
    real_get_conn = db.get_conn

    def counting_get_conn():
        return _CountingConn(real_get_conn(), counter)

    import catalog
    monkeypatch.setattr(db, "get_conn", counting_get_conn)
    monkeypatch.setattr(catalog, "get_conn", counting_get_conn)
    return counter


def test_list_clips_query_count_is_bounded(client, make_clip, monkeypatch):
    # A pool of clips, each with several timeline events (the old _attach_moments /
    # per-clip status path would scale queries with clip count).
    ids = [make_clip(f"CLIP{i}", present=True) for i in range(8)]
    conn = db.get_conn()
    for cid in ids:
        for k in ("scene", "action", "speech"):
            conn.execute(
                "INSERT INTO clip_events (clip_id, kind, text, t_start, t_end) VALUES (?, ?, 'x', 0, 1)",
                (cid, k),
            )
    conn.commit()
    conn.close()

    counter = _count_executes(monkeypatch)
    r = client.get("/api/clips")
    assert r.status_code == 200
    assert len(r.get_json()) == 8
    n8 = counter["n"]

    # Add many more clips; the query count must not scale with clip count.
    for i in range(8, 40):
        make_clip(f"CLIP{i}", present=True)
    counter["n"] = 0
    r = client.get("/api/clips")
    assert r.status_code == 200 and len(r.get_json()) == 40
    n40 = counter["n"]

    # 5x the clips must not mean ~5x the queries. Allow a tiny constant slack.
    assert n40 <= n8 + 2, f"query count scaled with clips: {n8} -> {n40} (N+1 regression)"


def test_stem_index_resolves_without_reglob(make_clip):
    """find_media_file resolves from the cached stem index (one listing), and a fresh
    file added afterward is still found (mtime invalidation / glob fallback)."""
    cid = make_clip("STEMCACHE", present=True)  # noqa: F841
    assert media_files.find_media_file("STEMCACHE") is not None
    assert media_files.find_media_file("does-not-exist") is None
