# Spec: Split core.py into domain modules

**Owns:** `editor/core.py` (shrinks), new `editor/` modules, import lines in `editor/blueprints/*.py` and `editor/app.py`.
**Parallel: EXCLUSIVE.** This touches the imports of everything; run it alone — no other backend spec in flight, commit checkpoint before starting (see CLAUDE.md). Frontend-only specs are fine concurrently.

## Goal
`core.py` (~2,500 lines) is the new monolith gravity well — same disease `app.py` had before the blueprint split. Split along the seams that already exist in its section comments before it hits 5k lines.

## Target layout (follow existing seams, don't redesign)
- `editor/db_access.py` — get_conn, init_db glue (or fold into migrations work if that landed first)
- `editor/media_files.py` — find_media_file, probes (fps/dims/audio), proxies, reframe filter builders
- `editor/indexing.py` — vision/whisper/deep-index/regions pipeline + its worker threads
- `editor/jobs_runtime.py` — job registry helpers, reconcile_orphaned_jobs, scheduler-style loops
- `editor/catalog.py` — `_pool_for_generation`, `_format_clip_catalog`, moments assembly (the AI-facing view of the library)
- `core.py` keeps only config/env/shared constants until it can be deleted.

## Carryover from schema-migrations (shipped)
`db.py` now owns the migration chain (`migrations/NNN_*.sql` + `schema_migrations`); the baseline creates every table/column. Three import-time shims in `core.py` are therefore redundant and should be removed during this split — but carefully, because of a startup-ordering coupling:
- `_ensure_source_columns()` (import-time) runs immediately before `_backfill_clip_sources()`, which writes `clips.source_kind`. `core` is imported *before* `init_db()` runs, so on an old DB's first launch the ensure-call is what guarantees the column exists in time. Removing it means moving `_backfill_clip_sources()` to run after `init_db()`.
- `_ensure_thing_thumbs_table()` / `_ensure_clip_regions_table()` have no such coupling (nothing queries them at import) — safe to drop once the baseline is guaranteed to have run at startup.
- Then drop the now-redundant `core._ensure_clip_regions_table()` call in `conftest.py`.

## Rules
- Pure moves — no behavior changes in the same commit. One module per commit, tests green after each (test-suite spec should land first; at minimum exercise assemble → chat → export manually per CLAUDE.md).
- No module over ~800 lines. No circular imports: modules may import `db_access`, never each other's internals — if two need the same helper, it moves down, not sideways.

## Acceptance
`core.py` under 300 lines or deleted; app boots under waitress; full loop works; imports in blueprints reference the new modules directly.
