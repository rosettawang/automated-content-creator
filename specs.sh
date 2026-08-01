#!/usr/bin/env bash
# Spec tooling — prune shipped specs, regenerate specs/index.html, derive the
# execution order. See CLAUDE.md ("Spec tooling") and specs/index.html.
#
# Deliberately a shell script and not a package.json: this repo is a Python app,
# and the scripts are stdlib-only Node with zero dependencies. A root manifest
# would mislabel the project for every tool that sniffs one, and invite an npm
# dependency into the docs tooling. `./specs.sh` sits next to `./run_tests.sh` —
# one convention for "run a repo chore".
set -euo pipefail
cd "$(dirname "$0")"

command -v node >/dev/null 2>&1 || {
  echo "specs.sh: needs node on PATH (docs tooling only — the app itself doesn't)." >&2
  exit 1
}

usage() {
  cat <<'EOF'
usage: ./specs.sh <command> [args]

  check    Dry run: what would be pruned + the derived order. Writes nothing.
  prune    Delete `done` specs, regenerate the index, then redraw the order.
           The one command to run after changing a spec's status.
  plan     Print the order without pruning. Pass --verify to run each
           spec's spec-verify command, or --write to rewrite just the order.

Extra arguments are passed through, e.g.:
  ./specs.sh plan --verify
EOF
}

cmd="${1:-}"
[ $# -gt 0 ] && shift

case "$cmd" in
  check)
    node scripts/prune-specs.mjs --dry-run "$@"
    node scripts/plan-specs.mjs "$@"
    ;;
  prune)
    # Chained on purpose: pruning a spec without redrawing the order leaves the
    # generated order linking to a file that no longer exists.
    node scripts/prune-specs.mjs "$@"
    node scripts/plan-specs.mjs --write
    ;;
  plan)
    node scripts/plan-specs.mjs "$@"
    ;;
  -h | --help | help | '')
    usage
    ;;
  *)
    echo "specs.sh: unknown command '$cmd'" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac
