#!/usr/bin/env bash
set -euo pipefail

auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
tenant="${BC_TENANT:-default}"

require_success() {
    local label="$1"
    local url="$2"
    local status

    status=$(curl -sS --max-time 5 -u "$auth" -o /dev/null -w '%{http_code}' "$url" || true)
    case "$status" in
        2*) return 0 ;;
        *) echo "healthcheck: $label returned HTTP $status" >&2; return 1 ;;
    esac
}

require_routed() {
    local label="$1"
    local url="$2"
    local status

    status=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "$url" || true)
    case "$status" in
        2*|3*|4*) return 0 ;;
        *) echo "healthcheck: $label returned HTTP $status" >&2; return 1 ;;
    esac
}

require_websocket_upgrade() {
    local label="$1"
    local url="$2"
    local response
    local status

    response=$(
        curl -i -sS --max-time 3 -u "$auth" \
            -H 'Connection: Upgrade' \
            -H 'Upgrade: websocket' \
            -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
            -H 'Sec-WebSocket-Version: 13' \
            "$url" 2>&1 || true
    )
    status=$(printf '%s\n' "$response" | awk 'BEGIN{IGNORECASE=1} /^HTTP\// {print $2; exit}')
    if [ "$status" = "101" ]; then
        return 0
    fi
    echo "healthcheck: $label websocket upgrade returned HTTP ${status:-000}" >&2
    printf '%s\n' "$response" | sed 's/^/healthcheck:   /' >&2
    return 1
}

require_tcp() {
    local label="$1"
    local port="$2"

    if timeout 5 bash -c ":</dev/tcp/127.0.0.1/${port}" 2>/dev/null; then
        return 0
    fi

    echo "healthcheck: $label did not accept TCP connections on port $port" >&2
    return 1
}

test -f /tmp/bc-ready

require_tcp "Management" "7045"
require_tcp "Client Services" "7046"
require_routed "Management" "http://localhost:7045/BC/Management"
require_success "Client Services" "http://localhost:7046/BC/client/SignIn"
require_websocket_upgrade "Client Services" "http://localhost:7046/BC/client/csh"
require_success "OData" "http://localhost:7048/BC/ODataV4/Company"
require_success "API" "http://localhost:7052/BC/api/v2.0/companies?tenant=${tenant}"
require_success "DevServices" "http://localhost:7049/BC/dev/metadata?tenant=${tenant}"
require_routed "SOAP" "http://localhost:7047/BC/WS/Services"
require_routed "Management API" "http://localhost:7086/BC/managementApi/v1.0/companies"
require_routed "WebClient" "http://localhost:7085/BC/client/SignIn"
