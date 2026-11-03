#!/bin/bash
# Daily Postgres backup cron host (plan §19.8 / §19.9.5).
#
# Cài đặt:
#   sudo cp scripts/desi-backup.sh /usr/local/bin/desi-backup.sh
#   sudo chmod +x /usr/local/bin/desi-backup.sh
#   # Thư mục backup — MỘT LẦN, cần root vì /opt không cho user thường ghi.
#   # Thiếu bước này thì script chết ở `mkdir` với "Permission denied".
#   sudo install -d -o desi -g desi -m 750 /opt/desi-backup
#   # Env (offsite + heartbeat) đặt ở /etc/desi-backup.env (chmod 600, xem
#   # docs/ops-hardening-2026-07.md) — cron source file này trước khi chạy:
#   echo '0 2 * * * desi . /etc/desi-backup.env && /usr/local/bin/desi-backup.sh >> /var/log/desi-backup.log 2>&1' | sudo tee /etc/cron.d/desi-backup
#
# ⚠️ KHÔNG bao giờ ghi backup vào trong repo git (chứa PII). Mặc định ghi ra
# /opt/desi-backup và (tuỳ chọn) đẩy lên object storage mã hoá.

set -euo pipefail

DATE=$(date +%Y%m%d-%H%M)
DIR="${DESI_BACKUP_DIR:-/opt/desi-backup}"
RETENTION_DAYS="${DESI_BACKUP_RETENTION_DAYS:-14}"
PG_CONTAINER="${DESI_PG_CONTAINER:-desi-postgres}"
PG_USER="${DESI_PG_USER:-desi}"
PG_DB="${DESI_PG_DB:-desi}"
# Offsite (tuỳ chọn nhưng NÊN bật — RB.3): backup chỉ nằm trên VPS = mất cả DB
# lẫn backup khi VPS chết. Đích offsite phải là storage KHÁC VPS:
#   - MinIO/B2/S3:  DESI_BACKUP_S3_DEST=s3://bucket/prefix (aws cli; với MinIO/B2
#     thêm --endpoint-url qua AWS_ENDPOINT_URL hoặc cấu hình profile aws)
#   - rclone:       DESI_BACKUP_RCLONE_DEST=remote:bucket/prefix
# Ép bắt buộc offsite: DESI_BACKUP_REQUIRE_OFFSITE=1 → thiếu đích thì exit 1
# (cron sẽ báo lỗi + metric không được ghi → BackupStale sẽ bắn).
S3_DEST="${DESI_BACKUP_S3_DEST:-}"
RCLONE_DEST="${DESI_BACKUP_RCLONE_DEST:-}"
REQUIRE_OFFSITE="${DESI_BACKUP_REQUIRE_OFFSITE:-0}"
# Heartbeat healthchecks.io (tuỳ chọn, docs/ops-hardening-2026-07.md): bắn
# /start lúc bắt đầu, ping gốc khi xong, /fail kèm log khi lỗi → phát hiện
# "cron không chạy / backup fail" từ NGOÀI VPS. Trống = tắt.
HEARTBEAT_URL="${DESI_BACKUP_HEARTBEAT_URL:-}"
# Textfile collector của node-exporter (bằng chứng sống cho alert BackupStale).
# Để trống để tắt; skip im lặng nếu thư mục không tồn tại.
METRIC_DIR="${DESI_BACKUP_METRIC_DIR:-/var/lib/node_exporter/textfile}"

log() { echo "$(date -Iseconds) $*"; }

# Heartbeat không bao giờ được làm hỏng backup: curl lỗi thì nuốt (|| true).
ping_heartbeat() { # $1 = "" | "/start" | "/fail"; $2 = body (tuỳ chọn)
  [ -n "$HEARTBEAT_URL" ] || return 0
  curl -fsS -m 10 --retry 2 -o /dev/null --data-raw "${2:-}" "${HEARTBEAT_URL}${1}" || true
}

fail() {
  log "BACKUP FAILED: $*" >&2
  ping_heartbeat "/fail" "$*"
  exit 1
}
trap 'fail "lỗi không mong đợi ở dòng $LINENO"' ERR

if [ "$REQUIRE_OFFSITE" = "1" ] && [ -z "$S3_DEST" ] && [ -z "$RCLONE_DEST" ]; then
  fail "DESI_BACKUP_REQUIRE_OFFSITE=1 nhưng chưa cấu hình DESI_BACKUP_S3_DEST / DESI_BACKUP_RCLONE_DEST"
fi

ping_heartbeat "/start"

# Thư mục backup phải TỒN TẠI và GHI ĐƯỢC bằng user chạy cron (thường là `desi`).
# Mặc định /opt/desi-backup nằm dưới /opt vốn chỉ root ghi được, nên `mkdir -p`
# trần sẽ chết với "Permission denied" và trap ERR chỉ báo được số dòng — người
# trực đêm không biết phải làm gì. Ở đây báo thẳng lệnh cần chạy.
#
# KHÔNG tự `sudo`: script chạy từ cron dưới user thường; tự nâng quyền là đường
# ngắn nhất tới một cron chạy root đẻ ra file root-owned mà retention sau đó
# không xoá nổi.
# `id -gn` kêu ra stderr khi không phân giải được tên group (container/LDAP rớt) —
# fallback về gid số để thông điệp hướng dẫn không bị nhiễu trong log cron.
RUN_USER=$(id -un)
RUN_GROUP=$(id -gn 2>/dev/null) || RUN_GROUP=$(id -g)

if [ ! -d "$DIR" ]; then
  mkdir -p "$DIR" 2>/dev/null || fail "thư mục backup '$DIR' chưa tồn tại và user '$RUN_USER' không tạo được (thường vì /opt cần root). Chạy MỘT LẦN với quyền root:
    sudo install -d -o $RUN_USER -g $RUN_GROUP -m 750 '$DIR'
  Hoặc trỏ sang chỗ khác ghi được: DESI_BACKUP_DIR=/duong/dan/khac"
fi

if [ ! -w "$DIR" ]; then
  fail "thư mục backup '$DIR' có tồn tại nhưng user '$RUN_USER' không ghi được. Sửa quyền sở hữu:
    sudo chown -R $RUN_USER:$RUN_GROUP '$DIR' && sudo chmod 750 '$DIR'"
fi

DUMP_FILE="$DIR/desi_${DATE}.dump"
GZ_FILE="$DUMP_FILE.gz"

# 1) Dump (custom format) + nén
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -F c "$PG_DB" > "$DUMP_FILE" \
  || fail "pg_dump lỗi"
gzip "$DUMP_FILE"

# 2) Verify tính toàn vẹn: gzip OK + TOC của pg dump đọc được (không cần restore thật)
gunzip -t "$GZ_FILE" || fail "file gzip hỏng: $GZ_FILE"
# `pg_restore --list` chỉ đọc TOC rồi ĐÓNG ống -> gunzip nhận SIGPIPE (141). Với
# `set -o pipefail` cả pipeline bị coi là lỗi dù dump hoàn toàn hợp lệ: đó là lý
# do backup đêm "fail" liên tục mà file dump vẫn đọc được (sự cố 22/09/2026).
# Tính toàn vẹn của file nén đã được `gunzip -t` ở trên bảo chứng, nên ở đây chỉ
# xét mã thoát của pg_restore.
set +o pipefail
gunzip -c "$GZ_FILE" | docker exec -i "$PG_CONTAINER" pg_restore --list >/dev/null
PG_RESTORE_RC=${PIPESTATUS[1]}
set -o pipefail
[ "$PG_RESTORE_RC" = 0 ] \
  || fail "pg dump không hợp lệ (pg_restore --list thất bại, rc=$PG_RESTORE_RC): $GZ_FILE"

# 3) Checksum để đối chiếu khi khôi phục
sha256sum "$GZ_FILE" | tee "$GZ_FILE.sha256" >/dev/null

SIZE=$(du -h "$GZ_FILE" | cut -f1)
log "backup OK & verified: $GZ_FILE ($SIZE)"

# 4) Offsite upload (chỉ chạy nếu cấu hình đích) — đẩy cả file + checksum
OFFSITE_CONFIGURED=0
if [ -n "$S3_DEST" ]; then
  OFFSITE_CONFIGURED=1
  aws s3 cp "$GZ_FILE" "$S3_DEST/" && aws s3 cp "$GZ_FILE.sha256" "$S3_DEST/" \
    && log "offsite (s3) OK: $S3_DEST" || fail "đẩy S3 lỗi"
elif [ -n "$RCLONE_DEST" ]; then
  OFFSITE_CONFIGURED=1
  rclone copy "$GZ_FILE" "$RCLONE_DEST/" && rclone copy "$GZ_FILE.sha256" "$RCLONE_DEST/" \
    && log "offsite (rclone) OK: $RCLONE_DEST" || fail "đẩy rclone lỗi"
else
  log "WARNING: chưa cấu hình offsite (DESI_BACKUP_S3_DEST / DESI_BACKUP_RCLONE_DEST) — backup CHỈ ở local $DIR"
fi

# 5) Bằng chứng sống: ghi metric textfile cho node-exporter. BackupStale bắn nếu
#    timestamp cũ hơn 26h; cặp metric offsite cho phép alert phân biệt "có backup
#    nhưng CHỈ local" (BackupOffsiteNotConfigured / BackupOffsiteStale).
#    Tới được đây khi offsite configured nghĩa là upload đã OK (lỗi đã fail sớm).
#    Ghi atomic (tmp + mv) để tránh scrape trúng file dở dang. Skip im lặng nếu
#    METRIC_DIR trống/không tồn tại.
if [ -n "$METRIC_DIR" ] && [ -d "$METRIC_DIR" ]; then
  {
    printf '# HELP desi_backup_last_success_timestamp Unix timestamp of last successful desi backup.\n'
    printf '# TYPE desi_backup_last_success_timestamp gauge\n'
    printf 'desi_backup_last_success_timestamp %s\n' "$(date +%s)"
    printf '# HELP desi_backup_offsite_configured 1 if an offsite destination (S3/rclone) is configured.\n'
    printf '# TYPE desi_backup_offsite_configured gauge\n'
    printf 'desi_backup_offsite_configured %s\n' "$OFFSITE_CONFIGURED"
    if [ "$OFFSITE_CONFIGURED" = "1" ]; then
      printf '# HELP desi_backup_offsite_last_success_timestamp Unix timestamp of last successful offsite upload.\n'
      printf '# TYPE desi_backup_offsite_last_success_timestamp gauge\n'
      printf 'desi_backup_offsite_last_success_timestamp %s\n' "$(date +%s)"
    fi
  } > "$METRIC_DIR/desi_backup.prom.tmp"
  mv "$METRIC_DIR/desi_backup.prom.tmp" "$METRIC_DIR/desi_backup.prom"
  log "metric ghi: $METRIC_DIR/desi_backup.prom"
else
  log "metric skip: METRIC_DIR '$METRIC_DIR' trống hoặc không tồn tại"
fi

# 6) Retention (xoá bản cũ + checksum tương ứng)
find "$DIR" -name 'desi_*.dump.gz' -mtime +"$RETENTION_DAYS" -delete
find "$DIR" -name 'desi_*.dump.gz.sha256' -mtime +"$RETENTION_DAYS" -delete
log "retention done (${RETENTION_DAYS}d)"

ping_heartbeat ""
