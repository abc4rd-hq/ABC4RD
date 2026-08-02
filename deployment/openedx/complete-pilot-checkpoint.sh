#!/usr/bin/env bash
set -euo pipefail

readonly OP_WRAPPER="/Users/dom/.codex/bin/op-sa.sh"
readonly LMS_URL="https://learn.abc4rd.org"
readonly LOGIN_URL="$LMS_URL/api/user/v1/account/login_session/"
readonly COURSE_KEY="course-v1:ABC4RD+0001+2026"
readonly HTML_KEY="block-v1:ABC4RD+0001+2026+type@html+block@pilot_welcome_text"
readonly PROBLEM_KEY="block-v1:ABC4RD+0001+2026+type@problem+block@pilot_route_check"

tmp_dir="$(mktemp -d /tmp/abc4rd-pilot-checkpoint.XXXXXX)"
cookie_file="$tmp_dir/cookie"
login_file="$tmp_dir/login.json"
html_file="$tmp_dir/html-xblock.html"
problem_file="$tmp_dir/problem-xblock.html"
problem_view_file="$tmp_dir/problem-xblock.json"
completion_file="$tmp_dir/completion.json"
answer_file="$tmp_dir/answer.json"
progress_before_file="$tmp_dir/progress-before.json"
progress_after_file="$tmp_dir/progress-after.json"

cleanup() {
  for file in \
    "$cookie_file" \
    "$login_file" \
    "$html_file" \
    "$problem_file" \
    "$problem_view_file" \
    "$completion_file" \
    "$answer_file" \
    "$progress_before_file" \
    "$progress_after_file"; do
    if [[ -f "$file" ]]; then
      unlink "$file"
    fi
  done
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
  --output "$login_file" \
  --write-out '%{http_code}' \
  "$LOGIN_URL")"

unset pilot_password

if [[ "$login_status" != "200" ]] || [[ "$(jq -r '.success' "$login_file")" != "true" ]]; then
  printf 'PILOT_LOGIN=failed|HTTP=%s\n' "$login_status" >&2
  exit 1
fi
printf 'PILOT_LOGIN=verified\n'
csrf_token="$(awk '$6 == "csrftoken" {print $7}' "$cookie_file" | tail -1)"

curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  "$LMS_URL/api/course_home/v1/progress/$COURSE_KEY" \
  --output "$progress_before_file"

complete_before="$(jq -r '.completion_summary.complete_count // 0' "$progress_before_file")"
incomplete_before="$(jq -r '.completion_summary.incomplete_count // 0' "$progress_before_file")"
if [[ "$complete_before" -ge 2 ]] && [[ "$incomplete_before" -eq 0 ]]; then
  printf 'PILOT_CHECKPOINT=already_complete\n'
  printf 'PROGRESS='
  jq -c '{completion_summary,course_grade}' "$progress_before_file"
  exit 0
fi

curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  "$LMS_URL/xblock/$HTML_KEY" \
  --output "$html_file"

if ! grep -q 'Технический пилот ABC4RD' "$html_file"; then
  printf 'HTML_XBLOCK=invalid\n' >&2
  exit 1
fi
printf 'HTML_XBLOCK=verified\n'

completion_status="$(curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  --header "X-CSRFToken: $csrf_token" \
  --header "Referer: $LMS_URL/xblock/$HTML_KEY" \
  --header 'Content-Type: application/json' \
  --data '{"completion":1}' \
  --output "$completion_file" \
  --write-out '%{http_code}' \
  "$LMS_URL/courses/$COURSE_KEY/xblock/$HTML_KEY/handler/publish_completion")"

if [[ "$completion_status" != "200" ]] || [[ "$(jq -r '.result' "$completion_file")" != "ok" ]]; then
  printf 'HTML_COMPLETION=failed|HTTP=%s\n' "$completion_status" >&2
  head -c 500 "$completion_file" >&2
  printf '\n' >&2
  exit 1
fi
printf 'HTML_COMPLETION=verified\n'

problem_view_status="$(curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  --header "X-CSRFToken: $csrf_token" \
  --header "Referer: $LMS_URL/xblock/$PROBLEM_KEY" \
  --request POST \
  "$LMS_URL/courses/$COURSE_KEY/xblock/$PROBLEM_KEY/handler/xmodule_handler/problem_get" \
  --output "$problem_view_file" \
  --write-out '%{http_code}')"

if [[ "$problem_view_status" != "200" ]] || ! jq -e '.html | type == "string"' "$problem_view_file" >/dev/null; then
  printf 'PROBLEM_VIEW=failed|HTTP=%s|' "$problem_view_status" >&2
  jq -c '{keys:keys,error,developer_message,detail}' "$problem_view_file" >&2
  exit 1
fi

jq -r '.html' "$problem_view_file" > "$problem_file"

answer_pair="$(python3 - "$problem_file" <<'PY'
from html.parser import HTMLParser
import sys

TARGET = "Работу маршрута слушателя на платформе"


class ChoiceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}
        self.labels = []
        self.current_label = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "label":
            self.current_label = {"for": attrs.get("for"), "text": [], "input": None}
        elif tag == "input" and attrs.get("type") == "radio":
            item = {
                "id": attrs.get("id"),
                "name": attrs.get("name"),
                "value": attrs.get("value"),
            }
            if item["id"]:
                self.inputs[item["id"]] = item
            if self.current_label is not None:
                self.current_label["input"] = item

    def handle_data(self, data):
        if self.current_label is not None:
            self.current_label["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "label" and self.current_label is not None:
            self.labels.append(self.current_label)
            self.current_label = None


parser = ChoiceParser()
with open(sys.argv[1], encoding="utf-8") as source:
    parser.feed(source.read())

for label in parser.labels:
    text = " ".join("".join(label["text"]).split())
    if TARGET in text:
        item = label["input"] or parser.inputs.get(label["for"])
        if item and item["name"] and item["value"]:
            print(f'{item["name"]}\t{item["value"]}')
            raise SystemExit(0)

raise SystemExit("Correct pilot choice was not found in rendered problem HTML")
PY
)"

IFS=$'\t' read -r answer_name answer_value <<< "$answer_pair"

answer_status="$(curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  --header "X-CSRFToken: $csrf_token" \
  --header "Referer: $LMS_URL/xblock/$PROBLEM_KEY" \
  --data-urlencode "$answer_name=$answer_value" \
  --output "$answer_file" \
  --write-out '%{http_code}' \
  "$LMS_URL/courses/$COURSE_KEY/xblock/$PROBLEM_KEY/handler/xmodule_handler/problem_check")"

if [[ "$answer_status" != "200" ]]; then
  printf 'PROBLEM_SUBMISSION=failed|HTTP=%s\n' "$answer_status" >&2
  exit 1
fi

answer_result="$(jq -r '.success // .contents.success // empty' "$answer_file")"
if [[ "$answer_result" != "correct" ]]; then
  printf 'PROBLEM_SUBMISSION=unexpected|result=%s\n' "$answer_result" >&2
  jq -c '{success,contents}' "$answer_file" >&2
  exit 1
fi
printf 'PROBLEM_SUBMISSION=correct\n'

curl --silent --show-error --connect-timeout 10 \
  --cookie "$cookie_file" \
  "$LMS_URL/api/course_home/v1/progress/$COURSE_KEY" \
  --output "$progress_after_file"

printf 'PROGRESS_BEFORE='
jq -c '{completion_summary,course_grade}' "$progress_before_file"
printf 'PROGRESS_AFTER='
jq -c '{completion_summary,course_grade}' "$progress_after_file"
