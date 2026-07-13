# Spec: Library data hygiene

**Owns:** the *data* in `editor/data/editor.db` (via one-off script `editor/scripts_hygiene.py` or manual SQL), `editor/migrate_xlsx.py`, `content_intake_log.xlsx`.
**Parallel:** safe alongside code specs (it changes rows, not code). Coordinate only on `migrate_xlsx.py`. Run while the app is stopped, back up `editor.db` first.

## Items
1. **CORRECTION-notes out of descriptions.** Some descriptions carry editorial notes ("CORRECTION (keyframe guessed 'gravel path'): …"). Move the note into `context` (or drop it), keep the corrected description clean — these strings feed the AI catalog and the UI verbatim.
2. **Photos are not 0-second videos.** Clips with `kind='photo'` (or sub-second durations) should not enter the video generation pool with durations like 0.3s; either exclude photos from `_pool_for_generation` or give them an explicit still-duration convention (e.g. usable as 2–3s stills — decide, then encode it in the catalog line so the model knows it's a still).
3. **XLSX resurrection guard** (residue noted in the archived review notes): the live DB was pruned to 36 local clips, but `content_intake_log.xlsx` still lists ~118 and `migrate_xlsx.py` upserts by filename — re-running it would resurrect the 82 ghosts. Either scope the import to rows whose files exist locally (flag-controlled), or trim the xlsx to match the DB (a pre-trim copy already exists in `archive/`). Update the README sentence describing the xlsx as "faithful export of the DB" if behavior changes.
4. **Duration/metadata spot-check.** One pass verifying `duration_s`, `width/height` against ffprobe for all 36 clips; fix drift; add the check as a maintenance script.

## Acceptance
No description contains "CORRECTION"; no sub-second videos in the generation pool; running `migrate_xlsx.py` twice is idempotent and resurrects nothing; spot-check script reports zero drift.
