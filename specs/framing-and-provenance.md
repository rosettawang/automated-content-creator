# Spec: Framing v2 + Provenance/Re-download

*Two designed-but-unbuilt features, extracted from review discussion 2026-07-13. Implementation order: provenance first (smaller), framing second (bigger). See `ROADMAP.md` for where these sit in overall priorities.*

**Owns (provenance):** importer modules (`drive_import.py`, `photos_import.py`, import endpoints), one migration (needs `schema-migrations.md` first), Re-download UI touches in `static/`.
**Owns (framing):** regions pipeline in indexing code, `clip_regions` migration, assemble-time crop fill in the generation endpoints, `crop.js` overlay behavior.
**Parallel:** the two halves of THIS spec conflict with each other (both touch indexing/import backend) — do sequentially. Blocked by `schema-migrations.md`. Don't run alongside `core-split.md`. The framing *quick win* (primary region instead of union in `_auto_crop_from_regions`) is a one-line change any session can take immediately.

## 1. Provenance + re-download

**Problem.** The `clips` table has no record of where any file came from. Importers know the URL at download time and discard it. When a file is missing ("not local"), the only fix is manually hunting it down — the app can report the problem but not solve it.

**Schema.** Add to `clips` (via a migration):
- `source_kind TEXT` — 'photos' | 'drive' | 'zip' | 'local'
- `source_url TEXT` — the share link / album URL / original path

Written at import time by each importer. Backfill existing rows coarsely: everything from the original indexing pass gets the shared Photos album URL; that's enough to make re-download meaningful.

**Behavior.**
- `POST /api/clips/<id>/pull` → routes on `source_kind` to the existing drive/photos import machinery → file lands in `MEDIA_DIR` → availability flips → existing `clip-updated` broadcast refreshes all panels.
- UI: the "⚠ not local" badge (timeline) and "not local" label (library/source list) gain a **Re-download** action calling that endpoint; button shows job progress via the jobs table.
- On `embed` (exiftool), also stamp `source_url` into XMP so provenance travels with the file and survives a library rebuild.

**Caveats.**
- Drive links re-fetch cleanly per file.
- Google Photos album links have no stable per-file URLs: re-download = re-run the album import and match by filename, verified by `content_hash`. Surface "couldn't match" honestly rather than guessing.

## 2. Framing v2 — subject-tracking reframe

**Problem (observed in exported reels: subject not centered).** Three compounding causes:
1. Regions come from **one keyframe per clip** (usually near the start), but cuts now begin deep inside clips — the box describes where the subject *was*, not where it is during the cut.
2. The crop centers on the **union bounding box of all regions**; with several spread-out regions the union ≈ whole frame and its center is background.
3. The crop is a **static window** over handheld/moving footage; even a correct initial box drifts off-subject.

**Quick win (one line, do immediately):** in `_auto_crop_from_regions`, center on the *primary* region — a watched-thing region if present, else the largest — never the union.

**Data changes.**
- `clip_regions` gains `t_frame REAL` (timestamp the box was observed at) and `is_primary INTEGER` (or a salience score). Multiple rows per clip across time.
- Sampling piggybacks on the deep-index pass, which already extracts frames per scene segment — request regions for each sampled frame, no extra ffmpeg work.
- Migration keeps existing rows with `t_frame = NULL` (treated as "unknown time, weak evidence").

**Edit changes — framing becomes an explicit, stored property of each timeline item, decided at assemble time:**
1. When assembling or revising a cut with aspect ≠ source, for each item query primary-subject boxes within `[in_point, out_point]`: box nearest `in_point` → `crop_*`, box nearest `out_point` → `kb_*`. The existing zoompan Ken Burns export path then *pans with the subject*. If boxes barely differ, write a static `crop_*` only.
2. No regions in range → lazily analyze the middle frame of the cut range (one vision call per item) → else center crop.
3. Editor: crop overlay (crop.js) shown by default when aspect ≠ source; dragging updates `crop_*`/`kb_*` on the item — human override is stored and export uses it verbatim.
4. Edit chat: include each item's regions + current crop in the revision context, so "keep the oil bowl centered" updates framing, not just clip choice.

**Verification loop.** The export job extracts one frame per segment from the finished file and runs a cheap vision check ("is <primary subject> fully in frame?"). Failures are flagged on the job result in the UI, with a one-click "widen window and re-export segment N" retry. This makes framing self-correcting instead of best-effort.

**Acceptance test.** The nut-oil reel prompt from test round 2 re-exported at 9:16 keeps the cracker's output chute, the pour, and the oil bowl centered through all four shots, verified by the frame-check step passing.
