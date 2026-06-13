# Design: Windows/Linux Business Central Container Parity CI

## Goal

Create a GitHub Actions workflow that runs the Linux container and Microsoft's
standard Windows Business Central container on GitHub-hosted runners, collects a
normalized user-visible behavior contract from both, and fails when Linux
differs from Windows in ways a normal BC container user can observe.

The first version targets a small BC matrix:

- `27.5`
- `28.1`

This covers the current supported major lines without making the first hosted
Windows runner experiment too expensive or flaky.

## Non-Goals

- Do not build a full WebClient UI automation suite in v1.
- Do not compare raw logs, startup durations, host paths, container names, or
  Docker image identifiers.
- Do not require self-hosted runners.
- Do not replace the existing `test-versions.yml` Linux matrix.
- Do not hide every possible OS signal from shell access. This workflow measures
  the public BC container surface, not arbitrary container introspection.

## Workflow

Add `.github/workflows/parity-windows-linux.yml`.

Triggers:

- `workflow_dispatch` initially.
- Optional later: scheduled weekly run or pull-request path filter after the
  Windows lane proves stable.

Jobs:

1. `build-smoke-app`
   - Runs on `ubuntu-latest`.
   - Matrix: `27.5`, `28.1`.
   - Compiles `extensions/smoke-test` once per BC version using the same AL
     compiler/runtime derivation already used by `bc-test-from-source.yml`.
   - Uploads `smoke-app-<bc_version>.app`.
   - This keeps the Linux and Windows lanes from comparing different compiler
     outputs.

2. `linux-contract`
   - Runs on `ubuntu-latest`.
   - Needs `build-smoke-app`.
   - Matrix: `27.5`, `28.1`.
   - Uses the existing Linux `docker-compose.yml` and `bc-runner` image.
   - Starts BC with `BC_VERSION`, `BC_COUNTRY=w1`, `BC_TYPE=onprem`,
     `BC_USERNAME=admin`, `BC_PASSWORD=admin`.
   - Waits with `scripts/wait-for-bc-healthy.sh`.
   - Downloads `smoke-app-<bc_version>.app`, publishes it, and runs its tests.
   - Runs a Linux contract collector script.
   - Uploads `contracts/linux-<bc_version>.json`.

3. `windows-contract`
   - Runs on `windows-2022`, not `windows-latest`, to avoid runner-label drift
     as GitHub transitions Windows labels.
   - Needs `build-smoke-app`.
   - Matrix: `27.5`, `28.1`.
   - Starts with a capability probe:
     - Windows version.
     - Docker CLI/server version.
     - Docker container mode.
     - Ability to run a trivial Windows container.
     - BcContainerHelper version.
   - Uses BcContainerHelper to create a standard Microsoft BC container with:
     - `Get-BCArtifactUrl -Type OnPrem -Country w1 -Version <matrix version>`.
     - `New-BcContainer -accept_eula -artifactUrl ... -auth UserPassword`.
     - Credentials `admin/admin`.
     - Container name `bc-parity`.
     - Process isolation requested explicitly. The job records the actual
       isolation mode in `diagnostics` and fails during capability setup if the
       hosted runner cannot run a matching Windows container.
   - Downloads `smoke-app-<bc_version>.app`, publishes it, and runs its tests.
   - Runs the Windows contract collector script.
   - Uploads `contracts/windows-<bc_version>.json`.

4. `compare-contracts`
   - Runs on `ubuntu-latest`.
   - Needs both contract jobs.
   - Downloads all contract artifacts.
   - Runs a comparator over each BC version pair.
   - Applies checked-in known deltas from `parity/known-deltas.json`.
   - Fails on any unexpected mismatch.
   - Prints a compact diff grouped by contract section.

## Contract Collector

Use one logical contract schema for both platforms. The implementation may use
Bash/Python on Linux and PowerShell on Windows, but the emitted JSON shape must
match.

Output path:

```text
contracts/<platform>-<bc_version>.json
```

Top-level schema:

```json
{
  "schemaVersion": 1,
  "platform": "linux",
  "bcVersionInput": "28.1",
  "surface": {},
  "auth": {},
  "company": {},
  "dev": {},
  "tests": {},
  "apps": {},
  "users": {},
  "diagnostics": {}
}
```

Only stable BC-level behavior is compared. `diagnostics` is uploaded for
debugging but ignored by default.

## Contract Sections

### `surface`

Probe the standard BC container ports and paths:

- Management: `/BC/Management`
- Client Services: `/BC/client/SignIn`
- Client Services WebSocket upgrade: `/BC/client/csh`
- SOAP: `/BC/WS/Services`
- OData: `/BC/ODataV4/Company`
- API: `/BC/api/v2.0/companies`
- DevServices: `/BC/dev/metadata`
- Management API: `/BC/managementApi/v1.0/companies`
- WebClient: `/BC/client/SignIn`

Record normalized fields:

- `tcpOpen`: boolean.
- `httpClass`: one of `2xx`, `3xx`, `4xx`, `5xx`, `000`.
- `requiresAuth`: boolean where applicable.
- `websocketUpgrade`: boolean for Client Services.

Do not compare exact status codes until the first live Windows run confirms
they are stable.

### `auth`

Probe valid and invalid credentials against OData/API/Dev endpoints.

Record:

- `validCredentialsAccepted`: boolean.
- `invalidCredentialsRejected`: boolean.
- `authSchemeClass`: normalized value such as `basic`, `userpassword`, or
  `unknown`.

Do not compare raw `WWW-Authenticate` header text in v1.

### `company`

Read company data through API v2.0 and OData.

Record:

- `companyCountAtLeastOne`: boolean.
- `firstCompanyName`: normalized string.
- `apiCompanyShape`: sorted list of top-level property names for the first
  company.
- `odataCompanyShape`: sorted list of top-level property names for the first
  company.

Expected first company name is `CRONUS International Ltd.` for `w1` artifacts.

### `dev`

Probe the dev endpoint.

Record:

- `metadataReachable`: boolean.
- `packagesEndpointReachable`: boolean.
- `devApiMajor`: integer or null when unavailable.
- `supportsTestRunnerHub`: boolean, derived from metadata or a direct probe.

This lets the comparator distinguish BC 27 and BC 28 behavior without treating
the expected TestRunnerHub difference as a Linux/Windows gap.

### `tests`

Publish and run the existing smoke test app on both platforms.

The `build-smoke-app` job creates one compiled `.app` per BC version. Both
platform lanes download and publish that exact `.app`.

Linux runs tests with `scripts/run-tests.sh` using `test_runner=websocket` for
the parity workflow. This avoids switching runner semantics between BC 27 and
BC 28 during the first contract comparison.

Windows runs tests with BcContainerHelper's standard container test command for
the published smoke app.

Record:

- `testCodeunitCount`.
- `total`.
- `passed`.
- `failed`.
- `skipped`.
- `runnerKind`: normalized as `websocket`, `altool`, `bccontainerhelper`, or
  `unknown`.

The comparator requires:

- `failed == 0` on both platforms.
- Same `testCodeunitCount`.
- Same `total`.
- Same `passed`.
- Same `skipped`.

### `apps`

Collect installed/published app surface through BC APIs or container helper
commands.

Record:

- `microsoftApps`: sorted selected Microsoft app names and versions.
- `customApps`: sorted non-Microsoft app names, publishers, ids, and versions.
- `testFrameworkPresent`: boolean.

Comparator rules:

- Required Microsoft baseline apps must exist on both platforms.
- Custom Linux-only infrastructure apps are reported as known deltas only if
  listed in `parity/known-deltas.json`.

### `users`

Collect the visible BC user/security surface through OData/API, AL helper page,
or container helper commands.

Record:

- `authUserName`.
- `enabledSuperUserCount`.
- `knownUserNames`: sorted list, excluding volatile system-generated users if
  needed.

Comparator rules:

- `authUserName` must match `admin`.
- `enabledSuperUserCount` must be at least one on both platforms.
- Extra Linux service/test users are unexpected unless allowlisted.

### `diagnostics`

Record helpful debug data without comparing by default:

- Runner OS version.
- Docker version.
- Container image references.
- Artifact URL.
- BcContainerHelper version on Windows.
- Startup durations.
- Last relevant container logs on failure.

## Known Deltas

Add `parity/known-deltas.json`.

Purpose:

- Make intentional gaps explicit.
- Avoid silent broad ignores.
- Keep each exception tied to a contract path and rationale.

Example:

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

The comparator must print all applied known deltas so the workflow summary still
shows what is not transparent yet.

## Windows Hosted Runner Risk Handling

The Windows job can fail for infrastructure reasons unrelated to BC parity.
Make those failures explicit:

- If Docker is missing or cannot contact the daemon, fail with
  `WINDOWS_RUNNER_DOCKER_UNAVAILABLE`.
- If Docker cannot run a trivial Windows container, fail with
  `WINDOWS_RUNNER_WINDOWS_CONTAINERS_UNAVAILABLE`.
- If BcContainerHelper install/import fails, fail with
  `BC_CONTAINER_HELPER_UNAVAILABLE`.
- If the BC Windows container cannot start, upload all helper logs and fail with
  `WINDOWS_BC_CONTAINER_START_FAILED`.

Do not mark these as parity mismatches. They are lane setup failures.

## Implementation Units

1. `parity/collect-linux-contract.sh`
   - Host-side Bash wrapper around curl, existing scripts, and Python JSON
     normalization.

2. `parity/collect-windows-contract.ps1`
   - PowerShell collector that probes the Windows container and emits the same
     schema.

3. `parity/compare-contracts.py`
   - Loads contract JSON pairs and known deltas.
   - Produces a readable diff.
   - Exits non-zero on unexpected mismatches.

4. `.github/workflows/parity-windows-linux.yml`
   - Orchestrates both platform lanes and comparison.

5. `parity/known-deltas.json`
   - Starts minimal. Expand only when a live run proves a difference is real
     and intentionally deferred.

## Rollout

Phase 1:

- Add workflow as manual-only.
- Matrix: `27.5`, `28.1`.
- Contract: endpoint/auth/company/dev/tests/apps/users.
- No PR gating.

Phase 2:

- Stabilize Windows boot and known deltas from live runs.
- Add scheduled weekly run.
- Add summary annotations for unexpected diffs.

Phase 3:

- Add focused checks for likely hard gaps:
  - WebClient protocol behavior beyond handshake.
  - Report rendering output.
  - Auth mutation/password-change behavior.
  - Windows identity/SID/ACL-visible behavior through AL/.NET probes.

## Success Criteria

- Manual workflow starts both platform lanes for `27.5` and `28.1`.
- Each lane uploads a contract artifact even on BC probe failures when possible.
- The compare job fails on unexpected Linux/Windows differences.
- Known deltas are visible in logs and are not silently ignored.
- Infrastructure failures are clearly separated from parity mismatches.
