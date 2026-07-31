// ===== Cuts view =====
// Browse every assembled timeline ("cut"/edit) — assigned to a campaign or orphaned.
// Open, rename, delete, or assign to a campaign, so generated work products stop
// getting lost behind a bare ?edit=<id> URL.
// Shares the library bundle's IIFE scope (fmtClock, campaignsById, etc.).

function cutsCampaignOptions(selectedId) {
  const opts = ['<option value="">Unassigned</option>'];
  Object.values(campaignsById || {}).forEach((p) => {
    const sel = String(p.id) === String(selectedId) ? " selected" : "";
    opts.push(`<option value="${p.id}"${sel}>${(p.name || "").replace(/[<>&]/g, "")}</option>`);
  });
  return opts.join("");
}

// Platforms to always surface an icon for (a new adapter's platform also appears
// automatically once a cut has a post on it — see below).
const CUT_PLATFORMS = ["instagram"];
const PLATFORM_GLYPH = { instagram: "IG", tiktok: "TT", youtube: "YT", facebook: "FB" };

// Build the platform status-icon row for a cut. Four states per platform:
//   not-posted (gray/dashed) · scheduled (clock) · published (color) · failed (red).
// The row NEVER publishes — it only navigates (composer / post detail / live URL).
function renderPlatformIcons(e) {
  const row = document.createElement("div");
  row.className = "cut-platforms";

  // Latest post per platform (posts arrive id-ascending, so last wins).
  const byPlatform = {};
  (e.posts || []).forEach((p) => { byPlatform[p.platform] = p; });
  const platforms = [...new Set([...CUT_PLATFORMS, ...Object.keys(byPlatform)])];

  platforms.forEach((platform) => {
    const post = byPlatform[platform];
    const btn = document.createElement("button");
    btn.className = "plat-icon";
    btn.textContent = PLATFORM_GLYPH[platform] || platform.slice(0, 2).toUpperCase();

    const status = post ? post.status : "none";
    if (!post || status === "draft" || status === "cancelled") {
      btn.classList.add("plat-none");
      btn.title = `Not posted to ${platform} — click to compose`;
      btn.onclick = () => openComposer(e, platform);
    } else if (status === "scheduled" || status === "claimed") {
      btn.classList.add("plat-scheduled");
      btn.title = post.scheduled_at
        ? `Scheduled for ${new Date(post.scheduled_at).toLocaleString()}`
        : "Scheduled";
      btn.onclick = () => openPostDetailFromCut(post.post_id);
    } else if (status === "failed" || status === "needs_review") {
      btn.classList.add("plat-failed");
      btn.title = `Publish failed on ${platform} — click for details`;
      btn.onclick = () => openPostDetailFromCut(post.post_id);
    } else if (status === "published") {
      btn.classList.add("plat-published");
      if (post.permalink) {
        btn.title = `Live on ${platform} — open post`;
        btn.onclick = () => window.open(post.permalink, "_blank", "noopener");
      } else {
        // Published but no stored permalink (dry-run legacy / adapter didn't return one):
        // still colored, but open the post detail rather than guessing a URL.
        btn.title = `Published to ${platform} (no link stored) — open details`;
        btn.onclick = () => openPostDetailFromCut(post.post_id);
      }
    }
    row.appendChild(btn);
  });
  return row;
}

// Navigate to the campaign hub's composer, prefilled with this cut + platform.
function openComposer(edit, platform) {
  if (window.studioComposeForCut) {
    window.studioComposeForCut({ editId: edit.id, campaignId: edit.campaign_id, platform });
  } else {
    window.location.href = `/campaigns?compose=${edit.id}&platform=${platform}`;
  }
}

function openPostDetailFromCut(postId) {
  if (window.studioOpenPostDetail) window.studioOpenPostDetail(postId);
  else window.location.href = `/campaigns?post=${postId}`;
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
    const campaigns = await (await fetch("/api/campaigns")).json();
    campaignsById = Object.fromEntries(campaigns.map((p) => [String(p.id), p]));
  } catch { /* keep whatever we had */ }

  empty.classList.toggle("hidden", edits.length > 0);

  // Newest first — most people want the cut they just made at the top.
  edits.sort((a, b) => (b.id || 0) - (a.id || 0));

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
    thumb.onclick = () => {
      if (window.studioOpenEdit) window.studioOpenEdit(e.id);
      else window.location.href = `/?edit=${e.id}`;
    };

    const body = document.createElement("div");
    body.className = "cut-body";

    const name = document.createElement("div");
    name.className = "cut-name";
    name.textContent = e.name || `Edit ${e.id}`;
    // Aspect badge: only when reframed away from the source frame (9:16 etc.).
    if (e.aspect && e.aspect !== "source") {
      const badge = document.createElement("span");
      badge.className = "cut-aspect";
      badge.textContent = e.aspect;
      badge.title = `Output frame: ${e.aspect}`;
      name.appendChild(badge);
    }

    const meta = document.createElement("div");
    meta.className = "cut-meta";
    const dur = e.duration_s ? fmtClock(e.duration_s) : "0:00";
    const n = e.item_count || 0;
    meta.textContent = `${dur} · ${n} clip${n === 1 ? "" : "s"}`;

    // Campaign assignment (also fixes orphaned cuts right here).
    const assign = document.createElement("select");
    assign.className = "cut-assign";
    assign.innerHTML = cutsCampaignOptions(e.campaign_id);
    assign.title = "Assign to a campaign";
    assign.onchange = async () => {
      await fetch(`/api/edits/${e.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ campaign_id: assign.value ? parseInt(assign.value, 10) : null }),
      });
      e.campaign_id = assign.value || null;
    };

    const actions = document.createElement("div");
    actions.className = "cut-actions";
    const openBtn = document.createElement("button");
    openBtn.textContent = "Open";
    openBtn.onclick = () => {
      if (window.studioOpenEdit) window.studioOpenEdit(e.id);
      else window.location.href = `/?edit=${e.id}`;
    };
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

    body.append(name, meta, renderPlatformIcons(e), assign, actions);
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
