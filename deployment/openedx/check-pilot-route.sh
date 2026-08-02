#!/usr/bin/env bash
set -euo pipefail

readonly OP_WRAPPER="/Users/dom/.codex/bin/op-sa.sh"
readonly LOGIN_URL="https://learn.abc4rd.org/api/user/v1/account/login_session/"
readonly COURSE_KEY="course-v1:ABC4RD+0001+2026"

tmp_dir="$(mktemp -d /tmp/abc4rd-pilot-route.XXXXXX)"
cookie_file="$tmp_dir/cookie"
response_file="$tmp_dir/login-response.json"
about_file="$tmp_dir/course-about.html"

cleanup() {
  if [[ -f "$cookie_file" ]]; then
    unlink "$cookie_file"
  fi
  if [[ -f "$response_file" ]]; then
    unlink "$response_file"
  fi
  if [[ -f "$about_file" ]]; then
    unlink "$about_file"
  fi
  rmdir "$tmp_dir"
}
trap cleanup EXIT

curl --silent --show-error --connect-timeout 10 \
  --cookie-jar "$cookie_file" \
  "$LOGIN_URL" \
  --output /dev/null

csrf_token="$(awk '$6 == "csrftoken" {print $7}' "$cookie_file" | tail -1)"
pilot_password="$($OP_WRAPPER item get 'ABC4RD PILOT-001 OPEN EDX' \
  --vault ABC4RD \
  --fields label=password \
  --reveal)"

login_status="$(curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  --cookie-jar "$cookie_file" \
  --header "X-CSRFToken: $csrf_token" \
  --header 'Referer: https://apps.learn.abc4rd.org/authn/login' \
  --data-urlencode 'email=pilot-001@abc4rd.invalid' \
  --data-urlencode "password=$pilot_password" \
  --output "$response_file" \
  --write-out '%{http_code}' \
  "$LOGIN_URL")"

unset pilot_password csrf_token

printf 'LOGIN_HTTP=%s\n' "$login_status"
jq -c '{success,redirect_url,error_code,field_errors,value_error}' "$response_file"

curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  --output "$about_file" \
  "https://learn.abc4rd.org/courses/$COURSE_KEY/about"

if grep -q 'О техническом пилоте' "$about_file" && \
   ! grep -q 'Include your long course description here' "$about_file"; then
  printf 'COURSE_ABOUT=verified\n'
else
  printf 'COURSE_ABOUT=invalid\n' >&2
  exit 1
fi

paths=(
  "/dashboard"
  "/api/course_home/v1/outline/$COURSE_KEY"
  "/api/course_home/v1/course_metadata/$COURSE_KEY"
  "/api/course_home/v1/progress/$COURSE_KEY"
)

for path in "${paths[@]}"; do
  status="$(curl --silent --show-error --connect-timeout 10 \
    --cookie "$cookie_file" \
    --output /dev/null \
    --write-out '%{http_code}' \
    "https://learn.abc4rd.org$path")"
  printf 'AUTH_GET=%s|HTTP=%s\n' "$path" "$status"
done
