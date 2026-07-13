// ===== Cuts view =====
// Browse every assembled timeline ("cut"/edit) — assigned to a campaign or orphaned.
// Open, rename, delete, or assign to a campaign, so generated work products stop
// getting lost behind a bare ?edit=<id> URL.
// Shares the library bundle's IIFE scope (fmtClock, projectsById, etc.).

function cutsProjectOptions(selectedId) {
  const opts = ['<option value="">Unassigned</option>'];
  Object.values(projectsById || {}).forEach((p) => {
    const sel = String(p.id) === String(selectedId) ? " selected" : "";
    opts.push(`<option value="${p.id}"${sel}>${(p.name || "").replace(/[<>&]/g, "")}</option>`);
  });
  return opts.join("");
}

async function loadCuts() {
  const grid = document.getElementById("cuts-grid");
  const empty = document.getElementById("cuts-empty");
  grid.innerHTML = "";
  let edits = [];
  try {
    edits = await (await fetch("/api/edits")).json();
  } catch {
    grid.textContent = "Couldn't load cuts.";
    return;
  }
  // Refresh campaign list so the assign dropdown is current.
  try {
    const projects = await (await fetch("/api/projects")).json();
    projectsById = Object.fromEntries(projects.map((p) => [String(p.id), p]));
  } catch { /* keep whatever we had */ }

  empty.classList.toggle("hidden", edits.length > 0);

  edits.forEach((e) => {
    const card = document.createElement("div");
    card.className = "cut-card";

    // Thumbnail from the first clip (falls back to a filmstrip glyph).
    const thumb = document.createElement("div");
    thumb.className = "cut-thumb";
    if (e.first_clip_id) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = `/api/clips/${e.first_clip_id}/thumbnail`;
      img.onerror = () => { thumb.classList.add("noimg"); img.remove(); };
      thumb.appendChild(img);
    } else {
      thumb.classList.add("noimg");
    }
    thumb.title = "Open in editor";
    thumb.onclick = () => { window.location.href = `/?edit=${e.id}`; };

    const body = document.createElement("div");
    body.className = "cut-body";

    const name = document.createElement("div");
    name.className = "cut-name";
    name.textContent = e.name || `Edit ${e.id}`;

    const meta = document.createElement("div");
    meta.className = "cut-meta";
    const dur = e.duration_s ? fmtClock(e.duration_s) : "0:00";
    const n = e.item_count || 0;
    meta.textContent = `${dur} · ${n} clip${n === 1 ? "" : "s"}`;

    // Campaign assignment (also fixes orphaned cuts right here).
    const assign = document.createElement("select");
    assign.className = "cut-assign";
    assign.innerHTML = cutsProjectOptions(e.project_id);
    assign.title = "Assign to a campaign";
    assign.onchange = async () => {
      await fetch(`/api/edits/${e.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: assign.value ? parseInt(assign.value, 10) : null }),
      });
      e.project_id = assign.value || null;
    };

    const actions = document.createElement("div");
    actions.className = "cut-actions";
    const openBtn = document.createElement("button");
    openBtn.textContent = "Open";
    openBtn.onclick = () => { window.location.href = `/?edit=${e.id}`; };
    const renameBtn = document.createElement("button");
    renameBtn.textContent = "Rename";
    renameBtn.onclick = async () => {
      const next = prompt("Rename cut:", e.name || "");
      if (next == null) return;
      const trimmed = next.trim();
      if (!trimmed || trimmed === e.name) return;
      await fetch(`/api/edits/${e.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      e.name = trimmed;
      name.textContent = trimmed;
    };
    const delBtn = document.createElement("button");
    delBtn.className = "cut-delete";
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => {
      if (!confirm(`Delete cut "${e.name || `Edit ${e.id}`}"? This can't be undone.`)) return;
      const res = await fetch(`/api/edits/${e.id}`, { method: "DELETE" });
      if (res.ok) card.remove();
      if (!document.getElementById("cuts-grid").children.length) {
        document.getElementById("cuts-empty").classList.remove("hidden");
      }
    };
    actions.append(openBtn, renameBtn, delBtn);

    body.append(name, meta, assign, actions);
    card.append(thumb, body);
    grid.appendChild(card);
  });
}

function showCuts() {
  document.getElementById("grid").classList.add("hidden");
  document.getElementById("map-view").classList.add("hidden");
  document.getElementById("things-view").classList.add("hidden");
  document.getElementById("cuts-view").classList.remove("hidden");
  document.getElementById("view-grid").classList.remove("active");
  document.getElementById("view-map").classList.remove("active");
  document.getElementById("view-things").classList.remove("active");
  document.getElementById("view-cuts").classList.add("active");
  loadCuts();
}

document.getElementById("view-cuts").addEventListener("click", showCuts);

// The other views' show* functions (in map.js/things.js) don't know about this
// view, so make sure switching away hides it and drops the Cuts button state.
["view-grid", "view-map", "view-things"].forEach((id) => {
  document.getElementById(id).addEventListener("click", () => {
    document.getElementById("cuts-view").classList.add("hidden");
    document.getElementById("view-cuts").classList.remove("active");
  });
});
