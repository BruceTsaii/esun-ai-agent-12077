#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python scripts/generate_demo.py --output runs/demo-inputs
.venv/bin/esun-agent run --mode test \
  --datapack runs/demo-inputs/datapack.json \
  --evidence runs/demo-inputs/bundle.json \
  --test-response runs/demo-inputs/response.json
