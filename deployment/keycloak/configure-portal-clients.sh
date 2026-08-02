#!/usr/bin/env bash
set -euo pipefail

container="${ABC4RD_KEYCLOAK_CONTAINER:-abc4rd-keycloak}"
admin_user="${ABC4RD_KEYCLOAK_ADMIN_USER:-abc4rd_identity_owner}"
realm="${ABC4RD_KEYCLOAK_REALM:-abc4rd}"

IFS= read -r admin_password
[[ -n "$admin_password" ]] || { echo "Keycloak administrator password is required on stdin" >&2; exit 1; }

docker exec -i \
  -e ABC4RD_ADMIN_PASSWORD="$admin_password" \
  "$container" bash -s -- "$admin_user" "$realm" <<'IN_CONTAINER'
set -euo pipefail

admin_user="$1"
realm="$2"
kcadm=/opt/keycloak/bin/kcadm.sh
config=/tmp/abc4rd-kcadm.config

"$kcadm" config credentials \
  --config "$config" \
  --server http://127.0.0.1:8080 \
  --realm master \
  --user "$admin_user" \
  --password "$ABC4RD_ADMIN_PASSWORD" >/dev/null
unset ABC4RD_ADMIN_PASSWORD

upsert_client() {
  local client_id="$1"
  local payload="$2"
  local internal_id
  internal_id="$("$kcadm" get clients -r "$realm" -q "clientId=$client_id" --config "$config" \
    | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  if [[ -z "$internal_id" ]]; then
    printf '%s' "$payload" | "$kcadm" create clients -r "$realm" -f - --config "$config" >/dev/null
    internal_id="$("$kcadm" get clients -r "$realm" -q "clientId=$client_id" --config "$config" \
      | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  else
    printf '%s' "$payload" | "$kcadm" update "clients/$internal_id" -r "$realm" -f - --config "$config" >/dev/null
  fi
  [[ -n "$internal_id" ]] || { echo "Client $client_id was not created" >&2; exit 1; }
  local secret
  secret="$("$kcadm" get "clients/$internal_id/client-secret" -r "$realm" --config "$config" \
    | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  [[ -n "$secret" ]] || { echo "Client secret for $client_id is missing" >&2; exit 1; }
  printf '%s\tconfigured\n' "$client_id"
}

portal_payload='{
  "clientId":"abc4rd-portal",
  "name":"ABC4RD Learner Portal",
  "enabled":true,
  "protocol":"openid-connect",
  "publicClient":false,
  "standardFlowEnabled":true,
  "directAccessGrantsEnabled":false,
  "serviceAccountsEnabled":false,
  "redirectUris":["https://app.abc4rd.org/oauth2/callback"],
  "webOrigins":["https://app.abc4rd.org"],
  "attributes":{"pkce.code.challenge.method":"S256","post.logout.redirect.uris":"https://app.abc4rd.org/*"}
}'

synapse_payload='{
  "clientId":"abc4rd-synapse",
  "name":"ABC4RD Matrix Synapse",
  "enabled":true,
  "protocol":"openid-connect",
  "publicClient":false,
  "standardFlowEnabled":true,
  "directAccessGrantsEnabled":false,
  "serviceAccountsEnabled":false,
  "redirectUris":["https://matrix.abc4rd.org/_synapse/client/oidc/callback"],
  "webOrigins":["https://chat.abc4rd.org"],
  "attributes":{"pkce.code.challenge.method":"S256","backchannel.logout.url":"https://matrix.abc4rd.org/_synapse/client/oidc/backchannel_logout","backchannel.logout.session.required":"true"}
}'

upsert_client abc4rd-portal "$portal_payload"
upsert_client abc4rd-synapse "$synapse_payload"
rm -f "$config"
IN_CONTAINER
