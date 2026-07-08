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
A local Flask app for browsing indexed clips, building a timeline, and exporting a cut.

```
cd editor
python3 -m pip install -r requirements.txt
python3 migrate_xlsx.py          # syncs content_intake_log.xlsx -> editor/data/editor.db
MEDIA_DIR=/path/to/local/footage python3 app.py
```

Then open `http://127.0.0.1:5001`. `MEDIA_DIR` should point at wherever you've temporarily
pulled the actual clips (see "Storage during editing" in `video-editor-plan.md`) — clips
without a matching local file still show up in the library (marked "not local") but can't
be previewed or exported until they're pulled down.

Re-run `migrate_xlsx.py` any time the xlsx changes — it's a safe upsert by filename and
won't touch existing projects/timelines. `editor/data/editor.db` holds your actual project/
timeline work, so unlike the rest of this repo's temp/local media, it's committed to git.

## Notes
- Large binaries (raw/rendered video) are kept out of git via `.gitignore` — they're temporary/local.

_Status: working prototype. Current output: a sample vertical cut (`IMG_2926`, caterpillar macro)._
