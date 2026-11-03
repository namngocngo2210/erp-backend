# Gỡ bỏ hoàn toàn tích hợp Zalo (2026-07-06)

## Quyết định

Loại bỏ toàn bộ tích hợp Zalo khỏi hệ thống theo yêu cầu vận hành. Bao gồm:

- **Backend**: xóa app `apps.zalo_bridge` (models, tasks, views, admin, public API client), gỡ route `/api/zalo/*`, gỡ 4 task Celery (`zalo.apply_initial_tags`, `zalo.send_order_created`, `zalo.send_shipping_started`, `zalo.send_delivery_completed`) khỏi dispatch registry + task routes, gỡ queue `zalo_bridge` khỏi worker.
- **Điểm bắn notification đã gỡ**: ingest_job_tasks (sau push/create_invoice/kship thành công), order_service_commands (đổi status tay → giao thành công), kiotviet webhook (delivered success).
- **API contract**: field `zalo_friend_status` bị gỡ khỏi `OrderSerializer` + `OrderEvent` (websocket); `schema.yaml` / `schema.d.ts` đã regen.
- **Frontend**: xóa `features/zalo`, `lib/api/zalo.ts`, nút "Kết bạn Zalo" + modal chọn kênh CSKH ở trang chi tiết đơn, plumbing `zaloAccounts`/`refreshZaloAccounts` trong App shell.
- **Hạ tầng**: gỡ biến `ZALO_*` khỏi `.env.example` + compose (local/prod), gỡ queue `zalo_bridge` khỏi lệnh worker.
- **Database**: migration `orders.0097_drop_zalo_bridge_tables` drop 3 bảng `zalo_bridge_contacts`, `zalo_bridge_order_notifications`, `zalo_cskh_accounts` + dọn `django_migrations` / content types của app.

## Giữ lại (chủ đích — KHÔNG phải sót)

- `order_source = "zalo"` (enum `OrderSource.ZALO`, sheet_rules, badge màu FE): đây là **nhãn nguồn đơn** trên dữ liệu đơn hàng hiện hữu, phục vụ phân sheet Landing và báo cáo — không thuộc tích hợp Zalo Bridge.
- `"Zalo"` trong `CONTACT_CHANNELS` của trang Resale tracking: nhãn kênh liên hệ thủ công của sale, không phải tích hợp.
- Các docs lịch sử (audit, sprint plans) nhắc Zalo: giữ nguyên làm tư liệu.

## Việc cần làm khi deploy prod

1. Deploy code + chạy migrate (migration 0097 tự drop bảng — **không đảo ngược được**, backup trước khi migrate nếu cần giữ dữ liệu contact).
2. Xóa các biến `ZALO_BRIDGE_*`, `ZALO_PUBLIC_API_*` khỏi file `.env` thật trên VPS.
3. Recreate worker để bỏ queue `zalo_bridge` (`docker compose -f docker-compose.prod.yml up -d --build`).
4. Nếu bên bridge (hệ thống Zalo ngoài) còn gọi webhook về, tắt/thu hồi endpoint ở phía đó.
