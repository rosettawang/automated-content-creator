"""MCP proxy-specific behavior: auto-start must not assume the repo is installed.

Phase C of specs/mcp-server.html: auto-start Popen's `app.py` next to the module,
which holds for a clone / `pip install -e .` but NOT for a plain PyPI install. Before
this guard it spawned a doomed child and waited out the ~15s health timeout, then
reported a misleading "couldn't start it automatically". These tests pin the guard.
"""
from pathlib import Path

import mcp_server


def test_entrypoint_found_in_repo_install():
    """In this checkout, app.py sits beside mcp_server.py — auto-start is viable."""
    entry = mcp_server._app_entrypoint()
    assert entry is not None and entry.name == "app.py" and entry.is_file()


def test_no_entrypoint_means_no_spawn_and_fast_clear_error(monkeypatch, tmp_path):
    """Simulate a non-editable install (module with no sibling app.py): we must NOT
    spawn anything, and the error must tell the user to start the app themselves."""
    monkeypatch.setattr(mcp_server, "HERE", tmp_path)          # nothing beside us
    monkeypatch.setattr(mcp_server, "EDITOR_URL", "http://127.0.0.1:5999")
    monkeypatch.setattr(mcp_server, "_is_up", lambda: False)   # app unreachable
    spawned = []
    monkeypatch.setattr(mcp_server.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a))

    assert mcp_server._app_entrypoint() is None
    assert mcp_server._start_app() is False
    assert spawned == [], "spawned a process with no app.py to run"

    try:
        mcp_server._require_app()
        raise AssertionError("expected AppDownError")
    except mcp_server.AppDownError as e:
        msg = str(e)
    assert "no app to start" in msg          # names the real cause
    assert "start it yourself" in msg        # tells the user what to do
