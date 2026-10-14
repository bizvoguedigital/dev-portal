#!/usr/bin/env bash
# Login and header checks against a running portal. Works on macOS and Linux.
# Start the stack first: docker compose up, with Keycloak and the portal.
set -uo pipefail

BASE="${PORTAL_URL:-http://localhost:8000}"
COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT

header_value() {
  printf '%s\n' "$1" | awk -v name="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" '
    {
      line = tolower($0)
      gsub(/\r/, "", line)
      prefix = name ":"
      if (index(line, prefix) == 1) {
        sub(/^[^:]+:[[:space:]]*/, "", $0)
        gsub(/\r/, "", $0)
        print
        exit
      }
    }
  '
}

has_header() {
  local value
  value="$(header_value "$1" "$2")"
  if [ -n "$value" ]; then
    printf 'true'
  else
    printf 'false'
  fi
}

echo "== 1. Security headers present even on an unauthenticated redirect =="
headers="$(curl -sS -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/")"
status_code="$(printf '%s\n' "$headers" | awk 'NR==1 { print $2 }' | tr -d '\r')"
location="$(header_value "$headers" "location")"
echo "GET / status: $status_code -> $location"
echo "X-Frame-Options present: $(has_header "$headers" "x-frame-options")"
echo "CSP present: $(has_header "$headers" "content-security-policy")"

echo
echo "== 2. Full login flow for the in-group user =="
login_headers="$(curl -sS -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/login/keycloak")"
authorize_url="$(header_value "$login_headers" "location")"

auth_page="$(curl -sS -b "$COOKIES" -c "$COOKIES" "$authorize_url")"
action_url="$(printf '%s' "$auth_page" | tr '\n' ' ' | sed -nE 's/.*<form[^>]*action="([^"]*)".*/\1/p' | head -n 1)"
action_url="${action_url//&amp;/&}"
action_url="$(printf '%s' "$action_url" | tr -d '\r')"

final_status_url="$(curl -sS -o /dev/null -w '%{http_code} %{url_effective}' -L --max-redirs 20 \
  -b "$COOKIES" -c "$COOKIES" \
  --data-urlencode "username=devportal-user" \
  --data-urlencode "password=devportal-pass" \
  "$action_url")"
echo "Post-login status/url: $final_status_url"

me="$(curl -sS -b "$COOKIES" -c "$COOKIES" "$BASE/api/me")"
echo "GET /api/me: $me"

echo
echo "== 3. Invalid enum value should be rejected with 422 =="
bad_status="$(curl -sS -o /dev/null -w '%{http_code}' -b "$COOKIES" -c "$COOKIES" \
  -H 'Content-Type: application/json' \
  -d '{"targetEnvironment":"staging-hacked","allowProd":false,"tenantList":"tenant-a","pipelineTrigger":"tenant_baseline","taint":false}' \
  "$BASE/api/trigger-deployment")"
echo "Invalid enum request status: $bad_status"

echo
echo "== 4. RP-initiated logout should end the Keycloak SSO session too =="
logout_headers="$(curl -sS -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/logout")"
logout_location="$(header_value "$logout_headers" "location")"
echo "GET /logout -> $logout_location"

end_session_body="$(curl -sS -L --max-redirs 20 -b "$COOKIES" -c "$COOKIES" "$logout_location")"
if printf '%s' "$end_session_body" | grep -qi 'logged out'; then
  echo "Landing page shows logged out: true"
else
  echo "Landing page shows logged out: false"
fi

me_after_status="$(curl -sS -o /dev/null -w '%{http_code}' -b "$COOKIES" -c "$COOKIES" "$BASE/api/me")"
echo "GET /api/me after logout status: $me_after_status"
