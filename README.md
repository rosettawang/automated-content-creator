# Automated Content Creator

A lightweight pipeline that turns a raw media library into short-form social clips, using **metadata as the index** — so you can find and cut footage without re-scanning raw video every time.

**Example dataset:** pipevine / pipevine-swallowtail footage for [@whrfund](https://www.instagram.com/whrfund) (Wild Harvest & Restoration Fund), used here to demonstrate the pipeline end to end.

## How it works
1. **Ingest** — open a shared Google Photos album in the browser; use "Download all" for the complete, deterministic set.
2. **Index (two-tier)** — quick-index every clip (keyframe + one-line description, category, duration), then deep-dive a flagged subset with ffmpeg frame extraction → quality notes + best-moment cut timestamps.
3. **Store** — metadata in `content_intake_log.xlsx`; ideas in markdown; hero stills in `reference_frames/`; raw video stays temporary.
4. **Ideate** — `reel_ideas_*.md` proposes 5–10s vertical concepts, each referencing a clip by filename + timestamp.
5. **Cut** — pull the needed clip, cut with ffmpeg (or Descript), export vertical to `clips_out/`.

## Repo contents
- `content_intake_log.xlsx` — the footage index (Intake Log + Video Index tabs)
- `media-pipeline.md` — pipeline design & decisions
- `video-editor-plan.md` — plan for the prompt-driven editor app + metadata architecture
- `editor/` — the rudimentary editor app (see below)
- `scripts/tag_metadata.py` — stamps descriptions from the xlsx into each file's EXIF/XMP metadata
- `reel_ideas.md` — short-form clip concepts
- `clips_out/` — edit specs (FCPXML timeline + README); rendered video is gitignored
- `reference_frames/` — hero stills
- `ads/` — Meta ads launch plan

## Editor app
A rudimentary desktop video editor: browse indexed clips, transcribe/analyze them, build a timeline, export a cut.

**Requires Python 3.10+** (the Composio dependency needs `typing.TypeAlias`, added in 3.10 — plain
`python3` may resolve to something older; on macOS `brew install python@3.11` and use that
interpreter explicitly if so).

```
cd editor
python3.11 -m pip install -r requirements.txt
python3.11 migrate_xlsx.py          # syncs content_intake_log.xlsx -> editor/data/editor.db
MEDIA_DIR=/path/to/local/footage python3.11 desktop.py
```

This opens **two native windows** (via `pywebview`), not browser tabs — an **Editor** window
and a separate **Clip Library** window, both backed by the same Flask process (`app.py`) that
`desktop.py` runs in a background thread. If you'd rather use a browser tab (e.g. for devtools),
run `python3.11 app.py` and open `http://127.0.0.1:5001` (editor) / `http://127.0.0.1:5001/library`
(library) yourself.

- **Clip Library window** — a thumbnail grid of every indexed clip with search and a per-clip
  info panel. This is the browse/catalog view.
- **Editor window** — a standard non-linear-editor layout: a **source** panel (search the bin,
  preview a clip, mark In/Out, add to the timeline), a **program monitor** that plays the whole
  timeline sequence end-to-end with transport controls, and a **timeline** with a time ruler,
  proportional clip blocks, a draggable playhead, edge-drag trimming, drag-to-reorder, and zoom.
  Keyboard: **Space** = play/pause, **Del** = remove the selected clip, **←/→** = nudge the playhead.

`MEDIA_DIR` should point at wherever you've temporarily pulled the actual clips (see "Storage
during editing" in `video-editor-plan.md`) — clips without a matching local file still show up
in the library (marked "not local") but can't be previewed, transcribed, or exported until
they're pulled down.

**Transcription** — select a clip in the editor's source panel and hit "Transcribe" to run
Whisper (`base` model, local, no internet needed) over its audio; the result is saved to the
clip's `transcript` field. Takes a few seconds per clip on a laptop CPU; slower for longer clips.

**Import from Drive** — paste one or more Google Drive share links (one per line, "anyone
with the link" sharing) into the "Import from Drive" box under **Tools** in the editor's source
panel and hit Import. Each link is downloaded straight into `MEDIA_DIR` under its original
filename; if the filename matches an already-indexed clip it just becomes available locally,
otherwise a new clip row is added so it shows up right away. Requires `MEDIA_DIR` to be set
when launching.

**AI-assisted rough cuts and content ideas** — requires an Anthropic API key:
`export ANTHROPIC_API_KEY=...` before launching. Type what you want in the "Describe the
video you want" box in the editor toolbar and hit "Generate" — Claude reads the whole clip
catalog (descriptions, categories, transcripts) and picks/orders clips with in/out points
onto the current project's timeline, which you can then hand-adjust as usual. "Suggest content
ideas" (under Tools in the source panel) asks Claude what's missing from the existing footage
and worth filming next, based on the same catalog.

**Analyze frame** — select a clip and hit "Analyze frame" to grab a keyframe (via ffmpeg) and
send it to Claude's vision to auto-fill `description`, `category`, and `tags`. Mainly useful
for clips that came in with blank metadata (e.g. via Drive import) rather than through the
original xlsx indexing pass; running it again overwrites whatever was there before.

Re-run `migrate_xlsx.py` any time the xlsx changes — it's a safe upsert by filename and
won't touch existing projects/timelines/transcripts. `editor/data/editor.db` holds your actual
project/timeline work, so unlike the rest of this repo's temp/local media, it's committed to git.

## Notes
- Large binaries (raw/rendered video) are kept out of git via `.gitignore` — they're temporary/local.

_Status: working prototype. Current output: a sample vertical cut (`IMG_2926`, caterpillar macro)._
