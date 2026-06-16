#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app>}"
bc_version="${2:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app>}"
out_json="${3:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app>}"
patched_test_runner_app="${4:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app>}"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_log="$(mktemp)"
trap 'rm -f "$test_log"' EXIT
test_status=0

mkdir -p "$(dirname "$out_json")"

if [ ! -f "$patched_test_runner_app" ]; then
  echo "patched Microsoft Test Runner app not found: $patched_test_runner_app" >&2
  exit 1
fi

. "$repo_dir/scripts/publish-app.sh"
echo "Publishing version-matched patched Microsoft Test Runner..."
bc_publish_app "$patched_test_runner_app" "http://localhost:7049/BC/dev" "$auth"

"$repo_dir/scripts/run-tests.sh" \
  --app "$app_path" \
  --auth "$auth" \
  --base-url "http://localhost:7046/BC" \
  --codeunit-range "70000|70001" \
  --timeout 30 2>&1 | tee "$test_log" || test_status=$?

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

exit "$test_status"
