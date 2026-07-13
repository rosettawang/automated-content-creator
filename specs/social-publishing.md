# Spec: Social publishing, scheduling & analytics (under Campaigns)

*Written 2026-07-13. This is the feature most likely to become the next monolith — and the first feature whose failures are public and irreversible (a double-post or wrong-account post can't be ctrl-Z'd). The design below is mostly about containment and safety, not capability.*

**Owns:** `editor/blueprints/publishing.py` (new), `editor/social/**` (new), `posts`/`post_metrics` migrations, publishing UI in campaign templates.
**Parallel:** mostly new files, so safe alongside frontend/test/data specs. Blocked by `schema-migrations.md` (its tables must be migrations). Don't run alongside `core-split.md`. Real posting additionally gated by `test-suite.md` (dry-run until then).

## Shape: a separate domain, joined at Campaign

Publishing is **distribution**; everything existing is **production**. Keep them apart:

- New blueprint `blueprints/publishing.py` + a `social/` package for platform adapters. Campaigns stay the *join point* (a post belongs to a campaign), but no publishing logic lives in `campaigns.py` or `core.py`.
- Existing seeds to build on: `composio_wrapper.py` (OAuth initiate + execute, already keyless-local — tokens live at Composio, which is the right call), the `jobs` table with boot-time `reconcile_orphaned_jobs()`, and campaign chat/`suggest_content` for the recommendations end.

## Data model (via migrations, not ad-hoc CREATE TABLE)

```
posts
  id, campaign_id → campaigns, edit_id → edits (nullable: text-only posts)
  platform TEXT            -- 'instagram' | 'tiktok' | ...
  account_ref TEXT         -- Composio connected-account id (never raw tokens)
  caption TEXT, hashtags TEXT
  media_path TEXT          -- the exported file at schedule time (exports are re-runnable)
  scheduled_at TEXT        -- null = draft / post-now
  status TEXT              -- draft → scheduled → claimed → publishing → published | failed | cancelled
  claimed_at TEXT, published_at TEXT
  external_id TEXT         -- platform post id, set on success
  error TEXT
  idempotency_key TEXT UNIQUE   -- see safety below

post_metrics                -- append-only time series
  id, post_id → posts, fetched_at TEXT
  impressions INT, reach INT, likes INT, comments INT, shares INT, saves INT
  raw TEXT                  -- full platform payload, JSON
```

Recommendations need no new table initially: they're derived (metrics + clip catalog) and delivered through campaign chat / `suggest_content`, whose messages are already persisted.

## Scheduler: DB-driven, single loop, idempotent

- One scheduler thread (started like the embed worker), polling every ~30s: `SELECT … WHERE status='scheduled' AND scheduled_at <= now`. **The DB is the schedule** — nothing fires from memory, so restarts lose nothing.
- Claim before work: atomically flip `scheduled → claimed` (single UPDATE … WHERE status='scheduled'); only the winner proceeds. This is what prevents double-posting if two processes ever run.
- Publish runs as a normal job (jobs table → progress visible in the existing UI), transitioning `claimed → publishing → published/failed`. `reconcile_orphaned_jobs()` at boot marks interrupted `publishing` rows for manual review — **never auto-retry a publish that may have gone out**; check `external_id` first.
- `idempotency_key` = `post:{id}:{scheduled_at}`; adapters must treat a repeat execution of the same key as a no-op.

## Adapters: one tiny interface, Composio behind it

```python
class SocialAdapter(Protocol):
    def publish(self, post) -> str: ...        # returns external_id
    def fetch_metrics(self, post) -> dict: ...
    def verify_connection(self) -> bool: ...
```

One module per platform in `social/` (`instagram.py`, `tiktok.py`, …), each wrapping the relevant Composio action slugs. All Composio calls stay inside adapters — if Composio's API changes or gets replaced, only `social/` is touched. Platform quirks (video specs, caption limits, rate limits) live in the adapter, surfaced as pre-publish validation ("caption too long for X", "video exceeds 90s for Reels") — same fail-loud philosophy as the export pre-flight.

## Analytics → recommendations loop

- A recurring metrics job (manual button first; scheduled later) calls `fetch_metrics` per published post, appending to `post_metrics`. Append-only: trends need history.
- Summarize per campaign (top posts, format/length/subject patterns) and inject that summary into the campaign-chat context and `suggest_content` prompt. That closes the loop: *"caterpillar macros outperform machinery shots 3:1 — film more of X"* becomes a grounded recommendation, not a vibe.
- Keep the summarizer a pure function over `post_metrics` — easy to test, no live API calls.

## Safety rails (non-negotiable, in build order)

1. **`SOCIAL_DRY_RUN=1` is the default** until the test suite from `ROADMAP.md` Priority 1 exists. Dry-run walks the entire pipeline — schedule, claim, job, state machine — and logs the exact payload instead of calling Composio. The state machine gets tested for weeks before a real post ever fires.
2. **Explicit confirmation to arm**: turning dry-run off is a visible per-campaign setting, plus a confirm dialog on the first real publish per platform showing the exact account name.
3. Publish failures land on the campaign UI loudly (post card turns red with the error), never only in a log.
4. No deletes of published content from the app (platform deletion is out of scope; link out to the platform instead).

## Build phases — ship each before starting the next

- **A. Connect + post-now (dry run):** account connect UI per campaign (OAuth via existing wrapper), post composer on a cut card (caption + platform), full pipeline in dry-run. *Ships: the whole state machine, zero risk.*
- **B. Scheduling:** `scheduled_at` picker, scheduler loop, calendar-ish list under the campaign. Still dry-run capable.
- **C. First real platform end-to-end** (pick one — Instagram Reels probably) with the arm/confirm flow. Prove it with a private/test account.
- **D. Metrics ingestion** for published posts + campaign summary.
- **E. Recommendations** wired into campaign chat / suggest-content.

Resist building B–E speculatively for platforms that aren't connected yet; each platform earns its adapter when it's actually used.
