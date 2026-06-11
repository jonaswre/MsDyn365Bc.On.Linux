#!/usr/bin/env bash
# run-tests.sh — Run AL tests through a Business Central network endpoint
#
# Network execution: OData/API for setup, execution, and result reading.
# This mirrors the public Business Central container surface: callers connect
# over HTTP endpoints and do not need shell access to the container.
#
# Usage:
#   ./scripts/run-tests.sh [options]
#
# Options:
#   --app <path>               Test app file (auto-published + codeunit discovery)
#   --codeunit-range <range>   Codeunit IDs to test. Accepts:
#                                "70000"                            single id
#                                "70000..70010"                     AL range
#                                "50000..50100|130450..130459"      multiple ranges (pipe)
#                                "50000,50001,50002"                explicit ids
#                                "50000..50100,130450,200000..210000" mixed
#   --junit-output <path>      Write per-test results as JUnit XML to <path>.
#                                Compatible with GitHub Checks reporters
#                                (dorny/test-reporter, EnricoMi/publish-unit-test-result-action),
#                                Azure DevOps "Publish Test Results" task, and
#                                AL-Go's AnalyzeTests post-step. Default off.
#   --company <name>           Company name (default: auto-detect)
#   --tenant <name>            BC tenant (default: default)
#   --suite <name>             Test suite name (default: DEFAULT)
#   --base-url <url>           BC base URL (default: http://localhost:7046/BC)
#   --dev-url <url>            BC Dev endpoint (default: derived from --base-url)
#   --auth <user:pass>         Authentication (default: $BC_USERNAME:$BC_PASSWORD or admin:admin)
#   --timeout <minutes>        Overall timeout (default: 30)
#   --test-runner-app <path>   TestRunnerExtension .app (auto-detected)

set -uo pipefail

# CDPATH makes cd print the resolved directory, which corrupts $(cd ... && pwd)
unset CDPATH

# === Configuration & CLI Parsing ===
BASE_URL="http://localhost:7046/BC"
DEV_URL=""
DEV_URL_SET=false
AUTH="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
COMPANY=""
TENANT="${BC_TENANT:-default}"
CODEUNIT_RANGE=""
APP_FILE=""
TIMEOUT_MIN=30
SUITE_NAME="DEFAULT"
DISABLED_TESTS_DIR=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_RUNNER_APP=""
JUNIT_OUTPUT=""

show_help() {
    cat <<'HELPEOF'
Usage: run-tests.sh [options]

Options:
  --app <path>               Test app file (auto-published + codeunit discovery)
  --codeunit-range <range>   Codeunit IDs to test. Accepts:
                               "70000"                            single id
                               "70000..70010"                     AL range
                               "50000..50100|130450..130459"      multiple ranges (pipe)
                               "50000,50001,50002"                explicit ids
                               "50000..50100,130450,200000..210000" mixed
  --junit-output <path>      Write per-test results as JUnit XML to <path>
  --company <name>           Company name (default: auto-detect)
  --tenant <name>            BC tenant (default: default)
  --suite <name>             Test suite name (default: DEFAULT)
  --base-url <url>           BC service base URL (default: http://localhost:7046/BC)
  --dev-url <url>            BC Dev endpoint (default: derived from --base-url)
  --auth <user:pass>         Authentication (default: \$BC_USERNAME:\$BC_PASSWORD or admin:admin)
  --timeout <minutes>        Overall timeout (default: 30)
  --test-runner-app <path>   TestRunnerExtension .app (auto-detected)
  --disabled-tests <dir>     Directory with DisabledTests JSON files
  -h, --help                 Show this help

Examples:
  # Run tests from a compiled .app
  ./scripts/run-tests.sh --app MyTestApp.app --codeunit-range 50000..50100

  # Remote BC (e.g. from a devcontainer / Codespace)
  ./scripts/run-tests.sh --base-url http://172.17.0.1:7046/BC --app MyTestApp.app
HELPEOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help) show_help;;
        --app) APP_FILE="$2"; shift 2;;
        --codeunit-range) CODEUNIT_RANGE="$2"; shift 2;;
        --company) COMPANY="$2"; shift 2;;
        --tenant) TENANT="$2"; shift 2;;
        --suite|--suite-name) SUITE_NAME="$2"; shift 2;;
        --base-url) BASE_URL="$2"; shift 2;;
        --dev-url) DEV_URL="$2"; DEV_URL_SET=true; shift 2;;
        --auth) AUTH="$2"; shift 2;;
        --timeout) TIMEOUT_MIN="$2"; shift 2;;
        --test-runner-app) TEST_RUNNER_APP="$2"; shift 2;;
        --disabled-tests) DISABLED_TESTS_DIR="$2"; shift 2;;
        --junit-output) JUNIT_OUTPUT="$2"; shift 2;;
        --host|--test-runner|--codeunit-timeout|--extension-id|--sql-password) shift 2;;
        *) echo "Unknown option: $1 (try --help)"; exit 1;;
    esac
done

echo "=== BC Test Runner ==="

# --- Helper ---
py3() { env -u PYTHONHOME -u PYTHONPATH python3 "$@"; }

derive_api_base_candidates() {
    py3 - "$BASE_URL" <<'PYEOF'
import sys
from urllib.parse import urlsplit, urlunsplit

raw = sys.argv[1].rstrip("/")
parts = urlsplit(raw)
if parts.scheme not in ("http", "https") or not parts.hostname:
    print(raw)
    raise SystemExit(0)

path = parts.path
if path.lower().endswith("/client"):
    path = path[:-7] or "/"

def is_standard_bc_base(path):
    normalized = path.rstrip("/").lower()
    return normalized == "/bc"

def is_known_bc_service_port(port):
    return ((7000 <= port < 8000) or port >= 10000) and port % 100 in (45, 46, 47, 48, 49, 52, 85, 86)

def add_port(ports, port):
    if port not in ports:
        ports.append(port)

ports = []
if parts.port is None:
    if is_standard_bc_base(path):
        add_port(ports, 7052)
        add_port(ports, 7048)
    else:
        add_port(ports, None)
elif is_known_bc_service_port(parts.port):
    add_port(ports, parts.port - (parts.port % 100) + 52)
    add_port(ports, parts.port - (parts.port % 100) + 48)
else:
    add_port(ports, parts.port)

seen = set()
for port in ports:
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    candidate = urlunsplit((parts.scheme, netloc, path, "", "")).rstrip("/")
    if candidate not in seen:
        seen.add(candidate)
        print(candidate)
PYEOF
}

derive_dev_url() {
    py3 - "$BASE_URL" <<'PYEOF'
import sys
from urllib.parse import urlsplit, urlunsplit

raw = sys.argv[1].rstrip("/")
parts = urlsplit(raw)
if parts.scheme not in ("http", "https") or not parts.hostname:
    print(raw)
    raise SystemExit(0)

path = parts.path.rstrip("/") or "/"
lower_path = path.lower()
if lower_path.endswith("/client"):
    path = path[:-7].rstrip("/") or "/"
    lower_path = path.lower()

if not lower_path.endswith("/dev"):
    path = f"{path}/dev" if path != "/" else "/dev"

port = parts.port
if port is None:
    service_tier_path = path[:-4].rstrip("/") or "/" if path.lower().endswith("/dev") else path
    if service_tier_path.rstrip("/").lower() == "/bc":
        port = 7049
elif ((7000 <= port < 8000) or port >= 10000) and port % 100 in (45, 46, 47, 48, 49, 52, 85, 86):
    port = port - (port % 100) + 49

host = parts.hostname
if ":" in host and not host.startswith("["):
    host = f"[{host}]"
netloc = host if port is None else f"{host}:{port}"
print(urlunsplit((parts.scheme, netloc, path, "", "")).rstrip("/"))
PYEOF
}

if [ "$DEV_URL_SET" != "true" ]; then
    DEV_URL="$(derive_dev_url)"
fi

# --- Find TestRunnerExtension .app ---
if [ -z "$TEST_RUNNER_APP" ]; then
    if [ -f /bc/testrunner/TestRunner.app ]; then
        TEST_RUNNER_APP="/bc/testrunner/TestRunner.app"
    else
        TEST_RUNNER_APP="$REPO_DIR/extensions/TestRunnerExtension/TestRunnerExtension.app"
    fi
fi
if [ ! -f "$TEST_RUNNER_APP" ]; then
    echo "ERROR: TestRunnerExtension .app not found at $TEST_RUNNER_APP"
    exit 1
fi

# === Company Auto-Detection ===
COMPANIES_JSON=""
while IFS= read -r candidate; do
    [ -z "$candidate" ] && continue
    COMPANIES_JSON=$(curl -sf --max-time 10 -u "$AUTH" "${candidate}/api/v2.0/companies?tenant=${TENANT}" 2>/dev/null || true)
    [ -n "$COMPANIES_JSON" ] && break
done <<EOF
$(derive_api_base_candidates)
EOF
if [ -z "$COMPANIES_JSON" ]; then
    while IFS= read -r candidate; do
        [ -z "$candidate" ] && continue
        COMPANIES_JSON=$(curl -sf --max-time 10 -u "$AUTH" "${candidate}/ODataV4/Company?tenant=${TENANT}" 2>/dev/null || true)
        [ -n "$COMPANIES_JSON" ] && break
    done <<EOF
$(derive_api_base_candidates)
EOF
fi
if [ -z "$COMPANIES_JSON" ]; then
    echo "ERROR: Cannot reach BC. Is it running?"
    exit 1
fi

COMPANY_AUTO=$(echo "$COMPANIES_JSON" | py3 -c "import sys,json; c=json.load(sys.stdin)['value'][0]; m={k.lower():v for k,v in c.items()}; print(m.get('name',''))" 2>/dev/null || true)
COMPANY_ID=$(echo "$COMPANIES_JSON" | py3 -c "import sys,json; c=json.load(sys.stdin)['value'][0]; m={k.lower():v for k,v in c.items()}; print(m.get('id',m.get('systemid','')))" 2>/dev/null || true)
[ -z "$COMPANY" ] && COMPANY="${COMPANY_AUTO:-CRONUS International Ltd.}"

if [ -z "$COMPANY_ID" ]; then
    while IFS= read -r candidate; do
        [ -z "$candidate" ] && continue
        COMPANY_ID=$(curl -sf --max-time 10 -u "$AUTH" "${candidate}/api/v2.0/companies?tenant=${TENANT}" 2>/dev/null \
            | py3 -c "import sys,json; ms=[{k.lower():v for k,v in c.items()} for c in json.load(sys.stdin)['value']]; [print(m.get('id',m.get('systemid',''))) for m in ms if m.get('name','')==sys.argv[1]]" "$COMPANY" 2>/dev/null || true)
        [ -n "$COMPANY_ID" ] && break
    done <<EOF
$(derive_api_base_candidates)
EOF
fi
if [ -z "$COMPANY_ID" ]; then
    echo "ERROR: Could not get company ID for '$COMPANY'"
    exit 1
fi
echo "Company: $COMPANY ($COMPANY_ID)"

# === Determine API Base URL ===
API_PORT_BASE=""
while IFS= read -r candidate; do
    [ -z "$candidate" ] && continue
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -u "$AUTH" \
        "${candidate}/api/custom/automation/v1.0/companies(${COMPANY_ID})/codeunitRunRequests" 2>/dev/null || echo "000")
    if [ "$HTTP" = "200" ]; then
        API_PORT_BASE="$candidate"
        break
    fi
done <<EOF
$(derive_api_base_candidates)
EOF
[ -z "$API_PORT_BASE" ] && API_PORT_BASE="$BASE_URL"
API_BASE="${API_PORT_BASE}/api/custom/automation/v1.0/companies(${COMPANY_ID})"

# Source the shared publish helper. Both this script and downstream
# consumers (e.g. bc-copilot-blueprint's iterate.sh) call into the same
# function so the 422-body-inspection / "missing dependency" logic only
# lives in one place.
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/publish-app.sh"

# === Ensure TestRunnerExtension Published ===
echo -n "Checking TestRunner API... "
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -u "$AUTH" "${API_BASE}/codeunitRunRequests" 2>/dev/null || echo "000")
if [ "$HTTP" = "200" ]; then
    echo "available"
else
    echo "not found, publishing..."
    bc_publish_app "$TEST_RUNNER_APP" "$DEV_URL" "$AUTH" || exit 1
    echo -n "  Waiting for API..."
    for i in $(seq 1 30); do
        sleep 2
        HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -u "$AUTH" "${API_BASE}/codeunitRunRequests" 2>/dev/null || echo "000")
        [ "$HTTP" = "200" ] && break
        echo -n "."
    done
    echo ""
    [ "$HTTP" != "200" ] && echo "ERROR: TestRunner API not available" && exit 1
    echo "  API ready"
fi

# === Publish Test App (if provided) ===
if [ -n "$APP_FILE" ] && [ -f "$APP_FILE" ]; then
    echo -n "Publishing $(basename "$APP_FILE")... "
    if bc_publish_app "$APP_FILE" "$DEV_URL" "$AUTH"; then
        echo "OK"
    else
        # bc_publish_app already printed the diagnostic body and likely
        # cause hints; just bail.
        exit 1
    fi
fi

# === Discover Test Codeunit IDs ===
#
# Strategy: when an --app is provided, ALWAYS read SymbolReference.json from
# the .app and extract the actual Test codeunit IDs. If --codeunit-range is
# *also* provided, intersect the discovered IDs with that range. This avoids
# the SetupSuite iterating tens of thousands of nonexistent IDs (each
# AddTestCodeunit call costs ~3-5 SQL ops, so a literal "50000-99999" range
# expands to ~250k SQL ops, which times out long before completing).
#
# When only --codeunit-range is provided (no .app to discover from), fall
# back to the literal range — this is for ad-hoc usage and large
# Microsoft-shipped test apps where the .app may not be on the host.
CODEUNIT_IDS=""

# Parse --codeunit-range into a normalized "lo-hi,lo-hi,id,..." form.
#
# Accepted user-facing input shapes:
#   "50000"                            single id
#   "50000..70000"                     single AL range
#   "50000..50100|130450..130459"      multiple ranges (pipe-separated)
#   "50000,50001,50002"                explicit ids (comma-separated)
#   "50000..50100,130450,200000..210000"  mixed
#
# Normalization (so the Python filter below sees one canonical form):
#   1. Replace `|` separator with `,` so all parts join into one list
#   2. Replace `..` AL range syntax with `-` so each part is `lo-hi` or `id`
#
# The Python parser at the discovery step already loops over comma-separated
# parts and handles `lo-hi` ranges and bare ids; this is just the bash side.
NORMALIZED_RANGE=""
if [ -n "$CODEUNIT_RANGE" ]; then
    NORMALIZED_RANGE=$(printf '%s' "$CODEUNIT_RANGE" | sed -e 's/|/,/g' -e 's/\.\./-/g')
fi

if [ -n "$APP_FILE" ] && [ -f "$APP_FILE" ]; then
    echo -n "Discovering test codeunits from $(basename "$APP_FILE")"
    [ -n "$NORMALIZED_RANGE" ] && echo -n " (filter: $NORMALIZED_RANGE)"
    echo -n "... "
    CODEUNIT_IDS=$(unzip -p "$APP_FILE" SymbolReference.json 2>/dev/null | RANGE="$NORMALIZED_RANGE" py3 -c "
import os, sys, json
raw = sys.stdin.read()
if not raw.strip(): sys.exit(0)
data = json.loads(raw.lstrip('\ufeff'))

# Parse the optional range filter into a set/range list of allowed IDs.
filt = os.environ.get('RANGE', '').strip()
allowed = None  # None = no filter
if filt:
    allowed = set()
    for part in filt.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            lo, hi = part.split('-', 1)
            try:
                allowed.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        else:
            try:
                allowed.add(int(part))
            except ValueError:
                pass

ids = []
def collect(node):
    for cu in node.get('Codeunits', []):
        props = {p['Name']: p['Value'] for p in cu.get('Properties', [])}
        if props.get('Subtype') != 'Test':
            continue
        cuid = cu.get('Id')
        if allowed is not None and cuid not in allowed:
            continue
        ids.append(str(cuid))
    for ns in node.get('Namespaces', []):
        collect(ns)
collect(data)
print(','.join(ids))
" 2>/dev/null || true)
    echo "${CODEUNIT_IDS:-none found}"
fi

# Fall back to literal range expansion if no .app was provided.
if [ -z "$CODEUNIT_IDS" ] && [ -n "$NORMALIZED_RANGE" ] && [ -z "$APP_FILE" ]; then
    CODEUNIT_IDS="$NORMALIZED_RANGE"
fi

if [ -z "$CODEUNIT_IDS" ]; then
    echo "ERROR: No test codeunits found. Provide --app (with test codeunits in the symbol) or --codeunit-range"
    exit 1
fi
echo "Test codeunits: $CODEUNIT_IDS"

# === Setup Test Suite via Network API ===
echo -n "Setting up test suite... "
CREATE_BODY=$(mktemp)
CREATE_JSON="{\"CodeunitIds\": \"$CODEUNIT_IDS\"}"
if [ "$SUITE_NAME" != "DEFAULT" ]; then
    CREATE_JSON="{\"CodeunitIds\": \"$CODEUNIT_IDS\", \"SuiteName\": \"$SUITE_NAME\"}"
fi
CREATE_HTTP=$(curl -s -o "$CREATE_BODY" -w "%{http_code}" --max-time 15 -u "$AUTH" -X POST \
    -H "Content-Type: application/json" \
    -d "$CREATE_JSON" \
    "${API_BASE}/codeunitRunRequests" 2>/dev/null)
REQUEST_ID=$(py3 -c "import sys,json; print(json.load(sys.stdin)['Id'])" < "$CREATE_BODY" 2>/dev/null || true)

if [ -z "$REQUEST_ID" ]; then
    echo "FAIL (could not create request)"
    echo ""
    echo "ERROR: POST ${API_BASE}/codeunitRunRequests"
    echo "       HTTP code: $CREATE_HTTP"
    echo "       Request body: $CREATE_JSON"
    echo "       Response body:"
    sed 's/^/         /' "$CREATE_BODY"
    echo ""
    echo "       Likely causes:"
    echo "         - The custom TestRunner extension (page 99902 'Codeunit Run Requests')"
    echo "           is not installed for the default tenant. Check 'Test Runner Extension'"
    echo "           in entrypoint.sh's BC startup logs."
    echo "         - The API endpoint we're hitting (\$API_PORT_BASE) is wrong for"
    echo "           POST in this BC version (the auto-detection picked an endpoint that"
    echo "           accepts GET on this path but not POST)."
    echo "         - The request body schema doesn't match the page's bound action."
    rm -f "$CREATE_BODY"
    exit 1
fi
rm -f "$CREATE_BODY"

SETUP_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 -u "$AUTH" -X POST \
    "${API_BASE}/codeunitRunRequests(${REQUEST_ID})/Microsoft.NAV.setupSuite" 2>/dev/null)
if [ "$SETUP_HTTP" != "200" ] && [ "$SETUP_HTTP" != "204" ]; then
    echo "FAIL (HTTP $SETUP_HTTP)"
    exit 1
fi

# === Verify the suite was populated ===
#
# setupSuite returns 200 even when it ended up populating the suite with
# zero codeunits. That happens when the test app was published just before
# this script ran but BC's metadata cache hasn't propagated the new
# codeunits to the test framework yet — a race we've observed when
# run-tests.sh is invoked back-to-back from a fast inner loop like
# bc-copilot-blueprint's iterate.sh.
#
    # Diagnostic signature of the race: the network runner reports
    # "total=0 passed=0 failed=0 skipped=0", then exits 1.
#
# Fix: query the testResults endpoint (which exposes Test Method Line
# rows from the suite). If empty, re-call setupSuite up to a handful of
# times with a small delay — usually the metadata catches up within a
# second or two.
VERIFY_LAST_RESPONSE=""
VERIFY_LAST_DETAIL=""
verify_suite_populated() {
    local req_id="$1"
    local expected_ids="$2"   # comma-separated codeunit IDs we care about
    # Up to 20 attempts × 2s sleep = ~40s of patience. The race the loop
    # is guarding against (publish → install → metadata propagation) is
    # usually <5s on a normal runner, but can stretch on faster I/O
    # environments where there is no accidental slack between publish
    # and the first setupSuite call.
    #
    # Stricter than "any row exists in the suite": only counts FUNCTION-type
    # rows that mention OUR expected codeunit IDs. setupSuite inserts a
    # placeholder Codeunit-type row even when the test app's metadata
    # isn't loaded (so the codeunit registration exists but with zero
    # [Test] functions). Without filtering by line type the lenient
    # check accepts those stubs and lets the runner produce "0 results"
    # silently — exactly the failure mode we're guarding against.
    local attempt
    for attempt in $(seq 1 20); do
        VERIFY_LAST_RESPONSE=$(curl -sf --max-time 10 -u "$AUTH" \
            "${API_BASE}/testResults?\$filter=testSuite%20eq%20%27${SUITE_NAME}%27%20and%20lineType%20eq%20%27Function%27&\$top=500" 2>/dev/null || true)
        if [ -n "$VERIFY_LAST_RESPONSE" ]; then
            VERIFY_LAST_DETAIL=$(echo "$VERIFY_LAST_RESPONSE" | EXPECTED="$expected_ids" py3 -c "
import os, sys, json
try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f'parse-error: {e}'); sys.exit(0)
rows = data.get('value', [])
# Only Function-type rows count — Codeunit-type rows are populated by
# setupSuite even when the actual codeunit metadata isn't loaded.
present = {str(r.get('testCodeunit', '')).strip() for r in rows if r.get('testCodeunit')}
expected = {x.strip() for x in os.environ.get('EXPECTED', '').split(',') if x.strip()}
matched = present & expected
print(f'function_rows={len(rows)} distinct_codeunits={len(present)} expected={len(expected)} matched={len(matched)} sample={sorted(present)[:5]}')
sys.exit(0 if matched else 1)
" 2>&1)
            local rc=$?
            echo "  [verify attempt $attempt] $VERIFY_LAST_DETAIL"
            if [ "$rc" = "0" ]; then
                return 0
            fi
        else
            echo "  [verify attempt $attempt] testResults endpoint returned no body (curl failed)"
        fi
        # Suite still empty for our codeunits — re-call setupSuite. The
        # metadata may have synced since the previous attempt.
        curl -s -o /dev/null --max-time 60 -u "$AUTH" -X POST \
            "${API_BASE}/codeunitRunRequests(${req_id})/Microsoft.NAV.setupSuite" 2>/dev/null
        sleep 2
    done
    return 1
}

echo ""   # newline so per-attempt logs are readable
if ! verify_suite_populated "$REQUEST_ID" "$CODEUNIT_IDS"; then
    echo ""
    echo "ERROR: setupSuite returned 200 but the ${SUITE_NAME} test suite never"
    echo "       contained any of the expected codeunits after 20 retries (~40s)."
    echo ""
    echo "       Expected codeunit IDs: $CODEUNIT_IDS"
    echo ""
    echo "       Last verify detail:    $VERIFY_LAST_DETAIL"
    echo ""
    echo "       Last raw testResults response (truncated to 800 chars):"
    echo "         URL: ${API_BASE}/testResults?\$filter=testSuite%20eq%20'${SUITE_NAME}'&\$top=500"
    echo "         Body: ${VERIFY_LAST_RESPONSE:0:800}"
    echo ""
    echo "       Possible causes:"
    echo "         - Test app failed to install for the default tenant"
    echo "           (publish succeeds without installing in some tenant configurations)"
    echo "         - Expected codeunit IDs are not in the published .app"
    echo "         - setupSuite is reading from a different tenant than testResults"
    echo "         - The test framework republish step in entrypoint.sh failed silently"
    echo ""
    echo "       Cross-check installed extensions:"
    echo "         curl -u $AUTH '${BASE_URL}/api/v2.0/extensionDeployments'"
    exit 1
fi
echo "Test suite populated."

# === Disable Known-Failing Tests ===
if [ -n "$DISABLED_TESTS_DIR" ] && [ -d "$DISABLED_TESTS_DIR" ]; then
    # Read each DisabledTests JSON file individually (concatenating produces invalid JSON)
    # BCApps format per file: [{"codeunitId": 132920, "method": "TestName"}, ...]
    DISABLED_ENTRIES=$(py3 -c "
import json, glob, os, sys

pairs = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        with open(f) as fh:
            entries = json.load(fh)
        if not isinstance(entries, list):
            entries = [entries]
        for e in entries:
            cu = e.get('codeunitId', 0)
            method = e.get('method', e.get('Method', ''))
            if cu and method:
                pairs.append(f'{cu}:{method}')
    except Exception as ex:
        pass

# Split into chunks of max 2000 chars (API field limit)
chunks, current = [], ''
for p in pairs:
    if len(current) + len(p) + 1 > 2000:
        chunks.append(current)
        current = p
    else:
        current = f'{current},{p}' if current else p
if current:
    chunks.append(current)
for c in chunks:
    print(c)
" "$DISABLED_TESTS_DIR" 2>/dev/null)
    echo "Parsed disabled tests from $(find "$DISABLED_TESTS_DIR" -name '*.json' | wc -l) files"

    if [ -n "$DISABLED_ENTRIES" ]; then
        DISABLED_COUNT=0
        while IFS= read -r CHUNK; do
            # Create a request and call DisableTests
            DIS_JSON="{\"CodeunitIds\": \"$CHUNK\"}"
            if [ "$SUITE_NAME" != "DEFAULT" ]; then
                DIS_JSON="{\"CodeunitIds\": \"$CHUNK\", \"SuiteName\": \"$SUITE_NAME\"}"
            fi
            DIS_RESP=$(curl -s --max-time 15 -u "$AUTH" -X POST \
                -H "Content-Type: application/json" \
                -d "$DIS_JSON" \
                "${API_BASE}/codeunitRunRequests" 2>/dev/null)
            DIS_ID=$(echo "$DIS_RESP" | py3 -c "import sys,json; print(json.load(sys.stdin)['Id'])" 2>/dev/null || true)
            if [ -n "$DIS_ID" ]; then
                curl -s -o /dev/null --max-time 30 -u "$AUTH" -X POST \
                    "${API_BASE}/codeunitRunRequests(${DIS_ID})/Microsoft.NAV.disableTests" 2>/dev/null
                DISABLED_COUNT=$((DISABLED_COUNT + 1))
            fi
        done <<< "$DISABLED_ENTRIES"
        echo "Disabled tests: $DISABLED_COUNT chunk(s) from $(find "$DISABLED_TESTS_DIR" -name "*.json" | wc -l) file(s)"
    fi
fi

# === Execute Tests via Network API ===
echo ""
echo "=== Running Tests ==="

IFS=',' read -ra CU_ARRAY <<< "$CODEUNIT_IDS"
NUM_CODEUNITS=${#CU_ARRAY[@]}
echo "Executing $NUM_CODEUNITS codeunit(s) via OData/API..."

RUN_BODY=$(mktemp)
RUN_HTTP=$(curl -s -o "$RUN_BODY" -w "%{http_code}" --max-time "$((TIMEOUT_MIN * 60))" -u "$AUTH" -X POST \
    "${API_BASE}/codeunitRunRequests(${REQUEST_ID})/Microsoft.NAV.runCodeunit" 2>/dev/null)
RUN_VALUE=$(py3 -c "import sys,json; print(str(json.load(sys.stdin).get('value','')).lower())" < "$RUN_BODY" 2>/dev/null || true)
if [ "$RUN_HTTP" != "200" ] && [ "$RUN_HTTP" != "204" ]; then
    echo "ERROR: runCodeunit failed (HTTP $RUN_HTTP)"
    sed 's/^/  /' "$RUN_BODY"
    rm -f "$RUN_BODY"
    exit 1
fi
rm -f "$RUN_BODY"

RESULTS_BODY=$(mktemp)
RESULTS_HTTP=$(curl -s -o "$RESULTS_BODY" -w "%{http_code}" --max-time 30 -u "$AUTH" \
    "${API_BASE}/testResults?\$filter=testSuite%20eq%20%27${SUITE_NAME}%27%20and%20lineType%20eq%20%27Function%27&\$top=20000" 2>/dev/null)
if [ "$RESULTS_HTTP" != "200" ]; then
    echo "ERROR: reading test results failed (HTTP $RESULTS_HTTP)"
    sed 's/^/  /' "$RESULTS_BODY"
    rm -f "$RESULTS_BODY"
    exit 1
fi

SUMMARY=$(JUNIT_OUTPUT="$JUNIT_OUTPUT" SUITE_NAME="$SUITE_NAME" py3 - "$RESULTS_BODY" <<'PY'
import html
import json
import os
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    rows = json.load(handle).get("value", [])

def status_of(row):
    raw = str(row.get("result", "")).strip().lower()
    if raw in {"success", "succeeded", "passed", "pass"}:
        return "passed"
    if raw in {"failure", "failed", "fail", "error"}:
        return "failed"
    return "skipped"

cases = []
for row in rows:
    codeunit = str(row.get("testCodeunit", "")).strip()
    method = str(row.get("functionName") or row.get("name") or "").strip()
    status = status_of(row)
    message = str(row.get("errorMessage") or row.get("errorMessagePreview") or "").strip()
    stack = str(row.get("errorCallStack") or "").strip()
    cases.append((codeunit, method, status, message, stack))

total = len(cases)
passed = sum(1 for _, _, status, _, _ in cases if status == "passed")
failed = sum(1 for _, _, status, _, _ in cases if status == "failed")
skipped = total - passed - failed

print(f"total={total} passed={passed} failed={failed} skipped={skipped}")
for codeunit, method, status, message, _ in cases:
    label = f"{codeunit}.{method}" if codeunit else method
    suffix = f" - {message}" if message and status == "failed" else ""
    print(f"{status.upper():7} {label}{suffix}")

junit_output = os.environ.get("JUNIT_OUTPUT", "").strip()
if junit_output:
    os.makedirs(os.path.dirname(junit_output) or ".", exist_ok=True)
    suite = ET.Element(
        "testsuite",
        {
            "name": os.environ.get("SUITE_NAME", "DEFAULT"),
            "tests": str(total),
            "failures": str(failed),
            "skipped": str(skipped),
        },
    )
    for codeunit, method, status, message, stack in cases:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": codeunit,
                "name": method,
            },
        )
        if status == "failed":
            failure = ET.SubElement(case, "failure", {"message": html.escape(message)})
            failure.text = stack or message
        elif status == "skipped":
            ET.SubElement(case, "skipped")
    ET.ElementTree(suite).write(junit_output, encoding="utf-8", xml_declaration=True)

sys.exit(1 if failed or total == 0 else 0)
PY
)
EXIT_CODE=$?
rm -f "$RESULTS_BODY"

printf '%s\n' "$SUMMARY"
if [ -n "$JUNIT_OUTPUT" ]; then
    echo "[run-tests] JUnit XML written to $JUNIT_OUTPUT"
fi

if [ "$RUN_VALUE" = "false" ] && [ "$EXIT_CODE" = "0" ]; then
    echo "ERROR: runCodeunit returned false without failed test rows"
    exit 1
fi

exit $EXIT_CODE
