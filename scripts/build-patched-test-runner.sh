#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  scripts/build-patched-test-runner.sh --test-runner-app <path> --package-cache <dir> [--out <path>]

Builds the patched Microsoft Test Runner app used by the container image.
The patch grants Test Runner Extension access to Test Runner internals so the
wrapper can initialize and drain Microsoft code coverage over the network API.

Arguments:
  --test-runner-app  Microsoft_Test Runner_*.app with source included
  --package-cache    Directory containing System/Application symbols
  --out              Output .app path
                     default: extensions/TestRunnerExtension/MicrosoftTestRunnerPatched.app

Environment:
  AL_COMPILER         Optional path/name for the compiler. Use either the
                      AL wrapper or the extracted alc executable.
USAGE
}

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_runner_app=""
package_cache=""
out="$repo_dir/extensions/TestRunnerExtension/MicrosoftTestRunnerPatched.app"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --test-runner-app)
            test_runner_app="${2:-}"
            shift 2
            ;;
        --package-cache)
            package_cache="${2:-}"
            shift 2
            ;;
        --out)
            out="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$test_runner_app" ] || [ -z "$package_cache" ]; then
    usage >&2
    exit 2
fi
if [ ! -f "$test_runner_app" ]; then
    echo "test runner app not found: $test_runner_app" >&2
    exit 1
fi
if [ ! -d "$package_cache" ]; then
    echo "package cache not found: $package_cache" >&2
    exit 1
fi
resolve_al_compiler() {
    if [ -n "${AL_COMPILER:-}" ]; then
        echo "$AL_COMPILER"
        return 0
    fi
    if command -v AL >/dev/null 2>&1; then
        command -v AL
        return 0
    fi
    if command -v alc >/dev/null 2>&1; then
        command -v alc
        return 0
    fi
    return 1
}

compile_al_project() {
    compiler="$1"
    shift

    case "$(basename "$compiler")" in
        AL|AL.exe|al|al.exe)
            "$compiler" compile "$@"
            ;;
        *)
            "$compiler" "$@"
            ;;
    esac
}

if ! al_compiler="$(resolve_al_compiler)"; then
    echo "AL compiler not found. Put AL or alc on PATH, or set AL_COMPILER." >&2
    exit 1
fi

work_dir="$(mktemp -d)"
cleanup() {
    rm -rf "$work_dir"
}
trap cleanup EXIT

python3 - "$test_runner_app" "$work_dir" <<'PY'
import json
import pathlib
import sys
import zipfile
import xml.etree.ElementTree as ET

app_path = pathlib.Path(sys.argv[1])
work_dir = pathlib.Path(sys.argv[2])

with zipfile.ZipFile(app_path) as archive:
    archive.extractall(work_dir)

manifest_path = work_dir / "NavxManifest.xml"
root = ET.fromstring(manifest_path.read_text(encoding="utf-8-sig"))
ns = {"m": "http://schemas.microsoft.com/navx/2015/manifest"}
app = root.find("m:App", ns)
if app is None:
    raise SystemExit("NavxManifest.xml does not contain an App element")

platform = app.attrib.get("Platform", "28.0.0.0")
major = platform.split(".", 1)[0]
runtime = app.attrib.get("Runtime", "17.0")
target = app.attrib.get("Target", "Cloud")

app_json = {
    "id": app.attrib["Id"],
    "name": app.attrib["Name"],
    "publisher": app.attrib["Publisher"],
    "version": app.attrib["Version"],
    "brief": app.attrib.get("Brief", ""),
    "description": app.attrib.get("Description", ""),
    "privacyStatement": app.attrib.get("PrivacyStatement", ""),
    "EULA": app.attrib.get("EULA", ""),
    "help": app.attrib.get("Help", ""),
    "url": app.attrib.get("Url", ""),
    "platform": platform,
    "application": f"{major}.0.0.0",
    "runtime": runtime,
    "target": target,
    "internalsVisibleTo": [
        {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "name": "Test Runner Extension",
            "publisher": "ALDirectCompile",
        }
    ],
    "resourceExposurePolicy": {
        "allowDebugging": True,
        "allowDownloadingSource": True,
        "includeSourceInSymbolFile": True,
    },
    "idRanges": [{"from": 130450, "to": 130480}],
}
(work_dir / "app.json").write_text(json.dumps(app_json, indent=2), encoding="utf-8")
PY

# BC 28 symbols already expose System.TestTools.CodeCoverage."Code Coverage Detailed"
# through Base Application, while the Test Runner source package also contains it.
# Remove the duplicate source object when present so the patched app compiles
# against the same symbol closure used by AL consumers.
rm -f "$work_dir/src/CodeCoverage/CodeCoverageDetailed.XmlPort.al"
rm -f "$work_dir/src/src/CodeCoverage/CodeCoverageDetailed.XmlPort.al"
rm -f "$work_dir/DocComments.xml"

mkdir -p "$(dirname "$out")"
compile_al_project "$al_compiler" "/project:$work_dir" "/packagecachepath:$package_cache" "/out:$out"

echo "wrote $out"
