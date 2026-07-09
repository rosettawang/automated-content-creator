let allClips = [];

async function loadClips() {
  const res = await fetch("/api/clips");
  allClips = await res.json();
  render(allClips);
}

function render(clips) {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  document.getElementById("count").textContent =
    `${clips.length} clip${clips.length === 1 ? "" : "s"}`;

  clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "card" + (clip.available_locally ? "" : " unavailable");

    const img = document.createElement("img");
    img.className = "thumb";
    img.loading = "lazy";
    img.src = `/api/clips/${clip.id}/thumbnail`;
    img.onerror = () => {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      ph.textContent = "▶"; // ▶ film/play glyph fallback
      img.replaceWith(ph);
    };

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
    const dur = clip.duration_s ? `${clip.duration_s}s` : "?";
    meta.innerHTML = `
      <div class="name">${clip.file_stem}${clip.available_locally ? "" : " · not local"}</div>
      <div class="sub">${clip.category || "—"} · ${dur}</div>
    `;

    card.appendChild(img);
    card.appendChild(infoBtn);
    card.appendChild(meta);
    card.onclick = () => showInfo(clip);
    grid.appendChild(card);
  });
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

function showInfo(clip) {
  document.getElementById("info-title").textContent = clip.file_stem;
  const thumb = document.getElementById("info-thumb");
  thumb.src = `/api/clips/${clip.id}/thumbnail`;
  thumb.style.display = "";
  thumb.onerror = () => { thumb.style.display = "none"; };

  const dl = document.getElementById("info-fields");
  dl.innerHTML = "";
  field(dl, "Filename", clip.file_stem);
  field(dl, "Available", clip.available_locally ? "Local" : "Not downloaded");
  field(dl, "Duration", clip.duration_s ? `${clip.duration_s}s` : null);
  field(dl, "Category", clip.category);
  field(dl, "Status", clip.status);
  field(dl, "Description", clip.description);
  field(dl, "Tags", clip.tags);
  field(dl, "Transcript", clip.transcript);

  document.getElementById("info-overlay").classList.remove("hidden");
}

function hideInfo() {
  document.getElementById("info-overlay").classList.add("hidden");
}

document.getElementById("info-close").addEventListener("click", hideInfo);
document.getElementById("info-overlay").addEventListener("click", (e) => {
  if (e.target.id === "info-overlay") hideInfo();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideInfo();
});

function applyFilter() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  if (!q) return render(allClips);
  render(
    allClips.filter((c) =>
      [c.file_stem, c.description, c.category, c.tags, c.transcript]
        .some((v) => (v || "").toLowerCase().includes(q))
    )
  );
}

document.getElementById("search").addEventListener("input", applyFilter);

// ---- import status helpers ----
function summarize(results) {
  const added = results.filter((r) => r.status === "added_new_clip").length;
  const matched = results.filter((r) => r.status === "matched_existing").length;
  const errors = results.filter((r) => r.status === "error");
  const parts = [];
  if (added) parts.push(`${added} added`);
  if (matched) parts.push(`${matched} matched existing`);
  if (errors.length) parts.push(`${errors.length} failed`);
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

// ---- local file import ----
document.getElementById("import-files-btn").addEventListener("click", () => {
  document.getElementById("file-input").click();
});

document.getElementById("file-input").addEventListener("change", async (e) => {
  const files = e.target.files;
  if (!files.length) return;
  showStatus(`Uploading ${files.length} file(s)…`, false);
  const form = new FormData();
  for (const f of files) form.append("files", f);
  try {
    const res = await fetch("/api/import-files", { method: "POST", body: form });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    showStatus(summarize(body.results), body.results.some((r) => r.status === "error"));
    await refreshAfterImport();
  } catch (err) {
    showStatus(`Error: ${err.message}`, true);
  }
  e.target.value = ""; // allow re-selecting the same file
});

// ---- Drive import ----
function openDrive() { document.getElementById("drive-overlay").classList.remove("hidden"); }
function closeDrive() { document.getElementById("drive-overlay").classList.add("hidden"); }

document.getElementById("import-drive-btn").addEventListener("click", openDrive);
document.getElementById("drive-close").addEventListener("click", closeDrive);
document.getElementById("drive-overlay").addEventListener("click", (e) => {
  if (e.target.id === "drive-overlay") closeDrive();
});

document.getElementById("drive-submit").addEventListener("click", async () => {
  const urls = document.getElementById("drive-links").value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  const resultEl = document.getElementById("drive-result");
  resultEl.textContent = "Importing… (this can take a bit per link)";
  try {
    const res = await fetch("/api/drive-import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    resultEl.textContent = summarize(body.results);
    document.getElementById("drive-links").value = "";
    await refreshAfterImport();
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }
});

loadClips();
