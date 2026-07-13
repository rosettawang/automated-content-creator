# Spec: Aspect from prompt wording

**Owns:** `editor/claude_client.py` (RoughCutPlan model + generate prompt), the aspect-handling lines of `editor/blueprints/ai.py` / `edits.py` generation endpoints.
**Parallel:** safe alongside frontend and test specs. Conflicts with `core-split.md` (backend imports moving) — don't run concurrently with it.

## Goal
"a 20s **vertical** reel…" currently produces `aspect='source'`; the user must know about the settings gear. The model should infer the output frame from the request.

## Design
- Add `aspect: Literal["source","9:16","4:5","1:1","16:9"] | None` to `RoughCutPlan`; prompt instructs: infer from wording ("vertical/Reels/TikTok/Story" → 9:16, "square" → 1:1, "feed portrait" → 4:5, "landscape/YouTube" → 16:9); null when unstated.
- Generation endpoints: explicit user-set aspect (request param) wins → else plan.aspect → else 'source'. Store on the edit as today.
- Edit chat: same field on `EditChatResult` so "make this square" works; a chat-set aspect updates the edit and is mentioned in the reply.
- UI: settings gear reflects whatever was inferred (it reads the edit; no new UI needed). One-line toast on generate when aspect was inferred ("Framing: 9:16 vertical — change in ⚙").

## Acceptance
The round-2 test prompt (contains "vertical") yields an edit with `aspect='9:16'` with no gear interaction; "make it square" in chat flips it to 1:1; explicit gear choice is never overridden by a later chat unless asked.
