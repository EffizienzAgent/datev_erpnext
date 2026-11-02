#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
args=""
if [ $# -gt 0 ]; then
	args=$(printf '%q ' "$@")
fi
docker compose -f dev/compose.yaml exec bench bash -lc "cd frappe-bench && bench --site datev-erpnext.test run-tests --app datev_erpnext $args"
