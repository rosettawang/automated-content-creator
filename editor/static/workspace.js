// Workspace shell: Editor / Clip Library / Campaigns as openable, closable,
// resizable panels (à la Claude desktop). Each panel hosts its existing page in
// an iframe. Layout (which panels + their widths) persists in localStorage.

const PANELS = {
  library:   { title: "Clip Library", url: "/library",  icon: "🎞️" },
  editor:    { title: "Editor",       url: "/",         icon: "🎬" },
  campaigns: { title: "Campaigns",    url: "/projects", icon: "📁" },
};

// Canonical left→right order. Panels always sit in this order regardless of the
// sequence they're opened in.
const PANEL_ORDER = ["library", "editor", "campaigns"];
const orderIndex = (key) => PANEL_ORDER.indexOf(key);

const LAYOUT_KEY = "studio.workspace.layout";

// open panels: [{ key, flex }] in left→right order. flex = relative width weight.
let layout = loadLayout();

function loadLayout() {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed.panels)) {
        return parsed.panels
          .filter((p) => PANELS[p.key])
          .sort((a, b) => orderIndex(a.key) - orderIndex(b.key));
      }
    }
  } catch (e) { /* ignore */ }
  // First run: Library + Editor side by side is the natural starting pair.
  return [{ key: "library", flex: 1 }, { key: "editor", flex: 1.4 }];
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ panels: layout }));
  } catch (e) { /* ignore */ }
}

const panes = document.getElementById("panes");
const empty = document.getElementById("panes-empty");

function render() {
  panes.innerHTML = "";
  empty.classList.toggle("hidden", layout.length > 0);

  layout.forEach((entry, i) => {
    const spec = PANELS[entry.key];
    const pane = document.createElement("section");
    pane.className = "pane";
    pane.style.flexGrow = String(entry.flex || 1);
    pane.dataset.key = entry.key;

    const head = document.createElement("div");
    head.className = "pane-head";
    const title = document.createElement("span");
    title.className = "pane-title";
    title.innerHTML = `<span class="pane-icon">${spec.icon}</span>${spec.title}`;
    const postToPane = (msg) => {
      const f = pane.querySelector(".pane-frame");
      f && f.contentWindow && f.contentWindow.postMessage(msg, "*");
    };

    // Export — a share-style icon, only for the editor pane, sitting left of the
    // gear. Clicking it asks the editor to run its export.
    if (entry.key === "editor") {
      const exportBtn = document.createElement("button");
      exportBtn.className = "pane-export";
      exportBtn.title = "Export this edit";
      exportBtn.setAttribute("aria-label", "Export");
      exportBtn.innerHTML =
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round"><path d="M12 3v12"/><path d="M8 7l4-4 4 4"/>' +
        '<path d="M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6"/></svg>';
      exportBtn.onclick = () => postToPane({ studio: "export" });
      head.appendChild(title);
      head.appendChild(exportBtn);
    } else {
      head.appendChild(title);
    }

    // Settings gear — sits just left of the close button. Clicking it asks the
    // pane's own page (in the iframe) to open its settings, via postMessage.
    const gear = document.createElement("button");
    gear.className = "pane-gear";
    gear.textContent = "⚙";
    gear.title = "Settings";
    gear.onclick = () => postToPane({ studio: "toggle-settings" });
    const close = document.createElement("button");
    close.className = "pane-close";
    close.textContent = "×";
    close.title = "Close panel";
    close.onclick = () => closePanel(entry.key);
    head.append(gear, close);

    const frame = document.createElement("iframe");
    frame.className = "pane-frame";
    frame.src = spec.url;
    frame.dataset.key = entry.key;
    // Same-origin panels: wire cross-panel drag from the Library once it loads.
    if (entry.key === "library") {
      frame.addEventListener("load", () => wireLibraryDrag(frame));
    }

    pane.append(head, frame);
    panes.appendChild(pane);

    // Resizer between this pane and the next.
    if (i < layout.length - 1) {
      const rz = document.createElement("div");
      rz.className = "resizer";
      rz.addEventListener("mousedown", (e) => startResize(e, i));
      panes.appendChild(rz);
    }
  });

  syncRail();
}

function syncRail() {
  const open = new Set(layout.map((p) => p.key));
  document.querySelectorAll(".rail-btn").forEach((btn) => {
    btn.classList.toggle("active", open.has(btn.dataset.panel));
  });
}

function openPanel(key) {
  if (!PANELS[key]) return;
  const existing = layout.find((p) => p.key === key);
  if (existing) {
    // Already open → focus it (flash + scroll into view) rather than duplicate.
    const pane = panes.querySelector(`.pane[data-key="${key}"]`);
    if (pane) {
      pane.scrollIntoView({ behavior: "smooth", inline: "center" });
      pane.classList.add("flash");
      setTimeout(() => pane.classList.remove("flash"), 600);
    }
    return;
  }
  layout.push({ key, flex: 1 });
  layout.sort((a, b) => orderIndex(a.key) - orderIndex(b.key));
  saveLayout();
  render();
}

function closePanel(key) {
  layout = layout.filter((p) => p.key !== key);
  saveLayout();
  render();
}

function togglePanel(key) {
  if (layout.find((p) => p.key === key)) closePanel(key);
  else openPanel(key);
}

document.querySelectorAll(".rail-btn").forEach((btn) => {
  btn.addEventListener("click", () => togglePanel(btn.dataset.panel));
});

// ---- resizing ----
function startResize(e, leftIndex) {
  e.preventDefault();
  const paneEls = [...panes.querySelectorAll(".pane")];
  const left = paneEls[leftIndex];
  const right = paneEls[leftIndex + 1];
  if (!left || !right) return;

  const startX = e.clientX;
  const leftStart = left.getBoundingClientRect().width;
  const rightStart = right.getBoundingClientRect().width;
  const totalFlex = (layout[leftIndex].flex || 1) + (layout[leftIndex + 1].flex || 1);
  const totalPx = leftStart + rightStart;

  // Block iframes from swallowing mouse events mid-drag.
  document.body.classList.add("resizing");

  function onMove(ev) {
    const dx = ev.clientX - startX;
    let leftPx = Math.max(160, Math.min(totalPx - 160, leftStart + dx));
    const leftFlex = (leftPx / totalPx) * totalFlex;
    layout[leftIndex].flex = leftFlex;
    layout[leftIndex + 1].flex = totalFlex - leftFlex;
    left.style.flexGrow = String(layout[leftIndex].flex);
    right.style.flexGrow = String(layout[leftIndex + 1].flex);
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    document.body.classList.remove("resizing");
    saveLayout();
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// ---- cross-panel messaging ----
// A panel's iframe can ask the shell to open/focus another panel, e.g. clicking a
// campaign's edit → open the Editor panel. Messages: {studio:'open', panel:'editor'}.
window.addEventListener("message", (e) => {
  const msg = e.data;
  if (!msg || !msg.studio) return;
  if (msg.studio === "open" && PANELS[msg.panel]) { openPanel(msg.panel); return; }
  // A panel changed a clip's data → fan the event out to every open panel so each
  // refreshes its own view. This is the shared-state channel that keeps panels from
  // going stale without merging them into one document.
  if (msg.studio === "clip-updated") {
    panes.querySelectorAll(".pane-frame").forEach((f) => {
      try { f.contentWindow && f.contentWindow.postMessage(msg, "*"); } catch (_) { /* cross-origin guard */ }
    });
  }
});

// ---- cross-panel drag: Clip Library card → Editor timeline ----
// The panels are same-origin iframes, so the shell can read the library's data and
// call the editor's drop hooks directly. During a drag we lay a transparent shield
// over all panes so mousemove/up come to the shell (not whichever iframe is under
// the cursor), track a ghost at the window level, and translate window coords into
// the editor iframe's local coords for its hit-test / insert.
function editorFrame() {
  return panes.querySelector('.pane-frame[data-key="editor"]');
}

function wireLibraryDrag(frame) {
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || doc._studioDragWired) return;
  doc._studioDragWired = true;

  doc.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const card = e.target.closest(".card");
    if (!card) return;
    const clipId = Number(card.dataset.clipId);
    const clip = win.studioLibrary && win.studioLibrary.getClip(clipId);
    if (!clip) return;

    const srcRect = frame.getBoundingClientRect();
    // Start point in shell (window) coordinates.
    const startGX = e.clientX + srcRect.left;
    const startGY = e.clientY + srcRect.top;
    let dragging = false, ghost = null, shield = null;

    const ed = () => editorFrame();
    const edLocal = (gx, gy) => {
      const r = ed().getBoundingClientRect();
      return { x: gx - r.left, y: gy - r.top, inFrame: gx >= r.left && gx <= r.right && gy >= r.top && gy <= r.bottom };
    };

    const begin = () => {
      dragging = true;
      shield = document.createElement("div");
      shield.className = "drag-shield";
      document.body.appendChild(shield);
      ghost = document.createElement("div");
      ghost.className = "shell-drag-ghost";
      ghost.textContent = clip.file_stem;
      document.body.appendChild(ghost);
      document.addEventListener("mousemove", onShellMove, true);
      document.addEventListener("mouseup", onShellUp, true);
    };

    const highlight = (on) => {
      const f = ed();
      if (f && f.contentWindow && f.contentWindow.studioEditor) f.contentWindow.studioEditor.highlight(on);
    };

    const onShellMove = (me) => {
      ghost.style.left = `${me.clientX + 10}px`;
      ghost.style.top = `${me.clientY + 10}px`;
      const f = ed();
      let over = false;
      if (f && f.contentWindow && f.contentWindow.studioEditor) {
        const l = edLocal(me.clientX, me.clientY);
        over = l.inFrame && f.contentWindow.studioEditor.overTimeline(l.x, l.y);
      }
      highlight(over);
      shield.classList.toggle("over", over);
    };

    const onShellUp = async (ue) => {
      document.removeEventListener("mousemove", onShellMove, true);
      document.removeEventListener("mouseup", onShellUp, true);
      if (ghost) ghost.remove();
      if (shield) shield.remove();
      highlight(false);
      const f = ed();
      if (!f || !f.contentWindow || !f.contentWindow.studioEditor) return;
      const l = edLocal(ue.clientX, ue.clientY);
      if (!l.inFrame || !f.contentWindow.studioEditor.overTimeline(l.x, l.y)) return;
      openPanel("editor"); // ensure it's focused
      await f.contentWindow.studioEditor.drop(clip, l.x);
    };

    // Detect the drag threshold from within the source iframe, then hand off to the
    // shield-level tracking above.
    const onSrcMove = (me) => {
      const gx = me.clientX + srcRect.left, gy = me.clientY + srcRect.top;
      if (Math.abs(gx - startGX) < 5 && Math.abs(gy - startGY) < 5) return;
      doc.removeEventListener("mousemove", onSrcMove, true);
      doc.removeEventListener("mouseup", onSrcUp, true);
      begin();
    };
    const onSrcUp = () => {
      doc.removeEventListener("mousemove", onSrcMove, true);
      doc.removeEventListener("mouseup", onSrcUp, true);
    };
    doc.addEventListener("mousemove", onSrcMove, true);
    doc.addEventListener("mouseup", onSrcUp, true);
  });
}

render();
