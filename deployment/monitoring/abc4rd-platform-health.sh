#!/usr/bin/env bash
set -uo pipefail

failures=()
checked_containers=0

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
  actual="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15 "$url" 2>/dev/null || true)"
  if [[ ! ",$expected," == *",$actual,"* ]]; then
    failures+=("http:$label:$actual")
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
check_http portal "https://app.abc4rd.org/" "302"
check_http verification "https://verify.abc4rd.org/health" "200"
check_http library "https://library.abc4rd.org/" "302"
check_http messenger "https://chat.abc4rd.org/config.json" "200"
check_http matrix "https://matrix.abc4rd.org/_matrix/client/versions" "200"
check_http matrix-discovery "https://matrix.abc4rd.org/.well-known/matrix/client" "200"
check_http matrix-rtc-auth "https://matrix.abc4rd.org/livekit/jwt/healthz" "200"

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

timestamp="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
if (( ${#failures[@]} > 0 )); then
  printf 'ABC4RD_HEALTH FAIL at=%s failures=%s\n' "$timestamp" "$(IFS=,; printf '%s' "${failures[*]}")" >&2
  exit 1
fi

printf 'ABC4RD_HEALTH OK at=%s containers=%d participants=%s certificates=%s\n' \
  "$timestamp" "$checked_containers" "$participant_count" "$certificate_count"
