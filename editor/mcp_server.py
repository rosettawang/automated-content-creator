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


def _start_app() -> bool:
    """Launch the Flask app headless as a detached child, then wait for health.
    Only attempts a local start when EDITOR_URL points at localhost."""
    host = httpx.URL(EDITOR_URL).host
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        return False  # remote app — not ours to start
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "app.py")],
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
    """Ensure the app is reachable — auto-start it if enabled, else raise."""
    if _is_up():
        return
    if AUTOSTART and _start_app():
        return
    raise AppDownError(
        f"Can't reach the content-creator app at {EDITOR_URL} and couldn't start "
        f"it automatically. Launch the desktop app (or run `python editor/app.py`) "
        f"and try again."
    )


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
    deadline = time.monotonic() + 600  # generous: albums/folders can be large
    while time.monotonic() < deadline:
        snap = c.get(f"/api/import-jobs/{job_id}").json()
        if snap.get("finished"):
            if snap.get("error"):
                return [{"status": "error", "error": snap["error"]}]
            return snap.get("results", [])
        time.sleep(2.0)
    return [{"status": "error", "error": "import timed out after 10 minutes"}]


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
def assemble_cut(
    prompt: str,
    clip_ids: list[int] | None = None,
    project_id: int | None = None,
    name: str | None = None,
) -> dict:
    """Assemble a rough-cut *edit* from the library for a described video: Claude (in
    the app) picks clips and in/out points and creates a new edit.

    Terminology: a **campaign** is a theme (e.g. "Holiday campaign", "Gardening")
    that groups related work; an **edit** is one assembled timeline/cut. This tool
    creates an edit — optionally filed under a campaign. (In the API a campaign id is
    passed as `project_id`, the underlying field name.)

    - prompt: what the video should be (e.g. "30s upbeat montage of pollinators").
    - clip_ids: optional — restrict the pool to these library clip ids.
    - project_id: optional — file the new edit under this campaign (its description
      is also fed to the model as context). Omit for a standalone edit.
    - name: optional edit name (defaults to a trimmed prompt).

    Returns the new edit id, its project_id, the concept, and the chosen selections.
    Open the edit in the editor to fine-tune and export.
    """
    _require_app()
    payload: dict = {"prompt": prompt}
    if clip_ids:
        payload["clip_ids"] = clip_ids
    if project_id is not None:
        payload["project_id"] = project_id
    if name:
        payload["name"] = name
    with _client() as c:
        resp = c.post("/api/generate-edit", json=payload)
    if resp.status_code != 200:
        return {"error": _err(resp)}
    data = resp.json()
    return {
        "edit_id": data.get("id"),
        "project_id": data.get("project_id"),
        "name": data.get("name"),
        "concept": data.get("concept"),
        "selections": data.get("selections", []),
        "next_step": "Open this edit in the editor window to review and export.",
    }


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
