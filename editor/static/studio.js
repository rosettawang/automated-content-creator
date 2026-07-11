// Unified-document glue: cross-panel drag from a Library card onto the Editor
// timeline. Both panels live in ONE document here, so — unlike the iframe
// /workspace bridge — there's no shield or coordinate translation: a plain pointer
// drag works, calling the editor's already-exposed hooks (window.studioEditor) and
// the library's clip lookup (window.studioLibrary).
(function () {
  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const card = e.target.closest(".pane-library .card");
    if (!card) return;
    const ed = window.studioEditor;
    const clip = window.studioLibrary && window.studioLibrary.getClip(card.dataset.clipId);
    if (!ed || !clip) return;

    const startX = e.clientX, startY = e.clientY;
    let dragging = false, ghost = null;

    const onMove = (me) => {
      if (!dragging) {
        if (Math.abs(me.clientX - startX) < 5 && Math.abs(me.clientY - startY) < 5) return;
        dragging = true;
        ghost = document.createElement("div");
        ghost.className = "drag-ghost";       // styled in style.css
        ghost.textContent = clip.file_stem;
        document.body.appendChild(ghost);
      }
      ghost.style.left = `${me.clientX + 8}px`;
      ghost.style.top = `${me.clientY + 8}px`;
      ed.highlight(ed.overTimeline(me.clientX, me.clientY));
    };

    const onUp = async (ue) => {
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", onUp, true);
      if (ghost) ghost.remove();
      ed.highlight(false);
      if (!dragging) return;                  // was a click → let the card open its info panel
      // Suppress the click that would otherwise fire after the drag.
      card.addEventListener("click", (ce) => ce.stopImmediatePropagation(), { once: true, capture: true });
      if (ed.overTimeline(ue.clientX, ue.clientY)) {
        await ed.drop(clip, ue.clientX);      // drop() surfaces its own error if any
      }
    };

    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
  });
})();
