#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app> [test-runner-extension-app]}"
bc_version="${2:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app> [test-runner-extension-app]}"
out_json="${3:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app> [test-runner-extension-app]}"
patched_test_runner_app="${4:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json> <patched-test-runner-app> [test-runner-extension-app]}"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_runner_extension_app="${5:-$repo_dir/extensions/TestRunnerExtension/TestRunnerExtension.app}"
test_log="$(mktemp)"
trap 'rm -f "$test_log"' EXIT
test_status=0
test_diagnostic=()

write_failed_test_summary() {
  local message="$1"
  {
    echo "Test codeunits:"
    echo "total=0 passed=0 failed=1 skipped=0"
    echo "$message"
  } >> "$test_log"
}

mkdir -p "$(dirname "$out_json")"

if [ ! -f "$patched_test_runner_app" ]; then
  echo "patched Microsoft Test Runner app not found: $patched_test_runner_app" >&2
  exit 1
fi

. "$repo_dir/scripts/publish-app.sh"
echo "Publishing version-matched patched Microsoft Test Runner..."
if ! bc_publish_app "$patched_test_runner_app" "http://localhost:7049/BC/dev" "$auth" 2>&1 | tee "$test_log"; then
  test_status=1
  test_diagnostic=(--diagnostic "tests.runnerSetup=patched Microsoft Test Runner publish failed")
  write_failed_test_summary "Linux Test Runner setup failed before smoke tests could run"
else
  "$repo_dir/scripts/run-tests.sh" \
    --app "$app_path" \
    --test-runner-app "$test_runner_extension_app" \
    --auth "$auth" \
    --base-url "http://localhost:7046/BC" \
    --codeunit-range "70000|70001" \
    --timeout 30 2>&1 | tee "$test_log" || test_status=$?
fi

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
  "${test_diagnostic[@]}" \
  --out "$out_json"

exit 0
