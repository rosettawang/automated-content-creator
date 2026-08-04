#!/usr/bin/env python3
"""
MCP server for the automated content creator app.

A THIN PROXY over the running Flask app (app.py). Every tool just calls the same
HTTP endpoints the desktop UI uses, so the two can never drift out of sync. The
Flask app must be running (launch the desktop app, or `python app.py`); if it
isn't, tools return a clear "start the app" message rather than a stack trace.

Configure the app URL with EDITOR_URL (default http://127.0.0.1:5001).

Run over stdio (the transport Claude Desktop / Claude Code use for local servers):
    python mcp_server.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

EDITOR_URL = os.environ.get("EDITOR_URL", "http://127.0.0.1:5001").rstrip("/")
# Imports transcode/probe and can pull from Drive, so allow a generous timeout.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)
# Auto-start the app if it isn't running (set MCP_AUTOSTART=0 to disable).
AUTOSTART = os.environ.get("MCP_AUTOSTART", "1") != "0"
HERE = Path(__file__).resolve().parent

mcp = FastMCP("content-creator")


class AppDownError(RuntimeError):
    """Raised when the Flask app isn't reachable and couldn't be started."""


def _client() -> httpx.Client:
    return httpx.Client(base_url=EDITOR_URL, timeout=TIMEOUT)


def _is_up() -> bool:
    try:
        with _client() as c:
            return c.get("/api/env", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _app_entrypoint() -> Path | None:
    """Path to the app's entrypoint, or None when it isn't alongside this module.

    Auto-start assumes `app.py` sits next to `mcp_server.py`, which holds for a repo
    clone and for `pip install -e .` (editable installs resolve back to the repo). It
    does NOT hold for a plain (non-editable) install — the module lands in
    site-packages with no app beside it — so we must detect that instead of spawning a
    doomed child and waiting out the health timeout (spec: mcp-server, phase C)."""
    candidate = HERE / "app.py"
    return candidate if candidate.is_file() else None


def _start_app() -> bool:
    """Launch the Flask app headless as a detached child, then wait for health.
    Only attempts a local start when EDITOR_URL points at localhost AND the app's
    entrypoint is actually present next to this module."""
    host = httpx.URL(EDITOR_URL).host
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        return False  # remote app — not ours to start
    entry = _app_entrypoint()
    if entry is None:
        return False  # non-editable install: nothing to start; caller explains how
    try:
        subprocess.Popen(
            [sys.executable, str(entry)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,  # survive independently of the MCP process
        )
    except Exception:
        return False
    for _ in range(30):  # up to ~15s for Flask + init_db to come up
        if _is_up():
            return True
        time.sleep(0.5)
    return False


def _require_app() -> None:
    """Ensure the app is reachable — auto-start it if enabled, else raise a message
    that says what to actually do (which differs by why the start didn't happen)."""
    if _is_up():
        return
    if AUTOSTART and _start_app():
        return
    if _app_entrypoint() is None:
        # Installed without the repo beside it (e.g. from PyPI): we can't start the
        # app, so say so plainly rather than implying auto-start was tried and failed.
        raise AppDownError(
            f"Can't reach the content-creator app at {EDITOR_URL}. This install has no "
            f"app to start (the package is installed without the repo alongside it), so "
            f"start it yourself — launch the desktop app, or run `python editor/app.py` "
            f"from a clone — then try again. Point EDITOR_URL at it if it's not on "
            f"{EDITOR_URL}."
        )
    raise AppDownError(
        f"Can't reach the content-creator app at {EDITOR_URL} and couldn't start "
        f"it automatically. Launch the desktop app (or run `python editor/app.py`) "
        f"and try again."
    )


def _poll_job(c: httpx.Client, job_id: str) -> list[dict]:
    """Poll a background import job (/api/import-jobs/<id>) to completion and return
    its per-item results. Shared by URL imports and clip re-downloads."""
    deadline = time.monotonic() + 600  # generous: albums/folders can be large
    while time.monotonic() < deadline:
        snap = c.get(f"/api/import-jobs/{job_id}").json()
        if snap.get("finished"):
            if snap.get("error"):
                return [{"status": "error", "error": snap["error"]}]
            return snap.get("results", [])
        time.sleep(2.0)
    return [{"status": "error", "error": "import timed out after 10 minutes"}]


def _run_import_job(c: httpx.Client, endpoint: str, urls: list[str]) -> list[dict]:
    """POST a url-based import (Drive/Photos), then poll its background job until
    finished and return the per-item results. The app runs these async and reports
    progress via /api/import-jobs/<id>."""
    resp = c.post(endpoint, json={"urls": urls})
    if resp.status_code != 200:
        return [{"status": "error", "error": _err(resp)}]
    job_id = resp.json().get("job_id")
    if not job_id:
        return [{"status": "error", "error": "app did not return a job id"}]
    return _poll_job(c, job_id)


@mcp.tool()
def import_media(
    paths: list[str] | None = None,
    drive_links: list[str] | None = None,
    photos_links: list[str] | None = None,
) -> dict:
    """Import media into the clip library, then auto-index it (vision description,
    transcript, GPS) exactly as the app's drop-zone does.

    Provide any combination of:
      - paths: local filesystem paths to video/image files, OR a .zip (its media
        is extracted and each file imported; the archive itself is discarded).
      - drive_links: Google Drive share links ("anyone with the link"); a single
        file or a whole folder.
      - photos_links: Google Photos shared-album links; every item in each album
        is downloaded and imported.

    Drive and Photos imports run as background jobs; this tool waits for them to
    finish (up to 10 min) and returns a per-item summary of what was added /
    matched / skipped / failed.
    """
    _require_app()
    if not paths and not drive_links and not photos_links:
        return {"error": "Provide at least one of `paths`, `drive_links`, or `photos_links`."}

    results: list[dict] = []
    with _client() as c:
        # Local files / zips -> multipart upload to /api/import-files (synchronous).
        for p in paths or []:
            fp = Path(p).expanduser()
            if not fp.is_file():
                results.append({"path": p, "status": "error", "error": "file not found"})
                continue
            with open(fp, "rb") as fh:
                resp = c.post("/api/import-files", files={"files": (fp.name, fh)})
            if resp.status_code != 200:
                results.append({"path": p, "status": "error", "error": _err(resp)})
                continue
            results.extend(resp.json().get("results", []))

        # Drive / Photos links -> async job endpoints, polled to completion.
        if drive_links:
            results.extend(_run_import_job(c, "/api/drive-import", drive_links))
        if photos_links:
            results.extend(_run_import_job(c, "/api/photos-import", photos_links))

    added = sum(1 for r in results if r.get("status") == "added_new_clip")
    return {
        "summary": f"{added} new clip(s) imported; indexing runs in the background.",
        "results": results,
    }


@mcp.tool()
def search_clips(query: str = "") -> dict:
    """Search the clip library by description, category, tags, transcript, or
    filename. An empty query returns the whole library. Each result includes
    metadata and its index status (pending / indexing / indexed)."""
    _require_app()
    with _client() as c:
        resp = c.get("/api/clips", params={"q": query})
    if resp.status_code != 200:
        return {"error": _err(resp)}
    clips = resp.json()
    fields = ("id", "file_stem", "category", "description", "tags", "location",
              "duration_s", "index_status", "available_locally")
    return {
        "count": len(clips),
        "clips": [{k: c.get(k) for k in fields} for c in clips],
    }


@mcp.tool()
def redownload_clip(clip_id: int) -> dict:
    """Re-download a "not local" clip's media from its recorded source (Drive/Photos)
    and relink it, so it becomes playable/exportable again.

    Use when `search_clips` shows a clip with available_locally=false — its file was
    pulled out of the media folder but the catalog row (and its source) remain. This
    only re-fetches EXISTING clips; use `import_media` to bring in new footage.

    Idempotent: if the clip is already downloaded it returns immediately. Fails
    cleanly when the clip has no recorded remote source (import its file manually).
    """
    _require_app()
    with _client() as c:
        resp = c.post(f"/api/clips/{clip_id}/pull")
        if resp.status_code != 200:
            return {"error": _err(resp)}
        data = resp.json()
        if data.get("status") == "present":
            return {"status": "already_local", "message": data.get("message", "already downloaded")}
        job_id = data.get("job_id")
        if not job_id:
            return {"error": "app did not return a job id"}
        results = _poll_job(c, job_id)
    return {"status": "done", "source_kind": data.get("source_kind"), "results": results}


@mcp.tool()
def assemble_cut(
    prompt: str,
    clip_ids: list[int] | None = None,
    campaign_id: int | None = None,
    name: str | None = None,
) -> dict:
    """Assemble a rough-cut *edit* from the library for a described video: Claude (in
    the app) picks clips and in/out points and creates a new edit.

    Terminology: a **campaign** is a theme (e.g. "Holiday campaign", "Gardening")
    that groups related work; an **edit** is one assembled timeline/cut. This tool
    creates an edit — optionally filed under a campaign. (In the API a campaign id is
    passed as `campaign_id`, the underlying field name.)

    Framing: the model also infers the output aspect from the prompt ("a vertical
    reel" → 9:16, "square" → 1:1). On export, clips are subject-tracking reframed to
    that aspect (indexed subject regions drive a per-clip crop/pan) rather than
    blind center-crop — so an edit assembled here is already framed for the target
    format. Word the prompt with the destination in mind, or change the aspect later
    in the editor.

    - prompt: what the video should be (e.g. "30s vertical montage of pollinators").
    - clip_ids: optional — restrict the pool to these library clip ids.
    - campaign_id: optional — file the new edit under this campaign (its description
      is also fed to the model as context). Omit for a standalone edit.
    - name: optional edit name (defaults to a trimmed prompt).

    Returns the new edit id, its campaign_id, the concept, and the chosen selections.
    Open the edit in the editor to fine-tune and export.
    """
    _require_app()
    payload: dict = {"prompt": prompt}
    if clip_ids:
        payload["clip_ids"] = clip_ids
    if campaign_id is not None:
        payload["campaign_id"] = campaign_id
    if name:
        payload["name"] = name
    with _client() as c:
        resp = c.post("/api/generate-edit", json=payload)
    if resp.status_code != 200:
        return {"error": _err(resp)}
    data = resp.json()
    return {
        "edit_id": data.get("id"),
        "campaign_id": data.get("campaign_id"),
        "name": data.get("name"),
        "concept": data.get("concept"),
        "selections": data.get("selections", []),
        "next_step": "Open this edit in the editor window to review and export.",
    }


@mcp.tool()
def list_campaigns() -> dict:
    """List campaigns (themes that group edits, e.g. "Holiday campaign", "Gardening"),
    with id, name, description, and clip count."""
    _require_app()
    with _client() as c:
        resp = c.get("/api/campaigns")
    if resp.status_code != 200:
        return {"error": _err(resp)}
    rows = resp.json()
    fields = ("id", "name", "description", "clip_count")
    return {"count": len(rows), "campaigns": [{k: r.get(k) for k in fields} for r in rows]}


@mcp.tool()
def create_campaign(name: str, description: str = "") -> dict:
    """Create a campaign (a theme that groups edits). The description is saved and
    used as context when assembling edits under it; the app also infers "things to
    watch for" from it. Returns the new campaign id and any inferred things."""
    _require_app()
    with _client() as c:
        resp = c.post("/api/campaigns", json={"name": name, "description": description})
    if resp.status_code != 200:
        return {"error": _err(resp)}
    data = resp.json()
    return {
        "campaign_id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "inferred_things": data.get("inferred_things", []),
    }


@mcp.tool()
def list_edits(campaign_id: int | None = None) -> dict:
    """List edits (assembled cuts). With `campaign_id`, only that campaign's edits;
    without it, every edit including unassigned ones. Each has id, name, campaign,
    clip count, and total duration."""
    _require_app()
    params = {"campaign": str(campaign_id)} if campaign_id is not None else {}
    with _client() as c:
        resp = c.get("/api/edits", params=params)
    if resp.status_code != 200:
        return {"error": _err(resp)}
    return {"edits": resp.json()}


@mcp.tool()
def get_edit(edit_id: int) -> dict:
    """Get one edit's full detail: its name, campaign, aspect, and the ordered
    timeline items (clip, in/out points). Use to inspect what a cut contains before
    revising or exporting."""
    _require_app()
    with _client() as c:
        resp = c.get(f"/api/edits/{edit_id}")
    if resp.status_code != 200:
        return {"error": _err(resp)}
    return resp.json()


@mcp.tool()
def revise_edit(edit_id: int, instruction: str) -> dict:
    """Apply a natural-language revision to an existing edit's timeline — e.g.
    "tighten it to 20 seconds", "open on the butterfly shot", "drop the machinery
    clips", "make it square". Snapshots first so it can be undone in the editor.
    Returns the revised timeline."""
    _require_app()
    with _client() as c:
        resp = c.post(f"/api/edits/{edit_id}/chat", json={"prompt": instruction})
    if resp.status_code != 200:
        return {"error": _err(resp)}
    return resp.json()


@mcp.tool()
def export_edit(edit_id: int) -> dict:
    """Render an edit to an mp4 in clips_out/ (trims, subject-tracking reframes to the
    edit's aspect, normalizes fps). Runs as a background job; this polls to completion
    and returns the output. Pre-flight fails cleanly (409) if any clip's media is
    missing — pull/import those first."""
    _require_app()
    with _client() as c:
        resp = c.post(f"/api/edits/{edit_id}/export")
        if resp.status_code != 200:
            return {"error": _err(resp)}
        job_id = resp.json().get("job_id")
        if not job_id:
            return {"error": "app did not return a job id"}
        results = _poll_job(c, job_id)
    return {"status": "done", "results": results}


@mcp.tool()
def suggest_content() -> dict:
    """Ask Claude what footage is missing and worth filming next, based on the current
    library (and how past posts performed). Returns a list of {idea, rationale}."""
    _require_app()
    with _client() as c:
        resp = c.post("/api/suggest-content")
    if resp.status_code != 200:
        return {"error": _err(resp)}
    return resp.json()


def _err(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", resp.text)
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"


def main() -> None:
    """Console entry point (see pyproject: `content-creator-mcp`)."""
    mcp.run()


if __name__ == "__main__":
    main()
