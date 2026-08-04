-- Platform status icons + open-on-platform links (spec: specs/platform-links.html).
-- Two additions so a cut can show where it's live and link straight to the post:
--   1. posts.permalink — the public URL of a published post, captured at publish time
--      by the adapter (never guessed client-side; URL formats differ per platform).
--   2. edit_exports — one row per finished export render, so a cut card can show an
--      "exported" badge and the desktop shell can offer "Open folder".

ALTER TABLE posts ADD COLUMN permalink TEXT;

CREATE TABLE IF NOT EXISTS edit_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id INTEGER NOT NULL REFERENCES edits(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    fps INTEGER,
    finished_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_edit_exports_edit ON edit_exports(edit_id);
