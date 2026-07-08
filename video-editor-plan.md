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

1. You describe the video ("30s reel about the butterfly lifecycle") in the editor's prompt box.
2. `editor/claude_client.py` sends the *entire* `clips` catalog (file stem, category, description, duration, transcript) plus the prompt to Claude (`claude-opus-4-8`, via `messages.parse` with a Pydantic schema for structured output), asking it to pick clips, order them, and give in/out points.
3. The app writes the returned selections straight into `timeline_items` for the project.
4. Draft lands in the editor for review/trim/reorder like any manually-built timeline.

This replaced the originally-planned keyword/tag search + rank step — with only ~80 clips, the whole catalog fits in one prompt, so Claude does the matching directly rather than a separate retrieval step narrowing candidates first. That keyword/tag or embedding-based narrowing would become worth adding if the catalog grows large enough that it no longer fits in context.

---

## Basic editor — minimum feature set

- Load a project's timeline (ordered clips + in/out points)
- Preview/scrub
- Trim in/out per clip
- Reorder clips
- Swap a clip for another candidate from search results
- Export (reuse the existing ffmpeg/FCPXML path already used in `clips_out/`)

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
- **Deeper analysis (motion, transcription) doesn't depend on UI shape.** Whether the UI is a webpage or a native window, backend analysis (Whisper, OpenCV, etc.) runs identically as Python code in the Flask process. First one built: **Whisper transcription** — `POST /api/clips/<id>/transcribe` runs the local `base` Whisper model over a clip's audio (no internet required) and stores the result in a new `clips.transcript` column. Exposed in the UI as a "Transcribe" button next to the preview. Motion detection would follow the same pattern (a new endpoint + a new `clips` column) whenever it's needed.
- **Claude API integration** — `editor/claude_client.py` (Anthropic Python SDK, `claude-opus-4-8`, structured output via `messages.parse`). Two features: `POST /api/projects/<id>/generate` (prompt → rough cut, described above) and `POST /api/suggest-content` (sends the full catalog and asks what's under-represented and worth filming next — surfaced as a "Suggest content ideas" button in the library panel). Requires `ANTHROPIC_API_KEY` set in the environment; without it both endpoints return a clear error rather than crashing.
- **Drive-link import** — `editor/drive_import.py` (`gdown`, fuzzy URL parsing) plus `POST /api/drive-import`. Paste one or more "anyone with the link" Google Drive share links into the library panel's "Import from Drive" box; each is downloaded straight into `MEDIA_DIR` under its original filename. If the filename matches an existing clip's `file_stem` it just becomes locally available; otherwise a new `clips` row is created (empty category/description, `status="imported"`) so it shows up immediately without waiting on a `migrate_xlsx.py` round-trip. This is a lighter-weight alternative to the `rclone`-based bulk sync discussed earlier — good for pulling in one or two specific files on demand, not a substitute for bulk ingest of a whole album.

## Open decisions

- **Where it runs** — currently local-only (`127.0.0.1:5001` under the hood, regardless of native-window or browser presentation). Reachable remotely only if that becomes a real need.
- **Storage during editing** — confirmed: the app reads clips from a `MEDIA_DIR` you point it at (your temp local pull), not a permanent copy. Clips not yet pulled show up in the library (marked "not local") but can't be previewed/transcribed/exported until they are.
- **Motion detection** — not yet built. Same pattern as transcription (OpenCV-based endpoint + stored column) whenever it's wanted.
