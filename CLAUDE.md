# Conventions for this repo

Most code here is written by Claude sessions, sometimes several in parallel. These rules keep that sustainable.

## Documentation policy — three living docs, everything else has a lifecycle

- **Living (must always be true):** `README.md` (how to run/use), `ROADMAP.md` (priorities + status; the single intake point for new ideas), and this file. If a commit changes how the app runs or is used, update `README.md` **in the same commit**.
- **Specs live in `specs/`, one file per unbuilt feature.** As many as needed. A spec's lifecycle ends when the feature ships: **delete the spec file** in the shipping commit and strike its line in `ROADMAP.md`. Git history is the archive. Never "update" a shipped spec.
- **`docs/archive/` is frozen history** (old plans, review logs). Never edit archived files; never treat them as current documentation.
- Don't create new top-level .md files. New idea → line in `ROADMAP.md`; new design → file in `specs/`.

## Code conventions

- `editor/app.py` is a thin entrypoint; routes live in `editor/blueprints/` and must stay thin (no business logic in route handlers).
- **Shared logic is split into focused modules — go straight to the right one instead of reading everything.** `core.py` is now just a ~205-line re-export facade (so blueprints' `from core import *` keeps working); it holds no business logic. The real code lives in:
  - `indexing.py` — content-understanding pipeline: vision/whisper/deep-index, things/regions, faces, motion, embeddings, + their job workers (the one big file, ~1k lines)
  - `export.py` — timeline serialization + social export/reframe (Ken Burns, aspect crop)
  - `catalog.py` — AI-facing library view: generation clip pool, moments, clip decoration, campaign membership
  - `ingest.py` — register/dedup a file as a clip (kicks off indexing), unzip, Drive/Photos import jobs
  - `media_files.py` — probe/proxy/quality/frame-sampling; `settings.py` — on-device toggle + remembered Photos albums; `jobs_runtime.py` — durable job registry; `config.py` — env/paths/constants
  - Dependency DAG (never violate): `config`/`db` → `jobs_runtime`/`media_files`/`settings` → `indexing`/`export` → `catalog`/`ingest` → `core`. No module imports `core`; no cycles.
- Adding shared logic: put it in the module that owns that concern (extract a new one if it fits nothing), then re-export it from `core.py` alongside the others. Prefer extracting over growing a file. Target: no module over ~800 lines (`indexing.py` is the known exception — one cohesive pipeline).
- Schema changes go through migrations (see `ROADMAP.md` Priority 2), not ad-hoc `CREATE TABLE`/`ALTER` scattered in code.
- Long work runs as a job (`jobs` table, reconciled at boot) — never inside an HTTP request.
- Fail loudly: any user-facing failure (playback, export, publish) must surface in the UI, never only in a log. Pre-flight checks over post-hoc errors.
- Social publishing (when built): follow `specs/social-publishing.md` strictly — DB-driven scheduling, atomic claims, idempotency keys, `SOCIAL_DRY_RUN=1` default. Never auto-retry a publish that may have gone out.

## Process

- Run the test suite before committing: `./run_tests.sh` (from repo root; ~1s, Flask test client, no running app needed). **No commit without it passing.** Add/adjust a case when you change an endpoint's contract. For anything the suite can't cover (playback, real export, publish), also manually exercise the loop: assemble → chat revision → export.
- Commit small, with a checkpoint commit before any large rename/refactor.
- One writer per file: if another session (or the user) may be editing concurrently, coordinate via ROADMAP or work on a branch. Check `git status` before large edits.
- The app may be running (waitress via `desktop.py`, or `FLASK_DEBUG=1` dev mode) while you edit — remember a running server doesn't pick up Python changes without a restart.
