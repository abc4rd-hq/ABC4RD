#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base_compose="$base_dir/docker-compose.yml"
restore_override="$base_dir/docker-compose.restore-drill.yml"
production_env="${ERPNEXT_ENV_FILE:-$base_dir/.env}"
drill_env="${1:-}"

if [[ ! -r "$production_env" || ! -r "$drill_env" || ! -r "$restore_override" ]]; then
  echo "Usage: ERPNEXT_ENV_FILE=/path/to/production.env $0 /path/to/restore-drill.env" >&2
  exit 2
fi

read_value() {
  local key="$1" file="$2"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

production_prefix="$(read_value ERPNEXT_RESOURCE_PREFIX "$production_env")"
drill_prefix="$(read_value ERPNEXT_RESOURCE_PREFIX "$drill_env")"
production_project="$(read_value ERPNEXT_PROJECT_NAME "$production_env")"
drill_project="$(read_value ERPNEXT_PROJECT_NAME "$drill_env")"
production_site="$(read_value SITE_NAME "$production_env")"
drill_site="$(read_value SITE_NAME "$drill_env")"
production_db_secret="$(read_value DB_ROOT_PASSWORD_FILE "$production_env")"
drill_db_secret="$(read_value DB_ROOT_PASSWORD_FILE "$drill_env")"
production_admin_secret="$(read_value ERPNEXT_ADMIN_PASSWORD_FILE "$production_env")"
drill_admin_secret="$(read_value ERPNEXT_ADMIN_PASSWORD_FILE "$drill_env")"

[[ "$production_prefix" == "abc4rd-erpnext" ]] || {
  echo "Unexpected production resource prefix: $production_prefix" >&2
  exit 1
}
[[ "$production_project" == "abc4rd-erpnext" ]] || {
  echo "Unexpected production project name: $production_project" >&2
  exit 1
}
[[ "$drill_prefix" =~ ^abc4rd-erpnext-restore-drill-[0-9]{8}$ ]] || {
  echo "Restore drill prefix must end in an 8-digit date: $drill_prefix" >&2
  exit 1
}
[[ "$drill_project" == "$drill_prefix" ]] || {
  echo "Restore drill project and resource prefix must be identical: $drill_project / $drill_prefix" >&2
  exit 1
}
[[ "$drill_site" =~ ^restore-drill-[0-9]{8}\.internal$ ]] || {
  echo "Restore drill site name is unsafe: $drill_site" >&2
  exit 1
}
[[ "$production_prefix" != "$drill_prefix" && "$production_project" != "$drill_project" && "$production_site" != "$drill_site" ]] || {
  echo "Restore drill resources overlap production" >&2
  exit 1
}
[[ "$production_db_secret" != "$drill_db_secret" ]] || {
  echo "Restore drill must use a separate database root secret" >&2
  exit 1
}
[[ "$production_admin_secret" != "$drill_admin_secret" ]] || {
  echo "Restore drill must use a separate Administrator secret" >&2
  exit 1
}

for secret_path in "$drill_db_secret" "$drill_admin_secret"; do
  if [[ ! -s "$secret_path" ]]; then
    echo "Restore drill secret is missing or empty: $secret_path" >&2
    exit 1
  fi
  mode="$(stat -c '%a' "$secret_path")"
  if (( 8#$mode & 8#077 )); then
    echo "Restore drill secret must not be group/world accessible: $secret_path ($mode)" >&2
    exit 1
  fi
done

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is required for resolved restore-isolation verification" >&2
  exit 2
fi

resolved_json="$(
  docker compose \
    -f "$base_compose" \
    -f "$restore_override" \
    --env-file "$drill_env" \
    --project-directory "$base_dir" \
    config --format json
)"

RESOLVED_JSON="$resolved_json" DRILL_PREFIX="$drill_prefix" DRILL_PROJECT="$drill_project" python3 - <<'PY'
import json
import os

document = json.loads(os.environ["RESOLVED_JSON"])
prefix = os.environ["DRILL_PREFIX"]
project = os.environ["DRILL_PROJECT"]

if document.get("name") != project:
    raise SystemExit(f"Resolved project name is unsafe: {document.get('name')}")

expected_volumes = {
    "sites": f"{prefix}-sites",
    "db-data": f"{prefix}-db-data",
    "redis-queue-data": f"{prefix}-redis-queue-data",
}
for key, expected in expected_volumes.items():
    actual = document.get("volumes", {}).get(key, {}).get("name")
    if actual != expected:
        raise SystemExit(f"Resolved restore volume is unsafe: {key}={actual}")

expected_networks = {
    "internal": f"{prefix}-internal",
    "egress": f"{prefix}-egress",
    "tutor": f"{prefix}-proxy-disabled",
}
for key, expected in expected_networks.items():
    actual = document.get("networks", {}).get(key, {}).get("name")
    if actual != expected:
        raise SystemExit(f"Resolved restore network is unsafe: {key}={actual}")

tutor_network = document.get("networks", {}).get("tutor", {})
if tutor_network.get("external"):
    raise SystemExit("Restore proxy network must not be external")
if not tutor_network.get("internal"):
    raise SystemExit("Restore proxy network must be internal")

egress_network = document.get("networks", {}).get("egress", {})
if not egress_network.get("internal"):
    raise SystemExit("Restore egress network must be internal")

expected_disabled_profiles = {
    "frontend": "restore-drill-frontend-disabled",
    "queue-short": "restore-drill-workers-disabled",
    "queue-long": "restore-drill-workers-disabled",
    "scheduler": "restore-drill-workers-disabled",
}
for service, expected_profile in expected_disabled_profiles.items():
    profiles = document.get("services", {}).get(service, {}).get("profiles", [])
    if expected_profile not in profiles:
        raise SystemExit(f"Restore service is not disabled: {service}")
PY

echo "PASS: restore drill project, site, secrets, volumes and enabled networks are isolated"
