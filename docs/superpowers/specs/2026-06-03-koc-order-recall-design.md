# Thiết kế: Quản lý thu hồi cho đơn KOC

- **Ngày:** 2026-06-03
- **Branch:** `koc-order-recall-20260603`
- **Phạm vi:** Thêm chức năng quản lý "thu hồi" (recall) cho các đơn KOC (`order_source = "koc"`), gồm tạo bảng dữ liệu mới, hiển thị + edit inline trên trang danh sách KOC, và thay panel "Hành vi mua hàng" bằng panel thu hồi ở trang chi tiết KOC.

## 1. Bối cảnh & yêu cầu

Cần quản lý thông tin thu hồi cho đơn KOC, gồm 4 trường:

| Trường (VN)            | Field code             | Kiểu        |
|------------------------|------------------------|-------------|
| Mã đơn thu hồi         | `recall_code`          | text        |
| Ngày tạo đơn thu hồi   | `recall_created_date`  | date        |
| Ngày nhận hàng thu hồi | `recall_received_date` | date        |
| Phiếu trả hàng         | `return_slip_code`     | text (mã)   |

Quyết định đã chốt với người dùng:

- Quan hệ **1 đơn KOC ↔ 1 đơn thu hồi** (OneToOne).
- "Phiếu trả hàng" lưu dạng **mã/text** (không upload file, không URL).
- Dữ liệu **nhân viên nhập tay** (inline edit), không đồng bộ tự động.
- Trang chi tiết đơn KOC **ẩn phần "Hành vi mua hàng" (CRM)** và thay bằng **panel thu hồi**.

## 2. Hướng tiếp cận

**Bảng riêng `OrderRecall` (OneToOne với `Order`)** — mirror đúng pattern `OrderAssessment` (CRM) đã có trong codebase. Lý do: đúng yêu cầu "làm thêm 1 bảng", giữ bảng `Order` không bị phình thêm 4 cột null cho ~95% đơn không phải KOC, và dễ mở rộng (thêm trạng thái/lý do thu hồi) về sau.

Đã loại: thêm 4 cột trực tiếp vào `Order` (làm bẩn bảng, đi ngược pattern tách bảng của CRM).

## 3. Thiết kế chi tiết

### 3.1. Backend — Model

File: `backend/apps/orders/models.py`

```python
class OrderRecall(models.Model):
    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="recall"
    )
    recall_code = models.CharField(max_length=64, blank=True, default="")          # Mã đơn thu hồi
    recall_created_date = models.DateField(null=True, blank=True)                  # Ngày tạo đơn thu hồi
    recall_received_date = models.DateField(null=True, blank=True)                 # Ngày nhận hàng thu hồi
    return_slip_code = models.CharField(max_length=64, blank=True, default="")     # Phiếu trả hàng (mã/text)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_order_recalls",
    )
```

- `related_name="recall"` → truy cập `order.recall`.
- Hai trường ngày dùng `DateField` (chỉ ngày, không giờ).

### 3.2. Backend — Migration

- File mới: `backend/apps/orders/migrations/0073_order_recall.py` (kế tiếp `0072`).
- Tạo bảng `OrderRecall`. Không cần index đặc biệt ngoài unique của OneToOne (FK `order_id`).

### 3.3. Backend — Serializer

File đọc: `order_core_serializers.py` (`OrderSerializer`)

- Thêm `recall = serializers.SerializerMethodField()`.
- `get_recall(self, obj)`: trả `{recall_code, recall_created_date, recall_received_date, return_slip_code}` hoặc `None` nếu chưa có (mirror `get_crm`). Ngày serialize dạng ISO `YYYY-MM-DD`.

File ghi: `kiotviet_landing_serializers.py` (`OrderInlineUpdateSerializer`)

- Nhận object con lồng nhau `recall` (giống cách `crm` đang làm).
- Validate: mã ≤ 64 ký tự; ngày parse `YYYY-MM-DD`; cho phép giá trị rỗng/null để xoá.

### 3.4. Backend — Service & Query

- `OrderService.update_order_inline`: nếu payload có key `recall` → **upsert** `OrderRecall` cho order (tạo nếu chưa có), set `updated_by = request.user`.
- Query list KOC (`list_koc_orders`) và detail (`update_order_inline` GET): thêm `select_related("recall")` để tránh N+1.
- Dùng chung endpoint `PATCH /api/orders/{id}/` — **không thêm route mới**.

### 3.5. Frontend — Types

File: `frontend/src/features/orders/order-types.ts`

- Thêm type:
  ```ts
  export type OrderRecallRow = {
    recall_code?: string | null;
    recall_created_date?: string | null;   // YYYY-MM-DD
    recall_received_date?: string | null;   // YYYY-MM-DD
    return_slip_code?: string | null;
  };
  ```
- `OrderRow`: thêm `recall?: OrderRecallRow | null;`.
- `OrderColumnKey`: thêm 4 key `recall_code`, `recall_created_date`, `recall_received_date`, `return_slip_code`.
- `ORDER_COLUMN_OPTIONS` + `ORDER_COLUMN_FIELD_BY_KEY`: thêm 4 entry tương ứng.

### 3.6. Frontend — Trang danh sách KOC (cột inline)

File: `frontend/src/components/OrdersTable.tsx` (`OrderCell` switch)

- Thêm 4 case:
  - `recall_code`, `return_slip_code`: ô `<input type="text">` (theo pattern input text inline hiện có).
  - `recall_created_date`, `recall_received_date`: ô `<input type="date">`.
- Khi đổi giá trị → `onPatch(row.id, { recall: { <field>: value } } as Partial<OrderRow>)`.
- Đọc giá trị hiển thị từ `row.recall?.<field>`.
- Tôn trọng `row.can_edit` như các cột khác (không edit được thì hiển thị text tĩnh).

**Chỉ hiện trên trang KOC:**

- Lọc danh sách cột chọn được (`ORDER_COLUMN_OPTIONS`) để 4 cột thu hồi chỉ xuất hiện khi `isKoc = true` (truyền cờ `isKoc` xuống chỗ render bộ chọn cột / bảng).
- Đưa 4 cột thu hồi vào tập cột mặc định của trang KOC (không ảnh hưởng default chung của các trang khác).

### 3.7. Frontend — Trang chi tiết KOC (swap panel)

- Component mới: `frontend/src/features/orders/BlockRecall.tsx`
  - Dùng `SectionCard` (icon phù hợp) tiêu đề "Thu hồi".
  - 4 hàng `ERow`: mã đơn thu hồi (text), ngày tạo (date), ngày nhận (date), phiếu trả hàng (text).
  - Patch qua `patch({ recall: { ... } })` (theo pattern của `BlockCrmRating`).
- File: `frontend/src/pages/OrderDetailPage.tsx` (vùng ~721-727)
  - Render có điều kiện: `isKoc ? <BlockRecall .../> : <BlockCrmRating .../>`.
  - Cờ `isKoc` đã được truyền vào `OrderDetailPage` từ route (`shell.section === "koc-orders"`), hiện chưa dùng → chỉ cần dùng.

### 3.8. Frontend — i18n

- Thêm labels cho 4 cột + tiêu đề panel (key `orders.columns.recallCode`, `orders.columns.recallCreatedDate`, `orders.columns.recallReceivedDate`, `orders.columns.returnSlipCode`, và tiêu đề panel thu hồi) trong file ngôn ngữ hiện có.

## 4. Luồng dữ liệu

```
[Trang KOC list / detail]
  GET /api/orders/{id}/  → OrderSerializer.get_recall → row.recall
  Inline edit / BlockRecall onChange
  PATCH /api/orders/{id}/ { recall: {...} }
     → OrderInlineUpdateSerializer (nested recall)
     → OrderService.update_order_inline → upsert OrderRecall (updated_by)
     → trả Order serialized (kèm recall mới)
```

## 5. Edge cases & xử lý lỗi

- Đơn chưa có bản ghi thu hồi → `recall = null`; UI hiển thị ô trống, patch lần đầu sẽ tạo mới.
- Xoá giá trị (để trống) → ghi chuỗi rỗng / `null` (date) hợp lệ.
- Đơn đã huỷ (status=6 / invoice_status=2) → giữ nguyên rule không cho edit như các field khác (logic `update_order_inline` hiện có).
- Mã vượt 64 ký tự / ngày sai định dạng → serializer trả lỗi validation 400.
- Các cột thu hồi không lộ trên trang đơn thường / landing (lọc theo `isKoc`).

## 6. Testing

Backend (`manage.py test`, theo memory về cách chạy test):

- Tạo `OrderRecall` qua PATCH với order KOC → bản ghi được tạo, GET trả đúng `recall`.
- PATCH cập nhật trường con → cập nhật đúng, không tạo bản ghi trùng (upsert).
- PATCH `recall` rỗng/null → xoá giá trị hợp lệ.
- Validation: mã quá dài, ngày sai định dạng → 400.
- `select_related("recall")` không gây N+1 (kiểm tra số query khi list).

Frontend:

- `OrderCell` render đúng 4 cột (text/date) và gọi `onPatch` với payload `{ recall: {...} }`.
- 4 cột thu hồi chỉ xuất hiện khi `isKoc = true`.
- Trang chi tiết KOC render `BlockRecall` thay vì `BlockCrmRating`; trang thường vẫn render `BlockCrmRating`.

## 7. Phạm vi loại trừ (YAGNI)

- Không upload file/ảnh phiếu trả hàng (chốt: chỉ mã/text).
- Không đồng bộ tự động từ KiotViet.
- Không thêm trạng thái/lý do thu hồi (có thể mở rộng sau nhờ bảng riêng).
- Không tạo trang quản lý thu hồi riêng — dữ liệu xem/sửa ngay trên trang đơn KOC.
