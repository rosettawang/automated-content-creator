-- Trending-audio compose (spec: specs/audio-design.md, Phase 4).
-- A per-edit LOCAL reference/scratch track: used to time cuts + preview, never written
-- into the export (export stays 'clean'). The real trending sound is attached natively
-- in the platform app at post time.
--   ref_audio_path  — local scratch file under editor/data/ref_audio/ (not distributed)
--   ref_audio_name  — the trending sound's display name (for the in-app handoff note)
--   ref_audio_start — offset (seconds) into the sound to align preview + cut timing with
--                     the platform's canonical "use this sound" start
--   ref_audio_beats — JSON array of beat timestamps (seconds), filled if beat detection runs
ALTER TABLE edits ADD COLUMN ref_audio_path TEXT;
ALTER TABLE edits ADD COLUMN ref_audio_name TEXT;
ALTER TABLE edits ADD COLUMN ref_audio_start REAL NOT NULL DEFAULT 0;
ALTER TABLE edits ADD COLUMN ref_audio_beats TEXT;
