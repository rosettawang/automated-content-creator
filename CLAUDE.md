# Conventions for this repo

Most code here is written by Claude sessions, sometimes several in parallel. These rules keep that sustainable.

## Documentation policy — three living docs, everything else has a lifecycle

- **Living (must always be true):** `README.md` (how to run/use), `ROADMAP.md` (priorities + status; the single intake point for new ideas), and this file. If a commit changes how the app runs or is used, update `README.md` **in the same commit**.
- **Specs live in `specs/`, one file per unbuilt feature — as `.html`, not `.md`** (owner preference: specs are read rendered in a browser). As many as needed. A spec's lifecycle is **declared, not remembered:** each carries `<meta name="spec-status" content="open|blocked|done|canonical">` in its `<head>`, and `./specs.sh prune` deletes the `done` ones, logs a dated completion note, and strikes them everywhere. Set the status and run the prune in the shipping commit; also strike the line in `ROADMAP.md`. Git history is the archive. Never "update" a shipped spec.
- **Writing a new spec:** copy the `<head>` from any existing file in `specs/`, link the shared stylesheet (`<link rel="stylesheet" href="spec.css">`), and write semantic HTML (`h1/h2`, `p`, `ul/ol`, `table`, `pre><code`, `del` for struck items). Declare its metadata so the machinery can see it — at minimum `spec-status`; add `spec-touches` (files it edits, for conflict detection + ordering), `spec-needs` (slugs that must land first), `spec-blocker` (one line of what remains), `spec-decision` (one per owner choice), `spec-account="yes"` (gated on a login-gated queue item), and `spec-verify` (a shell command that exits 0 when done). Give it a `<p class="lede">` — the prune uses it as the one-line summary. The full table is in the folded **Conventions reference** at the bottom of `specs/index.html`.
- **`specs/index.html` is the specs dashboard — the fourth living doc, and mostly generated.** `./specs.sh prune` rewrites the **Live specs** list, the **Decisions waiting** section, the **Recently completed** log, the mirrored **spec-pass** block, and (chained via `plan --write`) the dependency-safe **Execution order** — all from spec metadata, between `GENERATED:*` markers. **Never hand-edit inside those markers or the generated lists.** Three sections *are* hand-maintained: the **Login-gated queue** (steps needing the owner signed in — add a row when a spec hits one, and keep building what's automatable), the **Bottlenecks**, and the **Reconciliations**. The owner clears the queue in batches via the Chrome extension: Claude drives each tab to the last safe step; the owner signs in and clicks the final button (Claude never enters credentials or clicks an irreversible publish/authorize).
- **Every spec run ends with its outcome recorded — no exceptions.** Per spec: (1) a step needs the owner signed in → **Login-gated queue** row + `spec-account="yes"`; (2) a choice only the owner can make → a `spec-decision` meta (recommended default first, whether it blocks — never silently pick and bury it in a commit); (3) built behavior deviates from the spec text → a **Reconciliations** row AND an edit to the owning spec's HTML in the same commit so the spec reads true. Then `./specs.sh prune` to regenerate. A multi-spec run (`/spec-run`) also appends per-spec entries to `specs/RUN-LOG.html` as it goes and reports from that file at the end. A run that ends without recording is an unfinished run.
- **`docs/archive/` is frozen history** (old plans, review logs). Never edit archived files; never treat them as current documentation.
- Don't create new top-level .md files. New idea → line in `ROADMAP.md`; new design → `.html` file in `specs/`.

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
- **Naming: "edit" (code) = "cut" (UI) — this is intentional, don't "fix" it.** An assembled timeline is the `edits` table / `edit_id` FKs / `/api/edits*` routes / `currentEditId` in JS, but is shown to the user as a **"cut"** (the Cuts view, "cut" copy). Stable internal name + friendly display name is deliberate — the same object, two audiences. Do NOT rename one to match the other on sight: unifying would mean a table+FK migration, ~16 route renames, six JS files, and every test hitting `/api/edits`, for zero user value. If a full rename is ever actually wanted, it's a spec (project→campaign-sized), not a drive-by.
- Long work runs as a job (`jobs` table, reconciled at boot) — never inside an HTTP request.
- Fail loudly: any user-facing failure (playback, export, publish) must surface in the UI, never only in a log. Pre-flight checks over post-hoc errors.
- Social publishing (when built): follow `specs/social-publishing.html` strictly — DB-driven scheduling, atomic claims, idempotency keys, `SOCIAL_DRY_RUN=1` default. Never auto-retry a publish that may have gone out.

## Process

- Run the test suite before committing: `./run_tests.sh` (from repo root; ~1s, Flask test client, no running app needed). **No commit without it passing.** Add/adjust a case when you change an endpoint's contract. For anything the suite can't cover (playback, real export, publish), also manually exercise the loop: assemble → chat revision → export.
- Commit small, with a checkpoint commit before any large rename/refactor.
- One writer per file: if another session (or the user) may be editing concurrently, coordinate via ROADMAP or work on a branch. Check `git status` before large edits.
- The app may be running (waitress via `desktop.py`, or `FLASK_DEBUG=1` dev mode) while you edit — remember a running server doesn't pick up Python changes without a restart.
- **Never leave the app server running from a session's sandboxed shell.** A server launched there inherits the sandbox's network allowlist — Drive/Photos imports then fail with `PermissionError(1, 'Operation not permitted')` on every connection. If a restart is needed, ask the user to relaunch from their own Terminal.

## Spec tooling — `./specs.sh`

The spec machinery is `scripts/*.mjs` (stdlib-only Node, zero dependencies) behind one entry point, `./specs.sh`, sitting next to `./run_tests.sh`. Three commands:

- `./specs.sh check` — dry run: prints what would be pruned and the derived order, writes nothing. Look before anything moves.
- `./specs.sh prune` — deletes `done` specs, regenerates every `GENERATED:*` region of `specs/index.html`, then redraws the execution order. **One command; never run plan separately after pruning** — that leaves the order linking to a deleted file.
- `./specs.sh plan` — prints the order without pruning (`--verify` also runs each `spec-verify`). Use after editing a spec's `spec-touches` without pruning anything.

**No `package.json`, deliberately.** This is a Python app; a root Node manifest would mislabel the repo for every tool that sniffs one, and invite an npm dependency into the docs tooling. The `.mjs` extension gives ESM on its own, so the manifest bought nothing. (WHR Fund's copy of this tooling *does* use `npm run specs:*` — correctly, because that repo is genuinely Node/Astro and those scripts sit beside `npm run build`. Same principle, opposite answer: match the repo.)

## How specs get worked

**Which procedure a request starts — the two are different and the wrong default has cost real work:**

| The owner says | What runs |
|---|---|
| **"do &lt;spec name&gt;"** — one spec named | The six steps below, on that spec. |
| **"run the specs"**, "work the specs", "do the specs", "run the spec pass", "spec run", or any plural/unnamed phrasing | **The whole queue, continuously — load `.claude/commands/spec-run.md` and follow it.** Every spec that can be worked without a human, one after another, ending only when each has shipped or carries a recorded `spec-blocker`. |

A plural or unnamed request means the queue; if ambiguous, it means the queue — that wastes nothing. The steps below still govern each individual spec inside a run.

<!-- SPEC-PASS:START -->
**Spec pass — how specs get worked, by default.** No further prompting should ever be needed: the session runs the whole thing without asking what to do next.

1. **Reassess the spec first.** A spec is a starting point, not a contract. Check its claims against the current code, schema, and DB state before building — specs here go stale. Where reality has moved, correct the spec and say what changed.
2. **Sequence it, then work the sequence end to end.** Don't stop after one item to report progress, and don't ask which item to do next.
3. **Use discretion to build it out.** Fill gaps the spec left, fix what it got wrong, improve on it where the better design is clear. Note the deviations.
4. **Stop only at a blocker or a decision** — credentials, an account, the owner's confirming publish click, content only the owner can write, or a choice that is structural and hard to reverse. Not on mere uncertainty: if a call is cheap and reversible, make it, note it, keep going. "Stop" means stop that spec, never the session — in a run, record it and pick up the next spec.
5. **Log the stop** in that spec's `spec-blocker` meta, then run `./specs.sh prune` so the index reflects it. That one command also regenerates the execution order.
6. **Declare each decision separately**, one `spec-decision` meta per choice, stating the recommended answer and whether it blocks. The prune collects them into the index's Decisions section. Burying a decision in blocker prose leaves it unfindable.

Verify as you go rather than at the end. The floor is `./run_tests.sh` green (no commit without it), but a suite passing is not a feature working — prefer exercising the real loop: assemble, chat revision, export.
<!-- SPEC-PASS:END -->
