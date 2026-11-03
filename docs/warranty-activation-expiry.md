# Phiếu bảo hành — Ngày kích hoạt, Ngày hết hạn, Số ngày bảo hành

- Ngày: 2026-07-11
- Branch: `feat/warranty-activation-expiry`
- Phạm vi: `backend/apps/warranty`, `frontend/src/features/warranty-claims`, `frontend/src/pages/WarrantyClaim*.tsx`

## 1. Bối cảnh

Trang **Phiếu bảo hành** (`/sales/warranty`, model `WarrantyClaim`) hiện theo dõi được đơn gốc, trạng thái
phiếu, ngày khách báo lỗi và các hành trình bảo hành, nhưng **không biết phiếu còn hạn bảo hành hay không**.
Nhân viên phải tự tra ngày mua rồi nhẩm 2 năm.

## 2. Mục tiêu

Thêm 3 cột vào bảng danh sách phiếu bảo hành (và trang chi tiết + file Excel):

| Cột | Nguồn | Ghi được? |
|---|---|---|
| Ngày kích hoạt | NV chọn (ngày khách kích hoạt bảo hành) | Có |
| Ngày hết hạn | `ngày kích hoạt + số tháng bảo hành` (mặc định 24 tháng = 2 năm) | Không (dẫn xuất) |
| Số ngày bảo hành | `ngày hết hạn − hôm nay` — số ngày còn lại | Không (dẫn xuất) |

## 3. Quyết định thiết kế (đã chốt)

1. **"Số ngày bảo hành" = số ngày CÒN LẠI** tới ngày hết hạn, không phải tổng thời hạn. Giá trị đổi theo từng ngày.
2. **Thời hạn lưu theo từng phiếu**: field `warranty_months` (mặc định 24), sửa được — để sau này sản phẩm có
   thời hạn khác vẫn dùng được mà không phải đổi code.
3. **Ngày kích hoạt mặc định trống**, NV tự chọn. Chưa chọn → cả 3 cột hiển thị `—`.
4. **Nhập liệu ở cả 2 nơi**: sửa inline ngay trên ô của bảng danh sách, và ở khối thông tin trang chi tiết phiếu.
5. **Tính ở backend** (không tính ở frontend): bảng, trang chi tiết và file Excel dùng chung một công thức duy nhất.
6. **3 cột mới bật mặc định cho mọi người**: đổi `localStorage` key của bộ chọn cột → cấu hình cột cũ (ẩn/hiện/thứ tự)
   của người dùng bị reset về mặc định một lần. Đây là đánh đổi đã chấp nhận.

## 4. Backend

### 4.1 Model — `apps/warranty/models.py`

`WarrantyClaim` thêm 2 field:

```python
activation_date = models.DateField(null=True, blank=True)
warranty_months = models.PositiveSmallIntegerField(default=24)
```

Và 2 property dẫn xuất (không lưu DB):

```python
@property
def expiry_date(self) -> date | None:
    if not self.activation_date:
        return None
    return self.activation_date + relativedelta(months=self.warranty_months)

@property
def warranty_days_left(self) -> int | None:
    expiry = self.expiry_date
    if expiry is None:
        return None
    return (expiry - timezone.localdate()).days
```

- `relativedelta` từ `python-dateutil` (đã có trong `requirements.txt`) — cộng theo tháng, tự xử lý 29/02 và tháng thiếu ngày.
- `warranty_days_left` trả **số nguyên có dấu**: `> 0` còn hạn, `<= 0` đã hết hạn. Không clamp về 0 ở backend
  để không mất thông tin; frontend quyết định cách hiển thị.
- `timezone.localdate()` — mốc "hôm nay" theo giờ VN (+07), không phải UTC.

Migration: `0012_claim_activation_warranty_months.py` (chỉ `AddField`, không backfill — dữ liệu cũ để trống).

### 4.2 Serializer — `apps/warranty/serializers.py`

- `WarrantyClaimSerializer` (read): thêm `activation_date`, `warranty_months`, `expiry_date`, `warranty_days_left`
  vào `fields`. 2 field dẫn xuất khai báo `serializers.DateField(read_only=True)` /
  `serializers.IntegerField(read_only=True, allow_null=True)` để OpenAPI sinh đúng kiểu.
- `WarrantyClaimWriteSerializer` (write): thêm `activation_date` (optional, cho `null`) và `warranty_months`
  (optional). `validate_warranty_months`: bắt buộc `1 <= value <= 120`, sai → "Số tháng bảo hành phải từ 1 đến 120."

### 4.3 Service — `apps/warranty/claim_service.py`

`_CLAIM_EDITABLE_FIELDS` thành `("customer_report_date", "activation_date", "warranty_months", "status")`.
Không cần sửa gì thêm: `create_claim` và `update_claim` đều duyệt theo hằng số này.

### 4.4 Excel — `apps/warranty/claim_excel.py`

`_HEADERS` + `_claim_row` thêm 3 cột **Ngày kích hoạt**, **Ngày hết hạn**, **Số ngày bảo hành** (đặt sau
"Ngày khách báo lỗi"). Ô "Số ngày bảo hành" ghi số nguyên, để trống khi chưa kích hoạt.

## 5. Frontend

### 5.1 Cấu hình cột — `features/warranty-claims/warranty-claim-columns.ts`

- `WarrantyColumnKind` thêm `"date"` (ô sửa inline kiểu ngày).
- `WarrantyColumnKey` thêm `"activation_date" | "expiry_date" | "warranty_days_left"`.
- `WARRANTY_COLUMN_OPTIONS` thêm 3 mục: Ngày kích hoạt (`date`), Ngày hết hạn (`readonly`),
  Số ngày bảo hành (`readonly`).
- `DEFAULT_WARRANTY_COLUMNS` thêm 3 key mới.
- `columnDisplayValue` xử lý: `expiry_date` → `fmtDateOnly`; `warranty_days_left` → `null` ⇒ `—`,
  `<= 0` ⇒ `Hết hạn`, ngược lại `N ngày`.

### 5.2 Reset cấu hình cột — `warranty-claims-storage.ts`

Đổi 3 hằng số key: `desi_warranty_visible_columns_v2`, `desi_warranty_locked_columns_v2`,
`desi_warranty_column_order_v2`. Key cũ không đọc nữa (không cần dọn — localStorage rác vài trăm byte).

### 5.3 Bảng — `components/WarrantyClaimsTable.tsx`

`renderCell` thêm nhánh `kind === "date"`: `<Input type="date">` với `key`/`defaultValue` = giá trị hiện tại,
`onBlur` → `onPatch(row.id, { activation_date: value || null })`, `onClick={stop}` để không mở trang chi tiết.
Đúng mẫu ô "Ngày báo lỗi" đang dùng ở `WarrantyClaimDetailPage`.

Ô "Số ngày bảo hành" hết hạn hiển thị chữ đỏ (`text-destructive`) để dễ nhận ra.

### 5.4 Trang chi tiết — `pages/WarrantyClaimDetailPage.tsx`

Khối thông tin thêm 4 mục:

- **Ngày kích hoạt** — `<Input type="date">`, PATCH khi blur (giống ô Ngày báo lỗi).
- **Số tháng bảo hành** — `<Input type="number" min=1 max=120>`, PATCH khi blur.
- **Ngày hết hạn** — chỉ đọc.
- **Số ngày bảo hành** — chỉ đọc, đỏ khi hết hạn.

### 5.5 Types — `warranty-claim-types.ts`

`WarrantyClaimPayload` thêm `activation_date?: string | null` và `warranty_months?: number`.
Các field đọc lấy tự động từ `components["schemas"]["WarrantyClaim"]` sau khi chạy `npm run gen:api`.

## 6. Test

Backend (`apps/warranty/tests/`):

- `test_models.py`: `expiry_date` = kích hoạt + 24 tháng; `activation_date = None` ⇒ cả 2 property trả `None`;
  `warranty_months` khác 24 cho ra hạn khác; kích hoạt 29/02 năm nhuận không vỡ.
- `test_claim_api.py`: PATCH `activation_date` + `warranty_months` lưu được và response trả về
  `expiry_date` / `warranty_days_left` đúng; PATCH `warranty_months = 0` ⇒ 400.
- `test_claim_bulk_export.py`: file Excel có đủ 3 cột mới.

Frontend: `warranty-claim-columns.test.ts` cho `columnDisplayValue` với 3 key mới (còn hạn / hết hạn / chưa kích hoạt).

## 7. Không làm (YAGNI)

- Không tự suy ngày kích hoạt từ ngày đơn / ngày giao — NV nhập tay.
- Không cảnh báo/nhắc phiếu sắp hết hạn, không lọc theo trạng thái hạn bảo hành.
- Không đưa ngày kích hoạt vào form "Thêm bảo hành" (tạo xong sửa inline ngay trên bảng).
- Không đưa `warranty_months` thành cột riêng của bảng (chỉ sửa ở trang chi tiết).
