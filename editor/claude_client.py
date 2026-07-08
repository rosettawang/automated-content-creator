from __future__ import annotations

from typing import List

from anthropic import Anthropic
from pydantic import BaseModel

MODEL = "claude-opus-4-8"

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


class ClipSelection(BaseModel):
    clip_id: int
    in_point: float
    out_point: float
    reason: str


class RoughCutPlan(BaseModel):
    concept: str
    selections: List[ClipSelection]


class ContentIdea(BaseModel):
    idea: str
    rationale: str


class ContentSuggestions(BaseModel):
    ideas: List[ContentIdea]


def _format_clip_catalog(clips: list[dict]) -> str:
    lines = []
    for c in clips:
        line = (
            f"- id={c['id']} file={c['file_stem']} duration={c['duration_s']}s "
            f"category={c['category'] or ''} description=\"{c['description'] or ''}\""
        )
        if c.get("transcript"):
            line += f" transcript=\"{c['transcript']}\""
        lines.append(line)
    return "\n".join(lines)


def generate_rough_cut(prompt: str, clips: list[dict]) -> RoughCutPlan:
    catalog = _format_clip_catalog(clips)
    message = (
        "You are helping assemble a rough-cut video timeline from a library of "
        "already-shot footage clips. Pick the clips that best match the request below, "
        "in the order they should play, with an in/out point (in seconds, within the "
        "clip's own duration) for each. Only use clip ids from the catalog. Keep the cut "
        "reasonably tight -- prefer fewer, well-chosen clips over including everything "
        "loosely related.\n\n"
        f"Clip catalog:\n{catalog}\n\n"
        f"Requested video: {prompt}"
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": message}],
        output_format=RoughCutPlan,
    )
    return response.parsed_output


def suggest_content(clips: list[dict]) -> ContentSuggestions:
    catalog = _format_clip_catalog(clips)
    message = (
        "Here is the catalog of footage already shot and indexed for a content "
        "channel:\n\n"
        f"{catalog}\n\n"
        "Based on what's already covered (subjects, settings, categories) and what's "
        "conspicuously missing or under-represented, suggest 5-8 new pieces of content "
        "worth filming next. For each, give a short idea and a one-sentence rationale "
        "tied to a specific gap or opportunity in the existing catalog -- not generic "
        "content advice."
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": message}],
        output_format=ContentSuggestions,
    )
    return response.parsed_output
