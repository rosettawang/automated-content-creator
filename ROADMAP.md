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

4. ~~**Split `core.py`**~~ ✅ Shipped. Decomposed along its section seams into `settings.py`, `export.py`, `indexing.py`, `catalog.py`, `ingest.py` (plus the earlier `config.py`/`jobs_runtime.py`/`media_files.py`); `core.py` is now a ~205-line re-export facade so blueprints' `from core import *` is unchanged. Redundant import-time schema shims removed (migrations own the baseline). `indexing.py` is ~1k lines (the one cohesive pipeline the spec kept whole); everything else is under ~800.
5. ~~**Schema migrations.**~~ ✅ Shipped. `editor/migrations/NNN_*.sql` + a `schema_migrations` table; `db.py`'s `init_db()` applies pending migrations (baseline = `001`, adopts existing DBs cleanly, refuses a future-version DB). Round-trip test in `tests/test_migrations.py`. Remaining `core.py` `_ensure_*` cleanup carried into `specs/core-split.md`.
6. **Docs follow code in the same commit.** README still describes the pre-refactor world in places (run instructions, window layout). Make "update README/CLAUDE.md" part of any commit that changes how the app is run or navigated. A short `CLAUDE.md` at repo root telling agent sessions the conventions (run tests, thin routes, migrations, commit style) pays for itself immediately given how much of this codebase is agent-written.

## Priority 3 — The two designed features (spec: `specs/framing-and-provenance.md`)

7. ~~**Provenance + re-download**~~ ✅ Shipped. `source_kind`/`source_url` on clips, written at import + backfilled to 'photos' at startup; `POST /api/clips/<id>/pull` re-downloads from the recorded source; "⚠ not local" is a Re-download action.
8. **Framing v2** — core shipped, tail remains. ✅ Time-aware regions (`clip_regions.t_frame`/`is_primary`, migration `002`; deep-index emits per-segment boxes) + assemble-time subject-tracking reframe (`_apply_auto_framing` fills `crop_*`/`kb_*` per item from boxes in its own in/out range; primary-region centering). ✅ Stage 4 complete: crop overlay on by default, **durable human overrides** (`crop_source`, migration `003` — manual crops survive an aspect change), and **edit-chat framing context** ("keep the bowl centered" → sticky crop). ⏳ Remaining in `specs/framing-and-provenance.md`: only the export frame-check (Stage 5, recurring API cost — gated behind a setting/on-device).

## Priority 3.5 — Social publishing (spec: `specs/social-publishing.md`)

Scheduled posts, analytics, and recommendations under Campaigns, via Composio. Explicitly designed as its own bounded domain (`blueprints/publishing.py` + `social/` adapters + `posts`/`post_metrics` tables) so it can't become the next monolith. Note the dependency: **the Priority 1 test suite gates real posting** — publishing failures are public and irreversible, so the spec keeps everything in dry-run mode until the safety net exists. Build phases A–E in the spec; ship each before starting the next.

**Status (2026-07-17):** ~~social-core (A–B)~~ and ~~social-analytics (D–E)~~ **shipped** — DB-driven scheduler, dry-run harness, campaign hub (KPIs, calendar/list, post-detail, Learn card), metrics ingestion + recommendations. Remaining: **social-adapters (C)** — Instagram adapter is built and dry-run gated but **not yet live-verified** on a real account; that verification (and turning `SOCIAL_DRY_RUN` off per campaign) is the only step left before real posting.

## ~~Priority 3.6 — Audio design for generated edits~~ ✅ SHIPPED

~~Generation currently decides what you see, not what you hear.~~ Shipped all four phases: per-edit `audio_plan` (ambient / speech-led / music / voiceover / clean), user-overridable and chat-changeable. Ambient treatment + beat-/sentence-snapped cuts; local `music/` library with mood match + faded/looped export bed; OpenAI TTS voiceover with an editable script; trending-audio compose (local reference track → beat detection → beat-timed cuts + synced preview, never exported, clean handoff). Deferred polish (git history has the spec): inter-clip audio crossfades, a `-20dB duck original under music` option, and live A/V preview-sync verification.

## Priority 3.7 — MCP server: Claude drives the app (spec: `specs/mcp-server.md`)

A thin-proxy MCP server (`editor/mcp_server.py`) lets Claude import footage, search the library, and assemble edits over the same HTTP endpoints the UI uses — one source of truth, and writes propagate to the editor via its live-refresh poll. **Core shipped 2026-07-17:** stdio + `content-creator-mcp` entry point, auto-start, `import_media`/`search_clips`/`assemble_cut`, portable `.mcp.json`, README install flow. Remaining in the spec: (A) multi-account posting — one Instagram account per campaign (gated on social-adapters C, real posting irreversible); (B) more thin-proxy tools on demand; (C) PyPI packaging for `uvx` clone-free install; (D) remote/hosted MCP — only if a shared multi-user library is ever wanted.

## Priority 4 — Papercuts (opportunistic, none blocking)

9. ~~Aspect from prompt wording ("vertical" → 9:16 on the edit).~~ ✅ Done (`aspect-from-prompt`) — model infers the frame on generate; "make it square" in chat reframes; explicit choices aren't clobbered.
10. ~~Auto-suggest campaign assignment for generated cuts.~~ ✅ Done (dismissible banner, `editor-ux-papercuts`).
11. ~~Settings popover clips off the left window edge.~~ ✅ Done (flip/clamp on open).
12. ~~Data hygiene: CORRECTION-notes out of descriptions; photos flagged as stills, not 0.3s "videos".~~ ✅ Done (`data-hygiene` spec; maintenance checks in `editor/scripts_hygiene.py`).
    - Also from `editor-ux-papercuts`: ✅ program idle poster, Cuts newest-first + aspect badge, non-local tooltip copy. ⏳ still open: Cuts export-status + "Open folder" (needs a backend export record; desktop-only for folder open).
13. Bitrate/length presets per destination (Reels vs Stories vs feed).

## Priority 4.5 — Efficiency & DRY pass ✅ Shipped 2026-07-16

From the 2026-07-13 code sweep. **Part A** ✅ `db_conn()` context manager adopted across every blueprint (no unguarded `get_conn()…close()` leaks under waitress; job workers stay manual for incremental commits); 20 silent `except: pass` swallows now log with context (`logging.basicConfig` wired into both entrypoints). **Part B** ✅ catalog N+1 removed (`_attach_moments` one `IN` query; `_decorate_clips` status O(1) via cache), `media_files._stem_index()` mtime-cached `find_media_file`, export ffprobe memoized + segment-encode cache (re-export of an unchanged edit = cache hits + concat). **Part C** ✅ shared `err()` + JSON errorhandler, `claude_client._parse()`, and `static/common.js` (`api`/`esc`/`pollJob`) prepended to every bundle. Tests: `test_perf.py` asserts `/api/clips` query count doesn't scale with clip count. ✅ Tail done: all raw `fetch()` in library/campaigns/things migrated to `api()`/`pollJob()` (verified panel-by-panel in-browser) — zero `fetch(` left outside `common.js` except the XHR upload-progress path.

## Parallel work matrix

Every spec in `specs/` declares which files it owns; two sessions may run concurrently iff their specs own disjoint files. Summary:

| Spec | Can run alongside | Must NOT run alongside | Blocked by |
|---|---|---|---|
| ~~`test-suite`~~ (shipped) | everything | — | — |
| ~~`schema-migrations`~~ (shipped) | — | — | — |
| ~~`core-split`~~ (shipped) | — | — | — |
| ~~`aspect-from-prompt`~~ (shipped) | frontend, tests, data-hygiene | — | — |
| `editor-ux-papercuts` | all backend specs | — | — |
| `data-hygiene` | code specs (it edits data) | — | — |
| `framing-and-provenance` | frontend, tests | its own other half | — (schema-migrations shipped) |
| `social-publishing` | frontend, tests, data-hygiene | — | test-suite gates real posting |
| `audio-design` | frontend, social-publishing | framing-and-provenance tail (both touch export.py) | sequenced after aspect-from-prompt (shipped) |
| ~~`efficiency-pass`~~ (shipped) | — | — | — |

`test-suite`, `schema-migrations`, `core-split`, and `aspect-from-prompt` are all shipped, so the remaining feature specs (`framing-and-provenance`, `social-publishing`) are unblocked — they no longer collide with an in-flight import-touching refactor.

## Process guardrails (cheap, start now)

- **One writer per file at a time.** Two sessions edited concurrently this week; it worked, but only by luck and disjoint hunks. Either serialize sessions or give each a branch.
- **Commit small and often** — the recent history is good; keep checkpoint commits before big renames.
- **A `BACKLOG.md` or issues list** as the single intake point, so ideas land there instead of expanding whatever file is currently open. This file is the current version of that list.

## What NOT to do yet

Multi-user/hosting, auth, remote MCP, a build system for the front-end, or swapping SQLite — all premature. The single-user local architecture is a feature: it keeps iteration this fast. Revisit only if a second real user appears.
