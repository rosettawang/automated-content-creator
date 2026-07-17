-- Audio design for generated edits (spec: specs/audio-design.md).
-- The model picks an audio treatment per edit (same pattern as aspect-from-prompt);
-- it's stored here, user-overridable in the settings gear, changeable via edit chat.
--   audio_mode ∈ 'ambient' | 'speech-led' | 'music' | 'voiceover' | 'clean'
--   audio_rationale — one line the model gives, shown in the UI
--   vo_script       — voiceover mode: the editable script (visible before synthesis)
--   music_path      — music mode: chosen track from the local music/ library
ALTER TABLE edits ADD COLUMN audio_mode TEXT;
ALTER TABLE edits ADD COLUMN audio_rationale TEXT;
ALTER TABLE edits ADD COLUMN vo_script TEXT;
ALTER TABLE edits ADD COLUMN music_path TEXT;
