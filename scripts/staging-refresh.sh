#!/bin/bash
# Refresh dữ liệu STAGING từ dump backup prod (docs/staging-environment.md §5).
#
# Chạy TRÊN HOST STAGING (không bao giờ trên prod):
#   DESI_STAGING_DUMP=/opt/desi-staging/desi_YYYYMMDD-0200.dump.gz ./scripts/staging-refresh.sh
#
# Các bước: verify checksum → stop app → drop/create DB → pg_restore -j →
# migrate → staging_neutralize --yes → start app → smoke check.
# Dữ liệu tester đã ghi trên staging BỊ XÓA TOÀN BỘ mỗi lần refresh (chủ đích).

set -euo pipefail

DUMP="${DESI_STAGING_DUMP:?DESI_STAGING_DUMP=/path/to/desi_*.dump.gz là bắt buộc}"
PG_CONTAINER="${DESI_STAGING_PG_CONTAINER:-desi-staging-postgres}"
WEB_CONTAINER="${DESI_STAGING_WEB_CONTAINER:-desi-staging-web}"
COMPOSE="${DESI_STAGING_COMPOSE:-docker compose -f docker-compose.staging.yml --env-file .env.staging}"
PG_USER="${DESI_STAGING_PG_USER:-desi}"
PG_DB="${DESI_STAGING_PG_DB:-desi}"
RESTORE_JOBS="${DESI_STAGING_RESTORE_JOBS:-4}"

log() { echo "$(date -Iseconds) $*"; }
fail() { log "REFRESH FAILED: $*" >&2; exit 1; }
trap 'fail "lỗi không mong đợi ở dòng $LINENO"' ERR

# ── Guard chống chạy nhầm prod ──────────────────────────────────────────────
# Tên container prod bị blacklist cứng — kể cả ai đó export nhầm biến env.
case "$PG_CONTAINER" in
  desi-postgres|desi-order-postgres-1)
    fail "PG_CONTAINER='$PG_CONTAINER' là container PROD — từ chối chạy." ;;
esac
docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || fail "container '$PG_CONTAINER' không tồn tại"
# Container phải thuộc compose project staging (label do compose gắn).
PROJECT=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$PG_CONTAINER")
[ "$PROJECT" = "desi-order-staging" ] \
  || fail "container '$PG_CONTAINER' thuộc project '$PROJECT', không phải desi-order-staging"

[ -f "$DUMP" ] || fail "không thấy file dump: $DUMP"

# ── 1) Verify dump (checksum nếu có + TOC đọc được) ─────────────────────────
# File .sha256 do desi-backup.sh ghi bằng ĐƯỜNG DẪN TUYỆT ĐỐI phía prod
# (/opt/desi-backup/...), còn dump trên staging nằm chỗ khác → `sha256sum -c`
# nguyên văn mở path không tồn tại và fail MỌI lần chạy (audit 2026-07-18).
# Tự dựng dòng check từ hash + basename để so đúng file local.
if [ -f "$DUMP.sha256" ]; then
  EXPECTED_HASH=$(cut -d' ' -f1 "$DUMP.sha256")
  (cd "$(dirname "$DUMP")" \
    && echo "$EXPECTED_HASH  $(basename "$DUMP")" | sha256sum -c -) \
    || fail "checksum không khớp: $DUMP"
  log "checksum OK"
else
  log "WARNING: không có $DUMP.sha256 — bỏ qua bước checksum"
fi
gunzip -t "$DUMP" || fail "file gzip hỏng: $DUMP"
gunzip -c "$DUMP" | docker exec -i "$PG_CONTAINER" pg_restore --list >/dev/null \
  || fail "dump không hợp lệ (pg_restore --list thất bại)"
log "dump hợp lệ: $DUMP"

# ── 2) Stop app (giữ postgres chạy) ─────────────────────────────────────────
# worker-export PHẢI có trong danh sách: nó có `restart: unless-stopped` nên nếu
# bỏ sót, nó vẫn sống suốt bước drop/restore/neutralize bên dưới — reconnect lại
# sau pg_terminate_backend rồi tiêu thụ export_jobs PENDING vừa restore từ dump
# prod, sinh file Excel chứa PII khách hàng THẬT trước khi neutralize kịp chạy
# (đúng cửa sổ mà audit 2026-07-18 đã bịt cho web).
# worker-foxia (spec 2026-09-09, queue `foxia,foxia_push`) cùng lý do y hệt: bỏ
# sót nó thì nó sống sót qua drop/restore/neutralize và có thể đẩy khách của đơn
# vừa restore từ dump prod sang Foxia THẬT trước khi staging_neutralize kịp chạy.
log "stop web/worker..."
$COMPOSE stop web worker worker-kiotviet worker-slow worker-export worker-foxia

# ── 3) Drop + create DB rồi restore ─────────────────────────────────────────
log "drop/create database $PG_DB..."
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_DB' AND pid <> pg_backend_pid();" >/dev/null
docker exec "$PG_CONTAINER" dropdb -U "$PG_USER" --if-exists "$PG_DB"
docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$PG_DB"

# pg_restore -j cần file trong container (stdin không parallel được) — copy vào
# rồi xóa. --no-owner/--no-privileges: role prod không tồn tại trên staging.
log "restore (jobs=$RESTORE_JOBS)... (DB lớn có thể mất hàng giờ)"
TMP_IN_CONTAINER=/tmp/staging-restore.dump
gunzip -c "$DUMP" | docker exec -i "$PG_CONTAINER" sh -c "cat > $TMP_IN_CONTAINER"
docker exec "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$PG_DB" \
  --no-owner --no-privileges -j "$RESTORE_JOBS" "$TMP_IN_CONTAINER" \
  || fail "pg_restore lỗi"
docker exec "$PG_CONTAINER" rm -f "$TMP_IN_CONTAINER"
log "restore xong"

# ── 4) Migrate + neutralize TRƯỚC khi bất kỳ container app nào chạy ─────────
# Thứ tự cũ (start web → neutralize) mở ra cửa sổ web serve DB còn NGUYÊN secrets
# prod: token KiotViet plaintext còn hạn trong dump + EncryptedCharField fallback
# raw khiến mọi đường sync trong request bắn THẬT lên KiotViet prod; neutralize
# fail thì web cũng bị bỏ chạy tiếp (audit 2026-07-18). `compose run --rm` chạy
# one-off container, không mở cổng serve.
log "migrate (one-off, chưa mở web)..."
$COMPOSE run --rm web python manage.py migrate --noinput \
  || fail "migrate lỗi — dừng trước khi mở staging"

log "staging_neutralize (BẮT BUỘC trước khi mở web)..."
$COMPOSE run --rm web python manage.py staging_neutralize --yes \
  || fail "staging_neutralize lỗi — DB đang còn secrets prod, KHÔNG mở staging cho user"

# ── 5) Start web ────────────────────────────────────────────────────────────
log "start web..."
$COMPOSE start web
for i in $(seq 1 60); do
  STATUS=$(docker inspect -f '{{.State.Health.Status}}' "$WEB_CONTAINER" 2>/dev/null || echo starting)
  [ "$STATUS" = "healthy" ] && break
  sleep 5
done
[ "$STATUS" = "healthy" ] || {
  $COMPOSE stop web worker worker-kiotviet worker-slow worker-export worker-foxia || true
  fail "web không healthy sau restore (docker logs $WEB_CONTAINER) — đã stop app"
}

# ── 6) Start worker + smoke ─────────────────────────────────────────────────
$COMPOSE start worker worker-kiotviet worker-slow worker-export worker-foxia
docker exec "$WEB_CONTAINER" python -c "
import urllib.request, sys
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/orders/metrics', timeout=5).status == 200 else 1)
" || {
  $COMPOSE stop web worker worker-kiotviet worker-slow worker-export worker-foxia || true
  fail "smoke check API lỗi — đã stop app, không mở staging cho user"
}

log "REFRESH OK — staging đang chạy snapshot: $(basename "$DUMP")"
