// "Things" view for the clip library: user-named subjects (a plant species like
// pipevine, an action, a person, an object) that the indexing pipeline watches
// for. Depends on globals from library.js (showInfo) and map.js (showGrid/showMap).

let _things = [];
let _selectedThingId = null;

const KIND_LABEL = {
  plant: "🌿 Plant", animal: "🦋 Animal", person: "🧑 Person",
  action: "🎬 Action", object: "📦 Object", other: "• Other",
};

// ---- view switching (companion to showGrid/showMap in map.js) ----
function showThings() {
  document.getElementById("grid").classList.add("hidden");
  document.getElementById("map-view").classList.add("hidden");
  document.getElementById("things-view").classList.remove("hidden");
  document.getElementById("view-grid").classList.remove("active");
  document.getElementById("view-map").classList.remove("active");
  document.getElementById("view-things").classList.add("active");
  loadThings();
  if (typeof loadPeople === "function") loadPeople();
  loadAnalysisMode();
}

// ---- analysis mode toggle: on-device (CLIP, free) vs Claude API ----
async function loadAnalysisMode() {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();
    const box = document.getElementById("on-device-toggle");
    box.checked = !!s.on_device_vision;
    renderAnalysisStatus(box.checked);
  } catch { /* leave as-is */ }
}

function renderAnalysisStatus(onDevice) {
  const el = document.getElementById("analysis-mode-status");
  el.textContent = onDevice ? "On-device · $0 per clip" : "Claude API · richer captions, costs per clip";
  el.className = onDevice ? "on" : "off";
}

document.getElementById("on-device-toggle").addEventListener("change", async (e) => {
  const onDevice = e.target.checked;
  renderAnalysisStatus(onDevice);
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on_device_vision: onDevice }),
    });
  } catch {
    e.target.checked = !onDevice;  // revert on failure
    renderAnalysisStatus(!onDevice);
  }
});

// Shared clip-grid renderer used by both the things list and the people panel.
function renderClipCards(headText, clips) {
  const head = document.getElementById("things-clips-head");
  const grid = document.getElementById("things-clips-grid");
  head.textContent = headText;
  grid.innerHTML = "";
  clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "card" + (clip.available_locally ? "" : " unavailable");

    // Thumbnail plus an optional overlay box showing WHERE the thing sits in the
    // frame (the region a reframe/crop should keep).
    const shot = document.createElement("div");
    shot.className = "shot";
    const img = document.createElement("img");
    img.className = "thumb";
    img.loading = "lazy";
    img.src = `/api/clips/${clip.id}/thumbnail`;
    img.onerror = () => {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      ph.textContent = "▶";
      img.replaceWith(ph);
    };
    shot.appendChild(img);
    if (clip.region && clip.region.w > 0 && clip.region.h > 0) {
      const box = document.createElement("div");
      box.className = "region-box";
      box.style.left = `${clip.region.x * 100}%`;
      box.style.top = `${clip.region.y * 100}%`;
      box.style.width = `${clip.region.w * 100}%`;
      box.style.height = `${clip.region.h * 100}%`;
      box.title = "Where this thing is in the frame (used for reframing)";
      shot.appendChild(box);
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    const nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = clip.file_stem;
    meta.appendChild(nameEl);
    card.appendChild(shot);
    card.appendChild(meta);
    card.onclick = () => showInfo(clip);
    grid.appendChild(card);
  });
}

async function loadThings() {
  const res = await fetch("/api/things");
  _things = await res.json();
  renderThingsList();
}

function renderThingsList() {
  const list = document.getElementById("things-list");
  list.innerHTML = "";
  if (!_things.length) {
    const li = document.createElement("li");
    li.className = "things-empty";
    li.textContent = "No things yet. Name one above to start watching for it.";
    list.appendChild(li);
    return;
  }
  _things.forEach((t) => {
    const li = document.createElement("li");
    li.className = "thing-row" + (t.id === _selectedThingId ? " selected" : "")
      + (t.active ? "" : " inactive");

    // Cover thumbnail: the most flattering frame among this thing's clips. Cache-bust
    // with the count so a freshly-picked cover refreshes. Hidden until one exists.
    const cover = document.createElement("img");
    cover.className = "thing-cover";
    if (t.clip_count > 0) {
      cover.src = `/api/things/${t.id}/thumbnail?v=${t.clip_count}`;
      cover.onerror = () => { cover.classList.add("empty"); };
    } else {
      cover.classList.add("empty");
    }

    const main = document.createElement("button");
    main.className = "thing-main";
    main.title = t.description || "";
    main.innerHTML =
      `<span class="thing-name">${escapeHtml(t.name)}</span>` +
      `<span class="thing-kind">${KIND_LABEL[t.kind] || (t.kind || "")}</span>` +
      `<span class="thing-count">${t.clip_count} clip${t.clip_count === 1 ? "" : "s"}</span>`;
    main.onclick = () => selectThing(t.id);

    // Re-pick the most flattering cover on demand.
    const pick = document.createElement("button");
    pick.className = "thing-pick";
    pick.textContent = "★";
    pick.title = "Pick the most flattering cover photo";
    pick.disabled = t.clip_count === 0;
    pick.onclick = async (e) => {
      e.stopPropagation();
      pick.disabled = true;
      pick.classList.add("busy");
      try {
        const res = await fetch(`/api/things/${t.id}/pick-thumbnail`, { method: "POST" });
        if (res.ok) {
          const { clip_id } = await res.json();
          cover.classList.remove("empty");
          cover.src = `/api/clips/${clip_id}/thumbnail?v=${Date.now()}`;
        }
      } finally {
        pick.classList.remove("busy");
        pick.disabled = t.clip_count === 0;
      }
    };

    // Active toggle: whether this thing is injected into future indexing.
    const toggle = document.createElement("button");
    toggle.className = "thing-toggle";
    toggle.textContent = t.active ? "watching" : "paused";
    toggle.title = t.active ? "Being watched for — click to pause" : "Paused — click to resume";
    toggle.onclick = async (e) => {
      e.stopPropagation();
      await fetch(`/api/things/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !t.active }),
      });
      loadThings();
    };

    const del = document.createElement("button");
    del.className = "thing-del";
    del.textContent = "×";
    del.title = "Delete this thing";
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${t.name}"? Its clip matches will be forgotten.`)) return;
      await fetch(`/api/things/${t.id}`, { method: "DELETE" });
      if (_selectedThingId === t.id) { _selectedThingId = null; renderThingClips(null, []); }
      loadThings();
    };

    li.appendChild(cover);
    li.appendChild(main);
    li.appendChild(pick);
    li.appendChild(toggle);
    li.appendChild(del);
    list.appendChild(li);
  });
}

async function selectThing(thingId) {
  _selectedThingId = thingId;
  renderThingsList();
  const thing = _things.find((t) => t.id === thingId);
  const res = await fetch(`/api/things/${thingId}/clips`);
  const clips = await res.json();
  renderThingClips(thing, clips);
}

function renderThingClips(thing, clips) {
  const head = document.getElementById("things-clips-head");
  const grid = document.getElementById("things-clips-grid");
  grid.innerHTML = "";
  if (!thing) {
    head.textContent = "Select a thing to see the clips it appears in.";
    return;
  }
  head.textContent = clips.length
    ? `${clips.length} clip${clips.length === 1 ? "" : "s"} with “${thing.name}”`
    : `No clips tagged with “${thing.name}” yet — try “Scan existing clips”.`;
  clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "card" + (clip.available_locally ? "" : " unavailable");

    // Thumbnail plus an optional overlay box showing WHERE the thing sits in the
    // frame (the region a reframe/crop should keep).
    const shot = document.createElement("div");
    shot.className = "shot";
    const img = document.createElement("img");
    img.className = "thumb";
    img.loading = "lazy";
    img.src = `/api/clips/${clip.id}/thumbnail`;
    img.onerror = () => {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      ph.textContent = "▶";
      img.replaceWith(ph);
    };
    shot.appendChild(img);
    if (clip.region && clip.region.w > 0 && clip.region.h > 0) {
      const box = document.createElement("div");
      box.className = "region-box";
      box.style.left = `${clip.region.x * 100}%`;
      box.style.top = `${clip.region.y * 100}%`;
      box.style.width = `${clip.region.w * 100}%`;
      box.style.height = `${clip.region.h * 100}%`;
      box.title = "Where this thing is in the frame (used for reframing)";
      shot.appendChild(box);
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    const nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = clip.file_stem;
    meta.appendChild(nameEl);
    card.appendChild(shot);
    card.appendChild(meta);
    card.onclick = () => showInfo(clip);
    grid.appendChild(card);
  });
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- add a thing ----
document.getElementById("thing-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("thing-name").value.trim();
  if (!name) return;
  const description = document.getElementById("thing-desc").value.trim();
  const btn = document.getElementById("thing-add-btn");
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = "Adding…";  // kind is inferred server-side, so this can take a moment
  try {
    // No `kind` sent — the server infers it from the name.
    const res = await fetch("/api/things", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
    const body = await res.json();
    if (!res.ok) { alert(body.error || "Could not add that thing."); return; }
    document.getElementById("thing-name").value = "";
    document.getElementById("thing-desc").value = "";
    loadThings();
  } finally {
    btn.disabled = false;
    btn.textContent = prevLabel;
  }
});

// ---- scan existing clips (background job with progress) ----
const thingsProgress = document.getElementById("things-progress");
const thingsBar = document.getElementById("things-bar");
const thingsBarFill = document.getElementById("things-bar-fill");
const thingsProgressLabel = document.getElementById("things-progress-label");

document.getElementById("things-scan-btn").addEventListener("click", async () => {
  const btn = document.getElementById("things-scan-btn");
  const status = document.getElementById("things-scan-status");
  const activeCount = _things.filter((t) => t.active).length;
  if (!activeCount) { status.textContent = "No active things to scan for."; return; }
  if (!confirm(`Scan every local clip for your ${activeCount} active thing(s)? This runs AI analysis per clip.`)) return;

  btn.disabled = true;
  status.textContent = "";
  thingsProgress.classList.remove("hidden");
  try {
    const res = await fetch("/api/things/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const started = await res.json();
    if (!res.ok) throw new Error(started.error || res.statusText);

    for (;;) {
      const r = await fetch(`/api/import-jobs/${started.job_id}`);
      const job = await r.json();
      if (!r.ok) throw new Error(job.error || r.statusText);
      if (!job.total) {
        thingsBar.classList.add("indeterminate");
        thingsProgressLabel.textContent = "Preparing scan…";
      } else {
        thingsBar.classList.remove("indeterminate");
        thingsBarFill.style.width = `${Math.round((job.done / job.total) * 100)}%`;
        const bits = [`Scanning · ${job.done} of ${job.total} clips`];
        if (job.current) bits.push(job.current);
        if (job.eta_s != null) bits.push(fmtTime(job.eta_s));
        thingsProgressLabel.textContent = bits.join(" · ");
      }
      if (job.finished) {
        const r0 = job.results && job.results[0];
        if (r0 && r0.status === "scanned") {
          status.textContent = `Scanned ${r0.clips} clip(s) · ${r0.new_matches} new match(es).`;
        } else if (r0 && r0.error) {
          status.textContent = r0.error;
        } else {
          status.textContent = "Scan complete.";
        }
        break;
      }
      await sleep(700);
    }
    thingsProgress.classList.add("hidden");
    loadThings();
    if (_selectedThingId) selectThing(_selectedThingId);
  } catch (err) {
    thingsProgress.classList.add("hidden");
    status.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("view-things").addEventListener("click", showThings);
