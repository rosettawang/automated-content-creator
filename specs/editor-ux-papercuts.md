# Spec: Editor/library UX papercuts

**Owns:** `editor/static/**` (css/js), `editor/templates/**`. Frontend only — no Python.
**Parallel:** safe alongside ALL backend specs, including `core-split.md`. The one shared-file risk is other sessions touching `static/app.js`/`studio.js` — check `git status` first per CLAUDE.md.

## Items (from review rounds 1–2)
1. ~~**Settings popover clips off-screen**~~ ✅ Done — the popover measures on open and flips to left-anchored if its left edge would go off-screen (`app.js` settings-gear `setOpen`). Gear now sits far-right so it opens leftward; clamp is defensive for narrow widths.
2. ~~**Campaign auto-suggest on generate**~~ ✅ Done — generating with no campaign selected does a client-side keyword match of the prompt against campaign name/description and shows a dismissible "Assign to '…'? [Yes] [No]" banner (`suggestCampaignForEdit` in `app.js`). Never auto-assigns.
3. **Cuts tab polish** — ✅ newest-first sort + aspect badge (9:16 etc.) done (`cuts.js`). ⏳ **Deferred:** export status + "Open folder" — both need backend support (no per-cut export record exists yet; "Open folder" only works in the desktop/pywebview app, not the browser). Split these into a follow-up paired with backend work; out of scope for a frontend-only spec.
4. ~~**Program monitor idle state**~~ ✅ Done — `updateIdlePoster()` sets the program `<video>` poster to the first local clip's thumbnail on load, so idle reads as ready, not broken.
5. ~~**Non-local styling coherence**~~ ✅ Done — provenance shipped; timeline badge + Re-download button + source-list tooltip now use unified copy naming the Re-download / import fix.

## Remaining
Only item 3's **export status + Open folder** (backend-dependent). Everything else shipped 2026-07-13.

## Acceptance
Each item verified in Chrome at 1280px and narrow (~900px) widths; no console errors; screenshots attached to the shipping commit message or PR description.
