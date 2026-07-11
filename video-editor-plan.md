# Video Editor App — Plan

## Vision

Move from a manual indexing pipeline to an app: describe the video you want in a prompt, get a rough-cut assembled automatically from indexed footage, then fine-tune it yourself in a lightweight timeline editor.

---

## Why the metadata model needs to change

Today, `content_intake_log.xlsx` (Intake Log + Video Index tabs) is a human-edited spreadsheet. That's fine for manual browsing/filtering, but it can't serve as a live app database:

- No query engine for "find clips matching this prompt"
- No place to store per-project timeline state (which clips, in what order, with what trim points)
- Not safe for concurrent read/write from a running app
- Best-moment in/out points already exist, but only as free text in the "Clip Ideas / Cut Notes" column (e.g. *"Usable window 2.3-4.5s"*) — the app needs these as structured fields, not prose to re-parse.

---

## Target metadata architecture

Three layers, each doing what it's actually good at:

1. **Structured DB (SQLite)** — becomes the real source of truth the app reads/writes at runtime.
   - `clips`: file stem, duration, category, description, transcript, quality, tags, source link, quality notes
   - `projects`: one row per video being built (name, prompt used, status)
   - `timeline_items`: project_id, clip_id, order, in_point, out_point, note — this is the editable timeline itself

2. **Embedded file metadata (exiftool)** — already underway via [scripts/tag_metadata.py](scripts/tag_metadata.py). Stays as the portable copy that travels with a file if it's ever moved or shared outside the app. Not queried live — just a durable backup layer.

3. **Semantic index (embeddings)** — built from the description/transcript/tags text in `clips`, so a written prompt ("clips of butterflies opening their wings") retrieves candidates by meaning, not just keyword match. This is what makes "prompt → video" possible. Not needed for v1 — keyword/tag filtering over the SQLite table is a fine stand-in until search quality demands more.

`content_intake_log.xlsx` doesn't have to disappear — it can become a generated export for manual browsing, rather than the thing the app reads from directly. Migrate it into SQLite once; treat SQLite as canonical from then on.

---

## How "prompt → rough cut" works (implemented)

1. You describe the video ("30s reel about the butterfly lifecycle"). Two entry points:
   - **Editor prompt box** — generates onto the *current* project's timeline (`POST /api/projects/<id>/generate`).
   - **Clip Library assemble bar** — creates a *new* project, generates, and deep-links into the editor at `/?project=<id>` with the timeline pre-filled (`POST /api/generate-project`). Optionally tick clip checkboxes first to pass a `clip_ids` subset, which scopes the candidate pool to those clips instead of the whole catalog. This is the "select → prompt → generate" flow, and it's the primary on-ramp: you go straight from browsing to a draft cut in one step.
2. `editor/claude_client.py` sends the candidate `clips` catalog (file stem, category, description, duration, transcript, context) plus the prompt to Claude (`claude-opus-4-8`, via `messages.parse` with a Pydantic schema for structured output), asking it to pick clips, order them, and give in/out points.
3. The app writes the returned selections straight into `timeline_items` for the project.
4. Draft lands in the editor for review/trim/reorder like any manually-built timeline.

Selected clips are a *suggestion*, not a mandate — Claude may use a subset for a tighter cut, but never reaches outside the selection. `/api/generate-project` runs the model first and only creates the project on success, so a failed generation never leaves an empty project behind.

This replaced the originally-planned keyword/tag search + rank step — with only ~80 clips, the whole catalog fits in one prompt, so Claude does the matching directly rather than a separate retrieval step narrowing candidates first. That keyword/tag or embedding-based narrowing would become worth adding if the catalog grows large enough that it no longer fits in context. (Manual clip-selection scoping is a lightweight stand-in for that narrowing in the meantime.)

---

## Basic editor — minimum feature set

Implemented as a standard non-linear-editor (NLE) layout in its own window (separate from the
Clip Library window):

- ✅ Load a project's timeline (ordered clips + in/out points)
- ✅ **Program monitor** — plays the whole timeline sequence end-to-end (segment-by-segment
  playback of each clip's in→out window in one `<video>` element), with transport controls
  (play/pause, prev/next clip, jump to start/end) and a running time readout
- ✅ **Horizontal timeline** — time ruler, clip blocks sized proportional to their trimmed
  duration, a draggable/clickable playhead for scrubbing
- ✅ Trim in/out per clip by dragging the block's edges (persists via the item PUT endpoint)
- ✅ Reorder clips by dragging blocks (persists via the reorder endpoint)
- ✅ Zoom the timeline (pixels-per-second), delete the selected clip
- ✅ Keyboard shortcuts: Space = play/pause, Del = remove selected, ←/→ = nudge playhead
- ✅ Source panel — search the bin, preview a clip, mark In/Out (`[` / `]` at the source
  playhead), add to the timeline; plus Transcribe / Analyze / Drive-import / content-ideas tools
- ✅ Export (ffmpeg trim + concat to `clips_out/`)
- Not yet: swap a timeline clip in place for another search candidate (currently: delete + re-add)

---

## Phased build order

1. ✅ Migrate xlsx → SQLite (one-time script; xlsx becomes a generated view, not the source)
2. ✅ Build the prompt → draft-timeline generator (Claude reasons over the full catalog directly — see below; no separate keyword/tag search step was needed at this catalog size)
3. ✅ Build the minimal editor UI (timeline list + trim/reorder + preview)
4. ✅ Wire export to the existing ffmpeg/FCPXML pipeline
5. Later: add semantic search / retrieval narrowing if the catalog grows too large to fit in one prompt

---

## Decided

- **App shape** — custom, rudimentary editor (not Descript). Built as a small local Flask app: `editor/app.py` + a single vanilla-JS page. Phases 1–3 above are implemented: xlsx → SQLite migration (`editor/migrate_xlsx.py`), a clip library with search, a timeline (add/trim/reorder/remove), and export via ffmpeg trim + concat to `clips_out/`. Semantic search (phase 5) is not built yet — search is still keyword/tag matching over the SQLite `clips` table.
- **Presentation: native window, not a browser tab.** `editor/desktop.py` runs the same Flask app in a background thread and opens it in a `pywebview` window. This is a presentation-layer change only — the Flask backend, SQLite data, and ffmpeg export are unchanged and still reachable via a plain browser tab (`python3 app.py`) if that's ever more convenient (e.g. devtools debugging).
- **Deeper analysis (motion, transcription, vision) doesn't depend on UI shape.** Whether the UI is a webpage or a native window, backend analysis (Whisper, Claude vision, OpenCV, etc.) runs identically as Python code in the Flask process. Two built so far, same pattern each time (new endpoint + new/reused `clips` column):
  - **Whisper transcription** — `POST /api/clips/<id>/transcribe` runs the local `base` Whisper model over a clip's audio (no internet required), stores the result in `clips.transcript`. "Transcribe" button next to the preview.
  - **Claude vision frame analysis** — `POST /api/clips/<id>/analyze` grabs one keyframe via ffmpeg and sends it to Claude vision, which returns a description/category/tags; overwrites `clips.description`/`clips.category` and a new `clips.tags` column. "Analyze frame" button next to Transcribe. Mainly closes the gap left by Drive-imported clips, which otherwise sit with blank metadata that the rough-cut/suggestion features can't reason about.
  - Motion detection would follow the same pattern whenever it's needed.
- **Claude API integration** — `editor/claude_client.py` (Anthropic Python SDK, `claude-opus-4-8`, structured output via `messages.parse`). Three endpoints: `POST /api/projects/<id>/generate` (prompt → rough cut onto an existing project), `POST /api/generate-project` (prompt [+ optional `clip_ids`] → brand-new project + rough cut in one shot, used by the Clip Library assemble bar), and `POST /api/suggest-content` (sends the full catalog and asks what's under-represented and worth filming next — surfaced as a "Suggest content ideas" button in the library panel). Requires `ANTHROPIC_API_KEY` set in the environment; without it these endpoints return a clear error rather than crashing.
- **Drive-link import** — `editor/drive_import.py` (`gdown`, fuzzy URL parsing) plus `POST /api/drive-import`. Paste one or more "anyone with the link" Google Drive share links into the library panel's "Import from Drive" box; each is downloaded straight into `MEDIA_DIR` under its original filename. If the filename matches an existing clip's `file_stem` it just becomes locally available; otherwise a new `clips` row is created (empty category/description, `status="imported"`) so it shows up immediately without waiting on a `migrate_xlsx.py` round-trip. This is a lighter-weight alternative to the `rclone`-based bulk sync discussed earlier — good for pulling in one or two specific files on demand, not a substitute for bulk ingest of a whole album.

## Current state (checked 2026-07-10)

The app has grown well past the original plan — ~72 API endpoints, 13 Python modules, 11 JS views. Snapshot:

- **Structure:** Campaigns (projects) → Edits (timelines w/ undo snapshots + per-edit chat) → clips; Clip Library with Grid / Map / **Things** views; native window (`desktop.py`), macOS app bundle, MCP server (`mcp_server.py`: import_media / search_clips / assemble_cut).
- **Ingest:** file/zip upload, Drive links, **Google Photos shared albums** (scraper, `=dv` for video originals), move-or-copy from disk — all with live progress bars + ETA (background job infra in `app.py`).
- **Understanding (the "analyze once" architecture — see sections above):**
  - Whisper timestamped speech; X-CLIP motion/actions; facenet faces (People UI, cluster→name); CLIP zero-shot things/category (`vision_lib.py`, action-kind excluded from still-frame CLIP); semantic search over on-device embeddings (`semantic.py`, `clip_embeddings` table, Semantic toggle in library header).
  - **Deep index** (`deep_index_clip`): sampled stills + transcript + watchlist → scene timeline in `clip_events(kind='scene')`; per-clip endpoint, bulk job, **Batch API mode** (`{"batch": true}`, 50% price). Scenes/Transcript/Actions are click-to-jump sections in the info panel.
  - **Analyze on-device toggle** (settings table) picks CLIP (free) vs Claude deep index per future analysis.
  - **Thing scan is API-free by design** (user decision): timeline-first text match over stored metadata; CLIP pixels only in on-device mode; no Claude fallback; scan-time covers use `use_api=False` (★ button = on-demand API pick).
- **Editing/export:** trim/reorder timeline, reframe/crop per aspect (9:16 etc.) incl. Ken Burns rects, ffmpeg export; prompt-driven rough cuts + revise-edit chat.
- **Data:** 36 clips indexed. **Deep-index backfill done (2026-07-10): all 27 local videos carry scene timelines — 112 segments total** (synchronous run; batch mode reserved for larger future libraries). Also 34 action events, 14 things, 6 faces.

## Open decisions

- **Where it runs** — currently local-only (`127.0.0.1:5001` under the hood, regardless of native-window or browser presentation). Reachable remotely only if that becomes a real need.
- **Storage during editing** — confirmed: the app reads clips from a `MEDIA_DIR` you point it at (your temp local pull), not a permanent copy. Clips not yet pulled show up in the library (marked "not local") but can't be previewed/transcribed/exported until they are.
- ~~**Motion detection** — not yet built.~~ Built: on-device X-CLIP action recognition → `clip_events(kind='action')` + "Detect motion" button (see Current state).
- ~~**Deep-index backfill**~~ Done — all 27 local videos indexed (112 scene segments, synchronous). Batch mode (`{"batch": true}`, 50% price) still unrun live; use it for the next large library and it'll confirm the poll/collect path.
- **Rough-cut assembly from scenes** — generation still reasons over clip-level metadata; feed the scene timelines in so cuts land on segments, not whole clips. **← now unblocked (the timelines exist); this is the next high-value step.**
- **UI for bulk deep index** — endpoint exists; a "Deep index library" button (with batch checkbox) is not yet in the UI.

---

## Analysis architecture — economical, flexible, and descriptive

Goal: understand clips well enough to edit from them (search, locate moments, assemble cuts) while minimizing recurring API cost. The guiding principle that falls out of how vision pricing works:

> **Recurring, per-frame work runs on-device (free). The paid API is reserved for occasional, per-*thing* or per-*request* reasoning — never per-frame in a loop.**

### Why (the cost model)
A Claude vision call bills as **input tokens (image + prompt) + output tokens**. The image is tokenized by *resolution, not by the question* — ~1,600 tokens/frame (up to ~4,800 high-res), the same whether you ask "is pipevine here?" or "describe everything." The model encodes the whole frame regardless; narrowing the prompt only trims the (5×-priced but usually small) output. So:
- Narrowing the question barely helps — the image is a fixed cost floor per call.
- **What multiplies cost is calls-per-frame.** One call per (frame × thing) pays the image cost N times; one call per frame covering everything pays it once. Never loop the watchlist into separate calls.
- CLIP inverts this: it encodes a frame **once**, then scores it against as many text labels as you want for ~free (a dot product each). "Look for many specific things" is the *cheap* case on-device and the *expensive* case on the API — which is why recognition belongs on-device.

### The tiers (what runs where)
| Layer | Job | Engine | Cost | Notes |
|---|---|---|---|---|
| **Speech** | what's said + when | Whisper (local) | free | timestamped segments → `clip_events(kind=speech)` |
| **Recognition** | is thing X present? (pipevine, oil press, objects) | **CLIP zero-shot** (open_clip, on-device) | free | one encode/frame, N label comparisons; the watchlist's natural engine |
| **Category / tags** | coarse labels | CLIP zero-shot vs a label vocab | free | terse; no prose |
| **Motion / actions** | what's happening + when | X-CLIP (local) | free | 8-frame windows → `clip_events(kind=action)` |
| **Faces** | who is this | facenet (local) | free | detect + embed + cluster + name |
| **Prose description** | rich 1–2 sentence caption | Claude vision *(or moondream2 local)* | paid per clip *(or free)* | the one genuinely descriptive gap CLIP can't fill |
| **Knowledge / reasoning** | "bay nuts → make chocolate", rough-cut assembly, content ideas, chat | Claude (or a local LLM) | paid, **user-triggered** | per-thing (once) or per-request, never per-frame |

Default (`ON_DEVICE_VISION=1`): recognition + category run on CLIP, so indexing a clip costs **nothing**. Prose captions are the only per-clip item that may still hit the API — and are optional (drop to a terse CLIP summary, or add moondream2 to go fully free).

### Flexibility for novel/rare things (e.g. "bay nuts")
CLIP recognizes by *appearance*, not by knowing a word, and can't reason about facts. Two moves keep it flexible without per-frame API cost:
1. **Describe the appearance** in a thing's `description` — it sharpens the CLIP prompt ("Aristolochia, heart-shaped leaves" beats bare "pipevine").
2. **Few-shot reference images** — attach 2–3 example crops to a thing; match new frames by image-to-image similarity to your own examples. This teaches a downloaded model an object it's never heard of, on-device.

The *meaning* of a thing ("bay nuts are roasted into chocolate") is a **one-time, per-thing** LLM enrichment done when you name it — not a per-frame call. Store name + aliases + visual description + context so recognition stays free and the knowledge carries forward.

### The substrate that ties it together
All layers write timestamped rows into one table — **`clip_events`** (`clip_id, kind = speech|thing|action, label|text, t_start, t_end, score`). That turns each clip from "one description" into a searchable timeline ("pipevine 3–7s", "planting 8–11s", "she says '…' at 12s") — the descriptive, editing-useful part — while the recurring compute stays on-device and free. The paid brain sits on top, reading that substrate only when the user asks for a cut, an idea, or an answer.

### Restructuring: "deep index" — analyze once, edit forever

**Decision (2026-07):** the philosophy is *analyze each clip deeply once, then make all cuts from the stored index*. That flips the economics — recurring cost stops being the issue (no per-scan loop), and what matters is how rich and **timestamped** that one analysis is. Claude has no video input, so the deep pass sends **sampled stills**: many frames in ONE call (labeled "t=0s, t=3s, …") lets Claude reason *across* frames and produce a temporal narrative — better than per-frame captioning, and ~$0.05–0.10/clip at 768px (halve via Batch API for bulk).

Under this philosophy the local-model stack simplifies:
- **Whisper — essential** (Claude takes no audio; free, timestamped).
- **facenet faces — keep** (Claude won't do face identity).
- **CLIP / X-CLIP — optional fallback** (their killer feature was free re-scanning; but adding a new thing later is first a *text search over the stored timeline*, no pixel re-analysis; the on-device toggle stays as the frugal mode).

**The deep index pass (per clip, once):**
1. **Adaptive frame sampling** (`_sample_frames`): a sparse uniform baseline (~every 6s, 768px) PLUS extra frames where the clip is *more interesting*, capped at 20. "Interesting" is a **free, on-device** signal computed before any Claude tokens — `_interest_times` = ffmpeg scene-cut detection ∪ `_motion_times` (frame-to-frame differencing over tiny grayscale thumbnails). Net: dull/static footage → few frames (cheap); active stretches → denser frames (better timeline). Frames carry their real timestamps, so uneven spacing is fine and informative. Take Whisper segments too.
2. ONE Claude call: frames + transcript + active watchlist → structured output = clip description/category/tags **plus a segment timeline** `[{t_start, t_end, description, things}]` covering the whole clip.
3. Store segments in `clip_events` (kind='scene') alongside speech/action rows; matched things → `clip_things`.
4. New things later → match against stored timeline text first (free, instant); re-analyze pixels only if that misses.
5. Bulk imports → Batch API later (50% off, background-friendly).

Result: bulky-but-worthwhile metadata created once, detailed enough to cut from ("the 4 seconds where oil pours"), searchable forever without re-analysis.

### Rule of thumb
- Per **frame** / per **clip**, on every import or scan → **on-device** (CLIP / Whisper / X-CLIP / facenet).
- Per **thing**, once, at definition time → a single enrichment call is fine (Claude or local LLM).
- Per **user request** (assemble, chat, suggest) → API/LLM, because it's occasional and reasoning-heavy.
- Batch everything about a frame into **one** call if the API is used at all; never fan out per-thing.

Escape hatch: an **"Analyze on-device" toggle** at the top of the Things view flips frame analysis between on-device CLIP (free) and the Claude vision API (richer prose captions, costs per clip). It's a persisted setting (`settings` table, `on_device_vision` key) read live per analysis via `_use_on_device()`; `GET`/`POST /api/settings` back it, and the `ON_DEVICE_VISION` env var only sets the initial default.
