from flask import Blueprint
from core import *

bp = Blueprint("jobs", __name__)


@bp.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    """Request cancellation of a running job and kill its current subprocess."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return err("unknown job", 404)
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
        return err("unknown job", 404)
    return jsonify(snap)


def _settings_payload():
    from config import MOONSHOT_API_KEY, KIMI_MODEL
    import os
    return {"on_device_vision": _use_on_device(),
            "export_frame_check": _use_export_frame_check(),
            "ai_provider": _ai_provider(),
            # So the UI can warn before the switch fails at call time.
            "kimi_key_present": bool((os.environ.get("MOONSHOT_API_KEY") or MOONSHOT_API_KEY or "").strip()),
            "kimi_model": KIMI_MODEL}


@bp.get("/api/settings")
def get_settings():
    return jsonify(_settings_payload())


@bp.post("/api/settings")
def update_settings():
    data = request.json or {}
    if "on_device_vision" in data:
        _set_setting("on_device_vision", "1" if data["on_device_vision"] else "0")
    # Framing v2 Stage 5: opt-in, since each export then costs ~1 vision call/segment.
    if "export_frame_check" in data:
        _set_setting("export_frame_check", "1" if data["export_frame_check"] else "0")
    # AI provider: one global switch for every structured model call.
    if "ai_provider" in data:
        want = str(data.get("ai_provider") or "").strip().lower()
        if want not in ("claude", "kimi"):
            return {"error": "ai_provider must be 'claude' or 'kimi'"}, 400
        _set_setting("ai_provider", want)
    return jsonify(_settings_payload())


@bp.get("/api/env")
def env_info():
    """Small capability probe the UI uses to show/hide the 'stamp to file' option."""
    return jsonify({
        "exiftool": exiftool_available(),
        "media_dir_set": MEDIA_DIR is not None and MEDIA_DIR.is_dir(),
    })
