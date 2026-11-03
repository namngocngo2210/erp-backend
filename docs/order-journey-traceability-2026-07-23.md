# Truy vết hành trình đơn hàng — vá 2 lỗ hổng (2026-07-23)

## Bối cảnh

Câu hỏi nghiệp vụ "đơn này ai đổi sale, lúc nào, đổi từ ai sang ai" hiện **không trả lời được**.
Rà lại đường ghi vết cho thấy hai lỗ hổng độc lập:

### Lỗ hổng 1 — audit log mất sạch request body

`AuditLogMiddleware` chỉ chạm `request.body` ở `process_response`, tức là SAU khi DRF đã đọc
stream qua `request.data`. Django chỉ cache `_body` nếu `request.body` được truy cập TRƯỚC, nên
tới lúc đó nó ném `RawPostDataException`; middleware bắt exception và log ở mức `debug`.

Hệ quả trên prod:
- `curl_command` của mọi POST/PATCH **không có `-d`** → không biết payload là gì.
- `metadata.request_size_bytes` luôn bằng 0 (cùng nguyên nhân, cùng đường code).

Test cũ không bắt được vì gọi thẳng `process_request`/`process_response` bằng `RequestFactory`,
không có bước DRF đọc stream ở giữa.

### Lỗ hổng 2 — journey chỉ ghi khối CRM

`OrderServiceHelpers._upsert_assessment` mở đầu bằng `if crm_payload is None: return`. PATCH không
kèm `crm` → không sinh event nào. Đổi sale, trạng thái, mã vận đơn, địa chỉ... đều im lặng.

Riêng đường gán sale còn tệ hơn:
- `LeadAssignmentService.assign_if_unassigned` chỉ `logger.info` rồi `order.save(["staff"])`.
- `LeadAssignmentService.transfer` dùng `.update()` hàng loạt — không log gì, không cả actor.

## Thay đổi

| Hạng mục | File | Nội dung |
|---|---|---|
| Buffer body | `apps/audit/middleware_impl.py` | `_buffer_request_body` đọc body trong `process_request` (chỉ JSON, ≤ `AUDIT_BODY_MAX_BYTES` mặc định 1MB). `_create_curl_command` + `_build_metadata` dùng bản buffer. |
| Event gán sale | `apps/orders/application/services/order_service_helpers.py` | `log_staff_change` — ghi `staff_assigned`/`staff_changed`, đóng băng tên sale vào `payload_json`. |
| Event field nghiệp vụ | nt. | `log_tracked_field_changes` — ghi `order_field_changed` cho field trong `TRACKED_ORDER_FIELDS`. |
| Hook PATCH | `apps/orders/application/services/order_service_commands.py` | Chụp giá trị cũ trước `setattr`, ghi event sau `order.save`. |
| Hook bộ chia | `apps/orders/application/services/lead_assignment_service.py` | Auto-assign ghi `staff_assigned` (actor=None); `transfer` nhận `actor` và ghi event từng đơn. |
| Truyền actor | `.../views/order_views_impl/lead_assignment_view.py` | `transfer(..., actor=request.user)`. |
| Nhãn event | `apps/orders/crm_catalog.py` | Thêm 5 nhãn còn thiếu: `sale_contact`, `case_closed` + 3 event mới. |

`TRACKED_ORDER_FIELDS` (`apps/orders/sheet_rules.py`) cố tình hẹp — 9 field mà việc đối soát thực
sự cần: `status`, `phone`, `address`, `shipping_code`, `shipping_provider`, `total_after_discount`,
`order_code`, `invoice_code`, `order_source`. Thêm field ở đây = thêm dòng vào timeline mọi đơn.

**Không có migration** — `order_journey_events` đã có sẵn `from_code`/`to_code`/`actor`/`payload_json`.

## Test

Viết theo TDD, mỗi test đã được xem fail trước khi implement:

- `apps/audit/tests/test_request_body_capture.py` — 5 test: body còn nguyên sau khi DRF đọc stream,
  `request_size_bytes` > 0, field nhạy cảm vẫn bị che, multipart KHÔNG bị buffer (upload ảnh chấm
  công vẫn parse được), payload > ngưỡng bị bỏ qua.
- `apps/orders/tests/test_order_field_journey_events.py` — 10 test: PATCH đổi/xoá sale, PATCH cùng
  giá trị không sinh event, field ngoài danh sách không sinh event, auto-assign, transfer hàng loạt,
  transfer qua endpoint ghi đúng người bấm nút.
- `apps/orders/tests/test_event_type_labels.py` — khoá lại: thêm event_type mà quên nhãn là fail.

Kết quả: `apps/orders` + `apps/audit` → 562 passed.

## Giới hạn cần biết

- **Không backfill được.** Cả hai vá chỉ có tác dụng từ lúc deploy. Body request cũ không lưu ở đâu
  (Loki chỉ có access log method/path), và không có nguồn nào giữ giá trị staff cũ để dựng lại.
- **Audit vẫn purge sau 90 ngày** (`AUDIT_LOG_RETENTION_DAYS`). Journey event thì giữ vĩnh viễn —
  đó là lý do nên ưu tiên đọc journey, audit chỉ là lưới đỡ.
- **Dung lượng**: body vào `curl_command` bị cắt 500 ký tự như cũ, nhưng số dòng PATCH có `-d` sẽ
  tăng. Theo dõi `pg_total_relation_size('audit_logs')` sau 1-2 tuần; nếu phình nhanh thì hạ
  `AUDIT_LOG_RETENTION_DAYS` hoặc tách cột body riêng để `defer()`.
- **Dữ liệu cá nhân**: body PATCH đơn chứa tên/địa chỉ khách. `_redact_sensitive` chỉ che
  password/token/phone. Chấp nhận trong phạm vi audit nội bộ, nhưng cần biết rõ khi mở quyền xem.

## Việc chưa làm

- UI: `Sheet3ContactTimeline` mới chỉ hiển thị ở panel CRM của trang Landing. Trang chi tiết đơn
  chưa có khối timeline — nên thêm ở sprint sau để sale/CSKH tự tra, khỏi phải hỏi kỹ thuật.
- `ingest_job_tasks` (đường sync KiotViet) vẫn cập nhật `invoice_code`/`status` bằng `.update()`
  không qua service → không sinh event. Cân nhắc bổ sung nếu cần đối soát cả đường máy chạy.
