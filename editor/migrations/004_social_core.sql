-- Social publishing — core (spec: specs/social-core.md).
-- Distribution domain, joined to production at the campaign. A `post` is one piece
-- of content scheduled/published to one platform account. Publishing is DB-driven:
-- the scheduler claims rows here, nothing fires from memory.

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    edit_id INTEGER REFERENCES edits(id) ON DELETE SET NULL,  -- null = text-only post
    platform TEXT NOT NULL,          -- 'instagram' | 'tiktok' | ...
    account_ref TEXT,                -- Composio connected-account id (never raw tokens)
    caption TEXT,
    hashtags TEXT,
    media_path TEXT,                 -- exported file captured at schedule time
    scheduled_at TEXT,               -- null = draft / post-now
    -- draft → scheduled → claimed → publishing → published | failed | cancelled
    -- (plus 'needs_review': interrupted mid-publish; never auto-retried)
    status TEXT NOT NULL DEFAULT 'draft',
    claimed_at TEXT,
    published_at TEXT,
    external_id TEXT,                -- platform post id, set on success
    error TEXT,
    idempotency_key TEXT UNIQUE,     -- post:{id}:{scheduled_at|now}; adapters no-op on repeat
    -- Boost/ad spend lives on the post (social-analytics writes spend later).
    boost_budget REAL,
    boost_spend REAL,
    boost_status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_posts_campaign ON posts(campaign_id);
-- The scheduler's hot query: due, still-schedulable rows.
CREATE INDEX IF NOT EXISTS idx_posts_due ON posts(status, scheduled_at);

-- Append-only metrics time series (written by social-analytics, read by the hub).
CREATE TABLE IF NOT EXISTS post_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    impressions INTEGER, reach INTEGER, likes INTEGER,
    comments INTEGER, shares INTEGER, saves INTEGER,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_post_metrics_post ON post_metrics(post_id);

-- Per-campaign arm switch. Real posting stays blocked until this is on AND
-- SOCIAL_DRY_RUN is off AND a real adapter exists (defence in depth).
ALTER TABLE campaigns ADD COLUMN publishing_armed INTEGER NOT NULL DEFAULT 0;
