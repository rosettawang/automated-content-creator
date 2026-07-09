import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "editor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_stem TEXT UNIQUE NOT NULL,
    duration_s REAL,
    category TEXT,
    description TEXT,
    status TEXT,
    transcript TEXT,
    tags TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS timeline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id),
    position INTEGER NOT NULL,
    in_point REAL NOT NULL DEFAULT 0,
    out_point REAL NOT NULL
);
"""


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(clips)")}
    if "transcript" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN transcript TEXT")
    if "tags" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN tags TEXT")
    conn.commit()
    conn.close()
