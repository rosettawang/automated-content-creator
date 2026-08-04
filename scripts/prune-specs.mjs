#!/usr/bin/env node
/**
 * Prune finished internal spec sheets and rewrite the index listing.
 *
 *   node scripts/prune-specs.mjs            # delete specs marked done, update index
 *   node scripts/prune-specs.mjs --dry-run  # show what would happen, change nothing
 *
 * A spec declares its state in its <head>:
 *   <meta name="spec-status" content="open|blocked|done|canonical">
 *
 * - done      → deleted
 * - open      → kept, listed
 * - blocked   → kept, listed
 * - canonical → never touched (index.html)
 * - missing   → kept and reported, so a typo can't cause a silent delete
 *
 * specs/ is internal planning material. It is not shipped in the app build.
 */

import { readdir, readFile, writeFile, unlink } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SPECS_DIR = join(ROOT, 'specs');
const RULES = join(ROOT, 'CLAUDE.md');
const INDEX = 'index.html';
const DRY = process.argv.includes('--dry-run');

const read = (f) => readFile(join(SPECS_DIR, f), 'utf8');

/* --------------------------------------------------- spec-pass mirroring -- *
 * The spec-pass rule is authored ONCE, in CLAUDE.md, because that is the file
 * Claude auto-loads every session. The index shows a copy so the folder
 * documents its own conventions — but a copy anyone can edit is a copy that
 * drifts, so this generates it instead.
 *
 * One authored source means the two files can lag behind each other, but can
 * never contradict each other. --dry-run reports staleness without writing.
 */

const SPEC_PASS_SRC = /<!--\s*SPEC-PASS:START\s*-->([\s\S]*?)<!--\s*SPEC-PASS:END\s*-->/;
const SPEC_PASS_DEST =
  /(<!--\s*GENERATED:SPEC-PASS:START\s*-->)[\s\S]*?(<!--\s*GENERATED:SPEC-PASS:END\s*-->)/;

/** Escape for HTML without double-escaping entities the source already used. */
const escapeText = (s) =>
  s
    .replace(/&(?![a-zA-Z]+;|#\d+;)/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

/**
 * Render the small markdown subset used in that block: paragraphs, one ordered
 * list, `code`, **bold**, *italic*. Deliberately tiny — if the block ever needs
 * more than this, that is a sign it belongs in its own document rather than
 * mirrored into two.
 */
function miniMarkdownToHtml(md) {
  const inline = (t) =>
    escapeText(t)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');

  const out = [];
  for (const block of md.trim().split(/\n{2,}/)) {
    const lines = block.split('\n').map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    if (lines.every((l) => /^\d+\.\s/.test(l))) {
      out.push(
        '<ol>\n' +
          lines.map((l) => `  <li>${inline(l.replace(/^\d+\.\s*/, ''))}</li>`).join('\n') +
          '\n</ol>'
      );
    } else {
      out.push(`<p>${inline(lines.join(' '))}</p>`);
    }
  }
  return out.join('\n');
}

function statusOf(html) {
  const m = html.match(/<meta\s+name=["']spec-status["']\s+content=["']([^"']+)["']/i);
  return m ? m[1].trim().toLowerCase() : null;
}
function titleOf(html, fallback) {
  const m = html.match(/<title>([^<]*)<\/title>/i);
  return m ? m[1].replace(/^Spec\s*[:—-]\s*/i, '').trim() : fallback;
}
function summaryOf(html) {
  const m = html.match(/<p class=["']lede["']>([\s\S]*?)<\/p>/i);
  if (!m) return '';
  return m[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
}
/**
 * Why a spec is blocked, so the index says so without anyone hand-editing it
 * (this list is regenerated, so hand-edits here get clobbered):
 *   <meta name="spec-blocker" content="…">
 */
function blockerOf(html) {
  const m = html.match(/<meta\s+name=["']spec-blocker["']\s+content=["']([^"]*)["']/i);
  return m ? m[1].replace(/\s+/g, ' ').trim() : '';
}
/**
 * Choices that are the owner's rather than a build problem. REPEATABLE — one
 * meta per decision, because one decision per line is what makes them
 * answerable:
 *   <meta name="spec-decision" content="Which model? Cost, not capability. Does not block the build.">
 *
 * State the recommendation and whether it blocks. A decision with no recommended
 * answer is a question, and a question is harder to answer than a proposal.
 */
function decisionsOf(html) {
  return [...html.matchAll(/<meta\s+name=["']spec-decision["']\s+content=["']([^"]*)["']/gi)].map(
    (m) => m[1].replace(/\s+/g, ' ').trim()
  ).filter(Boolean);
}
const escapeHtml = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const files = (await readdir(SPECS_DIR))
  .filter((f) => f.endsWith('.html') && f !== INDEX)
  .sort();

const kept = [];
const removed = [];
const unmarked = [];

for (const file of files) {
  const html = await read(file);
  const status = statusOf(html);
  if (status === 'done') {
    removed.push({ file, title: titleOf(html, file), summary: summaryOf(html) });
    continue;
  }
  if (status === 'canonical') continue;
  if (!status) unmarked.push(file);
  kept.push({
    file,
    status: status ?? 'unmarked',
    title: titleOf(html, file),
    summary: summaryOf(html),
    blocker: blockerOf(html),
    decisions: decisionsOf(html),
  });
}

// --- delete finished specs -------------------------------------------------
for (const { file } of removed) {
  if (!DRY) await unlink(join(SPECS_DIR, file));
}

// --- rewrite the index's "Live specs" list ---------------------------------
// Repeated procedures do not live here. Ones Claude runs end to end are command
// files (.claude/commands/*.md); everything in specs/ is disposable work, so
// this list is a to-do list and nothing else.
const order = { blocked: 0, open: 1, unmarked: 2 };
kept.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || a.title.localeCompare(b.title));

const listHtml = kept.length
  ? kept
      .map(
        ({ file, status, title, summary, blocker }) => `  <li>
    <a href="${file}">${title}</a>
    <span class="tag tag-${status === 'unmarked' ? 'open' : status}">${status}</span>
    <p>${summary}</p>${
      // Label by status: "Blocked:" only when it really is. An open spec can still
      // carry a spec-blocker note (what's left, who owns it), and calling that
      // "Blocked:" made the index read "Blocked: Unblocked…".
      blocker
        ? `\n    <p class="blocker"><strong>${status === 'blocked' ? 'Blocked' : 'Remaining'}:</strong> ${escapeHtml(blocker)}</p>`
        : ''
    }
  </li>`
      )
      .join('\n')
  : '  <li><p>No live specs. Everything is shipped.</p></li>';

const indexPath = join(SPECS_DIR, INDEX);
let index = await readFile(indexPath, 'utf8');
const listRe = /(<ul class="spec-list">)[\s\S]*?(<\/ul>)/;

if (listRe.test(index)) {
  // Replacer FUNCTION, not a template string: spec text can legitimately contain
  // "$1", "$&" etc., and String.replace would treat those as backreferences.
  index = index.replace(listRe, (_m, open, close) => `${open}\n${listHtml}\n${close}`);
} else {
  console.warn(`! Could not find <ul class="spec-list"> in ${INDEX} — live listing not updated.`);
}

// --- rewrite the "Decisions waiting" section --------------------------------
// Generated rather than hand-maintained: a hand-kept list of what is outstanding
// goes stale quietly, and a stale decision list is worse than none — it asks for
// an answer that was already given.
const withDecisions = kept.filter((k) => k.decisions.length);
const decisionCount = withDecisions.reduce((n, k) => n + k.decisions.length, 0);

const decisionsHtml = withDecisions.length
  ? `<p class="meta">${decisionCount} decision${decisionCount === 1 ? '' : 's'} across ` +
    `${withDecisions.length} spec${withDecisions.length === 1 ? '' : 's'}, generated from each spec's ` +
    `<code>spec-decision</code> meta. Answer one and it disappears from here on the next prune.</p>\n` +
    '<ul class="spec-decision-list">\n' +
    withDecisions
      .map(
        ({ file, title, status, decisions }) => `  <li>
    <a href="${file}">${title}</a>${status === 'blocked' ? ' <span class="tag tag-blocked">blocked</span>' : ''}
    <ul>
${decisions.map((d) => `      <li>${escapeHtml(d)}</li>`).join('\n')}
    </ul>
  </li>`
      )
      .join('\n') +
    '\n</ul>'
  : '<p>No decisions outstanding. Everything live is a build problem.</p>';

const DECISIONS_DEST =
  /(<!--\s*GENERATED:DECISIONS:START\s*-->)[\s\S]*?(<!--\s*GENERATED:DECISIONS:END\s*-->)/;
let decisionsNote = null;
if (DECISIONS_DEST.test(index)) {
  index = index.replace(DECISIONS_DEST, (_m, open, close) => `${open}\n${decisionsHtml}\n${close}`);
} else {
  decisionsNote = `! Could not find the GENERATED:DECISIONS markers in ${INDEX} — decisions section not updated.`;
}

// Drift guard: a spec whose blocker prose talks about a decision but which never
// declared one, so it stays invisible in the list above. Plural matters;
// "decided" (past tense — already made) must NOT match.
const DECISION_PROSE =
  /\bdecisions?\b|\bdecide(?:s)?\b|owner's call|\bher call\b|\bhis call\b|\btheir call\b|\bis hers\b|\bis his\b|\bis theirs\b/i;
const undeclared = kept.filter((k) => !k.decisions.length && DECISION_PROSE.test(k.blocker));

// --- mirror the spec-pass rule from CLAUDE.md into the index ---------------
let specPassNote = null;
try {
  const rules = await readFile(RULES, 'utf8');
  const src = rules.match(SPEC_PASS_SRC);
  if (!src) {
    specPassNote = `! CLAUDE.md has no <!-- SPEC-PASS:START --> block — spec-pass section not updated.`;
  } else if (!SPEC_PASS_DEST.test(index)) {
    specPassNote = `! Could not find the GENERATED:SPEC-PASS markers in ${INDEX} — spec-pass section not updated.`;
  } else {
    const rendered = miniMarkdownToHtml(src[1]);
    const current = index.match(SPEC_PASS_DEST)[0];
    const wasStale = !current.includes(rendered);
    index = index.replace(SPEC_PASS_DEST, (_m, open, close) => `${open}\n${rendered}\n${close}`);
    if (wasStale) {
      specPassNote = DRY
        ? `! ${INDEX} spec-pass block is STALE — CLAUDE.md changed. Run \`./specs.sh prune\`.`
        : `  synced   spec-pass block in ${INDEX} from CLAUDE.md`;
    }
  }
} catch (err) {
  specPassNote = `! Could not read CLAUDE.md (${err.code ?? err.message}) — spec-pass section not updated.`;
}

// --- record what was pruned, so finished work leaves a trace ---------------
if (removed.length) {
  const today = new Date().toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  const entries = removed
    .map(
      ({ title, summary }) =>
        `  <li><strong>${title}</strong> <span class="when">shipped ${today}</span>` +
        `${summary ? ` — ${summary}` : ''}</li>`
    )
    .join('\n');
  const doneRe = /(<ul class="spec-done-list">)/;
  if (doneRe.test(index)) {
    index = index.replace(doneRe, (_m, open) => `${open}\n${entries}`);
  } else {
    console.warn(
      `! Could not find <ul class="spec-done-list"> in ${INDEX} — completions not recorded.\n` +
        `  Add one under a "Recently completed" heading.`
    );
  }
}

if (!DRY) await writeFile(indexPath, index);

// --- report ----------------------------------------------------------------
const tag = DRY ? '[dry run] ' : '';
console.log(`${tag}specs/ — ${kept.length} live, ${removed.length} pruned`);
for (const { file, title } of removed) console.log(`${tag}  deleted  ${file}  (${title})`);
for (const { file, status, decisions } of kept) {
  const d = decisions.length ? `  ${decisions.length} decision${decisions.length === 1 ? '' : 's'}` : '';
  console.log(`${tag}  kept     ${file}  [${status}]${d}`);
}
console.log(
  `${tag}  ${decisionCount} decision(s) awaiting the owner, across ${withDecisions.length} spec(s)`
);
if (decisionsNote) console.log(`${tag}${decisionsNote}`);
if (specPassNote) console.log(`${tag}${specPassNote}`);
if (undeclared.length) {
  console.warn(
    `\n! ${undeclared.length} spec(s) mention a decision in spec-blocker but declare no ` +
      `spec-decision meta, so it will not appear in "Decisions waiting":\n` +
      undeclared.map((k) => `    ${k.file}`).join('\n') +
      `\n  Add <meta name="spec-decision" content="…"> per decision, or reword the blocker.`
  );
}
if (unmarked.length) {
  console.warn(
    `\n! ${unmarked.length} spec(s) have no spec-status meta and were kept: ${unmarked.join(', ')}\n` +
      `  Add <meta name="spec-status" content="open"> so the index reads correctly.`
  );
}
if (DRY && removed.length) console.log('\nNothing was deleted. Re-run without --dry-run to apply.');
