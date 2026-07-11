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
  info panel. This is the browse/catalog view, and it's also a launch point for a cut: type
  what you want in the **"Describe the video you want"** bar and hit **Assemble in editor →**.
  That creates a new project, generates a rough cut from your indexed clips, and jumps straight
  into the Editor window (via `/?project=<id>`) with the timeline already populated. Tick the
  **checkbox** on any cards first to scope the generation to just those clips (a hint shows the
  count, and **Clear selection** resets it); with nothing selected it draws from the whole
  library. Selected clips are treated as a suggestion — Claude may use a subset for a tighter cut.
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
`export ANTHROPIC_API_KEY=...` before launching. There are two ways in: the "Describe the
video you want" box in the editor toolbar (hit "Generate" to add clips onto the *current*
project's timeline), or the same box in the **Clip Library** (hit "Assemble in editor →" to spin
up a *new* project and land in the editor — see the Clip Library window notes above, including
selecting specific clips to narrow the pool). Either way Claude reads the clip catalog
(descriptions, categories, transcripts, context) and picks/orders clips with in/out points, which
you can then hand-adjust as usual. "Suggest content ideas" (under Tools in the source panel) asks
Claude what's missing from the existing footage and worth filming next, based on the same catalog.

**Describe & tag (metadata is the index)** — select a clip and use the **Describe this clip**
panel in the source monitor to write your own `description`, `category`, `tags`, and a freeform
`context` note (what the clip is for, why it matters, how you'd use it). Save writes it to the
clip's row in `editor.db` — the single source of truth for the index — and it's immediately
searchable (search now matches description, category, tags, and context). Tick **embed in file**
to also stamp the metadata into the media file's XMP/EXIF via `exiftool`, so it travels with the
file to any machine or tool and the library can be rebuilt by re-scanning files. To describe
several clips at once, tick their checkboxes in the source list and use the green **bulk bar** to
apply a shared category/tags/context to all of them. Your typed `context` is fed into Claude's
clip catalog, so rough-cut generation and content suggestions take it into account.

Under **Tools**: **Embed all into files** stamps every clip's current metadata into its local
file at once; **Export to xlsx** writes the index back out to `content_intake_log.xlsx` (adding a
`Context` column), keeping the spreadsheet a faithful export of the DB rather than the master.

**Analyze frame** — select a clip and hit "Analyze frame" to grab a keyframe (via ffmpeg) and
send it to Claude's vision to auto-fill `description`, `category`, and `tags`. It now *merges*
rather than clobbers: your hand-written `context` is preserved, AI tags are unioned with any
existing tags, and an existing human description is kept. Mainly useful for clips that came in
with blank metadata (e.g. via Drive import) rather than through the original xlsx indexing pass.

Re-run `migrate_xlsx.py` any time the xlsx changes — it's a safe upsert by filename and
won't touch existing projects/timelines/transcripts. `editor/data/editor.db` holds your actual
project/timeline work, so unlike the rest of this repo's temp/local media, it's committed to git.

## Claude MCP server

The repo ships an **MCP server** (`editor/mcp_server.py`) that exposes the app to Claude
(Claude Code or Claude Desktop) as three tools:

- **`import_media`** — import local files, a `.zip`, Google Drive links, or Google Photos
  album links; each is auto-indexed (vision description, transcript, GPS) just like the
  drop-zone.
- **`search_clips`** — search the library by description / category / tags / transcript /
  filename.
- **`assemble_cut`** — turn a prompt into a new rough-cut project.

It's a **thin proxy over the running app** — every tool calls the same HTTP endpoints the
desktop UI uses, so the two can't drift apart. If the app isn't running the server will try
to start it automatically (set `MCP_AUTOSTART=0` to disable); point it elsewhere with
`EDITOR_URL` (default `http://127.0.0.1:5001`).

### Install (fresh clone, any machine)

```bash
git clone <this-repo> && cd automated-content-creator
python3.11 -m venv editor/venv
editor/venv/bin/python -m pip install -e editor        # installs deps + the MCP entry point
printf 'ANTHROPIC_API_KEY=sk-...\nMEDIA_DIR=%s/media\n' "$PWD" > editor/.env
```

That's it — no paths to edit. `pip install -e editor` reads `editor/pyproject.toml`, pulls
in every dependency, and creates a `content-creator-mcp` command inside the venv.

### Register it with your Claude client

**Claude Code** — nothing to do: `.mcp.json` is committed at the repo root with
**relative** paths (`editor/venv/bin/python editor/mcp_server.py`), so it works from any
clone location and any username. Open the project in Claude Code and approve the
`content-creator` server when prompted.

**Claude Desktop** — add this to `claude_desktop_config.json` (Settings → Developer → Edit
Config), using the **absolute** path to your clone (Desktop doesn't resolve relative paths):

```json
{
  "mcpServers": {
    "content-creator": {
      "command": "/absolute/path/to/automated-content-creator/editor/venv/bin/content-creator-mcp"
    }
  }
}
```

Then restart the client. Ask Claude to *"search my clips for pollinators"* or *"import this
zip"* to confirm the tools are live.

> **Note — this runs *your own* instance against *your own* library.** Cloning gets you the
> machinery, not someone else's footage; each person supplies their own `ANTHROPIC_API_KEY`,
> `MEDIA_DIR`, and local clip database. Sharing one hosted library with others would instead
> mean deploying this as a *remote* MCP behind a URL with authentication.

## Notes
- Large binaries (raw/rendered video) are kept out of git via `.gitignore` — they're temporary/local.

_Status: working prototype. Current output: a sample vertical cut (`IMG_2926`, caterpillar macro)._
