#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ERPNEXT_ENV_FILE:-$base_dir/.env}"

if [[ ! -r "$env_file" ]]; then
  echo "Environment file is not readable: $env_file" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

: "${SITE_NAME:?SITE_NAME is required}"
compose=(docker compose --env-file "$env_file" --project-directory "$base_dir")

expected=(db redis-cache redis-queue backend frontend websocket queue-short queue-long scheduler)
ps_json="$("${compose[@]}" ps --format json)"
PS_JSON="$ps_json" EXPECTED_SERVICES="${expected[*]}" python3 - <<'PY'
import json
import os

raw = os.environ["PS_JSON"].strip()
try:
    rows = json.loads(raw)
except json.JSONDecodeError:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
if isinstance(rows, dict):
    rows = [rows]

by_service = {row.get("Service"): row for row in rows}
for service in os.environ["EXPECTED_SERVICES"].split():
    row = by_service.get(service)
    if not row:
        raise SystemExit(f"Required service is missing: {service}")
    if str(row.get("State", "")).lower() != "running":
        raise SystemExit(f"Required service is not running: {service}")
    health = str(row.get("Health", "")).lower()
    if health and health != "healthy":
        raise SystemExit(f"Service healthcheck is not healthy: {service} ({health})")
PY

version_output="$("${compose[@]}" exec -T backend bench version --format legacy)"
actual_erpnext="$(awk '$1 == "erpnext" {print $2}' <<<"$version_output")"
actual_frappe="$(awk '$1 == "frappe" {print $2}' <<<"$version_output")"
actual_abc4rd_crm="$(awk '$1 == "abc4rd_crm" {print $2}' <<<"$version_output")"

[[ "$actual_erpnext" == "16.30.0" ]] || {
  echo "ERPNext version mismatch: $actual_erpnext" >&2
  exit 1
}
[[ "$actual_frappe" == "16.29.0" ]] || {
  echo "Frappe version mismatch: $actual_frappe" >&2
  exit 1
}
[[ "$actual_abc4rd_crm" == "0.1.0" ]] || {
  echo "ABC4RD CRM version mismatch: $actual_abc4rd_crm" >&2
  exit 1
}
printf '%s\n' "$version_output"
installed_apps="$(
  "${compose[@]}" exec -T backend bench --site "$SITE_NAME" list-apps --format json \
    | python3 -c 'import json, sys; print("\n".join(next(iter(json.load(sys.stdin).values()))))'
)"
for app in erpnext abc4rd_crm; do
  grep -Fxq "$app" <<<"$installed_apps" || {
    echo "Required app is not installed: $app" >&2
    exit 1
  }
done

for doctype in "ABC4RD Participant" "ABC4RD Inquiry" "ABC4RD Audit Reference"; do
  result="$("${compose[@]}" exec -T backend bench --site "$SITE_NAME" execute frappe.db.exists --args "[\"DocType\",\"$doctype\"]")"
  [[ "$result" == "$doctype" ]] || {
    echo "DocType readback failed: $doctype" >&2
    exit 1
  }
done

"${compose[@]}" exec -T frontend \
  curl -fsS -H "Host: $SITE_NAME" http://127.0.0.1:8080/api/method/ping >/dev/null

echo "PASS: runtime services, versions, apps, DocTypes and internal HTTP readback"
echo "NOTE: DNS, TLS, Keycloak SSO, backup restore and synthetic route remain separate gates"
