import shutil
import sqlite3
import subprocess
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
    tags TEXT,
    context TEXT,
    location TEXT,
    latitude REAL,
    longitude REAL,
    indexed_at TEXT,
    kind TEXT,
    content_hash TEXT,
    -- Technical quality (measured on-device at index time; informational, never
    -- auto-excludes). width/height = display resolution (rotation-aware);
    -- sharpness = variance-of-Laplacian (higher = crisper); quality = 0-100 heuristic.
    width INTEGER,
    height INTEGER,
    sharpness REAL,
    quality INTEGER,
    -- Provenance: how to get the file back if it's not local (see migration note).
    source_kind TEXT,
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- An "edit" is one assembled timeline (a rough cut / mix). A project (theme) has
-- many edits. project_id is nullable so an edit can exist unassigned.
CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    aspect TEXT,                        -- output aspect: 'source'|'9:16'|'1:1'|'16:9'|'4:5'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS timeline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id),
    position INTEGER NOT NULL,
    in_point REAL NOT NULL DEFAULT 0,
    out_point REAL NOT NULL,
    -- Reframe crop rect as fractions of the SOURCE frame (0..1). NULL => auto
    -- center-crop to fill the edit's target aspect. All four set together.
    crop_x REAL, crop_y REAL, crop_w REAL, crop_h REAL,
    -- Ken Burns END rect (crop_* is the START). When all set, the crop animates
    -- linearly from start->end over the clip. NULL => static crop (no motion).
    kb_x REAL, kb_y REAL, kb_w REAL, kb_h REAL
);

-- Which clips belong to which project (a clip may belong to many projects).
CREATE TABLE IF NOT EXISTS project_clips (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, clip_id)
);

-- User-named "things" to watch for: a plant species (pipevine), an action, an
-- object, a person, etc. Active things are injected into the frame-analysis
-- prompt so indexing actively looks for them.
CREATE TABLE IF NOT EXISTS things (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT,                          -- plant | animal | action | object | person | other
    description TEXT,                   -- optional hint that helps the model spot it
    active INTEGER NOT NULL DEFAULT 1,  -- 1 => injected into future indexing
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A clip contains a thing (recorded when analysis detects it).
CREATE TABLE IF NOT EXISTS clip_things (
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    thing_id INTEGER NOT NULL REFERENCES things(id) ON DELETE CASCADE,
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (clip_id, thing_id)
);

-- Which things matter to which campaign (inferred at creation, then user-editable).
CREATE TABLE IF NOT EXISTS project_things (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thing_id INTEGER NOT NULL REFERENCES things(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, thing_id)
);

-- Per-campaign chat history (the assistant conversation shown in the campaign drawer).
CREATE TABLE IF NOT EXISTS project_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Timestamped events within a clip -- the substrate for editing. One row per
-- speech segment (kind='speech', text=words) or visual event (kind='thing'/'action',
-- label=name). t_start/t_end are seconds into the clip.
CREATE TABLE IF NOT EXISTS clip_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,        -- speech | thing | action
    label TEXT,                -- for thing/action: the subject name
    text TEXT,                 -- for speech: the spoken words
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    score REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Simple key/value app settings (e.g. whether analysis runs on-device or via Claude).
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Durable background-job records (import, index, faces, motion, deep-index, export…).
-- The live copy lives in memory for fast per-item progress; this table is written
-- through on transitions + throttled progress so job state survives a restart
-- (results stay readable; unfinished rows are marked interrupted on startup).
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    label TEXT,
    unit TEXT,
    phase TEXT,
    total INTEGER,
    done INTEGER DEFAULT 0,
    current TEXT,
    error TEXT,
    cancelled INTEGER DEFAULT 0,
    finished INTEGER DEFAULT 0,
    results TEXT,                         -- JSON, populated on finish
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_finished ON jobs(finished);

-- Semantic-search vectors: one embedding per clip over its combined text.
-- text_hash lets us skip re-embedding clips whose text hasn't changed.
CREATE TABLE IF NOT EXISTS clip_embeddings (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    text_hash TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A named individual for face recognition.
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A face detected in a clip's keyframe. person_id is set once named; until then
-- cluster_id provisionally groups faces that look like the same person.
CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    cluster_id INTEGER,
    embedding BLOB NOT NULL,   -- float32[512]
    box TEXT,                  -- json [x1,y1,x2,y2]
    prob REAL,
    thumb_path TEXT,           -- cropped face jpg under data/faces/
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Undo stack for an edit's timeline: each prompt-driven change pushes a snapshot
-- of the timeline BEFORE it was applied, so it can be restored. `data` is a JSON
-- array of {clip_id, position, in_point, out_point}.
CREATE TABLE IF NOT EXISTS edit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    label TEXT,
    data TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Chat transcript for an edit (the "prompt further edits" conversation).
CREATE TABLE IF NOT EXISTS edit_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


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


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(clips)")}
    if "transcript" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN transcript TEXT")
    if "tags" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN tags TEXT")
    if "context" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN context TEXT")
    if "location" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN location TEXT")
    if "latitude" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN latitude REAL")
    if "longitude" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN longitude REAL")
    if "indexed_at" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN indexed_at TEXT")
    if "kind" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN kind TEXT")
    if "content_hash" not in existing_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN content_hash TEXT")
    for col, typ in (("width", "INTEGER"), ("height", "INTEGER"),
                     ("sharpness", "REAL"), ("quality", "INTEGER"),
                     # Media-presence tracking: last-known-good absolute path, when it
                     # was last verified on disk, and the verified state.
                     ("media_path", "TEXT"), ("media_checked_at", "TEXT"),
                     ("media_status", "TEXT"),
                     # Provenance: where this clip's file came from, so a missing/absent
                     # file can be re-downloaded. source_kind ∈ drive|photos|zip|local|
                     # upload; source_url is the remote link (Drive file/folder, or the
                     # Google Photos album) when there is one. Metadata-only catalog rows
                     # keep their provenance even with no local file.
                     ("source_kind", "TEXT"), ("source_url", "TEXT")):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {typ}")
    project_cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    if "description" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN description TEXT")

    # One-time restructure: originally a "project" row WAS a timeline, with
    # timeline_items.project_id pointing at it. The model is now
    # Project (theme) -> many Edits -> timeline_items. Old projects rows were really
    # edits (test generations); per product decision they're discarded so we start
    # clean on the new hierarchy.
    tl_cols = {row["name"] for row in conn.execute("PRAGMA table_info(timeline_items)")}
    if "project_id" in tl_cols and "edit_id" not in tl_cols:
        conn.executescript(
            """
            DROP TABLE IF EXISTS timeline_items;
            DELETE FROM projects;
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
    # Reframe columns (added later): output aspect on edits, per-item crop rect.
    edit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(edits)")}
    if "aspect" not in edit_cols:
        conn.execute("ALTER TABLE edits ADD COLUMN aspect TEXT")
    tl_cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(timeline_items)")}
    for col in ("crop_x", "crop_y", "crop_w", "crop_h", "kb_x", "kb_y", "kb_w", "kb_h"):
        if col not in tl_cols2:
            conn.execute(f"ALTER TABLE timeline_items ADD COLUMN {col} REAL")

    conn.commit()
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
