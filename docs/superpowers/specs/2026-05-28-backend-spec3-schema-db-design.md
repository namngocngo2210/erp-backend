# Spec 3 — Schema & Database hardening

**Ngày:** 2026-05-28
**Phụ thuộc:** Spec 1 + Spec 2 đã merge.
**Nguồn:** `docs/backend_review_report.md` mục 7, 12, 13, 14, 15.

## Nguyên tắc chỉ đạo

> **Đo trước, sửa sau.** Order model hiện đã có nhiều composite + partial + GIN index hợp lý. Không thêm index theo phỏng đoán. Mỗi index mới phải gắn với một truy vấn thật được chứng minh chậm (qua `EXPLAIN`/`pg_stat_statements`/`django-debug-toolbar`). Index thừa làm chậm ghi và tốn dung lượng.

> **Thay đổi schema = migration.** Mọi thay đổi phải có migration Django, chạy thuận + nghịch được, và an toàn với dữ liệu production hiện có.

## Phân nhóm theo rủi ro

### Nhóm A — An toàn, additive (làm trước)
**A1. Composite/partial index theo query đo được**
- Ứng viên (chỉ thêm nếu đo thấy chậm):
  - `OrderAssessment` tổ hợp filter hay dùng (vd `sales_status_code` + `customer_classification_code`).
  - `SaleReminder` partial index `Index(fields=["due_at"], condition=Q(status="pending"))` cho job quét reminder đến hạn.
  - `OrderKiotVietJob.finished_at` partial (`finished_at__isnull=True`) nếu có query "job chưa xong".
- **Quy trình:** trước mỗi index, ghi lại query thật + `EXPLAIN (ANALYZE)` trước/sau vào phần "Bằng chứng" của plan. Không có bằng chứng ⇒ không thêm.
- Migration additive thuần, dùng `AddIndex`. Cân nhắc `CREATE INDEX CONCURRENTLY` (Django `AddIndexConcurrently` + `atomic=False`) cho bảng lớn để không khóa ghi.

**A2. `Job` (dispatch): TextField → CharField cho `queue`, `task_type`**
- Đổi kiểu cho cột cần index. Migration `AlterField`. Kiểm tra độ dài dữ liệu hiện có (`MAX(length(...))`) trước khi chọn `max_length`.

### Nhóm B — Cần audit dữ liệu + xác nhận business (gated)
**B1. Unique constraint cho `Order.external_order_id` / `order_code`**
- ⚠️ **CẦN ANH XÁC NHẬN BUSINESS RULE:** `order_code` unique toàn cục hay theo channel? `external_order_id` có thể trùng hợp lệ không?
- **Bắt buộc trước khi thêm:** chạy query tìm bản ghi trùng hiện có. Nếu có trùng ⇒ phải xử lý dedup/làm sạch trước, nếu không migration sẽ fail trên production.
- Dùng `UniqueConstraint` có `condition=Q(deleted_at__isnull=True)` (nhất quán pattern hiện tại) và chỉ áp cho giá trị khác rỗng.

**B2. on_delete CASCADE chuỗi (`OrderAssessment.order`, `OrderNeed.order_assessment`)**
- ⚠️ Hiện CASCADE: hard-delete Order ⇒ mất assessment + needs (dữ liệu đánh giá).
- **Cần xác nhận:** hệ thống có bao giờ hard-delete Order không? Nếu chỉ soft-delete thì rủi ro thấp, có thể hoãn.
- Nếu cần giữ: cân nhắc `SET_NULL` (mất liên kết nhưng giữ dữ liệu) hoặc soft-delete cho assessment. Đây là thay đổi ngữ nghĩa ⇒ cần test kỹ.

### Nhóm C — Cải thiện mô hình dữ liệu (cân nhắc, có thể hoãn)
**C1. Tách trường hay-query khỏi JSONField**
- `CustomerResaleFollowUp.contact_logs/resale_orders`: nếu có report lọc theo "lần liên hệ cuối"/"số lần liên hệ" ⇒ thêm cột `last_contact_at` (index), `contact_count`, giữ JSON cho raw.
- **Chỉ làm nếu có nhu cầu report thật.** Nếu không ai query ⇒ YAGNI, bỏ.

## Out-of-scope
- Đặt lại tên index auto-generated (cosmetic, mục Low — bỏ qua, không đáng rủi ro migration).
- Tăng `max_length` phone (Low, không cấp thiết).

## Chiến lược test & verify
- Mỗi migration: chạy `migrate` thuận, rồi `migrate <app> <prev>` nghịch trên DB tạm để xác nhận reversible.
- Với B1: viết script kiểm tra trùng + log số lượng trước khi tạo constraint.
- Test ORM: query dùng index mới vẫn trả đúng kết quả; với on_delete đổi → test hành vi delete.
- `python manage.py makemigrations --check` không còn diff sau khi xong.

## Tiêu chí hoàn thành
- Chỉ thêm index có bằng chứng query chậm (kèm EXPLAIN trước/sau trong plan).
- Migration reversible, `migrate` + rollback chạy sạch trên DB tạm.
- Các mục gated (B1, B2) chỉ thực hiện sau khi anh xác nhận; nếu chưa xác nhận → để TODO trong plan, không tự ý đổi.
- `makemigrations --check` sạch.
