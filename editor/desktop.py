#!/usr/bin/env python3
"""
Desktop window wrapper for the editor — runs the same Flask app, but opens it
in a native window instead of requiring you to open a browser tab.

Usage:
    MEDIA_DIR=/path/to/local/footage python3 editor/desktop.py
"""
import threading

import webview

from app import app, init_db

PORT = 5001


def run_flask():
    app.run(port=PORT, debug=False, use_reloader=False)


def main():
    init_db()
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    webview.create_window("Editor", f"http://127.0.0.1:{PORT}", width=1280, height=800)
    webview.create_window(
        "Clip Library", f"http://127.0.0.1:{PORT}/library", width=1100, height=760
    )
    webview.start()


if __name__ == "__main__":
    main()
