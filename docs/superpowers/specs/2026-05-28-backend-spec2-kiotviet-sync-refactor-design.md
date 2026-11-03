# Spec 2 — Refactor & tăng tốc đồng bộ invoice KiotViet

**Ngày:** 2026-05-28
**Phụ thuộc:** hoàn thành Spec 1 trước (để có baseline pooling + pytest chạy được).
**Nguồn:** `docs/backend_review_report.md` mục 1, 4, 5, 9, 10.

## Bối cảnh (đã xác minh)

- `apps/kiotviet/infrastructure/services/sync_invoices_service.py` (2.462 dòng, có `run(**options)`) là **bản chạy thật** trong production: gọi từ `apps/internal/views.py`, `apps/kiotviet/views.py`, `apps/orders/.../cron_hourly_audit_tasks.py`.
- `apps/kiotviet/management/commands/sync_invoices_between_channels.py` (3.029 dòng) là **bản CLI**, chứa logic gần như trùng lặp với service.
- `apps/kiotviet/management/commands/import_kiotviet_invoices.py` (1.538 dòng) gánh 8 chế độ.

## Mục tiêu

1. **Một nguồn sự thật:** logic sync nằm ở service; command trở thành wrapper mỏng (parse args → gọi `service.run()`). Xóa logic trùng trong command.
2. **Tăng tốc:** loại N+1 DB/API trong vòng lặp bằng pre-loading + song song hóa I/O.
3. **Chia nhỏ:** tách hàm khổng lồ (`run`, `_sync_invoice` ~446 dòng) thành các bước có tên rõ ràng, mỗi hàm < ~80 dòng.

**Nguyên tắc số 1: refactor BẢO TOÀN HÀNH VI.** Không đổi payload gửi KiotViet, không đổi kết quả nghiệp vụ. Chỉ đổi cấu trúc + hiệu năng.

## Kiến trúc đích

```
sync_invoices_service.py  (orchestrator mỏng)
  ├─ SyncContext            # tham số 1 lần sync + cache pre-loaded
  ├─ preload/               # nạp trước mappings & customers (1 query mỗi loại)
  │    ├─ ChannelMappingCache   (thay id_mappers gọi-từng-đơn)
  │    └─ CustomerResolverCache (thay find_customer_by_phone từng đơn)
  ├─ invoice_sync_pipeline.py   # các bước cho 1 invoice: resolve → build → push → patch_status
  └─ parallel_runner.py         # ThreadPoolExecutor + gom kết quả/summary an toàn thread

management/commands/sync_invoices_between_channels.py
  └─ Command.handle(): parse args → SyncInvoicesService().run(**options)  (≈ < 80 dòng)
```

### Pre-loading (bỏ N+1)
- **Channel mapping:** trước vòng lặp, nạp toàn bộ `ChannelKiotvietMapping` cho cặp kênh nguồn/đích vào dict `(kiotviet_channel_id, external_sale_channel_id) → target`. Thay 2 query/đơn ở `id_mappers.map_sale_channel_id`.
- **Customer:** gom tất cả phone của lô invoice, `Customer.objects.filter(phone__in=...)` một lần → dict. Thay `find_customer_by_phone` từng đơn. (Trường hợp KiotViet cần tạo customer mới vẫn gọi API, nhưng phần tra cứu đã batch.)

### Song song hóa
- Phần xử lý mỗi invoice độc lập nhau → `ThreadPoolExecutor(max_workers=N)` với `N = int(os.getenv("KIOTVIET_SYNC_WORKERS", "5"))`.
- **An toàn:** summary/counter gom qua kết quả trả về của future (không mutate shared dict trong thread) hoặc dùng `threading.Lock`. Mỗi thread dùng kết nối DB riêng (Django mở connection theo thread — chú ý đóng connection cuối mỗi task bằng `django.db.connection.close()` trong worker để tránh rò connection).
- Giữ tùy chọn `--workers 1` (tuần tự) để debug/rollback hành vi.
- Backoff: nâng cấp retry hiện tại (linear) thành exponential + xử lý HTTP 429 (nếu KiotViet trả). Giữ `--sleep` để điều tiết.

### Tách import_kiotviet_invoices.py
- Tách 8 chế độ thành các hàm/handler riêng trong một module `import_modes/` (mỗi mode 1 file/hàm), command chỉ dispatch theo flag. Không gộp chung biến `summary` toàn cục.
- *(Có thể đưa sang sprint con thứ 2 của Spec 2 nếu phạm vi quá lớn — ưu tiên sync trước.)*

### Gom helper trùng
- `_first_non_empty`, `_to_decimal` → module `apps/kiotviet/infrastructure/services/_payload_utils.py` dùng chung.

## Chiến lược thực thi an toàn (quan trọng)

1. **Characterization tests trước refactor:** viết test khóa hành vi hiện tại của `service.run()` với KiotViet API được mock (đã có `test_kiotviet_service_mock.py` làm mẫu). Bao gồm: payload build cho 1 invoice mẫu, mapping sale channel, resolve customer, các mode (`--dry-run`, `--update-status`, `--check-missing`). **Các test này phải xanh trước và sau refactor.**
2. Refactor từng bước nhỏ, chạy test sau mỗi bước (TDD/đỏ-xanh-refactor).
3. Pre-loading & song song hóa thêm SAU khi đã hợp nhất command→service và test xanh.
4. So sánh payload trước/sau bằng golden snapshot (dump payload build ra JSON, diff phải rỗng).

## Rủi ro & giảm thiểu
- **Đổi hành vi ngầm:** giảm bằng characterization test + golden snapshot payload.
- **Race condition khi song song:** worker không chia sẻ state ghi; summary gom qua return value; đóng DB connection mỗi task.
- **Thứ tự xử lý đổi:** nếu nghiệp vụ phụ thuộc thứ tự (vd chống trùng theo code), giữ bước pre-scan/dedup TRƯỚC khi vào pool song song.
- **Phạm vi lớn:** nếu refactor `import_kiotviet_invoices.py` làm phình, tách thành sprint con riêng.

## Tiêu chí hoàn thành
- Command `sync_invoices_between_channels` chỉ còn dispatch (< ~120 dòng), không trùng logic với service.
- Không còn query mapping/customer theo từng đơn (đo bằng `assertNumQueries` trên lô N đơn → số query hằng số, không tăng theo N).
- Có đường chạy song song (mặc định 5 worker) + fallback tuần tự.
- Characterization test + golden payload snapshot xanh.
- Payload gửi KiotViet không đổi so với trước.
