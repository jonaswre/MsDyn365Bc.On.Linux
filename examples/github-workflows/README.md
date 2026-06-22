# GitHub Actions Starter Workflows For Business Central Containers

Boot and publish AL apps against a Business Central container from your own GitHub
repo. All flavours pull the public
`ghcr.io/jonaswre/msdyn365bc.on.linux/bc-runner` image (no auth needed)
and use the bundled scripts to boot BC and publish your apps. Test execution is
owned by your project's runner against the standard BC container endpoints.

## Recommended: reusable workflow

This repo ships two **reusable workflows** in its own `.github/workflows/`
that you can call from your repo. The consumer file is tiny:

```yaml
# .github/workflows/bc-container.yml
name: BC Container Publish
on: [push, pull_request, workflow_dispatch]
jobs:
  bc-tests:
    uses: jonaswre/MsDyn365Bc.On.Linux/.github/workflows/bc-test-from-source.yml@master
    with:
      bc_version:     "latest"
      app_dirs:       "app"
      test_app_dirs:  "test"
      bc_username:    ${{ vars.BC_USERNAME }}
      bc_password:    ${{ secrets.BC_PASSWORD }}
      sql_sa_password: ${{ secrets.BC_SQL_SA_PASSWORD }}
```

Two flavours are available — pick whichever fits:

| Reusable workflow | When to use |
|---|---|
| `bc-test-from-source.yml` | Compile AL source from your repo, stage symbols from BC artifacts, and publish apps. |
| `bc-test-prebuilt.yml`    | Skip compilation; publish pre-built `.app` files. |

A copy-paste consumer example is in
[`bc-test-using-reusable.yml`](./bc-test-using-reusable.yml).

**Pin to a tag in production.** `@master` is great while iterating, but for
reproducible CI runs swap it for a release tag once one exists
(`@v1`, `@v2.1`, etc.) so an upstream change can't break your pipeline.

### Inputs (from-source)

| Input | Required | Default | Description |
|---|---|---|---|
| `bc_version` | no | `latest` | BC platform version. Use `latest` for the newest artifact, or pin a major/minor/full version such as `28.1`. |
| `bc_country` | no | `w1` | BC country code |
| `bc_type` | no | `onprem` | `onprem` or `sandbox` |
| `bc_username` | **yes** | — | NavUserPassword username for OData/API/Dev/WebClient access |
| `bc_password` | **yes** | — | NavUserPassword password for OData/API/Dev/WebClient access |
| `sql_sa_password` | **yes** | — | SQL Server sa password for the disposable test container |
| `app_dirs` | no | `""` | Space-separated dirs containing `app.json` for production apps |
| `test_app_dirs` | **yes** | — | Space-separated dirs containing `app.json` for test apps |
| `codeunit_range` | no | `""` | Deprecated compatibility input. This workflow no longer ships a test runner. |
| `al_tool_version` | no | *(auto-derived from bc_version)* | AL compiler CLI tool NuGet version. Auto-derived for BC 28 as `17.0.34.45391`. Set explicitly to pin. |
| `preprocessor_symbols` | no | `""` | Comma-separated preprocessor symbols for `/preprocessorsymbols` (e.g. `"BC28PLUS"`). |
| `runtime_version` | no | *(auto-derived from bc_version)* | Override `app.json` `runtime` before compile. Auto-derived when blank for BC 28 as `17.0`. |
| `runner_image` | no | public ghcr.io tag | Override the bc-runner image |
| `runtime_ref` | no | `master` | Git ref of the Business Central runtime to check out for scripts |
| `timeout_minutes` | no | `45` | Job timeout |
| `enable_code_cop` | no | `false` | Enable Microsoft CodeCop |
| `enable_ui_cop` | no | `false` | Enable Microsoft UICop |
| `enable_app_source_cop` | no | `false` | Enable Microsoft AppSourceCop |
| `enable_per_tenant_extension_cop` | no | `false` | Enable Microsoft PerTenantExtensionCop |
| `custom_code_cops` | no | `""` | Comma- or newline-separated list of additional cop DLLs (local paths or `https://` URLs). `.nupkg` URLs are unzipped and every DLL under `lib/net8.0/` is added — point at the [ALCops](https://github.com/ALCops/Analyzers) release nupkg to enable all 7 community cops in one shot. |
| `ruleset_file` | no | `""` | Local path or `https://` URL to a JSON ruleset file. Note: AL compiler accepts JSON only, not the classic Visual Studio XML `.ruleset` format. |
| `enable_external_rulesets` | no | `false` | Required when your `ruleset_file` includes remote rulesets via `includedRuleSets[].path` URL entries (e.g. shared org-wide rulesets). |
| `enable_code_analyzers_on_test_apps` | no | `false` | When `false` (default — matches AL-Go), test app compiles skip the analyzer flags entirely. Production apps still get them. |

### Inputs (prebuilt)

Same as above but with `app_files` / `test_app_files` (paths to `.app`
files) instead of `app_dirs` / `test_app_dirs`, and no `al_tool_version`.

### Secrets (both flavours)

| Secret | Required | Description |
|---|---|---|
| `bc_license` | no | Optional ISV/developer BC license, **base64-encoded**. When set, the workflow imports this license BEFORE NST starts — no boot/import/restart cycle. Leave unset to use the public Cronus license. |

To prepare the secret:

```bash
base64 -w0 < your-license.bclicense | gh secret set BC_LICENSE
```

Then call the reusable workflow with:

```yaml
jobs:
  bc-tests:
    uses: jonaswre/MsDyn365Bc.On.Linux/.github/workflows/bc-test-from-source.yml@master
    with:
      ...
    secrets:
      bc_license: ${{ secrets.BC_LICENSE }}
```


All flavours:

- Boot BC and SQL Server with Docker containers on a standard hosted runner
- Download BC artifacts on demand (~50s on a hosted runner thanks to the
  HTTP/1.1 fix in `download-artifacts.sh`)
- Publish via the BC dev endpoint
- Print the BC log tail on failure

## Setup

1. **Copy** `bc-test-using-reusable.yml` into your repo at
   `.github/workflows/bc-container.yml` (or any name you like).
2. **Edit the `with:` block**:
   - `bc_version`, `bc_country`, `bc_type` — which Microsoft BC build to test
     against. The reusable workflows default to `latest`.
   - **From-source**: `APP_DIRS` and `TEST_APP_DIRS` — space-separated paths
     to directories containing `app.json`.
   - **Pre-built**: `APP_FILES` and `TEST_APP_FILES` — space-separated paths
     to `.app` files in your repo.
   - `bc_username`, `bc_password`, and `sql_sa_password` — pass explicit
     non-default credentials for the disposable CI stack.
3. **Commit & push**. The workflow runs on every push and PR to `main`/`master`,
   plus manually via the Actions tab.

## What's running under the hood

- **Image**: `ghcr.io/jonaswre/msdyn365bc.on.linux/bc-runner:latest` — multi-stage Docker
  image that downloads Microsoft BC artifacts at boot, copies the .NET 8
  service tier into place, applies a startup-hook patch set, restores the
  CRONUS demo DB, and exposes the standard BC service endpoints used by
  container automation: Management 7045, Client Services 7046, SOAP 7047,
  OData 7048, Dev 7049, API 7052, WebClient 7085, and Management API 7086.
- **Repository checkout**: brings in `docker-compose.yml`,
  `download-artifacts.sh`, publish helpers, and the startup scripts.
- **Runtime hardening**: the base Compose file keeps SQL internal, persists
  SQL data by default, and disables dev/test/admin surfaces unless explicitly
  enabled. The reusable CI workflows run disposable containers and opt into
  the CI-only services they need.

## Custom analyzers and rulesets

The from-source flavours support Microsoft's four cops (CodeCop /
AppSourceCop / PerTenantExtensionCop / UICop), arbitrary custom cops,
and JSON rulesets — including rulesets that include remote ones via
`includedRuleSets[].path` URLs.

The reusable workflow exposes them as inputs (see the table above). They
default to off, so existing workflows that don't touch these see zero behaviour
change.

When **any** analyzer is configured, the compile step captures
output and fails the workflow if it sees `AD0001`, `Could not load`,
`BadImageFormatException`, or `PlatformNotSupportedException` — so a
silently-broken cop can never produce a green build with disabled
enforcement. This is the load guarantee these workflows give you on top of
AL compile's normal exit-code semantics.

A worked example using the reusable flavour:

```yaml
jobs:
  bc-tests:
    uses: jonaswre/MsDyn365Bc.On.Linux/.github/workflows/bc-test-from-source.yml@master
    with:
      bc_version:     "latest"
      app_dirs:       "app"
      test_app_dirs:  "test"
      bc_username:    ${{ vars.BC_USERNAME }}
      bc_password:    ${{ secrets.BC_PASSWORD }}
      sql_sa_password: ${{ secrets.BC_SQL_SA_PASSWORD }}
      enable_code_cop: true
      enable_ui_cop: true
      enable_app_source_cop: true
      enable_per_tenant_extension_cop: true
      custom_code_cops: "https://github.com/ALCops/Analyzers/releases/download/v0.6.1/ALCops.Analyzers.0.6.1.nupkg"
      ruleset_file: "rules.rulset.json"
      enable_external_rulesets: true
```

The full design + the six gotchas the empirical investigation surfaced
(JSON-only rulesets, `.rulset.json` typo, no `app.json` ruleset prop,
`/enableexternalrulesets` requirement, `AL1033` is a catch-all error
that hides the real cause, `ALCops.Common.dll` co-load) live in
[`ANALYZER-SUPPORT-PLAN.md`](../../ANALYZER-SUPPORT-PLAN.md).

## Customising further

- **Multiple BC versions**: turn the single job into a matrix on `BC_VERSION`.
  See `.github/workflows/test-versions.yml` in this repo for a
  worked example.
- **Different artifact source**: pass `BC_ARTIFACT_URL=skip` to the BC
  container env and pre-populate `BC_ARTIFACTS_DIR` yourself.
- **Multiple test apps with shared symbols**: build production apps first,
  copy their `.app` outputs into each test app's `.alpackages/` directory
  (the from-source template already does this).
- **Test execution**: run your own test runner against the published standard
  BC endpoints after this workflow has built and published the apps.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `BC unhealthy` after several minutes | Artifact download timed out, or first-boot DB restore is still running. Look at the failure log tail in the workflow output. |
| `publish failed: 422` | App schema conflict — make sure your version number bumps between runs, or set `SchemaUpdateMode=ForceSync` (already the default in these templates). |
| AL compile errors about missing symbols | The "Stage symbols" step could not resolve a declared dependency. Check the consumer `app.json` dependency IDs, the selected artifact version, and the `stage-symbols.py` warning output. |
| Test fails with `serviceConnection` errors | Use the latest `bc-runner` image — `serviceConnection`/TestPage support depends on the current startup-hook patch set. |

## Reporting issues

If a workflow fails in the container workflow layer (not your AL code), open an issue
at <https://github.com/jonaswre/MsDyn365Bc.On.Linux/issues> with:

- Your `env:` block
- The BC version you targeted
- The full failure log tail
