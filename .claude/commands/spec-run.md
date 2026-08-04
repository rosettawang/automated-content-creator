---
description: Work multiple specs end to end in one long session — sequenced, committed per spec, logged to disk, reported at the end
---

# Spec run

Work the whole queue in one session. Not one spec: as many as the session can
finish, in a derived order, stopping for nothing that can be recorded and
skipped.

**You are here whenever the owner asked for the specs in the plural or without
naming one** — "run the specs", "work the specs", "do the specs", "run the spec
pass" — not only when `/spec-run` was typed. `CLAUDE.md` routes those phrasings
here. The spec-pass rule in `CLAUDE.md` governs each *individual* spec; this adds
the four things a multi-spec run needs on top: a derived order, a commit
boundary, a durable log, and a batched report.

---

## Run until exhausted. Exhaustion is a defined state, not a feeling.

**The run ends when every spec in the derived order has either shipped or has a
recorded `spec-blocker` naming the specific human, credential, or tool it waits
on.** Nothing else ends it. Check the list, not your sense of having done a lot.

These are **not** stopping conditions, and each has ended a run early before:

| Not a reason to stop | What to do instead |
|---|---|
| A natural-feeling boundary after several specs | Take the next spec off the list |
| Wanting to report progress | Write it to the run log. Reports are terminal only |
| Another session holding a file | See the contention rule below |
| Being unsure whether something is in scope | Cheap and reversible? Do it, note it |
| Running low on context | Write the log, keep going. See below |

**Do not stop to report.** A mid-run summary reads like completion and ends the
run in practice. One report, at the end, generated from the log.

**Ending your turn IS stopping.** If the next action is known, take it in this
turn. Never end a turn on a question the run already answers ("shall I
continue?" — yes, that is what a run is). The log exists so a session that is
*killed* can resume, not so a running one can excuse itself.

## Standing authority — never ask for these

Asking for a spec pass grants all of it for the whole run:

- **Commit after every spec, once `./run_tests.sh` passes.** Small commits, one
  per spec, with a message naming what shipped.
- **Run `./specs.sh prune`** after every spec that changes status, and again
  at the end. It chains `plan --write`, so one command reprunes and redraws the
  order together. Skipping it leaves the index claiming shipped work is
  outstanding, and everything it writes regenerates — a bad write costs one
  command.
- **Edit any non-app-runtime file** — `specs/`, `CLAUDE.md`, `README.md`,
  `ROADMAP.md`, `scripts/`. Correcting a spec that is wrong is step 1, not a
  deviation.

**Do not push, and do not touch `main`.** This is a local desktop app on a
feature branch (`git branch --show-current`). Commits stay local unless the
owner asks to push. If HEAD is somehow on `main`, branch first — per `CLAUDE.md`.
Still requires asking, every time: **any real publish/post** (social publishing
runs `SOCIAL_DRY_RUN=1` by default and never auto-retries a publish), anything
that spends money, a destructive DB statement, and outbound mail.

## Contention is not a blocker

Only one kind matters: **another agent editing the same source file you are about
to change.** Uncommitted changes to `editor/` you did not make mean a second
agent is live in this checkout — say so and stop. Everything else (a doc open in
an editor, uncommitted work in `specs/`) is noise; stage deliberately instead of
`-A`, and **if a write is refused, re-run it — a refusal is not permission to
skip the step.** Do not change branches while anyone else is working here: moving
HEAD moves it for the whole checkout.

## Running low on context is not the end of the run

Append the current spec's entry to `specs/RUN-LOG.html`, run the prune so the
index is truthful, commit, and **keep going**. If the session genuinely ends, the
next one resumes from `./specs.sh plan` plus the log with nothing lost — that
is the whole point of writing them as you go.

---

## Before touching anything

**1. Work out who else is in the tree.**

```bash
git status --porcelain
git branch --show-current
```

The owner editing `specs/` or any doc is fine — do not stop. Another Claude
session editing `editor/` is the one to stop for.

**2. Derive the order. Do not read it off the index.**

```bash
./specs.sh plan --verify
```

This prints the order, the contended files, and any spec whose `spec-verify`
already passes. **Resolve every `X` line before starting** — an `X` is a spec
deleting a file another spec edits. A spec whose verify already passes is
**done**: confirm and prune it, do not work it.

---

## Per spec, in the derived order

1. **Reassess before building.** A spec is a starting point, not a contract.
   Check its claims against the current code, schema, and DB state — specs go
   stale. Where reality has moved, correct the spec and say what changed.
2. **Work it** per the spec-pass rule in `CLAUDE.md`: sequence it, use discretion
   to fill gaps and fix what it got wrong, and prefer exercising the real thing
   over asserting it works.
3. **Verify for real. Both, before committing.**

   ```bash
   ./run_tests.sh
   ```

   The suite is the floor, not the ceiling. **No commit without it passing.** A
   suite passing is not a feature working: for anything the tests can't cover
   (playback, real export, publish), exercise the real loop — assemble → chat
   revision → export — as `CLAUDE.md` requires. Add or adjust a test case when
   you change an endpoint's contract. If the spec declares a `spec-verify`, it
   must now pass.
4. **Commit. Every spec, small, no asking. Do not push.**

   ```bash
   git add -A && git commit -m "<slug>: <what shipped>"
   ```

   Stage deliberately if anyone else has work in the tree — `-A` sweeps up their
   files too.
5. **Append to `specs/RUN-LOG.html`** — what shipped, how it was verified (the
   command, the loop you exercised — not "verified"), what was deviated from,
   what is left.
6. **Prune:** set the spec's `spec-status` to `done` (the prune deletes it), or
   record what stopped it in `spec-blocker`, then `./specs.sh prune`.

---

## When a spec blocks, keep going

A single spec pass stops at a blocker. **A run does not.** Record it and move on:

- Write what stopped it into that spec's `<meta name="spec-blocker">`.
- If it is an account chore — a password, an OAuth grant, accepting terms, a
  secret's *value*, a 2FA code, the owner's confirming publish click — add a row
  to the **Login-gated queue** in `specs/index.html` and set
  `<meta name="spec-account" content="yes">`.
- If it is a choice only the owner can make, add a `<meta name="spec-decision">`
  (recommendation first, whether it blocks). The prune surfaces it in
  **Decisions**.
- Move to the next spec.

Six blockers presented together are one conversation; six one at a time are six
interruptions.

**Do stop the whole run for:** a destructive DB statement, anything that
publishes/posts/emails outward, a structural decision expensive to reverse, or a
planner `X` conflict you cannot resolve. Ask, then continue.

---

## Reporting back

At the end, after a final `./specs.sh prune`:

1. **Re-read `specs/RUN-LOG.html`.** Report from it, not from the conversation —
   the early specs have been summarized out of context by now.
2. Lead with **what shipped**, spec by spec, and **how each was verified** — name
   which claims rest on a real exercise and which rest only on the suite passing.
   "Built" and "works" are different words.
3. Then, in one batch: **blockers**, **decisions waiting on the owner**, and the
   **Login-gated queue**. This batch is the point of the run.
4. Then what was **not** verified, and could not be.

Nothing was pushed — the commits are local on the branch. Say so, and let the
owner decide when to push.
