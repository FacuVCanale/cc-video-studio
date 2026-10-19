#!/usr/bin/env bash
# Delete temporary bundles left by renders. Each `npx remotion render ...` or `bundle()` without its own directory
# leaves a ~170 MB remotion-webpack-bundle-* in the system temp directory: hundreds of renders fill the disk.
# Project scripts should clean their bundle (see drop_bundle_on_exit.mjs); this sweeps bundles from manual renders.
# It deletes nothing while a render is active, nor anything newer than --older-than.
#
# usage: bash clean_render_tmp.sh [--older-than HOURS] [--dir DIR] [--pattern GLOB]... [--process-regex RE] [--dry-run] [--help]
#   --older-than HOURS   only entries unchanged in the last HOURS (default 6)
#   --dir DIR            temporary directory to sweep (default $TMPDIR or /tmp)
#   --pattern GLOB       directory name to delete; repeatable (default: Remotion bundles and assets)
#   --process-regex RE   delete nothing if pgrep -f finds this (default: Remotion processes)
#   --dry-run            list what would be deleted without deleting it
#   --help               show this usage and exit
set -euo pipefail

usage() {
  cat <<'EOF'
usage: bash clean_render_tmp.sh [--older-than HOURS] [--dir DIR] [--pattern GLOB]... [--process-regex RE] [--dry-run] [--help]
  --older-than HOURS   only entries unchanged in the last HOURS (default 6)
  --dir DIR            temporary directory to sweep (default $TMPDIR or /tmp)
  --pattern GLOB       directory name to delete; repeatable (default: Remotion bundles and assets)
  --process-regex RE   delete nothing if pgrep -f finds this (default: Remotion processes)
  --dry-run            list what would be deleted without deleting it
  --help               show this usage and exit
EOF
}

HOURS=6
DRY=0
DIR="${TMPDIR:-/tmp}"
PATTERNS=()
PROC='remotion (render|still|bundle)|@remotion/renderer|remotion/cli'
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--older-than" ]]; then HOURS="$2"; shift 2
  elif [[ "$1" == "--dir" ]]; then DIR="$2"; shift 2
  elif [[ "$1" == "--pattern" ]]; then PATTERNS+=("$2"); shift 2
  elif [[ "$1" == "--process-regex" ]]; then PROC="$2"; shift 2
  elif [[ "$1" == "--dry-run" ]]; then DRY=1; shift
  elif [[ "$1" == "--help" ]]; then usage; exit 0
  else echo "clean_render_tmp: unknown argument: $1" >&2; exit 2
  fi
done
if [[ ${#PATTERNS[@]} -eq 0 ]]; then PATTERNS=('remotion-webpack-bundle-*' 'remotion-v4.*-assets*'); fi

T="$(cd "$DIR" && pwd -P)"
if pgrep -f "$PROC" >/dev/null 2>&1; then
  echo "clean_render_tmp: a render is active; nothing deleted" >&2
  exit 0
fi

EXPR=()
for p in "${PATTERNS[@]}"; do
  if [[ ${#EXPR[@]} -gt 0 ]]; then EXPR+=(-o); fi
  EXPR+=(-name "$p")
done

MINS=$((HOURS * 60))
n=0
kb=0
while IFS= read -r d; do
  [[ -n "$d" ]] || continue
  s=$(du -sk "$d" | cut -f1)
  n=$((n + 1)); kb=$((kb + s))
  if [[ $DRY -eq 1 ]]; then echo "$d"; else rm -rf "${d:?}"; fi
done < <(find "$T" -mindepth 1 -maxdepth 1 -type d \( "${EXPR[@]}" \) -mmin "+$MINS" 2>/dev/null)

verb="deleted"; [[ $DRY -eq 1 ]] && verb="would delete"
echo "clean_render_tmp: $verb $n directories, $((kb / 1024)) MB, from $T (older than ${HOURS} h)"
