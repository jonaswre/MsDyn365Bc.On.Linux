# Repository Instructions For Agents

## Project

This repository runs Microsoft Dynamics 365 Business Central on Linux with Docker Compose. Treat the Docker Compose stack, Bash entrypoint scripts, GitHub Actions workflows, and parity tests as the main production surface.

## Verification Contract

Always verify end-to-end when it is feasible and proportionate to the change. Prefer tests that exercise the project in the most realistic available environment: real containers, real services, real networking, real persistence, and the same scripts users or CI will run. Static tests, syntax checks, mocks, and config rendering are useful guardrails, but they do not replace a live proof for behavior that depends on runtime integration.

Choose verification based on the behavior being changed:

- Container, networking, service startup, or healthcheck changes: start the Compose stack and probe the running services.
- Storage, database, migration, or persistence changes: write data, recreate the relevant service/container, and prove the data survives.
- CI workflow changes: parse the workflow and, when possible, run the same helper script path locally or in a realistic container context.
- Documentation-only changes: run link/config examples when they are executable and cheap enough to validate.

If a full end-to-end run is skipped, state why and make clear which lower-level checks were run instead. Do not describe behavior as production-ready when only static checks were run for a runtime-dependent change.

For this repo's SQL persistence path, a realistic check means starting the Compose stack with explicit non-default credentials and running:

```bash
BC_USERNAME=bcrunner \
BC_PASSWORD='BcRunnerTests!23456' \
SA_PASSWORD='BcRunnerSql!23456' \
docker compose up -d --wait

BC_USERNAME=bcrunner \
BC_PASSWORD='BcRunnerTests!23456' \
SA_PASSWORD='BcRunnerSql!23456' \
./scripts/verify-sql-persistence.sh
```

## Baseline Checks

Before claiming a change is complete, run the smallest relevant set from:

```bash
python3 -m unittest discover -s tests/parity -v
python3 -m unittest discover -s tests -v
bash -n scripts/entrypoint.sh scripts/healthcheck.sh scripts/verify-network-surface.sh scripts/verify-sql-persistence.sh scripts/publish-app.sh scripts/run-tests.sh
BC_USERNAME=bcrunner BC_PASSWORD='BcRunnerTests!23456' SA_PASSWORD='BcRunnerSql!23456' docker compose config >/tmp/bc-compose-config.yml
git diff --check
```

Use the broader set when touching shared scripts, workflows, Compose files, or documentation that describes runtime behavior.
