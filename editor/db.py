import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "editor.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers and a writer proceed concurrently; busy_timeout makes the
    # ~14 background job threads + request threads wait for a lock instead of
    # failing immediately with "database is locked". WAL persists on the db file
    # (idempotent to set per-connection); busy_timeout is per-connection.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ---------------------------------------------------------------------------
# Schema migrations
#
# One ordered chain of `migrations/NNN_*.sql` files, tracked in a
# `schema_migrations` table, replaces the old scatter of `CREATE TABLE IF NOT
# EXISTS` + ad-hoc ALTERs. `001_baseline.sql` is the full current schema (a
# no-op on already-current databases via IF NOT EXISTS); later schema changes
# ship as `002_*.sql`, `003_*.sql`, … and never edit an applied migration.
# ---------------------------------------------------------------------------

def _ensure_migrations_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT)"
    )


def _applied_versions(conn) -> set[int]:
    _ensure_migrations_table(conn)
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}


def _migration_files() -> list[tuple[int, Path]]:
    """All `NNN_*.sql` migrations, sorted by their numeric version."""
    files: list[tuple[int, Path]] = []
    for p in MIGRATIONS_DIR.glob("[0-9]*.sql"):
        num = p.name.split("_", 1)[0]
        if num.isdigit():
            files.append((int(num), p))
    files.sort(key=lambda t: t[0])
    return files


def _apply_sql_file(conn, path: Path) -> None:
    conn.executescript(path.read_text())


def _record_migration(conn, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, datetime.now(timezone.utc).isoformat()),
    )


def _migrate_project_to_campaign(conn):
    """Rename the legacy `project*` tables/columns to `campaign*` in place, before
    the baseline's CREATE TABLE IF NOT EXISTS would otherwise make empty new ones
    and strand the old data. Idempotent: only renames what still has the old name."""
    tbls = lambda: {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for old, new in (("projects", "campaigns"), ("project_clips", "campaign_clips"),
                     ("project_things", "campaign_things"),
                     ("project_messages", "campaign_messages")):
        t = tbls()
        if old in t and new not in t:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
    # Rename the FK column project_id -> campaign_id wherever it survives.
    for tbl in ("edits", "campaign_clips", "campaign_things", "campaign_messages"):
        if tbl in tbls():
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
            if "project_id" in cols and "campaign_id" not in cols:
                conn.execute(f"ALTER TABLE {tbl} RENAME COLUMN project_id TO campaign_id")


def _reconcile_added_columns(conn):
    """Bring a *pre-migration* database (created by the old scattered code) up to the
    baseline by adding any columns it's missing. SQLite has no ADD COLUMN IF NOT
    EXISTS, so each add is guarded by a table_info check. On a fresh database the
    baseline already created every column, so all of these are no-ops.

    Kept in Python (not SQL) precisely because it's conditional per existing column;
    once every live database has adopted the chain this can retire."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(clips)")}
    for col, typ in (("transcript", "TEXT"), ("tags", "TEXT"), ("context", "TEXT"),
                     ("location", "TEXT"), ("latitude", "REAL"), ("longitude", "REAL"),
                     ("indexed_at", "TEXT"), ("kind", "TEXT"), ("content_hash", "TEXT"),
                     ("width", "INTEGER"), ("height", "INTEGER"),
                     ("sharpness", "REAL"), ("quality", "INTEGER"),
                     # Media-presence tracking: last-known-good absolute path, when it
                     # was last verified on disk, and the verified state.
                     ("media_path", "TEXT"), ("media_checked_at", "TEXT"),
                     ("media_status", "TEXT"),
                     # Provenance: where this clip's file came from, so a missing/absent
                     # file can be re-downloaded. source_kind ∈ drive|photos|zip|local|
                     # upload; source_url is the remote link when there is one.
                     ("source_kind", "TEXT"), ("source_url", "TEXT")):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {typ}")

    campaign_cols = {row["name"] for row in conn.execute("PRAGMA table_info(campaigns)")}
    if "description" not in campaign_cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN description TEXT")
    # Living campaign brief the chat keeps up to date (subject/angle/tone/decisions).
    if "context_doc" not in campaign_cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN context_doc TEXT")

    # One-time restructure: originally a "campaign" row WAS a timeline, with
    # timeline_items.campaign_id pointing at it. The model is now
    # Campaign (theme) -> many Edits -> timeline_items. Old campaigns rows were really
    # edits (test generations); per product decision they're discarded so we start
    # clean on the new hierarchy.
    tl_cols = {row["name"] for row in conn.execute("PRAGMA table_info(timeline_items)")}
    if "campaign_id" in tl_cols and "edit_id" not in tl_cols:
        conn.executescript(
            """
            DROP TABLE IF EXISTS timeline_items;
            DELETE FROM campaigns;
            CREATE TABLE timeline_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
                clip_id INTEGER NOT NULL REFERENCES clips(id),
                position INTEGER NOT NULL,
                in_point REAL NOT NULL DEFAULT 0,
                out_point REAL NOT NULL
            );
            """
        )
    # Reframe columns (added later): output aspect on edits, per-item crop/kb rect.
    edit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(edits)")}
    if "aspect" not in edit_cols:
        conn.execute("ALTER TABLE edits ADD COLUMN aspect TEXT")
    tl_cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(timeline_items)")}
    for col in ("crop_x", "crop_y", "crop_w", "crop_h", "kb_x", "kb_y", "kb_w", "kb_h"):
        if col not in tl_cols2:
            conn.execute(f"ALTER TABLE timeline_items ADD COLUMN {col} REAL")


def init_db():
    """Bring the database to the latest schema by applying any migrations it hasn't
    seen. Safe on a fresh DB (creates everything from 001), an already-current DB
    (all no-ops), and a legacy pre-migration DB (reconciled once, then stamped)."""
    conn = get_conn()
    _ensure_migrations_table(conn)
    files = _migration_files()
    applied = _applied_versions(conn)

    # Guard: refuse to run against a database migrated by newer code than this.
    max_known = max((v for v, _ in files), default=0)
    db_version = max(applied, default=0)
    if db_version > max_known:
        conn.close()
        raise RuntimeError(
            f"Database schema is at version {db_version}, but this code only knows up "
            f"to {max_known}. Update the app before opening this database."
        )

    for version, path in files:
        if version in applied:
            continue
        if version == 1:
            # Baseline. Also reconcile any pre-migration DB so existing installs
            # (legacy `project*` names, columns from old ad-hoc ALTERs) adopt the
            # chain cleanly. All of these are no-ops on a fresh or current DB.
            _migrate_project_to_campaign(conn)
            _apply_sql_file(conn, path)
            _reconcile_added_columns(conn)
        else:
            _apply_sql_file(conn, path)
        _record_migration(conn, version)
        conn.commit()  # one committed step per migration

    conn.close()


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def stamp_file_metadata(path: Path, *, description: str = "", category: str = "",
                        tags: str = "", context: str = "") -> None:
    """Embed metadata into a media file's XMP/EXIF so it travels with the file.

    Maps our fields onto widely-read standard tags:
        Description -> the human 'what's in it' blurb (context preferred, else description)
        Keywords    -> category + individual tags (multi-valued)
        UserComment -> the full context note
    Requires exiftool on PATH; raises RuntimeError if it's missing or fails.
    """
    if not exiftool_available():
        raise RuntimeError(
            "exiftool not found on PATH -- install it (brew install exiftool) to embed "
            "metadata into files."
        )

    blurb = (context or description or "").strip()
    keywords: list[str] = []
    if category.strip():
        keywords.append(category.strip())
    keywords.extend(t.strip() for t in tags.split(",") if t.strip())

    # Two passes, because within a single exiftool command `-Keywords+=` appends to
    # the *original* file value -- so combining a `-Keywords=` clear with `+=` adds in
    # one call still accumulates. Pass 1 clears the multi-valued keyword tags; pass 2
    # writes the scalar fields and adds the fresh keywords. Net result: a true rewrite.
    clear = ["exiftool", "-overwrite_original", "-P",
             "-Keywords=", "-Subject=", "-XMP-dc:Subject=", str(path)]
    proc = subprocess.run(clear, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "exiftool failed (clear pass)")

    cmd = ["exiftool", "-overwrite_original", "-P"]
    cmd.append(f"-Description={blurb}")
    cmd.append(f"-XMP-dc:Description={blurb}")
    cmd.append(f"-UserComment={(context or '').strip()}")
    for kw in keywords:
        cmd.append(f"-Keywords+={kw}")
        cmd.append(f"-XMP-dc:Subject+={kw}")
    cmd.append(str(path))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "exiftool failed")
