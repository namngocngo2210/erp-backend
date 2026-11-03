# Kiến trúc đích của app `orders` (BE-2 / BE-9 — docs/audit.md)

Ngày: 2026-07-29. Phạm vi: hướng đích cho `backend/apps/orders`, và cách trả nợ
vòng phụ thuộc orders↔kiotviet (audit BE-2), module god `repositories.py` (BE-9).

## 1. Đích: layered theo `docs/architecture-standard.md`

`orders` là app lớn nhất → áp layered ĐẦY ĐỦ:

```
apps/orders/
  contracts/        # ranh giới công khai: events + ports (đã có, mở rộng dần)
  domain/           # entity/value object thuần, không Django
  application/      # use case (services) — đã có application/services/
  infrastructure/   # tasks, gateway, query builder nặng
  transport/        # views/serializers DRF
```

App nhỏ (ví dụ `warranty`, `best_express`) KHÔNG cần layered đầy đủ — flat
`models.py + services.py + views.py` là đủ; chỉ bắt buộc: mọi phụ thuộc chéo app
đi qua `contracts/` hoặc event bus (`apps/common/events.py`), không import
service/nội tạng của app khác (contract `.importlinter` chặn nợ mới).

### `repositories.py` (BE-9)

Sau Sprint 1 nó còn giữ filter registry + query builder cho list/report — một
"god module" thế hệ cũ sống song song `application/services/`. Đích:

- Filter registry + query builder → `apps/orders/infrastructure/queries/`
  (hoặc `application/queries/` nếu không đụng hạ tầng), tách theo chủ đề
  (list đơn, report, export).
- `_repositories_reports.py` đi cùng đợt đó (đang import model của
  facebook_ads/google_ads — cần port/contract riêng cho số spend).
- KHÔNG dời trong đợt 2026-07-29 (nhiều luồng sửa song song trên orders trong
  ngày — tránh churn); `repositories.py` hiện KHÔNG import gì từ kiotviet nên
  không tạo nợ orders→kiotviet mới.

## 2. Vòng orders↔kiotviet (BE-2) — nguyên tắc trả nợ

Chiều hợp lệ theo phân tầng: kiotviet (tầng trên) ĐƯỢC import orders. Nợ là
chiều ngược orders→kiotviet. Phân loại:

| Loại | Cách xử | Ví dụ |
| --- | --- | --- |
| Schema FK | GIỮ, ghi nhận là nợ chấp nhận (gỡ = đổi schema, ngoài phạm vi) | `orders.models -> kiotviet.models` (FK `Order.kiotviet_channel`) |
| Service call | Port ở `orders/contracts/` + adapter phía kiotviet đăng ký lúc `ready()` | `orders/invoice.py` (ĐÃ trả 2026-07-29) |
| Type-only | Chuyển vào `if TYPE_CHECKING:` (importlinter đã exclude) | `KiotVietChannel` hint trong `invoice.py` |
| Test fixture | Ưu tiên thấp; dựng dữ liệu qua `apps.get_model` khi tiện tay | mục (b) trong `.importlinter` |

### Mẫu port đã dựng (dùng lại cho các cụm sau)

- `orders/contracts/kiotviet_invoice_port.py`: `Protocol` + registry
  (`register_kiotviet_invoice_port` / `kiotviet_invoice_port`).
- `kiotviet/adapters.py`: adapter delegate lười vào service thật; đăng ký tại
  `KiotVietConfig.ready()` — mirror cách `OrdersConfig.ready()` wiring
  subscriber chiều ngược lại.
- Lý do không dùng event bus cho các cụm này: chiều gọi là request/response
  (orders hỏi, cần kết quả ngay), event bus chỉ hợp side-effect một chiều.

### Nợ orders→kiotviet còn lại (2026-07-29) và kế trả

1. `order_service_commands` / `shipping_lookup_view` → `cancellation_verifier`:
   port tương tự, nhưng `CancelCheck`/`VerificationUnavailable`/`CANCELLED_INVOICE_STATUS`
   phải dời về `orders/contracts` (so sánh `is` trên enum → cần một class duy nhất,
   kiotviet re-export để test cũ khỏi đổi). Guard chống xuất kho đơn hủy là đường
   an toàn — làm riêng một MR với đủ bộ test guard chạy kèm.
2. `ingest_job_tasks` → `apps.kiotviet.services` (push/update/delete invoice):
   bề mặt lớn nhất còn lại, gồm cả helper private (`_ensure_old_channel_invoice`,
   `_resolve_paired_old_channel`) — cần kiotviet công khai hoá API push trước
   khi port; làm sau khi luồng ingest (đang refactor 2026-07-29) ổn định.
3. `order_crud_view` → `kiotviet.models` + `kiotviet.services`
   (`get_kship_providers_and_services`): port nhỏ, làm cùng đợt (1).
4. `auto_sync_invoice_full` / `product_item_crm_serializers` → `kiotviet.models`:
   đọc bảng channel — gần loại FK; trả bằng port đọc channel hoặc chấp nhận cùng
   nhóm schema.

## 3. Nợ chấp nhận (không trả)

- `orders.models -> kiotviet.models`: FK schema, giữ và ghi chú trong
  `.importlinter`.
- `users.staging_neutralize -> *`: theo bản chất phải chạm mọi app (đã ghi chú
  tại chỗ trong `.importlinter`).
