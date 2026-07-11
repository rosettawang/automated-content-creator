"""On-device image understanding with CLIP (open_clip). Replaces the paid
frame-analysis for watchlist detection + category, so per-clip indexing is free.

Zero-shot: encode the frame and candidate text prompts into one space and compare.
CLIP can't write prose, so the "description" is a terse local summary composed from
the category + detected subjects. Model (~350 MB) downloads once; lazy-loaded."""
from __future__ import annotations

import io

from PIL import Image

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

# Per-thing presence threshold: softmax prob of "contains X" vs negatives.
THING_THRESHOLD = 0.55

# Default category vocabulary (extended at call time with the library's own labels).
DEFAULT_CATEGORIES = [
    "Gardening", "Wildlife", "Habitat", "Plant", "Caterpillar", "Butterfly",
    "Water", "Machinery", "Manufacturing", "Food processing", "Workshop",
    "Person", "Landscape", "Indoor", "Narration",
]

_NEGATIVES = ["a photo", "a photo of something else", "an unrelated scene"]

_model = None
_preprocess = None
_tokenizer = None


def _load():
    global _model, _preprocess, _tokenizer
    if _model is None:
        import open_clip
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=PRETRAINED
        )
        _model.eval()
        _tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    return _model, _preprocess, _tokenizer


def _image_embedding(image_bytes: bytes):
    import torch
    model, preprocess, _ = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    tensor = preprocess(img).unsqueeze(0)
    with torch.no_grad():
        emb = model.encode_image(tensor)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb


def _text_embeddings(prompts: list[str]):
    import torch
    model, _, tokenizer = _load()
    toks = tokenizer(prompts)
    with torch.no_grad():
        emb = model.encode_text(toks)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb


def _thing_prompt(thing: dict) -> str:
    name = thing["name"]
    desc = (thing.get("description") or "").strip()
    return f"a photo of {name}, {desc}" if desc else f"a photo of {name}"


def detect_things(image_bytes: bytes, things: list[dict],
                  threshold: float = THING_THRESHOLD) -> list[str]:
    """Return the names of watchlist things present in the frame (zero-shot, each
    decided by a softmax of "contains X" vs generic negatives)."""
    import torch
    if not things:
        return []
    # Still-frame CLIP judges appearance, not motion. Action-kind things ("pouring",
    # "extruding") can't be reliably decided from one frame and over-fire against the
    # generic negatives, so leave them to X-CLIP motion detection (kind=action).
    things = [t for t in things if (t.get("kind") or "").lower() != "action"]
    if not things:
        return []
    model, _, _ = _load()
    img_emb = _image_embedding(image_bytes)
    scale = model.logit_scale.exp()
    neg_emb = _text_embeddings(_NEGATIVES)
    matched = []
    for t in things:
        pos_emb = _text_embeddings([_thing_prompt(t)])
        cand = torch.cat([pos_emb, neg_emb], dim=0)
        logits = (scale * img_emb @ cand.T).softmax(dim=-1)[0]
        if float(logits[0]) >= threshold:
            matched.append(t["name"])
    return matched


def classify_category(image_bytes: bytes, categories: list[str]) -> str:
    import torch
    cats = categories or DEFAULT_CATEGORIES
    img_emb = _image_embedding(image_bytes)
    model, _, _ = _load()
    scale = model.logit_scale.exp()
    txt = _text_embeddings([f"a photo of {c.lower()}" for c in cats])
    probs = (scale * img_emb @ txt.T).softmax(dim=-1)[0]
    return cats[int(probs.argmax())]


def analyze(image_bytes: bytes, watchlist: list[dict] | None = None,
            categories: list[str] | None = None) -> dict:
    """On-device stand-in for the Claude frame analysis. Returns
    {description, category, tags, matched_things}. `tags` = detected things (CLIP
    doesn't free-associate tags), `description` = a terse local summary."""
    watchlist = watchlist or []
    matched = detect_things(image_bytes, watchlist) if watchlist else []
    category = classify_category(image_bytes, categories or DEFAULT_CATEGORIES)
    if matched:
        description = f"{category}: {', '.join(matched)}."
    else:
        description = f"{category}."
    return {
        "description": description,
        "category": category,
        "tags": matched,
        "matched_things": matched,
    }
