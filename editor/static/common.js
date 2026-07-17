// Shared frontend helpers, prepended to EVERY panel bundle (see /bundle/<panel>.js).
// Each panel's files are wrapped in their own IIFE, so these are published on
// `window` (guarded so re-running across bundles is idempotent) and every panel
// reaches them by the normal scope chain — no more per-panel copies of fetch/escape/
// job-polling with subtly different error handling.

// api(path, options) — fetch + JSON with consistent error surfacing. Throws
// Error(body.error) on a non-2xx so callers can `try/catch` one way everywhere.
window.api = window.api || async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || res.statusText);
  }
  return res.status === 204 ? null : res.json();
};

// esc(s) — minimal HTML-escape for interpolating text into innerHTML.
window.esc = window.esc || function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
};

// pollJob(jobId, onProgress?, intervalMs?) — poll a background job to completion.
// Calls onProgress(job) each tick; resolves with job.results; throws on job/HTTP
// error. Replaces the three hand-rolled polling loops.
window.pollJob = window.pollJob || async function pollJob(jobId, onProgress, intervalMs = 700) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  for (;;) {
    const r = await fetch(`/api/import-jobs/${jobId}`);
    const job = await r.json();
    if (!r.ok) throw new Error(job.error || r.statusText);
    if (onProgress) onProgress(job);
    if (job.finished) {
      if (job.error) throw new Error(job.error);
      return job.results;
    }
    await sleep(intervalMs);
  }
};
