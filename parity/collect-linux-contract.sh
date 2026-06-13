#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
bc_version="${2:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
out_json="${3:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_log="$(mktemp)"

mkdir -p "$(dirname "$out_json")"

"$repo_dir/scripts/run-tests.sh" \
  --app "$app_path" \
  --auth "$auth" \
  --base-url "http://localhost:7046/BC" \
  --codeunit-range "70000|70001" \
  --timeout 30 2>&1 | tee "$test_log"

python3 "$repo_dir/parity/collect_contract.py" \
  --platform linux \
  --bc-version "$bc_version" \
  --base-url "http://localhost:7046/BC" \
  --dev-url "http://localhost:7049/BC/dev" \
  --odata-url "http://localhost:7048/BC/ODataV4" \
  --api-url "http://localhost:7052/BC/api/v2.0" \
  --auth "$auth" \
  --invalid-auth "not-admin:not-admin" \
  --test-output "$test_log" \
  --runner-kind websocket \
  --diagnostic "docker=$(docker --version 2>/dev/null || true)" \
  --out "$out_json"
