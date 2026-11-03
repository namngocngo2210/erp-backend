#!/usr/bin/env bash
# Deploy prod an toàn — sinh ra từ sự cố 2026-07-03: hai MR song song cùng tạo
# migration inventory 0009, mỗi MR riêng lẻ qua CI nhưng master sau khi merge cả
# hai bị "Conflicting migrations: multiple leaf nodes" → web crash-loop ngay lúc
# `compose up`. Script này chạy preflight check migration graph TRONG IMAGE MỚI
# trước khi thay container, nên lỗi kiểu đó chặn được ở bước build thay vì downtime.
#
# Dùng trên VPS:  bash scripts/deploy-prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"

echo "==> [1/5] git pull origin master"
git pull --ff-only origin master

# Sự cố 2026-07-30 23:33: compose passthrough ${POSTGRES_HOST} nên giá trị
# localhost của host rò vào container → mọi service boot fail "connection to
# server at 127.0.0.1:5432 refused". Rẻ và nhanh, đặt TRƯỚC build để hỏng thì
# dừng ngay chứ không đợi build xong mới chết lúc thay container.
# Script tự stub biến bắt buộc và dùng --env-file /dev/null nên KHÔNG đọc .env
# thật, cũng không rò biến ngược lại deploy (chạy ở process riêng).
echo "==> [2/5] preflight: compose render đúng giá trị cho container"
sh scripts/check-compose-render.sh

echo "==> [3/5] build image web (code mới)"
$COMPOSE build web

echo "==> [4/5] preflight: migration graph + system check trong image MỚI"
# `mkdir -p $PROM_DIR` là BẮT BUỘC ở MỌI lệnh `compose run`: khối env chung đặt
# PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc, nên prometheus_client vào chế độ
# multiprocess và mmap file ngay lúc nạp app. Các service thật tự tạo thư mục đó
# trong `command:` của mình, còn `compose run` sinh container MỚI với /tmp rỗng —
# thiếu mkdir thì mọi `manage.py ...` chết bằng
#   FileNotFoundError: '/tmp/prom_multiproc/histogram_1.db'
# TRƯỚC khi chạy được bất kỳ check nào, và `set -e` giết luôn deploy.
PROM_DIR=/tmp/prom_multiproc

# makemigrations --check bắt cả 'multiple leaf nodes' lẫn model-thiếu-migration.
$COMPOSE run --rm --no-deps web sh -c "mkdir -p $PROM_DIR && python manage.py makemigrations --check --dry-run"
# `--database default` là BẮT BUỘC: không có nó, Model._check_indexes(databases)
# lặp trên danh sách RỖNG nên toàn bộ check phụ thuộc DB (E033/E034 — tên index
# quá 30 ký tự, ràng buộc không hỗ trợ...) bị bỏ qua im lặng. Đúng lỗ hổng đã để
# lọt một index 31 ký tự tới tận lúc `migrate` abort trên prod.
$COMPOSE run --rm --no-deps web sh -c "mkdir -p $PROM_DIR && python manage.py check --database default"

echo "==> [5/5] preflight OK — triển khai redis-cache + web/worker/beat/worker-kiotviet/worker-slow/worker-export/worker-foxia"
# `redis-cache` (audit cache/latency 2026-08-11) đứng ĐẦU danh sách và cố ý KHÔNG
# `--build`: nó phải TỒN TẠI trước khi web/worker lên với env `REDIS_CACHE_HOST=redis-cache`,
# nếu không mọi thao tác cache đều lỗi kết nối ngay từ request đầu (fail-soft của
# apps.common.cache_backend đỡ được — hệ thống chậm chứ không ngã — nhưng đó là
# lưới an toàn, không phải quy trình). `up -d` là idempotent: lần đầu tạo mới,
# các lần sau không đụng tới container đang chạy nếu cấu hình không đổi, nên KHÔNG
# có chuyện mỗi lần deploy lại xoá sạch cache.
$COMPOSE up -d --no-deps redis-cache

# `worker-kiotviet` PHẢI nằm trong danh sách này. Nó là service riêng trong
# docker-compose.prod.yml (queue `kiotviet`), không được `up` kèm thì container cũ
# cứ chạy tiếp bằng IMAGE CŨ vô thời hạn. Mà worker đó GHI SỔ KHO: `resync_for_order`
# đi xuống `StockCommandService.record_movement` — bỏ sót nó nghĩa là sau MỖI lần
# deploy vẫn còn một tiến trình ghi `stock_movements` bằng code cũ, lệch hẳn với
# web/worker vừa lên.
# `worker-slow` (queue `slow`, audit 2026-07-26) cũng vậy: nó chạy các job Gemini
# và precompute báo cáo khách hàng — bỏ sót thì sau mỗi deploy vẫn còn tiến trình
# ghi snapshot báo cáo bằng code cũ.
# `worker-export` (queue `exports`, spec 2026-08-06) cùng bẫy: đây là consumer DUY
# NHẤT của queue export file. Bỏ sót nó không chỉ để lại code cũ chạy tiếp như hai
# worker trên — nó KHÔNG BAO GIỜ được tạo ở lần deploy đầu (service mới hoàn toàn),
# nên queue `exports` không có consumer nào cả: job nằm PENDING vĩnh viễn, người
# dùng nhận 429 sau vài lần bấm Xuất rồi mất hẳn khả năng xuất file.
# `worker-foxia` (queue `foxia,foxia_push`, spec 2026-09-09) cùng bẫy CHÍNH XÁC như
# worker-export ở trên, cộng thêm một lớp: lần deploy ĐẦU TIÊN sau khi service này
# được thêm vào docker-compose.prod.yml, worker-slow (đang chạy queue cũ `slow,foxia`)
# BỊ recreate và mất luôn queue `foxia` — trong khi worker-foxia KHÔNG được tạo vì bỏ
# sót ở đây. Kết quả: backfill CRM `foxia` (đang sống trên prod) VÀ đẩy đơn landing
# `foxia_push` cùng lúc không còn consumer nào, task chất đống trong Redis mà không
# có gì báo lỗi. Xem docs/runbooks/2026-09-09-add-foxia-worker.md.
$COMPOSE up -d --build web worker beat worker-kiotviet worker-slow worker-export worker-foxia

echo "==> Xong. Theo dõi: docker logs -f desi-web"
