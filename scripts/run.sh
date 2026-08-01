#!/usr/bin/env bash
#
# Start FactorioReforge from the project's virtualenv.
#
# Exists so you do not have to remember to activate .venv, and so the process
# runs with the repository as its working directory -- every relative path in
# config.yml is resolved against that.
#
# Anything passed here goes straight to the module:
#   ./scripts/run.sh                    # normal start
#   ./scripts/run.sh init               # regenerate config.yml and directories
#   ./scripts/run.sh --config other.yml

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "No .venv here. Run ./scripts/install.sh first." >&2
  exit 1
fi

if [[ ! -f config.yml && ${1:-} != "init" ]]; then
  echo "No config.yml here. Run ./scripts/install.sh, or ./scripts/run.sh init." >&2
  exit 1
fi

exec .venv/bin/python -m factorio_reforge "$@"
