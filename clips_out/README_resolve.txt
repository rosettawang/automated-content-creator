RESOLVE HANDOFF — IMG_2926 "caterpillar macro" (Reel idea #1)
============================================================

Files in this folder:
  IMG_2926_source.mov          Full-res source clip (1920x1080, rotation flag = vertical), 15.84s.
  IMG_2926_2-7s_vertical.mp4   GUARANTEED asset: precise 2-7s cut, upright vertical 1080x1920, 5.0s.
  IMG_2926_2-7s.fcpxml         Timeline that rebuilds the 2-7s cut from the source (best-effort).

QUICKEST PATH (always works):
  Drag IMG_2926_2-7s_vertical.mp4 into a DaVinci Resolve timeline. Done — it's already the 5s vertical cut.

REBUILD-THE-EDIT PATH (FCPXML):
  1. Keep IMG_2926_source.mov in this folder.
  2. In Resolve: File > Import > Timeline... > select IMG_2926_2-7s.fcpxml
  3. If Resolve asks to relink media, point it at IMG_2926_source.mov (same folder).
  The timeline is a 1080x1920 / 29.97fps vertical project with one clip cut from 2.000s to 7.005s.
  Note: FCPXML across apps can be finicky — if it doesn't import cleanly, use the Quickest Path above.

SUGGESTED TEXT (add in Resolve):
  "This caterpillar eats ONE plant on Earth."  ->  "No pipevine = no pipevine swallowtail."
