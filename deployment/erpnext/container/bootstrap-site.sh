#!/usr/bin/env bash
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"

if [[ ! "$SITE_NAME" =~ ^[a-z0-9][a-z0-9.-]+$ ]]; then
  echo "SITE_NAME must be a lowercase DNS name" >&2
  exit 2
fi

for secret_file in /run/secrets/db_root_password /run/secrets/administrator_password; do
  if [[ ! -s "$secret_file" ]]; then
    echo "Required secret file is missing or empty: $secret_file" >&2
    exit 2
  fi
done

db_root_password="$(tr -d '\r\n' < /run/secrets/db_root_password)"
administrator_password="$(tr -d '\r\n' < /run/secrets/administrator_password)"

if [[ ! -d "sites/$SITE_NAME" ]]; then
  bench new-site "$SITE_NAME" \
    --mariadb-user-host-login-scope='%' \
    --db-root-username=root \
    --db-root-password "$db_root_password" \
    --admin-password "$administrator_password"
fi

installed_apps="$(
  bench --site "$SITE_NAME" list-apps --format json \
    | python -c 'import json, sys; print("\n".join(next(iter(json.load(sys.stdin).values()))))'
)"
for app in erpnext abc4rd_crm; do
  if ! grep -Fxq "$app" <<<"$installed_apps"; then
    bench --site "$SITE_NAME" install-app "$app"
  fi
done

bench --site "$SITE_NAME" set-config host_name "https://$SITE_NAME"
bench --site "$SITE_NAME" set-config --parse developer_mode 0
bench --site "$SITE_NAME" migrate
bench --site "$SITE_NAME" enable-scheduler
bench --site "$SITE_NAME" clear-cache
bench --site "$SITE_NAME" list-apps
