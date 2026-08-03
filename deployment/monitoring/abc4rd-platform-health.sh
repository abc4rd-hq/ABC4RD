#!/usr/bin/env bash
set -uo pipefail

failures=()
checked_containers=0
checked_http=0
checked_tls=0
tls_min_days=99999
backup_age_hours=unknown
disk_used_percent=unknown
inode_used_percent=unknown
memory_available_percent=unknown
failed_units=unknown
reboot_required=no

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

check_tls() {
  local label="$1"
  local host="$2"
  local end_date expiry_epoch remaining_seconds remaining_days
  checked_tls=$((checked_tls + 1))
  end_date="$(
    timeout 15 openssl s_client -connect "${host}:443" -servername "$host" </dev/null 2>/dev/null \
      | openssl x509 -noout -enddate 2>/dev/null \
      | sed 's/^notAfter=//'
  )"
  expiry_epoch="$(date --date="$end_date" +%s 2>/dev/null || printf '0')"
  if [[ -z "$end_date" || "$expiry_epoch" == "0" ]]; then
    failures+=("tls:$label:unreadable")
    return
  fi
  remaining_seconds=$(( expiry_epoch - $(date +%s) ))
  remaining_days=$(( remaining_seconds / 86400 ))
  (( remaining_days < tls_min_days )) && tls_min_days=$remaining_days
  (( remaining_seconds >= 1209600 )) \
    || failures+=("tls:$label:expires-in-${remaining_days}d")
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

check_tls academy learn.abc4rd.org
check_tls studio studio.abc4rd.org
check_tls identity id.abc4rd.org
check_tls crm crm.abc4rd.org
check_tls portal app.abc4rd.org
check_tls verification verify.abc4rd.org
check_tls library library.abc4rd.org
check_tls messenger chat.abc4rd.org
check_tls matrix matrix.abc4rd.org
[[ "$tls_min_days" != "99999" ]] || tls_min_days=unknown

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

disk_used_percent="$(df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
if [[ ! "$disk_used_percent" =~ ^[0-9]+$ ]]; then
  failures+=("host:disk-unreadable")
elif (( disk_used_percent >= 85 )); then
  failures+=("host:disk-${disk_used_percent}pct")
fi

inode_used_percent="$(df -Pi / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
if [[ ! "$inode_used_percent" =~ ^[0-9]+$ ]]; then
  failures+=("host:inodes-unreadable")
elif (( inode_used_percent >= 85 )); then
  failures+=("host:inodes-${inode_used_percent}pct")
fi

memory_available_percent="$(awk '
  /^MemTotal:/ { total=$2 }
  /^MemAvailable:/ { available=$2 }
  END { if (total > 0) printf "%d", (available * 100) / total; else print "0" }
' /proc/meminfo 2>/dev/null)"
if [[ ! "$memory_available_percent" =~ ^[0-9]+$ ]]; then
  failures+=("host:memory-unreadable")
elif (( memory_available_percent < 15 )); then
  failures+=("host:memory-available-${memory_available_percent}pct")
fi

failed_units="$(systemctl list-units --failed --no-legend --plain --no-pager 2>/dev/null \
  | awk 'NF { count++ } END { print count + 0 }')"
if [[ ! "$failed_units" =~ ^[0-9]+$ ]]; then
  failures+=("host:failed-units-unreadable")
elif (( failed_units > 0 )); then
  failures+=("host:failed-units-${failed_units}")
fi

if [[ -e /run/reboot-required ]]; then
  reboot_required=yes
  failures+=("host:reboot-required")
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

printf 'ABC4RD_HEALTH OK at=%s containers=%d http=%d tls=%d tls_min=%sd participants=%s certificates=%s backup_age=%sh disk=%s%% inodes=%s%% mem_available=%s%% failed_units=%s reboot=%s\n' \
  "$timestamp" "$checked_containers" "$checked_http" "$checked_tls" \
  "$tls_min_days" "$participant_count" "$certificate_count" "$backup_age_hours" \
  "$disk_used_percent" "$inode_used_percent" "$memory_available_percent" \
  "$failed_units" "$reboot_required"
