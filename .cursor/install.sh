#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the Player Similarity Bot notebook project.
set -euo pipefail

cd "$(dirname "$0")/.."

# Ensure the Python venv module is available. The stable system package is
# normally baked into the environment snapshot; this guard makes a cold setup
# (no snapshot) self-healing without reinstalling on every boot.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3.12-venv
fi

# Create the project virtualenv if it does not already exist.
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
