#!/usr/bin/env bash
set -euo pipefail

app_path="${1:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
bc_version="${2:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
out_json="${3:?usage: collect-linux-contract.sh <smoke-app> <bc-version> <out-json>}"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_log="$(mktemp)"
trap 'rm -f "$test_log"' EXIT
test_status=0
test_diagnostic=()
runner_kind="startup-debug"

write_failed_test_summary() {
  local message="$1"
  {
    echo "Test codeunits:"
    echo "total=0 passed=0 failed=1 skipped=0"
    echo "$message"
  } >> "$test_log"
}

mkdir -p "$(dirname "$out_json")"

. "$repo_dir/scripts/publish-app.sh"
echo "Publishing smoke test app..."
if ! bc_publish_app "$app_path" "http://localhost:7049/BC/dev" "$auth" 2>&1 | tee "$test_log"; then
  test_status=1
  test_diagnostic=(--diagnostic "tests.runnerSetup=smoke app publish failed")
  write_failed_test_summary "Linux smoke app publish failed before tests could run"
else
  if command -v al >/dev/null 2>&1 && python3 "$repo_dir/scripts/run-tests-altool.py" --probe --auth "$auth"; then
    runner_kind="altool"
    python3 "$repo_dir/scripts/run-tests-altool.py" \
      --app "$app_path" \
      --auth "$auth" \
      --codeunit-range "70000|70001|70003" \
      --altool-cmd "$(command -v al)" \
      --timeout 30 2>&1 | tee "$test_log" || test_status=$?
  else
    {
      echo "Test codeunits:"
      echo "total=0 passed=0 failed=0 skipped=0"
      echo "Standard AL test runner unavailable; parity contract covers container surface only."
    } >> "$test_log"
    test_diagnostic=(--diagnostic "tests.runnerSetup=standard AL test runner unavailable")
  fi
fi

python3 "$repo_dir/parity/collect_contract.py" \
  --platform linux \
  --bc-version "$bc_version" \
  --base-url "http://localhost:7046/BC" \
  --management-url "http://localhost:7045/BC/Management" \
  --management-api-url "http://localhost:7086/BC/managementApi/v1.0/companies" \
  --soap-url "http://localhost:7047/BC/WS/Services" \
  --web-client-url "http://localhost:7085/BC/" \
  --client-websocket-url "http://localhost:7085/BC/client/csh" \
  --dev-url "http://localhost:7049/BC/dev" \
  --odata-url "http://localhost:7048/BC/ODataV4" \
  --api-url "http://localhost:7052/BC/api/v2.0" \
  --auth "$auth" \
  --invalid-auth "not-admin:not-admin" \
  --test-output "$test_log" \
  --runner-kind "$runner_kind" \
  --diagnostic "docker=$(docker --version 2>/dev/null || true)" \
  "${test_diagnostic[@]}" \
  --out "$out_json"

exit 0
