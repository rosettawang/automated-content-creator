from __future__ import annotations

import base64
from typing import List, Literal, Optional

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


# Output aspect the model may infer from the request wording. None = the request
# didn't specify a frame, so the caller keeps whatever's already set / 'source'.
AspectHint = Optional[Literal["source", "9:16", "4:5", "1:1", "16:9"]]

# Shared instruction appended to generation/edit prompts so the model fills `aspect`
# from wording (kept in one place so both prompts stay consistent).
_ASPECT_INSTRUCTION = (
    "Also set `aspect` (the output frame) from the request wording: "
    '"vertical"/"Reels"/"TikTok"/"Story"/"9:16" -> "9:16"; '
    '"square"/"1:1" -> "1:1"; "portrait feed"/"4:5" -> "4:5"; '
    '"landscape"/"widescreen"/"YouTube"/"16:9" -> "16:9". '
    "If the request doesn't mention a frame or orientation, set aspect to null "
    "(do NOT guess). Only use one of those exact values or null."
)


class RoughCutPlan(BaseModel):
    concept: str
    selections: List[ClipSelection]
    aspect: AspectHint = None         # inferred output frame, or null when unstated


class CropEdit(BaseModel):
    index: int    # 0-based index into `selections` of the item to reframe
    cx: float     # desired horizontal center, fraction of the source frame (0..1)
    cy: float     # desired vertical center, fraction of the source frame (0..1)


class EditChatResult(BaseModel):
    reply: str                        # short, conversational summary of what changed
    selections: List[ClipSelection]   # the COMPLETE new timeline, in play order
    aspect: AspectHint = None         # set only when the instruction asks to reframe (e.g. "make it square")
    # Per-item framing changes: only for items the instruction asks to reframe
    # ("keep the oil bowl centered"). Empty when framing isn't mentioned. The exact
    # aspect-correct crop window is computed server-side from this center point.
    crops: List[CropEdit] = []


def _format_timeline(items: list[dict]) -> str:
    if not items:
        return "(the timeline is currently empty)"
    lines = []
    for i, it in enumerate(items):
        line = (
            f"{i}. clip_id={it['clip_id']} file={it.get('file_stem', '')} "
            f"in={it.get('in_point', 0)} out={it.get('out_point', 0)} "
            f"dur={it.get('duration_s', '?')}s desc=\"{it.get('description', '') or ''}\""
        )
        # Framing context so the model can honor "keep the bowl centered".
        cr = it.get("crop")
        if cr:
            line += f" current_crop_center=({cr['cx']:.2f},{cr['cy']:.2f})"
        regs = it.get("regions") or []
        if regs:
            rs = "; ".join(
                f"{r['label']}@({r['x'] + r['w'] / 2:.2f},{r['y'] + r['h'] / 2:.2f})"
                for r in regs
            )
            line += f" subjects=[{rs}]"
        lines.append(line)
    return "\n".join(lines)


def revise_edit(instruction: str, current_timeline: list[dict], clips: list[dict],
                aspect: str | None = None) -> EditChatResult:
    """Revise an existing timeline per a natural-language instruction. Returns a short
    reply plus the COMPLETE new timeline (the model may reorder, trim, drop, or add
    clips from the catalog). Preserves anything the instruction doesn't touch. When the
    instruction is about framing ("keep the bowl centered"), returns `crops` keyed by
    the item's index in `selections`."""
    catalog = _format_clip_catalog(clips)
    current = _format_timeline(current_timeline)
    framing_ctx = ""
    if aspect and aspect != "source":
        framing_ctx = (
            f"\n\nThe output frame is {aspect}, so each clip is cropped from its source. "
            "Timeline lines list each item's `subjects` (name@center, normalized 0..1) and "
            "its `current_crop_center`. If — and only if — the instruction asks to reframe "
            "or keep a subject in view (e.g. \"keep the oil bowl centered\", \"frame on her "
            "hands\"), return `crops`: one entry per affected item with its `index` (the "
            "0-based number shown in the timeline) and the `cx`,`cy` center to keep in "
            "frame (usually a listed subject's center). Leave `crops` empty for any "
            "instruction that isn't about framing."
        )
    message = (
        "You are an assistant editing a video timeline built from a library of "
        "already-shot clips. Apply the user's instruction to the CURRENT timeline and "
        "return the COMPLETE revised timeline as an ordered list of selections "
        "(clip_id + in/out points in seconds within each clip's own duration). Only use "
        "clip ids from the catalog. Preserve the parts of the timeline the user didn't "
        "ask to change (keep their order and in/out points). Keep cuts tight and "
        "purposeful. When a clip lists timestamped 'moment' lines, place in/out points "
        "around the best-matching moment rather than defaulting to the start of the "
        "clip. Also give a short, friendly one- or two-sentence reply describing "
        "what you changed.\n\n"
        + _ASPECT_INSTRUCTION
        + " Here, set aspect ONLY when the instruction asks to reframe (e.g. \"make it "
        "square\", \"turn this vertical\"); otherwise null. When you do change it, say "
        "so in your reply."
        + framing_ctx
        + f"\n\nCURRENT TIMELINE (in order):\n{current}\n\n"
        f"CLIP CATALOG (available to pull from):\n{catalog}\n\n"
        f"USER INSTRUCTION: {instruction}"
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": message}],
        output_format=EditChatResult,
    )
    return response.parsed_output


class ContentIdea(BaseModel):
    idea: str
    rationale: str


class ContentSuggestions(BaseModel):
    ideas: List[ContentIdea]


class Region(BaseModel):
    label: str            # what's in this region (a matched thing's name, or a notable subject)
    x: float              # left edge, fraction of frame width  (0..1, top-left origin)
    y: float              # top edge,  fraction of frame height (0..1)
    w: float              # width,  fraction of frame width
    h: float              # height, fraction of frame height


class VisualAnalysis(BaseModel):
    description: str
    category: str
    tags: List[str]
    # Names (verbatim from the provided watchlist) of target things visible in the
    # frame. Empty when no watchlist is given or nothing matches.
    matched_things: List[str] = []
    # Where notable subjects (and any matched things) sit in the frame, as normalized
    # boxes — so a later reframe/crop to a different aspect can keep them in shot.
    regions: List[Region] = []


class SceneSegment(BaseModel):
    t_start: float          # seconds into the clip
    t_end: float
    description: str        # what happens in this span (subjects, action, camera/shot)
    things: List[str] = []  # watchlist names visible in this span (verbatim)
    # Where the notable subjects sit DURING this span, as normalized boxes — so a
    # later reframe to a different aspect can center on the subject at the right
    # moment (boxes vary across the clip as the subject/camera moves).
    regions: List[Region] = []


class DeepIndex(BaseModel):
    description: str        # 1-3 sentence clip-level summary
    category: str
    tags: List[str]
    matched_things: List[str] = []   # watchlist names present anywhere in the clip
    segments: List[SceneSegment]     # timeline covering the clip start to end


def _deep_index_content(
    frames: list[tuple[float, bytes]],
    duration: float,
    transcript_segments: list[dict] | None = None,
    watchlist: list[dict] | None = None,
    media_type: str = "image/jpeg",
) -> list[dict]:
    """The user-message content blocks for one deep-index pass (shared by the
    synchronous call and the Batch API path)."""
    content: list[dict] = []
    for t, img in frames:
        content.append({"type": "text", "text": f"Frame at t={t:.1f}s:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.standard_b64encode(img).decode("utf-8")},
        })

    ts = ""
    if transcript_segments:
        lines = [f"[{s['t_start']:.1f}-{s['t_end']:.1f}s] {s['text']}" for s in transcript_segments]
        ts = "\n\nTimestamped transcript of the clip's audio:\n" + "\n".join(lines)

    watch = ""
    if watchlist:
        watch = (
            "\n\nThe user tracks these specific subjects. Where one is clearly visible, "
            "include its bare name (exactly as written, no kind label) in that segment's "
            "`things` and in `matched_things`. Don't guess:\n"
            + _format_watchlist(watchlist)
        )

    content.append({"type": "text", "text": (
        f"These are frames sampled from a single {duration:.1f}s video clip in a content "
        "library, labeled with their timestamps. Build an editing index for it:\n"
        "1. `description`: 1-3 sentences on what the clip shows overall.\n"
        "2. `category`: a short label consistent with 'Gardening', 'Wildlife', 'Machinery', etc.\n"
        "3. `tags`: specific subjects/setting/visual details.\n"
        "4. `segments`: a timeline of consecutive spans covering 0 to "
        f"{duration:.1f}s. Start a new segment when the action, subject, or shot changes. "
        "For each, describe what happens concretely enough that an editor could pick the "
        "right moment to cut to (action, subjects, camera/framing). Use the transcript to "
        "inform what's happening. Also give each segment `regions`: normalized boxes "
        "(label, x, y, w, h in 0..1, top-left origin) locating the notable subjects — and "
        "every watched thing named in that segment's `things` — AS THEY APPEAR IN THAT "
        "SPAN, using the frame(s) whose timestamp falls in it. Boxes should move across "
        "segments as the subject or camera moves." + ts + watch
    )})
    return content


def deep_index_clip(
    frames: list[tuple[float, bytes]],
    duration: float,
    transcript_segments: list[dict] | None = None,
    watchlist: list[dict] | None = None,
    media_type: str = "image/jpeg",
) -> DeepIndex:
    """ONE deep 'analyze once, edit forever' pass over a clip -> clip summary +
    a timestamped segment timeline detailed enough to cut from."""
    content = _deep_index_content(frames, duration, transcript_segments, watchlist, media_type)
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": content}],
        output_format=DeepIndex,
    )
    return response.parsed_output


def deep_index_batch_request(custom_id: str, frames, duration,
                             transcript_segments=None, watchlist=None) -> dict:
    """One Batch API request entry for a deep-index pass (50% price, async).
    Uses a JSON instruction + tolerant parse instead of messages.parse."""
    content = _deep_index_content(frames, duration, transcript_segments, watchlist)
    content.append({"type": "text", "text": (
        "Respond with ONLY a JSON object (no prose, no code fences) with keys: "
        "description (string), category (string), tags (string[]), "
        "matched_things (string[]), segments (array of {t_start: number, "
        "t_end: number, description: string, things: string[], regions: array of "
        "{label: string, x: number, y: number, w: number, h: number}})."
    )})
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": content}],
        },
    }


def parse_deep_index_json(text: str) -> DeepIndex:
    """Tolerant parse of a batch result's text into a DeepIndex."""
    import json, re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in response")
    return DeepIndex.model_validate(json.loads(m.group(0)))


class ThingKind(BaseModel):
    kind: str


class BestFrame(BaseModel):
    index: int  # 0-based index of the chosen image
    reason: str


class CropSuggestion(BaseModel):
    crop_x: float  # left edge, fraction of source width (0..1)
    crop_y: float  # top edge, fraction of source height (0..1)
    crop_w: float  # width, fraction of source width (0..1)
    crop_h: float  # height, fraction of source height (0..1)
    reason: str


def propose_crop(image_bytes: bytes, aspect: str, media_type: str = "image/jpeg") -> CropSuggestion:
    """As a director, choose how to reframe this landscape frame into `aspect`
    (e.g. '9:16'). Returns a crop rectangle in fractions of the source frame whose
    own ratio matches the target aspect, positioned to keep the main subject well
    composed (rule of thirds, headroom, don't cut off the subject).

    The model reasons about *where the subject is*; we enforce the exact aspect
    math afterward so the rect is always geometrically valid."""
    w_ratio, h_ratio = (float(x) for x in aspect.split(":"))
    target_ar = w_ratio / h_ratio
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    msg = (
        f"This is a frame from a landscape video clip. I need to reframe it to a "
        f"{aspect} ({'vertical' if target_ar < 1 else 'square' if target_ar == 1 else 'horizontal'}) "
        f"crop for short-form social video. As the director, decide which region to "
        f"keep so the main subject stays well composed — follow rule-of-thirds, keep "
        f"headroom, and never cut the subject awkwardly. Return the crop as fractions "
        f"of the frame (crop_x, crop_y = top-left corner; crop_w, crop_h = size, all "
        f"0..1). Aim for the crop's width:height to be about {target_ar:.4f}. Briefly "
        f"say what you framed for."
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
            {"type": "text", "text": msg},
        ]}],
        output_format=CropSuggestion,
    )
    out = response.parsed_output
    return _normalize_crop(out, target_ar)


def _normalize_crop(c: CropSuggestion, target_ar: float) -> CropSuggestion:
    """Force the model's rough rect to exactly the target aspect and clamp it fully
    inside the frame, keeping it centered on the model's chosen center point.

    Source frame is normalized to a 1x1 box, but a crop of aspect target_ar in that
    box must respect the *frame's own* aspect — since we work in fractions of W and H
    independently, a rect with crop_w/crop_h (in fractions) maps to pixels
    crop_w*W by crop_h*H. We don't know W:H here, so the caller passes frames whose
    pixel aspect we correct at render time; here we simply keep the model's fractions
    but clamp them into range. Exact aspect is enforced in the export filter's
    cover-scale, so a slightly-off rect still yields a clean result."""
    cx = min(max(c.crop_x, 0.0), 1.0)
    cy = min(max(c.crop_y, 0.0), 1.0)
    cw = min(max(c.crop_w, 0.05), 1.0)
    ch = min(max(c.crop_h, 0.05), 1.0)
    # keep the rect inside the frame
    cx = min(cx, 1.0 - cw)
    cy = min(cy, 1.0 - ch)
    return CropSuggestion(crop_x=round(cx, 4), crop_y=round(cy, 4),
                          crop_w=round(cw, 4), crop_h=round(ch, 4), reason=c.reason)


def pick_best_frame(subject: str, images: list[bytes], media_type: str = "image/jpeg") -> BestFrame:
    """Given several candidate frames that all contain `subject`, pick the single
    most flattering / representative one. Returns the chosen 0-based index.

    "Flattering" = the subject is clearly visible, well-lit, in focus, nicely framed
    and prominent — the shot you'd choose to represent it. Falls back to index 0."""
    if not images:
        return BestFrame(index=0, reason="no candidates")
    content: list[dict] = []
    for i, img in enumerate(images):
        content.append({"type": "text", "text": f"Image {i}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(img).decode("utf-8"),
            },
        })
    content.append({"type": "text", "text": (
        f'These images all contain "{subject}". Choose the single most flattering, '
        "representative one to use as its cover thumbnail — the subject should be "
        "clearly visible, well-lit, in focus, well-composed, and prominent in the frame. "
        "Avoid blurry, dark, awkward, cropped, or cluttered shots. Return the 0-based "
        "index of the best image and a brief reason."
    )})
    try:
        response = get_client().messages.parse(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": content}],
            output_format=BestFrame,
        )
        out = response.parsed_output
        if 0 <= out.index < len(images):
            return out
        return BestFrame(index=0, reason="model index out of range; fell back to first")
    except Exception as e:
        return BestFrame(index=0, reason=f"selection failed: {e}")


class InferredThing(BaseModel):
    name: str          # concise subject name, e.g. "pipevine" or "swallowtail caterpillar"
    kind: str          # plant | animal | person | action | object | other
    description: str   # one short hint that helps spot it on screen


class CampaignThings(BaseModel):
    things: List[InferredThing]


def infer_campaign_things(name: str, description: str = "") -> CampaignThings:
    """From a campaign's name + description, infer the concrete subjects ('things')
    worth watching for in footage — species, objects, actions, people, settings.
    These seed the campaign's watchlist so indexing actively flags relevant clips."""
    ctx = f"\n\nCampaign description:\n{description}" if description else ""
    message = (
        "A content creator is starting a video campaign. From its name and description, "
        "list the concrete, visually-identifiable subjects worth watching for in their "
        "footage — specific plants/species, animals, objects, recurring actions, people, "
        "or distinctive settings. Prefer specific nameable subjects (e.g. 'pipevine', "
        "'swallowtail caterpillar', 'seed planting') over vague themes (e.g. 'nature', "
        "'growth'). Return 3-8 of them; skip anything too generic to spot in a frame.\n\n"
        f"Campaign name: {name}{ctx}"
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": message}],
        output_format=CampaignThings,
    )
    return response.parsed_output


class CampaignChatResult(BaseModel):
    reply: str                                  # the conversational answer to show
    context_doc: Optional[str] = None           # full rewritten campaign context doc,
                                                 # or null if this turn didn't change it
    recommend_clip_ids: List[int] = []          # clips (by id, from the catalog) to
                                                 # suggest ADDING to the campaign
    recommend_reason: Optional[str] = None       # one line explaining the recommendation


def campaign_chat(
    campaign: dict,
    things: list[dict],
    in_campaign: list[dict],
    catalog: list[dict],
    history: list[dict],
    user_message: str,
) -> CampaignChatResult:
    """Assist with ONE campaign. Grounded in its description + evolving context doc,
    its watched things, the clips already in it, and the FULL clip catalog (so it can
    recommend whole GROUPS of clips to add). Returns structured output: a reply, an
    optionally-updated context document, and an optional set of recommended clip ids.

    `in_campaign` = clips already in the campaign; `catalog` = every clip available.
    `history` = prior {role, content} turns (excluding the new user_message)."""
    watch = _format_watchlist(things) if things else "(none yet)"
    have = _format_clip_catalog(in_campaign) if in_campaign else "(none yet)"
    all_clips = _format_clip_catalog(catalog) if catalog else "(catalog empty)"
    ctx_doc = (campaign.get("context_doc") or "").strip() or "(empty — start building it)"

    system = (
        "You are a creative producer assisting with ONE video campaign. Be concrete and "
        "concise, and ground everything in the campaign's ACTUAL footage — refer to clips "
        "by description; when footage is missing for an idea, say what to shoot; never "
        "invent clips.\n\n"
        "You maintain a living CONTEXT DOCUMENT for this campaign — a short evolving brief "
        "(subject, angle, tone, audience, decisions so far, what's still needed). Whenever "
        "the conversation establishes or changes something material, return the FULL "
        "rewritten context_doc (keep it tight — a few short sections, not a transcript). If "
        "nothing material changed this turn, return null for context_doc.\n\n"
        "You can recommend whole GROUPS of clips to add to the campaign. When the user asks "
        "to pull in footage (e.g. 'add the pipevine clips', 'everything from the oil-press "
        "shoot', 'all the butterfly stuff') or when a group clearly fits, put the matching "
        "clip ids from the FULL CATALOG in recommend_clip_ids and a one-line reason. Only "
        "recommend; the user confirms. Leave it empty when not recommending clips. Never "
        "recommend clips already in the campaign.\n\n"
        f"Campaign: {campaign.get('name','')}\n"
        f"About: {campaign.get('description','') or '(no description)'}\n\n"
        f"CONTEXT DOCUMENT (current):\n{ctx_doc}\n\n"
        f"Watched things:\n{watch}\n\n"
        f"Clips ALREADY in this campaign:\n{have}\n\n"
        f"FULL CLIP CATALOG (recommend_clip_ids must come from here):\n{all_clips}"
    )
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=3000,
        system=system,
        messages=messages,
        output_format=CampaignChatResult,
    )
    return response.parsed_output


def classify_thing_kind(name: str, description: str = "") -> str:
    """Infer the kind of a watched thing from its name (so the user doesn't have to
    pick). Returns one of: plant, animal, person, action, object, other."""
    hint = f" (context: {description})" if description else ""
    message = (
        f'Classify the subject "{name}"{hint} into exactly one of these kinds: '
        "plant, animal, person, action, object, other. Return only the single word."
    )
    try:
        response = get_client().messages.parse(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": message}],
            output_format=ThingKind,
        )
        kind = (response.parsed_output.kind or "").strip().lower()
        return kind if kind in {"plant", "animal", "person", "action", "object", "other"} else "other"
    except Exception:
        return "other"


class ClipMatch(BaseModel):
    clip_id: int
    matched: List[str]


class ClipMatches(BaseModel):
    results: List[ClipMatch]


def match_things_in_text(watchlist: list[dict], clips: list[dict]) -> dict[int, list[str]]:
    """Given the watched things and a batch of clips (each with id + existing text
    metadata), decide which things each clip contains -- using taxonomic/semantic
    reasoning, so a 'swallowtail' matches 'butterfly' and 'Aristolochia' matches
    'pipevine'. Returns {clip_id: [thing names]}."""
    if not watchlist or not clips:
        return {}

    things_block = _format_watchlist(watchlist)
    lines = []
    for c in clips:
        bits = [f"id={c['id']}"]
        if c.get("description"):
            bits.append(f"description=\"{c['description']}\"")
        if c.get("category"):
            bits.append(f"category=\"{c['category']}\"")
        if c.get("tags"):
            bits.append(f"tags=\"{c['tags']}\"")
        if c.get("transcript"):
            bits.append(f"transcript=\"{c['transcript'][:300]}\"")
        lines.append("- " + " ".join(bits))
    clips_block = "\n".join(lines)

    message = (
        "The user watches for these specific subjects in their footage library:\n"
        f"{things_block}\n\n"
        "Below is a batch of clips, each with an id and the text metadata already "
        "known about it. For EACH clip, decide which of the watched subjects it "
        "contains. Use taxonomic and semantic reasoning, not just literal word "
        "matching: a more specific term counts as its general category — e.g. a "
        "'swallowtail' or 'monarch' IS a butterfly; 'Aristolochia' or 'Dutchman's "
        "pipe' IS pipevine; a 'bee' IS a pollinator. Only include a subject if the "
        "clip clearly contains it. Return matches per clip using the bare subject "
        "name exactly as written above. Omit clips with no matches.\n\n"
        f"Clips:\n{clips_block}"
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": message}],
        output_format=ClipMatches,
    )
    return {m.clip_id: m.matched for m in response.parsed_output.results if m.matched}


def _format_clip_catalog(clips: list[dict]) -> str:
    lines = []
    for c in clips:
        line = (
            f"- id={c['id']} file={c['file_stem']} duration={c['duration_s']}s "
            f"category={c['category'] or ''} description=\"{c['description'] or ''}\""
        )
        if c.get("context"):
            line += f" context=\"{c['context']}\""
        if c.get("tags"):
            line += f" tags=\"{c['tags']}\""
        if c.get("transcript"):
            line += f" transcript=\"{c['transcript']}\""
        # Technical quality (informational): resolution, sharpness, 0-100 score.
        # The model may weigh these when the user asks (e.g. "prefer the sharpest,
        # highest-res shots"), but should NOT exclude on quality unless told to.
        if c.get("width") and c.get("height"):
            line += f" resolution={c['width']}x{c['height']}"
        if c.get("quality") is not None:
            line += f" quality={c['quality']}/100"
        if c.get("sharpness") is not None:
            line += f" sharpness={c['sharpness']}"
        # Availability: a non-local clip can't be cut. The pool builder already
        # excludes these, but flag it so the model never leans on a ghost if one
        # ever reaches the catalog.
        if c.get("available_locally") is False:
            line += " available_locally=false(NOT DOWNLOADED — do not use)"
        # Deep-index timeline: timestamped moments within the clip (scene spans,
        # detected actions, speech). This is what lets the model cut TO a moment
        # ("the caterpillar close-up at 4.2s") instead of blindly taking the front
        # of the clip.
        for m in c.get("moments") or []:
            what = (m.get("text") or m.get("label") or "").strip()
            if not what:
                continue
            tag = "" if m.get("kind") == "scene" else f" [{m['kind']}]"
            line += f"\n    moment {m['t_start']:.1f}-{m['t_end']:.1f}s{tag}: {what}"
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
        "Choosing in/out points: many clips list timestamped 'moment' lines (scene "
        "spans, actions, speech). When present, cut TO the best-matching moment -- set "
        "in/out around its timestamps rather than defaulting to the start of the clip. "
        "For clips without moments, still pick a plausible window (e.g. skip a shaky "
        "first second on long handheld clips). Vary shot lengths to fit the content "
        "instead of giving every clip an identical duration.\n\n"
        + _ASPECT_INSTRUCTION + "\n\n"
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


def _format_watchlist(watchlist: list[dict]) -> str:
    lines = []
    for t in watchlist:
        kind = f" ({t['kind']})" if t.get("kind") else ""
        hint = f" — {t['description']}" if t.get("description") else ""
        lines.append(f"- {t['name']}{kind}{hint}")
    return "\n".join(lines)


def analyze_frame(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
    watchlist: list[dict] | None = None,
) -> VisualAnalysis:
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    message = (
        "This is a keyframe from a raw footage clip in a content library. Describe what's "
        "visible in one or two sentences, suggest a short category label consistent with "
        "labels like 'Gardening', 'Wildlife', 'Habitat', 'Caterpillar', 'Plant' (reuse an "
        "existing-style label rather than inventing an overly specific one), and list a "
        "handful of specific tags (subjects, setting, notable visual details)."
    )
    if watchlist:
        message += (
            "\n\nAdditionally, the user is specifically watching for these subjects. "
            "Determine which (if any) are clearly visible in this frame. In "
            "`matched_things`, return ONLY the bare name of each one that appears — the "
            "text before the parenthesis, exactly as written, with no kind label or hint. "
            "Only include one if you are confident it is present — do not guess. Leave the "
            "list empty if none appear.\n"
            f"{_format_watchlist(watchlist)}"
        )
    message += (
        "\n\nAlso return `regions`: for each notable subject in the frame (and every "
        "matched thing above), give a bounding box locating it, as fractions of the "
        "frame with a top-left origin — x,y = the box's top-left corner, w,h = its "
        "width and height, all in 0..1. Use the thing's exact name as the region label "
        "when it corresponds to a matched thing; otherwise a short subject label. "
        "Include only genuinely notable subjects (skip background clutter); it's fine "
        "to return an empty list if nothing stands out. These boxes are used to reframe "
        "or crop the video to other aspect ratios without cutting the subject."
    )
    response = get_client().messages.parse(
        model=MODEL,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                },
                {"type": "text", "text": message},
            ],
        }],
        output_format=VisualAnalysis,
    )
    return response.parsed_output
