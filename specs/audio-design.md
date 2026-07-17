# Spec: Audio design for generated edits

**Owns:** the audio path of `editor/export.py`, plan models in `claude_client.py`, audio fields on generation/chat endpoints, migrations (`edits.audio_mode` etc.; later `edits.ref_audio_path`/`ref_audio_start`), music-library UI touches, later a `tts.py` adapter, and — for trending-audio compose (Phase 4) — a synced reference-audio layer in the editor program monitor + beat-aware cut-timing.
**Parallel:** `aspect-from-prompt` has shipped — copy its pattern (plan field → edit column → settings-gear override → chat-changeable). Conflicts with the framing-v2 *tail* (export frame-check) — both touch `export.py`; sequence around it. Safe alongside social-publishing and frontend specs. Phase 3 (TTS) blocked by nothing but shouldn't start before Phases 1–2 prove the plumbing.

## Problem
Generation decides *what you see* but not *what you hear*. Exports concatenate raw camera audio: ambient level jumps at every cut, sentences chopped mid-word, silence over photo-stills. A short-form edit needs an audio treatment chosen at generation time — speech-led, music bed, generated voiceover, or cleaned-up ambient — the same way it now gets clips, moments, and (once `aspect-from-prompt` ships) an aspect.

## Model output
`RoughCutPlan` and `EditChatResult` gain:

```
audio_plan:
  mode: 'ambient' | 'speech-led' | 'music' | 'voiceover' | 'clean'
  rationale: str            # one line, shown in the UI
  vo_script: str | None     # mode=voiceover: written to fit ~total duration
  music_mood: str | None    # mode=music: matched against the local library's tags
```

Prompt guidance: prefer `speech-led` when chosen clips have transcript/speech moments that carry the story (then in/out points MUST respect sentence boundaries — speech events are already in the catalog); `music` or `clean` for montage-style cuts; `voiceover` when the user asks for narration or the story needs glue the footage can't provide. User wording always wins ("with upbeat music" → music).

Stored on the edit (migration: `audio_mode`, `audio_rationale`, `vo_script`, `music_path`); user-overridable in the settings gear next to aspect; changeable via edit chat ("swap the music for a voiceover explaining each step").

## Export behavior per mode
- **ambient** (default today, improved): per-segment `loudnorm`, `acrossfade` (~0.25s) between segments, silence generated under photo-stills. No new assets needed — this alone removes the worst artifacts.
- **speech-led**: ambient treatment + generation-side guarantee that cuts land on sentence boundaries (data already exists as speech events; this is mostly a prompting/validation change).
- **music**: replace ambient with a track from a local `music/` folder (user-supplied files with a tiny mood-tag sidecar; model picks by `music_mood`), faded in/out, trimmed/looped to length. Keep original audio mixed low (-20dB) or off per a flag.
- **voiceover**: TTS the `vo_script` (adapter interface so the engine is swappable — on-device first if available, API engines behind the same interface), lay it over ducked ambient (`sidechaincompress` or simple -15dB duck), pad/trim script-to-timeline mismatch honestly (warn if VO runs long rather than silently speeding it up).
- **clean**: strip all audio. First-class mode, not an afterthought — for Reels/TikTok it's often the *right* answer: platforms license trending music in-app, and baking commercial music into the file risks copyright flags. The UI should say this ("export clean, add music in the app") rather than bury it.

## Trending-audio compose (the reference-track workflow)

Clean mode above is only *half* the trending-audio story: it hands off cleanly, but doesn't help you **compose to** a trending sound — which is what actually makes trending-audio content land (cuts on the beat, plus the platform sound-page discovery boost). The app enables that with a **local reference (scratch) track**:

- **You supply the track.** The app cannot pull audio out of TikTok/Instagram (no API for it, and ripping it *for distribution* is the copyright problem). You drop the trending sound onto an edit as a **reference** — used locally only, to time cuts and preview. It is never written into the export.
- **Compose to it.** Generation/chat time the edit to the track: match total length and, with beat detection, snap cuts to the beat / land on the drop.
- **Preview with it.** The program monitor plays the (muted) video with the reference laid over it, synced to the timeline — so you hear the finished feel before posting.
- **Clean handoff.** Export `clean` (no baked audio); on-platform, attach the **same** trending sound natively. Because it's the same track aligned to the same start, your cuts hit the beats and you get the sound-page boost.
- **Start-offset alignment.** For the native sound to line up, the reference must start at the sound's canonical "use this sound" offset — store that offset so preview + cut-timing match what the platform will play. If it drifts, the beats drift.

**Boundary:** the reference track is a *local scratch guide only* — never distributed, never in the exported file. Attaching the real sound is a **manual, in-app step** (publishing APIs / Composio can't attach licensed platform audio), so this workflow is **manual-post, not schedulable** — coordinate with `social-core`'s post composer (a "add trending sound in-app" checklist item on the post).

**Data (edit-scoped, NOT part of `audio_plan`):** `ref_audio_path` (the local scratch file), `ref_audio_start` (offset into the sound, seconds), optional cached beat markers. `audio_plan` is the *exported* treatment; this is *composition input*. Export ignores it (stays `clean`); it drives cut-timing + preview only.

## Phases (ship each before the next)
1. **Ambient treatment + speech-boundary cutting.** No new assets, no new deps, biggest artifact removal. Also fixes silent photo-still gaps.
2. **Music bed + clean mode.** Local `music/` library with mood tags; picker in settings gear; licensing note in UI.
3. **Generated voiceover.** TTS adapter, script-fit loop, ducking. The script is a *visible, editable field* on the edit before synthesis — never straight-to-audio.
4. **Trending-audio compose.** Reference-track field on the edit + upload; beat/length-aware cut-timing in generate/chat; preview-with-audio (synced audio layer in the program monitor); a "post clean → add this sound in-app" handoff note (sound name + start offset) on the edit/post. **Highest-value phase for trending content.** Depends on clean mode (Phase 2). Manual-post only — see the boundary above.

## Acceptance
Round-2 test reel re-exported in each mode: no audible level jump at cuts (ambient); no mid-word cut when speech-led; music fades and fits duration exactly; VO audible over ducked ambient with script matching what's heard; clean file has no audio stream. Tests cover the plan→edit field storage and the export filter-graph construction per mode (string assertions, no audio analysis needed).

**Trending-audio compose:** an edit with a reference track previews in sync (cuts audibly on the beat); the export is still clean (no audio stream — the reference never ships); the handoff note carries the sound name + start offset. Tests cover `ref_audio_path`/`ref_audio_start` storage and that export stays clean regardless of a set reference.
