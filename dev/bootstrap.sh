#!/usr/bin/env bash
# Run inside the bench container:
#   docker compose -f dev/compose.yaml exec bench bash /workspace/frappe-datev-erpnext/dev/bootstrap.sh
set -euo pipefail

APP_DIR=/workspace/frappe-datev-erpnext
CORE_DIR=/workspace/frappe-datev
BENCH_ROOT=/home/frappe/bench-data
BENCH_DIR=$BENCH_ROOT/frappe-bench
SITE=datev-erpnext.test

set -a
. "$APP_DIR/.github/versions.env"
set +a

fetch_pinned() {
  local url=$1 commit=$2 branch=$3 dir=$4
  rm -rf "$dir"
  git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 "$url" "$commit"
  git -C "$dir" checkout -q -b "$branch" FETCH_HEAD
}

sudo chown frappe:frappe "$BENCH_ROOT"
cd "$BENCH_ROOT"

if [ ! -d "$BENCH_DIR/apps/frappe" ]; then
  fetch_pinned https://github.com/frappe/frappe "$FRAPPE_COMMIT" "$FRAPPE_BRANCH" "$BENCH_ROOT/frappe-src"
  bench init --frappe-branch "$FRAPPE_BRANCH" --frappe-path "$BENCH_ROOT/frappe-src" \
    --skip-redis-config-generation --skip-assets frappe-bench
fi

cd "$BENCH_DIR"
bench set-config -g db_host db
bench set-config -g redis_cache redis://redis-cache:6379
bench set-config -g redis_queue redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379

if [ ! -d apps/erpnext ]; then
  fetch_pinned https://github.com/frappe/erpnext "$ERPNEXT_COMMIT" "$ERPNEXT_BRANCH" "$BENCH_ROOT/erpnext-src"
  bench get-app --branch "$ERPNEXT_BRANCH" "$BENCH_ROOT/erpnext-src"
fi

if [ ! -d apps/eu_einvoice ]; then
  fetch_pinned https://github.com/alyf-de/eu_einvoice "$EU_EINVOICE_COMMIT" "$EU_EINVOICE_BRANCH" "$BENCH_ROOT/eu_einvoice-src"
  bench get-app --branch "$EU_EINVOICE_BRANCH" "$BENCH_ROOT/eu_einvoice-src"
fi

[ -d apps/datev ] || bench get-app --soft-link "$CORE_DIR"
[ -d apps/datev_erpnext ] || bench get-app --soft-link "$APP_DIR"

if [ ! -d "sites/$SITE" ]; then
  bench new-site "$SITE" --db-root-password 123 --admin-password admin --mariadb-user-host-login-scope=%
fi

for app in erpnext eu_einvoice datev datev_erpnext; do
  bench --site "$SITE" list-apps | grep -qx "$app" || bench --site "$SITE" install-app "$app"
done
bench --site "$SITE" set-config allow_tests true
bench --site "$SITE" set-config developer_mode 1

[ -d sites/assets/frappe/dist ] || bench build
