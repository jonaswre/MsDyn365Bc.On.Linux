#!/usr/bin/env bash
# Verify that the default SQL data volume survives container recreation.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -z "${SA_PASSWORD:-}" ]; then
    echo "[sql-persistence] ERROR: SA_PASSWORD is required" >&2
    exit 1
fi

MARKER="marker_$(date +%s)_$$"

sqlcmd() {
    docker compose exec -T sql /opt/mssql-tools18/bin/sqlcmd \
        -S localhost \
        -U sa \
        -P "$SA_PASSWORD" \
        -C \
        -No \
        "$@"
}

echo "[sql-persistence] Writing marker before container recreation"
sqlcmd -d CRONUS -b -Q "
SET NOCOUNT ON;
IF OBJECT_ID('dbo.bc_sql_persistence_marker', 'U') IS NULL
    CREATE TABLE dbo.bc_sql_persistence_marker (
        marker NVARCHAR(128) NOT NULL PRIMARY KEY,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );
INSERT INTO dbo.bc_sql_persistence_marker (marker) VALUES (N'$MARKER');
"

echo "[sql-persistence] Recreating containers without removing Docker volumes"
docker compose down
docker compose up -d

"$ROOT_DIR/scripts/wait-for-bc-healthy.sh" 30

echo "[sql-persistence] Checking marker after container recreation"
COUNT="$(sqlcmd -d CRONUS -h -1 -W -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM dbo.bc_sql_persistence_marker WHERE marker = N'$MARKER';" | tr -d '[:space:]')"

if [ "$COUNT" != "1" ]; then
    echo "[sql-persistence] ERROR: persistence marker missing after container recreation" >&2
    exit 1
fi

echo "[sql-persistence] Persistence marker survived container recreation"
