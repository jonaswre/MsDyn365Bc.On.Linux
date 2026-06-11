#!/usr/bin/env bash
# Verify the standard Business Central container network surface from outside
# the container. This is a host-side companion to scripts/healthcheck.sh.
set -euo pipefail

host="${BC_HOST:-localhost}"
auth="${BC_USERNAME:-admin}:${BC_PASSWORD:-admin}"
tenant="${BC_TENANT:-default}"

client_services_port="${BC_CLIENT_SERVICES_PORT:-7046}"
soap_port="${BC_SOAP_PORT:-7047}"
dev_port="${BC_DEV_PORT:-7049}"
odata_port="${BC_ODATA_PORT:-7048}"
api_port="${BC_API_PORT:-7052}"
management_port="${BC_MGMT_PORT:-7045}"
management_api_port="${BC_MGMT_API_PORT:-7086}"
webclient_port="${BC_CLIENT_PORT:-7085}"

usage() {
    cat <<'EOF'
Usage: scripts/verify-network-surface.sh

Environment variables:
  BC_HOST                  Hostname to probe (default: localhost)
  BC_USERNAME              NavUserPassword username (default: admin)
  BC_PASSWORD              NavUserPassword password (default: admin)
  BC_TENANT                Tenant name (default: default)
  BC_CLIENT_SERVICES_PORT  Client Services host port (default: 7046)
  BC_SOAP_PORT             SOAP host port (default: 7047)
  BC_DEV_PORT              Dev Services host port (default: 7049)
  BC_ODATA_PORT            OData host port (default: 7048)
  BC_API_PORT              API host port (default: 7052)
  BC_MGMT_PORT             Management host port (default: 7045)
  BC_MGMT_API_PORT         Management API host port (default: 7086)
  BC_CLIENT_PORT           WebClient host port (default: 7085)
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

url() {
    local port="$1"
    local path="$2"
    printf 'http://%s:%s%s' "$host" "$port" "$path"
}

status_of() {
    local curl_auth=()
    if [ "${1:-}" = "--auth" ]; then
        curl_auth=(-u "$auth")
        shift
    fi
    curl -sS --max-time 10 "${curl_auth[@]}" -o /dev/null -w '%{http_code}' "$1" || true
}

require_tcp() {
    local label="$1"
    local port="$2"
    if timeout 10 bash -c ":</dev/tcp/${host}/${port}" 2>/dev/null; then
        printf 'OK   %-18s tcp/%s\n' "$label" "$port"
        return 0
    fi
    printf 'FAIL %-18s tcp/%s did not accept connections\n' "$label" "$port" >&2
    return 1
}

require_success() {
    local label="$1"
    local target="$2"
    local status
    status=$(status_of --auth "$target")
    case "$status" in
        2*) printf 'OK   %-18s HTTP %s %s\n' "$label" "$status" "$target" ;;
        *) printf 'FAIL %-18s HTTP %s %s\n' "$label" "$status" "$target" >&2; return 1 ;;
    esac
}

require_routed() {
    local label="$1"
    local target="$2"
    local status
    status=$(status_of "$target")
    case "$status" in
        2*|3*|4*) printf 'OK   %-18s HTTP %s %s\n' "$label" "$status" "$target" ;;
        *) printf 'FAIL %-18s HTTP %s %s\n' "$label" "$status" "$target" >&2; return 1 ;;
    esac
}

require_websocket_upgrade() {
    local label="$1"
    local target="$2"
    local response
    local status

    response=$(
        curl -i -sS --max-time 3 -u "$auth" \
            -H 'Connection: Upgrade' \
            -H 'Upgrade: websocket' \
            -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
            -H 'Sec-WebSocket-Version: 13' \
            "$target" 2>&1 || true
    )
    status=$(printf '%s\n' "$response" | awk 'BEGIN{IGNORECASE=1} /^HTTP\// {print $2; exit}')
    if [ "$status" = "101" ]; then
        printf 'OK   %-18s WS %s %s\n' "$label" "$status" "$target"
        return 0
    fi
    printf 'FAIL %-18s WS %s %s\n' "$label" "${status:-000}" "$target" >&2
    printf '%s\n' "$response" | sed 's/^/       /' >&2
    return 1
}

require_tcp "Management" "$management_port"
require_tcp "Client Services" "$client_services_port"
require_tcp "SOAP" "$soap_port"
require_tcp "OData" "$odata_port"
require_tcp "DevServices" "$dev_port"
require_tcp "API" "$api_port"
require_tcp "Management API" "$management_api_port"
require_tcp "WebClient" "$webclient_port"

require_routed "Management" "$(url "$management_port" "/BC/Management")"
require_success "Client Services" "$(url "$client_services_port" "/BC/client/SignIn")"
require_websocket_upgrade "Client Services WS" "$(url "$client_services_port" "/BC/client/csh")"
require_success "OData" "$(url "$odata_port" "/BC/ODataV4/Company?tenant=${tenant}")"
require_success "API" "$(url "$api_port" "/BC/api/v2.0/companies?tenant=${tenant}")"
require_success "DevServices" "$(url "$dev_port" "/BC/dev/metadata?tenant=${tenant}")"
require_routed "SOAP" "$(url "$soap_port" "/BC/WS/Services")"
require_routed "Management API" "$(url "$management_api_port" "/BC/managementApi/v1.0/companies")"
require_routed "WebClient" "$(url "$webclient_port" "/BC/client/SignIn")"

echo "Business Central network surface is available at http://${host}:${client_services_port}/BC"
