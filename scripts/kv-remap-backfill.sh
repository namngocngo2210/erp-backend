#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Chuyển đơn khỏi 3 kênh fallback KiotViet (22 kiotviet-4 / 23 kiotviet-5 /
# 29 kiotviet-6) về kênh bán thật, chạy theo TỪNG THÁNG.
#
# VÌ SAO TỒN TẠI: backfill 2025-01 → nay là ~20 tháng × 3 KiotViet ≈ 60 lần gọi
# `import_kiotviet_invoices --check-channel-match` với 8 tham số giống nhau. Gõ
# tay 60 lần trên SSH vừa lâu vừa dễ sai một tham số mà không ai phát hiện —
# nhất là khi terminal cắt dòng ở ~110 ký tự.
#
# VÌ SAO CHIA THÁNG chứ không một phát cả dải: handler tích luỹ toàn bộ hoá đơn
# của khoảng thời gian vào RAM trước khi xử lý. Cả dải 2025→nay của KV Old là
# ~58.800 hoá đơn ≈ vài trăm MB trong container mem_limit 1024m → OOM.
#
# Xem docs/kv-fallback-channel-remap-2026-08-23.md để biết bối cảnh và 4 quyết
# định nghiệp vụ đứng sau các tham số mặc định bên dưới.
#
# Dùng trên VPS:
#   bash scripts/kv-remap-backfill.sh 5 2026-08 2026-08            # dry-run
#   bash scripts/kv-remap-backfill.sh 5 2025-01 2026-08 --apply    # chạy thật
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CONTAINER="${KV_REMAP_CONTAINER:-desi-worker-kiotviet}"
# Kênh gom đơn có saleChannelId RỖNG trên KV (bán tại quầy) — "Khác".
EMPTY_SALE_CHANNEL_TO="${KV_REMAP_EMPTY_TO:-26}"
# Chỉ đụng đơn đang nằm ở 3 kênh fallback. Bỏ filter này thì lệnh ghi đè MỌI
# đơn lệch kênh, kể cả đơn nhân viên đã sửa tay.
ONLY_CURRENT="${KV_REMAP_ONLY_CURRENT:-22,23,29}"
LOG_DIR="${KV_REMAP_LOG_DIR:-/tmp/kv-remap}"

usage() {
  cat >&2 <<'USAGE'
Cách dùng: bash scripts/kv-remap-backfill.sh <kv-id> <từ YYYY-MM> <đến YYYY-MM> [--apply]

  <kv-id>    5 = Old Kiot | 4 = New Kiot | 6 = HKD Kiot
  --apply    Ghi DB thật. Không có cờ này = dry-run (mặc định, an toàn).

Ví dụ:
  bash scripts/kv-remap-backfill.sh 5 2026-08 2026-08
  bash scripts/kv-remap-backfill.sh 5 2025-01 2026-08 --apply
USAGE
  exit 2
}

[ $# -ge 3 ] || usage

KV_ID="$1"
FROM_MONTH="$2"
TO_MONTH="$3"
APPLY="${4:-}"

case "$KV_ID" in
  # branch_ids lấy từ kiotviet_channels.branch_ids trên prod. Thiếu một branch
  # là bỏ sót im lặng đúng phần đơn của kho đó.
  5) BRANCH_IDS="30965,1000000139,1000000477" ;;
  4) BRANCH_IDS="231174,241190" ;;
  6) BRANCH_IDS="89084" ;;
  *) echo "kv-id không hợp lệ: $KV_ID (chỉ nhận 4, 5, 6)" >&2; exit 2 ;;
esac

for month in "$FROM_MONTH" "$TO_MONTH"; do
  case "$month" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]) ;;
    *) echo "Tháng phải theo dạng YYYY-MM, nhận được: $month" >&2; exit 2 ;;
  esac
done

DRY_RUN_FLAG="--dry-run"
MODE="DRY-RUN"
if [ "$APPLY" = "--apply" ]; then
  DRY_RUN_FLAG=""
  MODE="GHI THẬT"
elif [ -n "$APPLY" ]; then
  echo "Tham số thứ 4 chỉ nhận '--apply', nhận được: $APPLY" >&2
  exit 2
fi

docker inspect "$CONTAINER" >/dev/null 2>&1 || {
  echo "Không thấy container '$CONTAINER'. Deploy chưa chạy?" >&2
  exit 1
}

mkdir -p "$LOG_DIR"

echo "==> KV $KV_ID | branch $BRANCH_IDS | $FROM_MONTH → $TO_MONTH | $MODE"
echo "==> Log: $LOG_DIR"

cursor="$FROM_MONTH-01"
stop_at="$(date -d "$TO_MONTH-01 +1 month" +%Y-%m-%d)"
failed=0

while [ "$(date -d "$cursor" +%s)" -lt "$(date -d "$stop_at" +%s)" ]; do
  month="$(date -d "$cursor" +%Y-%m)"
  # Mốc `đến` là 00:00:00 ngày đầu tháng kế: tránh phải tính ngày cuối tháng
  # (28/29/30/31). Chồng lấn đúng 1 giây với tháng sau là vô hại — lệnh
  # idempotent, đơn đã đúng kênh rơi vào `matched`.
  next="$(date -d "$cursor +1 month" +%Y-%m-%d)"
  log="$LOG_DIR/kv$KV_ID-$month.log"

  printf '  %s ... ' "$month"
  set +e
  docker exec "$CONTAINER" python manage.py \
    import_kiotviet_invoices \
    --channel-id "$KV_ID" \
    --branch-ids "$BRANCH_IDS" \
    --check-channel-match \
    $DRY_RUN_FLAG \
    --only-current-channel-ids "$ONLY_CURRENT" \
    --empty-sale-channel-to "$EMPTY_SALE_CHANNEL_TO" \
    --from "$cursor 00:00:00" \
    --to "$next 00:00:00" \
    >"$log" 2>&1
  rc=$?
  set -e

  if [ $rc -ne 0 ]; then
    failed=$((failed + 1))
    echo "LỖI (rc=$rc) — xem $log"
  else
    # Dòng summary là JSON object cuối cùng bắt đầu bằng '{'.
    grep '^{' "$log" | tail -1
  fi

  cursor="$next"
done

echo
echo "==> Sale channel CHƯA có mapping (gộp mọi tháng vừa chạy):"
# Đây là phần phải hành động: đơn của các id này KHÔNG được chuyển đi đâu cả,
# chúng chờ khai báo Channel + mapping trong CRM (Kênh → sửa kênh → Mapping
# KiotViet). Dồn chúng vào kênh "Khác" là chôn doanh thu của shop thật.
# Gán ra biến rồi mới xét rỗng: exit code của pipeline là của lệnh CUỐI (sort),
# luôn 0 kể cả khi grep không khớp gì — `if ! grep ... | sort` sẽ không bao giờ
# vào nhánh "không có".
unmapped="$(cat "$LOG_DIR"/kv"$KV_ID"-*.log 2>/dev/null \
  | grep '^UNMAPPED_SALE_CHANNEL' \
  | sed 's/.*sale_channel_id=//' | sort | uniq -c | sort -rn || true)"
if [ -n "$unmapped" ]; then
  echo "$unmapped"
  echo "  → Khai báo trong CRM: Kênh → sửa kênh → Mapping KiotViet, rồi chạy lại."
else
  echo "  (không có — mapping đã đủ)"
fi

[ "$failed" -eq 0 ] || { echo; echo "==> $failed tháng lỗi, xem log ở trên." >&2; exit 1; }
