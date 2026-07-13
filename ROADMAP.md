# Roadmap — keeping this project manageable as it grows

*Written 2026-07-13, after two full end-to-end test rounds (see `docs/archive/app-review-notes.md`). Ordered by what protects the project's ability to keep moving, not by what's most exciting. Each phase assumes the previous one. Doc policy: see `CLAUDE.md` — specs live in `specs/` and are deleted when shipped.*

## Where the project stands

The core loop works: prompt → moment-aware rough cut → chat revision → region-aware vertical export. The backend just went through a major structural upgrade (blueprints, jobs table, waitress, single `/studio` shell, project→campaign rename). Development velocity is very high — multiple large refactors per day, sometimes from parallel Claude sessions — and that velocity is now the biggest risk: there are **no tests**, docs lag the code, and `core.py` (~2,500 lines) is quietly becoming the next monolith.

## Priority 1 — Safety net ✅ Shipped 2026-07-13

The project can no longer verify itself by hand; every refactor bets days of work on nothing silently breaking.

1. ~~**API smoke-test suite.**~~ ✅ Done — 21 pytest cases in `editor/tests/` (clips list/search, media MIME + Range + 404, edit CRUD + reorder/trim, generation with mocked model + ghost exclusion, chat snapshot/undo, export pre-flight 409 + 9:16 job success, job reconciliation, xlsx import). Runs in ~1s against the Flask test client.
2. ~~**One-command check.**~~ ✅ Done — `./run_tests.sh` (repo root); `no commit without it passing` is now a rule in `CLAUDE.md`.
3. ~~**Fixture discipline.**~~ ✅ Done — fixtures generate tiny clips with ffmpeg at test time (nothing committed, never touches the real `media/` folder); a catalog-only "ghost" row covers the not-local path.

## Priority 2 — Keep the structure honest

The blueprint split fixed `app.py`; don't let its problem respawn elsewhere.

4. **Split `core.py`** along the seams that already exist in its section comments: `db/`, `media` (probe/ffmpeg/proxies), `indexing` (vision/whisper/regions), `jobs`, `catalog` (AI pool + formatting). Rule of thumb: no module over ~800 lines, no business logic in blueprint routes.
5. ~~**Schema migrations.**~~ ✅ Shipped. `editor/migrations/NNN_*.sql` + a `schema_migrations` table; `db.py`'s `init_db()` applies pending migrations (baseline = `001`, adopts existing DBs cleanly, refuses a future-version DB). Round-trip test in `tests/test_migrations.py`. Remaining `core.py` `_ensure_*` cleanup carried into `specs/core-split.md`.
6. **Docs follow code in the same commit.** README still describes the pre-refactor world in places (run instructions, window layout). Make "update README/CLAUDE.md" part of any commit that changes how the app is run or navigated. A short `CLAUDE.md` at repo root telling agent sessions the conventions (run tests, thin routes, migrations, commit style) pays for itself immediately given how much of this codebase is agent-written.

## Priority 3 — The two designed features (spec: `specs/framing-and-provenance.md`)

7. **Provenance + re-download** (smaller, do first): `source_kind`/`source_url` on clips, written at import, backfilled coarsely; "⚠ not local" becomes a Re-download action. This is what lets the library grow past what fits on disk — the whole premise of metadata-as-index.
8. **Framing v2** (bigger, biggest quality win): time-sampled regions with a primary-subject flag; fill `crop_*`/`kb_*` per timeline item at assemble time from regions inside the item's own in/out range; crop overlay on by default when aspect ≠ source; vision spot-check of exported frames. Quick win available immediately: center on the primary region instead of the union box.

## Priority 3.5 — Social publishing (spec: `specs/social-publishing.md`)

Scheduled posts, analytics, and recommendations under Campaigns, via Composio. Explicitly designed as its own bounded domain (`blueprints/publishing.py` + `social/` adapters + `posts`/`post_metrics` tables) so it can't become the next monolith. Note the dependency: **the Priority 1 test suite gates real posting** — publishing failures are public and irreversible, so the spec keeps everything in dry-run mode until the safety net exists. Build phases A–E in the spec; ship each before starting the next.

## Priority 4 — Papercuts (opportunistic, none blocking)

9. Aspect from prompt wording ("vertical" → 9:16 on the edit).
10. Auto-suggest campaign assignment for generated cuts.
11. Settings popover clips off the left window edge.
12. Data hygiene: CORRECTION-notes out of descriptions; photos flagged as stills, not 0.3s "videos".
13. Bitrate/length presets per destination (Reels vs Stories vs feed).

## Parallel work matrix

Every spec in `specs/` declares which files it owns; two sessions may run concurrently iff their specs own disjoint files. Summary:

| Spec | Can run alongside | Must NOT run alongside | Blocked by |
|---|---|---|---|
| ~~`test-suite`~~ (shipped) | everything | — | — |
| ~~`schema-migrations`~~ (shipped) | — | — | — |
| `core-split` | frontend-only, tests | **all other backend specs** | test-suite (strongly advised) |
| `aspect-from-prompt` | frontend, tests, data-hygiene | core-split | — |
| `editor-ux-papercuts` | all backend specs | — | — |
| `data-hygiene` | code specs (it edits data) | — | — |
| `framing-and-provenance` | frontend, tests | core-split, its own other half | — (schema-migrations shipped) |
| `social-publishing` | frontend, tests, data-hygiene | core-split | test-suite gates real posting |

A good next split: `test-suite` and `schema-migrations` are both shipped, so `core-split` is unblocked — run it **alone** (it touches everyone's imports, and now also removes the redundant `_ensure_*` per its carryover note), then the feature specs.

## Process guardrails (cheap, start now)

- **One writer per file at a time.** Two sessions edited concurrently this week; it worked, but only by luck and disjoint hunks. Either serialize sessions or give each a branch.
- **Commit small and often** — the recent history is good; keep checkpoint commits before big renames.
- **A `BACKLOG.md` or issues list** as the single intake point, so ideas land there instead of expanding whatever file is currently open. This file is the current version of that list.

## What NOT to do yet

Multi-user/hosting, auth, remote MCP, a build system for the front-end, or swapping SQLite — all premature. The single-user local architecture is a feature: it keeps iteration this fast. Revisit only if a second real user appears.
