# Social publishing — index

*Split on 2026-07-16 into three focused specs. This file is just the map; delete each entry when it ships, and this index when all three are gone.*

- ~~**`social-core.md`** — domain skeleton, state machine, DB-driven scheduler, dry-run harness, campaign hub + post-detail UI.~~ **SHIPPED** 2026-07-17 (migration 004, `social/`, `blueprints/publishing.py`, hub in the campaign drawer; 15 dry-run tests).
- **`social-adapters.md`** — the adapter interface + one file per platform. Phase C. **In progress:** the Instagram adapter (`social/instagram.py`) + arm/confirm flow are built and dry-run gated, but Phase C's acceptance — *prove it on a private/test account before real use* — is **not** met (not live-verified). Kept open until then.
- ~~**`social-analytics.md`** — metrics ingestion + recommendations loop (the hub "Learn" card). Phases D–E.~~ **SHIPPED** 2026-07-17 (`social/analytics.py`: append-only ingestion job + pure `summarize_campaign`, injected into campaign chat + `suggest_content`, Learn card in the hub).

Real posting stays behind `SOCIAL_DRY_RUN=1` and a per-campaign arm switch until the Instagram adapter is live-verified on a real account.
