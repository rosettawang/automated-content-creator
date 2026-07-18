"""User music library for 'music' audio mode (spec: specs/audio-design.md, Phase 2).

Scans `config.MUSIC_DIR` for audio files the user has supplied (their own or licensed).
Each track may carry a tiny sidecar `<stem>.json` = {"mood": "...", "tags": [...]}; a
missing sidecar falls back to the filename as the mood text. `match_track(mood)` picks
the best track by word overlap so the model's `music_mood` selects a bed.

Leaf module: depends only on `config` (+ stdlib), so blueprints and export can use it
without a cycle.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import config

log = logging.getLogger("editor.music_lib")

_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w}


def list_tracks() -> list[dict]:
    """Every usable track in MUSIC_DIR: {name, path, mood, tags}. Empty if the folder
    doesn't exist (music mode then just falls back to ambient on export)."""
    d = config.MUSIC_DIR
    if not d or not Path(d).is_dir():
        return []
    tracks = []
    for p in sorted(Path(d).iterdir()):
        if p.suffix.lower() not in _AUDIO_EXTS:
            continue
        mood, tags = "", []
        sidecar = p.with_suffix(p.suffix + ".json")
        alt = p.with_suffix(".json")
        for sc in (sidecar, alt):
            if sc.exists():
                try:
                    meta = json.loads(sc.read_text())
                    mood = (meta.get("mood") or "").strip()
                    tags = [str(t) for t in (meta.get("tags") or [])]
                except Exception as e:
                    log.warning("bad music sidecar %s: %s", sc, e)
                break
        tracks.append({
            "name": p.stem,
            "path": str(p),
            # Fall back to the filename as mood text so bare files still match.
            "mood": mood or p.stem.replace("_", " ").replace("-", " "),
            "tags": tags,
        })
    return tracks


def match_track(mood: str, tracks: list[dict] | None = None) -> str | None:
    """Best-matching track path for a `music_mood` string, by word overlap against each
    track's mood+tags. Falls back to the first track when nothing overlaps (so 'music'
    mode still gets a bed); None only when the library is empty."""
    tracks = tracks if tracks is not None else list_tracks()
    if not tracks:
        return None
    want = _words(mood)
    if not want:
        return tracks[0]["path"]
    best, best_score = None, -1
    for t in tracks:
        have = _words(t["mood"]) | _words(" ".join(t["tags"]))
        score = len(want & have)
        if score > best_score:
            best, best_score = t, score
    return (best or tracks[0])["path"]
