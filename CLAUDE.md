# Conventions for this repo

Most code here is written by Claude sessions, sometimes several in parallel. These rules keep that sustainable.

## Documentation policy — three living docs, everything else has a lifecycle

- **Living (must always be true):** `README.md` (how to run/use), `ROADMAP.md` (priorities + status; the single intake point for new ideas), and this file. If a commit changes how the app runs or is used, update `README.md` **in the same commit**.
- **Specs live in `specs/`, one file per unbuilt feature.** As many as needed. A spec's lifecycle ends when the feature ships: **delete the spec file** in the shipping commit and strike its line in `ROADMAP.md`. Git history is the archive. Never "update" a shipped spec.
- **`docs/archive/` is frozen history** (old plans, review logs). Never edit archived files; never treat them as current documentation.
- Don't create new top-level .md files. New idea → line in `ROADMAP.md`; new design → file in `specs/`.

## Code conventions

- `editor/app.py` is a thin entrypoint; routes live in `editor/blueprints/` and must stay thin (no business logic in route handlers). Shared logic lives in `editor/core.py` — which is over budget; when touching it, prefer extracting a module over adding to it. Target: no module over ~800 lines.
- Schema changes go through migrations (see `ROADMAP.md` Priority 2), not ad-hoc `CREATE TABLE`/`ALTER` scattered in code.
- Long work runs as a job (`jobs` table, reconciled at boot) — never inside an HTTP request.
- Fail loudly: any user-facing failure (playback, export, publish) must surface in the UI, never only in a log. Pre-flight checks over post-hoc errors.
- Social publishing (when built): follow `specs/social-publishing.md` strictly — DB-driven scheduling, atomic claims, idempotency keys, `SOCIAL_DRY_RUN=1` default. Never auto-retry a publish that may have gone out.

## Process

- Run the test suite before committing: `./run_tests.sh` (from repo root; ~1s, Flask test client, no running app needed). **No commit without it passing.** Add/adjust a case when you change an endpoint's contract. For anything the suite can't cover (playback, real export, publish), also manually exercise the loop: assemble → chat revision → export.
- Commit small, with a checkpoint commit before any large rename/refactor.
- One writer per file: if another session (or the user) may be editing concurrently, coordinate via ROADMAP or work on a branch. Check `git status` before large edits.
- The app may be running (waitress via `desktop.py`, or `FLASK_DEBUG=1` dev mode) while you edit — remember a running server doesn't pick up Python changes without a restart.
