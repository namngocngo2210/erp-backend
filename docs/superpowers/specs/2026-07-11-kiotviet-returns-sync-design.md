# Đồng bộ mã trả hàng từ KiotViet (`GET /returns`)

**Ngày:** 2026-07-11
**Branch:** `feat/kiotviet-returns-sync`

## Mục tiêu

Lấy danh sách phiếu trả hàng đã xác nhận từ KiotViet (`GET https://public.kiotapi.com/returns`),
map về đơn trong hệ thống theo hóa đơn, và điền mã phiếu trả vào
`OrderRecall.return_slip_code` (cột "Phiếu trả hàng" — hiện nhân viên nhập tay).

Một hóa đơn có thể có nhiều phiếu trả, nên field đổi từ chuỗi đơn sang **mảng**.

## Quyết định đã chốt

| Vấn đề | Quyết định |
| --- | --- |
| Field đích | `OrderRecall.return_slip_code` (không thêm cột mới trên `Order`) |
| Nhiều phiếu trả / 1 hóa đơn | Lưu **mảng** mã |
| Kiểu dữ liệu | Postgres `ArrayField(CharField(64))` ở DB; **contract API giữ nguyên string** (join `", "`) |
| Nguồn dữ liệu | Tất cả `KiotVietChannel` active |
| Ghi đè | **UNION**: giữ nguyên mọi mã đang có (nhập tay hoặc đã sync trước), chỉ nối thêm mã KiotViet chưa có — không xoá/sắp xếp lại mã nhân viên đã nhập. *(Cập nhật sau review whole-branch: quyết định gốc "chỉ điền khi rỗng" bị lỗi — sau lần sync đầu, mảng không bao giờ nhận thêm phiếu trả thứ 2 trở đi.)* |
| Cách chạy | Celery beat định kỳ + management command để backfill |

## Kiến trúc

Tuân theo layering sẵn có của app `kiotviet` (infrastructure service → task → command).

### Infrastructure — `apps/kiotviet/infrastructure/services/returns_sync_service.py`

- `iter_returns(channel, *, last_modified_from) -> Iterator[dict]`
  Gọi `_kiotviet_request(order=None, channel=..., method="GET", endpoint="/returns", params=...)`.
  Phân trang `pageSize=100` + `currentItem`, dừng khi trang trả về < `pageSize`
  (đúng pattern `backfill_kiotviet_purchase_date`). Truyền `lastModifiedFrom` khi có.
  HTTP ≠ 200 → raise (không nuốt lỗi KiotViet).
- `sync_returns_for_channel(channel, *, last_modified_from=None, date_from=None, date_to=None, dry_run=False) -> dict`
  Duyệt phiếu trả, map về order, gom theo order, ghi DB, trả summary.
  `date_from/date_to` lọc client-side theo `returnDate` (KiotViet không có filter khoảng
  ngày trả hàng trong hợp đồng API đã cho).
- `sync_returns_all_channels(...) -> dict`
  Lặp mọi `KiotVietChannel` (`deleted_at__isnull=True`), gộp summary; một channel lỗi
  được ghi vào `errors[]` và **không chặn** các channel còn lại.

Summary keys: `read`, `matched`, `updated`, `skipped_no_order`, `skipped_no_code`,
`skipped_no_new_codes`, `pages`, `errors`.

### Mapping return → order

`/returns` chỉ trả `invoiceId`, không trả mã hóa đơn. `Order.invoice_id` đã được
`import_invoices_service` ghi sẵn khi import hóa đơn.

1. **Khóa chính:** `Order.objects.filter(kiotviet_channel=channel, invoice_id=ret["invoiceId"], deleted_at__isnull=True)`.
2. **Fallback** (đơn cũ thiếu `invoice_id`): gọi `/invoices/{invoiceId}` lấy `code`,
   match theo `invoice_code` + `kiotviet_channel`. Chỉ gọi khi bước 1 trượt; cache
   `invoice_id -> code` trong phạm vi một lần chạy để không gọi lặp.
3. Không tìm thấy → `skipped_no_order`, log, bỏ qua (**không** tạo order mới).

Gom nhiều phiếu trả cùng một order: dedupe theo `code`, sort theo `returnDate` tăng dần.

### Ghi dữ liệu

- `transaction.atomic()` + `OrderRecall.objects.select_for_update().get_or_create(order_id=...)`
  mỗi đơn — khoá đúng dòng recall trong lúc đọc-sửa-ghi (chống PATCH tay chạy song song) và
  tránh `IntegrityError` khi hai tiến trình cùng tạo recall lần đầu làm hỏng các đơn còn lại.
- Nối mã KiotViet (đã qua `_parse_slip_codes`) vào **cuối** mảng hiện có, dedupe, giữ nguyên
  thứ tự/nội dung mã cũ. Nếu không có mã mới nào → `skipped_no_new_codes`, không ghi.
- `dry_run=True` → chỉ log, không ghi, không tạo row recall mới.

### Task & lịch chạy

- `apps/kiotviet/tasks.py::sync_kiotviet_returns_task` (`shared_task`, `soft_time_limit=240`).
- Beat trong `config/settings_parts/celery.py`: mỗi **15 phút**,
  `lastModifiedFrom = now - 2h` (cửa sổ chồng lấn để không mất bản ghi do lệch giờ / trễ webhook).
- Single-flight lock qua `cache` giống các task khác trong file.

### Command — `apps/kiotviet/management/commands/sync_kiotviet_returns.py`

Tham số: `--channel-id` (optional, mặc định tất cả channel), `--from`, `--to`,
`--dry-run`. In JSON summary. Dùng để backfill lịch sử.

## Migration & blast radius

**Migration** (`orders`): `return_slip_code` `CharField(64)` → `ArrayField(CharField(64), default=list, blank=True)`,
kèm data migration: `"X"` → `["X"]`, `""` → `[]`.

Các chỗ phải sửa theo (contract API **không đổi**):

- `apps/orders/repositories.py` — filter `has_return_slip`: `.exclude(return_slip_code="")` → `.exclude(return_slip_code=[])`.
- `order_core_serializers.py` — trả `", ".join(recall.return_slip_code)`.
- `order_service_helpers.py::_upsert_order_recall` — inline PATCH / import Excel nhận string:
  tách theo dấu phẩy → strip → bỏ rỗng → dedupe → list.
- **Frontend: không đổi** (vẫn là ô text hiển thị/sửa chuỗi phẩy; export/import Excel giữ nguyên).

## Test

Unit test với `_kiotviet_request` được mock:

- Phân trang nhiều trang (dừng đúng khi trang cuối < pageSize).
- Map theo `invoice_id`; fallback `/invoices/{id}` khi thiếu `invoice_id`.
- Nhiều phiếu trả cùng hóa đơn → mảng nhiều phần tử, dedupe, sort theo `returnDate`.
- **Không ghi đè** khi mảng đã có mã.
- `--dry-run` không ghi DB.
- Một channel lỗi không chặn channel còn lại.

Regression:

- Filter `has_return_slip` yes/no với `ArrayField` (mở rộng `tests/test_return_slip_filter.py`).
- Serializer join mảng thành string.
- PATCH string → mảng (`_upsert_order_recall`).

## Ngoài phạm vi

- Không đồng bộ chi tiết hàng hoàn (`returnDetails`), tiền trả (`returnTotal`), thanh toán.
- Không tự tạo `ReturnNote` / phiếu kho từ dữ liệu này (luồng inventory tách biệt).
- Không đổi `Order.customer_return` / `Order.return_date`.
