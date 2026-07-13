# App Review — Functionality & Architecture Notes

*Test session: 2026-07-12, app live at 127.0.0.1:5001, driven via Chrome. Test prompt: "a 15s vertical reel of the pipevine swallowtail lifecycle — caterpillar to butterfly — ending on a wide garden shot" (edit #18).*

## What happened, end to end

1. **Library → Assemble**: prompt accepted, rough cut generated in ~15s. Clip selection quality was genuinely good — host plant → caterpillar → macro caterpillar → adult swallowtail → wide meadow, exactly the requested arc, 5 clips × 3.0s = 15s.
2. **Fatal catch**: all 5 chosen clips were *not local* (only 36 of 118 clips exist in `MEDIA_DIR`). The timeline looked normal, the play button toggled to pause, but the playhead sat at 0:00 forever, the program monitor stayed black, and nothing told me why. Export would have 404'd.
3. **Edit chat recovery**: "replace every clip that isn't downloaded locally…" worked — Claude swapped in 3 local clips and kept 15s. But it inferred locality from description text; the catalog it sees has no availability field (see Flaw 1). The result was on-format but **off-topic** (forest b-roll instead of swallowtails), because none of the topical footage is local.
4. **Export**: worked. `clips_out/A_tight_15s_…mp4`, 1080×1920, 15.03s, h264+aac. So the whole loop *can* run — but the produced video matches the request in duration/format only, not content.

## Flaws found (with evidence)

### 1. The AI can't see clip availability — the core flow breaks on it
`claude_client.py:_format_clip_catalog()` sends id/file/duration/category/description/context/tags/transcript/resolution/quality — **not `available_locally`**, even though `/api/clips` returns that field. So `generate_rough_cut` happily builds timelines from clips that can't be previewed, transcribed, or exported. With 82/118 clips non-local, most prompts will produce broken cuts. This is the single highest-impact fix.

### 2. Failures are silent
- Non-local clips on the timeline get no badge; playback failure produces no message (play toggles to pause, playhead frozen at 0:00).
- The `<video>` element has no `error`/`stalled` listeners, so even genuine media errors never surface.
- Export on a non-local timeline returns a 404 JSON error only after the click.

### 3. `.MOV` served as `video/quicktime` will stall Chrome
`/api/clips/<id>/media` uses `send_file(path)` → `Content-Type: video/quicktime` for .MOV. Chrome reports `canPlayType('video/quicktime') === ""`. The README explicitly offers the browser-tab workflow, and library.js/app.js set this URL as `<video src>`. (Caveat: my automated Chrome couldn't decode *any* video, so I verified the request/MIME behavior and codec support flags, not final rendering in a normal Chrome — worth a 2-minute manual check.) Underlying files are H.264, so a remux — not a transcode — is enough. Fix: remux to faststart .mp4 on import, or at minimum serve .mov as `video/mp4`.

### 4. In/out points ignore the "best moment" work
Every generated item was `in=0.0, out=3.0` regardless of clip length (9s–40s clips). The deep-index pipeline stores per-clip `segments` with timestamps and descriptions, but they're not included in the rough-cut catalog, so the model can only guess from the front of each clip. The two-tier indexing effort isn't paying off at the cut stage.

### 5. "Vertical" was luck, not logic
The prompt said vertical; `edits.aspect` stayed `null` (= "source"). Output was 1080×1920 only because the chosen iPhone clips carry portrait rotation metadata. A landscape clip in the mix would (a) come out landscape, and (b) worse — `ffmpeg concat -c copy` of mismatched-dimension segments produces a corrupt file. The exported file's avg fps is already an odd 58.33 from unnormalized VFR sources.

### 6. Export is synchronous and unoptimized for social
- N× ffmpeg re-encodes + concat run inside the HTTP request — no progress, no job id, timeout risk on longer timelines (a background-job pattern already exists for indexing; export doesn't use it).
- No bitrate/fps normalization: 36.8MB for 15s (~19.6 Mbps). Instagram will crush it anyway; ~8–12 Mbps 1080×1920 at a fixed 30/60fps with `+faststart` is the right target.
- Output filename comes from the edit name (keeps the Unicode "…"), and collisions silently overwrite (`-y`).

### 7. Generated cuts don't land anywhere
The new edit was created "(Unassigned)"; the Campaigns page still says "No cuts yet — open the editor and Generate one" for both campaigns. There's also no edit browser in the UI — getting back to a cut means knowing `/?edit=18`. Work products get orphaned.

### 8. Chat input can vanish
First edit-chat attempt: clicked the box, typed, hit Send — input was cleared, no POST fired, no message appeared. Second attempt (setting the field value directly) worked. Likely a re-render stealing focus/state between typing and submit. Add a pending state and don't clear the input until the POST is acknowledged.

### 9. Library UX
- Thumbnails render black until lazy-load kicks in on scroll; first paint looks broken.
- No "local only" filter, and the dominant visual is "Not downloaded" cards — the 36 usable clips are buried.
- Debris in the index shows up as titles ("CORRECTION (keyframe guessed…)" as a description; a 0.3s "clip" that's a photo).

## Architecture assessment

**Sound ideas worth keeping:** metadata-as-index (DB as source of truth, xlsx as export, XMP/EXIF embedding for portability); the edit-chat + undo/snapshot loop; the MCP server as a thin proxy over the same HTTP API (genuinely prevents drift); prompt → structured `RoughCutPlan` via typed outputs.

**Structural concerns:**

1. **`app.py` is a 3,528-line monolith with 78 endpoints** plus ~10 kinds of daemon threads (indexing, embeddings, faces, motion, drive/photos imports, thing-scans) sharing SQLite and in-memory job dicts. Job state dies on restart; Flask dev server with `debug=True` (auto-reloader can double-spawn/kill those threads; Werkzeug debugger is enabled). Split into blueprints (clips / edits / campaigns / media / ai / jobs), move jobs to a table, run under waitress/gunicorn.
2. **Two overlapping domain models**: `projects` ("campaigns") vs `edits`, with `timeline_items` hanging off edits. README still documents `/?project=<id>`; the code uses `/?edit=<id>`. There are also two UI shells (index/library/projects pages vs `studio.html` for the desktop build). Consolidate naming and kill one shell before they drift further.
3. **Media resolution is a per-request glob** (`find_media_file` globs `MEDIA_DIR` on every media/export hit) and "local" is recomputed rather than tracked; fine at 118 clips, but it's also why availability never made it into the AI catalog — it's not a first-class property of the clip lifecycle. Make availability a stored, event-updated field, and make "pull this clip down" an in-app action (Drive import already half-exists).
4. **The playback layer swaps one `<video>` src per segment** with no preloading — even when media loads, segment boundaries will hiccup. Two alternating video elements (or a pre-rendered low-res proxy of the timeline) fixes it.

## Prioritized recommendations

**P0 — make the promise real ("prompt → usable reel"):**
1. Add `available_locally` (+ duration and deep-index segments) to the AI catalog; default generation to local clips; badge non-local items on the timeline with a one-click "pull from Drive/Photos" path.
2. Remux imports to faststart H.264 .mp4; serve `video/mp4`; add video `error` handlers with visible toasts.
3. Have the model return an `aspect` with the plan (or parse "vertical/square" from the prompt), store it on the edit, and normalize dims+fps in export (scale/crop filter always on, fixed fps, `+faststart`, ~10 Mbps).
4. Make export a background job with progress; sanitize output filenames and avoid overwrites.

**P1 — reliability:**
5. Blueprint split of `app.py`; jobs table; production WSGI server, `debug=False`.
6. Fix chat-input loss; disable Send while a revision is in flight (spinner exists, input handling doesn't).
7. Auto-assign generated edits to the active campaign; add an "Edits" list view.

**P2 — polish/leverage:**
8. "Local only" filter + local-count in the library header; placeholder thumbnails instead of black.
9. Use deep-index segments to pick in/out points (this is where cut quality will jump).
10. Preload next segment for gapless program playback.
11. Data hygiene pass: move "CORRECTION…" notes out of descriptions, mark photos as stills (no 0.3s durations in the video pool).

## Update (same day) — silent-failure fixes landed

The "ghost clip" problem is now addressed at three layers. A parallel session working in this repo fixed prevention on the backend (uncommitted as of this note): `_pool_for_generation` only offers downloaded clips to the AI, the catalog flags any non-local stragglers, empty pools return a clear error, and `GET /api/edits/<id>` now reports `available_locally` per timeline item.

## Planned — consolidate domain naming + kill one shell (decision: 2026-07-13)

Owner decision on review item #2 (architecture): **do a full `project` → `campaign` rename across code + DB**, and **collapse to one shell** (`/studio`), executed as soon as it can be the sole writer on the contended files.

**Blast radius measured:** 416 `project` occurrences — app.py (154), library.js (84), projects.js (55), app.js (31), db.py (21), `_campaigns_panel.html` (14), `_library_panel.html` (10), cuts.js (8), `_editor_panel.html` (2), studio/projects.html (2). Live DB is tiny: 2 projects, 8 project_things, 3 edits, 0 project_clips/messages — migration is cheap; the risk is code collision with the parallel session (which has app.py/db.py/library.js/partials uncommitted).

**Rename map (do in one atomic pass, app stopped):**
- DB tables: `projects`→`campaigns`, `project_clips`→`campaign_clips`, `project_things`→`campaign_things`, `project_messages`→`campaign_messages`. Columns: every `project_id`→`campaign_id` (in edits, the three join tables). Migration: `ALTER TABLE … RENAME TO` + `RENAME COLUMN` (SQLite ≥3.25) guarded by existence checks in `init_db`.
- API routes: `/api/projects*`→`/api/campaigns*`; page route `/projects`→`/campaigns` (keep a redirect from the old paths for one release).
- Query params: keep `/?project=<id>` as an accepted alias of `/?campaign=<id>` so existing links/deep-links don't break.
- Python: functions/vars (`create_project`, `_project_membership`, `project_id`, …) and every SQL string.
- JS: `currentProjectId`→`currentCampaignId`, `projectsById`, fetch URLs, `?project=`.
- Templates/CSS: `project-*` ids/classes in the campaigns panel + partials.

**Shell consolidation (`/studio` is canonical):**
- `/` → redirect to `/studio`; remove/redirect `/library`, `/projects`.
- `/studio` reads `?edit=<id>` / `?campaign=<id>` on load → opens+focuses the Editor panel on that edit.
- Rewire in-app `window.location.href='/?edit=…'` (library.js, cuts.js, projects.js) to, when inside the single-doc shell, focus the Editor panel + load the edit instead of a full-document navigation (which currently destroys the shell).
- Delete the now-redundant standalone wrappers (`index.html`, `library.html`, `projects.html`) once `/studio` owns deep-links; keep the `_*_panel.html` partials.

**Hard constraint / why not yet fully executed:** a 416-site rewrite across app.py/db.py/library.js/partials cannot run concurrently with the parallel session that has those same files uncommitted — whoever saves second clobbers the other, and the parallel session's uncommitted P0 silent-failure fixes would be at risk. This pass must be serialized (sole writer). README route docs were already corrected this session (safe, uncontended).

On top of that, this session added the visibility layer (`static/app.js`, `static/style.css`):

- Non-local timeline clips render with a red hatched background and a "⚠ not local" badge (tooltip explains how to fix).
- Export pre-flight: clicking Export with non-local clips shows "Can't export — not downloaded: <files>…" instead of failing server-side.
- Playback watchdog: if a clip neither loads nor errors within 8s (the silent-stall case, e.g. `.MOV` served as `video/quicktime` in Chrome), playback stops and the program monitor says why instead of freezing at 0:00. (Complements existing not-local and error-event messages that were already in app.js but had no data to act on.)

Verified live: badge + pre-flight confirmed by temporarily hiding a media file; state fully restored afterward. Still open from the P0 list: remux-to-mp4 on import, aspect from prompt, async export.

## Fixes applied since review (2026-07-12)

- **Flaw 4 — fixed.** `_pool_for_generation` now attaches each clip's deep-index events (`clip_events`: scene/action/speech) as `moments`; `_format_clip_catalog` renders them as timestamped lines; both `generate_rough_cut` and `revise_edit` prompts instruct the model to cut to the best-matching moment and vary shot lengths. Verified live: a "oil dripping from the press" prompt picked the one local clip whose scene index describes exactly that, with reasoning that mirrored the indexed moments. (Test item removed from edit #18 afterward.)
- **Flaw 1 — already addressed in the working tree** (uncommitted): the generation pool now drops non-local clips, and the catalog flags `available_locally=false`. Note the ghost-clip failure happened because the *running server* predates these changes — restart the app to pick them up if it was launched via `desktop.py` (no auto-reloader).

## Punch-list status (2026-07-13)

Checked against the current working tree (a parallel dev session has been landing fixes continuously; most are uncommitted — restart the app to pick them up if launched via `desktop.py`).

**P0 — make the promise real**

- ✅ P0.1 Availability in the AI catalog — done, stricter than proposed: `_pool_for_generation` only offers downloaded clips; catalog flags stragglers; empty pool errors clearly; timeline items report `available_locally`; non-local badges + guards throughout the UI.
- ⬜ P0.2 Remux imports to faststart .mp4 / serve `video/mp4` — not done. `.MOV` still ships as `video/quicktime`; UI error handlers and the 8s stall watchdog mitigate the symptom, not the cause.
- 🟡 P0.3 Aspect handling — partial: `aspect` is now stored on the edit at generation time and export reframes to it; still not inferred from prompt wording ("vertical" in prose doesn't set it).
- ✅ P0.4 Async export — done: export runs as a background job (`_run_export_job`).

**P1 — reliability**

- ⬜ P1.5 Blueprint split / jobs table / production WSGI, debug off — not done; `app.py` has grown to ~4,300 lines, job state still in-memory, dev path still `app.run(debug=True)`. Reasonable to defer until active development on the file settles.
- ✅ P1.6 Chat-input loss — fixed in `chat.js`: prompt captured up front, Send + input disabled while in flight, input cleared only after the server acknowledges.
- 🟡 P1.7 Campaign attachment + edits browser — auto-assign done (generated edits take the active campaign; switching campaigns auto-loads its newest edit). A browsable "all edits" list is still missing.

**P2 — polish/leverage**

- ✅ P2.9 Deep-index moments feed cut selection — `clip_events` attached as timestamped `moments` in the catalog; prompts instruct cutting to the best moment with varied shot lengths. Verified live.
- ✅ P2.10 Gapless playback — double-buffered program monitor (two alternating `<video>` elements preloading the next segment).
- ✅ (New, this session) Timeline non-local badges, export pre-flight, playback stall messaging.
- ⬜ P2.8 "Local only" library filter; placeholder thumbnails.
- ⬜ P2.11 Data hygiene (CORRECTION-notes in descriptions; photos mixed into the video pool).

**Designed, not yet built — provenance / re-download.** The `clips` table has no source pointer; the Drive importer knows each URL at download time and discards it, so a missing file can't be re-fetched. Plan: add `source_kind` + `source_url` columns written at import; backfill existing clips (album-level is fine); turn the "⚠ not local" badge into a **Re-download** button (`POST /api/clips/<id>/pull` → existing drive/photos import machinery → flips availability → `clip-updated` broadcast refreshes all panels). Caveats: Drive links re-fetch cleanly per file; Google Photos album links lack stable per-file URLs, so Photos re-download means re-running the album import and matching by filename/`content_hash`. Also stamp `source_url` into XMP on embed so provenance travels with the file.

## Second test session (2026-07-13) — full re-run, new assessment

*Same method as round 1: drove the app in Chrome as a user. Prompt: "a 20s vertical reel of small-batch nut oil pressing — shelling and sorting the nuts, feeding the press, golden oil dripping, end on the finished product" (edit #33).*

**The intended loop now completes cleanly, end to end.**

1. **Assemble** — 5 clips, all local, correct narrative arc (cracking → sorting → pouring → press → golden-oil close-up). Shot lengths varied (2.8–4.6s) and in-points are non-zero and story-matched (e.g. 18.9s into a 30s clip) — the deep-index moments are genuinely driving cut selection now, vs. round 1's uniform `0.0–3.0` on every clip.
2. **Playback works in Chrome** — the program monitor actually plays through the timeline. Root cause fixed: media is now served relabeled as `video/mp4` (with a proxy path for HEVC), and the double-buffered monitor steps across segments. Round 1's freeze-at-0:00 is gone.
3. **Edit chat** — "open on the shelling shot… drop the patio sorting clip… hold the oil close-up longer" was applied exactly, with an honest reply noting the close-up already runs its full length. No input loss; Send disabled while in flight.
4. **Aspect + export** — set 9:16 in the settings gear, exported: true **1080×1920 reframe from landscape sources** with subjects kept centered (region-aware crop), constant 30fps (VFR normalized), 6.3 Mbps, `+faststart`, clean ASCII filename, ~15.4s. Every export defect from round 1 (sync request, 19.6 Mbps, weird 58.33fps, Unicode filename) is fixed; export runs as a cancellable background job.
5. **Library** — every card has a thumbnail (no black grid), count reflects the 36 usable clips, and a new **Cuts** tab is the missing edits browser: thumbnail cards with duration/clip count, per-cut campaign assignment, Open/Rename/Delete.

**Architecture caught up too.** The 4,300-line `app.py` is gone: app factory + 7 blueprints (`pages/jobs/clips/media/ai/campaigns/edits`), shared helpers in `core.py`, a real `jobs` table with orphan reconciliation at boot, and **waitress** replacing the Werkzeug dev server (debug now opt-in via `FLASK_DEBUG=1`). That closes P1.5 and P0.2 — the two biggest open items from the punch list.

**Remaining papercuts (new list, all small):**

- "vertical" in the prompt still doesn't set `aspect` — the model should return it with the plan; today you must know about the settings gear.
- The settings popover renders partially off-screen (clipped at the window's left edge).
- Generated cuts still default to "(Unassigned)" unless a campaign is pre-selected; the Cuts tab makes this survivable but auto-suggesting a campaign would be better.
- Provenance / re-download (`source_url` + pull button) — designed above, still unbuilt; there's now a "Verify / relink" tool for local files, which is adjacent but not the same thing.
- `core.py` is a new 2,475-line gravity well; worth splitting before it recreates the old problem.
- Data hygiene items from round 1 still stand (CORRECTION-notes in descriptions, photos mixed into the video pool).

**Verdict:** round 1 produced an on-format, off-topic video through a silent minefield; round 2 produced exactly the requested reel — right story, right footage, right moments, right format — with failures that speak up when something's wrong. This has crossed from demo to genuinely usable. The gap between "what I typed" and "what I got" is now mostly the aspect-from-prompt papercut, not the pipeline.

## Bottom line
The pipeline concept holds up — prompt in, structured cut out, real 1080×1920 file rendered. The narrative clip selection is already good. What breaks trust is everything around it: the AI can't see which clips are usable, failures are invisible, and format correctness is accidental. Items P0.1–P0.4 are a small amount of work relative to what's built and would take this from demo to dependable.
