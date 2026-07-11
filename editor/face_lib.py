"""On-device face detection + recognition (facenet-pytorch).

Everything runs locally: MTCNN finds faces, InceptionResnetV1 (vggface2) turns each
into a 512-d embedding. Faces never leave the machine. The model is loaded lazily so
importing this module (and starting the app) stays cheap when faces aren't used."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Detection confidence floor -- MTCNN emits low-prob boxes for partial/background
# faces; below this we ignore them to keep embeddings meaningful.
MIN_PROB = 0.92

# Cosine-similarity threshold for "same person" with vggface2 embeddings. Same
# person typically lands well above this; different people below.
SAME_THRESHOLD = 0.55

_mtcnn = None
_resnet = None


def _load():
    global _mtcnn, _resnet
    if _resnet is None:
        from facenet_pytorch import MTCNN, InceptionResnetV1
        _mtcnn = MTCNN(keep_all=True, device="cpu", post_process=True)
        _resnet = InceptionResnetV1(pretrained="vggface2").eval()
    return _mtcnn, _resnet


def detect_faces(image_bytes: bytes) -> list[dict]:
    """Detect faces in an image. Returns a list of
        {box:[x1,y1,x2,y2], prob:float, embedding:np.float32[512], crop:PIL.Image}
    one per face at or above MIN_PROB (empty if none)."""
    import torch

    mtcnn, resnet = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    boxes, probs = mtcnn.detect(img)
    if boxes is None:
        return []
    aligned = mtcnn(img)  # tensor [n,3,160,160], aligned crops in box order
    if aligned is None:
        return []
    with torch.no_grad():
        embs = resnet(aligned).numpy().astype("float32")

    out: list[dict] = []
    for i, (box, prob) in enumerate(zip(boxes, probs)):
        if prob is None or prob < MIN_PROB:
            continue
        x1, y1, x2, y2 = (int(v) for v in box)
        crop = img.crop((max(0, x1), max(0, y1), max(x1 + 1, x2), max(y1 + 1, y2)))
        out.append({
            "box": [x1, y1, x2, y2],
            "prob": float(prob),
            "embedding": embs[i],
            "crop": crop,
        })
    return out


# ---- embedding (de)serialization for the BLOB column ----
def emb_to_bytes(emb: np.ndarray) -> bytes:
    return np.asarray(emb, dtype="float32").tobytes()


def emb_from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="float32")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


def cluster(embeddings: list[np.ndarray], threshold: float = SAME_THRESHOLD) -> list[int]:
    """Greedy single-pass clustering by cosine similarity to running cluster
    centroids. Returns a cluster index per input embedding (parallel list)."""
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    labels: list[int] = []
    for emb in embeddings:
        best, best_sim = -1, -1.0
        for ci, c in enumerate(centroids):
            s = cosine(emb, c)
            if s > best_sim:
                best_sim, best = s, ci
        if best_sim >= threshold:
            n = counts[best]
            centroids[best] = (centroids[best] * n + emb) / (n + 1)
            counts[best] += 1
            labels.append(best)
        else:
            centroids.append(np.array(emb, dtype="float32"))
            counts.append(1)
            labels.append(len(centroids) - 1)
    return labels
