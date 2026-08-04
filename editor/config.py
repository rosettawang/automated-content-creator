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

# ---- AI provider (spec: ai-provider-switch) --------------------------------
# Which cloud model provider serves structured calls. 'claude' (Anthropic) or
# 'kimi' (Moonshot, OpenAI-wire-compatible). Runtime-switchable in the Things
# panel; this is only the default when the setting is unset.
AI_PROVIDER_DEFAULT = (os.environ.get("AI_PROVIDER", "claude").strip().lower()
                       or "claude")
# Decision D1: kimi-k3 is the recommended default (1M context, vision, strict
# JSON-schema structured outputs); kimi-k2.6 is the cheaper 256K fallback.
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k3").strip() or "kimi-k3"
# Decision D4: use the official openai SDK pointed at Moonshot's base URL.
MOONSHOT_BASE_URL = (os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").strip()
                     or "https://api.moonshot.ai/v1")
MOONSHOT_API_KEY = os.environ.get("MOONSHOT_API_KEY", "").strip()

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
    "AI_PROVIDER_DEFAULT", "KIMI_MODEL", "MOONSHOT_BASE_URL", "MOONSHOT_API_KEY",
    "VIDEO_EXTS", "IMAGE_EXTS", "MEDIA_EXTS", "classify_kind",
    "REPO_ROOT", "CLIPS_OUT", "REFERENCE_FRAMES", "THUMB_CACHE",
    "FACES_DIR", "PROXY_CACHE", "REF_AUDIO_DIR", "MUSIC_DIR",
]
