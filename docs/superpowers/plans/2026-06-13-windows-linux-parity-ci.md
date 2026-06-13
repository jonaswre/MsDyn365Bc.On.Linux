# Windows/Linux Parity CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manual GitHub Actions workflow that runs Linux and Windows Business Central containers on GitHub-hosted runners, emits normalized behavior contracts for BC `27.5` and `28.1`, and fails on unexpected Linux/Windows differences.

**Architecture:** Use one shared Python contract collector and one shared Python comparator so Linux and Windows emit the same JSON shape. Keep platform-specific code in thin Bash/PowerShell wrappers that only start containers, publish/run the smoke app, and invoke the shared collector.

**Tech Stack:** GitHub Actions, Docker Compose, BcContainerHelper, Bash, PowerShell, Python standard library `unittest`, `urllib`, `json`, `argparse`.

---

## File Structure

- Create `parity/collect_contract.py`: shared Python HTTP collector and contract normalizer.
- Create `parity/compare_contracts.py`: shared comparator with known-delta support.
- Create `parity/known-deltas.json`: explicit allowlist for intentional v1 differences.
- Create `parity/collect-linux-contract.sh`: Linux wrapper for publishing/running the smoke app and writing the Linux contract.
- Create `parity/collect-windows-contract.ps1`: Windows wrapper for BcContainerHelper capability checks, container startup, smoke test execution, and contract output.
- Create `.github/workflows/parity-windows-linux.yml`: manual cross-platform parity workflow.
- Create `tests/parity/test_collect_contract.py`: unit tests for normalization and collector helpers.
- Create `tests/parity/test_compare_contracts.py`: unit tests for comparator and known deltas.

## Task 1: Comparator Tests And Known Delta File

**Files:**
- Create: `tests/parity/test_compare_contracts.py`
- Create: `parity/known-deltas.json`

- [ ] **Step 1: Create comparator unit tests**

Create `tests/parity/test_compare_contracts.py` with these tests:

```python
import json
import tempfile
import unittest
from pathlib import Path

from parity.compare_contracts import compare_contracts, load_known_deltas


def base_contract(platform):
    return {
        "schemaVersion": 1,
        "platform": platform,
        "bcVersionInput": "28.1",
        "surface": {
            "odata": {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True},
            "api": {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True},
        },
        "auth": {
            "validCredentialsAccepted": True,
            "invalidCredentialsRejected": True,
            "authSchemeClass": "basic",
        },
        "company": {
            "companyCountAtLeastOne": True,
            "firstCompanyName": "CRONUS International Ltd.",
            "apiCompanyShape": ["id", "name", "systemVersion"],
            "odataCompanyShape": ["Name"],
        },
        "dev": {
            "metadataReachable": True,
            "packagesEndpointReachable": True,
            "devApiMajor": 7,
            "supportsTestRunnerHub": True,
        },
        "tests": {
            "testCodeunitCount": 2,
            "total": 4,
            "passed": 4,
            "failed": 0,
            "skipped": 0,
            "runnerKind": "websocket",
        },
        "apps": {
            "microsoftApps": [
                {"publisher": "Microsoft", "name": "System Application", "version": "28.1.0.0"}
            ],
            "customApps": [],
            "testFrameworkPresent": True,
        },
        "users": {
            "authUserName": "admin",
            "enabledSuperUserCount": 1,
            "knownUserNames": ["ADMIN"],
        },
        "diagnostics": {"ignored": "value"},
    }


class CompareContractsTests(unittest.TestCase):
    def test_identical_contracts_have_no_unexpected_diffs(self):
        result = compare_contracts(base_contract("linux"), base_contract("windows"), [])
        self.assertEqual([], result.unexpected)
        self.assertEqual([], result.applied_known_deltas)

    def test_diagnostics_and_platform_are_ignored(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["diagnostics"]["docker"] = "linux"
        windows["diagnostics"]["docker"] = "windows"

        result = compare_contracts(linux, windows, [])

        self.assertEqual([], result.unexpected)

    def test_unexpected_value_difference_is_reported(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["auth"]["invalidCredentialsRejected"] = False

        result = compare_contracts(linux, windows, [])

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("auth.invalidCredentialsRejected", result.unexpected[0]["path"])

    def test_known_delta_suppresses_matching_custom_app_difference(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["apps"]["customApps"] = [
            {
                "publisher": "ALDirectCompile",
                "name": "Test Runner Extension",
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "version": "3.0.0.0",
            }
        ]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "ALDirectCompile", "name": "Test Runner Extension"},
                "reason": "Linux runner installs a custom API extension for v1 test orchestration.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual([], result.unexpected)
        self.assertEqual(1, len(result.applied_known_deltas))

    def test_known_delta_file_loads_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "known-deltas.json"
            path.write_text(json.dumps([{"path": "apps.customApps[]", "match": {}, "reason": "test"}]), encoding="utf-8")

            self.assertEqual(1, len(load_known_deltas(path)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Create the initial known delta file**

Create `parity/known-deltas.json`:

```json
[
  {
    "path": "apps.customApps[]",
    "match": {
      "publisher": "ALDirectCompile",
      "name": "Test Runner Extension"
    },
    "reason": "Linux runner installs a custom API extension for v1 test orchestration."
  }
]
```

- [ ] **Step 3: Run the failing comparator tests**

Run:

```bash
python3 -m unittest tests.parity.test_compare_contracts -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'parity.compare_contracts'`.

- [ ] **Step 4: Commit tests and known deltas**

```bash
git add tests/parity/test_compare_contracts.py parity/known-deltas.json
git commit -m "test: define parity comparator behavior"
```

## Task 2: Comparator Implementation

**Files:**
- Create: `parity/__init__.py`
- Create: `parity/compare_contracts.py`
- Test: `tests/parity/test_compare_contracts.py`

- [ ] **Step 1: Implement comparator data model and recursive diff**

Create `parity/__init__.py` as an empty package marker.

Create `parity/compare_contracts.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_TOP_LEVEL_KEYS = {"platform", "diagnostics"}


@dataclass
class CompareResult:
    unexpected: list[dict[str, Any]]
    applied_known_deltas: list[dict[str, Any]]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_known_deltas(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return data


def normalize_for_compare(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_for_compare(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((normalize_for_compare(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def flatten_diff(path: str, left: Any, right: Any) -> list[dict[str, Any]]:
    left = normalize_for_compare(left)
    right = normalize_for_compare(right)
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else key
            diffs.extend(flatten_diff(child_path, left.get(key), right.get(key)))
        return diffs
    return [{"path": path, "linux": left, "windows": right}]


def item_matches(item: Any, match: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    return all(item.get(key) == value for key, value in match.items())


def apply_known_deltas(diffs: list[dict[str, Any]], known_deltas: list[dict[str, Any]]) -> CompareResult:
    unexpected: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for diff in diffs:
        handled = False
        for delta in known_deltas:
            if delta.get("path") == "apps.customApps[]" and diff["path"] == "apps.customApps":
                linux_items = diff.get("linux") if isinstance(diff.get("linux"), list) else []
                windows_items = diff.get("windows") if isinstance(diff.get("windows"), list) else []
                extra_linux = [item for item in linux_items if item not in windows_items]
                if extra_linux and all(item_matches(item, delta.get("match", {})) for item in extra_linux):
                    applied.append({"delta": delta, "diff": diff})
                    handled = True
                    break
        if not handled:
            unexpected.append(diff)
    return CompareResult(unexpected=unexpected, applied_known_deltas=applied)


def compare_contracts(linux_contract: dict[str, Any], windows_contract: dict[str, Any], known_deltas: list[dict[str, Any]]) -> CompareResult:
    left = {key: value for key, value in linux_contract.items() if key not in IGNORED_TOP_LEVEL_KEYS}
    right = {key: value for key, value in windows_contract.items() if key not in IGNORED_TOP_LEVEL_KEYS}
    return apply_known_deltas(flatten_diff("", left, right), known_deltas)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare Linux and Windows BC parity contracts")
    parser.add_argument("--linux", required=True, type=Path)
    parser.add_argument("--windows", required=True, type=Path)
    parser.add_argument("--known-deltas", default=Path("parity/known-deltas.json"), type=Path)
    args = parser.parse_args(argv)

    result = compare_contracts(load_json(args.linux), load_json(args.windows), load_known_deltas(args.known_deltas))
    for applied in result.applied_known_deltas:
        reason = applied["delta"].get("reason", "known delta")
        print(f"KNOWN {applied['diff']['path']}: {reason}")
    for diff in result.unexpected:
        print(f"DIFF {diff['path']}")
        print(f"  linux:   {json.dumps(diff['linux'], sort_keys=True)}")
        print(f"  windows: {json.dumps(diff['windows'], sort_keys=True)}")
    return 1 if result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: Run comparator tests**

Run:

```bash
python3 -m unittest tests.parity.test_compare_contracts -v
```

Expected: PASS.

- [ ] **Step 3: Commit comparator implementation**

```bash
git add parity/__init__.py parity/compare_contracts.py
git commit -m "feat: compare parity contracts"
```

## Task 3: Contract Collector Tests

**Files:**
- Create: `tests/parity/test_collect_contract.py`
- Test: `parity/collect_contract.py`

- [ ] **Step 1: Create collector unit tests**

Create `tests/parity/test_collect_contract.py`:

```python
import unittest

from parity.collect_contract import (
    http_class,
    normalize_company_name,
    parse_dev_api_major,
    summarize_test_output,
)


class CollectContractTests(unittest.TestCase):
    def test_http_class_normalizes_status_codes(self):
        self.assertEqual("2xx", http_class(200))
        self.assertEqual("3xx", http_class(302))
        self.assertEqual("4xx", http_class(401))
        self.assertEqual("5xx", http_class(503))
        self.assertEqual("000", http_class(0))

    def test_company_name_strips_whitespace(self):
        self.assertEqual("CRONUS International Ltd.", normalize_company_name("  CRONUS International Ltd.  "))

    def test_dev_api_major_reads_metadata_key(self):
        metadata = {"supportedVersions": [{"apiVersion": "7.0"}, {"apiVersion": "6.0"}]}
        self.assertEqual(7, parse_dev_api_major(metadata))

    def test_dev_api_major_returns_none_for_missing_shape(self):
        self.assertIsNone(parse_dev_api_major({"value": []}))

    def test_summarize_test_output_parses_current_run_tests_format(self):
        output = "Test codeunits: 70000,70001\ntotal=4 passed=4 failed=0 skipped=0\n"
        summary = summarize_test_output(output, "websocket")
        self.assertEqual(2, summary["testCodeunitCount"])
        self.assertEqual(4, summary["total"])
        self.assertEqual(4, summary["passed"])
        self.assertEqual(0, summary["failed"])
        self.assertEqual(0, summary["skipped"])
        self.assertEqual("websocket", summary["runnerKind"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing collector tests**

Run:

```bash
python3 -m unittest tests.parity.test_collect_contract -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'parity.collect_contract'`.

- [ ] **Step 3: Commit collector tests**

```bash
git add tests/parity/test_collect_contract.py
git commit -m "test: define parity contract collector helpers"
```

## Task 4: Shared Contract Collector Implementation

**Files:**
- Create: `parity/collect_contract.py`
- Test: `tests/parity/test_collect_contract.py`

- [ ] **Step 1: Implement collector helpers and CLI**

Create `parity/collect_contract.py` with HTTP helpers, normalizers, and a CLI that accepts:

```text
--platform linux|windows
--bc-version <version>
--base-url http://localhost:7046/BC
--dev-url http://localhost:7049/BC/dev
--odata-url http://localhost:7048/BC/ODataV4
--api-url http://localhost:7052/BC/api/v2.0
--auth admin:admin
--invalid-auth not-admin:not-admin
--test-output <path>
--runner-kind websocket|bccontainerhelper
--diagnostic key=value
--out contracts/linux-28.1.json
```

Required functions:

```python
def http_class(status: int) -> str:
    if 200 <= status <= 299:
        return "2xx"
    if 300 <= status <= 399:
        return "3xx"
    if 400 <= status <= 499:
        return "4xx"
    if 500 <= status <= 599:
        return "5xx"
    return "000"
```

```python
def normalize_company_name(value: str) -> str:
    return " ".join((value or "").split())
```

```python
def parse_dev_api_major(metadata: dict) -> int | None:
    versions = metadata.get("supportedVersions")
    if not isinstance(versions, list):
        return None
    majors = []
    for item in versions:
        raw = str(item.get("apiVersion", ""))
        head = raw.split(".", 1)[0]
        if head.isdigit():
            majors.append(int(head))
    return max(majors) if majors else None
```

```python
def summarize_test_output(output: str, runner_kind: str) -> dict:
    import re

    codeunit_count = 0
    match = re.search(r"Test codeunits:\s*([^\n]+)", output)
    if match:
        codeunit_count = len([part for part in match.group(1).replace("|", ",").split(",") if part.strip()])

    totals = re.search(r"total=(\d+)\s+passed=(\d+)\s+failed=(\d+)\s+skipped=(\d+)", output)
    if not totals:
        totals = re.search(r"(\d+)\s+total,\s+(\d+)\s+passed,\s+(\d+)\s+failed", output)
        if totals:
            return {
                "testCodeunitCount": codeunit_count,
                "total": int(totals.group(1)),
                "passed": int(totals.group(2)),
                "failed": int(totals.group(3)),
                "skipped": 0,
                "runnerKind": runner_kind,
            }
    if totals:
        return {
            "testCodeunitCount": codeunit_count,
            "total": int(totals.group(1)),
            "passed": int(totals.group(2)),
            "failed": int(totals.group(3)),
            "skipped": int(totals.group(4)),
            "runnerKind": runner_kind,
        }
    return {
        "testCodeunitCount": codeunit_count,
        "total": 0,
        "passed": 0,
        "failed": 1,
        "skipped": 0,
        "runnerKind": runner_kind,
    }
```

For live HTTP collection, use only Python standard library:

```python
from urllib import request, error
import base64
import json


def basic_header(auth: str) -> str:
    return "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii")


def fetch_json(url: str, auth: str, timeout: int = 15) -> tuple[int, dict]:
    req = request.Request(url, headers={"Authorization": basic_header(auth)})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, {}
    except Exception:
        return 0, {}
```

The CLI must write a complete contract even when one section fails. Failed
sections should emit booleans/status classes that make the comparator fail,
while `diagnostics` carries the error string.

- [ ] **Step 2: Run collector tests**

Run:

```bash
python3 -m unittest tests.parity.test_collect_contract -v
```

Expected: PASS.

- [ ] **Step 3: Run all parity unit tests**

Run:

```bash
python3 -m unittest discover -s tests/parity -v
```

Expected: PASS.

- [ ] **Step 4: Commit collector implementation**

```bash
git add parity/collect_contract.py
git commit -m "feat: collect normalized parity contracts"
```

## Task 5: Linux Contract Wrapper

**Files:**
- Create: `parity/collect-linux-contract.sh`
- Modify: no existing files

- [ ] **Step 1: Create Linux wrapper**

Create `parity/collect-linux-contract.sh`:

```bash
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
```

- [ ] **Step 2: Make wrapper executable and syntax-check it**

Run:

```bash
chmod +x parity/collect-linux-contract.sh
bash -n parity/collect-linux-contract.sh
```

Expected: no output and exit code `0`.

- [ ] **Step 3: Commit Linux wrapper**

```bash
git add parity/collect-linux-contract.sh
git commit -m "feat: add linux parity contract wrapper"
```

## Task 6: Windows Contract Wrapper

**Files:**
- Create: `parity/collect-windows-contract.ps1`

- [ ] **Step 1: Create Windows wrapper**

Create `parity/collect-windows-contract.ps1` with parameters:

```powershell
param(
    [Parameter(Mandatory = $true)][string]$BcVersion,
    [Parameter(Mandatory = $true)][string]$SmokeAppPath,
    [Parameter(Mandatory = $true)][string]$OutJson,
    [string]$ContainerName = "bc-parity",
    [string]$Username = "admin",
    [string]$Password = "admin"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$contractsDir = Split-Path -Parent $OutJson
New-Item -ItemType Directory -Force -Path $contractsDir | Out-Null

function Fail-Capability($Code, $Message) {
    Write-Error "$Code`: $Message"
    exit 1
}

try {
    docker version | Out-Host
} catch {
    Fail-Capability "WINDOWS_RUNNER_DOCKER_UNAVAILABLE" $_.Exception.Message
}

try {
    docker run --rm mcr.microsoft.com/windows/nanoserver:ltsc2022 cmd /c ver | Out-Host
} catch {
    Fail-Capability "WINDOWS_RUNNER_WINDOWS_CONTAINERS_UNAVAILABLE" $_.Exception.Message
}

try {
    Install-PackageProvider -Name NuGet -Force | Out-Null
    Install-Module BcContainerHelper -Force -AllowClobber
    Import-Module BcContainerHelper
    Get-Module BcContainerHelper | Format-List Name,Version | Out-Host
} catch {
    Fail-Capability "BC_CONTAINER_HELPER_UNAVAILABLE" $_.Exception.Message
}

$securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)
$artifactUrl = Get-BCArtifactUrl -Type OnPrem -Country w1 -Version $BcVersion

try {
    New-BcContainer `
        -accept_eula `
        -containerName $ContainerName `
        -artifactUrl $artifactUrl `
        -Credential $credential `
        -auth UserPassword `
        -isolation process `
        -updateHosts `
        -shortcuts None
} catch {
    Fail-Capability "WINDOWS_BC_CONTAINER_START_FAILED" $_.Exception.Message
}

$testLog = Join-Path $env:RUNNER_TEMP "bc-parity-tests-$BcVersion.log"
try {
    Publish-BcContainerApp -containerName $ContainerName -appFile $SmokeAppPath -sync -install -skipVerification
    $results = Run-TestsInBcContainer -containerName $ContainerName -credential $credential -extensionId "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" -XUnitResultFileName (Join-Path $env:RUNNER_TEMP "bc-parity-$BcVersion.xml")
    $results | Out-String | Tee-Object -FilePath $testLog
} catch {
    "Test codeunits: 70000,70001" | Set-Content -Path $testLog
    "total=0 passed=0 failed=1 skipped=0" | Add-Content -Path $testLog
    $_.Exception.Message | Add-Content -Path $testLog
}

python "$repoRoot\parity\collect_contract.py" `
    --platform windows `
    --bc-version $BcVersion `
    --base-url "http://localhost:7046/BC" `
    --dev-url "http://localhost:7049/BC/dev" `
    --odata-url "http://localhost:7048/BC/ODataV4" `
    --api-url "http://localhost:7052/BC/api/v2.0" `
    --auth "$Username`:$Password" `
    --invalid-auth "not-admin:not-admin" `
    --test-output $testLog `
    --runner-kind bccontainerhelper `
    --diagnostic "artifactUrl=$artifactUrl" `
    --diagnostic "bcContainerHelper=$((Get-Module BcContainerHelper).Version)" `
    --out $OutJson
```

- [ ] **Step 2: Syntax-check the PowerShell file**

Run on a machine with PowerShell:

```bash
pwsh -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw parity/collect-windows-contract.ps1)); "ok"'
```

Expected: prints `ok`.

- [ ] **Step 3: Commit Windows wrapper**

```bash
git add parity/collect-windows-contract.ps1
git commit -m "feat: add windows parity contract wrapper"
```

## Task 7: Manual Parity Workflow

**Files:**
- Create: `.github/workflows/parity-windows-linux.yml`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/parity-windows-linux.yml`:

```yaml
name: Windows/Linux BC parity

on:
  workflow_dispatch:
    inputs:
      versions:
        description: 'BC versions to compare'
        required: false
        default: '27.5,28.1'
        type: string

permissions:
  contents: read
  packages: read

jobs:
  prepare:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.matrix.outputs.matrix }}
    steps:
      - id: matrix
        run: |
          versions='${{ inputs.versions }}'
          python3 - <<'PY' "$versions" >> "$GITHUB_OUTPUT"
          import json, sys
          versions = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
          print("matrix=" + json.dumps({"bc_version": versions}))
          PY

  build-smoke-app:
    needs: prepare
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix: ${{ fromJson(needs.prepare.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - name: Download BC artifacts
        run: |
          mkdir -p artifact-cache/${{ matrix.bc_version }}
          scripts/download-artifacts.sh onprem ${{ matrix.bc_version }} w1 artifact-cache/${{ matrix.bc_version }}
      - name: Install AL compiler and compile smoke app
        run: |
          BC_MAJOR=$(echo "${{ matrix.bc_version }}" | cut -d. -f1)
          case "$BC_MAJOR" in
            27) AL_TOOL="16.2.28.57946"; RUNTIME="16.0" ;;
            28) AL_TOOL="17.0.34.45391"; RUNTIME="17.0" ;;
            *) echo "Unsupported BC major $BC_MAJOR"; exit 1 ;;
          esac
          export RUNTIME
          PKG="microsoft.dynamics.businesscentral.development.tools.linux"
          EXTRACT_DIR="$HOME/.al-tool-cache/$AL_TOOL"
          mkdir -p "$EXTRACT_DIR"
          curl -fsSL "https://api.nuget.org/v3-flatcontainer/${PKG}/${AL_TOOL}/${PKG}.${AL_TOOL}.nupkg" -o "$EXTRACT_DIR/pkg.zip"
          unzip -q -o "$EXTRACT_DIR/pkg.zip" -d "$EXTRACT_DIR"
          rm "$EXTRACT_DIR/pkg.zip"
          AL_TOOL_DIR="$EXTRACT_DIR/tools/net8.0/any"
          if [ ! -d "$AL_TOOL_DIR" ]; then AL_TOOL_DIR="$EXTRACT_DIR/lib/net8.0"; fi
          printf '#!/usr/bin/env bash\nexec dotnet "%s/Microsoft.Dynamics.BusinessCentral.Development.Tools.dll" "$@"\n' "$AL_TOOL_DIR" > "$HOME/al"
          chmod +x "$HOME/al"
          echo "$HOME" >> "$GITHUB_PATH"
          python3 scripts/stage-symbols.py \
            --artifact-dir artifact-cache/${{ matrix.bc_version }} \
            --app-json extensions/smoke-test/app.json \
            --out-dir extensions/smoke-test/.alpackages
          python3 - <<'PY'
          import os
          import json
          from pathlib import Path
          path = Path("extensions/smoke-test/app.json")
          data = json.loads(path.read_text())
          data["runtime"] = os.environ["RUNTIME"]
          path.write_text(json.dumps(data, indent=2) + "\n")
          PY
          "$HOME/al" compile "/project:extensions/smoke-test" "/packagecachepath:extensions/smoke-test/.alpackages" "/out:smoke-test-${{ matrix.bc_version }}.app"
      - uses: actions/upload-artifact@v4
        with:
          name: smoke-app-${{ matrix.bc_version }}
          path: smoke-test-${{ matrix.bc_version }}.app

  linux-contract:
    needs: [prepare, build-smoke-app]
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix: ${{ fromJson(needs.prepare.outputs.matrix) }}
    env:
      BC_VERSION: ${{ matrix.bc_version }}
      BC_COUNTRY: w1
      BC_TYPE: onprem
      BC_USERNAME: admin
      BC_PASSWORD: admin
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: smoke-app-${{ matrix.bc_version }}
          path: build
      - name: Start Linux BC
        run: |
          docker compose up -d
          scripts/wait-for-bc-healthy.sh 30
      - name: Collect Linux contract
        run: |
          parity/collect-linux-contract.sh "build/smoke-test-${{ matrix.bc_version }}.app" "${{ matrix.bc_version }}" "contracts/linux-${{ matrix.bc_version }}.json"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: contract-linux-${{ matrix.bc_version }}
          path: contracts/linux-${{ matrix.bc_version }}.json

  windows-contract:
    needs: [prepare, build-smoke-app]
    runs-on: windows-2022
    strategy:
      fail-fast: false
      matrix: ${{ fromJson(needs.prepare.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: smoke-app-${{ matrix.bc_version }}
          path: build
      - name: Collect Windows contract
        shell: pwsh
        run: |
          .\parity\collect-windows-contract.ps1 -BcVersion "${{ matrix.bc_version }}" -SmokeAppPath "$PWD\build\smoke-test-${{ matrix.bc_version }}.app" -OutJson "$PWD\contracts\windows-${{ matrix.bc_version }}.json"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: contract-windows-${{ matrix.bc_version }}
          path: contracts/windows-${{ matrix.bc_version }}.json

  compare-contracts:
    needs: [prepare, linux-contract, windows-contract]
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix: ${{ fromJson(needs.prepare.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: contract-linux-${{ matrix.bc_version }}
          path: contracts
      - uses: actions/download-artifact@v4
        with:
          name: contract-windows-${{ matrix.bc_version }}
          path: contracts
      - name: Compare contracts
        run: |
          python3 parity/compare_contracts.py \
            --linux "contracts/linux-${{ matrix.bc_version }}.json" \
            --windows "contracts/windows-${{ matrix.bc_version }}.json" \
            --known-deltas parity/known-deltas.json
```

- [ ] **Step 2: Validate workflow YAML parses as YAML**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
yaml.safe_load(Path(".github/workflows/parity-windows-linux.yml").read_text())
print("ok")
PY
```

Expected: prints `ok`. If `yaml` is unavailable, run:

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/parity-windows-linux.yml"); puts "ok"'
```

- [ ] **Step 3: Commit workflow**

```bash
git add .github/workflows/parity-windows-linux.yml
git commit -m "ci: add windows linux parity workflow"
```

## Task 8: Local Verification And Final Review

**Files:**
- Modify only files from previous tasks if verification finds defects.

- [ ] **Step 1: Run unit tests**

Run:

```bash
python3 -m unittest discover -s tests/parity -v
```

Expected: PASS.

- [ ] **Step 2: Run shell syntax checks**

Run:

```bash
bash -n parity/collect-linux-contract.sh
```

Expected: no output and exit code `0`.

- [ ] **Step 3: Run comparator against synthetic equal contracts**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path
contract = {
  "schemaVersion": 1,
  "platform": "linux",
  "bcVersionInput": "28.1",
  "surface": {},
  "auth": {},
  "company": {},
  "dev": {},
  "tests": {"testCodeunitCount": 2, "total": 4, "passed": 4, "failed": 0, "skipped": 0, "runnerKind": "websocket"},
  "apps": {"microsoftApps": [], "customApps": [], "testFrameworkPresent": True},
  "users": {"authUserName": "admin", "enabledSuperUserCount": 1, "knownUserNames": ["ADMIN"]},
  "diagnostics": {}
}
Path("/tmp/linux.json").write_text(json.dumps({**contract, "platform": "linux"}))
Path("/tmp/windows.json").write_text(json.dumps({**contract, "platform": "windows"}))
PY
python3 parity/compare_contracts.py --linux /tmp/linux.json --windows /tmp/windows.json --known-deltas parity/known-deltas.json
```

Expected: exit code `0`.

- [ ] **Step 4: Inspect git diff**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: worktree clean after task commits, latest commits correspond to the parity implementation tasks.

- [ ] **Step 5: Record manual workflow execution command**

Run after pushing the branch:

```bash
gh workflow run "Windows/Linux BC parity" --ref "$(git branch --show-current)" -f versions="27.5,28.1"
```

Expected: GitHub queues the manual workflow. The first hosted Windows run may fail with one of the explicit capability codes; that is a setup result, not a comparator defect.
