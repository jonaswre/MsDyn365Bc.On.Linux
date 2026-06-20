# Harden Default Runtime Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the default Business Central Linux runtime surface without adding a separate production mode.

**Architecture:** Keep a single default runtime path, but change defaults to fail closed. Sensitive endpoints and compatibility bypasses become explicit opt-ins; CI and examples opt in where they need development/test behavior.

**Tech Stack:** Docker Compose, Bash entrypoint and health scripts, C# startup hook, Python unittest static checks, GitHub Actions and Azure Pipelines YAML.

---

## File Structure

- Modify `docker-compose.yml` for required credentials, loopback host bindings, SQL internalization, and new service-flag env vars.
- Modify `scripts/entrypoint.sh` for credential validation, service flag defaults, non-dev readiness, and dev-publish gating.
- Modify `scripts/healthcheck.sh` and `scripts/verify-network-surface.sh` to probe only enabled services.
- Modify `src/StartupHook/StartupHook.cs` to gate the auth bypass.
- Modify `tests/parity/test_workflows.py` and add focused static tests for hardening defaults.
- Modify `.github/workflows/*.yml`, `examples/**/*.yml`, `README.md`, and related docs to pass explicit credentials and opt into dev/test surfaces where required.

### Task 1: Static Tests For Hardened Defaults

**Files:**
- Modify: `tests/parity/test_workflows.py`

- [ ] **Step 1: Add failing tests**

Add tests that inspect `docker-compose.yml`, `scripts/entrypoint.sh`, and `src/StartupHook/StartupHook.cs` for the hardened defaults:

```python
    def test_compose_requires_explicit_credentials_and_internal_sql(self):
        compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")
        compose = yaml.safe_load(compose_text)

        sql = compose["services"]["sql"]
        bc = compose["services"]["bc"]

        self.assertNotIn("ports", sql)
        self.assertIn("1433", sql.get("expose", []))
        self.assertIn("${SA_PASSWORD:?", compose_text)
        self.assertIn("${BC_USERNAME:?", compose_text)
        self.assertIn("${BC_PASSWORD:?", compose_text)
        self.assertNotIn("Passw0rd123!", compose_text)
        self.assertNotIn("${BC_PASSWORD:-admin}", compose_text)
        self.assertNotIn("${BC_USERNAME:-admin}", compose_text)

    def test_compose_binds_host_ports_to_loopback(self):
        compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
        ports = compose["services"]["bc"]["ports"]

        self.assertTrue(ports)
        for port in ports:
            self.assertTrue(str(port).startswith("127.0.0.1:"), port)

    def test_entrypoint_hardens_dev_test_defaults(self):
        script = Path("scripts/entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('BC_INCLUDE_TEST_TOOLKIT="${BC_INCLUDE_TEST_TOOLKIT:-false}"', script)
        self.assertIn('BC_DEV_SERVICES_ENABLED="${BC_DEV_SERVICES_ENABLED:-false}"', script)
        self.assertIn('BC_MANAGEMENT_SERVICES_ENABLED="${BC_MANAGEMENT_SERVICES_ENABLED:-false}"', script)
        self.assertIn('BC_MANAGEMENT_API_SERVICES_ENABLED="${BC_MANAGEMENT_API_SERVICES_ENABLED:-false}"', script)
        self.assertIn('BC_TEST_AUTOMATION_ENABLED="${BC_TEST_AUTOMATION_ENABLED:-false}"', script)
        self.assertIn("validate_required_credentials", script)
        self.assertIn("Refusing default BC credentials", script)

    def test_auth_bypass_requires_explicit_opt_in(self):
        source = Path("src/StartupHook/StartupHook.cs").read_text(encoding="utf-8")

        self.assertIn("BC_ALLOW_INSECURE_AUTH_BYPASS", source)
        self.assertIn("IsTruthy", source)
        self.assertIn("NavUser.TryAuthenticate bypass disabled", source)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.parity.test_workflows.ParityWorkflowTests \
  -k 'compose_requires_explicit_credentials_and_internal_sql or compose_binds_host_ports_to_loopback or entrypoint_hardens_dev_test_defaults or auth_bypass_requires_explicit_opt_in' -v
```

Expected: failures because the hardening has not been implemented.

### Task 2: Compose And Entrypoint Hardening

**Files:**
- Modify: `docker-compose.yml`
- Modify: `scripts/entrypoint.sh`
- Modify: `scripts/healthcheck.sh`
- Modify: `scripts/verify-network-surface.sh`

- [ ] **Step 1: Implement minimal shell and compose changes**

Change compose to require credentials, remove SQL host publishing, bind BC ports to loopback, and pass service flags. Change entrypoint defaults to disable dev/test surfaces and validate credentials. Change health scripts to probe only enabled surfaces.

- [ ] **Step 2: Run static tests**

Run:

```bash
python3 -m unittest tests.parity.test_workflows.ParityWorkflowTests -v
```

Expected: hardening tests pass; workflow tests may still fail until CI env updates land.

### Task 3: Gate Auth Bypass

**Files:**
- Modify: `src/StartupHook/StartupHook.cs`

- [ ] **Step 1: Implement C# opt-in gate**

Only call `PatchNavUserTryAuthenticate` when `BC_ALLOW_INSECURE_AUTH_BYPASS` is truthy. Add a small local `IsTruthy` helper.

- [ ] **Step 2: Run static tests**

Run:

```bash
python3 -m unittest tests.parity.test_workflows.ParityWorkflowTests.test_auth_bypass_requires_explicit_opt_in -v
```

Expected: pass.

### Task 4: CI And Example Opt-Ins

**Files:**
- Modify: `.github/workflows/bc-test-prebuilt.yml`
- Modify: `.github/workflows/bc-test-from-source.yml`
- Modify: `.github/workflows/test-versions.yml`
- Modify: `.github/workflows/parity-windows-linux.yml`
- Modify: `.github/workflows/linux-startup-debug.yml`
- Modify: `examples/github-workflows/*.yml`
- Modify: `examples/azure-pipelines/*.yml`

- [ ] **Step 1: Update workflows**

Set non-default credentials and opt into `BC_DEV_SERVICES_ENABLED=true`,
`BC_TEST_AUTOMATION_ENABLED=true`, `BC_INCLUDE_TEST_TOOLKIT=true` where tests,
publishing, or symbol download require them. Set management flags only in jobs
that verify or collect the full compatibility surface.

- [ ] **Step 2: Run workflow static tests**

Run:

```bash
python3 -m unittest tests.parity.test_workflows.ParityWorkflowTests -v
```

Expected: pass.

### Task 5: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/WEBCLIENT-POC.md`
- Modify: `examples/github-workflows/README.md`
- Modify: `examples/azure-pipelines/README.md`

- [ ] **Step 1: Update docs**

Document required credentials, loopback-only host bindings, no SQL host port by
default, and explicit dev/test opt-ins.

- [ ] **Step 2: Run final verification**

Run:

```bash
python3 -m unittest discover -s tests -v
docker compose config >/tmp/bc-compose-config.yml
```

Expected: tests pass and compose config renders when required env vars are set
by the command environment.
