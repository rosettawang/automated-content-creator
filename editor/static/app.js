// ---------- state ----------
let currentProjectId = null;
let clips = [];
let selectedClip = null;
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

function renderClipList() {
  const list = document.getElementById("clip-list");
  list.innerHTML = "";
  clips.forEach((clip) => {
    const el = document.createElement("div");
    el.className = "clip-item" +
      (clip.available_locally ? "" : " unavailable") +
      (selectedClip && selectedClip.id === clip.id ? " selected" : "");
    el.innerHTML = `
      <div class="stem">${clip.file_stem}${clip.available_locally ? "" : " · not local"}</div>
      <div class="desc">${clip.description || ""}</div>`;
    el.onclick = () => selectClip(clip);
    list.appendChild(el);
  });
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
  document.getElementById("in-point").value = 0;
  document.getElementById("out-point").value = clip.duration_s || 0;
  document.getElementById("transcript-box").textContent = clip.transcript ? `Transcript: ${clip.transcript}` : "";
  document.getElementById("analysis-box").textContent = clip.tags ? `Tags: ${clip.tags}` : "";
}

// ---------- projects / timeline ----------
async function loadProjects() {
  const projects = await api("/api/projects");
  const select = document.getElementById("project-select");
  select.innerHTML = "";
  projects.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    select.appendChild(opt);
  });
  if (projects.length && currentProjectId === null) {
    currentProjectId = projects[0].id;
    select.value = currentProjectId;
  }
  if (currentProjectId) await loadTimeline();
}

async function loadTimeline() {
  if (!currentProjectId) return;
  const project = await api(`/api/projects/${currentProjectId}`);
  timeline = project.items;
  if (!timeline.some((i) => i.id === selectedItemId)) selectedItemId = null;
  rebuildSegments();
  renderTimeline();
}

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
    await api(`/api/projects/${currentProjectId}/items/${seg.item.id}`, {
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
  await api(`/api/projects/${currentProjectId}/reorder`, {
    method: "POST", body: JSON.stringify({ item_ids: ids }),
  });
  await loadTimeline();
}

async function removeSelected() {
  if (!selectedItemId) return;
  await api(`/api/projects/${currentProjectId}/items/${selectedItemId}`, { method: "DELETE" });
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

document.getElementById("project-select").addEventListener("change", (e) => {
  currentProjectId = parseInt(e.target.value, 10);
  if (playing) pause();
  activeSeg = -1;
  loadTimeline();
});

document.getElementById("new-project").addEventListener("click", async () => {
  const name = prompt("Project name?");
  if (!name) return;
  const project = await api("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
  currentProjectId = project.id;
  await loadProjects();
  document.getElementById("project-select").value = currentProjectId;
});

document.getElementById("set-in").addEventListener("click", () => {
  document.getElementById("in-point").value = document.getElementById("source-video").currentTime.toFixed(1);
});
document.getElementById("set-out").addEventListener("click", () => {
  document.getElementById("out-point").value = document.getElementById("source-video").currentTime.toFixed(1);
});

document.getElementById("add-to-timeline").addEventListener("click", async () => {
  if (!selectedClip || !currentProjectId) return;
  const inPoint = parseFloat(document.getElementById("in-point").value);
  const outPoint = parseFloat(document.getElementById("out-point").value);
  if (!(outPoint > inPoint)) { alert("Out must be greater than In."); return; }
  await api(`/api/projects/${currentProjectId}/items`, {
    method: "POST",
    body: JSON.stringify({ clip_id: selectedClip.id, in_point: inPoint, out_point: outPoint }),
  });
  await loadTimeline();
});

document.getElementById("export-btn").addEventListener("click", async () => {
  const el = document.getElementById("export-result");
  el.textContent = "Exporting…";
  try {
    const r = await api(`/api/projects/${currentProjectId}/export`, { method: "POST" });
    el.textContent = `Exported to ${r.output}`;
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

document.getElementById("generate-btn").addEventListener("click", async () => {
  const el = document.getElementById("generate-result");
  const prompt = document.getElementById("generate-prompt").value.trim();
  if (!prompt || !currentProjectId) return;
  el.textContent = "Generating…";
  try {
    const r = await api(`/api/projects/${currentProjectId}/generate`, {
      method: "POST", body: JSON.stringify({ prompt }),
    });
    el.textContent = `${r.selections.length} clips added`;
    await loadTimeline();
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
    box.textContent = `${r.category} — ${r.description} Tags: ${r.tags.join(", ")}`;
    document.getElementById("source-info").textContent =
      `${selectedClip.file_stem} — ${selectedClip.duration_s || "?"}s — ${r.category}`;
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

window.addEventListener("resize", () => { if (currentProjectId) renderTimeline(); });

loadClips();
loadProjects();
