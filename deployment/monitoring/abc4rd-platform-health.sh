#!/usr/bin/env bash
set -uo pipefail

failures=()
checked_containers=0
checked_http=0
backup_age_hours=unknown

check_container() {
  local name="$1"
  local state
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true)"
  checked_containers=$((checked_containers + 1))
  if [[ "$state" != "healthy" && "$state" != "running" ]]; then
    failures+=("container:$name:$state")
  fi
}

check_http() {
  local label="$1"
  local url="$2"
  local expected="$3"
  local actual
  checked_http=$((checked_http + 1))
  actual="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15 "$url" 2>/dev/null || true)"
  if [[ ! ",$expected," == *",$actual,"* ]]; then
    failures+=("http:$label:$actual")
  fi
}

check_redirect() {
  local label="$1"
  local url="$2"
  local expected_prefix="$3"
  local result status location
  checked_http=$((checked_http + 1))
  result="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}\n%{redirect_url}' --max-time 15 "$url" 2>/dev/null || true)"
  status="$(printf '%s\n' "$result" | sed -n '1p')"
  location="$(printf '%s\n' "$result" | sed -n '2p')"
  if [[ "$status" != "302" || "$location" != "$expected_prefix"* ]]; then
    failures+=("redirect:$label:$status")
  fi
}

for container in \
  abc4rd-academy-core \
  abc4rd-keycloak \
  abc4rd-keycloak-db \
  abc4rd-erpnext-frontend-1 \
  abc4rd-portal \
  abc4rd-portal-auth \
  abc4rd-matrix-db \
  abc4rd-synapse \
  abc4rd-element \
  abc4rd-rtc-redis \
  abc4rd-livekit \
  abc4rd-lk-jwt
do
  check_container "$container"
done

check_http academy "https://learn.abc4rd.org/" "200,302"
check_http studio "https://studio.abc4rd.org/" "200,302"
check_http identity "https://id.abc4rd.org/realms/abc4rd/.well-known/openid-configuration" "200"
check_http crm "https://crm.abc4rd.org/" "200,302"
check_redirect portal "https://app.abc4rd.org/" "https://id.abc4rd.org/realms/abc4rd/"
check_redirect mobile "https://app.abc4rd.org/mobile" "https://id.abc4rd.org/realms/abc4rd/"
check_http verification "https://verify.abc4rd.org/health" "200"
check_http library "https://library.abc4rd.org/" "302"
check_http messenger "https://chat.abc4rd.org/config.json" "200"
check_http matrix "https://matrix.abc4rd.org/_matrix/client/versions" "200"
check_http matrix-discovery "https://matrix.abc4rd.org/.well-known/matrix/client" "200"
check_http matrix-rtc-auth "https://matrix.abc4rd.org/livekit/jwt/healthz" "200"

if ! docker exec abc4rd-portal curl -fsS http://127.0.0.1:8080/mobile 2>/dev/null \
  | grep -Fq 'https://matrix.abc4rd.org'; then
  failures+=("portal:mobile-guide-missing")
fi

if ! ss -lnt | grep -q ':7881 '; then
  failures+=("port:livekit-tcp:closed")
fi
if ! ss -lnu | grep -q ':7882 '; then
  failures+=("port:livekit-udp:closed")
fi

state_file=/opt/abc4rd/portal/data/state.json
participant_count=0
certificate_count=0
if [[ -r "$state_file" ]]; then
  participant_count="$(jq -er '.participants | length' "$state_file" 2>/dev/null || printf '0')"
  certificate_count="$(jq -er '[.participants[].courses[].certificate?] | length' "$state_file" 2>/dev/null || printf '0')"
  state_age=$(( $(date +%s) - $(stat -c %Y "$state_file") ))
  (( participant_count >= 1 )) || failures+=("sync:no-participants")
  (( certificate_count >= 1 )) || failures+=("sync:no-certificates")
  (( state_age <= 600 )) || failures+=("sync:stale:${state_age}s")
else
  failures+=("sync:state-missing")
fi

if [[ "$(systemctl is-active abc4rd-pilot-sync.timer 2>/dev/null || true)" != "active" ]]; then
  failures+=("sync:timer-inactive")
fi

backup_dir=/opt/abc4rd/backups/runtime
latest_backup="$(find "$backup_dir" -maxdepth 1 -type f -name 'abc4rd-runtime-*.tar.gz.gpg' \
  -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
if [[ -z "$latest_backup" ]]; then
  failures+=("backup:runtime-missing")
else
  backup_age_seconds=$(( $(date +%s) - $(stat -c %Y "$latest_backup") ))
  backup_age_hours=$(( backup_age_seconds / 3600 ))
  (( backup_age_seconds >= 0 && backup_age_seconds <= 604800 )) \
    || failures+=("backup:runtime-stale:${backup_age_hours}h")
  checksum_file="${latest_backup}.sha256"
  if [[ ! -r "$checksum_file" ]]; then
    failures+=("backup:checksum-missing")
  else
    expected_checksum="$(tr -d '[:space:]' <"$checksum_file")"
    actual_checksum="$(sha256sum "$latest_backup" | cut -d' ' -f1)"
    [[ "$actual_checksum" == "$expected_checksum" ]] \
      || failures+=("backup:checksum-mismatch")
  fi
fi

timestamp="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
if (( ${#failures[@]} > 0 )); then
  printf 'ABC4RD_HEALTH FAIL at=%s failures=%s\n' "$timestamp" "$(IFS=,; printf '%s' "${failures[*]}")" >&2
  exit 1
fi

printf 'ABC4RD_HEALTH OK at=%s containers=%d http=%d participants=%s certificates=%s backup_age=%sh\n' \
  "$timestamp" "$checked_containers" "$checked_http" "$participant_count" \
  "$certificate_count" "$backup_age_hours"
