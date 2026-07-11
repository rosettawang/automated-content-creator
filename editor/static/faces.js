// People / face-recognition panel inside the Things view. Depends on globals:
// fmtTime + sleep (library.js) and renderClipCards + showInfo (things.js/library.js).

let _selectedPersonId = null;

async function loadPeople() {
  const res = await fetch("/api/faces/groups");
  const data = await res.json();
  renderPeople(data);
}

function renderPeople(data) {
  const grid = document.getElementById("people-grid");
  grid.innerHTML = "";
  const { people, clusters } = data;
  if (!people.length && !clusters.length) {
    const d = document.createElement("div");
    d.className = "people-empty";
    d.textContent = "No faces yet. Click “Detect faces” to find people across your local clips.";
    grid.appendChild(d);
    return;
  }

  // Named people first.
  people.forEach((p) => {
    const card = document.createElement("div");
    card.className = "person-card named" + (p.id === _selectedPersonId ? " selected" : "");
    card.appendChild(faceImg(p.rep_face));
    const label = document.createElement("div");
    label.className = "person-label";
    label.textContent = p.name;
    const sub = document.createElement("div");
    sub.className = "person-sub";
    sub.textContent = `${p.count} clip${p.count === 1 ? "" : "s"}`;
    const del = document.createElement("button");
    del.className = "del-person";
    del.textContent = "un-name";
    del.title = "Remove this name (faces return to unknown)";
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Remove the name "${p.name}"?`)) return;
      await fetch(`/api/people/${p.id}`, { method: "DELETE" });
      if (_selectedPersonId === p.id) _selectedPersonId = null;
      loadPeople();
    };
    card.appendChild(label);
    card.appendChild(sub);
    card.appendChild(del);
    card.onclick = () => selectPerson(p);
    grid.appendChild(card);
  });

  // Unnamed clusters, offering a "name" action.
  clusters.forEach((c) => {
    const card = document.createElement("div");
    card.className = "person-card";
    card.appendChild(faceImg(c.rep_face));
    const label = document.createElement("div");
    label.className = "person-label";
    label.textContent = "Unknown";
    const sub = document.createElement("div");
    sub.className = "person-sub";
    sub.textContent = `${c.count} face${c.count === 1 ? "" : "s"}`;
    const nameBtn = document.createElement("button");
    nameBtn.className = "name-btn";
    nameBtn.textContent = "name…";
    nameBtn.onclick = async (e) => {
      e.stopPropagation();
      const name = prompt("Who is this?");
      if (!name || !name.trim()) return;
      await fetch("/api/faces/name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cluster_id: c.cluster_id, name: name.trim() }),
      });
      loadPeople();
    };
    card.appendChild(label);
    card.appendChild(sub);
    card.appendChild(nameBtn);
    grid.appendChild(card);
  });
}

function faceImg(faceId) {
  const img = document.createElement("img");
  if (faceId != null) img.src = `/api/faces/${faceId}/thumb`;
  img.alt = "face";
  return img;
}

async function selectPerson(p) {
  _selectedPersonId = p.id;
  renderPeople({ people: [], clusters: [] }); // clear selection state cheaply…
  loadPeople(); // …then reload to reflect it
  const res = await fetch(`/api/people/${p.id}/clips`);
  const clips = await res.json();
  renderClipCards(
    clips.length ? `${clips.length} clip${clips.length === 1 ? "" : "s"} with ${p.name}`
                 : `No clips for ${p.name} yet.`,
    clips,
  );
}

// ---- detect faces (background job with progress) ----
const facesProgress = document.getElementById("faces-progress");
const facesBar = document.getElementById("faces-bar");
const facesBarFill = document.getElementById("faces-bar-fill");
const facesProgressLabel = document.getElementById("faces-progress-label");

document.getElementById("faces-detect-btn").addEventListener("click", async () => {
  const btn = document.getElementById("faces-detect-btn");
  const status = document.getElementById("faces-status");
  if (!confirm("Detect faces across your local clips? This runs on-device and can take a bit.")) return;
  btn.disabled = true;
  status.textContent = "";
  facesProgress.classList.remove("hidden");
  try {
    const res = await fetch("/api/faces/detect", { method: "POST" });
    const started = await res.json();
    if (!res.ok) throw new Error(started.error || res.statusText);
    for (;;) {
      const r = await fetch(`/api/import-jobs/${started.job_id}`);
      const job = await r.json();
      if (!r.ok) throw new Error(job.error || r.statusText);
      if (!job.total) {
        facesBar.classList.add("indeterminate");
        facesProgressLabel.textContent = "Preparing…";
      } else {
        facesBar.classList.remove("indeterminate");
        facesBarFill.style.width = `${Math.round((job.done / job.total) * 100)}%`;
        const bits = [`Detecting · ${job.done} of ${job.total} clips`];
        if (job.current) bits.push(job.current);
        if (job.eta_s != null) bits.push(fmtTime(job.eta_s));
        facesProgressLabel.textContent = bits.join(" · ");
      }
      if (job.finished) {
        const r0 = job.results && job.results[0];
        status.textContent = r0 && r0.status === "detected"
          ? `Found ${r0.faces} face(s) across ${r0.clips} clip(s).`
          : "Detection complete.";
        break;
      }
      await sleep(700);
    }
    facesProgress.classList.add("hidden");
    loadPeople();
  } catch (err) {
    facesProgress.classList.add("hidden");
    status.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});
