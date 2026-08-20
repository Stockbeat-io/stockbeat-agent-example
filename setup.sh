#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt yfinance
echo "Setup complete. Copy .env.example to .env and fill in keys."
echo "Then pick an LLM provider — see 'Pick your LLM' in README.md."
