# Loại hoàn theo từng dòng hàng trên phiếu trả — Design

- **Ngày**: 2026-07-06
- **Trạng thái**: Đã chốt thiết kế với user (per-line + mặc định Hàng hoàn + responsive mobile)
- **Branch**: `feat/return-line-type`

## Bài toán

Phiếu trả hàng hiện chỉ có **một** `return_type` cấp phiếu. Thực tế 1 kiện hoàn
về có thể chứa nhiều sản phẩm cần xử lý khác nhau (cái nhập lại bán tiếp, cái
vào kho bảo hành, cái vào kho lỗi). Người dùng phải chọn 1 loại cho cả phiếu —
không phản ánh đúng thực tế, và phiếu quét hàng loạt tạo ra ở trạng thái "Chưa
phân loại" nên hay bị chặn ghi sổ vì quên chọn.

Yêu cầu đã chốt:

1. Trong dialog chi tiết phiếu trả (mở từ danh sách, trước khi ghi sổ): chọn
   được **loại hoàn cho từng dòng hàng**.
2. **Mặc định là Hàng hoàn** (`delivery_returned`) — phiếu mới tạo và dòng mới
   đều mang loại này sẵn; ai cần khác thì đổi từng dòng.
3. Dialog chi tiết phiếu phải **responsive trên mobile**.
4. Hiển thị **người tạo phiếu** trên chi tiết phiếu.

## Không làm (phạm vi)

- Không đổi cấu trúc 1 phiếu = 1 đơn gốc.
- Báo cáo/thống kê đang group theo loại cấp phiếu giữ nguyên (hạn chế đã biết:
  phiếu trộn loại vẫn được đếm theo loại chung của phiếu).
- Không thêm bulk-classify nhiều phiếu (đã bàn, user chốt không cần).

## Thiết kế

### Data model

`ReturnNoteLine` thêm cột:

```python
return_type = models.CharField(max_length=20, choices=ReturnType.choices, blank=True, default="")
```

Ngữ nghĩa: dòng để trống ("") = **kế thừa** `return_type` của phiếu. Loại cấp
phiếu trở thành "loại chung/mặc định". Không backfill dữ liệu cũ — dòng cũ
trống sẽ kế thừa loại phiếu như hành vi hiện tại.

### Backend — `ReturnNoteService`

- `post_return`:
  - Loại hiệu lực của dòng = `line.return_type or rn.return_type`.
  - Validation thay câu "Phiếu chưa phân loại…": nếu **bất kỳ dòng nào** không
    xác định được loại hiệu lực → lỗi 400 nêu rõ SKU dòng đó. (Phiếu có
    `return_type` thì mọi dòng luôn xác định — tương thích ngược.)
  - `state` movement tính **theo từng dòng** qua `_RETURN_STATE[loại hiệu lực]`.
  - Đánh dấu đơn gốc RETURNED khi **có ít nhất 1 dòng** loại Hàng hoàn (thay vì
    phiếu là Hàng hoàn). `order_status_before`/huỷ-phiếu-revert giữ nguyên.
- `cancel_return`: không đổi — đảo theo movement đã ghi (đã lưu state per dòng).
- `_guard_double_credit`: không đổi (đã tính mọi loại, đúng theo thiết kế
  "1 đơn vị vật lý chỉ cộng tồn 1 lần").
- `create_return` / `update_draft`: nhận `return_type` trong từng phần tử
  `lines`, lưu xuống dòng.

### API / serializers

- `ReturnNoteLineSerializer`: thêm `return_type` (read + write qua lines).
- `_ReturnLineInput`: thêm `return_type` optional (ChoiceField, allow_blank).
- `ReturnNoteSerializer`: thêm `created_by_name` (read-only, từ
  `created_by.display_name`, null-safe vì `created_by` SET_NULL); các view
  list/detail thêm `select_related("created_by")` để tránh N+1.
- Mặc định Hàng hoàn đặt ở **tầng tạo phiếu phía FE** (không đổi default model
  để tránh ảnh hưởng import/luồng khác):
  - Dialog quét trả hàng loạt: mọi payload tạo phiếu gửi
    `return_type: "delivery_returned"`.
  - Dialog tạo phiếu thủ công: select loại chung mặc định Hàng hoàn.
- Regen `frontend/src/api/schema.d.ts` (openapi-typescript).

### Frontend — `ReturnNoteDetailDialog`

- Ô "Loại" đổi nhãn thành **"Loại chung"**; khi phiếu nháp chưa có loại, hiển
  thị mặc định Hàng hoàn (và PATCH set luôn để dữ liệu tường minh).
- Header phiếu thêm dòng **"Người tạo"** (`created_by_name`; hiển thị "—" nếu
  phiếu cũ không còn người tạo).
- Bảng Dòng hàng thêm cột **Loại hoàn**: select per dòng, giá trị hiển thị =
  loại hiệu lực (dòng trống hiển thị loại chung). Chỉ sửa được khi phiếu nháp;
  phiếu đã ghi sổ hiển thị text tĩnh.
- Đổi loại 1 dòng → PATCH với mảng `lines` đầy đủ (backend replace toàn bộ
  lines — hành vi sẵn có), kèm `return_type` từng dòng.
- Logic build payload lines + resolve loại hiệu lực tách ra module thuần
  (`return-line-type.ts`) để unit-test được.

### Responsive mobile (dialog chi tiết phiếu)

- Header thông tin phiếu: `grid-cols-2` → `grid-cols-1 sm:grid-cols-2`.
- Bảng Dòng hàng: desktop giữ table; mobile chuyển dạng card từng dòng (SKU +
  tên + SL + select loại) theo chuẩn responsive của dự án (mobile-first,
  ResponsiveList/pattern tương đương đang dùng).
- Dialog dùng FormDialogShell sẵn có (full-screen trên mobile theo chuẩn
  dialog form của dự án).

### Hiển thị danh sách

- Card/danh sách phiếu: nếu các dòng có loại hiệu lực khác nhau → hiển thị
  "Nhiều loại"; ngược lại hiển thị như cũ. (Chỉ nơi đã có sẵn `lines` trong
  response; không thêm query mới.)

## Test

Backend (pytest, image `desi-order-web-test`):
- Ghi sổ phiếu 3 dòng 3 loại → 3 movement đúng 3 state (SELLABLE/WARRANTY/DEFECT).
- Dòng trống kế thừa loại phiếu; phiếu trống + dòng trống → 400 nêu SKU.
- Đơn gốc RETURNED khi chỉ 1 dòng Hàng hoàn (phiếu loại chung khác).
- Huỷ phiếu trộn loại → tồn đảo đúng từng state.
- create/update lưu `return_type` dòng qua API.

Frontend (vitest):
- Unit test `return-line-type.ts` (resolve loại hiệu lực, build PATCH payload).
- Component test dialog: render select per dòng ở nháp, text tĩnh khi đã ghi
  sổ; đổi 1 dòng gửi đúng payload.

## Rủi ro / tương thích ngược

- Phiếu cũ (loại cấp phiếu, dòng trống): hành vi ghi sổ không đổi từng byte.
- Phiếu nháp cũ "Chưa phân loại": vẫn bị chặn ghi sổ như cũ cho tới khi chọn
  loại (giờ chọn nhanh hơn vì có mặc định).
- KiotViet không liên quan (phiếu trả không đẩy sang KiotViet).
