# Spec: Framing v2 — remaining work (editor overrides + export verification)

*The provenance/re-download half of the original spec shipped (source_kind/source_url,
`POST /api/clips/<id>/pull`, Re-download UI). Framing v2 Stages 1–3 also shipped — see
git history for the full original spec. What remains below is the unbuilt tail of framing.*

**Shipped already (context for what's below):**
- `clip_regions` carries `t_frame` + `is_primary` (migration `002`); the deep-index pass
  returns regions per scene segment and `_store_segment_regions` persists them time-stamped.
- `_auto_crop_from_regions` centers on the primary region (not the union).
- `_apply_auto_framing` fills `crop_*`/`kb_*` per timeline item at assemble time from boxes
  inside the item's own `[in,out]` range (nearest-in → crop, nearest-out → kb → the zoompan
  export path pans with the subject); wired into generate/append, revise, and aspect-change.

**Owns:** `crop.js` overlay behavior; edit-chat context in `blueprints/edits.py` +
`claude_client.py`; the export verification step in `_run_export_job` (`export.py`).
**Parallel:** frontend-heavy; safe alongside backend specs that don't touch export/edits.

## Stage 4 — editor overrides + chat framing context ✅ Shipped 2026-07-13

**Problem.** Framing is now decided and stored automatically, but the human can't see or
correct it, and the edit-chat can't reason about it.

- ~~**Crop overlay on by default**~~ ✅ Done — `crop.js` `refreshCropOverlay()` shows the
  overlay whenever the edit's aspect ≠ source, draws the stored `crop_*`/`kb_*`, and dragging
  PUTs the new rect to the item.
- ~~**Mark human overrides as sticky.**~~ ✅ Done (2026-07-13) — `timeline_items.crop_source`
  (migration `003`) = 'auto' | 'manual' | NULL. A crop/kb write from the client tags the item
  `manual`; `_apply_auto_framing` reset now preserves `manual` items and only recomputes autos
  (tagging its own fills `auto`); Reset-to-auto (crop_x=null) clears the flag. Covered by
  `tests/test_framing_overrides.py`.
- ~~**Edit chat framing context.**~~ ✅ Done (2026-07-13) — `chat_edit` passes each item's
  subject regions + current crop center + the output aspect into `revise_edit`, which returns
  `crops` (per-item center points) when the instruction is about framing. `_apply_framing_edits`
  turns each into an exact aspect-correct, sticky (`manual`) crop window, applied after
  auto-framing. Verified against the real model ("keep the machine centered" → a crop centered
  on the oil-press region) and in `tests/test_framing_overrides.py`.

## Stage 5 — export frame-check (self-correcting framing)

**Problem.** Framing is best-effort; nothing verifies the subject actually landed in frame.

- After the export renders, extract one frame per segment from the finished file and run a
  cheap vision check ("is `<primary subject>` fully in frame?"). This is a **recurring
  per-export API cost** — gate it behind a setting (default off, matching the on-device
  preference) or run it on-device if a detector is available.
- Flag failures on the job result in the UI, with a one-click "widen window and re-export
  segment N" retry. Makes framing self-correcting instead of fire-and-forget.

**Acceptance test.** The nut-oil reel prompt from test round 2, re-exported at 9:16, keeps
the cracker's output chute, the pour, and the oil bowl centered through all four shots,
verified by the frame-check passing.
