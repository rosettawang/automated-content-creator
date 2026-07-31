# Spec: Social publishing — platform adapters

*Split out of the former `social-publishing.md` on 2026-07-16. One thin interface, one file per platform, Composio behind each. Adapters are independently ownable by file, so different platforms can be built in parallel once `social-core.md` has shipped the interface + registry + dry-run harness.*

**Owns:** `editor/social/<platform>.py` (one file per platform), plus that platform's connect-UI pre-flight check and the arm/confirm flow (first real publish).
**Blocked by:** `social-core.md` (needs the `SocialAdapter` Protocol, registry, and state machine). Do not start an adapter before core's dry-run lifecycle passes.
**Parallel:** two adapters = two disjoint files = two sessions at once, safely. Each also touches only its own connect-UI fragment.

## The interface (defined in `social/base.py` by social-core)

```python
class SocialAdapter(Protocol):
    def publish(self, post) -> str: ...        # returns external_id
    def fetch_metrics(self, post) -> dict: ...
    def verify_connection(self) -> bool: ...
```

All Composio calls stay inside adapters — if Composio's API changes, only `social/` is touched. Platform quirks (video specs, caption limits, rate limits) live in the adapter as pre-publish validation ("caption too long", "video exceeds 90s for Reels") — same fail-loud philosophy as the export pre-flight. Account-type constraints surface in the connect UI as pre-flight checks.

## Platform matrix (verified against Composio's catalog, 2026-07-16 — re-verify per adapter with a test account, APIs drift)

**Tier 1 — vertical-video native, first real platform (Phase C):**

| Platform | Composio support | Verify before building |
|---|---|---|
| Instagram | Reels/video + photo posts | Business/Creator account only — confirm @whrfund's type |
| TikTok | publish/upload video, photo posts (1–35) via Content Posting API | Publish pulls from a **public URL**; a local app has none — confirm the direct-upload path works |

**Tier 2 — after one Tier-1 platform ships end-to-end:**

| Platform | Composio support | Note |
|---|---|---|
| YouTube | video upload → Shorts | — |
| Facebook Pages | photo/video to a Page | Pages only, not personal profiles |

**Tier 3 — when a concrete need appears:**

| Platform | Composio support | Note |
|---|---|---|
| X / Twitter | posts + media upload (well covered) | — |
| LinkedIn | posts with media; image upload confirmed | video support thinner — verify |

Out of scope: Pinterest (not a target for this app).

## Phase C — first real platform end-to-end

Pick one Tier-1 platform (Instagram Reels probably), implement its adapter, wire the arm/confirm flow (turn `SOCIAL_DRY_RUN` off per campaign; confirm dialog shows the exact account). Prove it on a private/test account before it's usable for real. Each subsequent platform is its own small follow-on using the same interface — resist building adapters for accounts nobody has connected.
