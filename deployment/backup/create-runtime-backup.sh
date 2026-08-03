#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root so protected databases and configuration can be read" >&2
  exit 1
fi

backup_root="${ABC4RD_BACKUP_ROOT:-/opt/abc4rd/backups/runtime}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive_name="abc4rd-runtime-${timestamp}.tar.gz"
encrypted_path="${backup_root}/${archive_name}.gpg"
checksum_path="${encrypted_path}.sha256"

IFS= read -r encryption_password
[[ -n "$encryption_password" ]] || {
  echo "Backup encryption password is required on stdin" >&2
  exit 1
}

umask 077
workdir="$(mktemp -d /var/tmp/abc4rd-runtime-backup.XXXXXX)"
payload="${workdir}/payload"
plain_archive="${workdir}/${archive_name}"

cleanup() {
  if [[ "$workdir" == /var/tmp/abc4rd-runtime-backup.* && -d "$workdir" ]]; then
    rm -rf -- "$workdir"
  fi
}
trap cleanup EXIT

mkdir -p "$payload/databases" "$payload/config" "$payload/matrix" "$backup_root"
chmod 700 "$backup_root"

required_containers=(
  abc4rd-keycloak-db
  abc4rd-matrix-db
  abc4rd-academy-core
)
for container in "${required_containers[@]}"; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == "true" ]] || {
    echo "Required container is not running: $container" >&2
    exit 1
  }
done

docker exec abc4rd-keycloak-db \
  pg_dump --format=custom --compress=6 --no-owner --no-privileges \
  --username=keycloak --dbname=keycloak >"$payload/databases/keycloak.dump"

docker exec abc4rd-matrix-db \
  pg_dump --format=custom --compress=6 --no-owner --no-privileges \
  --username=synapse --dbname=synapse >"$payload/databases/matrix.dump"

docker exec abc4rd-academy-core python -c '
import sqlite3
source = sqlite3.connect("/var/lib/abc4rd/academy-core.db")
target = sqlite3.connect("/tmp/abc4rd-academy-core-backup.db")
with target:
    source.backup(target)
target.close()
source.close()
'
docker exec abc4rd-academy-core \
  cat /tmp/abc4rd-academy-core-backup.db \
  >"$payload/databases/academy-core.db"
docker exec abc4rd-academy-core rm -f /tmp/abc4rd-academy-core-backup.db

integrity="$(python3 - "$payload/databases/academy-core.db" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
print(connection.execute("PRAGMA integrity_check").fetchone()[0])
connection.close()
PY
)"
[[ "$integrity" == "ok" ]] || {
  echo "Academy Core backup failed SQLite integrity check" >&2
  exit 1
}

docker run --rm --network none \
  -v "$payload/databases:/backup:ro" postgres:17.10-alpine \
  pg_restore --list /backup/keycloak.dump >/dev/null
docker run --rm --network none \
  -v "$payload/databases:/backup:ro" postgres:17.10-alpine \
  pg_restore --list /backup/matrix.dump >/dev/null

copy_if_present() {
  local source="$1"
  local destination="$2"
  if [[ -e "$source" ]]; then
    mkdir -p "$(dirname "$payload/$destination")"
    cp -a "$source" "$payload/$destination"
  fi
}

copy_if_present /opt/abc4rd/keycloak/.env config/keycloak.env
copy_if_present /opt/abc4rd/matrix/.env config/matrix.env
copy_if_present /opt/abc4rd/matrix/config matrix/config
copy_if_present /opt/abc4rd/matrix/data/abc4rd.org.signing.key matrix/abc4rd.org.signing.key
copy_if_present /opt/abc4rd/matrix/data/media_store matrix/media_store
copy_if_present /opt/abc4rd/portal/.env config/portal.env
copy_if_present /opt/abc4rd/portal/data/state.json config/portal-state.json
copy_if_present /opt/abc4rd/academy-core/.env config/academy-core.env

{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'host=%s\n' "$(hostname)"
  printf 'scope=Keycloak DB, Matrix DB/media/signing key, Academy Core DB, portal projection, runtime env\n'
  printf 'excluded=Open edX and ERPNext use their separate backup procedures\n'
  docker inspect \
    abc4rd-keycloak abc4rd-keycloak-db abc4rd-matrix-db abc4rd-synapse \
    abc4rd-element abc4rd-livekit abc4rd-lk-jwt abc4rd-academy-core abc4rd-portal \
    --format '{{.Name}} image={{.Config.Image}} image_id={{.Image}}'
} >"$payload/MANIFEST.txt"

(
  cd "$payload"
  find . -type f ! -name MANIFEST.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum >MANIFEST.sha256
)

tar -C "$payload" -czf "$plain_archive" .
printf '%s' "$encryption_password" | gpg \
  --batch --yes --quiet --pinentry-mode loopback --passphrase-fd 0 \
  --symmetric --cipher-algo AES256 --s2k-digest-algo SHA512 \
  --output "$encrypted_path" "$plain_archive"
chmod 600 "$encrypted_path"
sha256sum "$encrypted_path" | cut -d' ' -f1 >"$checksum_path"
chmod 644 "$checksum_path"

printf '%s' "$encryption_password" | gpg \
  --batch --quiet --pinentry-mode loopback --passphrase-fd 0 \
  --decrypt "$encrypted_path" 2>/dev/null \
  | tar -tzf - >/dev/null
unset encryption_password

printf 'BACKUP_FILE=%s\n' "$encrypted_path"
printf 'CHECKSUM_FILE=%s\n' "$checksum_path"
printf 'SHA256=%s\n' "$(cat "$checksum_path")"
printf 'ENCRYPTED_ARCHIVE_VERIFIED=1\n'
