// Map / heatmap view for the clip library.
// Depends on Leaflet + Leaflet.heat (loaded before this file) and on
// library.js for `allClips` and `showInfo` (both global).

let _map = null;
let _heat = null;
let _geoClips = [];
const SELECT_RADIUS_PX = 60; // click tolerance for "clips in this area"
const VIEW_KEY = "clipLibrary.mapView"; // localStorage: last viewed map area

function saveView() {
  if (!_map) return;
  const c = _map.getCenter();
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify({
      lat: c.lat, lng: c.lng, zoom: _map.getZoom(),
    }));
  } catch (e) { /* storage disabled — no-op */ }
}

function loadSavedView() {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (typeof v.lat === "number" && typeof v.lng === "number" && typeof v.zoom === "number") {
      return v;
    }
  } catch (e) { /* corrupt/unavailable — ignore */ }
  return null;
}

function initMapOnce() {
  if (_map) return;
  _map = L.map("map", { worldCopyJump: true }).setView([20, 0], 2);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(_map);

  _map.on("click", (e) => selectClipsNear(e.containerPoint));
  // Remember wherever the user leaves the map so we can restore it next time.
  _map.on("moveend", saveView);
}

async function loadGeo() {
  // The map container may have just become visible; make sure Leaflet knows its
  // real size before we draw the heat canvas (else getImageData sees width 0).
  _map.invalidateSize();

  const res = await fetch("/api/clips/geo");
  _geoClips = await res.json();

  const empty = document.getElementById("map-empty");
  if (!_geoClips.length) {
    empty.classList.remove("hidden");
    if (_heat) { _map.removeLayer(_heat); _heat = null; }
    return;
  }
  empty.classList.add("hidden");

  const points = _geoClips.map((c) => [c.lat, c.lon, 1]);
  if (_heat) _map.removeLayer(_heat);
  _heat = L.heatLayer(points, { radius: 28, blur: 18, maxZoom: 12 }).addTo(_map);

  frameView();
}

// Decide the initial framing: restore the last-viewed area if it still shows some
// footage, otherwise fit to all points (first visit, or the saved area no longer
// has any clips because the library moved on).
function frameView() {
  const dataBounds = L.latLngBounds(_geoClips.map((c) => [c.lat, c.lon]));
  const saved = loadSavedView();
  if (saved) {
    // Silently apply the saved view, then check whether any point is on-screen.
    _map.setView([saved.lat, saved.lng], saved.zoom, { animate: false });
    const visible = _geoClips.some((c) => _map.getBounds().contains([c.lat, c.lon]));
    if (visible) return;
  }
  // Fall back to framing everything.
  _map.fitBounds(dataBounds.pad(0.2), { maxZoom: 14 });
}

function selectClipsNear(clickPoint) {
  if (!_geoClips.length) return;
  const hits = _geoClips.filter((c) => {
    const p = _map.latLngToContainerPoint([c.lat, c.lon]);
    return clickPoint.distanceTo(p) <= SELECT_RADIUS_PX;
  });
  renderMapPanel(hits);
}

function renderMapPanel(clips) {
  const panel = document.getElementById("map-panel");
  const grid = document.getElementById("map-panel-grid");
  const title = document.getElementById("map-panel-title");

  if (!clips.length) {
    panel.classList.add("hidden");
    return;
  }
  title.textContent = `${clips.length} clip${clips.length === 1 ? "" : "s"} here`;
  grid.innerHTML = "";
  clips.forEach((c) => {
    const cell = document.createElement("div");
    cell.className = "map-cell";

    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/api/clips/${c.id}/thumbnail`;
    img.onerror = () => { img.style.display = "none"; };

    const name = document.createElement("div");
    name.className = "map-cell-name";
    name.textContent = c.file_stem;

    cell.appendChild(img);
    cell.appendChild(name);
    cell.onclick = () => {
      // Prefer the full clip record from the grid; fall back to the geo record.
      const full = (typeof allClips !== "undefined" && allClips.find((x) => x.id === c.id)) || c;
      showInfo(full);
    };
    grid.appendChild(cell);
  });
  panel.classList.remove("hidden");
}

// ---- view toggle ----
// Three views share the toggle: grid, map, things. Each show* hides the others
// and lights up its own button. (showThings lives in things.js.)
function showGrid() {
  document.getElementById("grid").classList.remove("hidden");
  document.getElementById("map-view").classList.add("hidden");
  document.getElementById("things-view").classList.add("hidden");
  document.getElementById("view-grid").classList.add("active");
  document.getElementById("view-map").classList.remove("active");
  document.getElementById("view-things").classList.remove("active");
}

function showMap() {
  document.getElementById("grid").classList.add("hidden");
  document.getElementById("things-view").classList.add("hidden");
  document.getElementById("map-view").classList.remove("hidden");
  document.getElementById("view-grid").classList.remove("active");
  document.getElementById("view-things").classList.remove("active");
  document.getElementById("view-map").classList.add("active");
  initMapOnce();
  // Wait (via setTimeout, which — unlike requestAnimationFrame — still fires in
  // background/inactive tabs) until the just-shown container has a real size, so
  // the heat canvas doesn't draw at width 0.
  whenSized(loadGeo);
}

function whenSized(cb, tries = 30) {
  _map.invalidateSize();
  const dom = document.getElementById("map");
  const s = _map.getSize();
  // Keep invalidating until Leaflet's cached size matches the element's real
  // laid-out size — otherwise tiles/heat render for a stale, smaller viewport.
  const settled = s.x === dom.clientWidth && s.y === dom.clientHeight && s.y > 0;
  if (settled || tries <= 0) return cb();
  setTimeout(() => whenSized(cb, tries - 1), 40);
}

document.getElementById("view-grid").addEventListener("click", showGrid);
document.getElementById("view-map").addEventListener("click", showMap);
document.getElementById("map-panel-close").addEventListener("click", () => {
  document.getElementById("map-panel").classList.add("hidden");
});
