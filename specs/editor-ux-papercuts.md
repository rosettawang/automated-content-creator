# Spec: Editor/library UX papercuts

**Owns:** `editor/static/**` (css/js), `editor/templates/**`. Frontend only — no Python.
**Parallel:** safe alongside ALL backend specs, including `core-split.md`. The one shared-file risk is other sessions touching `static/app.js`/`studio.js` — check `git status` first per CLAUDE.md.

## Items (from review rounds 1–2)
1. **Settings popover clips off-screen** — `#settings-popover` is right-anchored to a control near the window's left edge in some layouts; it renders past the left edge. Flip anchoring (left: 0 when the trigger sits in the left half) or clamp with `max(0, …)`; verify in both the standalone editor and `/studio` shell.
2. **Campaign auto-suggest on generate** — when a cut is generated with no campaign selected, suggest the best-matching campaign (simple: the campaign whose description/things best match the prompt — backend already exposes campaigns; a dumb keyword match is fine v1) as a dismissible banner on the new edit: "Assign to 'WHRF'? [Yes] [No]". No silent auto-assign.
3. **Cuts tab polish** — sort newest-first, show aspect badge (9:16 etc.) and export status on each card; "Open folder" link on exported cuts.
4. **Program monitor idle state** — before first play, show the first frame (poster via existing thumbnail endpoint) instead of a black box.
5. **Non-local styling coherence** — timeline badge (red hatch), library dim, and source-list "not local" all exist; unify tooltip copy to mention the future Re-download action once provenance ships.

## Acceptance
Each item verified in Chrome at 1280px and narrow (~900px) widths; no console errors; screenshots attached to the shipping commit message or PR description.
