"""Shared configuration: env, paths, media extensions.

The leaf of the import graph — no imports from core or the feature modules, so
everything can depend on it without cycles. Loads `.env` itself (idempotent) so
`MEDIA_DIR` resolves the same no matter who imports this first.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

MEDIA_DIR_RAW = os.environ.get("MEDIA_DIR", "").strip()
MEDIA_DIR = Path(MEDIA_DIR_RAW).expanduser() if MEDIA_DIR_RAW else None

ON_DEVICE_VISION_DEFAULT = os.environ.get("ON_DEVICE_VISION", "1") != "0"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tiff", ".webp"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIPS_OUT = REPO_ROOT / "clips_out"
REFERENCE_FRAMES = REPO_ROOT / "reference_frames"
THUMB_CACHE = Path(__file__).resolve().parent / "data" / "thumbs"
FACES_DIR = Path(__file__).resolve().parent / "data" / "faces"
PROXY_CACHE = Path(__file__).resolve().parent / "data" / "proxies"
# Local trending-audio scratch tracks (Phase 4): reference-only, never exported.
REF_AUDIO_DIR = Path(__file__).resolve().parent / "data" / "ref_audio"
# User-supplied music library (audio-design Phase 2): drop tracks here (optionally with
# a <stem>.json {mood, tags} sidecar); the model picks one for 'music' mode. Your own /
# licensed files only — this bakes into the export.
MUSIC_DIR = REPO_ROOT / "music"


def classify_kind(path: Path) -> str:
    """'photo' for image files, 'video' otherwise (by file extension)."""
    return "photo" if path.suffix.lower() in IMAGE_EXTS else "video"


__all__ = [
    "MEDIA_DIR_RAW", "MEDIA_DIR", "ON_DEVICE_VISION_DEFAULT",
    "VIDEO_EXTS", "IMAGE_EXTS", "MEDIA_EXTS", "classify_kind",
    "REPO_ROOT", "CLIPS_OUT", "REFERENCE_FRAMES", "THUMB_CACHE",
    "FACES_DIR", "PROXY_CACHE", "REF_AUDIO_DIR", "MUSIC_DIR",
]
