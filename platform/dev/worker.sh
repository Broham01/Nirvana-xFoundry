#!/usr/bin/env bash
# Start the xFoundry worker for the dev platform (ADR 0004). It carries out jobs you queue in Nirvana xStage's
# console (Environments): it plans them, waits for you to approve each plan in xStage, and applies only what passes
# the gate. Stop it with Ctrl-C; a job waiting for approval resumes when it starts again.
#
# Settings (all optional):
#   NIRVANA_URL   xStage's API (default http://localhost:8000)
#   NIRVANA_DIR   your xStage folder, for its snapshot collector (default ~/Claude /Nirvana)
#   NIRVANA_TOKEN the worker's service token; by default read from the Keychain item "nirvana-xfoundry-token",
#                 which "Set xFoundry token.command" stores. It is never printed.
set -euo pipefail
DEV_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$DEV_DIR/../.." && pwd)
cd "$ROOT"
if [ ! -f "$DEV_DIR/env.sh" ]; then
  echo "The dev platform is not set up yet: run platform/dev/up.sh first."; exit 1
fi
# shellcheck source=/dev/null
source "$DEV_DIR/env.sh"
export NIRVANA_URL="${NIRVANA_URL:-http://localhost:8000}"
export NIRVANA_DIR="${NIRVANA_DIR:-$HOME/Claude /Nirvana}"
if [ -z "${NIRVANA_TOKEN:-}" ]; then
  NIRVANA_TOKEN=$(security find-generic-password -s nirvana-xfoundry-token -a "$USER" -w 2>/dev/null || true)
  export NIRVANA_TOKEN
fi
if [ -z "$NIRVANA_TOKEN" ]; then
  echo "No worker token yet. In xStage, open Account > Service tokens, create one, then double-click"
  echo "\"Set xFoundry token.command\" in this folder and paste it there."; exit 1
fi
if [ ! -x .venv/bin/xfoundry ]; then
  echo "Installing xFoundry into .venv (first run only)…"
  python3 -m venv .venv && .venv/bin/pip install -q -e .
fi
if ! curl -fsS -m 3 "$NIRVANA_URL/healthz" >/dev/null 2>&1; then
  echo "xStage is not answering at $NIRVANA_URL. Start it (nirvana-update.command), then try again."; exit 1
fi
exec .venv/bin/xfoundry worker "$@"
