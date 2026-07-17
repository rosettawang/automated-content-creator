"""Text-to-speech adapters (spec: specs/audio-design.md, Phase 3).

One swappable interface, engine chosen at call time. OpenAI TTS is the economical
high-quality default when OPENAI_API_KEY is set; a local engine (Kokoro/Piper) can be
added behind the same `synthesize()` later. All network calls stay in this module, and
we hit the REST endpoint directly (no extra SDK dependency).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger("editor.tts")

# Cheap, natural, fine for social VO. Overridable via env without a code change.
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")
OPENAI_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")


class TTSUnavailable(RuntimeError):
    """No usable TTS engine (e.g. OPENAI_API_KEY missing) — surfaced to the UI, not swallowed."""


def openai_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def available_engine() -> str | None:
    """Which engine we'd use right now (only OpenAI for Phase 3; local added later)."""
    return "openai" if openai_available() else None


def synthesize_openai(text: str, out_path, voice: str | None = None,
                      model: str | None = None) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise TTSUnavailable("OPENAI_API_KEY is not set — add it to editor/.env.")
    text = (text or "").strip()
    if not text:
        raise TTSUnavailable("empty voiceover script — nothing to synthesize.")
    voice = voice or OPENAI_TTS_VOICE
    if voice not in OPENAI_VOICES:
        voice = OPENAI_TTS_VOICE
    body = json.dumps({
        "model": model or OPENAI_TTS_MODEL,
        "voice": voice,
        "input": text,
        "response_format": "mp3",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise TTSUnavailable(f"OpenAI TTS failed ({e.code}): {detail}") from e
    Path(out_path).write_bytes(audio)
    return str(out_path)


def synthesize(text: str, out_path, engine: str | None = None,
               voice: str | None = None) -> str:
    """Synthesize `text` to `out_path` (mp3). Picks OpenAI when available; raises
    TTSUnavailable if no engine is configured, so the caller can fail loudly."""
    engine = engine or available_engine()
    if engine == "openai":
        return synthesize_openai(text, out_path, voice=voice)
    raise TTSUnavailable(
        "No TTS engine available. Set OPENAI_API_KEY in editor/.env (or add a local engine)."
    )
