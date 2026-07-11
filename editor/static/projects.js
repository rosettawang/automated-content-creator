let allProjects = [];

function escapeText(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let editsByProject = {}; // project_id (or "" for unassigned) -> [edit, ...]
let editingId = null; // null = creating, otherwise editing this project id

async function loadProjects() {
  const [pRes, eRes] = await Promise.all([fetch("/api/projects"), fetch("/api/edits")]);
  allProjects = await pRes.json();
  const edits = await eRes.json();
  editsByProject = {};
  edits.forEach((e) => {
    const key = e.project_id != null ? String(e.project_id) : "";
    (editsByProject[key] = editsByProject[key] || []).push(e);
  });
  render();
}

function render() {
  const grid = document.getElementById("projects-grid");
  grid.innerHTML = "";
  document.getElementById("count").textContent =
    `${allProjects.length} campaign${allProjects.length === 1 ? "" : "s"}`;
  document.getElementById("empty").classList.toggle("hidden", allProjects.length > 0);

  allProjects.forEach((p) => {
    const card = document.createElement("div");
    card.className = "project-card";

    const title = document.createElement("div");
    title.className = "project-title";
    title.textContent = p.name;

    const desc = document.createElement("div");
    desc.className = "project-desc";
    desc.textContent = p.description || "No description yet.";
    if (!p.description) desc.classList.add("muted");

    const meta = document.createElement("div");
    meta.className = "project-meta";
    meta.textContent = `${p.clip_count || 0} clip${p.clip_count === 1 ? "" : "s"}`;

    // Saved cuts (edits) in this campaign — click one to open it in the editor.
    const cuts = editsByProject[String(p.id)] || [];
    const cutsWrap = document.createElement("div");
    cutsWrap.className = "project-cuts";
    if (cuts.length) {
      const label = document.createElement("div");
      label.className = "cuts-label";
      label.textContent = `Cuts (${cuts.length})`;
      cutsWrap.appendChild(label);
      cuts.forEach((cut) => {
        const row = document.createElement("button");
        row.className = "cut-row";
        row.title = "Open this cut in the editor";
        row.innerHTML =
          `<span class="cut-name">${escapeText(cut.name)}</span>` +
          `<span class="cut-count">${cut.item_count || 0} clip${cut.item_count === 1 ? "" : "s"}</span>`;
        row.onclick = (e) => {
          e.stopPropagation();
          window.location.href = `/?edit=${cut.id}`;
        };
        cutsWrap.appendChild(row);
      });
    } else {
      const none = document.createElement("div");
      none.className = "cuts-label muted";
      none.textContent = "No cuts yet — open the editor and Generate one";
      cutsWrap.appendChild(none);
    }

    const actions = document.createElement("div");
    actions.className = "project-actions";

    const editBtn = document.createElement("button");
    editBtn.className = "secondary";
    editBtn.textContent = "Edit";
    editBtn.onclick = (e) => { e.stopPropagation(); openDialog(p); };

    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "Delete";
    delBtn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete campaign "${p.name}"? Clips themselves are not deleted.`)) return;
      await fetch(`/api/projects/${p.id}`, { method: "DELETE" });
      await loadProjects();
    };

    actions.append(editBtn, delBtn);
    card.append(title, desc, meta, cutsWrap, actions);
    card.onclick = () => openDrawer(p);
    grid.appendChild(card);
  });
}

// ---- create / edit dialog ----
const overlay = document.getElementById("project-overlay");
const nameInput = document.getElementById("project-name");
const descInput = document.getElementById("project-description");
const errorEl = document.getElementById("project-error");

function openDialog(project) {
  editingId = project ? project.id : null;
  document.getElementById("project-dialog-title").textContent =
    project ? "Edit campaign" : "New campaign";
  document.getElementById("project-save").textContent = project ? "Save" : "Create";
  nameInput.value = project ? project.name : "";
  descInput.value = project ? (project.description || "") : "";
  errorEl.textContent = "";
  overlay.classList.remove("hidden");
  nameInput.focus();
}

function closeDialog() {
  overlay.classList.add("hidden");
  editingId = null;
}

document.getElementById("new-project-btn").addEventListener("click", () => openDialog(null));
document.getElementById("project-close").addEventListener("click", closeDialog);
document.getElementById("project-cancel").addEventListener("click", closeDialog);
overlay.addEventListener("click", (e) => { if (e.target.id === "project-overlay") closeDialog(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !overlay.classList.contains("hidden")) closeDialog();
});

const saveBtn = document.getElementById("project-save");
saveBtn.addEventListener("click", async () => {
  const name = nameInput.value.trim();
  const description = descInput.value.trim();
  if (!name) { errorEl.textContent = "Give the campaign a name."; return; }
  saveBtn.disabled = true;
  try {
    if (editingId == null) {
      errorEl.textContent = "Creating campaign and inferring key things…";
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description }),
      });
      const created = await res.json();
      closeDialog();
      await loadProjects();
      // Jump straight into the new campaign's drawer so the inferred things show.
      const full = allProjects.find((p) => p.id === created.id) || created;
      openDrawer(full);
    } else {
      await fetch(`/api/projects/${editingId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description }),
      });
      closeDialog();
      await loadProjects();
    }
  } catch (err) {
    errorEl.textContent = `Error: ${err.message}`;
  } finally {
    saveBtn.disabled = false;
  }
});

// ============================ Campaign drawer ============================
let drawerProject = null;

const drawer = document.getElementById("campaign-drawer");
const scrim = document.getElementById("drawer-scrim");

function openDrawer(project) {
  drawerProject = project;
  document.getElementById("drawer-title").textContent = project.name;
  document.getElementById("drawer-desc").textContent = project.description || "";
  drawer.classList.remove("hidden");
  scrim.classList.remove("hidden");
  loadThings();
  loadChat();
}

function closeDrawer() {
  drawer.classList.add("hidden");
  scrim.classList.add("hidden");
  drawerProject = null;
}

document.getElementById("drawer-close").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !drawer.classList.contains("hidden")) closeDrawer();
});

// ---- things ----
async function loadThings() {
  const list = document.getElementById("things-list");
  list.innerHTML = "<li class='muted'>Loading…</li>";
  const res = await fetch(`/api/projects/${drawerProject.id}/things`);
  const things = await res.json();
  renderThings(things);
}

function renderThings(things) {
  const list = document.getElementById("things-list");
  list.innerHTML = "";
  if (!things.length) {
    list.innerHTML = "<li class='muted'>No things yet — add ones to watch for.</li>";
    return;
  }
  things.forEach((t) => {
    const li = document.createElement("li");
    li.className = "thing-item";

    const nameEl = document.createElement("span");
    nameEl.className = "thing-name";
    nameEl.textContent = t.name;
    nameEl.title = "Click to rename";
    nameEl.onclick = () => editThing(t);

    const kindEl = document.createElement("span");
    kindEl.className = "thing-kind";
    kindEl.textContent = t.kind || "";

    const countEl = document.createElement("span");
    countEl.className = "thing-count";
    countEl.textContent = t.clip_count ? `${t.clip_count} clip${t.clip_count === 1 ? "" : "s"}` : "";

    const rm = document.createElement("button");
    rm.className = "thing-remove";
    rm.textContent = "×";
    rm.title = "Remove from this campaign";
    rm.onclick = async () => {
      await fetch(`/api/projects/${drawerProject.id}/things/${t.id}`, { method: "DELETE" });
      loadThings();
    };

    li.append(nameEl, kindEl, countEl, rm);
    if (t.description) {
      const d = document.createElement("div");
      d.className = "thing-desc";
      d.textContent = t.description;
      li.appendChild(d);
    }
    list.appendChild(li);
  });
}

async function editThing(t) {
  const name = prompt("Rename this thing:", t.name);
  if (name === null) return;
  const description = prompt("Hint to help spot it (optional):", t.description || "");
  await fetch(`/api/things/${t.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() || t.name, description: (description || "").trim() }),
  });
  loadThings();
}

document.getElementById("thing-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("thing-add-name");
  const name = input.value.trim();
  if (!name) return;
  input.value = "";
  await fetch(`/api/projects/${drawerProject.id}/things`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  loadThings();
});

// ---- chat ----
async function loadChat() {
  const log = document.getElementById("chat-log");
  log.innerHTML = "";
  const res = await fetch(`/api/projects/${drawerProject.id}/chat`);
  const msgs = await res.json();
  if (!msgs.length) {
    log.innerHTML = "<div class='chat-empty'>Ask anything about this campaign — ideas, "
      + "what footage you have, what to shoot next.</div>";
  } else {
    msgs.forEach((m) => appendChat(m.role, m.content));
  }
}

function appendChat(role, content) {
  const log = document.getElementById("chat-log");
  const empty = log.querySelector(".chat-empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.textContent = content;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendChat("user", message);
  const thinking = appendChat("assistant", "…");
  sendBtn.disabled = true;
  try {
    const res = await fetch(`/api/projects/${drawerProject.id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const body = await res.json();
    thinking.textContent = res.ok ? body.reply : `Error: ${body.error || res.statusText}`;
  } catch (err) {
    thinking.textContent = `Error: ${err.message}`;
  } finally {
    sendBtn.disabled = false;
    document.getElementById("chat-log").scrollTop = 1e9;
  }
});

loadProjects();
