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

## Stage 4 — editor overrides + chat framing context

**Problem.** Framing is now decided and stored automatically, but the human can't see or
correct it, and the edit-chat can't reason about it.

- **Crop overlay on by default** (`crop.js`) when the edit's aspect ≠ source: draw the
  stored `crop_*` (and, if set, the `kb_*` end rect) over the player. Dragging updates the
  item's `crop_*`/`kb_*` via the existing crop/ken-burns endpoints.
- **Mark human overrides as sticky.** Auto-framing (`_apply_auto_framing`) currently fills
  only NULL-crop items and `reset=True` (aspect change) clears everything — which would wipe
  a human drag. Add a way to distinguish a manual crop from an auto one (e.g. a
  `timeline_items.crop_source TEXT` = 'auto' | 'manual', or a boolean) so reset/reframe
  preserves manual crops and only recomputes auto ones. This is the missing piece that makes
  overrides durable across an aspect change.
- **Edit chat framing context.** Include each item's regions + current `crop_*`/`kb_*` in the
  revision prompt so "keep the oil bowl centered" updates framing, not just clip choice. Have
  `revise_edit` optionally return per-item crop adjustments, applied like `_apply_auto_framing`
  but honoring the instruction.

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
