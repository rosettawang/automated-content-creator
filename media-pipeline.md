# Media Pipeline

## Goal

A system that uses metadata as a lightweight index into a Google Photos library, so you can quickly find and cut relevant photos and videos without parsing raw media every time.

---

## Flow

1. **Ingest** — Claude in Chrome opens the shared Google Photos album and analyzes content from screenshots/frames. Note: the shared grid lazy-loads only part of a large album on scroll (we saw ~13 of 85 videos that way), so use the album's **"Download all"** to get the complete, deterministic set in one action.
2. **Index (two-tier)** — quick-index *every* item first (one keyframe + a one-line description, category, duration) in the metadata sheet; then **deep-dive** only a flagged subset — evenly-spaced ffmpeg frame extraction → quality notes + best-moment cut timestamps.
3. **Store** — metadata in `content_intake_log.xlsx` (Intake Log + Video Index tabs); reel/edit ideas in markdown; hero stills in `/reference_frames`; raw video stays temporary in `_video_temp`.
4. **Query** — filter/search the sheet by category, tag, or keyword; ideas reference each clip by filename + timestamp.
5. **Retrieve** — to pull a specific known clip, extract it by **filename** from the "Download all" zip. The shared view hides the `IMG_####` filename, so per-item links can't target a named file (only ~33 shareable links are even exposed). Per-item links are captured incrementally when a clip is opened.
6. **Transcribe + cut** — import the needed clip(s) into Descript via the MCP → timestamped transcript → prompt Descript to cut/caption/export the vertical clip → output to `clips_out` (or Drive / back to Photos). Temp source deleted after. (Local ffmpeg remains a fallback for quick cuts — used for the proof-of-concept sample.)

---

## Storage Model

**Photos — no local storage needed.**
Claude in Chrome browses and analyzes directly in the browser. Nothing is downloaded.

**Videos — temporary only.**
Pulled into `_video_temp` for transcription and cutting (via Descript MCP; ffmpeg fallback), then deleted. Only the metadata, ideas, and hero stills persist.

**Output clips** — land wherever you want: local folder, Google Drive, or back to Google Photos.

**Metadata database** — small, local, permanent. This is the whole point.

---

## Components

| Layer | Tool | Notes |
|---|---|---|
| Browser access | Claude in Chrome | Authenticated Google Photos session, no API needed |
| Photo analysis | Claude API (vision) | Analyzes screenshots → descriptive metadata |
| Transcription + cutting | Descript (MCP) | Imports media, transcribes, edits via prompts — replaces Whisper + FFmpeg |
| Metadata store | SQLite | Structured data: filenames, timestamps, descriptions |
| Semantic search | ChromaDB (optional) | Natural language queries across metadata |
| Orchestration | Cowork | Directs the flow, queries, reviews output |

---

## Cowork + Descript Flow

1. Cowork imports media into Descript via the connector
2. Descript transcribes and returns timestamped text → stored in metadata DB
3. Cowork queries metadata to find relevant segments
4. Cowork prompts Descript to cut and export the clip
5. No temp file management needed — Descript handles it

---

## Resolved this session

- **Ingest trigger** — manual: drop a share link in the intake chat. (Album watcher still open.)
- **Metadata schema** — locked in `content_intake_log.xlsx`: file/ID, duration, category, description, transcription, quality + notes, tags, clip/cut ideas, status (+ Item Link). Video Index tab carries the quick-index of all clips.
- **Query UX** — structured filters on the sheet (category/tag/status), keyword search in descriptions.
- **Storage split** — metadata = xlsx; ideas = markdown; stills = `/reference_frames`; video = temp only.
- **Retrieval reality** — "Download all" → extract by filename is the deterministic way to get a named clip; shared view hides filenames.

## Still open

- **Transcription** — Whisper model download is blocked in the sandbox; route through the **Descript MCP** transcript export instead (or run Whisper locally).
- **Output destination** — currently `clips_out` locally; decide on Drive / back-to-Photos.
- **Album watcher** — auto-ingest of new media not yet built (manual for now).
