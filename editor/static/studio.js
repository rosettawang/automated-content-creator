// Unified single-document shell. Unlike the iframe /workspace, all three panels
// live in ONE document as sibling <section> elements, so the shell just shows/hides
// and resizes them (no iframes) and cross-panel drag is a plain pointer drag.
(function () {
  const PANEL_ORDER = ["library", "editor", "campaigns"];
  const LAYOUT_KEY = "studio.unified.layout";
  const panesEl = document.getElementById("studio-panes");
  const paneOf = (key) => panesEl.querySelector(`.studio-pane[data-panel="${key}"]`);

  // layout: { open: [keys in canonical order], flex: {key: weight} }
  function loadLayout() {
    try {
      const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
      if (raw && Array.isArray(raw.open)) {
        return { open: raw.open.filter((k) => PANEL_ORDER.includes(k)), flex: raw.flex || {} };
      }
    } catch (_) { /* ignore */ }
    return { open: ["library", "editor"], flex: {} };
  }
  let layout = loadLayout();
  const saveLayout = () => {
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch (_) { /* ignore */ }
  };

  function render() {
    // Remove old resizers.
    panesEl.querySelectorAll(".studio-resizer").forEach((r) => r.remove());
    const openSet = new Set(layout.open);

    PANEL_ORDER.forEach((key) => {
      const pane = paneOf(key);
      if (!pane) return;
      pane.classList.toggle("hidden", !openSet.has(key));
      pane.style.flexGrow = String(layout.flex[key] || (key === "editor" ? 1.4 : 1));
    });

    // Insert a resizer between each adjacent pair of visible panes (canonical order).
    const openInOrder = PANEL_ORDER.filter((k) => openSet.has(k));
    for (let i = 0; i < openInOrder.length - 1; i++) {
      const left = paneOf(openInOrder[i]);
      const rz = document.createElement("div");
      rz.className = "studio-resizer";
      rz.dataset.left = openInOrder[i];
      rz.dataset.right = openInOrder[i + 1];
      rz.addEventListener("mousedown", (e) => startResize(e, openInOrder[i], openInOrder[i + 1]));
      left.after(rz);
    }

    document.querySelectorAll(".rail-btn").forEach((btn) => {
      btn.classList.toggle("active", openSet.has(btn.dataset.panel));
    });
  }

  function toggle(key) {
    if (layout.open.includes(key)) {
      layout.open = layout.open.filter((k) => k !== key);
    } else {
      layout.open = PANEL_ORDER.filter((k) => k === key || layout.open.includes(k));
    }
    saveLayout();
    render();
  }

  document.querySelectorAll(".rail-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggle(btn.dataset.panel));
  });

  // Ensure a panel is open (used cross-panel, e.g. Campaigns → open Editor on a Cut).
  window.studioOpenPanel = (key) => {
    if (!PANEL_ORDER.includes(key) || layout.open.includes(key)) return;
    layout.open = PANEL_ORDER.filter((k) => k === key || layout.open.includes(k));
    saveLayout();
    render();
  };

  // Open an edit in-place: reveal the Editor pane and load the edit there, instead
  // of navigating away from /studio. Callers (Cuts lists) fall back to a link when
  // this helper is absent (i.e. on a standalone page).
  window.studioOpenEdit = (editId) => {
    window.studioOpenPanel("editor");
    if (window.studioEditor && window.studioEditor.openEdit) {
      window.studioEditor.openEdit(editId);
    }
  };

  // ---- resize between two adjacent panes ----
  function startResize(e, leftKey, rightKey) {
    e.preventDefault();
    const left = paneOf(leftKey), right = paneOf(rightKey);
    const startX = e.clientX;
    const leftW = left.getBoundingClientRect().width;
    const rightW = right.getBoundingClientRect().width;
    const totalFlex = (layout.flex[leftKey] || 1) + (layout.flex[rightKey] || 1) || 2;
    const totalPx = leftW + rightW;
    document.body.classList.add("studio-resizing");

    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      const lPx = Math.max(200, Math.min(totalPx - 200, leftW + dx));
      const lFlex = (lPx / totalPx) * totalFlex;
      layout.flex[leftKey] = lFlex;
      layout.flex[rightKey] = totalFlex - lFlex;
      left.style.flexGrow = String(lFlex);
      right.style.flexGrow = String(totalFlex - lFlex);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("studio-resizing");
      saveLayout();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // ---- cross-panel drag: Library card → Editor timeline (one document) ----
  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const card = e.target.closest(".studio-pane[data-panel='library'] .card");
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
        ghost.className = "drag-ghost";
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
      if (!dragging) return;
      card.addEventListener("click", (ce) => ce.stopImmediatePropagation(), { once: true, capture: true });
      if (ed.overTimeline(ue.clientX, ue.clientY)) await ed.drop(clip, ue.clientX);
    };
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
  });

  render();
})();
