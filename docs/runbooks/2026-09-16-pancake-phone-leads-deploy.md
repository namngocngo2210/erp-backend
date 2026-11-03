# Runbook — Kéo số điện thoại Pancake → Foxia (spec 2026-09-16)

## 1. Deploy code
- Migration: `pancake.0001_initial`, `users.0049_seed_pancake_leads_permission`,
  `pancake.0002_staff_map_and_lead_order` (bảng `PancakeStaffMap`, cột `PancakeLead.
  order`), `pancake.0003_alter_pancakelead_push_status` (thêm choice `order_created`/
  `order_failed`/`order_exists` cho `push_status`) — chạy TRONG lệnh khởi động của
  `web` (`docker-compose.prod.yml` service `web`, không có service `backend`), không
  phải một bước riêng.
- Env (`.env` prod): `PANCAKE_POLL_ENABLED=0` (giữ tắt ở bước này), `PANCAKE_HTTP_TIMEOUT=15`.
- Chạy `bash scripts/deploy-prod.sh` trên VPS: script `git pull`, build image `web`, chạy
  preflight `makemigrations --check` + `check --database default` TRONG image mới, rồi
  `docker compose -f docker-compose.prod.yml up -d --build web worker beat worker-kiotviet
  worker-slow worker-export worker-foxia` (env nướng vào container lúc tạo — `docker
  restart` KHÔNG đọc lại `.env`). `web` PHẢI được recreate cùng đợt này dù thay đổi chỉ nằm
  ở `apps/pancake`/`apps/foxia`: `retry_lead_order.delay(...)` (nút "Tạo đơn" ở trang
  Lead Pancake, trước 17/09 gọi là `repush_lead`/"Đẩy lại") được xếp hàng từ chính
  process API (`web`), không phải từ worker — quên recreate `web` thì API vẫn chạy code
  cũ dù worker đã lên bản mới.
- Frontend: SPA được build/serve bởi service `frontend` riêng trong `docker-compose.prod.yml`
  (multi-stage: `npm run build` rồi nginx serve `dist`, xem `frontend/Dockerfile`), KHÔNG nằm
  trong image `web`. `scripts/deploy-prod.sh` không đụng tới service `frontend` (danh sách
  `up -d --build` ở bước 5 chỉ có `web worker beat worker-kiotviet worker-slow worker-export
  worker-foxia`) — đổi FE (`canRepush` nhận `pending`, xem spec §9g) phải tự
  `docker compose -f docker-compose.prod.yml up -d --build frontend` thêm một bước, deploy
  script không tự làm việc này.

## 2. Cấu hình (UI, tài khoản superuser)
1. Vai trò: cấp `pancake-leads:view` cho role cần xem trang Sales → Lead Pancake.
2. Cài đặt → Cấu hình liên kết Foxia: điền ô (kênh, Messenger) và (kênh, Zalo) TRƯỚC
   khi bật poll: thiếu ô thì đơn tạo từ lead sẽ ghi `skipped_no_mapping` ở
   `foxia_order_pushes` và không tự đẩy lại.
3. Cài đặt → Pancake: dán access token (tài khoản Admin trên các page) → *Lưu & kiểm tra*
   (đồng bộ page ngay). Gán kênh cho từng page, gạt *Bật* — sinh token page lúc này;
   lỗi "Thiếu quyền Admin" / "không có gói cước" hiện ngay ở cột Lỗi.
4. Cài đặt → Pancake → Nhân viên Pancake: sau lượt poll đầu, các tên nhân viên Pancake
   (lấy từ `assignee_name` của hội thoại) tự xuất hiện ở bảng này (dòng mới, cột người
   dùng còn trống) — chọn sale nội bộ cho từng tên. Chỉ chọn được tài khoản CÒN active
   và KHÔNG phải admin (cùng luật `resolve_ingest_staff_id` của ingest landing); tên
   chưa gán hoặc gán tài khoản không hợp lệ → đơn tạo từ lead của nhân viên đó rơi về bộ
   chia tự động, không lỗi.

## 3. Bật cron
- `.env`: `PANCAKE_POLL_ENABLED=1` → `up -d worker-foxia`.
- Kiểm tra: `docker compose logs -f worker-foxia | grep "Pancake poll"` — mỗi 3 phút một
  dòng `Pancake poll ok: page=…`. Metric `desi_pancake_poll_pages_total{status}`.

## 4. Vận hành
- Lead lỗi tạo đơn (`order_failed`) hoặc còn `pending` chưa kịp tạo đơn: trang Lead
  Pancake → nút **Tạo đơn** (trước 17/09 gọi là "Đẩy lại", gọi `retry_lead_order` qua
  endpoint `retry-order`). Lead đã có `order_id` (`order_created`/`order_exists`) không
  còn nút này — bấm vào để xem đơn.
- Kết quả đẩy Foxia của lead KHÔNG còn nằm trên trang Lead Pancake — xem ở chính đơn đã
  tạo: trang chi tiết đơn, hoặc Django admin → Foxia → Foxia order pushes
  (`foxia_order_pushes`), lọc theo `order_id`.
- Lead tạo TRƯỚC 17/09 (đã đẩy thẳng sang Foxia theo đường cũ, `push_status=ok`) không
  có `order`, và **không** được tạo đơn bù khi bấm Tạo đơn hay khi retry — đây là hành
  vi có chủ đích (spec §9h), không phải lỗi.
- Token page bị Pancake từ chối: hệ thống sinh lại 1 lần; vẫn hỏng → page tự TẮT,
  cột Lỗi ghi lý do; sửa quyền bên Pancake rồi gạt Bật lại (cursor đặt lại = now).
- Xem nhật ký thô (response Foxia của lead cũ trước 17/09, hoặc lỗi tạo đơn của lead
  mới): Django admin → Pancake → Pancake leads.
- Sau lượt poll đầu có lead: kiểm tra cột "Nhân viên Pancake" có tên hay rỗng. Hình dạng
  `current_assign_users` chưa kiểm chứng bằng token thật (spec §3) — nếu rỗng, lấy một
  hội thoại thật, gọi `GET .../conversations?...` bằng token page (xem
  `apps/pancake/infrastructure/pancake_client.py`) và sửa `_assignee_name` theo đúng khoá.
- Tắt khẩn: `PANCAKE_POLL_ENABLED=0` + `up -d worker-foxia`, hoặc gạt tắt từng page.
