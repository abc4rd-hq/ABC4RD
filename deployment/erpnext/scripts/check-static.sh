#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$base_dir"

find container scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
python3 -m compileall -q apps/abc4rd_crm
python3 scripts/validate-schema.py

if command -v shellcheck >/dev/null 2>&1; then
  find container scripts -type f -name '*.sh' -print0 | xargs -0 shellcheck
else
  echo "SKIP: shellcheck is not installed"
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose --env-file .env.example config --quiet
elif command -v ruby >/dev/null 2>&1; then
  ruby -e 'require "yaml"; YAML.safe_load(File.read("docker-compose.yml"), [], [], true)'
  ruby -e 'require "yaml"; YAML.safe_load(File.read("docker-compose.restore-drill.yml"), [], [], true)'
  echo "PASS: Compose YAML files parsed with Ruby YAML"
else
  echo "SKIP: docker compose and Ruby YAML are not available"
fi

echo "PASS: ERPNext preparation passed local static checks"
