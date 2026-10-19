#!/usr/bin/env bash
# earshot (https://github.com/Leonxlnx/earshot, MIT): ears for the agent and a bank of 201 code-generated effects.
# It is an optional dependency pinned to a commit and never installed globally.
#
# usage: bash earshot.sh <earshot command> [args]
#      bash earshot.sh doctor
#      bash earshot.sh sfx render whoosh_fast/crisp -o /ABS/w.wav --dur 0.8 --apex 0.32
#      bash earshot.sh kit init --preset calm -o /ABS/audio/kit.json
#
# earshot is resolved in this order:
#   1. EARSHOT_PYTHON=/path/to/venv/bin/python: an interpreter that already has it (the same variable Cinetic uses).
#      EARSHOT_PYTHON=none disables it.
#   2. uvx, from git at EARSHOT_REF. uv stores it in its cache, not the global environment.
# If neither exists, exit with code 3 and explain what is missing; the skill workflow continues without earshot.
# Models (~1.1 GB, only for timeline, sheet, critique, describe, match, ...) go to ~/.cache/earshot or
# EARSHOT_HOME; sfx, kit, and measure do not load models.
set -euo pipefail
# The same commit is in listen.py (PEP 723); tests/test_earshot.py verifies that they match.
EARSHOT_REF="${EARSHOT_REF:-925ebe56a1b755e5d354ab642bb8f344440cfa9c}"
EARSHOT_REPO="${EARSHOT_REPO:-https://github.com/Leonxlnx/earshot}"
if [ "${EARSHOT_PYTHON:-}" = "none" ]; then
  echo "earshot: disabled (EARSHOT_PYTHON=none)" >&2
  exit 3
fi
if [ -n "${EARSHOT_PYTHON:-}" ]; then
  exec "$EARSHOT_PYTHON" -m earshot "$@"
fi
if command -v uvx >/dev/null 2>&1; then
  exec uvx --from "git+${EARSHOT_REPO}@${EARSHOT_REF}" earshot "$@"
fi
echo "earshot: unavailable. Install uv (https://docs.astral.sh/uv/) or point EARSHOT_PYTHON to a venv with earshot." >&2
exit 3
