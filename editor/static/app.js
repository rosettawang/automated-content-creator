// ---------- state ----------
let currentCampaignId = null;
let currentEditId = null;
let clips = [];
let selectedClip = null;
let bulkSelection = new Set();   // clip ids checked for bulk "describe together"
let exiftoolAvailable = false;
let timeline = [];            // [{id, clip_id, file_stem, description, in_point, out_point, clip_duration_s, available_locally}]
let selectedItemId = null;
let pxPerSec = 40;

// program playback — double-buffered: two <video>s alternate so the NEXT segment is
// preloaded and sought while the current one plays, so cuts don't stall.
const _videoA = document.getElementById("program-video");
const _videoB = document.getElementById("program-video-b");
let programVideo = _videoA;    // the active/visible element (crop.js reads this live)
let _bufferedSeg = -1;         // segment index currently preloaded (paused at its in-point) in the hidden buffer
let segments = [];            // derived from timeline: {item, start, end, dur, clipId, inP, outP}
let activeSeg = -1;
let playing = false;
let loadingSeg = false;
let rafId = null;

function inactiveVideo() { return programVideo === _videoA ? _videoB : _videoA; }

function setActiveVideo(el) {
  if (el === programVideo) return;
  programVideo.pause();
  programVideo = el;
  _videoA.classList.toggle("pv-active", programVideo === _videoA);
  _videoB.classList.toggle("pv-active", programVideo === _videoB);
  // The hidden buffer preloads muted (so it's silent); the active element must have
  // audio. Muting the outgoing one keeps the next preload silent too.
  programVideo.muted = false;
  inactiveVideo().muted = true;
  // With reference-audio preview on, the video plays silently under the scratch track.
  if (typeof refApplyMute === "function") refApplyMute();
  // crop.js positions its overlay against the live programVideo — refresh on swap.
  if (typeof refreshCropOverlay === "function") refreshCropOverlay();
}

const segSrc = (seg) => `/api/clips/${seg.clipId}/media`;

// Load `seg` into video element `el` and seek to (inP + within); cb(err|null) when
// ready (paused). Used both to make a segment live and to preload the next one.
function prepareOn(el, seg, within, cb) {
  const want = segSrc(seg);
  const seekTo = seg.inP + Math.max(0, within || 0);
  const ready = () => {
    el.removeEventListener("error", onErr);
    try { el.currentTime = seekTo; } catch (_) { /* not seekable yet */ }
    cb && cb(null);
  };
  const onErr = () => {
    el.removeEventListener("loadeddata", ready);
    cb && cb(new Error("load-failed"));
  };
  if (!el.src.endsWith(want)) {
    el.src = want;
    el.addEventListener("loadeddata", ready, { once: true });
    el.addEventListener("error", onErr, { once: true });
  } else if (el.readyState >= 1) {
    ready();
  } else {
    el.addEventListener("loadeddata", ready, { once: true });
    el.addEventListener("error", onErr, { once: true });
  }
}

// Preload the segment AFTER i into the hidden buffer (skips non-local / end-of-timeline).
function preloadNext(i) {
  _bufferedSeg = -1;
  const n = i + 1;
  if (n >= segments.length) return;
  const seg = segments[n];
  if (seg.item && seg.item.available_locally === false) return;
  prepareOn(inactiveVideo(), seg, 0, (err) => { if (!err) _bufferedSeg = n; });
}

// Promote the hidden buffer (already loaded + sought to its in-point) to active.
function goLiveWithBuffer(thenPlay) {
  setActiveVideo(inactiveVideo());
  loadingSeg = false;
  showProgramMessage("");
  if (thenPlay) programVideo.play();
  preloadNext(activeSeg);
}

// Persistent media-error / stall surfacing. prepareOn's listeners are one-shot and
// only cover the initial load; without these, an error AFTER a clip has loaded (a
// mid-play decode failure, a network drop, a swapped-in buffer that went bad) — or a
// buffer underrun — would never surface. These stay attached for the elements' life.
function _mediaErrorText(el) {
  const seg = segments[activeSeg];
  const name = seg && seg.item ? seg.item.file_stem : "clip";
  const code = el.error && el.error.code;
  const why = code === 3 ? "the file couldn't be decoded (unsupported codec or container)"
            : code === 2 ? "a network error interrupted it"
            : code === 4 ? "its media is missing or unsupported"
            : "a media error occurred";
  return `Playback error on "${name}" — ${why}. Try Verify media, or swap the clip.`;
}
[_videoA, _videoB].forEach((el) => {
  el.addEventListener("error", () => {
    // Initial-load errors are handled by prepareOn's cb (loadingSeg is true then);
    // this catches errors on the ACTIVE element after it had loaded fine.
    if (el !== programVideo || loadingSeg) return;
    pause();
    showProgramMessage(_mediaErrorText(el));
  });
  // A buffer underrun mid-playback: tell the user we're waiting, non-fatally.
  const onStall = () => {
    if (el === programVideo && playing && !loadingSeg) showProgramMessage("Buffering…");
  };
  el.addEventListener("stalled", onStall);
  el.addEventListener("waiting", onStall);
  // Recovered — clear the transient buffering note.
  el.addEventListener("playing", () => { if (el === programVideo) showProgramMessage(""); });
});

// api() is provided globally by common.js (prepended to every bundle).

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
  // Every panel lives in one document (/studio), so a same-window message reaches
  // the sibling panels' clip-updated listeners. Harmless self-refresh on standalone.
  try {
    window.postMessage({ studio: "clip-updated", clipId: Number(id) }, "*");
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

// esc() is provided globally by common.js.

function renderClipList() {
  const list = document.getElementById("clip-list");
  list.innerHTML = "";
  clips.forEach((clip) => {
    const el = document.createElement("div");
    el.className = "clip-item" +
      (clip.available_locally ? "" : " unavailable") +
      (selectedClip && selectedClip.id === clip.id ? " selected" : "") +
      (bulkSelection.has(clip.id) ? " bulk-selected" : "");
    // Unified non-local tooltip copy (matches the timeline badge): name the fix.
    if (!clip.available_locally) {
      el.title = clip.source_kind
        ? `${clip.file_stem} isn't downloaded — drop it on the timeline and use Re-download to fetch it from its source (${clip.source_kind}).`
        : `${clip.file_stem} isn't downloaded and has no recorded source — import its file to use it.`;
    }
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
  // Open a specific edit in-place (Campaigns "Cuts" list → Editor), without
  // navigating away from the unified /studio document.
  openEdit: async (editId) => {
    try {
      const edit = await api(`/api/edits/${editId}`);
      currentCampaignId = edit.campaign_id != null ? String(edit.campaign_id) : "";
      currentEditId = String(editId);
      await loadCampaigns();   // syncs campaign selector, then loadEdits() keeps currentEditId → loads it
    } catch (_) { /* ignore */ }
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

// Human-readable reason for an HTMLMediaElement error code.
function mediaErrorText(v) {
  const code = v.error && v.error.code;
  if (code === 1) return "load aborted";
  if (code === 2) return "network error reading the file";
  if (code === 3) return "can't decode this clip";
  if (code === 4) return "unsupported codec / format";
  return "can't be played in-app";
}

function selectClip(clip) {
  selectedClip = clip;
  renderClipList();
  const v = document.getElementById("source-video");
  const infoEl = document.getElementById("source-info");
  const baseInfo = `${clip.file_stem} — ${clip.duration_s || "?"}s — ${clip.category || ""}`;

  // Surface genuine media failures instead of a silent black frame. Listeners are
  // set BEFORE src so an immediate error is caught; onerror/onstalled (not
  // addEventListener) so they replace rather than stack across clip switches.
  let stallTimer = null;
  const clearStall = () => { if (stallTimer) { clearTimeout(stallTimer); stallTimer = null; } };
  v.onerror = () => {
    clearStall();
    infoEl.textContent = `${baseInfo} — ⚠ ${mediaErrorText(v)} (still usable in exports)`;
    infoEl.classList.add("load-error");
    if (window.showToast) {
      showToast(`${clip.file_stem}: ${mediaErrorText(v)}. A web-safe copy is being prepared — try again shortly.`,
                { type: "error" });
    }
  };
  v.onloadeddata = () => { clearStall(); infoEl.classList.remove("load-error"); };
  v.onstalled = v.onwaiting = () => {
    // Only warn if it's still not ready a few seconds on — buffering briefly is normal.
    clearStall();
    stallTimer = setTimeout(() => {
      if (v.readyState < 2 && !v.error) infoEl.textContent = `${baseInfo} — ⏳ still loading…`;
    }, 4000);
  };

  infoEl.classList.remove("load-error");
  infoEl.textContent = baseInfo;
  v.src = `/api/clips/${clip.id}/media`;
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

// ---------- campaigns (themes) / edits (timelines) ----------
// currentCampaignId = the theme; currentEditId = the timeline being edited.
async function loadCampaigns() {
  const campaigns = await api("/api/campaigns");
  const select = document.getElementById("campaign-select");
  select.innerHTML = "";
  // A pseudo-option for edits not filed under any campaign.
  const unassigned = document.createElement("option");
  unassigned.value = "";
  unassigned.textContent = "(Unassigned)";
  select.appendChild(unassigned);
  campaigns.forEach((p) => {
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
  const ids = campaigns.map((p) => String(p.id));
  if (currentCampaignId === null) {
    currentCampaignId = campaigns.length ? String(campaigns[0].id) : "";
  } else if (currentCampaignId && !ids.includes(String(currentCampaignId))) {
    currentCampaignId = "";
  }
  select.value = currentCampaignId == null ? "" : String(currentCampaignId);
  await loadEdits();
}

async function loadEdits() {
  const q = currentCampaignId ? `?campaign=${currentCampaignId}` : "";
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

// Friendly labels for the Frame gear + inferred-framing toasts.
const ASPECT_LABELS = {
  "9:16": "9:16 vertical", "4:5": "4:5 portrait", "1:1": "1:1 square",
  "16:9": "16:9 landscape", "source": "Source (no reframe)",
};

async function loadTimeline() {
  if (!currentEditId) return;
  const edit = await api(`/api/edits/${currentEditId}`);
  timeline = edit.items;
  const aspectSel = document.getElementById("aspect-select");
  if (aspectSel) aspectSel.value = edit.aspect || "source";
  const audioSel = document.getElementById("audio-select");
  if (audioSel) audioSel.value = edit.audio_mode || "ambient";
  const rat = document.getElementById("audio-rationale");
  if (rat) rat.textContent = edit.audio_rationale || "";
  if (typeof renderReferenceAudio === "function") renderReferenceAudio(edit);
  const voScript = document.getElementById("vo-script");
  if (voScript) voScript.value = edit.vo_script || "";
  syncVoScriptVisibility();
  if (!timeline.some((i) => i.id === selectedItemId)) selectedItemId = null;
  rebuildSegments();
  renderTimeline();
  updateIdlePoster();
}

// Before first play the program monitor is otherwise a black box — show the first
// (local) clip's frame as a poster so idle state reads as "ready", not "broken".
// A real frame replaces the poster once playback starts; the poster returns when a
// new edit is loaded and nothing has played yet.
function updateIdlePoster() {
  const first = segments.find((s) => s.item && s.item.available_locally !== false);
  const poster = first ? `/api/clips/${first.clipId}/thumbnail` : "";
  [_videoA, _videoB].forEach((v) => {
    if (poster) v.poster = poster;
    else v.removeAttribute("poster");
  });
}

// Persist the output frame/aspect on the current edit. With no edit yet, the choice
// is simply held in the control and applied to the next edit you Generate.
document.getElementById("aspect-select").addEventListener("change", async (e) => {
  if (!currentEditId) return;   // pending default for the next Generate; nothing to save
  await api(`/api/edits/${currentEditId}`, {
    method: "PUT",
    body: JSON.stringify({ aspect: e.target.value }),
  });
  if (typeof refreshCropOverlay === "function") refreshCropOverlay();
});

// Show the voiceover script field only when Audio = Voiceover.
function syncVoScriptVisibility() {
  const sel = document.getElementById("audio-select");
  const row = document.getElementById("vo-script-row");
  if (row && sel) row.classList.toggle("hidden", sel.value !== "voiceover");
}

// Persist the audio treatment on the current edit (mirrors the aspect control).
document.getElementById("audio-select").addEventListener("change", async (e) => {
  syncVoScriptVisibility();
  if (!currentEditId) return;   // pending default for the next Generate
  await api(`/api/edits/${currentEditId}`, {
    method: "PUT",
    body: JSON.stringify({ audio_mode: e.target.value }),
  });
  // A manual pick clears the model's rationale (it no longer explains the choice).
  const rat = document.getElementById("audio-rationale");
  if (rat) rat.textContent = "";
});

// Save the (editable) voiceover script — synthesized only at export, never here.
document.getElementById("vo-script-save").addEventListener("click", async () => {
  if (!currentEditId) return;
  const status = document.getElementById("vo-script-status");
  status.textContent = "Saving…";
  try {
    await api(`/api/edits/${currentEditId}`, {
      method: "PUT",
      body: JSON.stringify({ vo_script: document.getElementById("vo-script").value }),
    });
    status.textContent = "Saved. Synthesized on export.";
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  }
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
    if (open) {
      // Default anchoring is right:0 (opens leftward). If that pushes the popover
      // past the left edge — e.g. a narrow window or the gear near the left — flip
      // to left-anchored so it stays fully on-screen.
      pop.style.right = "";
      pop.style.left = "";
      const r = pop.getBoundingClientRect();
      if (r.left < 4) { pop.style.right = "auto"; pop.style.left = "0"; }
    }
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
})();

function rebuildSegments() {
  // The timeline changed, so any preloaded buffer is stale — invalidate it so we
  // never swap to a segment that no longer follows the current one.
  _bufferedSeg = -1;
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
    const notLocal = seg.item.available_locally === false;
    const canPull = notLocal && seg.item.can_redownload;
    el.className = "tl-clip" + (notLocal ? " not-local" : "") + (seg.item.id === selectedItemId ? " selected" : "");
    if (notLocal) el.title = canPull
      ? `${seg.item.file_stem} isn't downloaded. Click "Re-download" to fetch it from its source (${seg.item.source_kind}).`
      : `${seg.item.file_stem} isn't downloaded and has no recorded source — import its file, or ask the edit chat to swap it for a local clip.`;
    el.style.left = `${seg.start * pxPerSec}px`;
    el.style.width = `${Math.max(seg.dur * pxPerSec, 12)}px`;
    el.innerHTML = `
      <div class="tl-label">${seg.item.file_stem}${notLocal ? ' <span class="tl-warn">⚠ not local</span>' : ""}
        <span class="tl-sub">${seg.dur.toFixed(1)}s</span></div>
      ${canPull ? '<button class="tl-redl" type="button">⤓ Re-download</button>' : ""}
      <div class="tl-handle left"></div>
      <div class="tl-handle right"></div>`;
    attachClipInteractions(el, seg);
    if (canPull) {
      const btn = el.querySelector(".tl-redl");
      // Don't let the button's drag/select gestures bubble to the clip block.
      btn.addEventListener("mousedown", (e) => e.stopPropagation());
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        pullClip(seg.item.clip_id, seg.item.file_stem, btn);
      });
    }
    track.appendChild(el);
  });
  track.appendChild(playhead);
  updatePlayhead(currentGlobalTime());
  document.getElementById("time-readout").textContent = `${fmt(currentGlobalTime())} / ${fmt(total)}`;
  if (typeof refreshCropOverlay === "function") refreshCropOverlay();
}

// Re-download a not-local clip from its recorded source, then relink + refresh so
// it flips to available (playable/exportable). Reuses the import job + poll pattern.
async function pullClip(clipId, fileStem, btnEl) {
  if (!clipId) return;
  const label = btnEl ? btnEl.textContent : null;
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = "…"; }
  showProgramMessage(`Re-downloading ${fileStem}…`);
  try {
    const started = await api(`/api/clips/${clipId}/pull`, { method: "POST" });
    if (started.job_id) {
      for (;;) {
        const job = await api(`/api/import-jobs/${started.job_id}`);
        if (job.finished) { if (job.error) throw new Error(job.error); break; }
        const count = job.total ? ` (${job.done}/${job.total})` : "";
        showProgramMessage(`Fetching ${fileStem}${count}…${job.current ? " " + job.current : ""}`);
        await new Promise((r) => setTimeout(r, 700));
      }
    }
    showProgramMessage("");
    const searchEl = document.getElementById("search");
    await loadClips(searchEl ? searchEl.value : "");
    if (currentEditId) await loadTimeline();   // rebuilds the timeline (button included)
    broadcastClipUpdated(clipId);
  } catch (err) {
    showProgramMessage(`Couldn't re-download ${fileStem}: ${err.message}`);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = label; }
  }
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

function showProgramMessage(text) {
  const el = document.getElementById("program-msg");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("hidden", !text);
}

function loadSegment(i, withinSeconds, thenPlay) {
  if (i < 0 || i >= segments.length) return;
  const seg = segments[i];
  activeSeg = i;
  const within = Math.max(0, withinSeconds || 0);

  // If we already know the media isn't on disk, don't spin forever waiting for a
  // load that will 404 — say so and stop.
  if (seg.item && seg.item.available_locally === false) {
    loadingSeg = false;
    pause();
    showProgramMessage(`"${seg.item.file_stem}" isn't downloaded — import its file to preview or export this edit.`);
    return;
  }

  // Fast path: this exact segment is already preloaded & sought in the hidden buffer
  // (the common case at a clean cut) — promote it instantly, no load/seek stall.
  if (within < 0.05 && _bufferedSeg === i
      && inactiveVideo().src.endsWith(segSrc(seg))
      && inactiveVideo().readyState >= 2) {
    goLiveWithBuffer(thenPlay);
    return;
  }

  // Otherwise load onto the active element (a fresh start or a scrub/seek).
  loadingSeg = true;
  showProgramMessage("");
  prepareOn(programVideo, seg, within, (err) => {
    loadingSeg = false;
    if (err) {
      pause();
      const stem = seg.item ? seg.item.file_stem : "clip";
      showProgramMessage(`Couldn't load "${stem}" — its media may be missing or an unsupported format.`);
      if (window.showToast) showToast(`Couldn't load "${stem}" — media missing or unsupported format.`, { type: "error" });
      return;
    }
    if (thenPlay) programVideo.play();
    preloadNext(i);   // get the following segment ready in the hidden buffer
  });

  // Some failures (a container/MIME the browser won't demux) neither load nor error —
  // they stall silently at readyState 0. Watchdog: after 8s, stop and say why.
  setTimeout(() => {
    if (loadingSeg && activeSeg === i && programVideo.readyState === 0 && !programVideo.error) {
      loadingSeg = false;
      pause();
      const stem = seg.item ? seg.item.file_stem : "clip";
      showProgramMessage(`"${stem}" isn't loading — the browser may not support this file's format. A web-safe .mp4 is being prepared; try again shortly.`);
      if (window.showToast) showToast(`"${stem}" isn't loading — preparing a web-safe copy, try again shortly.`, { type: "warn" });
    }
  }, 8000);
}

function seekGlobal(t, thenPlay) {
  const total = totalDuration();
  t = Math.max(0, Math.min(total, t));
  const i = segAtTime(t);
  if (i < 0) return;
  loadSegment(i, t - segments[i].start, thenPlay);
  updatePlayhead(t);
  document.getElementById("time-readout").textContent = `${fmt(t)} / ${fmt(total)}`;
  if (typeof refOnSeek === "function") refOnSeek(t);
}

// ---------- transport ----------
function play() {
  if (!segments.length) return;
  if (currentGlobalTime() >= totalDuration() - 0.05) seekGlobal(0, false);
  playing = true;
  document.getElementById("play-pause").textContent = "❚❚";
  if (activeSeg < 0) loadSegment(0, 0, true);
  else programVideo.play();
  if (typeof refOnPlay === "function") refOnPlay();
  tick();
}

function pause() {
  playing = false;
  document.getElementById("play-pause").textContent = "▶";
  programVideo.pause();
  if (typeof refOnPause === "function") refOnPause();
  if (rafId) cancelAnimationFrame(rafId);
}

function togglePlay() { playing ? pause() : play(); }

function tick() {
  if (!playing) return;
  if (!loadingSeg && activeSeg >= 0) {
    const seg = segments[activeSeg];
    if (programVideo.currentTime >= seg.outP - 0.02) {
      if (activeSeg + 1 < segments.length) {
        const nextI = activeSeg + 1;
        // Swap to the preloaded buffer if it's ready (seamless); else fall back to a load.
        if (_bufferedSeg === nextI && inactiveVideo().src.endsWith(segSrc(segments[nextI]))
            && inactiveVideo().readyState >= 2) {
          activeSeg = nextI;
          goLiveWithBuffer(true);
        } else {
          loadSegment(nextI, 0, true);
        }
      } else {
        pause();
        updatePlayhead(totalDuration());
        return;
      }
    } else {
      const t = currentGlobalTime();
      updatePlayhead(t);
      document.getElementById("time-readout").textContent = `${fmt(t)} / ${fmt(totalDuration())}`;
      if (typeof refDriftCorrect === "function") refDriftCorrect(t);
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
  const campaign = await api("/api/campaigns", { method: "POST", body: JSON.stringify({ name }) });
  currentCampaignId = String(campaign.id);
  currentEditId = null;
  await loadCampaigns();
  return true;
}

document.getElementById("campaign-select").addEventListener("change", async (e) => {
  if (e.target.value === "__new__") {
    // createCampaign() reloads on success; on cancel, reload to restore the dropdown
    // to its real selection (so it doesn't sit on "+ New campaign…").
    if (!(await createCampaign())) await loadCampaigns();
    return;
  }
  currentCampaignId = e.target.value; // "" = Unassigned
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
  // Pre-flight: every timeline clip must be on disk, or ffmpeg has nothing to cut.
  const missing = [...new Set(timeline.filter((i) => i.available_locally === false).map((i) => i.file_stem))];
  if (missing.length) {
    el.textContent = `Can't export — not downloaded: ${missing.join(", ")}. Import these files or swap them for local clips.`;
    return;
  }
  const btn = document.getElementById("export-btn");
  const cancelBtn = document.getElementById("export-cancel");
  btn.disabled = true;
  el.textContent = "Exporting…";
  try {
    // Export runs as a background job (long timelines would time out a request).
    // Kick it off, then poll progress until done; expose a Cancel that kills ffmpeg.
    const started = await api(`/api/edits/${currentEditId}/export`, { method: "POST" });
    cancelBtn.classList.remove("hidden");
    cancelBtn.onclick = async () => {
      cancelBtn.disabled = true;
      el.textContent = "Cancelling…";
      try { await api(`/api/jobs/${started.job_id}/cancel`, { method: "POST" }); }
      catch (e) { /* the poll loop will report the final state */ }
    };
    for (;;) {
      const job = await api(`/api/import-jobs/${started.job_id}`);
      if (job.finished) {
        if (job.phase === "cancelled" || job.cancelled) { el.textContent = "Export cancelled."; break; }
        if (job.error) throw new Error(job.error);
        const out = (job.results && job.results[0]) || {};
        el.textContent = out.output ? `Exported to ${out.output}` : "Exported.";
        break;
      }
      const phase = job.phase === "stitching" ? "Joining clips" : "Encoding";
      const count = job.total ? ` (${job.done}/${job.total})` : "";
      el.textContent = `${phase}${count}…${job.current ? " " + job.current : ""}`;
      await new Promise((r) => setTimeout(r, 700));
    }
  } catch (err) {
    el.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    cancelBtn.classList.add("hidden");
    cancelBtn.disabled = false;
    cancelBtn.onclick = null;
  }
}
document.getElementById("export-btn").addEventListener("click", doExport);

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
    if (currentCampaignId) body.campaign_id = parseInt(currentCampaignId, 10);
    // Apply the editor's current Frame setting to the new edit, so "generate vertical"
    // actually reframes to 9:16 rather than defaulting to source.
    const aspectSel = document.getElementById("aspect-select");
    if (aspectSel) body.aspect = aspectSel.value;
    const r = await api("/api/generate-edit", { method: "POST", body: JSON.stringify(body) });
    currentEditId = String(r.id);
    await loadEdits();   // resyncs the Frame gear from the new edit's aspect
    document.getElementById("generate-prompt").value = "";
    el.textContent = `New edit · ${r.selections.length} clips`;
    // If the model inferred the frame from the wording, say so (the gear already reflects it).
    if (r.aspect_inferred && r.aspect && window.showToast) {
      showToast(`Framing: ${ASPECT_LABELS[r.aspect] || r.aspect} — change in ⚙`, { type: "info" });
    }
    // Generated with no campaign chosen? Offer the best keyword match — never auto-assign.
    if (!body.campaign_id) suggestCampaignForEdit(r.id, prompt);
  } catch (err) { el.textContent = `Error: ${err.message}`; }
});

// Suggest a campaign for a freshly generated cut when none was selected: a simple
// client-side keyword match of the prompt against each campaign's name/description.
// Purely a suggestion — it shows a dismissible banner and never assigns silently.
const _SUGGEST_STOP = new Set(
  "the a an and or of to in on for with your you it is at this that make making made short reel reels video clip clips cut edit about into from show showing footage".split(" ")
);
async function suggestCampaignForEdit(editId, prompt) {
  let campaigns = [];
  try { campaigns = await api("/api/campaigns"); } catch { return; }
  if (!campaigns.length) return;
  const words = new Set(
    (prompt.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 2 && !_SUGGEST_STOP.has(w))
  );
  if (!words.size) return;
  let best = null, bestScore = 0;
  for (const c of campaigns) {
    const hay = `${c.name || ""} ${c.description || ""}`.toLowerCase();
    let score = 0;
    for (const w of words) if (hay.includes(w)) score++;
    if (score > bestScore) { best = c; bestScore = score; }
  }
  if (best && bestScore >= 1) showCampaignBanner(editId, best);
}

function showCampaignBanner(editId, campaign) {
  const old = document.getElementById("campaign-suggest-banner");
  if (old) old.remove();
  const bar = document.createElement("div");
  bar.id = "campaign-suggest-banner";
  Object.assign(bar.style, {
    position: "fixed", top: "0.6rem", left: "50%", transform: "translateX(-50%)",
    zIndex: "10000", background: "#232f23", border: "1px solid #4a6a4a",
    color: "#e6f0e6", borderRadius: "8px", padding: "0.5rem 0.7rem",
    fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.6rem",
    boxShadow: "0 6px 20px rgba(0,0,0,0.45)", fontFamily: "-apple-system, sans-serif",
  });
  const text = document.createElement("span");
  text.textContent = `Assign this cut to “${campaign.name}”?`;
  const yes = document.createElement("button");
  yes.textContent = "Yes";
  const no = document.createElement("button");
  no.textContent = "No";
  for (const b of [yes, no]) {
    Object.assign(b.style, {
      fontSize: "0.8rem", padding: "0.25rem 0.7rem", borderRadius: "5px",
      cursor: "pointer", border: "1px solid #4a6a4a", background: "#2c2c2c", color: "#eee",
    });
  }
  Object.assign(yes.style, { background: "#3a5c3a", color: "#fff" });
  const close = () => bar.remove();
  no.onclick = close;
  yes.onclick = async () => {
    try {
      await api(`/api/edits/${editId}`, { method: "PUT", body: JSON.stringify({ campaign_id: campaign.id }) });
      currentCampaignId = String(campaign.id);
      if (typeof loadEdits === "function") await loadEdits();
      if (window.showToast) showToast(`Assigned to “${campaign.name}”.`, { type: "success" });
    } catch (err) {
      if (window.showToast) showToast(`Couldn't assign: ${err.message}`, { type: "error" });
    }
    close();
  };
  bar.append(text, yes, no);
  document.body.appendChild(bar);
  // No auto-dismiss: it's an actionable Yes/No prompt, so it waits for the user
  // (No is the dismiss). Only a later suggestion replaces it (handled at the top).
}

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
//   /?campaign=<id> open into a campaign (its first edit)
const params = new URLSearchParams(location.search);
const requestedEdit = params.get("edit");
// `project` kept as a backward-compat alias for the renamed `campaign` param.
const requestedCampaign = params.get("campaign") || params.get("project");

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
  currentCampaignId = fresh.campaign_id != null ? String(fresh.campaign_id) : "";
  currentEditId = String(fresh.id);
  activeSeg = -1;
  await loadCampaigns(); // rebuilds campaign selector + loads the new edit's timeline
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
    // Resolve which campaign the deep-linked edit lives in so the selectors line up.
    try {
      const edit = await api(`/api/edits/${requestedEdit}`);
      currentEditId = String(edit.id);
      currentCampaignId = edit.campaign_id != null ? String(edit.campaign_id) : "";
    } catch { /* fall through to defaults */ }
  } else if (requestedCampaign) {
    currentCampaignId = requestedCampaign;
  }
  await loadCampaigns();
  startLiveRefresh();
}

// ================= Trending-audio compose: reference track + beat preview =========
// A local scratch track that times cuts to the beat and can be previewed under the
// (muted) program video. Never exported — see audio-design Phase 4.
const _refAudio = document.getElementById("ref-audio");
let refPreviewOn = false;
let refStart = 0;
let refHasRef = false;

function _bpmFromBeats(beatsJson) {
  try {
    const b = JSON.parse(beatsJson || "[]");
    if (b.length < 2) return null;
    const d = []; for (let i = 1; i < b.length; i++) d.push(b[i] - b[i - 1]);
    d.sort((x, y) => x - y);
    const med = d[Math.floor(d.length / 2)];
    return med > 0 ? Math.round(60 / med) : null;
  } catch (_) { return null; }
}

function renderReferenceAudio(edit) {
  const block = document.getElementById("ref-audio-block");
  if (!block) return;
  refHasRef = !!edit.ref_audio_path;
  refStart = Number(edit.ref_audio_start || 0);
  document.getElementById("ref-audio-empty").classList.toggle("hidden", refHasRef);
  document.getElementById("ref-audio-set").classList.toggle("hidden", !refHasRef);
  if (!refHasRef) {
    refPreviewOn = false;
    _refAudio.pause(); _refAudio.removeAttribute("src");
    return;
  }
  document.getElementById("ref-audio-name").textContent = edit.ref_audio_name || "reference track";
  document.getElementById("ref-audio-start").value = refStart;
  const bpm = _bpmFromBeats(edit.ref_audio_beats);
  document.getElementById("ref-audio-status").textContent =
    bpm ? `Beat grid: ~${bpm} BPM — cuts snap to it on Generate.` : "Detecting beats…";
  document.getElementById("ref-handoff").textContent =
    `Export clean, then add “${edit.ref_audio_name || "this sound"}” in the app at ${refStart}s ` +
    "— attaching trending audio can't be automated.";
  // Point the (paused) audio element at this edit's scratch track.
  _refAudio.src = `/api/edits/${currentEditId}/reference-audio/media`;
  const chk = document.getElementById("ref-preview-check");
  chk.checked = refPreviewOn;
}

// ---- upload / offset / clear ----
document.getElementById("ref-audio-add").addEventListener("click", () =>
  document.getElementById("ref-audio-file").click());

document.getElementById("ref-audio-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || !currentEditId) return;
  const status = document.getElementById("ref-audio-status");
  const form = new FormData();
  form.append("file", file);
  form.append("name", file.name.replace(/\.[^.]+$/, ""));
  await fetch(`/api/edits/${currentEditId}/reference-audio`, { method: "POST", body: form });
  await loadTimeline();
  // Beats detect async — poll a few times to update the BPM line.
  let tries = 0;
  const iv = setInterval(async () => {
    const ed = await api(`/api/edits/${currentEditId}`);
    const bpm = _bpmFromBeats(ed.ref_audio_beats);
    if (bpm || ++tries > 6) {
      clearInterval(iv);
      if (status) status.textContent = bpm
        ? `Beat grid: ~${bpm} BPM — cuts snap to it on Generate.`
        : "Couldn't detect a clear beat — cuts won't snap.";
    }
  }, 1000);
  e.target.value = "";
});

document.getElementById("ref-audio-start").addEventListener("change", async (e) => {
  if (!currentEditId) return;
  refStart = Math.max(0, Number(e.target.value) || 0);
  await api(`/api/edits/${currentEditId}`, { method: "PUT", body: JSON.stringify({ ref_audio_start: refStart }) });
  const ed = await api(`/api/edits/${currentEditId}`);
  document.getElementById("ref-handoff").textContent =
    `Export clean, then add “${ed.ref_audio_name || "this sound"}” in the app at ${refStart}s ` +
    "— attaching trending audio can't be automated.";
  if (refPreviewOn && playing) refOnSeek(currentGlobalTime());
});

document.getElementById("ref-audio-clear").addEventListener("click", async () => {
  if (!currentEditId) return;
  await fetch(`/api/edits/${currentEditId}/reference-audio`, { method: "DELETE" });
  refPreviewOn = false;
  await loadTimeline();
});

document.getElementById("ref-preview-check").addEventListener("change", (e) => {
  refPreviewOn = e.target.checked && refHasRef;
  refApplyMute();
  if (refPreviewOn && playing) refOnPlay();
  else refOnPause();
});

// ---- playback sync (called from play/pause/seek/tick/setActiveVideo) ----
function refApplyMute() {
  const on = refPreviewOn && refHasRef;
  _videoA.muted = on ? true : (_videoA === programVideo ? false : true);
  _videoB.muted = on ? true : (_videoB === programVideo ? false : true);
}
function refOnPlay() {
  if (!(refPreviewOn && refHasRef)) return;
  try { _refAudio.currentTime = refStart + currentGlobalTime(); } catch (_) {}
  _refAudio.play().catch(() => {});
}
function refOnPause() { _refAudio.pause(); }
function refOnSeek(t) {
  if (!(refPreviewOn && refHasRef)) return;
  try { _refAudio.currentTime = refStart + t; } catch (_) {}
}
function refDriftCorrect(t) {
  if (!(refPreviewOn && refHasRef) || _refAudio.paused) return;
  const want = refStart + t;
  if (Math.abs(_refAudio.currentTime - want) > 0.15) {
    try { _refAudio.currentTime = want; } catch (_) {}
  }
}

init();
