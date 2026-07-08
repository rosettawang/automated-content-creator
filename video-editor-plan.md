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

## How "prompt → rough cut" would work

1. You describe the video ("30s reel about the butterfly lifecycle").
2. App searches `clips` (keyword/tag filter now, semantic search later) for matches, ranked by quality flag and relevance.
3. App assembles a first-pass `timeline_items` sequence using each clip's already-logged best-moment in/out points.
4. Draft lands in the editor for review.

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

1. Migrate xlsx → SQLite (one-time script; xlsx becomes a generated view, not the source)
2. Build the prompt → draft-timeline generator (keyword/tag search first — no ML required for v1)
3. Build the minimal editor UI (timeline list + trim/reorder + preview)
4. Wire export to the existing ffmpeg/FCPXML pipeline
5. Later: add semantic search over descriptions/transcripts for better prompt matching

---

## Decided

- **App shape** — custom, rudimentary editor (not Descript). Built as a small local Flask app: `editor/app.py` + a single vanilla-JS page. Phases 1–3 above are implemented: xlsx → SQLite migration (`editor/migrate_xlsx.py`), a clip library with search, a timeline (add/trim/reorder/remove), and export via ffmpeg trim + concat to `clips_out/`. Semantic search (phase 5) is not built yet — search is still keyword/tag matching over the SQLite `clips` table.
- **Presentation: native window, not a browser tab.** `editor/desktop.py` runs the same Flask app in a background thread and opens it in a `pywebview` window. This is a presentation-layer change only — the Flask backend, SQLite data, and ffmpeg export are unchanged and still reachable via a plain browser tab (`python3 app.py`) if that's ever more convenient (e.g. devtools debugging).
- **Deeper analysis (motion, transcription) doesn't depend on UI shape.** Whether the UI is a webpage or a native window, backend analysis (Whisper, OpenCV, etc.) runs identically as Python code in the Flask process. First one built: **Whisper transcription** — `POST /api/clips/<id>/transcribe` runs the local `base` Whisper model over a clip's audio (no internet required) and stores the result in a new `clips.transcript` column. Exposed in the UI as a "Transcribe" button next to the preview. Motion detection would follow the same pattern (a new endpoint + a new `clips` column) whenever it's needed.

## Open decisions

- **Where it runs** — currently local-only (`127.0.0.1:5001` under the hood, regardless of native-window or browser presentation). Reachable remotely only if that becomes a real need.
- **Storage during editing** — confirmed: the app reads clips from a `MEDIA_DIR` you point it at (your temp local pull), not a permanent copy. Clips not yet pulled show up in the library (marked "not local") but can't be previewed/transcribed/exported until they are.
- **Motion detection** — not yet built. Same pattern as transcription (OpenCV-based endpoint + stored column) whenever it's wanted.
