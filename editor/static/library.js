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

document.getElementById("search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) return render(allClips);
  render(
    allClips.filter((c) =>
      [c.file_stem, c.description, c.category, c.tags, c.transcript]
        .some((v) => (v || "").toLowerCase().includes(q))
    )
  );
});

loadClips();
