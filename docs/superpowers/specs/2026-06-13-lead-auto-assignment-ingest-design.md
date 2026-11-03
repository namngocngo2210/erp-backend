# Chia đơn tự động + chuyển đơn theo sheet tại API ingest

- **Ngày:** 2026-06-13
- **Trạng thái:** Đã duyệt thiết kế, chờ viết plan
- **Phạm vi:** Backend (Django) + Frontend (React SPA)

## 1. Bối cảnh & vấn đề

Đơn từ các luồng ingest hiện được tạo **không gán nhân viên** (`Order.staff = NULL`),
phải chia tay thủ công:

- `ingest_landing_order` → task `orders.landing_ingest` → `OrderService.create_order_from_landing`:
  staff **luôn null**.
- `lark_ingest` → `OrderService.create_order_manual(validated, actor)`:
  staff có thể **đã được set** nếu payload gửi `staff_username`, ngược lại null.

Mục tiêu:
1. **Tự động gán nhân viên** (`Order.staff`) ngay tại API ingest, **chỉ khi đơn chưa có staff**,
   theo **tỉ lệ % cấu hình theo ngày, tách riêng theo từng sheet** (Landing/Messenger/Tư vấn).
2. **Xem tải** hiện tại của từng nhân viên theo sheet (số đơn đang chờ) và **chuyển một số lượng
   đơn** từ nhân viên này sang nhân viên khác.

## 2. Khái niệm "sheet" (3 luồng đơn)

3 tab của trang Landing (`frontend/src/pages/OrdersPage.tsx`), suy ra từ `order_source` + tình
trạng đủ thông tin (`apps/orders/sheet_rules.py`):

| sheet | Nhãn | Quy tắc |
|---|---|---|
| `sheet1` | Landing xác nhận đơn | source ∈ {landing, hotline, zalo} **và đủ thông tin** |
| `sheet2` | Messenger xác nhận đơn | source = messenger **và đủ thông tin** |
| `sheet3` | Tư vấn | source ∈ {landing, hotline, zalo, messenger} **và chưa đủ thông tin** |

"Đủ thông tin" = có tên + sđt + địa chỉ (`is_info_complete`). Đơn không thuộc 3 sheet (vd koc/kol)
→ không auto-chia. Nguồn sự thật chung đặt trong `apps/orders/sheet_rules.py`.

## 3. Yêu cầu đã chốt

1. Auto-assign chỉ chạy **khi `order.staff` đang null** (không ghi đè staff đã có).
2. Áp dụng cho **mọi luồng ingest** (landing + lark); **không** đụng tạo đơn thủ công trong UI.
3. Chia theo **trọng số/% theo ngày, tách riêng theo từng sheet**.
4. Thuật toán **largest-remainder** trong pool của **đúng sheet** mà đơn được phân loại vào.
5. **Sheet/ngày chưa cấu hình → để trống** (staff = null), không tự chia.
6. **Chuyển đơn:** phạm vi = đơn **chưa có trạng thái sale** (assessment null hoặc
   `sales_status_code` rỗng), thuộc đúng sheet; chọn **N đơn cũ nhất (FIFO)** chuyển A→B.
7. Chuyển đơn **độc lập** với bộ đếm `assigned_count` của chia tự động (không sửa counter).

## 4. Quyết định kiến trúc

**Gán đồng bộ trong transaction tạo đơn (Phương án A — đã duyệt).** Ingest vốn chạy trong task
nền; gán đồng bộ trong transaction là sạch nhất, không có trạng thái trung gian "đơn chưa có staff",
đảm bảo tỉ lệ chính xác nhờ khóa hàng theo (ngày, sheet). Loại bỏ phương án event-listener bất
đồng bộ (race phức tạp, khó bám tỉ lệ, khó test).

Timezone hệ thống: `Asia/Bangkok` (UTC+7 ≡ giờ VN) → "ngày" = `timezone.localdate()`.

## 5. Data model

Bảng mới `lead_assignment_shares` — mỗi dòng = cấu hình 1 nhân viên cho 1 (ngày, sheet).

| field | kiểu | ghi chú |
|---|---|---|
| `id` | PK bigint | |
| `date` | DateField, index | ngày áp dụng (giờ VN) |
| `sheet` | CharField(16), choices sheet1/sheet2/sheet3 | luồng đơn |
| `user` | FK → users, on_delete CASCADE | nhân viên nhận đơn |
| `weight` | PositiveSmallIntegerField (>0) | trọng số/% trong (ngày, sheet) |
| `assigned_count` | PositiveIntegerField, default 0 | bộ đếm đơn đã chia trong (ngày, sheet) |
| `created_at` / `updated_at` | | |

- `unique_together = ("date", "sheet", "user")`
- `indexes = [Index(fields=["date", "sheet"])]`
- **Không có dòng nào cho một (ngày, sheet) ⇒ luồng đó không auto-chia ngày đó.**

## 6. Phân loại sheet — `apps/orders/sheet_rules.py`

Thêm helper dùng chung (nguồn sự thật cho cả ingest, workload, transfer):

```python
SHEET_CHOICES = ("sheet1", "sheet2", "sheet3")

def classify_sheet(*, order_source, name, phone, raw_address="", address="") -> str | None:
    complete = is_info_complete(name=name, phone=phone, raw_address=raw_address, address=address)
    src = (order_source or "").strip().lower()
    if complete and src in SHEET2_SOURCES:      # messenger
        return "sheet2"
    if complete and src in SHEET1_SOURCES:      # landing/hotline/zalo
        return "sheet1"
    if (not complete) and src in SHEET3_SOURCES:  # landing/hotline/zalo/messenger
        return "sheet3"
    return None
```

Và một queryset-filter helper để workload/transfer khớp đúng định nghĩa sheet của trang Landing
(dựa trên cùng `SHEET*_SOURCES` + `is_info_complete` bằng Q-objects).

## 7. Service — `LeadAssignmentService`

Đặt tại `apps/orders/application/services/lead_assignment_service.py`.

```python
pick_staff_for(date, sheet) -> User | None
    with transaction.atomic():
        shares = LeadAssignmentShare.objects.select_for_update()
                   .filter(date=date, sheet=sheet, weight__gt=0).order_by("id")
        if empty: return None
        total_w = Σ weight; total_assigned = Σ assigned_count + 1
        best = argmax( weight/total_w * total_assigned - assigned_count )  # tie-break: weight cao, id nhỏ
        best.assigned_count += 1; best.save()
        return best.user

assign_if_unassigned(order)  # no-op nếu order.staff_id đã có
    sheet = classify_sheet(order_source=order.order_source, name=order.name,
                           phone=order.phone, raw_address=order.raw_address, address=order.address)
    if sheet is None: return
    user = pick_staff_for(localdate(), sheet)
    if user: order.staff = user; order.save(update_fields=["staff", "updated_at"])

workload(sheet) -> list[{user_id, pending_count}]
    # đơn: staff != null, thuộc sheet, chưa có trạng thái sale → group by staff, count
transfer(sheet, from_user_id, to_user_id, count) -> int
    với transaction.atomic + select_for_update: lấy N đơn cũ nhất (FIFO) của from_user trong
    sheet & chưa có trạng thái sale → set staff = to_user; trả số đơn đã chuyển.
    KHÔNG sửa assigned_count.
```

`select_for_update` trên tập nhỏ dòng-của-(ngày,sheet) serialize các ingest đồng thời ⇒ tỉ lệ
chính xác, không race.

## 8. Điểm móc (chỉ ingest)

- **Landing:** trong `create_order_from_landing` (trong `@transaction.atomic` sẵn có), sau khi tạo
  `order` + `OrderItem` + sync totals, **trước `push_event(...)`**: gọi
  `LeadAssignmentService.assign_if_unassigned(order)`.
- **Lark:** trong view `lark_ingest`, sau `create_order_manual(...)`: gọi cùng hàm — tự no-op nếu
  staff đã được set từ `staff_username`.

Tạo đơn thủ công trong UI **không** móc → không ảnh hưởng.

## 9. API (admin)

Section quyền mới `"lead-assignment"` (thêm vào `VALID_SECTIONS`). Tất cả dùng
`require_section("lead-assignment")`.

- `GET /api/orders/lead-assignment/shares?date=YYYY-MM-DD`
  → `{ date, sheets: [{key, label, total_weight}], rows: [{ user_id, username, display_name,
  cells: { sheet1: {weight, assigned_count, target_pct, actual_pct}, sheet2: {...}, sheet3: {...} } }] }`.
  `target_pct = weight / total_weight(sheet)`; `actual_pct = assigned_count / Σassigned_count(sheet)`.
- `PUT /api/orders/lead-assignment/shares` body `{ date, rows: [{ user_id, weights: {sheet1, sheet2,
  sheet3} }] }` → upsert toàn bộ (ngày): ô weight > 0 → upsert (giữ `assigned_count` của dòng còn
  lại); ô 0/absent → xoá dòng (date, sheet, user) đó. Validate: weight ≥ 0, user tồn tại & chưa xoá,
  không trùng user trong rows.
- `GET /api/orders/lead-assignment/workload?sheet=sheet1`
  → `{ sheet, items: [{ user_id, username, display_name, pending_count }] }`.
- `POST /api/orders/lead-assignment/transfer` body `{ sheet, from_user_id, to_user_id, count }`
  → `{ transferred_count }`. Validate: sheet hợp lệ, from ≠ to, count ≥ 1, user tồn tại.

## 10. Frontend — trang "Chia đơn tự động" (module Settings)

Section `"lead-assignment"`, path `/settings/lead-assignment`. Hai khối:

**Khối 1 — Cấu hình chia (ma trận):**
- Date picker (mặc định hôm nay).
- Bảng: mỗi **dòng = 1 nhân viên**; 3 **cột = 3 sheet** (Landing xác nhận đơn / Messenger xác nhận
  đơn / Tư vấn); mỗi **ô = input %**. Dòng tổng cuối hiển thị **tổng mỗi cột** (gợi ý = 100%).
- Thêm/xoá dòng nhân viên; nút **Lưu** (gọi PUT). Ô 0/trống = NV không nhận sheet đó.

**Khối 2 — Tải & chuyển đơn:**
- Chọn sheet → bảng số đơn đang chờ (no sales_status) mỗi nhân viên.
- Form chuyển: **Từ NV → Đến NV → Số lượng → Chuyển** (gọi transfer, refresh workload).

shadcn/ui, light theme (xanh da trời), lucide-react — theo chuẩn app-shell hiện có.

## 11. Migration & Tests

- Migration tạo bảng `lead_assignment_shares`.
- **Unit test service:** bám tỉ lệ per-sheet qua N lượt; sheet trống → None; `classify_sheet` đúng
  cho từng tổ hợp source/đủ-thông-tin; workload đếm đúng; transfer FIFO chuyển đúng N & không động
  `assigned_count`.
- **Test ingest:** landing đủ TT có cấu hình sheet1 → gán đúng; landing thiếu địa chỉ → sheet3;
  không cấu hình → null; lark có `staff_username` → không ghi đè.
- **Test API:** GET/PUT ma trận roundtrip + xoá ô 0 + giữ assigned_count; workload; transfer +
  validate.

## 12. Ngoài phạm vi (YAGNI)

- Quota/giới hạn số đơn/ngày, ưu tiên waterfall, cấu hình mặc định lặp lại, copy ngày gần nhất.
- Chia theo kênh/sản phẩm/khu vực (chỉ theo nhân viên + sheet + ngày).
- Tự gán cho đơn tạo thủ công trong UI; điều chỉnh counter khi transfer; chọn đơn transfer thủ công.
