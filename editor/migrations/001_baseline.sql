-- 001_baseline — the full current schema as one ordered starting point.
--
-- Generated from the live editor.db .schema (2026-07), folding in every column
-- previously added by ad-hoc ALTERs (clips.transcript/source_kind/…,
-- timeline_items.crop_*/kb_*, edits.aspect, campaigns.description/context_doc)
-- and the two tables born at import time in core.py (thing_thumbs, clip_regions).
--
-- Everything is IF NOT EXISTS, so applying this to an existing database is a
-- no-op — that's how current installs adopt the migration chain cleanly, while a
-- fresh database reaches exactly today's schema in one step.

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
    indexed_at TEXT,
    latitude REAL,
    longitude REAL,
    kind TEXT,
    content_hash TEXT,
    width INTEGER,
    height INTEGER,
    sharpness REAL,
    quality INTEGER,
    media_path TEXT,
    media_checked_at TEXT,
    media_status TEXT,
    source_kind TEXT,
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    context_doc TEXT
);

CREATE TABLE IF NOT EXISTS campaign_clips (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (campaign_id, clip_id)
);

CREATE TABLE IF NOT EXISTS campaign_things (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    thing_id INTEGER NOT NULL REFERENCES things(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (campaign_id, thing_id)
);

CREATE TABLE IF NOT EXISTS campaign_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    aspect TEXT
);

CREATE TABLE IF NOT EXISTS timeline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id),
    position INTEGER NOT NULL,
    in_point REAL NOT NULL DEFAULT 0,
    out_point REAL NOT NULL,
    crop_x REAL, crop_y REAL, crop_w REAL, crop_h REAL,
    kb_x REAL, kb_y REAL, kb_w REAL, kb_h REAL
);

CREATE TABLE IF NOT EXISTS edit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    label TEXT,
    data TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS edit_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS things (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT,                          -- plant | animal | action | object | person | other
    description TEXT,                   -- optional hint that helps the model spot it
    active INTEGER NOT NULL DEFAULT 1,  -- 1 => injected into future indexing
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_things (
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    thing_id INTEGER NOT NULL REFERENCES things(id) ON DELETE CASCADE,
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (clip_id, thing_id)
);

CREATE TABLE IF NOT EXISTS thing_thumbs (
    thing_id INTEGER PRIMARY KEY REFERENCES things(id) ON DELETE CASCADE,
    clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    chosen_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,        -- speech | thing | action | scene
    label TEXT,                -- for thing/action: the subject name
    text TEXT,                 -- for speech/scene: the words / description
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    score REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    thing_id INTEGER REFERENCES things(id) ON DELETE SET NULL,
    label TEXT,
    x REAL, y REAL, w REAL, h REAL,
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_clip_regions_clip ON clip_regions(clip_id);

CREATE TABLE IF NOT EXISTS clip_embeddings (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    text_hash TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

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
