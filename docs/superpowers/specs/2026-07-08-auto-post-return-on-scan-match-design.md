# Tự động ghi sổ phiếu trả khi quét khớp mã vận đơn

**Ngày:** 2026-07-08
**Nhánh:** `feat/auto-post-return-on-scan`

## Bối cảnh & vấn đề

Hiện tại luồng quét trả hàng (`ScanReturnDialog`) chia làm 2 nhóm khi bấm **Xác nhận**:

- Mã **khớp đơn** (matched) → tạo **phiếu trả NHÁP** qua `create_return` (`POST /api/inventory/returns`).
- Mã **chưa khớp** (unmatched) → đẩy sang bảng chờ khớp (pending-scans).

Sau đó nhân viên phải **ghi sổ thủ công** từng phiếu nháp (hoặc "Ghi sổ cả đợt") thì `post_return` mới thật sự:
1. Cộng tồn kho (`record_movement` RETURN_IN), và
2. Với phiếu `delivery_returned` có `source_order` → gọi `mark_order_returned(order)` để set `Order.status = RETURNED` ("đã chuyển hoàn") **và** đẩy KiotViet cập nhật `deliveryDetail.status`.

Với đơn đã khớp mã vận đơn, bước ghi sổ tay là dư thừa: dữ liệu đã đầy đủ (đơn nguồn + dòng hàng + loại trả). Yêu cầu: **tự động ghi sổ ngay khi Xác nhận quét** + cập nhật trạng thái vận chuyển = đã chuyển hoàn.

Toàn bộ hành vi mong muốn (ghi sổ + set "đã chuyển hoàn") **đã nằm sẵn trong `post_return`**. Việc cần làm là tự động gọi `post_return` cho dòng khớp đơn, **atomic** với lúc tạo phiếu.

## Quyết định đã chốt

| Vấn đề | Quyết định |
|---|---|
| Thời điểm auto-post | **Ngay khi bấm "Xác nhận" trong ScanReturnDialog.** Không auto ở từng lần quét; không mở rộng sang phiếu tạo tay. |
| Khi ghi sổ bị chặn (guard double-credit, đơn đã hoàn kho, đã có phiếu trả khác...) | **Báo lỗi đỏ, KHÔNG tạo gì** → create + post phải atomic, lỗi thì rollback toàn bộ. |
| Phạm vi | Chỉ luồng quét trả hàng. `CreateReturnDialog` (tạo tay) giữ nguyên tạo nháp. |
| Cách triển khai | Backend atomic `create_and_post` + cờ `auto_post` trên endpoint tạo phiếu (Hướng A). |
| Phiếu nháp đang tồn (đã khớp đơn) | **Backfill 1 lần** bằng management command (có `--dry-run`); phiếu bị guard chặn → skip + log, không chặn phiếu khác. Không tự động dọn qua UI. |

## Kiến trúc

Layering giữ nguyên: `transport → business (service) → storage`. Không phá ranh giới module (import `apps.orders` vẫn là local import trong service như hiện tại).

### 1. Business — `ReturnNoteService.create_and_post_return`

Thêm method mới, bọc atomic:

```python
@staticmethod
@transaction.atomic
def create_and_post_return(*, user_id: int | None = None, **create_kwargs) -> ReturnNote:
    rn = ReturnNoteService.create_return(user_id=user_id, **create_kwargs)
    return ReturnNoteService.post_return(return_note_id=rn.id, user_id=user_id)
```

- `create_return` và `post_return` đều đã `@transaction.atomic` (savepoint lồng nhau). Bọc thêm `@transaction.atomic` ở ngoài → bất kỳ `ValueError` nào từ 2 bước đều làm **rollback toàn bộ**: không phiếu, không dòng, không movement, không đổi status đơn.
- Tái dùng nguyên `post_return` → gồm cả `mark_order_returned` (set RETURNED) + push KiotViet (chạy `on_commit`, kích hoạt sau khi block atomic ngoài cùng commit).
- Luồng quét set `return_type = "delivery_returned"` ở cấp phiếu → `post_return` không vướng lỗi "chưa xác định loại trả hàng". Đường lỗi thực tế chính là `_guard_double_credit`.

### 2. Transport — endpoint tạo phiếu

`CreateReturnNoteSerializer`: thêm field

```python
auto_post = drf_serializers.BooleanField(required=False, default=False)
```

Trong `list_create_returns` (nhánh POST của `return_views.py`):

```python
data = serializer.validated_data
auto_post = data.pop("auto_post", False)  # tách khỏi kwargs của service
create_kwargs = dict(
    return_type=data["return_type"],
    warehouse_id=data["warehouse_id"],
    lines=list(data["lines"]),
    source_order_id=data.get("source_order_id"),
    shipping_code=data.get("shipping_code", ""),
    customer_name=data.get("customer_name", ""),
    customer_phone=data.get("customer_phone", ""),
    source_note=data.get("source_note", ""),
    note=data.get("note", ""),
    user_id=request.user.id,
    scan_batch_id=data.get("scan_batch_id"),
)
try:
    if auto_post:
        rn = ReturnNoteService.create_and_post_return(**create_kwargs)
    else:
        rn = ReturnNoteService.create_return(**create_kwargs)
except ValueError as exc:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
```

- Permission giữ nguyên `require_section("returns")` — endpoint post thủ công (`post_return_view`) cũng chỉ cần section này, nên không phát sinh nâng quyền.
- Vẫn trả **201** kèm phiếu đã serialize; khi auto-post, `status` trong body = `posted`.

### 3. Frontend — types & payload

- `frontend/src/features/inventory/returns-types.ts`: `CreateReturnNotePayload` thêm `auto_post?: boolean`.
- `frontend/src/features/inventory/scan/scan-return-rows.ts` — `buildReturnPayloads(...)`: thêm `auto_post: true` vào payload (chỉ áp cho dòng matched của luồng quét).
- Chạy lại `gen:api` (openapi-typescript) để cập nhật `frontend/src/api/schema.d.ts` theo đúng convention typed-from-schema.

### 4. Frontend — UX `ScanReturnDialog`

- Hiển thị kết quả dòng matched: dựa vào `note.status`.
  - `status === "posted"` → "✓ Đã ghi sổ {code}" (xanh).
  - Lỗi (guard...) → giữ hiển thị đỏ "✗ {error}", **không tạo phiếu** (đúng quyết định).
- Toast tổng kết đổi thành: `Đã ghi sổ {createdCount} phiếu — {pendingCount} mã chờ khớp{ — {failedCount} lỗi nếu >0}`.
- Luồng unmatched → pending-scans **giữ nguyên**.
- Idempotency giữ nguyên: FE đã bỏ qua dòng `results[code].ok` khi bấm Xác nhận lại → không double-post.

### 5. Backfill phiếu nháp đang tồn (management command, 1 lần)

Thay đổi ở trên chỉ ngăn phát sinh nháp mới; các phiếu **đã là nháp** không tự ghi sổ. Bổ sung command chạy 1 lần để dọn:

`python manage.py post_matched_return_drafts [--dry-run] [--limit N]`

- Ứng viên: `ReturnNote` `status=DRAFT`, `source_order` không NULL, `return_type=delivery_returned`, **có ít nhất 1 dòng**. (Đúng đặc trưng phiếu do luồng quét khớp mã sinh ra; không đụng phiếu chờ khớp 0 dòng hay loại khác.)
- Mỗi phiếu gọi `ReturnNoteService.post_return(...)` (tái dùng nguyên guard + mark_order_returned + push KiotViet). `ValueError` (guard double-credit, đơn đã hoàn kho...) → **skip + log lý do**, không chặn phiếu còn lại.
- `--dry-run`: chỉ liệt kê ứng viên (số lượng + mã), không ghi DB. (Guard chỉ chạy khi post thật; dry-run báo trước "một số có thể bị skip khi chạy thật".)
- `--limit N`: giới hạn số phiếu xử lý mỗi lần (chạy theo mẻ cho an toàn trên prod).
- In summary JSON: `candidates / posted / skipped` (+ danh sách mã skip kèm lý do).

### 6. Không đổi

- `CreateReturnDialog` (tạo phiếu tay): vẫn tạo nháp, ghi sổ tay như cũ.
- `bulk_post_returns` ("Ghi sổ cả đợt"): giữ nguyên — vẫn hữu ích cho phiếu nháp còn tồn (vd unmatched sau này được khớp thủ công).

## Luồng dữ liệu (sau thay đổi)

```
Quét mã → matched (đơn + dòng)
  Xác nhận
    → POST /api/inventory/returns { ..., auto_post: true }
        create_and_post_return (atomic)
          create_return  → phiếu DRAFT + dòng hàng
          post_return    → record_movement RETURN_IN (cộng tồn)
                         → mark_order_returned → Order.status = RETURNED
                         → on_commit: push KiotViet deliveryDetail.status
        thành công → 201, status=posted → dòng xanh "Đã ghi sổ {code}"
        ValueError  → rollback toàn bộ → 400 → dòng đỏ "✗ {error}"
Quét mã → unmatched → pending-scans (giữ nguyên)
```

## Xử lý lỗi

- Guard double-credit / thiếu loại / đơn đã RETURNED-liên-quan: `ValueError` từ `post_return` → rollback trong `create_and_post_return` → 400 `{detail}` → FE hiện đỏ, không phiếu nào lưu.
- Lỗi mạng/500: FE per-row try/catch hiện đỏ; các dòng khác vẫn xử lý độc lập (vòng lặp `handleConfirm` không đổi).

## Test

**Backend — service (`test_...` trong apps/inventory/tests):**
- `create_and_post_return` happy path: phiếu `POSTED`, đơn nguồn `RETURNED`, tồn kho cộng đúng số lượng, `order_status_before` được lưu.
- Failure path (dựng tình huống guard double-credit): `ValueError` được raise; **không** có `ReturnNote`/`ReturnNoteLine`/`StockMovement` nào được tạo; `Order.status` **không** đổi.

**Backend — endpoint (`return_views`):**
- `POST /api/inventory/returns` với `auto_post=true` → 201, body `status="posted"`, đơn RETURNED.
- `auto_post=true` gặp guard → 400 và `ReturnNote.objects.count()` không tăng.
- `auto_post=false` (mặc định) → hành vi cũ: 201, `status="draft"`.

**Frontend:**
- `scan-return-rows.test.ts`: `buildReturnPayloads` gắn `auto_post: true` cho dòng matched.
- `ScanReturnDialog`: dòng thành công hiển thị "Đã ghi sổ"; dòng lỗi hiển thị đỏ và không xoá khỏi bảng.

## Ghi chú kỹ thuật / rủi ro

- **Đồng thời:** `post_return` dùng `select_for_update` trên phiếu + đơn + advisory lock đơn (`lock_order_stock`); FE post tuần tự từng dòng trong `handleConfirm` → an toàn.
- **KiotViet:** push chạy `transaction.on_commit`; với transaction lồng, `on_commit` chỉ chạy khi transaction ngoài cùng (`create_and_post_return`) commit — đúng như mong đợi.
- **Backward compat:** `auto_post` mặc định `false` → mọi caller cũ (tạo tay, test hiện có) không đổi hành vi.
- **Không migration DB:** chỉ thêm field serializer (transport), không đổi model.
```
