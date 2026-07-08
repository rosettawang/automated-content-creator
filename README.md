# Automated Content Creator

A lightweight pipeline that turns a raw media library into short-form social clips, using **metadata as the index** — so you can find and cut footage without re-scanning raw video every time.

**Current dataset:** pipevine / pipevine-swallowtail footage for [@whrfund](https://www.instagram.com/whrfund) (Wild Harvest & Restoration Fund) — the "Save the Pipevine" campaign.

## How it works
1. **Ingest** — open a shared Google Photos album in the browser; use "Download all" for the complete, deterministic set.
2. **Index (two-tier)** — quick-index every clip (keyframe + one-line description, category, duration), then deep-dive a flagged subset with ffmpeg frame extraction → quality notes + best-moment cut timestamps.
3. **Store** — metadata in `content_intake_log.xlsx`; ideas in markdown; hero stills in `reference_frames/`; raw video stays temporary.
4. **Ideate** — `reel_ideas_*.md` proposes 5–10s vertical concepts, each referencing a clip by filename + timestamp.
5. **Cut** — pull the needed clip, cut with ffmpeg (or Descript), export vertical to `clips_out/`.

## Repo contents
- `content_intake_log.xlsx` — the footage index (Intake Log + Video Index tabs)
- `media-pipeline.md` — pipeline design & decisions
- `reel_ideas_save_the_pipevine.md` — short-form clip concepts
- `clips_out/` — edit specs (FCPXML timeline + README); rendered video is gitignored
- `reference_frames/` — hero stills
- `ads/` — Meta ads launch plan

## Notes
- Large binaries (raw/rendered video) are kept out of git via `.gitignore` — they're temporary/local.

_Status: working prototype. Current output: a sample vertical cut (`IMG_2926`, caterpillar macro)._
