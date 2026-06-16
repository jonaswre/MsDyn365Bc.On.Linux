#!/usr/bin/env bash
set -euo pipefail

requested="${1:-}"
if [ -z "$requested" ] || [ "$requested" = "none" ]; then
    echo "No toolkit apps requested."
    exit 0
fi

docker compose exec -T bc bash -s -- "$requested" <<'BCDEBUG'
set -euo pipefail

requested="$1"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
dev_url="http://localhost:7049/BC/dev"
artifacts="${ARTIFACTS:-/bc/artifacts}"

. /bc/scripts/publish-app.sh

memory_snapshot() {
    local label="$1"
    echo "--- memory: $label ---"
    grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree' /proc/meminfo || true
    pgrep -af 'Microsoft.Dynamics.Nav.Server.dll|dotnet' || true
}

apps_to_publish=$(python3 - "$artifacts" "$requested" <<'PY'
import os
import sys

sys.path.insert(0, "/bc/scripts")
from _bcapp import load_artifact_apps  # noqa

artifact_dir = sys.argv[1]
raw_requested = sys.argv[2]

aliases = {
    "core": [
        "Library Assert",
        "Library Variable Storage",
        "Permissions Mock",
        "Any",
        "System Application Test Library",
        "Business Foundation Test Libraries",
        "Tests-TestLibraries",
    ],
}

requested = []
for item in raw_requested.split(","):
    item = item.strip()
    if not item:
        continue
    requested.extend(aliases.get(item.lower(), [item]))

requested_keys = {item.casefold() for item in requested}
apps = load_artifact_apps(artifact_dir)
selected_ids = {
    app_id
    for app_id, info in apps.items()
    if info.get("name", "").casefold() in requested_keys
}

missing = [
    name
    for name in requested
    if not any(info.get("name", "").casefold() == name.casefold() for info in apps.values())
]
if missing:
    print("Missing toolkit app(s): " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

visited = set()
ordered = []

def visit(app_id):
    if app_id in visited:
        return
    visited.add(app_id)
    info = apps.get(app_id)
    if info is None:
        return
    for dep in info.get("dependencies", []):
        visit(dep.get("id", ""))
    if app_id in selected_ids:
        ordered.append(info)

for app_id in sorted(selected_ids):
    visit(app_id)

for info in ordered:
    print(f"{info['name']}\t{info['path']}")
PY
)

memory_snapshot "before publishing"
while IFS=$'\t' read -r app_name app_path; do
    [ -n "$app_path" ] || continue
    echo "--- publishing: $app_name ---"
    bc_publish_app "$app_path" "$dev_url" "$auth"
    memory_snapshot "after $app_name"
done <<< "$apps_to_publish"
BCDEBUG
