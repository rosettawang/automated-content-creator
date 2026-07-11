let allClips = [];
const selectedClipIds = new Set(); // clips hand-picked to scope generation
let currentProjectId = ""; // "" = All clips, else a project id (string)
let projectsById = {};     // id -> project record

// Exposed to the workspace shell for cross-panel drag. Top-level `let` bindings
// aren't window properties, so the shell can't read `allClips` directly — this hook
// hands it a clip by id when a card is dragged toward the Editor.
window.studioLibrary = { getClip: (id) => allClips.find((c) => c.id === Number(id)) || null };

// Tell the workspace shell a clip changed, so sibling panels refresh. No-op when
// this page is opened standalone (not inside the shell).
function broadcastClipUpdated(id) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ studio: "clip-updated", clipId: Number(id) }, "*");
    }
  } catch (_) { /* ignore */ }
}

// Another panel changed a clip → re-fetch so the grid reflects it.
window.addEventListener("message", (e) => {
  if (e.data && e.data.studio === "clip-updated") loadClips();
});

async function loadClips() {
  const url = currentProjectId ? `/api/clips?project=${currentProjectId}` : "/api/clips";
  const res = await fetch(url);
  allClips = await res.json();
  applyFilter();
}

const INDEX_BADGE = {
  indexed:  { icon: "✓", cls: "badge-indexed",  title: "Fully indexed" },
  indexing: { icon: "⟳", cls: "badge-indexing", title: "Indexing…" },
  pending:  { icon: "⏳", cls: "badge-pending",  title: "Not yet indexed (waiting for AI analysis)" },
};

const LABEL_MAX = 42;

// Short human-readable header for a card: the AI/short description, trimmed to a
// tidy length. Falls back to the filename until a description exists.
function shortLabel(clip) {
  const desc = (clip.description || "").trim();
  if (!desc) return clip.file_stem;
  const oneLine = desc.replace(/\s+/g, " ");
  return oneLine.length > LABEL_MAX
    ? oneLine.slice(0, LABEL_MAX - 1).trimEnd() + "…"
    : oneLine;
}

function render(clips) {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  document.getElementById("count").textContent =
    `${clips.length} clip${clips.length === 1 ? "" : "s"}`;

  clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "card" + (clip.available_locally ? "" : " unavailable")
      + (selectedClipIds.has(clip.id) ? " selected" : "");
    card.dataset.clipId = clip.id;
    card.dataset.indexStatus = clip.index_status;

    const selectBox = document.createElement("input");
    selectBox.type = "checkbox";
    selectBox.className = "select-box";
    selectBox.title = "Select for generation";
    selectBox.checked = selectedClipIds.has(clip.id);
    selectBox.onclick = (e) => e.stopPropagation(); // don't open info
    selectBox.onchange = () => {
      if (selectBox.checked) selectedClipIds.add(clip.id);
      else selectedClipIds.delete(clip.id);
      card.classList.toggle("selected", selectBox.checked);
      updateAssembleHint();
    };

    const img = document.createElement("img");
    img.className = "thumb";
    img.loading = "lazy";
    img.src = `/api/clips/${clip.id}/thumbnail`;
    img.onerror = () => {
      const ph = document.createElement("div");
      // Distinguish "file isn't on this machine" from "local file but no
      // thumbnail" — the former is expected (metadata-only index row), not a
      // stalled/broken load.
      if (clip.available_locally) {
        ph.className = "thumb-placeholder";
        ph.textContent = "▶";
      } else {
        ph.className = "thumb-placeholder not-downloaded";
        ph.innerHTML =
          '<span class="ph-icon">☁</span><span class="ph-text">Not downloaded</span>';
      }
      img.replaceWith(ph);
    };

    const b = INDEX_BADGE[clip.index_status] || INDEX_BADGE.pending;
    const badge = document.createElement("span");
    badge.className = `index-badge ${b.cls}`;
    badge.textContent = b.icon;
    badge.title = b.title;

    const infoBtn = document.createElement("button");
    infoBtn.className = "info-btn";
    infoBtn.textContent = "i";
    infoBtn.title = "Get info";
    infoBtn.onclick = (e) => {
      e.stopPropagation();
      showInfo(clip);
    };

    const meta = document.createElement("div");
    meta.className = "meta";
    const detail = clip.kind === "photo"
      ? "Photo"
      : (clip.duration_s ? `${clip.duration_s}s` : "?");
    const nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = shortLabel(clip);
    nameEl.title = clip.file_stem; // keep the real filename on hover
    const subEl = document.createElement("div");
    subEl.className = "sub";
    subEl.textContent =
      `${clip.category || "—"} · ${detail}${clip.available_locally ? "" : " · not local"}`;
    meta.appendChild(nameEl);
    meta.appendChild(subEl);

    card.appendChild(img);
    card.appendChild(badge);
    card.appendChild(selectBox);
    card.appendChild(infoBtn);
    card.appendChild(meta);
    card.onclick = () => showInfo(clip);
    grid.appendChild(card);
  });

  updateAssembleHint();
  schedulePollingIfNeeded(clips);
}

// Poll every 4s while any clip is indexing, then stop.
let _pollTimer = null;
function schedulePollingIfNeeded(clips) {
  const hasIndexing = clips.some((c) => c.index_status === "indexing");
  if (hasIndexing && !_pollTimer) {
    _pollTimer = setInterval(async () => {
      const res = await fetch("/api/clips");
      const fresh = await res.json();
      const stillIndexing = fresh.some((c) => c.index_status === "indexing");
      // Patch badges in-place to avoid a full re-render flicker.
      fresh.forEach((clip) => {
        const card = document.querySelector(`.card[data-clip-id="${clip.id}"]`);
        if (!card) return;
        if (card.dataset.indexStatus === clip.index_status) return;
        card.dataset.indexStatus = clip.index_status;
        const badge = card.querySelector(".index-badge");
        if (badge) {
          const b = INDEX_BADGE[clip.index_status] || INDEX_BADGE.pending;
          badge.className = `index-badge ${b.cls}`;
          badge.textContent = b.icon;
          badge.title = b.title;
        }
        // Refresh the header (now that a description may exist) and the sub-line.
        const nameEl = card.querySelector(".name");
        if (nameEl) {
          nameEl.textContent = shortLabel(clip);
          nameEl.title = clip.file_stem;
        }
        const sub = card.querySelector(".sub");
        if (sub) {
          const detail = clip.kind === "photo"
            ? "Photo"
            : (clip.duration_s ? `${clip.duration_s}s` : "?");
          sub.textContent =
            `${clip.category || "—"} · ${detail}${clip.available_locally ? "" : " · not local"}`;
        }
        // Update the in-memory copy so showInfo reflects new data.
        const idx = allClips.findIndex((c) => c.id === clip.id);
        if (idx !== -1) allClips[idx] = clip;
      });
      if (!stillIndexing) {
        clearInterval(_pollTimer);
        _pollTimer = null;
      }
    }, 4000);
  } else if (!hasIndexing && _pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

function field(dl, label, value) {
  if (!value) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

let infoClip = null; // the clip currently open in the info panel

// The curated things (watchlist matches) recorded for this clip — the "what I
// actually care about" view. This is the prominent chip row in the info panel.
function renderThings(clip) {
  const wrap = document.getElementById("things-in-clip");
  const chips = document.getElementById("things-chips");
  chips.innerHTML = "";
  const things = (clip.things || []);
  if (!things.length) {
    // Show a quiet empty state rather than hiding, so it's clear nothing tracked
    // was found here (vs. the feature not existing).
    const empty = document.createElement("span");
    empty.className = "things-empty";
    empty.textContent = "No tracked things found in this clip.";
    chips.appendChild(empty);
    wrap.classList.remove("hidden");
    return;
  }
  // Uniform chips — kind is an auto-inferred background hint, not something worth
  // surfacing or styling around here.
  things.forEach((t) => {
    const el = document.createElement("span");
    el.className = "chip chip-thing";
    el.textContent = t.name;
    chips.appendChild(el);
  });
  wrap.classList.remove("hidden");
}

// The full AI output — category + every tag — kept available but collapsed, since
// it's the "mess" the user doesn't want front-and-center.
function renderIdentified(clip) {
  const wrap = document.getElementById("identified");
  const chips = document.getElementById("identified-chips");
  const toggle = document.getElementById("identified-toggle");
  chips.innerHTML = "";
  const items = [];
  if ((clip.category || "").trim()) {
    items.push({ text: clip.category.trim(), cls: "chip chip-category" });
  }
  (clip.tags || "").split(",").map((t) => t.trim()).filter(Boolean)
    .forEach((t) => items.push({ text: t, cls: "chip" }));

  if (!items.length) {
    wrap.classList.add("hidden");
    return;
  }
  items.forEach(({ text, cls }) => {
    const el = document.createElement("span");
    el.className = cls;
    el.textContent = text;
    chips.appendChild(el);
  });
  // Always start collapsed when the panel opens.
  chips.classList.add("hidden");
  toggle.textContent = `Show all identified tags (${items.length}) ▸`;
  wrap.classList.remove("hidden");
}

document.getElementById("identified-toggle").addEventListener("click", () => {
  const chips = document.getElementById("identified-chips");
  const toggle = document.getElementById("identified-toggle");
  const n = chips.children.length;
  const collapsed = chips.classList.toggle("hidden");
  toggle.textContent = collapsed
    ? `Show all identified tags (${n}) ▸`
    : `Hide identified tags ▾`;
});

// Narration = the transcribed speech, shown in its own block when present.
function renderNarration(clip) {
  const wrap = document.getElementById("info-narration");
  const text = (clip.transcript || "").trim();
  if (!text) {
    wrap.classList.add("hidden");
    return;
  }
  document.getElementById("narration-text").textContent = text;
  wrap.classList.remove("hidden");
}

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Timestamped transcript: each Whisper segment is a clickable line that seeks the
// video. Hidden for photos or when there are no speech segments.
async function renderTranscript(clip) {
  const wrap = document.getElementById("info-transcript");
  const lines = document.getElementById("transcript-lines");
  lines.innerHTML = "";
  wrap.classList.add("hidden");
  if (clip.kind === "photo") return;
  let segs = [];
  try {
    const res = await fetch(`/api/clips/${clip.id}/events?kind=speech`);
    segs = await res.json();
  } catch { return; }
  if (!segs.length) return;

  const video = document.getElementById("info-video");
  segs.forEach((seg) => {
    const line = document.createElement("button");
    line.className = "transcript-line";
    line.innerHTML =
      `<span class="ts">${fmtClock(seg.t_start)}</span>` +
      `<span class="tx">${(seg.text || "").replace(/[<>&]/g, "")}</span>`;
    line.onclick = () => {
      if (video && video.src) {
        video.currentTime = seg.t_start;
        video.play?.();
      }
    };
    lines.appendChild(line);
  });
  wrap.classList.remove("hidden");
}

// Scene timeline (deep index): each segment is a clickable span that seeks the video.
async function renderScenes(clip) {
  const wrap = document.getElementById("info-scenes");
  const lines = document.getElementById("scene-lines");
  lines.innerHTML = "";
  wrap.classList.add("hidden");
  if (clip.kind === "photo") return;
  let evs = [];
  try {
    const res = await fetch(`/api/clips/${clip.id}/events?kind=scene`);
    evs = await res.json();
  } catch { return; }
  if (!evs.length) return;

  const video = document.getElementById("info-video");
  evs.forEach((e) => {
    const line = document.createElement("button");
    line.className = "transcript-line";
    const label = e.label ? ` — ${e.label}` : "";
    line.innerHTML =
      `<span class="ts">${fmtClock(e.t_start)}–${fmtClock(e.t_end)}</span>` +
      `<span class="tx">${((e.text || "") + label).replace(/[<>&]/g, "")}</span>`;
    line.onclick = () => {
      if (video && video.src) { video.currentTime = e.t_start; video.play?.(); }
    };
    lines.appendChild(line);
  });
  wrap.classList.remove("hidden");
}

// Action events (X-CLIP motion): each a clickable span that seeks the video.
async function renderActions(clip) {
  const wrap = document.getElementById("info-actions");
  const lines = document.getElementById("action-lines");
  lines.innerHTML = "";
  wrap.classList.add("hidden");
  if (clip.kind === "photo") return;
  let evs = [];
  try {
    const res = await fetch(`/api/clips/${clip.id}/events?kind=action`);
    evs = await res.json();
  } catch { return; }
  if (!evs.length) return;

  const video = document.getElementById("info-video");
  evs.forEach((e) => {
    const line = document.createElement("button");
    line.className = "transcript-line";
    line.innerHTML =
      `<span class="ts">${fmtClock(e.t_start)}–${fmtClock(e.t_end)}</span>` +
      `<span class="tx">${(e.label || "").replace(/[<>&]/g, "")}</span>`;
    line.onclick = () => {
      if (video && video.src) { video.currentTime = e.t_start; video.play?.(); }
    };
    lines.appendChild(line);
  });
  wrap.classList.remove("hidden");
}

function showInfo(clip) {
  document.getElementById("info-title").textContent = clip.file_stem;
  const thumb = document.getElementById("info-thumb");
  const video = document.getElementById("info-video");

  const isPhoto = clip.kind === "photo";
  if (isPhoto) {
    // Photos: show the still image (full-res when local, else the thumbnail).
    video.pause?.();
    video.removeAttribute("src");
    video.load();
    video.style.display = "none";
    thumb.src = clip.available_locally
      ? `/api/clips/${clip.id}/media`
      : `/api/clips/${clip.id}/thumbnail`;
    thumb.style.display = "";
    thumb.onerror = () => { thumb.style.display = "none"; };
  } else if (clip.available_locally) {
    // Video that's downloaded: play it.
    video.src = `/api/clips/${clip.id}/media`;
    video.poster = `/api/clips/${clip.id}/thumbnail`;
    video.style.display = "";
    thumb.style.display = "none";
    // If the codec can't be decoded by the webview, drop back to the thumbnail.
    video.onerror = () => {
      video.style.display = "none";
      thumb.src = `/api/clips/${clip.id}/thumbnail`;
      thumb.style.display = "";
    };
  } else {
    // Video not downloaded: show the keyframe.
    video.removeAttribute("src");
    video.load();
    video.style.display = "none";
    thumb.src = `/api/clips/${clip.id}/thumbnail`;
    thumb.style.display = "";
    thumb.onerror = () => { thumb.style.display = "none"; };
  }

  const dl = document.getElementById("info-fields");
  dl.innerHTML = "";
  field(dl, "Filename", clip.file_stem);
  field(dl, "Type", clip.kind === "photo" ? "Photo" : "Video");
  field(dl, "Available", clip.available_locally ? "Local" : "Not downloaded");
  field(dl, "Duration", clip.kind === "photo" ? null : (clip.duration_s ? `${clip.duration_s}s` : null));
  field(dl, "Status", clip.status);
  field(dl, "Location", clip.location);

  // The description is the one qualitative "what's going on" box, and the only
  // editable field. Category + tags are surfaced read-only as "identified" chips.
  infoClip = clip;
  document.getElementById("edit-description").value = clip.description || "";
  document.getElementById("edit-status").textContent = "";
  renderThings(clip);
  renderIdentified(clip);
  renderNarration(clip);
  renderScenes(clip);
  renderActions(clip);
  renderTranscript(clip);
  // Reset the raw-metadata section to collapsed each time the panel opens.
  document.getElementById("raw-metadata").classList.add("hidden");
  document.getElementById("raw-toggle").textContent = "View raw metadata ▸";

  document.getElementById("info-overlay").classList.remove("hidden");
}

function hideInfo() {
  // Stop playback and release the file so audio doesn't keep going after close.
  const video = document.getElementById("info-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  document.getElementById("info-overlay").classList.add("hidden");
}

document.getElementById("info-close").addEventListener("click", hideInfo);
document.getElementById("info-overlay").addEventListener("click", (e) => {
  if (e.target.id === "info-overlay") hideInfo();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("info-overlay").classList.contains("hidden")) {
    hideInfo();
  }
});

// ---- edit metadata (info panel) ----
document.getElementById("edit-save").addEventListener("click", async () => {
  if (!infoClip) return;
  const saveBtn = document.getElementById("edit-save");
  const statusEl = document.getElementById("edit-status");
  const stamp = document.getElementById("edit-stamp").checked;
  // Only the qualitative description is editable here; the endpoint leaves any
  // field we don't send (category, tags) untouched.
  const body = {
    description: document.getElementById("edit-description").value,
    stamp,
  };
  saveBtn.disabled = true;
  statusEl.textContent = "Saving…";
  statusEl.classList.remove("error");
  try {
    const res = await fetch(`/api/clips/${infoClip.id}/metadata`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const saved = await res.json();
    if (!res.ok) throw new Error(saved.error || res.statusText);

    // Reflect the saved values in memory so the grid + panel stay in sync.
    Object.assign(infoClip, {
      description: saved.description,
      category: saved.category,
      tags: saved.tags,
    });
    const idx = allClips.findIndex((c) => c.id === infoClip.id);
    if (idx !== -1) Object.assign(allClips[idx], infoClip);
    applyFilter();
    broadcastClipUpdated(infoClip.id);  // let other panels (Editor) refresh this clip

    let msg = "Saved";
    if (stamp) {
      const s = saved.stamped;
      msg += s == null ? " (no local file to embed into)"
           : s.ok ? " · embedded into file"
           : ` · embed failed: ${s.error}`;
    }
    statusEl.textContent = msg;
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
    statusEl.classList.add("error");
  } finally {
    saveBtn.disabled = false;
  }
});

// ---- raw metadata peek ----
document.getElementById("raw-toggle").addEventListener("click", async () => {
  if (!infoClip) return;
  const panel = document.getElementById("raw-metadata");
  const toggle = document.getElementById("raw-toggle");
  if (!panel.classList.contains("hidden")) {
    panel.classList.add("hidden");
    toggle.textContent = "View raw metadata ▸";
    return;
  }
  panel.classList.remove("hidden");
  toggle.textContent = "Hide raw metadata ▾";
  const dbEl = document.getElementById("raw-db");
  const embEl = document.getElementById("raw-embedded");
  dbEl.textContent = "Loading…";
  embEl.textContent = "";
  try {
    const res = await fetch(`/api/clips/${infoClip.id}/raw-metadata`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    dbEl.textContent = JSON.stringify(data.db_row, null, 2);
    if (data.embedded) {
      embEl.textContent = JSON.stringify(data.embedded, null, 2);
    } else if (!data.available_locally) {
      embEl.textContent = "(file not downloaded — nothing embedded yet)";
    } else {
      embEl.textContent = `(${data.embedded_error || "no embedded tags"})`;
    }
  } catch (err) {
    dbEl.textContent = `Error: ${err.message}`;
  }
});

function applyFilter() {
  // Semantic mode is async and server-ranked — handled separately.
  if (document.getElementById("semantic-mode").checked) {
    runSemanticSearch();
    return;
  }
  let q = document.getElementById("search").value.trim().toLowerCase();
  const includePhotos = document.getElementById("include-photos").checked;
  let clips = allClips;
  if (!includePhotos) clips = clips.filter((c) => c.kind !== "photo");

  // Quality intent typed in the search box: "high quality" / "hq" / "high-res"
  // filters to well-scoring clips (and implies a quality sort) without needing
  // the dropdown. The phrase is stripped before the normal text match.
  let qualityIntent = null;
  const HQ = /\b(high[\s-]?quality|hq|high[\s-]?res(olution)?|sharp(est)?|crisp)\b/;
  const LQ = /\b(low[\s-]?quality|lq|blurry|soft|low[\s-]?res(olution)?)\b/;
  if (HQ.test(q)) { qualityIntent = "high"; q = q.replace(HQ, "").trim(); }
  else if (LQ.test(q)) { qualityIntent = "low"; q = q.replace(LQ, "").trim(); }

  if (q) {
    clips = clips.filter((c) =>
      [c.file_stem, c.description, c.category, c.tags, c.transcript]
        .some((v) => (v || "").toLowerCase().includes(q))
    );
  }
  if (qualityIntent === "high") clips = clips.filter((c) => (c.quality ?? 0) >= 70);
  else if (qualityIntent === "low") clips = clips.filter((c) => c.quality != null && c.quality < 70);

  // Sorting: the dropdown, or an implicit quality sort from a typed intent.
  const sortBy = document.getElementById("sort-by").value;
  const effectiveSort =
    sortBy !== "name" ? sortBy
    : qualityIntent === "high" ? "quality_desc"
    : qualityIntent === "low" ? "quality_asc"
    : "name";
  clips = sortClips(clips, effectiveSort);

  render(clips);
}

// Sort a clip list. Clips with no measured quality (e.g. not-local, unindexed)
// always sink to the bottom so a quality sort surfaces real, scored footage.
function sortClips(clips, mode) {
  if (mode === "name") {
    return [...clips].sort((a, b) => a.file_stem.localeCompare(b.file_stem));
  }
  const dir = mode === "quality_asc" ? 1 : -1;
  return [...clips].sort((a, b) => {
    const qa = a.quality, qb = b.quality;
    if (qa == null && qb == null) return a.file_stem.localeCompare(b.file_stem);
    if (qa == null) return 1;   // unmeasured → bottom
    if (qb == null) return -1;
    return (qa - qb) * dir;
  });
}

// ---- semantic search (on-device embeddings, server-ranked) ----
let _semanticTimer = null;

function runSemanticSearch() {
  const q = document.getElementById("search").value.trim();
  // Empty query in semantic mode: fall back to the normal (unfiltered) view.
  if (!q) {
    const includePhotos = document.getElementById("include-photos").checked;
    render(includePhotos ? allClips : allClips.filter((c) => c.kind !== "photo"));
    return;
  }
  clearTimeout(_semanticTimer);
  _semanticTimer = setTimeout(async () => {
    try {
      const res = await fetch("/api/search-semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, project: currentProjectId || "", top_k: 60 }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || res.statusText);
      if (body.unindexed) {
        document.getElementById("count").textContent =
          "no embeddings yet — click Build search index";
        showBuildIndexHint();
        return;
      }
      let clips = body.results || [];
      if (!document.getElementById("include-photos").checked) {
        clips = clips.filter((c) => c.kind !== "photo");
      }
      render(clips);
    } catch (err) {
      document.getElementById("count").textContent = `search error: ${err.message}`;
    }
  }, 250);
}

// Offer a one-click build if no embeddings exist yet.
function showBuildIndexHint() {
  if (document.getElementById("build-index-btn")) return;
  const btn = document.createElement("button");
  btn.id = "build-index-btn";
  btn.textContent = "Build search index";
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = "Building…";
    try {
      const results = await runServerJob("/api/embeddings/build", {});
      const n = (results && results[0] && results[0].embedded) || 0;
      showStatus(`Search index built (${n} clips embedded).`, false);
      btn.remove();
      runSemanticSearch();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Build search index";
      showStatus(`Index build failed: ${err.message}`, true);
    }
  };
  document.getElementById("library-header").appendChild(btn);
}

document.getElementById("search").addEventListener("input", applyFilter);
document.getElementById("sort-by").addEventListener("change", applyFilter);
document.getElementById("semantic-mode").addEventListener("change", () => {
  const on = document.getElementById("semantic-mode").checked;
  document.getElementById("search").placeholder = on
    ? "describe what you're looking for — ranked by meaning"
    : "search description / category / tags / filename";
  applyFilter();
});

// "Include photos" toggle — persist the choice across sessions.
const includePhotosBox = document.getElementById("include-photos");
includePhotosBox.checked = localStorage.getItem("includePhotos") !== "false";
includePhotosBox.addEventListener("change", () => {
  localStorage.setItem("includePhotos", includePhotosBox.checked);
  applyFilter();
});

// ---- view-settings popup (gear) ----
const settingsOverlay = document.getElementById("settings-overlay");
document.getElementById("settings-btn").addEventListener("click", () => {
  settingsOverlay.classList.remove("hidden");
});
// In the workspace shell the pane header owns the gear (left of the ×); hide the
// in-header one and let the shell drive this overlay via postMessage.
if (window.self !== window.top) {
  document.getElementById("settings-btn").style.display = "none";
}
window.addEventListener("message", (e) => {
  if (e.data && e.data.studio === "toggle-settings") {
    settingsOverlay.classList.toggle("hidden");
  }
});
document.getElementById("settings-close").addEventListener("click", () => {
  settingsOverlay.classList.add("hidden");
});
settingsOverlay.addEventListener("click", (e) => {
  if (e.target.id === "settings-overlay") settingsOverlay.classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsOverlay.classList.contains("hidden")) {
    settingsOverlay.classList.add("hidden");
  }
});

// ---- import status helpers ----
function summarize(results) {
  const added = results.filter((r) => r.status === "added_new_clip").length;
  const matched = results.filter((r) => r.status === "matched_existing").length;
  const duplicates = results.filter((r) => r.status === "duplicate").length;
  const errors = results.filter((r) => r.status === "error");
  // Originals deleted after a successful move (set only by the local-path importer).
  const moved = results.filter((r) => r.moved && r.status !== "error").length;
  const parts = [];
  if (added) parts.push(`${added} added`);
  if (matched) parts.push(`${matched} matched existing`);
  if (duplicates) parts.push(`${duplicates} skipped (already in library)`);
  if (errors.length) parts.push(`${errors.length} failed`);
  if (moved) parts.push(`${moved} original${moved === 1 ? "" : "s"} deleted`);
  let msg = parts.join(", ") || "nothing imported";
  if (errors.length) {
    msg += " — " + errors.map((e) => `${e.filename || e.url}: ${e.error}`).join("; ");
  }
  return msg;
}

function showStatus(text, isError) {
  const el = document.getElementById("import-status");
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("error", !!isError);
}

async function refreshAfterImport() {
  await loadClips();
  applyFilter();
}

// ---- unified import dialog ----
const importOverlay = document.getElementById("import-overlay");
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const importLinks = document.getElementById("import-links");
const importResult = document.getElementById("import-result");
const fileChosen = document.getElementById("file-chosen");
const importSubmit = document.getElementById("import-submit");

let selectedFiles = []; // File[] staged for upload

function openImport() {
  importOverlay.classList.remove("hidden");
}

function closeImport() {
  importOverlay.classList.add("hidden");
  // Reset dialog state so it opens fresh next time.
  selectedFiles = [];
  fileInput.value = "";
  importLinks.value = "";
  importResult.textContent = "";
  document.getElementById("import-progress").classList.add("hidden");
  fileChosen.classList.add("hidden");
  fileChosen.textContent = "";
  dropZone.classList.remove("dragover");
  importSubmit.disabled = false;
  document.getElementById("add-from-disk").disabled = false;
}

function stageFiles(fileList) {
  selectedFiles = Array.from(fileList || []);
  if (selectedFiles.length) {
    fileChosen.textContent =
      `${selectedFiles.length} file${selectedFiles.length === 1 ? "" : "s"} ready: ` +
      selectedFiles.map((f) => f.name).join(", ");
    fileChosen.classList.remove("hidden");
  } else {
    fileChosen.classList.add("hidden");
  }
}

document.getElementById("import-btn").addEventListener("click", openImport);
document.getElementById("import-close").addEventListener("click", closeImport);
importOverlay.addEventListener("click", (e) => {
  if (e.target.id === "import-overlay") closeImport();
});

// browse / drag-drop
document.getElementById("browse-btn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => stageFiles(e.target.files));

["dragenter", "dragover"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    if (ev === "dragleave" && dropZone.contains(e.relatedTarget)) return;
    dropZone.classList.remove("dragover");
  })
);
dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer?.files?.length) stageFiles(e.dataTransfer.files);
});

// ---- progress helpers ----
const importProgress = document.getElementById("import-progress");
const importBar = document.getElementById("import-bar");
const importBarFill = document.getElementById("import-bar-fill");
const importProgressLabel = document.getElementById("import-progress-label");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fmtTime(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.round(sec);
  if (sec < 60) return `~${sec}s left`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `~${m}m ${s}s left` : `~${m}m left`;
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function showProgressUI() {
  importProgress.classList.remove("hidden");
  importResult.textContent = "";
}

// determinate: pass a fraction 0..1; indeterminate: pass null
function setProgress(fraction, label) {
  if (fraction == null) {
    importBar.classList.add("indeterminate");
  } else {
    importBar.classList.remove("indeterminate");
    importBarFill.style.width = `${Math.round(fraction * 100)}%`;
  }
  importProgressLabel.textContent = label;
}

function renderJobProgress(job) {
  const phaseText = job.phase === "listing" ? "listing album…" : "starting…";
  if (!job.total) {
    setProgress(null, `${job.label}: ${phaseText}`);
    return;
  }
  const bits = [`${job.label} · ${job.done} of ${job.total} ${job.unit}${job.total === 1 ? "" : "s"}`];
  if (job.current) bits.push(job.current);
  const eta = fmtTime(job.eta_s);
  if (eta) bits.push(eta);
  setProgress(job.total ? job.done / job.total : null, bits.join(" · "));
}

// Kick off a server-side import job and poll it to completion; returns results[].
async function runServerJob(endpoint, body) {
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const started = await res.json();
  if (!res.ok) throw new Error(started.error || res.statusText);

  for (;;) {
    const r = await fetch(`/api/import-jobs/${started.job_id}`);
    const job = await r.json();
    if (!r.ok) throw new Error(job.error || r.statusText);
    renderJobProgress(job);
    if (job.finished) {
      if (job.error) throw new Error(job.error);
      return job.results;
    }
    await sleep(700);
  }
}

// Upload files with a live byte-level progress bar (the upload is the slow part
// client-side, so XHR upload events give real progress + ETA here).
function uploadFilesWithProgress(files) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    const total = files.reduce((n, f) => n + f.size, 0);
    const start = performance.now();

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/import-files");
    xhr.upload.onprogress = (e) => {
      const loaded = e.lengthComputable ? e.loaded : 0;
      const totalBytes = e.lengthComputable ? e.total : total;
      const frac = totalBytes ? loaded / totalBytes : null;
      const elapsed = (performance.now() - start) / 1000;
      const eta = loaded > 0 && frac != null ? (elapsed / loaded) * (totalBytes - loaded) : null;
      const bits = [`Uploading · ${fmtBytes(loaded)} of ${fmtBytes(totalBytes)}`];
      const etaStr = fmtTime(eta);
      if (etaStr) bits.push(etaStr);
      setProgress(frac, bits.join(" · "));
    };
    xhr.upload.onload = () => setProgress(1, "Upload complete · registering clips…");
    xhr.onload = () => {
      let body;
      try { body = JSON.parse(xhr.responseText); } catch { return reject(new Error("bad server response")); }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body.results);
      else reject(new Error(body.error || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error("upload failed"));
    xhr.send(form);
  });
}

// submit — handles files, Drive links, and Photos album links in one go
importSubmit.addEventListener("click", async () => {
  // One box for all links; route each to the right backend by its URL shape.
  const allLinks = importLinks.value.split("\n").map((s) => s.trim()).filter(Boolean);
  const isPhotos = (u) => /photos\.app\.goo\.gl|photos\.google\.com/i.test(u);
  const photoUrls = allLinks.filter(isPhotos);
  const urls = allLinks.filter((u) => !isPhotos(u));
  if (!selectedFiles.length && !allLinks.length) {
    importResult.textContent = "Add files or paste at least one Google Drive or Google Photos link first.";
    return;
  }

  importSubmit.disabled = true;
  showProgressUI();
  const parts = [];
  let anyError = false;

  try {
    if (selectedFiles.length) {
      const results = await uploadFilesWithProgress(selectedFiles);
      parts.push(summarize(results));
      anyError = anyError || results.some((r) => r.status === "error");
    }

    if (urls.length) {
      const results = await runServerJob("/api/drive-import", { urls });
      parts.push(summarize(results));
      anyError = anyError || results.some((r) => r.status === "error");
    }

    if (photoUrls.length) {
      const results = await runServerJob("/api/photos-import", { urls: photoUrls });
      parts.push(summarize(results));
      anyError = anyError || results.some((r) => r.status === "error");
    }

    importProgress.classList.add("hidden");
    importResult.textContent = parts.join(" · ");
    showStatus(parts.join(" · "), anyError);
    await refreshAfterImport();
    if (!anyError) closeImport();
  } catch (err) {
    importProgress.classList.add("hidden");
    importResult.textContent = `Error: ${err.message}`;
  } finally {
    importSubmit.disabled = false;
  }
});

// ---- native "add from disk" (desktop app only) ----
// window.pywebview.api is injected only by the desktop wrapper; in the browser it's
// undefined, so this whole feature stays hidden there.
const addFromDisk = document.getElementById("add-from-disk");
const movecopyOverlay = document.getElementById("movecopy-overlay");
const movecopyText = document.getElementById("movecopy-text");

function nativeApiReady() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_files);
}

// pywebview injects its API asynchronously, so re-check shortly after load.
function refreshNativeUI() {
  addFromDisk.classList.toggle("hidden", !nativeApiReady());
}
refreshNativeUI();
window.addEventListener("pywebviewready", refreshNativeUI);
setTimeout(refreshNativeUI, 500);

// Show the move/copy chooser and resolve to true (move) / false (copy) / null (cancel).
function askMoveOrCopy(count) {
  movecopyText.textContent =
    `${count} file${count === 1 ? "" : "s"} selected. Copy them into the library, or move them in?`;
  movecopyOverlay.classList.remove("hidden");
  return new Promise((resolve) => {
    const done = (val) => {
      movecopyOverlay.classList.add("hidden");
      copyBtn.removeEventListener("click", onCopy);
      moveBtn.removeEventListener("click", onMove);
      cancelBtn.removeEventListener("click", onCancel);
      resolve(val);
    };
    const copyBtn = document.getElementById("movecopy-copy");
    const moveBtn = document.getElementById("movecopy-move");
    const cancelBtn = document.getElementById("movecopy-cancel");
    const onCopy = () => done(false);
    const onMove = () => done(true);
    const onCancel = () => done(null);
    copyBtn.addEventListener("click", onCopy);
    moveBtn.addEventListener("click", onMove);
    cancelBtn.addEventListener("click", onCancel);
  });
}

addFromDisk.addEventListener("click", async () => {
  let paths;
  try {
    paths = await window.pywebview.api.pick_files();
  } catch (err) {
    importResult.textContent = `Couldn't open the file picker: ${err.message || err}`;
    return;
  }
  if (!paths || !paths.length) return; // cancelled

  const deleteOriginals = await askMoveOrCopy(paths.length);
  if (deleteOriginals === null) return; // cancelled the chooser

  importSubmit.disabled = true;
  addFromDisk.disabled = true;
  showProgressUI();
  setProgress(null, deleteOriginals ? "Moving files in…" : "Copying files in…");
  try {
    const res = await fetch("/api/import-local-paths", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, delete_originals: deleteOriginals }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    const anyError = body.results.some((r) => r.status === "error");
    importProgress.classList.add("hidden");
    const msg = summarize(body.results);
    importResult.textContent = msg;
    showStatus(msg, anyError);
    await refreshAfterImport();
    if (!anyError) closeImport();
  } catch (err) {
    importProgress.classList.add("hidden");
    importResult.textContent = `Error: ${err.message}`;
  } finally {
    importSubmit.disabled = false;
    addFromDisk.disabled = false;
  }
});

// Escape closes the import dialog too (info panel handles its own Escape above)
document.addEventListener("keydown", (e) => {
  if (!movecopyOverlay.classList.contains("hidden")) return; // let the chooser own Escape
  if (e.key === "Escape" && !importOverlay.classList.contains("hidden")) closeImport();
});

// ---- assemble: prompt -> new project rough cut -> jump to editor ----
const assemblePrompt = document.getElementById("assemble-prompt");
const assembleBtn = document.getElementById("assemble-btn");
const assembleStatus = document.getElementById("assemble-status");
const assembleClear = document.getElementById("assemble-clear");

// Reflect the current selection in the assemble bar (idle state only, so we
// don't clobber an in-progress / error message).
function updateAssembleHint() {
  const n = selectedClipIds.size;
  assembleClear.classList.toggle("hidden", n === 0);
  if (assembleStatus.classList.contains("error")) return;
  assembleStatus.textContent = n
    ? `${n} clip${n === 1 ? "" : "s"} selected — will generate from these`
    : "Using all clips (select some to narrow it down)";
  if (typeof updateProjectBar === "function") updateProjectBar();
}

async function assembleVideo() {
  const prompt = assemblePrompt.value.trim();
  if (!prompt) {
    assemblePrompt.focus();
    return;
  }
  const clipIds = [...selectedClipIds];
  assembleBtn.disabled = true;
  assembleStatus.classList.remove("error");
  assembleStatus.textContent = clipIds.length
    ? `Assembling a rough cut from your ${clipIds.length} selected clip(s)…`
    : "Assembling a rough cut from your indexed clips…";
  try {
    // Assemble creates a new EDIT. If a project is selected in the library, attach
    // the edit to it (its saved context also steers the cut, server-side).
    const payload = { prompt, clip_ids: clipIds };
    if (currentProjectId) payload.project_id = parseInt(currentProjectId, 10);
    const res = await fetch("/api/generate-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    assembleStatus.textContent =
      `Assembled ${body.selections.length} clip(s). Opening editor…`;
    // Navigate is intentional — the editor deep-links via ?edit=<id>.
    window.location.href = `/?edit=${body.id}`;
  } catch (err) {
    assembleStatus.classList.add("error");
    assembleStatus.textContent = `Error: ${err.message}`;
    assembleBtn.disabled = false;
  }
}

assembleBtn.addEventListener("click", assembleVideo);
assemblePrompt.addEventListener("keydown", (e) => {
  if (e.key === "Enter") assembleVideo();
});
assembleClear.addEventListener("click", () => {
  clearSelection();
});

function clearSelection() {
  selectedClipIds.clear();
  document.querySelectorAll(".card.selected").forEach((c) => {
    c.classList.remove("selected");
    const box = c.querySelector(".select-box");
    if (box) box.checked = false;
  });
  assembleStatus.classList.remove("error");
  updateAssembleHint();
  updateProjectBar();
}

// ---- projects: filter + add/remove membership ----
const projectFilter = document.getElementById("project-filter");
const projectBar = document.getElementById("project-bar");
const projectBarLabel = document.getElementById("project-bar-label");
const projectBarDesc = document.getElementById("project-bar-desc");
const addToProject = document.getElementById("add-to-project");
const removeFromProject = document.getElementById("remove-from-project");
const projectActionStatus = document.getElementById("project-action-status");

async function loadProjects() {
  const res = await fetch("/api/projects");
  const projects = await res.json();
  projectsById = Object.fromEntries(projects.map((p) => [String(p.id), p]));

  // Fill the header filter (preserving current selection).
  projectFilter.innerHTML = '<option value="">All clips</option>';
  addToProject.innerHTML = '<option value="">Add selected to campaign…</option>';
  projects.forEach((p) => {
    const o1 = document.createElement("option");
    o1.value = String(p.id);
    o1.textContent = `${p.name} (${p.clip_count || 0})`;
    projectFilter.appendChild(o1);
    const o2 = document.createElement("option");
    o2.value = String(p.id);
    o2.textContent = p.name;
    addToProject.appendChild(o2);
  });
  projectFilter.value = currentProjectId;
  updateProjectBar();
}

function updateProjectBar() {
  const p = projectsById[currentProjectId];
  const nSel = selectedClipIds.size;
  // The bar shows whenever a project is open, or when clips are selected (so you
  // can add them to a project from the All-clips view).
  const show = !!p || nSel > 0;
  projectBar.classList.toggle("hidden", !show);
  if (p) {
    projectBarLabel.textContent = p.name;
    projectBarDesc.textContent = p.description || "";
  } else {
    projectBarLabel.textContent = "";
    projectBarDesc.textContent = "";
  }
  // "Remove from project" only makes sense inside a project with a selection.
  removeFromProject.classList.toggle("hidden", !(p && nSel > 0));
  removeFromProject.textContent = `Remove ${nSel} from campaign`;
  // "Add to campaign" dropdown label reflects selection count.
  addToProject.disabled = nSel === 0;
  addToProject.options[0].textContent =
    nSel > 0 ? `Add ${nSel} selected to campaign…` : "Add selected to campaign…";
}

projectFilter.addEventListener("change", async () => {
  currentProjectId = projectFilter.value;
  const url = new URL(window.location);
  if (currentProjectId) url.searchParams.set("project", currentProjectId);
  else url.searchParams.delete("project");
  history.replaceState(null, "", url);
  clearSelection();
  await loadClips();
  updateProjectBar();
});

addToProject.addEventListener("change", async () => {
  const pid = addToProject.value;
  const ids = [...selectedClipIds];
  if (!pid || !ids.length) { addToProject.value = ""; return; }
  projectActionStatus.textContent = "Adding…";
  try {
    await fetch(`/api/projects/${pid}/clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_ids: ids }),
    });
    const name = projectsById[pid]?.name || "project";
    projectActionStatus.textContent = `Added ${ids.length} to "${name}".`;
    clearSelection();
    await loadProjects();          // refresh counts
    if (currentProjectId) await loadClips();
  } catch (err) {
    projectActionStatus.textContent = `Error: ${err.message}`;
  }
  addToProject.value = "";
});

removeFromProject.addEventListener("click", async () => {
  const ids = [...selectedClipIds];
  if (!currentProjectId || !ids.length) return;
  projectActionStatus.textContent = "Removing…";
  try {
    await fetch(`/api/projects/${currentProjectId}/clips`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_ids: ids }),
    });
    projectActionStatus.textContent = `Removed ${ids.length}.`;
    clearSelection();
    await loadProjects();
    await loadClips();
  } catch (err) {
    projectActionStatus.textContent = `Error: ${err.message}`;
  }
});

// Init: honor ?project=<id> deep link, then load projects + clips.
currentProjectId = new URLSearchParams(window.location.search).get("project") || "";
(async () => {
  await loadProjects();
  await loadClips();
})();
