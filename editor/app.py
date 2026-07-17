"""Editor backend entrypoint: app factory + blueprint registration + waitress serve.
Routes live in blueprints/*.py; shared state and helpers live in core.py."""
import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

import core
from core import init_db, reconcile_orphaned_jobs, _no_cache_static, _backfill_clip_sources

_log = logging.getLogger("editor.app")
from blueprints.pages import bp as pages_bp
from blueprints.jobs import bp as jobs_bp
from blueprints.clips import bp as clips_bp
from blueprints.media import bp as media_bp
from blueprints.ai import bp as ai_bp
from blueprints.campaigns import bp as campaigns_bp
from blueprints.edits import bp as edits_bp
from blueprints.publishing import bp as publishing_bp


def create_app():
    app = Flask(__name__)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.after_request(_no_cache_static)
    app.register_blueprint(pages_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(clips_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(edits_bp)
    app.register_blueprint(publishing_bp)

    @app.errorhandler(Exception)
    def _json_error(e):
        """Always answer the JSON API with JSON, never Flask's HTML error page —
        the frontend parses response.json() and an HTML 500 surfaces as an opaque
        'Unexpected token <' instead of the real message (fail-loudly)."""
        if isinstance(e, HTTPException):
            return jsonify({"error": e.description}), e.code
        _log.exception("unhandled error on %s", request.path)
        return jsonify({"error": f"internal server error: {e}"}), 500

    return app


app = create_app()


def serve(port: int | None = None) -> None:
    """Start the app for real use. Production runtime is waitress (single process:
    one shared copy of the ML models + one job registry; no reloader that would kill
    background jobs; no exposed Werkzeug debugger). Set FLASK_DEBUG=1 for the rare
    devtools session (reloader + debugger, dev only)."""
    port = port or int(os.environ.get("PORT", "5001"))
    # Surface the module loggers (indexing/media/jobs/etc.) so per-item job failures
    # that were previously swallowed are visible instead of silent.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    reconcile_orphaned_jobs()
    _backfill_clip_sources()  # data backfill; needs the migrated schema to exist first
    # Publishing: never auto-retry an interrupted publish, then start the poll loop.
    from social.scheduler import reconcile_orphaned_posts, start_scheduler
    reconcile_orphaned_posts()
    start_scheduler()
    if os.environ.get("FLASK_DEBUG") == "1":
        app.run(debug=True, port=port)
    else:
        from waitress import serve as waitress_serve
        print(f" * Editor (waitress) on http://127.0.0.1:{port}", flush=True)
        waitress_serve(app, host="127.0.0.1", port=port, threads=12)


if __name__ == "__main__":
    serve()
