#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root so the protected backup can be read" >&2
  exit 1
fi

backup_root="${ABC4RD_BACKUP_ROOT:-/opt/abc4rd/backups/runtime}"
backup_path="${1:-}"
if [[ -z "$backup_path" ]]; then
  backup_path="$(find "$backup_root" -maxdepth 1 -type f -name 'abc4rd-runtime-*.tar.gz.gpg' \
    -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
fi
[[ -r "$backup_path" ]] || {
  echo "Readable runtime backup was not found" >&2
  exit 1
}

checksum_path="${backup_path}.sha256"
[[ -r "$checksum_path" ]] || {
  echo "Backup checksum sidecar was not found" >&2
  exit 1
}

IFS= read -r encryption_password
[[ -n "$encryption_password" ]] || {
  echo "Backup encryption password is required on stdin" >&2
  exit 1
}

umask 077
workdir="$(mktemp -d /var/tmp/abc4rd-runtime-restore.XXXXXX)"
plain_archive="${workdir}/runtime.tar.gz"
payload="${workdir}/payload"

cleanup() {
  if [[ "$workdir" == /var/tmp/abc4rd-runtime-restore.* && -d "$workdir" ]]; then
    rm -rf -- "$workdir"
  fi
}
trap cleanup EXIT

expected_checksum="$(tr -d '[:space:]' <"$checksum_path")"
actual_checksum="$(sha256sum "$backup_path" | cut -d' ' -f1)"
[[ "$actual_checksum" == "$expected_checksum" ]] || {
  echo "Encrypted backup checksum does not match" >&2
  exit 1
}

if ! printf '%s' "$encryption_password" | gpg \
  --batch --quiet --pinentry-mode loopback --passphrase-fd 0 \
  --output "$plain_archive" --decrypt "$backup_path" 2>/dev/null; then
  echo "Backup decryption failed" >&2
  exit 1
fi
unset encryption_password

if tar -tzf "$plain_archive" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "Backup archive contains an unsafe path" >&2
  exit 1
fi
mkdir -p "$payload"
tar -xzf "$plain_archive" -C "$payload"

[[ -r "$payload/MANIFEST.sha256" ]] || {
  echo "Backup manifest is missing" >&2
  exit 1
}
(
  cd "$payload"
  sha256sum -c MANIFEST.sha256 >/dev/null
)

restore_postgres() {
  local dump_path="$1"
  local verification_query="$2"
  local dump_dir dump_name
  dump_dir="$(dirname "$dump_path")"
  dump_name="$(basename "$dump_path")"
  docker run --rm \
    --network none \
    --memory 512m \
    --cpus 1 \
    --pids-limit 256 \
    --tmpfs /var/lib/postgresql/data:rw,nosuid,size=256m \
    -v "${dump_dir}:/backup:ro" \
    -e "ABC4RD_DUMP=/backup/${dump_name}" \
    -e "ABC4RD_VERIFY_QUERY=${verification_query}" \
    postgres:17.10-alpine sh -ceu '
      mkdir -p "$PGDATA" /tmp/pgsocket
      chown -R postgres:postgres "$PGDATA" /tmp/pgsocket
      if ! gosu postgres initdb --username=postgres --auth=trust --locale=C --encoding=UTF8 \
        --pgdata="$PGDATA" >/dev/null 2>/tmp/initdb.err; then
        cat /tmp/initdb.err >&2
        exit 1
      fi
      gosu postgres pg_ctl --pgdata="$PGDATA" \
        --options="-c listen_addresses= -c unix_socket_directories=/tmp/pgsocket" \
        --wait start >/dev/null
      gosu postgres createdb --host=/tmp/pgsocket --username=postgres restored
      pg_restore --exit-on-error --no-owner --no-privileges \
        --host=/tmp/pgsocket --username=postgres --dbname=restored \
        "$ABC4RD_DUMP" >/dev/null
      gosu postgres psql --host=/tmp/pgsocket --username=postgres --dbname=restored \
        --no-align --tuples-only --field-separator="|" \
        --command="$ABC4RD_VERIFY_QUERY"
      gosu postgres pg_ctl --pgdata="$PGDATA" --mode=fast --wait stop >/dev/null
    '
}

keycloak_counts="$(restore_postgres \
  "$payload/databases/keycloak.dump" \
  'SELECT (SELECT count(*) FROM realm),(SELECT count(*) FROM user_entity),(SELECT count(*) FROM credential);')"
matrix_counts="$(restore_postgres \
  "$payload/databases/matrix.dump" \
  'SELECT (SELECT count(*) FROM users),(SELECT count(*) FROM rooms),(SELECT count(*) FROM events);')"

core_counts="$(python3 - "$payload/databases/academy-core.db" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
if integrity != "ok":
    raise SystemExit("Academy Core integrity check failed")
counts = [
    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    for table in ("abc4rd_identities", "domain_events", "audit_entries")
]
connection.close()
print("|".join(str(value) for value in counts))
PY
)"

portal_counts="$(jq -er '
  [(.participants | length), ([.participants[].courses[].certificate?] | length)]
  | map(tostring) | join("|")
' "$payload/config/portal-state.json")"

[[ -s "$payload/matrix/abc4rd.org.signing.key" ]] || {
  echo "Matrix signing key is missing from the restored payload" >&2
  exit 1
}

IFS='|' read -r keycloak_realms keycloak_users keycloak_credentials <<<"$keycloak_counts"
IFS='|' read -r matrix_users matrix_rooms matrix_events <<<"$matrix_counts"
IFS='|' read -r core_identities core_events core_audit <<<"$core_counts"
IFS='|' read -r portal_participants portal_certificates <<<"$portal_counts"

printf 'RESTORE_DRILL PASS keycloak_realms=%s keycloak_users=%s keycloak_credentials=%s matrix_users=%s matrix_rooms=%s matrix_events=%s core_identities=%s core_events=%s core_audit=%s portal_participants=%s portal_certificates=%s\n' \
  "$keycloak_realms" "$keycloak_users" "$keycloak_credentials" \
  "$matrix_users" "$matrix_rooms" "$matrix_events" \
  "$core_identities" "$core_events" "$core_audit" \
  "$portal_participants" "$portal_certificates"
