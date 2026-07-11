// On-device motion/action detection (X-CLIP). Reuses the Things progress bar and
// the fmtTime/sleep globals from library.js. Actions are things of kind 'action';
// this pass finds them in video motion and tags clips + stores timestamped events.

document.getElementById("motion-detect-btn").addEventListener("click", async () => {
  const btn = document.getElementById("motion-detect-btn");
  const status = document.getElementById("things-scan-status");
  const progress = document.getElementById("things-progress");
  const bar = document.getElementById("things-bar");
  const fill = document.getElementById("things-bar-fill");
  const label = document.getElementById("things-progress-label");

  const actionThings = (typeof _things !== "undefined" ? _things : [])
    .filter((t) => t.active && t.kind === "action");
  if (!actionThings.length) {
    status.textContent = "Add a thing of kind “action” (e.g. “pouring oil”) first.";
    return;
  }
  if (!confirm(`Scan video clips for ${actionThings.length} action(s) using on-device motion recognition? This runs X-CLIP per clip and can take a while.`)) return;

  btn.disabled = true;
  status.textContent = "";
  progress.classList.remove("hidden");
  try {
    const res = await fetch("/api/motion/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const started = await res.json();
    if (!res.ok) throw new Error(started.error || res.statusText);
    for (;;) {
      const r = await fetch(`/api/import-jobs/${started.job_id}`);
      const job = await r.json();
      if (!r.ok) throw new Error(job.error || r.statusText);
      if (!job.total) {
        bar.classList.add("indeterminate");
        label.textContent = "Preparing…";
      } else {
        bar.classList.remove("indeterminate");
        fill.style.width = `${Math.round((job.done / job.total) * 100)}%`;
        const bits = [`Motion · ${job.done} of ${job.total} clips`];
        if (job.current) bits.push(job.current);
        if (job.eta_s != null) bits.push(fmtTime(job.eta_s));
        label.textContent = bits.join(" · ");
      }
      if (job.finished) {
        const r0 = job.results && job.results[0];
        status.textContent = r0 && r0.status === "detected"
          ? `Found ${r0.action_events} action event(s) across ${r0.clips} clip(s).`
          : (r0 && r0.error) || "Motion detection complete.";
        break;
      }
      await sleep(700);
    }
    progress.classList.add("hidden");
    if (typeof loadThings === "function") loadThings();
  } catch (err) {
    progress.classList.add("hidden");
    status.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});
