// Wrapped in an IIFE so this panel's top-level names (loadClips, clips,
// currentProjectId, …) stay private and don't collide with the Library panel when
// both run in one unified document. Cross-panel hooks are exposed via window.* below.
(function () {
// ---------- state ----------
let currentProjectId = null;
let currentEditId = null;
let clips = [];
let selectedClip = null;
let bulkSelection = new Set();   // clip ids checked for bulk "describe together"
let exiftoolAvailable = false;
let timeline = [];            // [{id, clip_id, file_stem, description, in_point, out_point, clip_duration_s, available_locally}]
let selectedItemId = null;
let pxPerSec = 40;

// program playback
const programVideo = document.getElementById("program-video");
let segments = [];            // derived from timeline: {item, start, end, dur, clipId, inP, outP}
let activeSeg = -1;
let playing = false;
let loadingSeg = false;
let rafId = null;

// ---------- api ----------
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

function fmt(t) {
  if (!isFinite(t)) t = 0;
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}

// ---------- source library ----------
async function loadClips(query = "") {
  clips = await api(`/api/clips?q=${encodeURIComponent(query)}`);
  renderClipList();
}

// ---- cross-panel clip sync ----
// Tell the shell a clip changed (no-op when opened standalone).
function broadcastClipUpdated(id) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ studio: "clip-updated", clipId: Number(id) }, "*");
    }
  } catch (_) { /* ignore */ }
}

// Another panel (e.g. the Library) changed a clip → refresh the source bin and the
// timeline so descriptions/tags shown here don't go stale.
window.addEventListener("message", (e) => {
  if (!e.data || e.data.studio !== "clip-updated") return;
  const searchEl = document.getElementById("search");
  loadClips(searchEl ? searchEl.value : "");
  if (currentEditId) loadTimeline();
});

function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderClipList() {
  const list = document.getElementById("clip-list");
  list.innerHTML = "";
  clips.forEach((clip) => {
    const el = document.createElement("div");
    el.className = "clip-item" +
      (clip.available_locally ? "" : " unavailable") +
      (selectedClip && selectedClip.id === clip.id ? " selected" : "") +
      (bulkSelection.has(clip.id) ? " bulk-selected" : "");
    el.innerHTML = `
      <input type="checkbox" class="clip-check" ${bulkSelection.has(clip.id) ? "checked" : ""}>
      <div class="clip-body">
        <div class="stem">${esc(clip.file_stem)}${clip.available_locally ? "" : " · not local"}</div>
        <div class="desc">${esc(clip.description || "")}</div>
      </div>`;
    const check = el.querySelector(".clip-check");
    check.onclick = (e) => {
      e.stopPropagation();
      if (check.checked) bulkSelection.add(clip.id); else bulkSelection.delete(clip.id);
      el.classList.toggle("bulk-selected", check.checked);
      updateBulkBar();
    };
    const body = el.querySelector(".clip-body");
    body.onclick = () => selectClip(clip);
    makeClipDraggable(el, body, clip);
    list.appendChild(el);
  });
}

// Is a point (in THIS document's client coords) over the timeline drop zone?
// Hit-test by what's under the cursor rather than the region's box — the region can
// report zero width in this flex layout even though its content renders full-width.
function pointOverTimeline(x, y) {
  const region = document.getElementById("timeline-region");
  const under = document.elementFromPoint(x, y);
  return !!region && !!under && region.contains(under);
}

// Insert a clip onto the timeline at the drop X (client coords in THIS document).
// Shared by the in-editor drag and the cross-panel drop bridge. Returns a short
// status the caller can surface.
async function placeClipOnTimeline(clip, clientX) {
  if (!currentEditId) return { ok: false, msg: "Create or pick an edit first." };
  if (!clip.available_locally) return { ok: false, msg: `"${clip.file_stem}" isn't downloaded yet.` };
  const dur = clip.duration_s || 0;
  if (!(dur > 0)) return { ok: false, msg: `"${clip.file_stem}" has no known duration.` };

  // Insertion index from the drop X (same math as timeline reorder). Past the last
  // clip's end -> append.
  const rect = document.getElementById("track").getBoundingClientRect();
  const scroll = document.getElementById("timeline-scroll").scrollLeft;
  const dropTime = (clientX - rect.left + scroll) / pxPerSec;
  let targetIdx = segments.length;
  if (segments.length && dropTime < totalDuration()) {
    targetIdx = Math.max(0, Math.min(segments.length, segAtTime(dropTime)));
  }
  await api(`/api/edits/${currentEditId}/items`, {
    method: "POST",
    body: JSON.stringify({
      clip_id: clip.id, in_point: 0, out_point: +dur.toFixed(2), position: targetIdx,
    }),
  });
  await loadTimeline();
  return { ok: true };
}

// ---- cross-panel drop bridge ----
// The workspace shell drives drags from the Clip Library panel (a sibling iframe)
// and, on drop over this editor's timeline, calls these same-origin hooks. All
// coordinates are in THIS iframe's client space (the shell translates them).
window.studioEditor = {
  overTimeline: (localX, localY) => pointOverTimeline(localX, localY),
  highlight: (on) => {
    const r = document.getElementById("timeline-region");
    if (r) r.classList.toggle("drop-target", !!on);
  },
  drop: async (clip, localX) => {
    const res = await placeClipOnTimeline(clip, localX);
    if (!res.ok) alert(res.msg);
    return res;
  },
};

// Drag a source clip onto the timeline to add it at the drop position (in-editor).
function makeClipDraggable(cardEl, handleEl, clip) {
  handleEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const startX = e.clientX, startY = e.clientY;
    let ghost = null, dragging = false;
    const region = document.getElementById("timeline-region");

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
      region.classList.toggle("drop-target", pointOverTimeline(me.clientX, me.clientY));
    };

    const onUp = async (ue) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (ghost) ghost.remove();
      region.classList.remove("drop-target");
      if (!dragging) return; // was a click; selectClip handles it
      cardEl.addEventListener("click", (ce) => ce.stopImmediatePropagation(),
        { once: true, capture: true });
      if (!pointOverTimeline(ue.clientX, ue.clientY)) return;
      const res = await placeClipOnTimeline(clip, ue.clientX);
      if (!res.ok) alert(res.msg);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function fillMetadataEditor(clip) {
  document.getElementById("meta-description").value = clip.description || "";
  document.getElementById("meta-category").value = clip.category || "";
  document.getElementById("meta-tags").value = clip.tags || "";
  document.getElementById("meta-context").value = clip.context || "";
  document.getElementById("save-meta-result").textContent = "";
}

function selectClip(clip) {
  selectedClip = clip;
  renderClipList();
  const v = document.getElementById("source-video");
  v.src = `/api/clips/${clip.id}/media`;
  document.getElementById("source-info").textContent =
    `${clip.file_stem} — ${clip.duration_s || "?"}s — ${clip.category || ""}`;
  const avail = clip.available_locally;
  document.getElementById("source-controls").style.display = avail ? "flex" : "none";
  document.getElementById("source-tools").style.display = avail ? "flex" : "none";
  document.getElementById("metadata-editor").style.display = "block";
  document.getElementById("in-point").value = 0;
  document.getElementById("out-point").value = clip.duration_s || 0;
  document.getElementById("transcript-box").textContent = clip.transcript ? `Transcript: ${clip.transcript}` : "";
  document.getElementById("analysis-box").textContent = clip.tags ? `Tags: ${clip.tags}` : "";
  fillMetadataEditor(clip);
}

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  const n = bulkSelection.size;
  bar.style.display = n ? "block" : "none";
  document.getElementById("bulk-count").textContent = `${n} selected`;
}

// ---------- projects (themes) / edits (timelines) ----------
// currentProjectId = the theme; currentEditId = the timeline being edited.
async function loadProjects() {
  const projects = await api("/api/projects");
  const select = document.getElementById("project-select");
  select.innerHTML = "";
  // A pseudo-option for edits not filed under any project.
  const unassigned = document.createElement("option");
  unassigned.value = "";
  unassigned.textContent = "(Unassigned)";
  select.appendChild(unassigned);
  projects.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = String(p.id);
    opt.textContent = p.name;
    select.appendChild(opt);
  });
  // Inline creation: picking this runs the new-campaign flow (no separate button).
  const newOpt = document.createElement("option");
  newOpt.value = "__new__";
  newOpt.textContent = "+ New campaign…";
  select.appendChild(newOpt);
  const ids = projects.map((p) => String(p.id));
  if (currentProjectId === null) {
    currentProjectId = projects.length ? String(projects[0].id) : "";
  } else if (currentProjectId && !ids.includes(String(currentProjectId))) {
    currentProjectId = "";
  }
  select.value = currentProjectId == null ? "" : String(currentProjectId);
  await loadEdits();
}

async function loadEdits() {
  const q = currentProjectId ? `?project=${currentProjectId}` : "";
  const edits = await api(`/api/edits${q}`);
  // Default to the current edit if still valid, else the campaign's newest.
  const ids = edits.map((e) => String(e.id));
  if (currentEditId == null || !ids.includes(String(currentEditId))) {
    currentEditId = edits.length ? String(edits[0].id) : null;
  }
  const label = document.getElementById("current-edit-name");
  if (label) {
    const cur = edits.find((e) => String(e.id) === String(currentEditId));
    label.textContent = cur ? cur.name : "No edit yet — type a prompt and Generate";
    label.classList.toggle("muted", !cur);
  }
  if (currentEditId) {
    await loadTimeline();
  } else {
    timeline = [];
    rebuildSegments();
    renderTimeline();
  }
}

async function loadTimeline() {
  if (!currentEditId) return;
  const edit = await api(`/api/edits/${currentEditId}`);
  timeline = edit.items;
  const aspectSel = document.getElementById("aspect-select");
  if (aspectSel) aspectSel.value = edit.aspect || "source";
  if (!timeline.some((i) => i.id === selectedItemId)) selectedItemId = null;
  rebuildSegments();
  renderTimeline();
}

// Persist the output frame/aspect on the current edit.
document.getElementById("aspect-select").addEventListener("change", async (e) => {
  if (!currentEditId) { e.target.value = "source"; alert("Create or pick an edit first."); return; }
  await api(`/api/edits/${currentEditId}`, {
    method: "PUT",
    body: JSON.stringify({ aspect: e.target.value }),
  });
  if (typeof refreshCropOverlay === "function") refreshCropOverlay();
});

// ---- settings gear popover ----
(function () {
  const btn = document.getElementById("settings-btn");
  const pop = document.getElementById("settings-popover");
  const wrap = document.getElementById("settings-wrap");
  if (!btn || !pop) return;
  const setOpen = (open) => {
    pop.classList.toggle("hidden", !open);
    btn.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(pop.classList.contains("hidden"));
  });
  // Close on outside click or Escape.
  document.addEventListener("click", (e) => {
    if (!pop.classList.contains("hidden") && !wrap.contains(e.target)) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });

  // In the workspace shell the pane header owns the gear (top-right, left of the
  // ×) and drives this popover via postMessage; hide the redundant in-toolbar
  // button but keep its wrapper as the popover's anchor.
  if (window.self !== window.top) btn.style.display = "none";
  window.addEventListener("message", (e) => {
    if (e.data && e.data.studio === "toggle-settings") {
      setOpen(pop.classList.contains("hidden"));
    }
  });
})();

function rebuildSegments() {
  segments = [];
  let t = 0;
  timeline.forEach((item) => {
    const dur = Math.max(0, item.out_point - item.in_point);
    segments.push({
      item, start: t, end: t + dur, dur,
      clipId: item.clip_id, inP: item.in_point, outP: item.out_point,
    });
    t += dur;
  });
}

function totalDuration() {
  return segments.length ? segments[segments.length - 1].end : 0;
}

function renderTimeline() {
  const total = totalDuration();
  const width = Math.max(total * pxPerSec + 40, document.getElementById("timeline-scroll").clientWidth);

  // ruler
  const ruler = document.getElementById("ruler");
  ruler.innerHTML = "";
  ruler.style.width = `${width}px`;
  const step = pxPerSec >= 60 ? 1 : pxPerSec >= 25 ? 2 : 5;
  for (let s = 0; s <= total + step; s += step) {
    const tick = document.createElement("div");
    tick.className = "tick";
    tick.style.left = `${s * pxPerSec}px`;
    tick.textContent = fmt(s);
    ruler.appendChild(tick);
  }

  // track
  const track = document.getElementById("track");
  const playhead = document.getElementById("playhead");
  track.querySelectorAll(".tl-clip").forEach((n) => n.remove());
  track.style.width = `${width}px`;

  segments.forEach((seg) => {
    const el = document.createElement("div");
    el.className = "tl-clip" + (seg.item.id === selectedItemId ? " selected" : "");
    el.style.left = `${seg.start * pxPerSec}px`;
    el.style.width = `${Math.max(seg.dur * pxPerSec, 12)}px`;
    el.innerHTML = `
      <div class="tl-label">${seg.item.file_stem}
        <span class="tl-sub">${seg.dur.toFixed(1)}s</span></div>
      <div class="tl-handle left"></div>
      <div class="tl-handle right"></div>`;
    attachClipInteractions(el, seg);
    track.appendChild(el);
  });
  track.appendChild(playhead);
  updatePlayhead(currentGlobalTime());
  document.getElementById("time-readout").textContent = `${fmt(currentGlobalTime())} / ${fmt(total)}`;
  if (typeof refreshCropOverlay === "function") refreshCropOverlay();
}

// ---------- playhead / seeking ----------
function currentGlobalTime() {
  if (activeSeg < 0 || activeSeg >= segments.length) return 0;
  const seg = segments[activeSeg];
  return seg.start + Math.max(0, Math.min(seg.dur, programVideo.currentTime - seg.inP));
}

function updatePlayhead(t) {
  document.getElementById("playhead").style.left = `${t * pxPerSec}px`;
}

function segAtTime(t) {
  for (let i = 0; i < segments.length; i++) {
    if (t >= segments[i].start && t < segments[i].end) return i;
  }
  return segments.length ? segments.length - 1 : -1;
}

function loadSegment(i, withinSeconds, thenPlay) {
  if (i < 0 || i >= segments.length) return;
  const seg = segments[i];
  activeSeg = i;
  const wantSrc = `/api/clips/${seg.clipId}/media`;
  const seekTo = seg.inP + Math.max(0, withinSeconds || 0);

  const doSeek = () => {
    programVideo.currentTime = seekTo;
    loadingSeg = false;
    if (thenPlay) programVideo.play();
  };

  if (!programVideo.src.endsWith(wantSrc)) {
    loadingSeg = true;
    programVideo.src = wantSrc;
    programVideo.addEventListener("loadeddata", doSeek, { once: true });
  } else {
    doSeek();
  }
}

function seekGlobal(t, thenPlay) {
  const total = totalDuration();
  t = Math.max(0, Math.min(total, t));
  const i = segAtTime(t);
  if (i < 0) return;
  loadSegment(i, t - segments[i].start, thenPlay);
  updatePlayhead(t);
  document.getElementById("time-readout").textContent = `${fmt(t)} / ${fmt(total)}`;
}

// ---------- transport ----------
function play() {
  if (!segments.length) return;
  if (currentGlobalTime() >= totalDuration() - 0.05) seekGlobal(0, false);
  playing = true;
  document.getElementById("play-pause").textContent = "❚❚";
  if (activeSeg < 0) loadSegment(0, 0, true);
  else programVideo.play();
  tick();
}

function pause() {
  playing = false;
  document.getElementById("play-pause").textContent = "▶";
  programVideo.pause();
  if (rafId) cancelAnimationFrame(rafId);
}

function togglePlay() { playing ? pause() : play(); }

function tick() {
  if (!playing) return;
  if (!loadingSeg && activeSeg >= 0) {
    const seg = segments[activeSeg];
    if (programVideo.currentTime >= seg.outP - 0.02) {
      if (activeSeg + 1 < segments.length) {
        loadSegment(activeSeg + 1, 0, true);
      } else {
        pause();
        updatePlayhead(totalDuration());
        return;
      }
    } else {
      const t = currentGlobalTime();
      updatePlayhead(t);
      document.getElementById("time-readout").textContent = `${fmt(t)} / ${fmt(totalDuration())}`;
    }
  }
  rafId = requestAnimationFrame(tick);
}

// ---------- timeline clip interactions (select / drag-reorder / trim) ----------
function attachClipInteractions(el, seg) {
  const onDown = (e) => {
    if (e.target.classList.contains("tl-handle")) return; // handled below
    e.preventDefault();
    selectedItemId = seg.item.id;
    renderTimeline();
    seekGlobal(seg.start, false);

    const startX = e.clientX;
    let moved = false;
    const onMove = (me) => {
      if (Math.abs(me.clientX - startX) > 4) moved = true;
    };
    const onUp = (ue) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (!moved) return;
      // reorder: figure out drop index from pointer global time
      const rect = document.getElementById("track").getBoundingClientRect();
      const scroll = document.getElementById("timeline-scroll").scrollLeft;
      const dropTime = (ue.clientX - rect.left + scroll) / pxPerSec;
      const targetIdx = Math.max(0, Math.min(segments.length - 1, segAtTime(dropTime)));
      reorderItem(seg.item.id, targetIdx);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };
  el.addEventListener("mousedown", onDown);

  el.querySelector(".tl-handle.left").addEventListener("mousedown", (e) => startTrim(e, seg, "in"));
  el.querySelector(".tl-handle.right").addEventListener("mousedown", (e) => startTrim(e, seg, "out"));
}

function startTrim(e, seg, edge) {
  e.preventDefault();
  e.stopPropagation();
  const startX = e.clientX;
  const origIn = seg.item.in_point;
  const origOut = seg.item.out_point;
  const maxDur = seg.item.clip_duration_s || origOut;

  const onMove = (me) => {
    const dt = (me.clientX - startX) / pxPerSec;
    if (edge === "in") {
      seg.item.in_point = Math.max(0, Math.min(origIn + dt, seg.item.out_point - 0.1));
    } else {
      seg.item.out_point = Math.min(maxDur, Math.max(origOut + dt, seg.item.in_point + 0.1));
    }
    rebuildSegments();
    renderTimeline();
  };
  const onUp = async () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    await api(`/api/edits/${currentEditId}/items/${seg.item.id}`, {
      method: "PUT",
      body: JSON.stringify({
        in_point: +seg.item.in_point.toFixed(2),
        out_point: +seg.item.out_point.toFixed(2),
      }),
    });
    await loadTimeline();
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

async function reorderItem(itemId, targetIdx) {
  const ids = timeline.map((i) => i.id);
  const from = ids.indexOf(itemId);
  if (from === -1 || from === targetIdx) return;
  ids.splice(from, 1);
  ids.splice(targetIdx, 0, itemId);
  await api(`/api/edits/${currentEditId}/reorder`, {
    method: "POST", body: JSON.stringify({ item_ids: ids }),
  });
  await loadTimeline();
}

async function removeSelected() {
  if (!selectedItemId) return;
  await api(`/api/edits/${currentEditId}/items/${selectedItemId}`, { method: "DELETE" });
  selectedItemId = null;
  if (playing) pause();
  activeSeg = -1;
  await loadTimeline();
}

// click on empty ruler/track to scrub
function trackClickToSeek(e) {
  if (e.target.classList.contains("tl-clip") || e.target.closest(".tl-clip")) return;
  const rect = document.getElementById("track").getBoundingClientRect();
  const scroll = document.getElementById("timeline-scroll").scrollLeft;
  const t = (e.clientX - rect.left + scroll) / pxPerSec;
  seekGlobal(t, false);
}

// ---------- wiring ----------
document.getElementById("search").addEventListener("input", (e) => loadClips(e.target.value));

async function createCampaign() {
  const name = prompt("Campaign name (theme, e.g. \"Holiday campaign\", \"Gardening\")?");
  if (!name) return false;
  const project = await api("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
  currentProjectId = String(project.id);
  currentEditId = null;
  await loadProjects();
  return true;
}

document.getElementById("project-select").addEventListener("change", async (e) => {
  if (e.target.value === "__new__") {
    // createCampaign() reloads on success; on cancel, reload to restore the dropdown
    // to its real selection (so it doesn't sit on "+ New campaign…").
    if (!(await createCampaign())) await loadProjects();
    return;
  }
  currentProjectId = e.target.value; // "" = Unassigned
  currentEditId = null;              // pick the campaign's first edit
  if (playing) pause();
  activeSeg = -1;
  loadEdits();
});


document.getElementById("set-in").addEventListener("click", () => {
  document.getElementById("in-point").value = document.getElementById("source-video").currentTime.toFixed(1);
});
document.getElementById("set-out").addEventListener("click", () => {
  document.getElementById("out-point").value = document.getElementById("source-video").currentTime.toFixed(1);
});

document.getElementById("add-to-timeline").addEventListener("click", async () => {
  if (!selectedClip) return;
  if (!currentEditId) { alert("Create or pick an edit first."); return; }
  const inPoint = parseFloat(document.getElementById("in-point").value);
  const outPoint = parseFloat(document.getElementById("out-point").value);
  if (!(outPoint > inPoint)) { alert("Out must be greater than In."); return; }
  await api(`/api/edits/${currentEditId}/items`, {
    method: "POST",
    body: JSON.stringify({ clip_id: selectedClip.id, in_point: inPoint, out_point: outPoint }),
  });
  await loadTimeline();
});

async function doExport() {
  const el = document.getElementById("export-result");
  if (!currentEditId) { el.textContent = "Pick an edit to export."; return; }
  el.textContent = "Exporting…";
  try {
    const r = await api(`/api/edits/${currentEditId}/export`, { method: "POST" });
    el.textContent = `Exported to ${r.output}`;
  } catch (err) { el.textContent = `Error: ${err.message}`; }
}
document.getElementById("export-btn").addEventListener("click", doExport);
// The workspace pane header hosts an Export icon (left of the gear) that fires this
// via postMessage; hide the redundant in-toolbar Export button when embedded.
if (window.self !== window.top) {
  document.getElementById("export-btn").style.display = "none";
}
window.addEventListener("message", (e) => {
  if (e.data && e.data.studio === "export") doExport();
});

// ---- double-click the edit name to rename it ----
(function () {
  const label = document.getElementById("current-edit-name");
  const input = document.getElementById("edit-name-input");
  if (!label || !input) return;

  function begin() {
    if (!currentEditId) return;              // nothing to rename yet
    input.value = label.textContent;
    label.classList.add("hidden");
    input.classList.remove("hidden");
    input.focus();
    input.select();
  }
  async function commit(save) {
    if (input.classList.contains("hidden")) return;
    const name = input.value.trim();
    input.classList.add("hidden");
    label.classList.remove("hidden");
    if (save && name && name !== label.textContent) {
      try {
        await api(`/api/edits/${currentEditId}`, {
          method: "PUT", body: JSON.stringify({ name }),
        });
        label.textContent = name;
        label.classList.remove("muted");
        // Reflect the new name in the campaign/edit lists too.
        if (typeof loadEdits === "function") loadEdits();
      } catch (err) { /* keep old name on failure */ }
    }
  }
  label.addEventListener("dblclick", begin);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(true); }
    else if (e.key === "Escape") { e.preventDefault(); commit(false); }
  });
  input.addEventListener("blur", () => commit(true));
})();

document.getElementById("generate-btn").addEventListener("click", async () => {
  const el = document.getElementById("generate-result");
  const prompt = document.getElementById("generate-prompt").value.trim();
  if (!prompt) return;
  el.textContent = "Generating…";
  try {
    // Generate always starts a NEW edit (a fresh timeline), auto-named from the
    // prompt. To reshape an existing edit, use the Edit Chat panel instead.
    const body = { prompt };
    if (currentProjectId) body.project_id = parseInt(currentProjectId, 10);
    const r = await api("/api/generate-edit", { method: "POST", body: JSON.stringify(body) });
    currentEditId = String(r.id);
    await loadEdits();
    document.getElementById("generate-prompt").value = "";
    el.textContent = `New edit · ${r.selections.length} clips`;
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

document.getElementById("transcribe-btn").addEventListener("click", async () => {
  if (!selectedClip) return;
  const box = document.getElementById("transcript-box");
  box.textContent = "Transcribing…";
  try {
    const r = await api(`/api/clips/${selectedClip.id}/transcribe`, { method: "POST" });
    selectedClip.transcript = r.transcript;
    box.textContent = `Transcript: ${r.transcript}`;
  } catch (err) { box.textContent = `Error: ${err.message}`; }
});

document.getElementById("analyze-btn").addEventListener("click", async () => {
  if (!selectedClip) return;
  const box = document.getElementById("analysis-box");
  box.textContent = "Analyzing…";
  try {
    const r = await api(`/api/clips/${selectedClip.id}/analyze`, { method: "POST" });
    selectedClip.description = r.description;
    selectedClip.category = r.category;
    selectedClip.tags = r.tags.join(", ");
    selectedClip.context = r.context || selectedClip.context || "";
    let note = `${r.category} — ${r.description} Tags: ${r.tags.join(", ")}`;
    if (r.stamped && r.stamped.ok) note += " · embedded in file";
    box.textContent = note;
    document.getElementById("source-info").textContent =
      `${selectedClip.file_stem} — ${selectedClip.duration_s || "?"}s — ${r.category}`;
    fillMetadataEditor(selectedClip);   // keeps your context, shows merged tags
    renderClipList();
  } catch (err) { box.textContent = `Error: ${err.message}`; }
});

document.getElementById("drive-import-btn").addEventListener("click", async () => {
  const el = document.getElementById("drive-import-result");
  const urls = document.getElementById("drive-links").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  el.textContent = "Importing…";
  try {
    const r = await api("/api/drive-import", { method: "POST", body: JSON.stringify({ urls }) });
    el.innerHTML = "";
    r.results.forEach((res) => {
      const line = document.createElement("div");
      if (res.status === "error") line.textContent = `Failed: ${res.url} — ${res.error}`;
      else if (res.status === "matched_existing") line.textContent = `${res.filename} — matched "${res.file_stem}", now local`;
      else line.textContent = `${res.filename} — added "${res.file_stem}"`;
      el.appendChild(line);
    });
    document.getElementById("drive-links").value = "";
    await loadClips(document.getElementById("search").value);
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

document.getElementById("suggest-content-btn").addEventListener("click", async () => {
  const c = document.getElementById("content-ideas");
  c.textContent = "Thinking…";
  try {
    const r = await api("/api/suggest-content", { method: "POST" });
    c.innerHTML = "";
    r.ideas.forEach((idea) => {
      const el = document.createElement("div");
      el.className = "idea-item";
      el.innerHTML = `<div class="idea-title">${idea.idea}</div><div class="idea-rationale">${idea.rationale}</div>`;
      c.appendChild(el);
    });
  } catch (err) { c.textContent = `Error: ${err.message}`; }
});

// transport buttons
document.getElementById("play-pause").addEventListener("click", togglePlay);
document.getElementById("jump-start").addEventListener("click", () => seekGlobal(0, false));
document.getElementById("jump-end").addEventListener("click", () => seekGlobal(totalDuration(), false));
document.getElementById("step-back").addEventListener("click", () => {
  const i = Math.max(0, (activeSeg < 0 ? 0 : activeSeg) - 1);
  if (segments.length) seekGlobal(segments[i].start, false);
});
document.getElementById("step-fwd").addEventListener("click", () => {
  const i = Math.min(segments.length - 1, (activeSeg < 0 ? 0 : activeSeg) + 1);
  if (segments.length) seekGlobal(segments[i].start, false);
});

// zoom
document.getElementById("zoom-in").addEventListener("click", () => { pxPerSec = Math.min(200, pxPerSec * 1.4); renderTimeline(); });
document.getElementById("zoom-out").addEventListener("click", () => { pxPerSec = Math.max(8, pxPerSec / 1.4); renderTimeline(); });

// scrub by clicking the timeline
document.getElementById("timeline-scroll").addEventListener("click", trackClickToSeek);

// keyboard shortcuts
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); removeSelected(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); seekGlobal(currentGlobalTime() + 0.5, false); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); seekGlobal(currentGlobalTime() - 0.5, false); }
});

// ---------- metadata: save single clip ----------
document.getElementById("save-meta-btn").addEventListener("click", async () => {
  if (!selectedClip) return;
  const el = document.getElementById("save-meta-result");
  const stamp = document.getElementById("meta-stamp").checked;
  const payload = {
    description: document.getElementById("meta-description").value,
    category: document.getElementById("meta-category").value,
    tags: document.getElementById("meta-tags").value,
    context: document.getElementById("meta-context").value,
    stamp,
  };
  el.textContent = "Saving…";
  try {
    const r = await api(`/api/clips/${selectedClip.id}/metadata`, {
      method: "PUT", body: JSON.stringify(payload),
    });
    Object.assign(selectedClip, {
      description: r.description, category: r.category, tags: r.tags, context: r.context,
    });
    let msg = "Saved";
    if (stamp) msg += r.stamped && r.stamped.ok ? " · embedded in file"
      : ` · file not embedded (${(r.stamped && r.stamped.error) || "no local file"})`;
    el.textContent = msg;
    document.getElementById("source-info").textContent =
      `${selectedClip.file_stem} — ${selectedClip.duration_s || "?"}s — ${r.category}`;
    renderClipList();
    broadcastClipUpdated(selectedClip.id);  // let the Library refresh this clip
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

// ---------- metadata: bulk describe selected ----------
document.getElementById("bulk-clear").addEventListener("click", () => {
  bulkSelection.clear();
  updateBulkBar();
  renderClipList();
});

document.getElementById("bulk-apply").addEventListener("click", async () => {
  const el = document.getElementById("bulk-result");
  if (!bulkSelection.size) return;
  const payload = { clip_ids: [...bulkSelection], stamp: document.getElementById("bulk-stamp").checked };
  const cat = document.getElementById("bulk-category").value.trim();
  const tags = document.getElementById("bulk-tags").value.trim();
  const ctx = document.getElementById("bulk-context").value.trim();
  if (cat) payload.category = cat;
  if (tags) payload.tags = tags;
  if (ctx) payload.context = ctx;
  if (!cat && !tags && !ctx) { el.textContent = "Enter something to apply."; return; }
  el.textContent = "Applying…";
  try {
    const r = await api("/api/clips/metadata-bulk", { method: "POST", body: JSON.stringify(payload) });
    el.textContent = `Applied to ${r.updated} clip(s)`;
    document.getElementById("bulk-category").value = "";
    document.getElementById("bulk-tags").value = "";
    document.getElementById("bulk-context").value = "";
    await loadClips(document.getElementById("search").value);
    updateBulkBar();
    [...bulkSelection].forEach(broadcastClipUpdated);  // refresh these clips elsewhere
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

// ---------- metadata index: embed-all / export xlsx ----------
document.getElementById("stamp-all-btn").addEventListener("click", async () => {
  const el = document.getElementById("metadata-index-result");
  el.textContent = "Embedding…";
  try {
    const r = await api("/api/clips/stamp-all", { method: "POST" });
    el.textContent = `Embedded into ${r.stamped} file(s); ${r.skipped_not_local} not local`
      + (r.failed.length ? `; ${r.failed.length} failed` : "");
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

document.getElementById("export-xlsx-btn").addEventListener("click", async () => {
  const el = document.getElementById("metadata-index-result");
  el.textContent = "Exporting…";
  try {
    const r = await api("/api/export-metadata-xlsx", { method: "POST" });
    el.textContent = `Wrote ${r.updated} row(s) to ${r.file}`;
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

async function probeEnv() {
  try {
    const r = await api("/api/env");
    exiftoolAvailable = !!r.exiftool;
  } catch { exiftoolAvailable = false; }
  if (!exiftoolAvailable) {
    // Hide the "embed in file" affordances if exiftool isn't installed.
    document.querySelectorAll(".stamp-opt").forEach((n) => (n.style.display = "none"));
    const sa = document.getElementById("stamp-all-btn");
    if (sa) { sa.disabled = true; sa.title = "Install exiftool (brew install exiftool) to embed metadata into files"; }
  }
}

window.addEventListener("resize", () => { if (currentEditId) renderTimeline(); });

// Deep-link support:
//   /?edit=<id>    open straight into that edit (library "Assemble" jumps here)
//   /?project=<id> open into a project (its first edit)
const params = new URLSearchParams(location.search);
const requestedEdit = params.get("edit");
const requestedProject = params.get("project");

// ---- live refresh: auto-surface edits created elsewhere (e.g. via the MCP) ----
// Both the desktop UI and the MCP write to the same backend, but this window only
// re-reads on demand. Poll for brand-new edits and open the newest automatically,
// so "ask Claude here → watch it appear in the editor" needs no manual reload.
let _knownMaxEditId = 0;
let _liveBaselineSet = false;
let _liveTimer = null;

function flashStatus(msg) {
  const el = document.getElementById("generate-result");
  if (!el) return;
  el.textContent = msg;
  clearTimeout(flashStatus._t);
  flashStatus._t = setTimeout(() => { el.textContent = ""; }, 6000);
}

async function pollForNewEdits() {
  let edits;
  try { edits = await api("/api/edits"); } catch { return; }
  if (!edits.length) return;
  const maxId = Math.max(...edits.map((e) => Number(e.id)));
  // First run just records the baseline — don't hijack the view to an existing edit.
  if (!_liveBaselineSet) { _knownMaxEditId = maxId; _liveBaselineSet = true; return; }
  if (maxId <= _knownMaxEditId) return;
  _knownMaxEditId = maxId;
  const fresh = edits.find((e) => Number(e.id) === maxId);
  if (!fresh) return;
  if (playing) pause();
  currentProjectId = fresh.project_id != null ? String(fresh.project_id) : "";
  currentEditId = String(fresh.id);
  activeSeg = -1;
  await loadProjects(); // rebuilds campaign selector + loads the new edit's timeline
  flashStatus(`New edit loaded: “${fresh.name}”`);
}

function startLiveRefresh() {
  if (_liveTimer) return;
  pollForNewEdits();                 // set the baseline promptly
  _liveTimer = setInterval(pollForNewEdits, 4000);
}

async function init() {
  probeEnv();
  loadClips();
  if (requestedEdit) {
    // Resolve which project the deep-linked edit lives in so the selectors line up.
    try {
      const edit = await api(`/api/edits/${requestedEdit}`);
      currentEditId = String(edit.id);
      currentProjectId = edit.project_id != null ? String(edit.project_id) : "";
    } catch { /* fall through to defaults */ }
  } else if (requestedProject) {
    currentProjectId = requestedProject;
  }
  await loadProjects();
  startLiveRefresh();
}

init();
})();
