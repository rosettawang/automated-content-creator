"""Beat detection for trending-audio compose (spec: specs/audio-design.md, Phase 4).

A compact, dependency-light beat tracker (numpy + scipy + ffmpeg decode — no librosa):
decode → spectral-flux onset envelope → tempo via autocorrelation → phase-align → an
even beat grid. An even grid is exactly what we want to cut on, and it's robust for the
strongly-rhythmic tracks trending audio tends to be.

Leaf module: depends only on numpy/scipy/subprocess, so it sits below the feature
modules and is safe to import from a blueprint.
"""
from __future__ import annotations

import logging
import subprocess

import numpy as np

log = logging.getLogger("editor.audio_beats")

_SR = 22050        # decode sample rate
_HOP = 512         # onset-envelope hop (≈23ms frames)
_WIN = 1024
_MIN_BPM, _MAX_BPM = 60.0, 190.0


def _decode_mono(path: str, start: float, max_seconds: float) -> np.ndarray:
    """Decode `max_seconds` of mono float32 PCM from `start` via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(max(0.0, start)), "-i", str(path),
           "-ac", "1", "-ar", str(_SR), "-t", str(max_seconds), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def _onset_envelope(y: np.ndarray) -> np.ndarray:
    """Spectral flux: sum of positive frame-to-frame magnitude increases."""
    if y.size < _WIN:
        return np.zeros(0, dtype=np.float32)
    n_frames = 1 + (len(y) - _WIN) // _HOP
    window = np.hanning(_WIN).astype(np.float32)
    mags = np.empty((n_frames, _WIN // 2 + 1), dtype=np.float32)
    for i in range(n_frames):
        frame = y[i * _HOP: i * _HOP + _WIN] * window
        mags[i] = np.abs(np.fft.rfft(frame))
    flux = np.maximum(0.0, np.diff(mags, axis=0)).sum(axis=1)
    # Normalize so autocorrelation/phase are amplitude-independent.
    if flux.max() > 0:
        flux = flux / flux.max()
    return flux.astype(np.float32)


def detect_beats(path: str, start: float = 0.0, max_seconds: float = 90.0) -> list[float]:
    """Beat times (seconds, relative to `start`) as an even grid at the detected tempo.
    Returns [] if the track is too short/quiet to find a tempo."""
    try:
        y = _decode_mono(path, start, max_seconds)
    except Exception as e:
        log.warning("beat decode failed for %s: %s", path, e)
        return []
    env = _onset_envelope(y)
    if env.size < 8:
        return []

    fps = _SR / _HOP
    min_lag = int(round(fps * 60.0 / _MAX_BPM))
    max_lag = int(round(fps * 60.0 / _MIN_BPM))
    max_lag = min(max_lag, env.size - 1)
    if max_lag <= min_lag:
        return []

    # Tempo via the onset-envelope autocorrelation over the BPM band. Two guards against
    # the classic octave error (locking onto half/quarter tempo):
    #  1. Smooth the envelope first — a beat period rarely lands on a whole number of
    #     frames, which SPLITS the fundamental peak and makes the octave lag look
    #     sharper; smoothing merges the split.
    #  2. Octave correction — if a sub-multiple lag (½, ⅓) still correlates strongly,
    #     prefer it (the faster, base tempo).
    from scipy.ndimage import gaussian_filter1d
    sm = gaussian_filter1d(env.astype(np.float64), sigma=1.0)
    e = sm - sm.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    band = ac[min_lag:max_lag + 1]
    if band.size == 0 or band.max() <= 0:
        return []
    period = min_lag + int(np.argmax(band))
    for div in (2, 3):
        sub = period // div
        if sub >= min_lag and ac[sub] >= 0.55 * ac[period]:
            period = sub
    if period <= 0:
        return []

    # Phase: the offset (0..period) whose beat comb best matches the onsets.
    scores = [env[off::period].sum() for off in range(period)]
    phase = int(np.argmax(scores))

    dur = len(y) / _SR
    beats = []
    k = 0
    while True:
        t = (phase + k * period) * _HOP / _SR
        if t >= dur:
            break
        beats.append(round(float(t), 3))
        k += 1
    return beats


def estimate_bpm(beats: list[float]) -> float | None:
    """Median-interval BPM from a beat list (for display / sanity)."""
    if len(beats) < 2:
        return None
    diffs = np.diff(beats)
    med = float(np.median(diffs))
    return round(60.0 / med, 1) if med > 0 else None
