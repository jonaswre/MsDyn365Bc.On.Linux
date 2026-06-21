# Production-Safe SQL Design

## Goal

Make the default SQL/database runtime safe for durable operation while keeping the existing CI speed tuning available through an explicit opt-in.

## Scope

This change addresses GitHub issue #3. It covers Compose SQL storage, SQL tuning defaults, database login privilege, restart persistence verification, and documentation. It does not add a separate production/development mode; defaults are hardened and unsafe CI shortcuts become named opt-ins.

## Design

The SQL service stores `/var/opt/mssql` in a named Docker volume by default, preserving the SQL data directory with the ownership expected by the Microsoft SQL Server image. `docker compose down` will stop and remove containers while preserving database files; `docker compose down -v` remains the explicit destructive reset path.

The BC entrypoint continues to use `sa` for server-level bootstrap work: waiting for SQL, creating the BC login, and restoring the CRONUS backup. After restore, it maps the BC login into `CRONUS` and grants the database-scoped `db_owner` role required by the service tier. The BC service account no longer joins the SQL Server `sysadmin` role.

SQL performance shortcuts move behind `BC_ENABLE_CI_SQL_TUNING=true`. When unset, the entrypoint leaves Query Store, auto statistics, page verification, delayed durability, and change tracking at the artifact/database defaults. Existing CI workflows and examples may opt into the speed path explicitly because their containers are disposable.

## Verification

Static parity tests assert the Compose and entrypoint defaults: persistent SQL data volume, no SQL `tmpfs`, no `sysadmin`, explicit CI tuning opt-in, and documented backup/restore expectations.

A restart persistence script creates a SQL marker table, runs `docker compose down` without `-v`, recreates the stack, waits for BC health, and verifies the marker survived. This proves the default profile preserves SQL data across container recreation.

## Security Notes

Credentials remain environment-driven and required by Compose. SQL stays internal to the Compose network. The BC database login is scoped to the restored database rather than receiving server-wide administrator rights.
