"""Local, free semantic search over clips.

Embeds each clip's text (description + category + tags + transcript + scene
timeline) with a small on-device sentence-transformer, so a natural-language
prompt retrieves clips by meaning rather than keyword overlap. No per-query API
cost -- this is the "on-device/free" tier the plan calls for (phase 5).
"""
from __future__ import annotations

import struct
import threading

import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384

_model = None
_model_lock = threading.Lock()


def get_model():
    """Lazy-load the embedding model once (a few seconds on first use)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Embed one string to a normalized float32 vector."""
    vec = get_model().encode([text or ""], normalize_embeddings=True)[0]
    return np.asarray(vec, dtype=np.float32)


def vec_to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def bytes_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def rank(query_vec: np.ndarray, items: list[tuple[int, bytes]], top_k: int) -> list[tuple[int, float]]:
    """Cosine-rank stored vectors against the query. `items` is (clip_id, blob).
    Vectors are already normalized, so cosine == dot product. Returns
    [(clip_id, score)] sorted high→low, truncated to top_k."""
    if not items:
        return []
    ids = [i for i, _ in items]
    mat = np.stack([bytes_to_vec(b) for _, b in items])  # (N, dim)
    scores = mat @ query_vec  # (N,)
    order = np.argsort(-scores)[:top_k]
    return [(ids[i], float(scores[i])) for i in order]
