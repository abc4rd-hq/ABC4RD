#!/usr/bin/env bash
set -euo pipefail

container="${ABC4RD_KEYCLOAK_CONTAINER:-abc4rd-keycloak}"
admin_user="${ABC4RD_KEYCLOAK_ADMIN_USER:-abc4rd_identity_owner}"
realm="${ABC4RD_KEYCLOAK_REALM:-abc4rd}"
backup_dir="${ABC4RD_KEYCLOAK_BACKUP_DIR:-/opt/abc4rd/backups/keycloak}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
container_backup="/tmp/abc4rd-realm-before-passkeys-${timestamp}.json"
host_backup="${backup_dir}/realm-before-passkeys-${timestamp}.json"

IFS= read -r admin_password
[[ -n "$admin_password" ]] || {
  echo "Keycloak administrator password is required on stdin" >&2
  exit 1
}

install -d -m 700 "$backup_dir"

docker exec -i \
  -e ABC4RD_ADMIN_PASSWORD="$admin_password" \
  "$container" bash -s -- "$admin_user" "$realm" "$container_backup" <<'IN_CONTAINER'
set -euo pipefail

admin_user="$1"
realm="$2"
backup="$3"
kcadm=/opt/keycloak/bin/kcadm.sh
config=/tmp/abc4rd-kcadm-passkeys.config

"$kcadm" config credentials \
  --config "$config" \
  --server http://127.0.0.1:8080 \
  --realm master \
  --user "$admin_user" \
  --password "$ABC4RD_ADMIN_PASSWORD" >/dev/null
unset ABC4RD_ADMIN_PASSWORD

"$kcadm" get "realms/$realm" --config "$config" >"$backup"

"$kcadm" update "realms/$realm" --config "$config" \
  -s 'webAuthnPolicyPasswordlessRpEntityName=ABC4RD ID' \
  -s 'webAuthnPolicyPasswordlessRpId=id.abc4rd.org' \
  -s 'webAuthnPolicyPasswordlessAttestationConveyancePreference=none' \
  -s 'webAuthnPolicyPasswordlessAuthenticatorAttachment=not specified' \
  -s 'webAuthnPolicyPasswordlessResidentKey=required' \
  -s 'webAuthnPolicyPasswordlessUserVerificationRequirement=required' \
  -s 'webAuthnPolicyPasswordlessAvoidSameAuthenticatorRegister=true' \
  -s 'webAuthnPolicyPasswordlessPasskeysEnabled=true' \
  -s 'webAuthnPolicyPasswordlessMediation=conditional'

"$kcadm" get "realms/$realm" --config "$config" \
  --fields realm,webAuthnPolicyPasswordlessRpEntityName,webAuthnPolicyPasswordlessRpId,webAuthnPolicyPasswordlessResidentKey,webAuthnPolicyPasswordlessUserVerificationRequirement,webAuthnPolicyPasswordlessAvoidSameAuthenticatorRegister,webAuthnPolicyPasswordlessPasskeysEnabled,webAuthnPolicyPasswordlessMediation

rm -f "$config"
IN_CONTAINER

unset admin_password
docker cp "${container}:${container_backup}" "$host_backup" >/dev/null
chmod 600 "$host_backup"
docker exec "$container" rm -f "$container_backup"

printf 'Backup: %s\n' "$host_backup"
