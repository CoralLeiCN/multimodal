#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python scripts/export_openapi.py
if command -v bun >/dev/null 2>&1; then
  bun run generate-client
else
  export PATH="$PWD/data/tools/node_modules/.bin:$PATH"
  bun run generate-client
fi
