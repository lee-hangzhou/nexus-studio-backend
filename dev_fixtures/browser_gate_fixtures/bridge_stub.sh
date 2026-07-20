#!/usr/bin/env bash
# Session bridge dev stub — NOT production bridge extension.
# Simulates cookie export with expected_domain validation.
#
# Usage:
#   ./bridge_stub.sh --token TOKEN --domain example.com --url https://app.example.com/dashboard
#
# Validates that URL host matches --domain (exact or subdomain), then prints
# a JSON cookie payload suitable for import_bridge_cookies API testing.

set -euo pipefail

TOKEN=""
DOMAIN=""
PAGE_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --url) PAGE_URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TOKEN" || -z "$DOMAIN" || -z "$PAGE_URL" ]]; then
  echo "Usage: $0 --token TOKEN --domain DOMAIN --url PAGE_URL" >&2
  exit 1
fi

export PAGE_URL
host="$(python3 -c "from urllib.parse import urlparse; import os; print((urlparse(os.environ['PAGE_URL']).hostname or '').lower())")"

expected="$(echo "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
if [[ -z "$host" ]]; then
  echo "error: invalid PAGE_URL host" >&2
  exit 2
fi
if [[ "$host" != "$expected" && "$host" != *".${expected}" ]]; then
  echo "error: domain mismatch — expected ${expected}, got ${host}" >&2
  exit 3
fi

echo "bridge_stub: token prefix ${TOKEN:0:8}… domain ok (${host})"
cat <<EOF
{"cookies":[{"name":"session","value":"stub-session-value","domain":".${expected}","path":"/","httpOnly":true}]}
EOF
