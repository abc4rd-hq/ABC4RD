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
"${compose[@]}" exec -T backend bench --site "$SITE_NAME" backup --with-files --compress

"${compose[@]}" exec -T backend bash -s -- "$SITE_NAME" <<'BACKUP_READBACK'
set -euo pipefail
site_name="$1"
backup_dir="sites/$site_name/private/backups"
cd "$backup_dir"

mapfile -t newest < <(find . -maxdepth 1 -type f -printf '%T@ %P\n' | sort -n | tail -n 4 | cut -d' ' -f2-)
if (( ${#newest[@]} < 4 )); then
  echo "Backup readback found fewer than four artifacts" >&2
  exit 1
fi

sha256sum "${newest[@]}"
echo "LOCAL_BACKUP_DIRECTORY=$backup_dir"
echo "LOCAL_BACKUP_ONLY=1"
BACKUP_READBACK

echo "NEXT: encrypt/copy this backup off-host and verify its checksum; local creation alone is not a completed backup"
