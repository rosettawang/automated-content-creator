let currentProjectId = null;
let selectedClip = null;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

async function loadClips(query = "") {
  const clips = await api(`/api/clips?q=${encodeURIComponent(query)}`);
  const list = document.getElementById("clip-list");
  list.innerHTML = "";
  clips.forEach((clip) => {
    const el = document.createElement("div");
    el.className = "clip-item" + (clip.available_locally ? "" : " unavailable");
    el.innerHTML = `
      <div class="stem">${clip.file_stem} ${clip.available_locally ? "" : "(not local)"}</div>
      <div class="desc">${clip.description || ""}</div>
    `;
    el.onclick = () => selectClip(clip);
    list.appendChild(el);
  });
}

function selectClip(clip) {
  selectedClip = clip;
  const video = document.getElementById("preview");
  video.src = `/api/clips/${clip.id}/media`;
  document.getElementById("preview-info").textContent =
    `${clip.file_stem} — ${clip.duration_s || "?"}s — ${clip.category || ""}`;
  const trimControls = document.getElementById("trim-controls");
  trimControls.style.display = clip.available_locally ? "flex" : "none";
  document.getElementById("in-point").value = 0;
  document.getElementById("out-point").value = clip.duration_s || 0;
  document.getElementById("transcript-box").textContent = clip.transcript
    ? `Transcript: ${clip.transcript}`
    : "";
}

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
  const list = document.getElementById("timeline-list");
  list.innerHTML = "";
  project.items.forEach((item, idx) => {
    const el = document.createElement("div");
    el.className = "timeline-item";
    el.innerHTML = `
      <div class="stem">${idx + 1}. ${item.file_stem}</div>
      <div class="desc">${item.description || ""}</div>
      <div class="controls">
        <input type="number" step="0.1" value="${item.in_point}" data-role="in">
        <span>&rarr;</span>
        <input type="number" step="0.1" value="${item.out_point}" data-role="out">
        <button data-role="up">&uarr;</button>
        <button data-role="down">&darr;</button>
        <button data-role="remove">x</button>
      </div>
    `;
    el.querySelector('[data-role="in"]').onchange = (e) =>
      updateItem(item.id, { in_point: parseFloat(e.target.value) });
    el.querySelector('[data-role="out"]').onchange = (e) =>
      updateItem(item.id, { out_point: parseFloat(e.target.value) });
    el.querySelector('[data-role="remove"]').onclick = () => removeItem(item.id);
    el.querySelector('[data-role="up"]').onclick = () => moveItem(idx, -1, project.items);
    el.querySelector('[data-role="down"]').onclick = () => moveItem(idx, 1, project.items);
    list.appendChild(el);
  });
}

async function updateItem(itemId, patch) {
  await api(`/api/projects/${currentProjectId}/items/${itemId}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
  await loadTimeline();
}

async function removeItem(itemId) {
  await api(`/api/projects/${currentProjectId}/items/${itemId}`, { method: "DELETE" });
  await loadTimeline();
}

async function moveItem(idx, dir, items) {
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= items.length) return;
  const ids = items.map((i) => i.id);
  [ids[idx], ids[newIdx]] = [ids[newIdx], ids[idx]];
  await api(`/api/projects/${currentProjectId}/reorder`, {
    method: "POST",
    body: JSON.stringify({ item_ids: ids }),
  });
  await loadTimeline();
}

document.getElementById("search").addEventListener("input", (e) => loadClips(e.target.value));

document.getElementById("project-select").addEventListener("change", (e) => {
  currentProjectId = parseInt(e.target.value, 10);
  loadTimeline();
});

document.getElementById("new-project").addEventListener("click", async () => {
  const name = prompt("Project name?");
  if (!name) return;
  const project = await api("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
  currentProjectId = project.id;
  await loadProjects();
});

document.getElementById("add-to-timeline").addEventListener("click", async () => {
  if (!selectedClip || !currentProjectId) return;
  const inPoint = parseFloat(document.getElementById("in-point").value);
  const outPoint = parseFloat(document.getElementById("out-point").value);
  await api(`/api/projects/${currentProjectId}/items`, {
    method: "POST",
    body: JSON.stringify({ clip_id: selectedClip.id, in_point: inPoint, out_point: outPoint }),
  });
  await loadTimeline();
});

document.getElementById("export-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("export-result");
  resultEl.textContent = "Exporting...";
  try {
    const result = await api(`/api/projects/${currentProjectId}/export`, { method: "POST" });
    resultEl.textContent = `Exported to ${result.output}`;
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }
});

document.getElementById("transcribe-btn").addEventListener("click", async () => {
  if (!selectedClip) return;
  const box = document.getElementById("transcript-box");
  box.textContent = "Transcribing... (this can take a bit)";
  try {
    const result = await api(`/api/clips/${selectedClip.id}/transcribe`, { method: "POST" });
    selectedClip.transcript = result.transcript;
    box.textContent = `Transcript: ${result.transcript}`;
  } catch (err) {
    box.textContent = `Error: ${err.message}`;
  }
});

document.getElementById("generate-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("generate-result");
  const prompt = document.getElementById("generate-prompt").value.trim();
  if (!prompt || !currentProjectId) return;
  resultEl.textContent = "Generating rough cut... (this can take a bit)";
  try {
    const result = await api(`/api/projects/${currentProjectId}/generate`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    resultEl.textContent = `${result.concept} (${result.selections.length} clips added)`;
    await loadTimeline();
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }
});

document.getElementById("suggest-content-btn").addEventListener("click", async () => {
  const container = document.getElementById("content-ideas");
  container.textContent = "Thinking of ideas...";
  try {
    const result = await api("/api/suggest-content", { method: "POST" });
    container.innerHTML = "";
    result.ideas.forEach((idea) => {
      const el = document.createElement("div");
      el.className = "idea-item";
      el.innerHTML = `
        <div class="idea-title">${idea.idea}</div>
        <div class="idea-rationale">${idea.rationale}</div>
      `;
      container.appendChild(el);
    });
  } catch (err) {
    container.textContent = `Error: ${err.message}`;
  }
});

loadClips();
loadProjects();
