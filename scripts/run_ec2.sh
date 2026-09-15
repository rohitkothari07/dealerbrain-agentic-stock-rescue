#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ ! -f .venv/bin/activate || ! -x .venv/bin/python ]]; then
    echo "Missing .venv; create it and install requirements before launching." >&2
    exit 1
fi
# Activate only the local Python virtual environment, never credential files.
source .venv/bin/activate
port="${PORT:-8501}"
if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    echo "PORT must be an integer from 1 to 65535." >&2
    exit 1
fi
mkdir -p runtime
exec .venv/bin/python -m streamlit run app.py \
    --server.address=0.0.0.0 --server.port="$port" --server.headless=true \
    --browser.gatherUsageStats=false
