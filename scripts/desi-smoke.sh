#!/bin/bash
# Phase 7 smoke test cho cutover sang docker compose unified.
# Plan §19.9.6.
#
# Usage: ./scripts/desi-smoke.sh [DOMAIN]
#   DOMAIN mặc định "localhost"; production set "customer.delax.vn".

set -euo pipefail

DOMAIN="${1:-localhost}"
SCHEME="https"
if [[ "$DOMAIN" == "localhost" ]]; then
  SCHEME="http"
fi
BASE="$SCHEME://$DOMAIN"
CURL_OPTS="-sS --max-time 10"
[[ "$SCHEME" == "https" ]] && CURL_OPTS+=" -k"

pass() { printf "  [OK] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }

echo "1. Nginx healthz..."
curl $CURL_OPTS "$BASE/healthz" | grep -q '^ok$' && pass healthz || fail healthz

echo "2. Django web (expect 401 unauth)..."
code=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' "$BASE/api/users/me" || echo "000")
[[ "$code" == "401" ]] && pass "web 401" || fail "expected 401 got $code"

echo "3. Frontend HTML..."
curl $CURL_OPTS "$BASE/" | grep -q '<title' && pass "frontend html" || fail "frontend missing <title>"

echo "4. Postgres reachable từ web container..."
docker exec desi-web python -c "
import os, psycopg
url = f\"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@postgres:5432/{os.environ['POSTGRES_DB']}\"
psycopg.connect(url, connect_timeout=3).close()
print('OK')
" >/dev/null 2>&1 && pass "web→postgres" || fail "postgres unreachable from web"

echo "5. Go services up..."
docker exec desi-task-worker /app/bin/task-worker -config /app/config/task-worker.yaml -validate 2>&1 | grep -q 'config OK' && pass "task-worker config" || fail "task-worker config"
docker exec desi-task-cron   /app/bin/task-cron   -config /app/config/task-cron.yaml   -validate 2>&1 | grep -q 'config OK' && pass "task-cron config"   || fail "task-cron config"
docker exec desi-audit-writer /app/bin/audit-writer -config /app/config/audit-writer.yaml -validate 2>&1 | grep -q 'config OK' && pass "audit-writer config" || fail "audit-writer config"

echo "6. dispatch.Job table exists..."
docker exec desi-postgres psql -U "${POSTGRES_USER:-desi}" -d "${POSTGRES_DB:-desi}" -t -c "SELECT to_regclass('public.jobs');" | grep -q jobs && pass "jobs table" || fail "jobs table missing"

echo "7. Pattern B endpoint reachable (expect 401 without token)..."
code=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/internal/tasks/orders.sale_reminders/run" || echo "000")
[[ "$code" == "401" ]] && pass "internal 401" || fail "expected 401 got $code"

echo
echo "OK — all smoke checks passed for $BASE"
