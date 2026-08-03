#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-https://learn.abc4rd.org}"
expected_site_name="ABC4RD Academy"
expected_theme_url="https://verify.abc4rd.org/static/openedx-theme.css"

for command_name in curl jq; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: ${command_name} is required" >&2
    exit 1
  fi
done

config_json="$(curl --fail --silent --show-error --max-time 20 "${base_url}/api/mfe_config/v1")"

jq --exit-status \
  --arg site_name "${expected_site_name}" \
  --arg theme_url "${expected_theme_url}" \
  '
    .SITE_NAME == $site_name and
    .PARAGON_THEME_URLS.core.urls.default == "https://cdn.jsdelivr.net/npm/@openedx/paragon@$paragonVersion/dist/core.min.css" and
    .PARAGON_THEME_URLS.core.urls.brandOverride == $theme_url and
    .PARAGON_THEME_URLS.defaults.light == "light" and
    .PARAGON_THEME_URLS.defaults.dark == "dark" and
    .PARAGON_THEME_URLS.variants.light.urls.default == $theme_url and
    .PARAGON_THEME_URLS.variants.light.urls.brandOverride == $theme_url and
    .PARAGON_THEME_URLS.variants.dark.urls.default == $theme_url and
    .PARAGON_THEME_URLS.variants.dark.urls.brandOverride == $theme_url
  ' <<<"${config_json}" >/dev/null

curl --fail --silent --show-error --max-time 20 \
  --output /dev/null "${expected_theme_url}"

echo "OK: ABC4RD dark theme is published for Open edX micro-frontends"
