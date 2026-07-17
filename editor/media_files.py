"""Media-file operations: probe, proxy, quality, location, and frame sampling.

Everything that reads or derives from the actual media files on disk. A leaf module
depending only on config + db + jobs_runtime, so indexing/export can build on it
without cycles.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import (
    MEDIA_DIR, VIDEO_EXTS, MEDIA_EXTS, REFERENCE_FRAMES, PROXY_CACHE,
)
from db import get_conn
from jobs_runtime import _update_job
import logging

log = logging.getLogger("editor.media")

_proxy_jobs: set[str] = set()


_proxy_lock = threading.Lock()


def _proxy_path_for(stem: str) -> Path:
    return PROXY_CACHE / f"{stem}.mp4"


def _generate_proxy(path: Path) -> Path | None:
    """Build a web-safe H.264/AAC faststart MP4 for a local video.

    H.264 sources are *remuxed* (`-c:v copy`) — near-instant and lossless; anything
    else (HEVC, etc.) is transcoded to H.264 once. Only the video + first audio track
    are mapped, so timecode/metadata data-streams that MP4 can't hold don't abort the
    copy. Idempotent: skips when a fresh proxy already exists. Returns the proxy path
    or None on failure."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return None
    out = _proxy_path_for(path.stem)
    try:
        if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            return out
    except OSError:
        pass  # can't stat cache/source -> treat as stale and (re)generate below
    PROXY_CACHE.mkdir(parents=True, exist_ok=True)

    try:
        raw = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        # ffmpeg 8's csv writer emits a trailing empty field ("h264,"); take the
        # first token so the codec compare below actually matches.
        codec = raw.split(",")[0].strip().lower()
    except Exception:
        codec = ""

    video_args = ["-c:v", "copy"] if codec == "h264" else \
        ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    tmp = out.with_suffix(".partial.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args, "-c:a", "aac",
        "-movflags", "+faststart", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(out)
        return out
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _ensure_proxy_async(path: Path) -> None:
    """Kick off proxy generation in a daemon thread if one isn't already running and
    a fresh proxy doesn't already exist. Never blocks the caller (request path)."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return
    stem = path.stem
    proxy = _proxy_path_for(stem)
    try:
        if proxy.exists() and proxy.stat().st_mtime >= path.stat().st_mtime:
            return
    except OSError:
        pass  # can't stat cache/source -> treat as stale and (re)generate below
    with _proxy_lock:
        if stem in _proxy_jobs:
            return
        _proxy_jobs.add(stem)

    def _work():
        try:
            _generate_proxy(path)
        finally:
            with _proxy_lock:
                _proxy_jobs.discard(stem)

    threading.Thread(target=_work, daemon=True).start()


def _probe_dims(path: Path) -> tuple[int | None, int | None]:
    """Display resolution (rotation-aware) via ffprobe: (width, height)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_side_data=rotation",
             "-of", "default=nw=1", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        vals = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
        w = int(vals.get("width", 0)) or None
        h = int(vals.get("height", 0)) or None
        rot = abs(int(vals.get("rotation", "0") or 0))
        if rot in (90, 270) and w and h:
            w, h = h, w  # rotated video displays with swapped dims
        return w, h
    except Exception:
        return None, None


def _laplacian_variance(gray) -> float:
    """Variance of the Laplacian (focus measure) — higher = sharper/crisper."""
    import numpy as np
    g = gray.astype("float64")
    lap = (-4 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def _measure_quality(path: Path) -> tuple[int | None, int | None, float | None, int | None]:
    """Measure technical quality on-device: (width, height, sharpness, quality).

    Resolution comes from ffprobe; sharpness is variance-of-Laplacian on a frame
    normalized to 512px wide (so it's comparable across clips). `quality` is a
    0-100 heuristic blending resolution and sharpness — informational only, it
    never auto-excludes anything."""
    import math
    import numpy as np
    from PIL import Image

    w, h = _probe_dims(path)
    sharpness = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "q.jpg"
            # A frame ~1s in (or the still itself), normalized to 512 wide.
            cmd = ["ffmpeg", "-y", "-ss", "1", "-i", str(path),
                   "-frames:v", "1", "-vf", "scale=512:-1", str(fp)]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not fp.exists():
                # stills / very short clips: no seek
                subprocess.run(["ffmpeg", "-y", "-i", str(path),
                                "-frames:v", "1", "-vf", "scale=512:-1", str(fp)],
                               capture_output=True)
            if fp.exists():
                gray = np.asarray(Image.open(fp).convert("L"))
                if gray.size:
                    sharpness = round(_laplacian_variance(gray), 1)
    except Exception as _e:
        log.warning("%s: %s", "_measure_quality", _e)

    quality = None
    if w and h:
        shortest = min(w, h)
        res_score = 40 if shortest >= 1080 else 30 if shortest >= 720 else 18 if shortest >= 480 else 8
        if sharpness is not None:
            # log-scaled: ~2000 var maps to full marks; clamp 0..1.
            sharp_score = max(0.0, min(1.0, math.log10(sharpness + 1) / math.log10(2000))) * 60
        else:
            sharp_score = 0
        quality = max(0, min(100, round(res_score + sharp_score)))
    return w, h, sharpness, quality


def find_media_file(file_stem: str) -> Path | None:
    if MEDIA_DIR is None or not MEDIA_DIR.is_dir():
        return None
    matches = list(MEDIA_DIR.glob(f"{file_stem}.*"))
    return matches[0] if matches else None


def _media_roots() -> list[Path]:
    """Folders to search for media. MEDIA_DIR today; extensible to a list later."""
    return [MEDIA_DIR] if (MEDIA_DIR and MEDIA_DIR.is_dir()) else []


def clip_media_status(row) -> tuple[str, str | None]:
    """Cheap resolve (no hashing): returns (status, path). Prefers the stored
    media_path, falls back to a stem glob. Used on every read, so it stays fast."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)
    mp = get("media_path")
    if mp and Path(mp).exists():
        return "present", mp
    local = find_media_file(row["file_stem"])
    if local:
        return "present", str(local)
    if mp:
        return "missing", None      # had a path once → it moved or was deleted
    return "absent", None           # never local on this machine


def _walk_media_files():
    for root in _media_roots():
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                yield p


def _set_media(conn, clip_id: int, path: str | None, status: str) -> None:
    conn.execute(
        "UPDATE clips SET media_path=?, media_status=?, media_checked_at=? WHERE id=?",
        (path, status, datetime.now(timezone.utc).isoformat(), clip_id),
    )


def _run_media_verify_job(job_id: str) -> None:
    """Reconcile every clip against disk. Broken paths are re-found by content hash
    across the media roots and relinked; the rest are recorded as missing."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, file_stem, content_hash, media_path FROM clips"
    ).fetchall()
    _update_job(job_id, total=len(rows), phase="checking")

    present = relocated = 0
    broken = []  # rows whose file isn't where we expect
    for i, r in enumerate(rows):
        status, path = clip_media_status(r)
        if status == "present":
            _set_media(conn, r["id"], path, "present")
            present += 1
        else:
            broken.append(r)
        _update_job(job_id, done=i + 1, current=r["file_stem"])
    conn.commit()

    # Relink phase: hash files under the roots and match broken clips by content hash.
    # Only runs when something is actually broken, so the common case pays nothing.
    by_hash = {r["content_hash"]: r for r in broken if r["content_hash"]}
    if by_hash:
        _update_job(job_id, phase="relinking", current="scanning for moved files…")
        for f in _walk_media_files():
            if not by_hash:
                break
            try:
                h = _hash_file(f)
            except Exception:
                continue
            r = by_hash.pop(h, None)
            if r:
                _set_media(conn, r["id"], str(f), "relocated")
                relocated += 1
        conn.commit()

    # Anything still unresolved is genuinely missing (or never was local).
    missing = []
    for r in broken:
        cur = conn.execute("SELECT media_status, media_path FROM clips WHERE id=?", (r["id"],)).fetchone()
        if cur["media_status"] == "relocated" and cur["media_path"]:
            continue
        new_status = "missing" if r["media_path"] else "absent"
        _set_media(conn, r["id"], None, new_status)
        if new_status == "missing":
            missing.append({"id": r["id"], "file_stem": r["file_stem"], "last_path": r["media_path"]})
    conn.commit()
    conn.close()
    _update_job(job_id, finished=True, current=None, phase="done",
                results=[{"status": "verified", "present": present,
                          "relocated": relocated, "missing": missing}])


def find_reference_frame(file_stem: str) -> Path | None:
    if not REFERENCE_FRAMES.is_dir():
        return None
    matches = sorted(
        p for p in REFERENCE_FRAMES.iterdir()
        if file_stem in p.name and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    return matches[0] if matches else None


def _hash_file(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed so large videos don't load into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _scene_change_times(path: Path, duration: float, thresh: float = 0.4) -> list[float]:
    """Timestamps of significant visual change, from ffmpeg scene detection — one
    local decode pass, free. This is the 'interesting-ness' signal used to sample
    more densely where the picture changes and sparsely where it's static."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path), "-vf",
             f"select='gt(scene,{thresh})',showinfo", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        times = []
        for m in re.finditer(r"pts_time:([0-9.]+)", r.stderr):
            t = float(m.group(1))
            if 0.0 < t < duration:
                times.append(round(t, 1))
        return sorted(set(times))
    except Exception:
        return []


def _motion_times(path: Path, duration: float, sample_fps: float = 2.0,
                  top_frac: float = 0.35, min_diff: float = 3.0) -> list[float]:
    """High-motion timestamps via frame differencing — the free 'interest' signal
    for continuous footage (where scene-cut detection finds nothing). Extracts tiny
    grayscale frames and returns the times of the largest frame-to-frame changes."""
    import numpy as np
    from PIL import Image
    n = int(duration * sample_fps) + 2
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "m%04d.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(path), "-vf",
                 f"fps={sample_fps},scale=64:36,format=gray", "-frames:v", str(n), str(out)],
                check=True, capture_output=True,
            )
        except Exception:
            return []
        files = sorted(Path(tmp).glob("m*.jpg"))
        if len(files) < 3:
            return []
        arrs = [np.asarray(Image.open(f), dtype="float32") for f in files]
    diffs = [float(np.abs(arrs[i] - arrs[i - 1]).mean()) for i in range(1, len(arrs))]
    times = [i / sample_fps for i in range(1, len(arrs))]
    if not diffs:
        return []
    order = sorted(range(len(diffs)), key=lambda i: -diffs[i])
    k = max(1, int(len(diffs) * top_frac))
    return sorted(round(times[i], 1) for i in order[:k]
                  if diffs[i] >= min_diff and 0.0 < times[i] < duration)


def _interest_times(path: Path, duration: float) -> list[float]:
    """Free, on-device signal for where a clip is 'more interesting': hard cuts
    (scene detection) plus within-shot motion peaks (frame differencing)."""
    try:
        from PIL import Image  # noqa: F401  (import guard: numpy/PIL present)
        return sorted(set(_scene_change_times(path, duration)) |
                      set(_motion_times(path, duration)))
    except Exception:
        return _scene_change_times(path, duration)


def _sample_frames(path: Path, duration: float, baseline_every: float = 6.0,
                   max_frames: int = 20) -> list[tuple[float, bytes]]:
    """Adaptive frame sampling for the deep-index pass: a sparse uniform BASELINE
    (cheap coverage of dull footage) PLUS extra frames at points of visual change
    (denser detail where it's interesting). The 'interest' signal is free ffmpeg
    scene detection, so we don't spend Claude tokens deciding where to spend them.
    Returns [(timestamp_seconds, jpeg_bytes), ...] in time order."""
    # Sparse uniform baseline so even a static clip is covered start-to-end.
    n_base = max(1, int(duration // baseline_every))
    baseline = {round(i * duration / n_base, 1) for i in range(n_base + 1)}
    baseline.add(round(max(0.0, duration - 0.3), 1))
    baseline = {t for t in baseline if 0.0 <= t < duration}

    changes = _interest_times(path, duration)  # free interest signal (cuts + motion)
    times = sorted(baseline | set(changes))

    # Cap total frames: always keep the baseline; thin the change points evenly.
    if len(times) > max_frames:
        extras = [t for t in times if t not in baseline]
        room = max(0, max_frames - len(baseline))
        if room and extras:
            step = len(extras) / room
            kept_extras = {extras[int(i * step)] for i in range(room)}
        else:
            kept_extras = set()
        times = sorted(baseline | kept_extras)[:max_frames]

    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            fp = Path(tmp) / f"f{i:04d}.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(t), "-i", str(path),
                     "-frames:v", "1", "-vf", "scale=768:-2", str(fp)],
                    check=True, capture_output=True,
                )
            except Exception:
                continue
            if fp.exists():
                frames.append((t, fp.read_bytes()))
    if not frames:
        raise RuntimeError("no frames extracted")
    return frames


def _frame_at(source: Path, ts: float) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(max(0.0, ts)), "-i", str(source),
             "-frames:v", "1", str(frame_path)],
            check=True, capture_output=True,
        )
        return frame_path.read_bytes()


def _source_dims(path: Path) -> tuple[int, int] | None:
    """Source pixel width,height via ffprobe (rotation-aware). None on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip().split("x")
        return int(out[0]), int(out[1])
    except Exception:
        return None


def _probe_fps(path: Path) -> float:
    """Average source fps via ffprobe (falls back to 30)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        num, den = out.split("/")
        return (float(num) / float(den)) if float(den) else 30.0
    except Exception:
        return 30.0


def _has_audio(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return bool(out)
    except Exception:
        return False


def _display_dims(path: Path) -> tuple[int, int] | None:
    """Rotation-corrected display width,height: if the stream carries a 90/270°
    rotation, the on-screen frame is the coded dimensions swapped. This is why an
    iPhone portrait clip (coded 1920x1080 + rotation) must be treated as 1080x1920."""
    import json as _json
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_tags=rotate:side_data=rotation",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        info = _json.loads(out)["streams"][0]
        w, h = int(info["width"]), int(info["height"])
        rot = 0
        if info.get("tags", {}).get("rotate"):
            rot = int(info["tags"]["rotate"])
        for sd in info.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return None


__all__ = [n for n in dir() if not n.startswith("__")]
