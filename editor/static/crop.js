// Reframe crop overlay for the program monitor.
// Depends on app.js globals: programVideo, currentEditId, selectedItemId, timeline.
// Lets the director drag/zoom a crop box (aspect-locked to the edit's target frame)
// or ask the AI to propose one. Crop is stored per timeline item as fractions of
// the source frame; the exact aspect is enforced at export.

const cropOverlay = document.getElementById("crop-overlay");
const cropBox = document.getElementById("crop-box");
const cropControls = document.getElementById("crop-controls");
const cropZoom = document.getElementById("crop-zoom");
const cropStatus = document.getElementById("crop-status");
const monitor = document.getElementById("program-monitor");

// Output pixel width per target aspect (mirrors the server's ASPECT_DIMS widths).
const ASPECT_OUT_W = { "9:16": 1080, "4:5": 1080, "1:1": 1080, "16:9": 1920 };
const MAX_UPSCALE = 1.5; // allow at most 1.5x upscale before the zoom is capped

function targetAR() {
  const v = (document.getElementById("aspect-select") || {}).value || "source";
  if (v === "source" || !v.includes(":")) return null;
  const [w, h] = v.split(":").map(Number);
  return w / h;
}

// Largest zoom that keeps the cropped region from being upscaled more than
// MAX_UPSCALE when covered to the target output width. Uses the clip's measured
// resolution; falls back to the slider max (3) when resolution is unknown.
function maxSafeZoom(W, H, ar) {
  const item = selectedItem();
  const v = document.getElementById("aspect-select").value;
  const outW = ASPECT_OUT_W[v];
  const srcW = item && item.clip_width;
  if (!outW || !srcW) return 3;
  const mb = maxBox(W, H, ar);
  const maxBoxFrac = mb.w / W;                 // widest crop (zoom=1) as frame fraction
  const z = maxBoxFrac * srcW * MAX_UPSCALE / outW;
  return Math.max(1, Math.min(3, z));
}

function selectedItem() {
  if (typeof timeline === "undefined" || selectedItemId == null) return null;
  return timeline.find((i) => i.id === selectedItemId) || null;
}

// Largest target-aspect rectangle (in px) that fits inside a WxH box.
function maxBox(W, H, ar) {
  let w = W, h = W / ar;
  if (h > H) { h = H; w = H * ar; }
  return { w, h };
}

// Position the overlay exactly over the video's displayed rect.
function syncOverlayRect() {
  const vr = programVideo.getBoundingClientRect();
  const mr = monitor.getBoundingClientRect();
  if (vr.width < 2 || vr.height < 2) return null;
  cropOverlay.style.left = `${vr.left - mr.left}px`;
  cropOverlay.style.top = `${vr.top - mr.top}px`;
  cropOverlay.style.width = `${vr.width}px`;
  cropOverlay.style.height = `${vr.height}px`;
  return { w: vr.width, h: vr.height };
}

let _drag = null;

function refreshCropOverlay() {
  const ar = targetAR();
  const item = selectedItem();
  const active = ar && item && currentEditId;
  cropOverlay.classList.toggle("hidden", !active);
  cropControls.classList.toggle("hidden", !active);
  if (!active) return;

  const dims = syncOverlayRect();
  if (!dims) return;
  const { w: W, h: H } = dims;

  // Draw the box: use the stored crop if present, else the auto center-crop region.
  let cx = item.crop_x, cy = item.crop_y, cw = item.crop_w, ch = item.crop_h;
  if ([cx, cy, cw, ch].some((v) => v == null)) {
    const mb = maxBox(W, H, ar);
    cw = mb.w / W; ch = mb.h / H;
    cx = (1 - cw) / 2; cy = (1 - ch) / 2;
  }
  placeBox(cx * W, cy * H, cw * W, ch * H);
  // reflect zoom slider from current box size vs the max box
  const mb = maxBox(W, H, ar);
  // Resolution-aware zoom cap: don't let a punch-in upscale beyond MAX_UPSCALE.
  const safeMax = maxSafeZoom(W, H, ar);
  cropZoom.max = safeMax.toFixed(2);
  cropZoom.value = Math.min(safeMax, Math.max(1, mb.w / (cw * W))).toFixed(2);
  // Reflect whether a Ken Burns end rect is set (dropdown shows on/off; exact
  // preset isn't reverse-derived — a set end simply shows as not "None").
  const motionSel = document.getElementById("crop-motion");
  const hasKB = [item.kb_x, item.kb_y, item.kb_w, item.kb_h].every((v) => v != null);
  if (!hasKB) motionSel.value = "none";
  cropStatus.textContent = item.crop_x == null ? "auto" : (hasKB ? "motion" : "custom");
}

// Compute a Ken Burns END rect from a START rect for a motion preset.
function motionEnd(mode, s) {
  const clamp = (x, y, w, h) => {
    w = Math.max(0.05, Math.min(1, w)); h = Math.max(0.05, Math.min(1, h));
    x = Math.max(0, Math.min(1 - w, x)); y = Math.max(0, Math.min(1 - h, y));
    return { x, y, w, h };
  };
  const cx = s.x + s.w / 2, cy = s.y + s.h / 2;
  if (mode === "in") { const k = 0.78; return clamp(cx - s.w * k / 2, cy - s.h * k / 2, s.w * k, s.h * k); }
  if (mode === "out") { const k = 1.28; return clamp(cx - s.w * k / 2, cy - s.h * k / 2, s.w * k, s.h * k); }
  if (mode === "right") return clamp(s.x + 0.25, s.y, s.w, s.h);
  if (mode === "left") return clamp(s.x - 0.25, s.y, s.w, s.h);
  return null; // none
}

async function applyMotion(mode) {
  const item = selectedItem();
  const dims = syncOverlayRect();
  if (!item || !dims) return;
  if (mode === "none") {
    Object.assign(item, { kb_x: null, kb_y: null, kb_w: null, kb_h: null });
    await api(`/api/edits/${currentEditId}/items/${item.id}`, {
      method: "PUT", body: JSON.stringify({ kb_x: null, kb_y: null, kb_w: null, kb_h: null }),
    });
    return;
  }
  // The current box (custom or auto) is the START; materialize it as crop_* so
  // the export has an explicit start, then derive the end from the preset.
  const b = boxRectPx();
  const start = {
    x: +(b.left / dims.w).toFixed(4), y: +(b.top / dims.h).toFixed(4),
    w: +(b.w / dims.w).toFixed(4), h: +(b.h / dims.h).toFixed(4),
  };
  const end = motionEnd(mode, start);
  Object.assign(item, {
    crop_x: start.x, crop_y: start.y, crop_w: start.w, crop_h: start.h,
    kb_x: end.x, kb_y: end.y, kb_w: end.w, kb_h: end.h,
  });
  await api(`/api/edits/${currentEditId}/items/${item.id}`, {
    method: "PUT",
    body: JSON.stringify({
      crop_x: start.x, crop_y: start.y, crop_w: start.w, crop_h: start.h,
      kb_x: end.x, kb_y: end.y, kb_w: end.w, kb_h: end.h,
    }),
  });
  cropStatus.textContent = `motion: ${mode}`;
}

function placeBox(left, top, w, h) {
  cropBox.style.left = `${left}px`;
  cropBox.style.top = `${top}px`;
  cropBox.style.width = `${w}px`;
  cropBox.style.height = `${h}px`;
}

function boxRectPx() {
  return {
    left: parseFloat(cropBox.style.left) || 0,
    top: parseFloat(cropBox.style.top) || 0,
    w: parseFloat(cropBox.style.width) || 0,
    h: parseFloat(cropBox.style.height) || 0,
  };
}

async function saveCrop() {
  const item = selectedItem();
  if (!item) return;
  const dims = syncOverlayRect();
  if (!dims) return;
  const b = boxRectPx();
  const crop = {
    crop_x: +(b.left / dims.w).toFixed(4),
    crop_y: +(b.top / dims.h).toFixed(4),
    crop_w: +(b.w / dims.w).toFixed(4),
    crop_h: +(b.h / dims.h).toFixed(4),
  };
  Object.assign(item, crop); // keep local copy in sync
  cropStatus.textContent = "custom";
  await api(`/api/edits/${currentEditId}/items/${item.id}`, {
    method: "PUT", body: JSON.stringify(crop),
  });
}

// ---- drag to move ----
cropBox.addEventListener("mousedown", (e) => {
  e.preventDefault();
  const b = boxRectPx();
  _drag = { sx: e.clientX, sy: e.clientY, left: b.left, top: b.top, w: b.w, h: b.h };
});
window.addEventListener("mousemove", (e) => {
  if (!_drag) return;
  const dims = syncOverlayRect();
  if (!dims) return;
  let left = _drag.left + (e.clientX - _drag.sx);
  let top = _drag.top + (e.clientY - _drag.sy);
  left = Math.max(0, Math.min(dims.w - _drag.w, left));
  top = Math.max(0, Math.min(dims.h - _drag.h, top));
  placeBox(left, top, _drag.w, _drag.h);
});
window.addEventListener("mouseup", () => {
  if (_drag) { _drag = null; saveCrop(); }
});

// ---- zoom (resize the aspect-locked box around its center) ----
cropZoom.addEventListener("input", () => {
  const ar = targetAR();
  const item = selectedItem();
  if (!ar || !item) return;
  const dims = syncOverlayRect();
  if (!dims) return;
  const mb = maxBox(dims.w, dims.h, ar);
  const zoom = parseFloat(cropZoom.value);
  const w = mb.w / zoom, h = mb.h / zoom;
  const b = boxRectPx();
  const cxPx = b.left + b.w / 2, cyPx = b.top + b.h / 2;
  let left = Math.max(0, Math.min(dims.w - w, cxPx - w / 2));
  let top = Math.max(0, Math.min(dims.h - h, cyPx - h / 2));
  placeBox(left, top, w, h);
});
cropZoom.addEventListener("change", saveCrop);

// ---- AI suggest ----
document.getElementById("crop-suggest").addEventListener("click", async () => {
  const item = selectedItem();
  if (!item) return;
  cropStatus.textContent = "asking the director…";
  try {
    const r = await api(`/api/edits/${currentEditId}/items/${item.id}/suggest-crop`, { method: "POST" });
    if (r.error) { cropStatus.textContent = r.error; return; }
    Object.assign(item, { crop_x: r.crop_x, crop_y: r.crop_y, crop_w: r.crop_w, crop_h: r.crop_h });
    refreshCropOverlay();
    cropStatus.textContent = r.reason ? `✨ ${r.reason}` : "✨ suggested";
  } catch (e) {
    cropStatus.textContent = "suggestion failed";
  }
});

// ---- reset to auto ----
document.getElementById("crop-reset").addEventListener("click", async () => {
  const item = selectedItem();
  if (!item) return;
  Object.assign(item, { crop_x: null, crop_y: null, crop_w: null, crop_h: null });
  await api(`/api/edits/${currentEditId}/items/${item.id}`, {
    method: "PUT",
    body: JSON.stringify({ crop_x: null, crop_y: null, crop_w: null, crop_h: null }),
  });
  refreshCropOverlay();
});

// ---- motion (Ken Burns) preset ----
document.getElementById("crop-motion").addEventListener("change", (e) => applyMotion(e.target.value));

programVideo.addEventListener("loadeddata", refreshCropOverlay);
programVideo.addEventListener("resize", refreshCropOverlay);
window.addEventListener("resize", () => refreshCropOverlay());
