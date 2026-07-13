from flask import Blueprint
from core import *

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/studio")
def studio():
    """The workspace: Editor + Library + Campaigns as sibling sections of ONE
    document (no iframes), with a rail, native cross-panel drag, and live shared
    state. Opened by the desktop app."""
    return render_template("studio.html")


@bp.get("/library")
def library():
    return render_template("library.html")


@bp.get("/campaigns")
def campaigns_page():
    return render_template("campaigns.html")


@bp.get("/bundle/<panel>.js")
def panel_bundle(panel):
    files = PANEL_BUNDLES.get(panel)
    if not files:
        return {"error": "unknown bundle"}, 404
    parts = []
    for name in files:
        path = STATIC_DIR / name
        parts.append(f"// ===== {name} =====\n{path.read_text()}")
    body = "(function () {\n" + "\n".join(parts) + "\n})();\n"
    # no-cache so edits during dev show up without a hard refresh
    resp = Response(body, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    return resp
