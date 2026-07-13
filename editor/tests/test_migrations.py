"""Schema-migration round-trip tests (see specs/schema-migrations.md acceptance).

Each test points db.DB_PATH at an isolated temp file via monkeypatch (auto-restored),
so these never touch the shared fixture DB.
"""
import sqlite3

import pytest

import db

# Every table the baseline (001) is expected to create on a fresh database.
EXPECTED_TABLES = {
    "clips", "campaigns", "campaign_clips", "campaign_things", "campaign_messages",
    "edits", "timeline_items", "edit_snapshots", "edit_messages",
    "things", "clip_things", "thing_thumbs",
    "clip_events", "clip_regions", "clip_embeddings",
    "people", "faces", "settings", "jobs",
}


def _tables(path):
    c = sqlite3.connect(path)
    names = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    c.close()
    return names


def _columns(path, table):
    c = sqlite3.connect(path)
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    c.close()
    return cols


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "migrate.db")
    return db.DB_PATH


def test_fresh_db_reaches_full_schema(fresh_db):
    db.init_db()
    assert EXPECTED_TABLES <= _tables(fresh_db)


def test_baseline_recorded_as_version_1(fresh_db):
    db.init_db()
    c = db.get_conn()
    versions = [r["version"] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")]
    c.close()
    assert versions == [1]


def test_clips_has_all_migrated_columns(fresh_db):
    """Columns that used to be added by ad-hoc ALTERs must be present from the baseline."""
    db.init_db()
    cols = _columns(fresh_db, "clips")
    assert {"transcript", "tags", "content_hash", "kind",
            "source_kind", "source_url", "media_status"} <= cols


def test_init_db_is_idempotent(fresh_db):
    db.init_db()
    db.init_db()  # second run must not re-apply or duplicate
    c = db.get_conn()
    row = c.execute("SELECT COUNT(*) n, MAX(version) m FROM schema_migrations").fetchone()
    c.close()
    assert row["n"] == 1 and row["m"] == 1


def test_refuses_db_newer_than_code(fresh_db):
    db.init_db()
    c = db.get_conn()
    c.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (999, 'future')")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match="newer|version"):
        db.init_db()
