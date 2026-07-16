-- Framing v2 / Stage 4: distinguish a human-dragged crop from an auto-computed one,
-- so reframing (aspect change) recomputes AUTO crops but preserves MANUAL overrides.
--   crop_source = 'manual'  -> set by a human drag; auto-framing must not touch it.
--   crop_source = 'auto' or NULL -> computed by _apply_auto_framing; free to recompute.
ALTER TABLE timeline_items ADD COLUMN crop_source TEXT;
