from flask import Blueprint, redirect, request
from core import *

bp = Blueprint("pages", __name__)


def _to_studio():
    """Redirect to the one shell, preserving any query string so deep links like
    `/?edit=18` or `/?campaign=3` keep working after the shell consolidation."""
    qs = request.query_string.decode()
    return redirect("/studio" + (f"?{qs}" if qs else ""))


# `/studio` is now the only shell. The former standalone pages redirect into it so
# existing links (and the `/?edit=`/`/?campaign=` deep links) still resolve.
@bp.get("/")
def index():
    return _to_studio()


@bp.get("/library")
def library():
    return _to_studio()


@bp.get("/campaigns")
def campaigns_page():
    return _to_studio()


@bp.get("/studio")
def studio():
    """The one workspace shell: Editor + Library + Campaigns as sibling sections of
    ONE document (no iframes), with a rail, native cross-panel drag, and live shared
    state. Reads `?edit=`/`?campaign=` to open a specific cut/campaign."""
    return render_template("studio.html")


@bp.get("/bundle/<panel>.js")
def panel_bundle(panel):
    files = PANEL_BUNDLES.get(panel)
    if not files:
        return err("unknown bundle", 404)
    # common.js first in every bundle: defines the shared window.api/esc/pollJob the
    # per-panel files rely on (each bundle is its own IIFE, so they can't otherwise
    # see helpers defined in a sibling bundle).
    parts = []
    for name in ["common.js", *files]:
        path = STATIC_DIR / name
        parts.append(f"// ===== {name} =====\n{path.read_text()}")
    body = "(function () {\n" + "\n".join(parts) + "\n})();\n"
    # no-cache so edits during dev show up without a hard refresh
    resp = Response(body, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    return resp
