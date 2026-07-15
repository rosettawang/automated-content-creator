-- Framing v2: time-aware regions.
--
-- clip_regions previously held one box per subject from a single keyframe (usually
-- near the clip's start). Reframing a cut that begins deep inside a clip then
-- centered on where the subject *was*, not where it is during the cut. These two
-- columns let a clip carry multiple boxes across time (one set per deep-index scene
-- segment), so assemble-time framing can pick the box nearest each item's in/out
-- point and pan with the subject.
--
--   t_frame    — timestamp (seconds into the clip) the box was observed at.
--                NULL on legacy single-keyframe rows ("unknown time, weak evidence").
--   is_primary — 1 for the dominant subject box in its frame (largest watched-thing
--                box, else largest box), which reframe centers on; 0/NULL otherwise.
ALTER TABLE clip_regions ADD COLUMN t_frame REAL;
ALTER TABLE clip_regions ADD COLUMN is_primary INTEGER;

CREATE INDEX IF NOT EXISTS ix_clip_regions_clip_time ON clip_regions(clip_id, t_frame);
