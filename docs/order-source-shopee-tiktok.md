# Nguồn đơn Shopee / TikTok + backfill theo mã hoá đơn

- **Ngày:** 2026-07-06
- **Branch:** `feat/order-source-shopee-tiktok`
- **Trạng thái:** Done (chờ review)

## Bối cảnh

Đơn bán qua sàn (Shopee, TikTok Shop) đang đổ về hệ thống qua KiotViet nhưng không có
nguồn đơn riêng — bị bỏ trống hoặc gắn nhầm nguồn khác. Mã hoá đơn KiotViet của các
đơn này có prefix nhận diện được:

| Prefix mã hoá đơn | Nguồn      |
| ----------------- | ---------- |
| `HDTTS_`          | TikTok     |
| `HDSPE_`          | Shopee     |

## Quyết định thiết kế (đã chốt với user)

1. **Backfill ghi đè luôn** nguồn cũ nếu mã hoá đơn khớp prefix — prefix là bằng chứng
   chắc chắn đơn từ sàn, kể cả khi đơn đang gắn nguồn khác (vd Landing).
2. **Auto-detect cho đơn mới**: hook trong `Order.save()` — nếu `invoice_code` khớp
   prefix thì tự set `order_source`. Phủ 100% mọi luồng ghi (form, cron
   `auto_sync_invoice_full`, webhook, import) vì không có chỗ nào ghi `invoice_code`
   qua `queryset.update()`.
3. **Khớp startswith, không phân biệt hoa thường.**
4. **Không** đưa `shopee`/`tiktok` vào `sheet_rules.py` (SHEET1/2/3) — đơn sàn không
   thuộc luồng sale landing/messenger.
5. **Loại đơn sàn khỏi trang Landing** (bổ sung sau khi chạy backfill trên prod):
   trang Landing vốn hiển thị "mọi đơn có nguồn trừ koc" nên đơn sàn lọt vào sau
   backfill. Thêm `MARKETPLACE_ORDER_SOURCES` (models.py) vào `order_source_exclude`
   ở `list_landing_orders` (list + sheet 1) và `list_sheet3_orders`.

## Thay đổi

### Backend

- `apps/orders/models.py`:
  - `OrderSource` thêm `SHOPEE = "shopee"`, `TIKTOK = "tiktok"`.
  - `INVOICE_CODE_SOURCE_PREFIXES` — mapping prefix → source, một điểm chân lý duy
    nhất cho cả auto-detect lẫn backfill.
  - `OrderSource.from_invoice_code(code)` — classmethod trả về source hoặc `None`.
  - `Order.save()` — nếu detect được source từ `invoice_code` thì set
    `order_source` (kèm xử lý `update_fields`, cùng pattern với `phone_normalized`).
- Migration `0098_alter_order_order_source` (alter choices, no-op ở DB).
- Command `backfill_marketplace_order_source`:
  - Mặc định **dry-run** — đếm số đơn khớp từng prefix, thống kê theo nguồn hiện tại.
  - `--apply` để ghi thật; chỉ update đơn đang sai nguồn, mỗi prefix một câu `UPDATE`.

### Frontend

- `features/orders/order-types.ts`: thêm `"shopee" | "tiktok"` vào union `OrderSource`.
- `features/orders/order-source.ts`: thêm vào `ORDER_SOURCES`, labels
  (Shopee/TikTok), màu badge (Shopee cam, TikTok đen/zinc).
- `api/schema.d.ts`: regenerate từ OpenAPI sau khi backend đổi enum.

## Test

- `from_invoice_code`: khớp đúng prefix, case-insensitive, không khớp → None.
- `Order.save()`: auto set nguồn khi tạo/sửa có mã sàn; giữ nguyên khi mã không khớp;
  hoạt động cả khi save với `update_fields=["invoice_code"]`.
- Command: dry-run không ghi; `--apply` ghi đè đúng, không đụng đơn không khớp.

## Vận hành

```bash
# Dry-run (mặc định) — xem trước số lượng sẽ ghi
python manage.py backfill_marketplace_order_source

# Ghi thật
python manage.py backfill_marketplace_order_source --apply
```

## Ghi chú

- Migration đánh số `0098` từ master (0097 là mới nhất). Session khác đang có
  `0098_order_outside_shipping_form` uncommitted ở checkout chính — bên nào merge sau
  phải renumber.
- Đã kiểm tra conflict (2026-07-06): sạch với origin/master; models.py của nhánh
  outside-orders apply chồng lên nhánh này không đụng văn bản. Khuyến nghị merge MR
  này trước, phía outside-orders xoá migration 0098 (còn untracked) và chạy lại
  `makemigrations` sau khi pull master → tự sinh 0099.
