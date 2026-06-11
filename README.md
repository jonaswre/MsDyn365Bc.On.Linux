# MsDyn365Bc.On.Linux

Run the Microsoft Dynamics 365 Business Central service tier on Linux with
Docker Compose. No fork — the unmodified Microsoft .NET 8 service tier is
patched at runtime so it boots and serves on Linux.

```bash
git clone https://github.com/jonaswre/MsDyn365Bc.On.Linux.git
cd MsDyn365Bc.On.Linux
docker compose up -d --wait
```

The `--wait` flag returns once BC is healthy. **First boot takes ~5 minutes**
(artifact download + database restore + extension compilation). Subsequent
starts take ~1 minute.

When the command returns, BC is running with a CRONUS demo database, dev
endpoint, SOAP, OData, API, and the test toolkit (Test Runner, Library Assert,
Variable Storage, Permissions Mock, Any, System Application Test Library,
Business Foundation Test Libraries, Tests-TestLibraries) all published —
ready for extension development and testing.

`docker compose up -d --wait` uses the container healthcheck, which probes the
published BC network surface: legacy Management on 7045 and Client Services on
7046, plus SOAP, OData, API, DevServices, Management API, and WebClient over
HTTP. For a quick interactive smoke check:

```bash
BC_AUTH="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
curl -sf -u "$BC_AUTH" http://localhost:7048/BC/ODataV4/Company \
  | python3 -c "import sys,json; print('OK:', json.load(sys.stdin)['value'][0]['Name'])"
# → OK: CRONUS International Ltd.
```

To verify the full standard container surface from the host, run:

```bash
scripts/verify-network-surface.sh
```

When ports are remapped, pass the same host-port environment variables used by
Docker Compose, for example `BC_CLIENT_SERVICES_PORT=17046
BC_ODATA_PORT=17048 BC_API_PORT=17052 scripts/verify-network-surface.sh`.

---

## Requirements

Just to start BC and run AL tests:

- Docker with Compose v2
- `python3`, `curl`, `unzip` (used by `scripts/run-tests.sh` for symbol
  parsing and OData calls)
- ~4 GB RAM (2 GB SQL + 1-2 GB BC)
- ~3 GB disk for artifacts (downloaded once, cached in Docker volumes)

That's it — **no .NET SDK on the host is required** for the normal container
surface. Providers and tools should treat the container like a Business Central
container: publish over dev services, talk to OData/API over HTTP, and run
tests through the exposed Client Services/WebClient or OData/API automation
endpoints. `scripts/run-tests.sh` is a repository helper for local diagnostics
and CI.

**Optional — only if you want to compile AL projects from the command line**
without using the VS Code AL extension's F5 build:

- `.NET 8 SDK` plus the AL compiler CLI tool. Pick the version that matches your BC major:

  | BC version | AL runtime (`app.json`) | `al_tool_version` (NuGet) |
  |---|---|---|
  | BC 27.x | `16.0` | `16.2.28.57946` |
  | BC 28.x | `17.0` | `17.0.34.45391` |

  ```bash
  # BC 27.x
  dotnet tool install -g \
    Microsoft.Dynamics.BusinessCentral.Development.Tools.Linux \
    --version 16.2.28.57946
  echo 'export PATH="$HOME/.dotnet/tools:$PATH"' >> ~/.bashrc
  ```

  The reusable workflow (`bc-test-from-source.yml`) auto-derives both values
  from `bc_version` — you only need to set `al_tool_version` explicitly to
  pin a specific build. If you use VS Code with the AL Language extension,
  F5 / Ctrl+F5 publishes via the dev endpoint without the CLI compiler —
  skip this section.

  **Why does the smoke test use `runtime: "14.0"`?**
  `extensions/smoke-test/app.json` uses a deliberately low runtime value so
  the same committed file compiles cleanly against any supported BC version
  without patching. Consumer apps should use the runtime matching their
  minimum supported BC version (auto-derived by the workflow).

---

## Endpoints

After `docker compose up`, these are available:

| Endpoint     | URL                                       | Purpose                              |
|--------------|-------------------------------------------|--------------------------------------|
| Management   | `http://localhost:7045/BC/Management`     | Legacy NAV management endpoint       |
| Client svc   | `http://localhost:7046/BC`                | Client Services compatibility port   |
| SOAP         | `http://localhost:7047/BC/WS`             | SOAP web services                    |
| Dev          | `http://localhost:7049/BC/dev`            | Publish extensions, download symbols |
| OData        | `http://localhost:7048/BC/ODataV4`        | Data access                          |
| API v2.0     | `http://localhost:7052/BC/api/v2.0`       | Business API                         |
| Mgmt API     | `http://localhost:7086/BC`                | Management API service               |
| Client       | `http://localhost:7085/BC/client`         | WebClient / client services          |

**Authentication:** `admin` / `admin` (NavUserPassword) by default.
All BC HTTP endpoints require these Basic credentials, matching the
standard NavUserPassword container surface. Set `BC_USERNAME` and
`BC_PASSWORD` to use custom container credentials.

---

## Local development with VS Code

1. Start BC (from this repo):
   ```bash
   docker compose up -d --wait
   ```

2. In **your AL project**, add a `.vscode/launch.json`:

   ```json
   {
       "version": "0.2.0",
       "configurations": [
           {
               "name": "BC Container",
               "type": "al",
               "request": "launch",
               "server": "http://localhost",
               "serverInstance": "BC",
               "port": 7049,
               "authentication": "UserPassword",
               "startupObjectId": 22,
               "startupObjectType": "Page",
               "breakOnError": "All",
               "launchBrowser": false,
               "enableLongRunningSqlStatements": true,
               "enableSqlInformationDebugger": true
           }
       ]
   }
   ```

   When the AL extension prompts for credentials on first publish, use
   the configured `BC_USERNAME` / `BC_PASSWORD` values, or **`admin`** /
   **`admin`** when you use the defaults.

3. **Download symbols** — `Ctrl+Shift+P` → **AL: Download Symbols**.
   Or manually:

   ```bash
   mkdir -p .alpackages
   BC_AUTH="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
   for app in System "System Application" "Base Application" "Application"; do
     curl -sf -u "$BC_AUTH" \
       "http://localhost:7049/BC/dev/packages?publisher=Microsoft&appName=$(echo $app | sed 's/ /%20/g')&versionText=0.0.0.0" \
       -o ".alpackages/${app}.app"
   done
   ```

4. **Publish + run** — press `F5` (or `Ctrl+F5`) in VS Code. The AL
   extension uses the `launch.json` settings to publish via the dev
   endpoint and open the configured startup page.

---

## Command-line workflow

For pipelines, scripts, and quick edits without VS Code.

**Compile** (after installing the AL compiler — see [Requirements](#requirements)):

```bash
AL compile "/project:." "/packagecachepath:.alpackages" "/out:MyExtension.app"
```

**Publish via dev endpoint:**

```bash
BC_AUTH="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
curl -u "$BC_AUTH" -X POST \
  -F "file=@MyExtension.app;type=application/octet-stream" \
  "http://localhost:7049/BC/dev/apps?SchemaUpdateMode=forcesync"
```

---

## Running AL tests

The test framework (Test Runner, Library Assert, Library Variable Storage,
Permissions Mock, Any) is published automatically on first boot of the BC
container, so a fresh `docker compose up -d --wait` is enough — no extra
setup. Set `BC_INCLUDE_TEST_TOOLKIT=false` to start without that test surface;
startup also clears stock test framework entries so the container does not
expose a partial toolkit.

```bash
# Auto-discover test codeunits from the .app's symbols
./scripts/run-tests.sh --app MyTestApp.app

# Provider/automation path: use the standard Business Central container
# network surface. Publish the app to the dev endpoint, then run tests through
# Client Services/WebClient and the OData automation API.
al run --env my-bc --workspace .
al test --env my-bc --workspace . --suite DEFAULT

# Same, but limit to specific codeunits. --codeunit-range accepts:
#   50000                                  single id
#   50000..50099                           single AL range
#   "50000..50099|130450..130459"          multiple ranges (pipe-separated)
#   "50000,50001,50002"                    explicit ids
#   "50000..50099,130450,200000..210000"   mixed
./scripts/run-tests.sh --app MyTestApp.app --codeunit-range 50000
./scripts/run-tests.sh --app MyTestApp.app --codeunit-range "50000..50099|130450..130459"
```

`run-tests.sh` defaults to the standard container base URL
`http://localhost:7046/BC` and derives the Dev, API, and OData service URLs
from that base. Pass `--base-url` only when the container ports are mapped to
non-default host ports or when running from another container or VM.

When `--app` is provided the script reads `SymbolReference.json` from the
`.app` zip, walks for codeunits with `Subtype = Test`, and intersects with
`--codeunit-range` if also provided. This avoids the SetupSuite call having
to iterate tens of thousands of nonexistent IDs. `--suite` defaults to
`DEFAULT` and is passed through the Test Runner API, so callers can use the
same suite name they use with other Docker-based BC test flows.

Sample output:

```
=== BC Test Runner ===
Company: CRONUS International Ltd.
Test codeunits: 50000,50004
Setting up test suite... OK

=== Running Tests ===
Executing 2 codeunit(s) via OData/API...
total=3 passed=3 failed=0 skipped=0
PASSED  50000.TestCustomerCreation
PASSED  50000.TestSalesOrderPosting
PASSED  50004.TestSomethingElse
```

### JUnit XML output

Pass `--junit-output <path>` to also write per-test results as a JUnit
XML file, compatible with GitHub Checks reporters
([dorny/test-reporter](https://github.com/dorny/test-reporter),
[EnricoMi/publish-unit-test-result-action](https://github.com/EnricoMi/publish-unit-test-result-action)),
the Azure DevOps "Publish Test Results" task, and any other CI tool that
ingests JUnit:

```bash
./scripts/run-tests.sh --app MyTestApp.app --junit-output ./test-results.xml
```

Each codeunit becomes one `<testsuite>`, each `[Test]` procedure one
`<testcase>`. Failing tests carry the BC error message in the `message`
attribute and the full AL call stack in the `<failure>` body.

The reusable workflows (`bc-test-from-source.yml`, `bc-test-prebuilt.yml`)
emit JUnit XML automatically (no opt-in needed) and upload it as a
`junit-test-results` workflow artifact.

`extensions/TestRunnerExtension/MicrosoftTestRunnerPatched.app` is a patched
Microsoft Test Runner build used by the container before publishing the wrapper
extension. It keeps the public test surface network-based and lets the wrapper
initialize and drain Microsoft code coverage through OData/API instead of
container internals. Rebuild it after changing BC artifacts or compiler version:

```bash
scripts/build-patched-test-runner.sh \
  --test-runner-app "extensions/TestRunnerExtension/.alpackages/Microsoft_Test Runner_28.1.49838.51179.app" \
  --package-cache "build/local-smoke/symbols"
```

The package cache must include the platform and application symbols used to
compile Microsoft Test Runner, including `System.app`,
`Microsoft_Application_*.app`, `Microsoft_System Application_*.app`, and
`Microsoft_Business Foundation_*.app`.

For end-to-end CI examples (compile + publish + test on every PR), see
[**Templates for your own repo**](#templates-for-your-own-repo) below.

---

## Configuration

Defaults are in `.env`. Override any variable on the command line without
editing files:

```bash
# Change BC version
BC_VERSION=28.0 docker compose up -d

# Change country
BC_VERSION=27.5 BC_COUNTRY=de docker compose up -d

# Change ports as a set (if defaults conflict)
BC_CLIENT_SERVICES_PORT=17046 \
BC_SOAP_PORT=17047 \
BC_DEV_PORT=17049 \
BC_ODATA_PORT=17048 \
BC_API_PORT=17052 \
BC_MGMT_PORT=17045 \
BC_MGMT_API_PORT=17086 \
BC_CLIENT_PORT=17085 \
  docker compose up -d
```

| Variable                  | Default        | Description                                                                                                                    |
|---------------------------|----------------|--------------------------------------------------------------------------------------------------------------------------------|
| `BC_VERSION`              | `latest`       | BC version (e.g. `27.5`, `28.0`, `latest`, or full like `27.5.46862.48612`)                                                    |
| `BC_COUNTRY`              | `w1`           | Country/region code                                                                                                            |
| `BC_TYPE`                 | `onprem`       | `onprem` or `sandbox`                                                                                                          |
| `ACCEPT_EULA`             | `Y`            | Accept the Microsoft container EULA for SQL/BC startup                                                                         |
| `BC_USERNAME`             | `admin`        | NavUserPassword username for OData/API/Dev/WebClient access                                                                    |
| `BC_PASSWORD`             | `admin`        | NavUserPassword password for OData/API/Dev/WebClient access                                                                    |
| `BC_INCLUDE_TEST_TOOLKIT` | `true`         | Publish the test toolkit and Test Runner API during startup. When `false`, stock test framework entries are cleared as well.    |
| `BC_MEMORY_LIMIT`         | `8G`           | Docker memory limit for the BC service container                                                                               |
| `MSSQL_MEMORY_LIMIT_MB`   | `2048`         | SQL Server memory limit in MB                                                                                                  |
| `BC_DNS`                  | unset          | Optional DNS server for the BC service container                                                                               |
| `BC_API_REQUEST_LIMIT`    | `50`           | OData/API per-user concurrency limit. Set `0` to leave artifact defaults                                                       |
| `SA_PASSWORD`             | `Passw0rd123!` | SQL Server SA password                                                                                                         |
| `SQL_PORT`                | `11433`        | Host port for SQL Server                                                                                                       |
| `BC_CLIENT_SERVICES_PORT` | `7046`         | Host port published for the standard Client Services compatibility endpoint                                                     |
| `BC_SOAP_PORT`            | `7047`         | Host port published for SOAP web services                                                                                      |
| `BC_DEV_PORT`             | `7049`         | Host port published for the Dev endpoint (publish, symbols)                                                                    |
| `BC_ODATA_PORT`           | `7048`         | Host port published for OData v4                                                                                               |
| `BC_API_PORT`             | `7052`         | Host port published for API v2.0 and automation API                                                                            |
| `BC_MGMT_PORT`            | `7045`         | Host port published for the legacy NAV management endpoint                                                                     |
| `BC_MGMT_API_PORT`        | `7086`         | Host port published for the Management API service                                                                             |
| `BC_CLIENT_PORT`          | `7085`         | Host port published for WebClient / client services                                                                            |
| `BC_LICENSE_HOST_PATH`    | unset          | Optional host path to a `.bclicense` file. Mounted into bc + sql containers and imported instead of the default Cronus license. |
| `BC_LICENSE_FILE`         | unset          | Path inside the container of the license file to import. Use `/bc/custom-license.bclicense` together with `BC_LICENSE_HOST_PATH`. |

**Custom license (ISVs / developer license):** by default the entrypoint
imports the public Cronus.bclicense that ships with the BC artifact. To
use your own license without the boot/import/restart cycle:

```bash
BC_LICENSE_HOST_PATH=/path/to/your-license.bclicense \
BC_LICENSE_FILE=/bc/custom-license.bclicense \
docker compose up -d
```

The entrypoint imports the override BEFORE NST starts, so the service
tier comes up with the right license on first boot. The reusable CI
workflows (`bc-test-from-source.yml` / `bc-test-prebuilt.yml`) accept
the same license via a `bc_license` secret (base64-encoded).

**Reset state:** `docker compose down -v` removes the containers *and* the
named volumes (`bc-artifacts`, `bc-service`), forcing a fresh artifact
download and BAK restore on the next `up`. Use this when you've changed
something the entrypoint guards on existing files (`/bc/service`,
patched DLLs).

---

## Templates for your own repo

This repo ships starter CI/CD templates so downstream projects can run AL
tests against a Business Central container without forking or copy-pasting
hundreds of lines of YAML. The image at
`ghcr.io/jonaswre/msdyn365bc.on.linux/bc-runner:latest` is publicly
accessible — no GHCR auth needed.

| Path                                         | What it is                                                                |
|----------------------------------------------|---------------------------------------------------------------------------|
| `examples/github-workflows/`                 | GitHub Actions starters (inlined templates + reusable workflow examples)  |
| `examples/azure-pipelines/`                  | Azure DevOps starter pipelines (inlined `azure-pipelines.yml` examples)   |
| `.github/workflows/bc-test-from-source.yml`  | Reusable GitHub workflow — compiles AL source from your repo              |
| `.github/workflows/bc-test-prebuilt.yml`     | Reusable GitHub workflow — publishes pre-built `.app` files               |

Cleanest consumer experience (10-line `.github/workflows/bc-test.yml`):

```yaml
name: BC Tests
on: [push, pull_request, workflow_dispatch]
jobs:
  bc-tests:
    uses: jonaswre/MsDyn365Bc.On.Linux/.github/workflows/bc-test-from-source.yml@master
    with:
      bc_version:     "latest"
      app_dirs:       "app"
      test_app_dirs:  "test"
      codeunit_range: "50000..99999"
```

See [`examples/github-workflows/README.md`](./examples/github-workflows/README.md)
and [`examples/azure-pipelines/README.md`](./examples/azure-pipelines/README.md)
for full input documentation, troubleshooting, and inlined alternatives.

---

## GitHub Codespaces

This repository includes a devcontainer at `.devcontainer/devcontainer.json`.
Open it in a Codespace and BC starts automatically via Docker-in-Docker.
The AL Language extension is pre-installed.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/jonaswre/MsDyn365Bc.On.Linux)

---

## Running multiple instances

Run multiple BC environments side-by-side by giving each stack a unique
project name and port set. Docker Compose uses the project name to
namespace all containers, networks, and volumes.

```bash
# Instance 1: BC 27.5 on default ports
docker compose -p bc275 up -d --wait

# Instance 2: BC 28.0 on offset ports
COMPOSE_PROJECT_NAME=bc280 \
BC_VERSION=28.0 \
SQL_PORT=21433 \
BC_CLIENT_SERVICES_PORT=17046 \
BC_SOAP_PORT=17047 \
BC_DEV_PORT=17049 \
BC_ODATA_PORT=17048 \
BC_API_PORT=17052 \
BC_MGMT_PORT=17045 \
BC_MGMT_API_PORT=17086 \
BC_CLIENT_PORT=17085 \
  docker compose up -d --wait
```

Each instance gets its own containers (`bc275-bc-1`, `bc280-bc-1`),
volumes, and network. Manage them independently:

```bash
docker compose -p bc275 logs -f     # logs for instance 1
docker compose -p bc280 down        # stop instance 2
docker compose -p bc275 down -v     # stop instance 1 + wipe its volumes
```

**Important:** every port must be unique across instances — you'll get a
bind error if two instances try to map the same host port. The easiest
approach is to pick a port offset (e.g. +10000) for each additional
instance.

For convenience, you can keep a per-instance `.env` file:

```bash
# .env.bc280
BC_VERSION=28.0
SQL_PORT=21433
BC_CLIENT_SERVICES_PORT=17046
BC_SOAP_PORT=17047
BC_DEV_PORT=17049
BC_ODATA_PORT=17048
BC_API_PORT=17052
BC_MGMT_PORT=17045
BC_MGMT_API_PORT=17086
BC_CLIENT_PORT=17085
```

```bash
docker compose -p bc280 --env-file .env.bc280 up -d --wait
```

---

## How it works

The BC service tier is a .NET 8 application designed for Windows. This
project patches it to run on Linux using a [.NET startup hook](https://learn.microsoft.com/en-us/dotnet/core/runtime-config/debugging-profiling#startup-hooks)
that intercepts and fixes Windows-specific calls at runtime:

- **Win32 P/Invoke stubs** — `kernel32.dll`, `user32.dll`, `advapi32.dll`
  etc. redirected to a shared library with Linux-compatible
  implementations
- **Assembly resolution** — .NET reference assemblies and type-forward
  merging for Cecil-based compilation
- **Service stubs** — `HttpSys` → Kestrel redirect, `PerformanceCounter`,
  `WindowsIdentity`, `Geneva ETW` stubs
- **Binary patches** — `CodeAnalysis.dll` and `Mono.Cecil.dll` fixes for
  type-forwarding resolution on Linux
- **Runtime AL fixes** — patches for Word picture-merger recursion, task
  page UI handler, and ~20 other Windows-only assumptions in the BC
  runtime

The full patch list is at the top of `src/StartupHook/StartupHook.cs`.
Known limitations are in [`KNOWN-LIMITATIONS.md`](./KNOWN-LIMITATIONS.md).
The SQL Server runs as a separate container using the official
`mssql/server:2022` Linux image.

---

## CI / version support

The `Test BC Versions` workflow (`.github/workflows/test-versions.yml`)
runs the full container build + smoke test sweep across multiple BC
versions. Trigger it manually with custom versions:

```
versions: "27.0,27.5,28.0"
```

The published image is `ghcr.io/jonaswre/msdyn365bc.on.linux/bc-runner`
(public). The `:latest` tag tracks `master`.
