let allCampaigns = [];

function escapeText(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// A one-line card summary from the markdown context brief: skip headings/blank
// lines, take the first real sentence-ish line, trimmed to a tidy length.
function contextSummary(doc, max = 160) {
  if (!doc) return "";
  const line = doc.split("\n")
    .filter((l) => !/^\s*#/.test(l))                 // skip markdown headings (# Campaign: …)
    .map((l) => l.replace(/^[>\-*\s]+/, "")          // strip list/quote marks
                 .replace(/\*\*/g, "").trim())       // drop bold markers
    .find((l) => l.length > 0);
  if (!line) return "";
  return line.length > max ? line.slice(0, max - 1).trimEnd() + "…" : line;
}

let editsByCampaign = {}; // campaign_id (or "" for unassigned) -> [edit, ...]
let editingId = null; // null = creating, otherwise editing this campaign id

async function loadCampaigns() {
  const [campaigns, edits] = await Promise.all([api("/api/campaigns"), api("/api/edits")]);
  allCampaigns = campaigns;
  editsByCampaign = {};
  edits.forEach((e) => {
    const key = e.campaign_id != null ? String(e.campaign_id) : "";
    (editsByCampaign[key] = editsByCampaign[key] || []).push(e);
  });
  render();
}

function render() {
  const grid = document.getElementById("campaigns-grid");
  grid.innerHTML = "";
  document.getElementById("cmp-count").textContent =
    `${allCampaigns.length} campaign${allCampaigns.length === 1 ? "" : "s"}`;
  document.getElementById("empty").classList.toggle("hidden", allCampaigns.length > 0);

  allCampaigns.forEach((p) => {
    const card = document.createElement("div");
    card.className = "campaign-card";

    const title = document.createElement("div");
    title.className = "campaign-title";
    title.textContent = p.name;

    const desc = document.createElement("div");
    desc.className = "campaign-desc";
    // Prefer a manual description; otherwise fall back to a summary of the living
    // context brief the chat maintains, so a campaign with real context doesn't
    // read as empty.
    const summary = (p.description || "").trim() || contextSummary(p.context_doc);
    desc.textContent = summary || "No description yet.";
    if (!summary) desc.classList.add("muted");

    const meta = document.createElement("div");
    meta.className = "campaign-meta";
    meta.textContent = `${p.clip_count || 0} clip${p.clip_count === 1 ? "" : "s"}`;

    // Saved cuts (edits) in this campaign — click one to open it in the editor.
    const cuts = editsByCampaign[String(p.id)] || [];
    const cutsWrap = document.createElement("div");
    cutsWrap.className = "campaign-cuts";
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
          if (window.studioOpenEdit) window.studioOpenEdit(cut.id);
          else window.location.href = `/?edit=${cut.id}`;
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
    actions.className = "campaign-actions";

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
      await api(`/api/campaigns/${p.id}`, { method: "DELETE" });
      await loadCampaigns();
    };

    actions.append(editBtn, delBtn);
    card.append(title, desc, meta, cutsWrap, actions);
    card.onclick = () => openDrawer(p);
    grid.appendChild(card);
  });
}

// ---- create / edit dialog ----
const overlay = document.getElementById("campaign-overlay");
const nameInput = document.getElementById("campaign-name");
const descInput = document.getElementById("campaign-description");
const errorEl = document.getElementById("campaign-error");

function openDialog(campaign) {
  editingId = campaign ? campaign.id : null;
  document.getElementById("campaign-dialog-title").textContent =
    campaign ? "Edit campaign" : "New campaign";
  document.getElementById("campaign-save").textContent = campaign ? "Save" : "Create";
  nameInput.value = campaign ? campaign.name : "";
  descInput.value = campaign ? (campaign.description || "") : "";
  errorEl.textContent = "";
  overlay.classList.remove("hidden");
  nameInput.focus();
}

function closeDialog() {
  overlay.classList.add("hidden");
  editingId = null;
}

document.getElementById("new-campaign-btn").addEventListener("click", () => openDialog(null));
document.getElementById("campaign-close").addEventListener("click", closeDialog);
document.getElementById("campaign-cancel").addEventListener("click", closeDialog);
overlay.addEventListener("click", (e) => { if (e.target.id === "campaign-overlay") closeDialog(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !overlay.classList.contains("hidden")) closeDialog();
});

const saveBtn = document.getElementById("campaign-save");
saveBtn.addEventListener("click", async () => {
  const name = nameInput.value.trim();
  const description = descInput.value.trim();
  if (!name) { errorEl.textContent = "Give the campaign a name."; return; }
  saveBtn.disabled = true;
  try {
    if (editingId == null) {
      errorEl.textContent = "Creating campaign and inferring key things…";
      const created = await api("/api/campaigns", { method: "POST", body: JSON.stringify({ name, description }) });
      closeDialog();
      await loadCampaigns();
      // Jump straight into the new campaign's drawer so the inferred things show.
      const full = allCampaigns.find((p) => p.id === created.id) || created;
      openDrawer(full);
    } else {
      await api(`/api/campaigns/${editingId}`, { method: "PUT", body: JSON.stringify({ name, description }) });
      closeDialog();
      await loadCampaigns();
    }
  } catch (err) {
    errorEl.textContent = `Error: ${err.message}`;
  } finally {
    saveBtn.disabled = false;
  }
});

// ============================ Campaign drawer ============================
let drawerCampaign = null;

const drawer = document.getElementById("campaign-drawer");
const scrim = document.getElementById("drawer-scrim");

function openDrawer(campaign) {
  drawerCampaign = campaign;
  document.getElementById("drawer-title").textContent = campaign.name;
  document.getElementById("drawer-desc").textContent = campaign.description || "";
  document.getElementById("cmp-context-doc").value = campaign.context_doc || "";
  document.getElementById("cmp-context-status").textContent = "";
  document.getElementById("cmp-arm-check").checked = !!campaign.publishing_armed;
  document.getElementById("cmp-arm-hint").textContent = "";
  drawer.classList.remove("hidden");
  scrim.classList.remove("hidden");
  loadThings();
  loadChat();
  loadPosts();
}

document.getElementById("cmp-arm-check").addEventListener("change", async (e) => {
  if (!drawerCampaign) return;
  const armed = e.target.checked;
  const hint = document.getElementById("cmp-arm-hint");
  try {
    const res = await api(`/api/campaigns/${drawerCampaign.id}/arm`, {
      method: "POST", body: JSON.stringify({ armed }),
    });
    drawerCampaign.publishing_armed = res.publishing_armed ? 1 : 0;
    cmpDryRun = res.dry_run;
    hint.textContent = !armed ? ""
      : (res.dry_run ? "Armed — but dry-run is on, so nothing goes live yet."
                     : "⚠ LIVE — posts to the connected account.");
  } catch (err) {
    e.target.checked = !armed;  // revert on failure
    hint.textContent = `Error: ${err.message}`;
  }
});

// The chat keeps the context doc current; the user can also edit it directly.
function setContextDoc(text) {
  document.getElementById("cmp-context-doc").value = text || "";
  if (drawerCampaign) drawerCampaign.context_doc = text || "";
}

document.getElementById("cmp-context-save").addEventListener("click", async () => {
  if (!drawerCampaign) return;
  const status = document.getElementById("cmp-context-status");
  const context_doc = document.getElementById("cmp-context-doc").value;
  status.textContent = "Saving…";
  try {
    await api(`/api/campaigns/${drawerCampaign.id}`, { method: "PUT", body: JSON.stringify({ context_doc }) });
    drawerCampaign.context_doc = context_doc;
    status.textContent = "Saved";
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  }
});

function closeDrawer() {
  drawer.classList.add("hidden");
  scrim.classList.add("hidden");
  drawerCampaign = null;
}

document.getElementById("drawer-close").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !drawer.classList.contains("hidden")) closeDrawer();
});

// ---- things ----
async function loadThings() {
  const list = document.getElementById("cmp-things-list");
  list.innerHTML = "<li class='muted'>Loading…</li>";
  const things = await api(`/api/campaigns/${drawerCampaign.id}/things`);
  renderThings(things);
}

function renderThings(things) {
  const list = document.getElementById("cmp-things-list");
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
      await api(`/api/campaigns/${drawerCampaign.id}/things/${t.id}`, { method: "DELETE" });
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
  await api(`/api/things/${t.id}`, {
    method: "PATCH",
    body: JSON.stringify({ name: name.trim() || t.name, description: (description || "").trim() }),
  });
  loadThings();
}

document.getElementById("cmp-thing-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("thing-add-name");
  const name = input.value.trim();
  if (!name) return;
  input.value = "";
  await api(`/api/campaigns/${drawerCampaign.id}/things`, { method: "POST", body: JSON.stringify({ name }) });
  loadThings();
});

// ---- chat ----
async function loadChat() {
  const log = document.getElementById("chat-log");
  log.innerHTML = "";
  const msgs = await api(`/api/campaigns/${drawerCampaign.id}/chat`);
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

let cmpChatSending = false;

// Render an actionable "add these clips" recommendation card in the chat log.
function appendRecommendation(rec) {
  if (!rec || !rec.clips || !rec.clips.length) return;
  const log = document.getElementById("chat-log");
  const card = document.createElement("div");
  card.className = "chat-rec";
  const n = rec.clips.length;
  const head = document.createElement("div");
  head.className = "chat-rec-head";
  head.textContent = rec.reason || `Add ${n} clip${n === 1 ? "" : "s"} to this campaign?`;
  const list = document.createElement("div");
  list.className = "chat-rec-list";
  list.textContent = rec.clips.map((c) => c.description || c.file_stem).join(" · ");
  const btn = document.createElement("button");
  btn.className = "chat-rec-add";
  btn.textContent = `Add ${n} clip${n === 1 ? "" : "s"}`;
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      await api(`/api/campaigns/${drawerCampaign.id}/clips`, {
        method: "POST",
        body: JSON.stringify({ clip_ids: rec.clips.map((c) => c.id) }),
      });
      btn.textContent = `Added ${n}`;
      if (window.toast) toast(`Added ${n} clip${n === 1 ? "" : "s"} to ${drawerCampaign.name}`);
      loadCampaigns();  // refresh clip counts on the cards
    } catch (err) {
      btn.disabled = false;
      btn.textContent = `Error — retry`;
    }
  };
  card.append(head, list, btn);
  log.appendChild(card);
  log.scrollTop = log.scrollHeight;
}

document.getElementById("cmp-chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (cmpChatSending) return;
  const input = document.getElementById("cmp-chat-input");
  const sendBtn = document.getElementById("cmp-chat-send");
  const message = input.value.trim();
  if (!message) return;

  cmpChatSending = true;
  sendBtn.disabled = true;
  input.disabled = true;                 // keep text until acknowledged
  appendChat("user", message);
  const thinking = appendChat("assistant", "…");
  try {
    const body = await api(`/api/campaigns/${drawerCampaign.id}/chat`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    thinking.textContent = body.reply;
    input.value = "";                    // clear only on success
    if (body.context_doc != null) setContextDoc(body.context_doc);
    appendRecommendation(body.recommend);
  } catch (err) {
    thinking.textContent = `Error: ${err.message}`;   // text preserved for retry
    thinking.classList.add("chat-error");
  } finally {
    cmpChatSending = false;
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
    document.getElementById("chat-log").scrollTop = 1e9;
  }
});

// ============================ Posts (publishing) ============================
const POST_PLATFORMS = ["instagram", "tiktok", "youtube", "facebook"];

function initPostPlatforms() {
  const sel = document.getElementById("cmp-post-platform");
  if (!sel || sel.options.length) return;
  POST_PLATFORMS.forEach((p) => {
    const o = document.createElement("option");
    o.value = p;
    o.textContent = p[0].toUpperCase() + p.slice(1);
    sel.appendChild(o);
  });
}

let cmpPosts = [];      // last-loaded posts for this campaign
let cmpCalView = false;  // false = list, true = calendar

async function loadPosts() {
  initPostPlatforms();
  if (!drawerCampaign) return;
  const list = document.getElementById("cmp-posts-list");
  list.innerHTML = "<li class='muted'>Loading…</li>";
  const [posts, summary] = await Promise.all([
    api(`/api/campaigns/${drawerCampaign.id}/posts`),
    api(`/api/campaigns/${drawerCampaign.id}/summary`).catch(() => ({})),
  ]);
  cmpPosts = posts;
  renderKpis(posts, summary);
  renderPosts(posts);
  renderCalendar(posts);
  renderLearn(summary);
}

function renderLearn(summary) {
  const body = document.getElementById("cmp-learn-body");
  if (!summary || !summary.has_data) {
    body.innerHTML = "<p class='muted'>" +
      escapeText((summary && summary.headline) || "No metrics yet — publish, then Refresh metrics.") +
      "</p>";
    return;
  }
  const tops = (summary.top_posts || []).map((t) =>
    `<li><span class="learn-plat">${escapeText(t.platform)}</span> ` +
    `<span class="learn-label">${escapeText(t.label || "(untitled)")}</span> ` +
    `<span class="learn-reach">${fmtCount(t.reach)} reach</span></li>`).join("");
  body.innerHTML =
    `<p class="learn-headline">${escapeText(summary.headline)}</p>` +
    (tops ? `<ul class="learn-tops">${tops}</ul>` : "") +
    `<div class="learn-actions"><button id="cmp-plan-shoot" type="button">Plan next shoot</button></div>`;
  // "Plan next shoot" seeds the chat with a recommendation ask (grounded in metrics).
  const plan = document.getElementById("cmp-plan-shoot");
  if (plan) plan.onclick = () => {
    const input = document.getElementById("cmp-chat-input");
    input.value = "Based on how these posts performed, what should I shoot and post next?";
    document.getElementById("cmp-chat-form").dispatchEvent(new Event("submit", {cancelable:true, bubbles:true}));
  };
}

document.getElementById("cmp-metrics-refresh").addEventListener("click", async () => {
  if (!drawerCampaign) return;
  const btn = document.getElementById("cmp-metrics-refresh");
  btn.disabled = true; btn.textContent = "Fetching…";
  try {
    await api(`/api/campaigns/${drawerCampaign.id}/metrics/refresh`, { method: "POST" });
    setTimeout(() => { loadPosts(); btn.disabled = false; btn.textContent = "Refresh metrics"; }, 1200);
  } catch (err) {
    btn.disabled = false; btn.textContent = "Refresh metrics";
  }
});

function fmtCount(n) {
  if (n == null) return "—";
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k";
  return String(n);
}

function renderKpis(posts, summary) {
  const scheduled = posts.filter((p) => p.status === "scheduled").length;
  const published = posts.filter((p) => p.status === "published").length;
  document.getElementById("kpi-scheduled").textContent = scheduled;
  document.getElementById("kpi-published").textContent = published;
  document.getElementById("kpi-reach").textContent = fmtCount(summary && summary.total_reach);
  const spend = summary && summary.total_spend;
  document.getElementById("kpi-spend").textContent = spend ? `$${Math.round(spend)}` : "$0";
  // Dry-run pill: reflect whatever the last create/arm call reported.
  const pill = document.getElementById("cmp-dryrun-pill");
  pill.classList.toggle("hidden", cmpDryRun === false);
}
let cmpDryRun = true;  // updated from create/arm responses

const POST_STATUS = {
  draft: "Draft", scheduled: "Scheduled", claimed: "Publishing…",
  publishing: "Publishing…", published: "Published", failed: "Failed",
  cancelled: "Cancelled", needs_review: "Needs review",
};

function renderPosts(posts) {
  const list = document.getElementById("cmp-posts-list");
  list.innerHTML = "";
  if (!posts.length) {
    list.innerHTML = "<li class='muted'>No posts yet.</li>";
    return;
  }
  posts.forEach((p) => {
    const li = document.createElement("li");
    li.className = `post-item post-${p.status}`;
    const cap = (p.caption || "").trim() || "(no caption)";
    const whenRaw = p.published_at || p.scheduled_at || "";
    // Show a compact local time (the ISO is stored UTC); fall back to raw on parse fail.
    let when = whenRaw;
    if (whenRaw) {
      const d = new Date(whenRaw);
      if (!isNaN(d)) {
        when = (p.scheduled_at && !p.published_at ? "⏰ " : "") +
          d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      }
    }
    li.innerHTML =
      `<span class="post-platform">${escapeText(p.platform)}</span>` +
      `<span class="post-status">${POST_STATUS[p.status] || p.status}</span>` +
      `<span class="post-caption">${escapeText(cap)}</span>` +
      (when ? `<span class="post-when">${escapeText(when)}</span>` : "") +
      (p.error ? `<span class="post-error">${escapeText(p.error)}</span>` : "");
    if (["draft", "scheduled", "failed", "needs_review"].includes(p.status)) {
      const cancel = document.createElement("button");
      cancel.className = "post-cancel";
      cancel.textContent = "×";
      cancel.title = "Cancel this post";
      cancel.onclick = async (e) => {
        e.stopPropagation();
        await api(`/api/posts/${p.id}/cancel`, { method: "POST" });
        loadPosts();
      };
      li.appendChild(cancel);
    }
    li.onclick = () => openPostDetail(p.id);
    list.appendChild(li);
  });
}

// ---- calendar view (current month; post chips on their day) ----
function renderCalendar(posts) {
  const cal = document.getElementById("cmp-posts-cal");
  cal.innerHTML = "";
  const now = new Date();
  const year = now.getFullYear(), month = now.getMonth();
  const first = new Date(year, month, 1);
  const startDow = (first.getDay() + 6) % 7;  // Mon=0
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach((d) => {
    const h = document.createElement("div"); h.className = "cal-dow"; h.textContent = d; cal.appendChild(h);
  });
  for (let i = 0; i < startDow; i++) cal.appendChild(Object.assign(document.createElement("div"), { className: "cal-day empty" }));

  // Bucket posts by day-of-month (this month only).
  const byDay = {};
  posts.forEach((p) => {
    const raw = p.published_at || p.scheduled_at;
    if (!raw) return;
    const d = new Date(raw);
    if (isNaN(d) || d.getFullYear() !== year || d.getMonth() !== month) return;
    (byDay[d.getDate()] = byDay[d.getDate()] || []).push(p);
  });

  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement("div");
    cell.className = "cal-day" + (day === now.getDate() ? " today" : "");
    const num = document.createElement("div"); num.className = "cal-num"; num.textContent = day;
    cell.appendChild(num);
    (byDay[day] || []).forEach((p) => {
      const chip = document.createElement("div");
      chip.className = `cal-chip post-${p.status}`;
      chip.textContent = `${p.platform} ${(p.caption || "").slice(0, 12)}`;
      chip.title = p.caption || p.platform;
      chip.onclick = () => openPostDetail(p.id);
      cell.appendChild(chip);
    });
    cal.appendChild(cell);
  }
}

document.getElementById("cmp-view-list").addEventListener("click", () => setScheduleView(false));
document.getElementById("cmp-view-cal").addEventListener("click", () => setScheduleView(true));

function setScheduleView(calendar) {
  cmpCalView = calendar;
  document.getElementById("cmp-posts-list").classList.toggle("hidden", calendar);
  document.getElementById("cmp-posts-cal").classList.toggle("hidden", !calendar);
  document.getElementById("cmp-view-list").classList.toggle("on", !calendar);
  document.getElementById("cmp-view-cal").classList.toggle("on", calendar);
}

// ---- post detail (metrics / boost / actions) ----
async function openPostDetail(postId) {
  const panel = document.getElementById("cmp-post-detail");
  const metricsEl = document.getElementById("cmp-detail-metrics");
  const actionsEl = document.getElementById("cmp-detail-actions");
  panel.classList.remove("hidden");
  document.getElementById("cmp-detail-title").textContent = "Loading…";
  metricsEl.innerHTML = ""; actionsEl.innerHTML = "";
  const p = await api(`/api/posts/${postId}`);
  document.getElementById("cmp-detail-title").textContent =
    `${p.platform} · ${POST_STATUS[p.status] || p.status}`;

  const m = p.latest_metrics;
  const tiles = [];
  if (m) {
    tiles.push(["Reach", fmtCount(m.reach)], ["Saves", fmtCount(m.saves)],
               ["Likes", fmtCount(m.likes)], ["Comments", fmtCount(m.comments)]);
  }
  if (p.boost_spend != null || p.boost_budget != null) {
    tiles.push(["Boost", `$${Math.round(p.boost_spend || 0)}${p.boost_budget ? ` of $${Math.round(p.boost_budget)}` : ""}`]);
  }
  if (!tiles.length) {
    metricsEl.innerHTML = "<div class='detail-empty'>No metrics yet — use “Refresh metrics”.</div>";
  } else {
    metricsEl.innerHTML = tiles.map(([l, v]) =>
      `<div class="detail-metric"><span class="dm-label">${l}</span><span class="dm-value">${escapeText(String(v))}</span></div>`).join("");
  }
  if (p.error) {
    metricsEl.innerHTML += `<div class="detail-error">${escapeText(p.error)}</div>`;
  }
  if (p.edit_id) {
    const btn = document.createElement("button");
    btn.className = "detail-act";
    btn.textContent = "Open cut in editor";
    btn.onclick = () => postToStudio({ studio: "open", panel: "editor" }) || (window.location = `/?edit=${p.edit_id}`);
    actionsEl.appendChild(btn);
  }
}

function postToStudio(msg) {
  // In the single-doc studio a same-window message reaches the editor panel; standalone it's a harmless no-op.
  try { window.postMessage(msg, "*"); } catch (_) {}
  return false;
}

document.getElementById("cmp-detail-close").addEventListener("click", () => {
  document.getElementById("cmp-post-detail").classList.add("hidden");
});

// Shared create path for both "Post now" and "Schedule".
async function submitPost({ schedule }) {
  if (!drawerCampaign) return;
  const platform = document.getElementById("cmp-post-platform").value;
  const caption = document.getElementById("cmp-post-caption").value.trim();
  const hashtags = document.getElementById("cmp-post-hashtags").value.trim();
  const whenEl = document.getElementById("cmp-post-when");
  const status = document.getElementById("cmp-post-status");
  const nowBtn = document.getElementById("cmp-post-now");
  const schedBtn = document.getElementById("cmp-post-schedule-btn");

  const body = { platform, caption, hashtags };
  if (schedule) {
    if (!whenEl.value) { status.textContent = "Pick a date & time to schedule."; return; }
    // datetime-local is local wall-time; send UTC ISO so it compares against the
    // scheduler's UTC clock.
    body.scheduled_at = new Date(whenEl.value).toISOString();
  } else {
    body.publish_now = true;
  }

  nowBtn.disabled = schedBtn.disabled = true;
  status.textContent = schedule ? "Scheduling…" : "Posting…";
  try {
    const res = await api(`/api/campaigns/${drawerCampaign.id}/posts`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (res.dry_run != null) cmpDryRun = res.dry_run;
    status.textContent = schedule
      ? "Scheduled."
      : (res.dry_run ? "Queued (dry run — nothing sent)" : "Queued");
    document.getElementById("cmp-post-caption").value = "";
    document.getElementById("cmp-post-hashtags").value = "";
    whenEl.value = "";
    setTimeout(loadPosts, schedule ? 0 : 400);  // publish-now needs the job to land
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  } finally {
    nowBtn.disabled = schedBtn.disabled = false;
  }
}

document.getElementById("cmp-post-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitPost({ schedule: false });
});
document.getElementById("cmp-post-schedule-btn").addEventListener("click", () => {
  submitPost({ schedule: true });
});

loadCampaigns();
