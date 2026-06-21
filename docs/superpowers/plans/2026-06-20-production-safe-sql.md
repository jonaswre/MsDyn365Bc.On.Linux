# Production-Safe SQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden SQL storage and database defaults for production readiness while keeping CI speed tuning explicit.

**Architecture:** Use a persistent named Docker volume for SQL data by default. Keep server-level operations under `sa`, scope the BC service login to the `CRONUS` database, and gate disposable SQL performance tuning behind `BC_ENABLE_CI_SQL_TUNING=true`.

**Tech Stack:** Docker Compose, Bash entrypoint scripts, Python unittest parity tests, GitHub Actions YAML.

---

### Task 1: Static Tests For SQL Safety Defaults

**Files:**
- Modify: `tests/parity/test_workflows.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `docker-compose.yml` does not use SQL `tmpfs`, mounts `bc-sql-data:/var/opt/mssql`, defines the volume, passes `BC_ENABLE_CI_SQL_TUNING`, and that `scripts/entrypoint.sh` no longer grants `sysadmin`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest tests.parity.test_workflows.ParityWorkflowTests.test_compose_uses_persistent_sql_data_volume tests.parity.test_workflows.ParityWorkflowTests.test_entrypoint_gates_ci_sql_tuning_and_avoids_sysadmin -v`

Expected: FAIL because Compose still uses `tmpfs` and the entrypoint still grants `sysadmin`.

### Task 2: Compose And Entrypoint Runtime Changes

**Files:**
- Modify: `docker-compose.yml`
- Modify: `scripts/entrypoint.sh`

- [ ] **Step 1: Replace SQL tmpfs**

Remove the SQL service `tmpfs` entry and add `bc-sql-data:/var/opt/mssql` to SQL volumes. Add `bc-sql-data:` under top-level `volumes`.

- [ ] **Step 2: Add CI tuning environment flag**

Pass `BC_ENABLE_CI_SQL_TUNING: ${BC_ENABLE_CI_SQL_TUNING:-false}` to the BC service.

- [ ] **Step 3: Reduce BC login privilege**

Replace `ALTER SERVER ROLE sysadmin ADD MEMBER [$BC_DB_USER];` with database mapping and a database-scoped owner role after restore:

```sql
USE [CRONUS];
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '$BC_DB_USER')
    CREATE USER [$BC_DB_USER] FOR LOGIN [$BC_DB_USER];
ALTER ROLE [db_owner] ADD MEMBER [$BC_DB_USER];
```

- [ ] **Step 4: Gate CI SQL tuning**

Wrap the existing query store/statistics/page verify/delayed durability/change tracking tuning block in `if is_truthy "${BC_ENABLE_CI_SQL_TUNING:-false}"; then ... else ... fi`.

- [ ] **Step 5: Run targeted tests**

Run the two tests from Task 1 and expect PASS.

### Task 3: Restart Persistence Check

**Files:**
- Create: `scripts/verify-sql-persistence.sh`
- Modify: `tests/parity/test_workflows.py`
- Modify: `.github/workflows/test-versions.yml`

- [ ] **Step 1: Write failing tests**

Add a static test that asserts the script exists and contains `docker compose down` without `-v`, creates a marker, waits for BC health after recreation, and checks the marker. Add a workflow test that asserts `test-container-download` runs the persistence script.

- [ ] **Step 2: Implement script**

Create a Bash script that requires `SA_PASSWORD`, inserts a marker table/value into `CRONUS`, runs `docker compose down`, runs `docker compose up -d`, waits with `scripts/wait-for-bc-healthy.sh`, and verifies the marker still exists.

- [ ] **Step 3: Wire CI**

Call `./scripts/verify-sql-persistence.sh` in the `test-container-download` job after the initial health check.

### Task 4: Documentation And CI Opt-Ins

**Files:**
- Modify: `README.md`
- Modify: `.github/workflows/*.yml`
- Modify: `examples/github-workflows/*.yml`
- Modify: `examples/azure-pipelines/*.yml`
- Modify: `examples/*/README.md`

- [ ] **Step 1: Document SQL durability**

Explain that SQL data lives in `bc-sql-data`, survives `docker compose down`, and is removed only by `docker compose down -v` or volume deletion. Document backup/restore using SQL backup files or preserving the Docker volume.

- [ ] **Step 2: Document CI tuning**

Add `BC_ENABLE_CI_SQL_TUNING` to configuration tables with default `false` and explain it is only for disposable CI/test stacks.

- [ ] **Step 3: Opt CI templates into disposable tuning**

Set `BC_ENABLE_CI_SQL_TUNING=true` in GitHub Actions and Azure examples that run disposable test containers.

### Task 5: Verification

**Files:**
- No code changes

- [ ] **Step 1: Run parity tests**

Run: `python3 -m unittest discover -s tests/parity -v`

- [ ] **Step 2: Run root tests**

Run: `python3 -m unittest discover -s tests -v`

- [ ] **Step 3: Validate shell scripts**

Run: `bash -n scripts/entrypoint.sh scripts/healthcheck.sh scripts/verify-network-surface.sh scripts/verify-sql-persistence.sh`

- [ ] **Step 4: Validate Compose and YAML**

Run Compose config with required credentials and parse workflows/examples as YAML.
