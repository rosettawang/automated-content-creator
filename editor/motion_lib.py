"""On-device action/motion recognition with X-CLIP (microsoft/xclip-base-patch32).

X-CLIP is CLIP extended to video: it samples 8 frames from a window and scores them
against text phrases, so it reasons about *motion* ("pouring oil", "planting a seed")
rather than a single still. Runs locally on CPU/MPS; model (~x00 MB) downloads once.
Loaded lazily so app startup stays cheap."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image

MODEL_ID = "microsoft/xclip-base-patch32"
NUM_FRAMES = 8  # patch32 variant expects 8 frames per clip window

# A window is tagged with an action when its softmax prob clears this and beats the
# "nothing" negatives below. Tunable.
ACTION_THRESHOLD = 0.5

# Generic negatives so a window with no real action isn't forced onto a label
# (X-CLIP softmax is relative to the provided set).
_NEGATIVES = ["a still scene with no motion", "nothing happening", "an unrelated activity"]

_processor = None
_model = None


def _load():
    global _processor, _model
    if _model is None:
        from transformers import AutoProcessor, AutoModel
        _processor = AutoProcessor.from_pretrained(MODEL_ID)
        _model = AutoModel.from_pretrained(MODEL_ID).eval()
    return _processor, _model


def extract_frames(path: Path, t0: float, t1: float, n: int = NUM_FRAMES) -> list[Image.Image]:
    """Pull n evenly-spaced frames from the [t0, t1] window of a video."""
    dur = max(0.1, t1 - t0)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f%03d.jpg"
        # fps chosen so ~n frames land inside the window; then take the first n.
        fps = max(1.0, n / dur)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t0), "-t", str(dur), "-i", str(path),
             "-vf", f"fps={fps},scale=224:224", "-frames:v", str(n), str(out)],
            check=True, capture_output=True,
        )
        frames = [Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("f*.jpg"))]
    if not frames:
        return []
    # Pad by repeating the last frame if ffmpeg produced fewer than n.
    while len(frames) < n:
        frames.append(frames[-1])
    return frames[:n]


def score_window(frames: list[Image.Image], labels: list[str]) -> dict[str, float]:
    """Softmax probability of each label for this window (over labels + negatives)."""
    import torch

    processor, model = _load()
    all_labels = labels + _NEGATIVES
    inputs = processor(text=all_labels, videos=[frames], return_tensors="pt", padding=True)
    with torch.no_grad():
        out = model(**inputs)
    probs = out.logits_per_video.softmax(dim=1)[0].tolist()
    return {lab: float(p) for lab, p in zip(all_labels, probs)}


def detect_actions(path: Path, duration: float, labels: list[str],
                   window: float = 4.0, threshold: float = ACTION_THRESHOLD) -> list[dict]:
    """Slide fixed windows across a clip; for each, keep any label that clears the
    threshold and outscores the negatives. Returns [{label, t_start, t_end, score}]."""
    if not labels or duration <= 0:
        return []
    neg = set(_NEGATIVES)
    events: list[dict] = []
    t = 0.0
    # For a very short clip, score it as a single window.
    step = window if duration > window else max(duration, 0.5)
    while t < duration - 0.05:
        t1 = min(t + window, duration)
        frames = extract_frames(path, t, t1)
        if frames:
            scores = score_window(frames, labels)
            best = max(scores, key=scores.get)
            if best not in neg and scores[best] >= threshold:
                events.append({"label": best, "t_start": round(t, 2),
                               "t_end": round(t1, 2), "score": round(scores[best], 3)})
        t += step
    return _merge_adjacent(events)


def _merge_adjacent(events: list[dict]) -> list[dict]:
    """Merge consecutive windows with the same label into one span."""
    merged: list[dict] = []
    for e in events:
        if merged and merged[-1]["label"] == e["label"] and e["t_start"] <= merged[-1]["t_end"] + 0.05:
            merged[-1]["t_end"] = e["t_end"]
            merged[-1]["score"] = max(merged[-1]["score"], e["score"])
        else:
            merged.append(dict(e))
    return merged
