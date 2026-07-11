#!/usr/bin/env python3
"""
Desktop window wrapper for the editor — runs the same Flask app, but opens it
in a native window instead of requiring you to open a browser tab.

Usage:
    MEDIA_DIR=/path/to/local/footage python3 editor/desktop.py
"""
import socket
import threading

import webview

from app import app, init_db


def _pick_port(preferred=5001):
    """Grab a port for the local server. Prefer the familiar 5001, but if it's
    already taken (a leftover server, another app on the machine), fall back to
    any free port the OS hands us -- so the desktop app never fails to open, or
    load blank, over a port clash."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port available for the local server")


PORT = _pick_port()


class NativeApi:
    """Bridge exposed to the page as `window.pywebview.api`. Only available in the
    desktop app -- the browser has no `window.pywebview`, which is exactly how the
    frontend decides whether to offer the "move files in" flow."""

    def pick_files(self):
        """Open a native file picker and return the chosen absolute paths.

        Returns a list of path strings (empty if the user cancelled). These are
        real on-disk paths, so the backend can move-and-delete originals -- which a
        browser upload can never do."""
        window = webview.active_window()
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        # pywebview returns a tuple/list of paths, or None on cancel.
        return list(result) if result else []


def run_flask():
    app.run(port=PORT, debug=False, use_reloader=False)


def main():
    init_db()
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    api = NativeApi()
    # One window, one document: /studio hosts Editor / Clip Library / Campaigns as
    # sibling sections (no iframes) with a left rail, native cross-panel drag, and
    # live shared state. (The older iframe shell at /workspace is still served as a
    # fallback but is no longer the default.)
    webview.create_window(
        "Content Studio", f"http://127.0.0.1:{PORT}/studio",
        width=1500, height=920, js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
