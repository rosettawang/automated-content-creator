from flask import Blueprint
from core import *

bp = Blueprint("jobs", __name__)


@bp.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    """Request cancellation of a running job and kill its current subprocess."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return {"error": "unknown job"}, 404
        if job["finished"]:
            return jsonify({"cancelled": False, "reason": "already finished"})
        job["cancelled"] = True
        _job_flush(job)
        proc = job.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()  # SIGTERM: ffmpeg exits cleanly within a moment
        # Escalate to SIGKILL if it ignores SIGTERM, so the worker's blocking
        # communicate() can't hang forever. Non-blocking so this request returns now.
        def _sigkill_fallback(p):
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if p.poll() is None:
                    p.kill()
        threading.Thread(target=_sigkill_fallback, args=(proc,), daemon=True).start()
    return jsonify({"cancelled": True, "job_id": job_id})


@bp.get("/api/import-jobs/<job_id>")
def import_job(job_id):
    snap = _job_snapshot(job_id)
    if snap is None:
        return {"error": "unknown job"}, 404
    return jsonify(snap)


@bp.get("/api/settings")
def get_settings():
    return jsonify({"on_device_vision": _use_on_device()})


@bp.post("/api/settings")
def update_settings():
    data = request.json or {}
    if "on_device_vision" in data:
        _set_setting("on_device_vision", "1" if data["on_device_vision"] else "0")
    return jsonify({"on_device_vision": _use_on_device()})


@bp.get("/api/env")
def env_info():
    """Small capability probe the UI uses to show/hide the 'stamp to file' option."""
    return jsonify({
        "exiftool": exiftool_available(),
        "media_dir_set": MEDIA_DIR is not None and MEDIA_DIR.is_dir(),
    })
