#!/usr/bin/env node
/**
 * plan-specs — derive the execution order instead of remembering it.
 *
 * WHY THIS EXISTS. A hand-written "execution order" section goes stale quietly:
 * it means reading a dozen HTML files before any work can start, every single
 * time, and re-deriving the order from prose by eye. So the specs declare what
 * they touch, and this derives the rest. Same move `prune-specs.mjs` makes for
 * the live listing and the decisions section: state it once, declaratively, and
 * generate the view.
 *
 * WHAT IT CATCHES, in the order the mistakes actually cost:
 *
 *   1. A spec that DELETES a file another spec EDITS. This is the expensive one:
 *      whoever edits the doomed file must go first, or the edit is wasted. It is
 *      an ERROR here.
 *   2. Two open specs editing the same page. Not fatal, but they must not be
 *      worked in parallel sessions, and the second inherits the first's changes.
 *   3. A spec whose verify command ALREADY PASSES. That spec is done and should
 *      be pruned, not worked. `CLAUDE.md` bans stating machine-checkable state
 *      from memory; `spec-verify` turns that rule into something a script checks.
 *
 * USAGE
 *   node scripts/plan-specs.mjs            # print the plan
 *   node scripts/plan-specs.mjs --verify   # also run each spec-verify command
 *   node scripts/plan-specs.mjs --write    # regenerate the order into the index
 *
 * THIS SCRIPT NEVER WRITES `specs/index.html` OUTSIDE ITS OWN MARKERS.
 *
 * The index is hand-edited constantly — Login-gated queue items, spec
 * descriptions, notes — and often while a run is in progress. Any script that
 * rewrites a hand-owned region races every editor save. So the machine gets its
 * own region, between GENERATED:EXECUTION-ORDER markers, and everything it
 * writes goes through `safeWrite` below, which refuses rather than clobbers.
 *
 * META IT READS (all optional; a spec with none still appears, just unordered):
 *   spec-touches   space/comma-separated paths this spec edits
 *   spec-deletes   paths this spec removes
 *   spec-needs     slugs of specs that must land first
 *   spec-verify    a shell command that exits 0 when the spec is genuinely done
 *   spec-account   "yes" when it is gated on a Login-gated queue item
 *   spec-decision  owned by prune-specs.mjs; read here, never written here.
 */

import { readdir, readFile, writeFile, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SPECS_DIR = join(ROOT, 'specs');
const INDEX = 'index.html';
const PLAN = 'PLAN.html';

/**
 * Read-modify-write that refuses to clobber a file edited underneath it.
 * Aborting costs a re-run; clobbering costs someone's unsaved work.
 */
async function safeWrite(path, transform) {
  const before = await stat(path).catch(() => null);
  const original = before ? await readFile(path, 'utf8') : '';
  const next = await transform(original);
  if (next == null || next === original) return 'unchanged';

  const after = await stat(path).catch(() => null);
  if (before && after && after.mtimeMs !== before.mtimeMs) {
    return 'changed-underneath';
  }
  await writeFile(path, next);
  return 'written';
}

const args = new Set(process.argv.slice(2));
const DO_VERIFY = args.has('--verify');
const DO_WRITE = args.has('--write');

/* ------------------------------------------------------------- parsing -- */

const meta = (html, name) => {
  const m = html.match(
    new RegExp(`<meta\\s+name=["']${name}["']\\s+content=["']([^"]*)["']`, 'i'),
  );
  return m ? m[1].trim() : '';
};

/** Paths are written space- or comma-separated; blank entries dropped. */
const paths = (raw) => raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);

const titleOf = (html, fallback) => {
  const m = html.match(/<title>([\s\S]*?)<\/title>/i);
  return m ? m[1].replace(/^Spec\s*[:—-]\s*/i, '').trim() : fallback;
};

/**
 * Files nearly every spec appends to. An overlap here is not a conflict worth
 * sequencing around — it is just how the repo works — so they are reported at a
 * lower volume than a genuine two-specs-one-file collision.
 */
const SHARED_APPEND_ONLY = new Set([
  'README.md',
  'ROADMAP.md',
  'CLAUDE.md',
  'run_tests.sh',
  'editor/core.py',
]);

/* -------------------------------------------------------------- loading -- */

const files = (await readdir(SPECS_DIR)).filter(
  (f) => f.endsWith('.html') && f !== INDEX,
);

const specs = [];
for (const file of files) {
  const html = await readFile(join(SPECS_DIR, file), 'utf8');
  const status = (meta(html, 'spec-status') || 'open').toLowerCase();
  if (status === 'canonical' || status === 'done') continue;

  specs.push({
    slug: file.replace(/\.html$/, ''),
    file,
    status,
    title: titleOf(html, file),
    touches: paths(meta(html, 'spec-touches')),
    deletes: paths(meta(html, 'spec-deletes')),
    needs: paths(meta(html, 'spec-needs')).map((s) => s.replace(/\.html$/, '')),
    verify: meta(html, 'spec-verify'),
    account: /^(yes|true|1)$/i.test(meta(html, 'spec-account')),
    blocker: meta(html, 'spec-blocker'),
    decisions: [
      ...html.matchAll(/<meta\s+name=["']spec-decision["']\s+content=["']([^"]*)["']/gi),
    ].map((m) => m[1].replace(/\s+/g, ' ').trim()).filter(Boolean),
  });
}

const bySlug = new Map(specs.map((s) => [s.slug, s]));
const errors = [];
const warnings = [];

/* ------------------------------------------------------------ conflicts -- */

// 1. delete-vs-touch. The expensive one: a spec editing a file another deletes.
const deleteEdges = [];
for (const a of specs) {
  for (const path of a.deletes) {
    for (const b of specs) {
      if (b.slug === a.slug) continue;
      if (!b.touches.includes(path)) continue;
      errors.push(
        `${b.slug} edits ${path}, which ${a.slug} DELETES. ` +
          `Work ${b.slug} first, or fold it in — do not do both blind.`,
      );
      deleteEdges.push([b.slug, a.slug]);
    }
  }
}

// 2. same file, two open specs.
const owners = new Map();
for (const s of specs) {
  for (const p of s.touches) {
    if (!owners.has(p)) owners.set(p, []);
    owners.get(p).push(s.slug);
  }
}
const contended = [];
for (const [path, slugs] of owners) {
  if (slugs.length < 2) continue;
  const shared = SHARED_APPEND_ONLY.has(path);
  contended.push({ path, slugs, shared });
  if (!shared) {
    warnings.push(
      `${path} is edited by ${slugs.length} specs (${slugs.join(', ')}). ` +
        `Sequence them; never work them in parallel sessions.`,
    );
  }
}

// 3. spec-needs pointing at nothing.
for (const s of specs) {
  for (const n of s.needs) {
    if (!bySlug.has(n)) {
      warnings.push(
        `${s.slug} declares spec-needs "${n}", which is not a live spec ` +
          `(already shipped and pruned, or a typo).`,
      );
    }
  }
}

/* ------------------------------------------------------- the ordering -- */

// Kahn's algorithm over spec-needs plus the delete edges. Cycles are reported
// rather than silently broken.
const edges = new Map(specs.map((s) => [s.slug, new Set()])); // slug -> must follow
for (const s of specs) {
  for (const n of s.needs) if (bySlug.has(n)) edges.get(s.slug).add(n);
}
for (const [first, then] of deleteEdges) {
  if (edges.has(then)) edges.get(then).add(first);
}

/**
 * Tie-break among specs that are equally ready. Cheap and unblocked first:
 * a blocked spec cannot be finished, and an account-gated one stops for a human,
 * so both are worse uses of a run than something that can go end to end.
 */
const rank = (s) =>
  (s.status === 'blocked' ? 100 : 0) +
  (s.decisions.length ? 80 : 0) +
  (s.account ? 50 : 0) +
  (s.blocker ? 10 : 0) -
  [...edges.values()].filter((deps) => deps.has(s.slug)).length * 5;

const order = [];
const remaining = new Map(specs.map((s) => [s.slug, new Set(edges.get(s.slug))]));
while (remaining.size) {
  const ready = [...remaining.entries()]
    .filter(([, deps]) => [...deps].every((d) => !remaining.has(d)))
    .map(([slug]) => bySlug.get(slug));

  if (!ready.length) {
    errors.push(
      `Dependency cycle among: ${[...remaining.keys()].join(', ')}. ` +
        `Two specs each expect the other first — split one of them.`,
    );
    for (const slug of remaining.keys()) order.push(bySlug.get(slug));
    break;
  }

  ready.sort((a, b) => rank(a) - rank(b) || a.slug.localeCompare(b.slug));
  const next = ready[0];
  order.push(next);
  remaining.delete(next.slug);
}

/* --------------------------------------------------------- verify pass -- */

const verdicts = new Map();
if (DO_VERIFY) {
  for (const s of order) {
    if (!s.verify) continue;
    try {
      execSync(s.verify, { cwd: ROOT, stdio: 'pipe', timeout: 120_000 });
      verdicts.set(s.slug, 'passes');
      warnings.push(
        `${s.slug}: its spec-verify command ALREADY PASSES. ` +
          `Confirm it is genuinely done and prune it rather than working it.`,
      );
    } catch {
      verdicts.set(s.slug, 'fails');
    }
  }
}

/* -------------------------------------------------------------- output -- */

const missing = specs.filter((s) => !s.touches.length && !s.deletes.length);

console.log(`\nspecs/ — ${specs.length} live, derived order:\n`);
order.forEach((s, i) => {
  const tags = [
    s.status === 'blocked' ? 'BLOCKED' : '',
    s.account ? 'account-gated' : '',
    verdicts.get(s.slug) === 'passes' ? 'VERIFY ALREADY PASSES' : '',
    s.decisions.length ? `${s.decisions.length} decision${s.decisions.length > 1 ? 's' : ''} waiting` : '',
    s.needs.filter((n) => bySlug.has(n)).length
      ? `after ${s.needs.filter((n) => bySlug.has(n)).join(' + ')}`
      : '',
  ].filter(Boolean);
  console.log(
    `  ${String(i + 1).padStart(2)}. ${s.slug}${tags.length ? `  [${tags.join(' · ')}]` : ''}`,
  );
});

if (contended.length) {
  console.log(`\ncontended files — sequence, do not parallelise:`);
  for (const c of contended.filter((c) => !c.shared)) {
    console.log(`  ${c.path}\n      ${c.slugs.join(', ')}`);
  }
  const sharedCount = contended.filter((c) => c.shared).length;
  if (sharedCount) {
    console.log(`  (${sharedCount} append-only shared files omitted: README, ROADMAP, core.py, etc.)`);
  }
}

if (missing.length) {
  console.log(
    `\nno spec-touches declared (invisible to conflict detection):\n  ${missing
      .map((s) => s.slug)
      .join(', ')}`,
  );
}

for (const w of warnings) console.log(`\n!  ${w}`);
for (const e of errors) console.log(`\nX  ${e}`);

/* --------------------------------------------------------------- write -- */

if (DO_WRITE) {
  const esc = (s) =>
    String(s)
      .replace(/&(?![a-zA-Z]+;|#\d+;)/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

  const items = order
    .map((s) => {
      const notes = [];
      if (s.status === 'blocked') notes.push('<strong>blocked</strong>');
      if (s.decisions.length) {
        notes.push(
          `<strong>${s.decisions.length} decision${s.decisions.length > 1 ? 's' : ''} waiting on the owner</strong>`,
        );
      }
      if (s.account) notes.push('account-gated, needs a Login-gated queue item');
      const deps = s.needs.filter((n) => bySlug.has(n));
      if (deps.length) notes.push(`after ${deps.map(esc).join(' + ')}`);
      const clash = contended
        .filter((c) => !c.shared && c.slugs.includes(s.slug))
        .map((c) => c.path);
      if (clash.length) {
        notes.push(`shares ${clash.map((p) => `<code>${esc(p)}</code>`).join(', ')}`);
      }
      return (
        `  <li><a href="${esc(s.file)}">${esc(s.slug)}</a> — ${esc(s.title)}` +
        (notes.length ? `<br /><span class="meta" style="margin:0">${notes.join(' · ')}</span>` : '') +
        `</li>`
      );
    })
    .join('\n');

  const problems = [...errors.map((e) => ['X', e]), ...warnings.map((w) => ['!', w])]
    .map(([k, t]) => `  <li>${k === 'X' ? '<strong>' : ''}${esc(t)}${k === 'X' ? '</strong>' : ''}</li>`)
    .join('\n');

  const block =
    `<p class="meta">Generated by <code>./specs.sh plan --write</code>. Do not hand-edit: ` +
    `it is derived from each spec's <code>spec-needs</code>, <code>spec-touches</code>, ` +
    `<code>spec-deletes</code> and <code>spec-decision</code>. To change the order, change a ` +
    `spec's meta — that is the point, so it cannot go stale.</p>\n` +
    `<ol class="spec-order">\n${items}\n</ol>` +
    (problems
      ? `\n<div class="callout warn">\n<h4>Conflicts to resolve first</h4>\n<ul>\n${problems}\n</ul>\n</div>`
      : '');

  const indexPath = join(SPECS_DIR, INDEX);
  const markers =
    /(<!--\s*GENERATED:EXECUTION-ORDER:START\s*-->)[\s\S]*?(<!--\s*GENERATED:EXECUTION-ORDER:END\s*-->)/;
  const indexHtml = await readFile(indexPath, 'utf8').catch(() => '');

  if (markers.test(indexHtml)) {
    const result = await safeWrite(indexPath, (current) =>
      current.replace(markers, (_m, open, close) => `${open}\n${block}\n${close}`),
    );
    if (result === 'changed-underneath') {
      console.log(
        `\n!  specs/${INDEX} was saved while this ran — nothing written, your edit is intact. Re-run.`,
      );
    } else if (result === 'written') {
      console.log(`\n   wrote the execution order into specs/${INDEX}`);
    } else {
      console.log(`\n   specs/${INDEX} execution order already current`);
    }

    if (existsSync(join(SPECS_DIR, PLAN))) {
      console.log(
        `\n!  specs/${PLAN} is now redundant — the order lives in the index. Delete it:\n` +
          `     git rm -f specs/${PLAN}`,
      );
    }
  } else {
    console.log(
      `\n!  No GENERATED:EXECUTION-ORDER markers in specs/${INDEX}.\n` +
        `   Add these two lines where the order should appear, then re-run:\n` +
        `     <!-- GENERATED:EXECUTION-ORDER:START -->\n` +
        `     <!-- GENERATED:EXECUTION-ORDER:END -->\n` +
        `   Falling back to specs/${PLAN} until then.`,
    );
    const result = await safeWrite(
      join(SPECS_DIR, PLAN),
      () => `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="spec-status" content="canonical" />
<title>Content creator — Derived execution order (fallback)</title>
<link rel="stylesheet" href="spec.css" />
</head>
<body>
<div class="internal">Internal planning document — <b>not part of the app</b></div>
<div class="wrap">
<p><a href="${INDEX}">← Specs index</a></p>
<h1>Derived execution order</h1>
<div class="callout warn">
  <h4>Temporary. This belongs in the index.</h4>
  <p>
    Generated here only because <a href="${INDEX}">${INDEX}</a> has no
    <code>GENERATED:EXECUTION-ORDER</code> markers yet. Add them and this file becomes redundant.
  </p>
</div>
${block}
</div>
</body>
</html>
`,
    );
    if (result === 'written') console.log(`   wrote specs/${PLAN}`);
  }
}

console.log('');
if (errors.length) process.exitCode = 1;
