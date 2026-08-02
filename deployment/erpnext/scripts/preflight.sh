#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ERPNEXT_ENV_FILE:-$base_dir/.env}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Run this read-only preflight on the target Linux host" >&2
  exit 2
fi

if [[ ! -r "$env_file" ]]; then
  echo "Environment file is not readable: $env_file" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

[[ "${ERPNEXT_PROJECT_NAME:-}" == "abc4rd-erpnext" ]] || {
  echo "Production project name must be abc4rd-erpnext" >&2
  exit 1
}
[[ "${ERPNEXT_RESOURCE_PREFIX:-}" == "abc4rd-erpnext" ]] || {
  echo "Production resource prefix must be abc4rd-erpnext" >&2
  exit 1
}
[[ "${SITE_NAME:-}" == "crm.abc4rd.org" ]] || {
  echo "Production SITE_NAME must be crm.abc4rd.org" >&2
  exit 1
}

required_commands=(curl docker python3)
for command_name in "${required_commands[@]}"; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done
docker compose version >/dev/null

cpu_count="$(getconf _NPROCESSORS_ONLN)"
available_mib="$(awk '/MemAvailable:/ {printf "%d", $2 / 1024}' /proc/meminfo)"
free_disk_gib="$(df -Pk "$base_dir" | awk 'NR == 2 {printf "%d", $4 / 1024 / 1024}')"

min_cpu="${ERPNEXT_MIN_CPU_COUNT:-4}"
min_memory="${ERPNEXT_MIN_AVAILABLE_MIB:-10240}"
min_disk="${ERPNEXT_MIN_FREE_DISK_GIB:-80}"

(( cpu_count >= min_cpu )) || {
  echo "CPU gate failed: $cpu_count available, $min_cpu required" >&2
  exit 1
}
(( available_mib >= min_memory )) || {
  echo "RAM gate failed: ${available_mib} MiB available, ${min_memory} MiB required" >&2
  exit 1
}
(( free_disk_gib >= min_disk )) || {
  echo "Disk gate failed: ${free_disk_gib} GiB free, ${min_disk} GiB required" >&2
  exit 1
}

docker network inspect "${TUTOR_NETWORK:-tutor_local_default}" >/dev/null

for secret_path in "$DB_ROOT_PASSWORD_FILE" "$ERPNEXT_ADMIN_PASSWORD_FILE"; do
  if [[ ! -s "$secret_path" ]]; then
    echo "Secret file is missing or empty: $secret_path" >&2
    exit 1
  fi
  mode="$(stat -c '%a' "$secret_path")"
  if (( 8#$mode & 8#077 )); then
    echo "Secret file must not be group/world accessible: $secret_path ($mode)" >&2
    exit 1
  fi
done

issuer="${KEYCLOAK_ISSUER:?KEYCLOAK_ISSUER is required}"
discovery_json="$(curl -fsS --max-time 15 "$issuer/.well-known/openid-configuration")"
DISCOVERY_JSON="$discovery_json" EXPECTED_ISSUER="$issuer" python3 - <<'PY'
import json
import os

document = json.loads(os.environ["DISCOVERY_JSON"])
if document.get("issuer") != os.environ["EXPECTED_ISSUER"]:
    raise SystemExit("Keycloak issuer mismatch")
PY

docker compose --env-file "$env_file" --project-directory "$base_dir" config --quiet

echo "PASS: read-only host gates"
echo "CPU_COUNT=$cpu_count"
echo "AVAILABLE_MIB=$available_mib"
echo "FREE_DISK_GIB=$free_disk_gib"
echo "KEYCLOAK_ISSUER=$issuer"
echo "NOTE: Open edX SSO and Keycloak access-control evidence still require human review"
