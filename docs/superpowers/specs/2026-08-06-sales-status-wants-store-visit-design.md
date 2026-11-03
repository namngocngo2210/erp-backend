# Trạng thái sale mới: "Khách muốn đến cửa hàng"

Ngày: 2026-08-06 · Nhánh: `feat/sales-status-wants-store-visit`

## 1. Bối cảnh

Sale cần ghi nhận trường hợp khách không chốt qua điện thoại mà muốn tự đến
cửa hàng xem/trải nghiệm sản phẩm. Hiện sale phải mượn tạm "Tư vấn thêm" hoặc
"Hẹn lịch", làm mất thông tin nghiệp vụ này trong báo cáo.

Catalog trạng thái sale là hằng số tĩnh trong code (`SALES_STATUS_OPTIONS` ở
`backend/apps/orders/crm_catalog.py`), không phải bảng DB. `sales_status_code`
là `CharField` không khai `choices` → **không cần migration**.

## 2. Quyết định nghiệp vụ (chốt với user 2026-08-06)

| Câu hỏi | Quyết định |
|---|---|
| Có tính là "đã chốt" không? | **Không.** Là lead tích cực, chưa chốt |
| Có sinh nhắc lịch Lark không? | **Không.** Không bắt nhập ngày hẹn |
| Xuất hiện ở đâu? | Chỉ trạng thái sale trên đơn. Không đụng resale (khách thân thiết) |

## 3. Mã và nhãn

```
code  = "wants_store_visit"
label = "Khách muốn đến cửa hàng"
```

Vị trí trong dropdown: ngay sau `call_back_later` ("Gọi lại sau") — cùng cụm
follow-up. Màu dòng: xanh dương, giống `scheduled` / `call_back_later`.

## 4. Trạng thái rơi vào tập nghiệp vụ nào

Mỗi tập hằng số dưới đây là một quyết định riêng, không suy ra được từ nhau.

### Thêm vào

| Tập | File | Ý nghĩa |
|---|---|---|
| `POSITIVE_SALES_STATUSES` | `orders/sheet_rules.py` | Lead "ấm" khi suy nhiệt độ |
| `_CODE_TO_ACTION` → `follow_up` | `orders/domain/contact_rules.py` | Trang Contact hiện tác nghiệp cần = Theo dõi |
| `NURTURE_SALES_STATUS_CODES` | `orders/_repositories_reports.py` | Rổ "đang nuôi" dùng chung (segmentation, follow-up history) — **KHÔNG còn là rổ nurture của Nhóm 2**, xem mục 4.1 |
| `NURTURE_SALES_STATUS_CODES` | `customers/segmentation.py` | Khách xếp phân khúc S1 (tiềm năng đang nuôi) |
| `CONSULTING_CODES` | `customers/behavior_classify.py` | Phễu "đang tư vấn" của báo cáo hành vi |

### Cố ý KHÔNG thêm vào

| Tập | Lý do |
|---|---|
| `CLOSED_SALES_STATUSES` (`sheet_rules.py`) | Đơn phải **ở lại Sheet 3 Tư vấn** để sale theo tiếp; vẫn chịu autoclose 14 ngày nếu bị bỏ quên — đúng ý đồ |
| `CLOSED_SALES_STATUS_CODES` / `CLOSED_REVENUE_SALES_STATUS_CODES` | Chưa chốt thì không được cộng doanh thu |
| `DEAD_SALES_STATUSES` | Không phải lead chết |
| `sale_reminder_service` | Không nhắc lịch (mục 2) |

### 4.1 ĐÍNH CHÍNH 2026-08-15 — loại khỏi mẫu số Nhóm 1 & Nhóm 2

Quyết định ban đầu ("vẫn là lead đang chạy → giữ trong mẫu số tỷ lệ chốt") đã bị
**đảo ngược** theo yêu cầu nghiệp vụ: khách tự ra cửa hàng thì kết quả chốt nằm
ngoài tầm tư vấn của sale online, để trong mẫu số sẽ dìm chỉ số của sale nào
nhận nhiều data dạng này. Từ nay `wants_store_visit` được đối xử **y hệt**
`underlying_condition`:

| Nơi | Thay đổi |
|---|---|
| Nhóm 1 — Tỷ lệ chốt | Mẫu số = `total − underlying_condition − wants_store_visit` (helper `closeRateDenominator`) |
| Nhóm 2 — Năng lực Sale | Mẫu số "Data tiếp cận" trừ thêm `wants_store_visit`; số này vào ô "Loại trừ" |
| Rổ `nurture` của Nhóm 2 | Dùng `NURTURE_SALES_STATUS_CODES_FOR_CAPACITY` (bỏ `wants_store_visit`) — đơn đã rời mẫu số thì cũng phải rời tử số |
| `close_rate_by_staff` | `.exclude()` thêm mã này để khớp mẫu số Nhóm 1 |

**KHÔNG đổi:** rổ nurture dùng chung (`customers/segmentation.py`,
`sheet_rules.py`), "% follow-up convert" (đo theo lịch sử journey, đơn từng đi
qua "đến cửa hàng" rồi chốt vẫn được ghi nhận), và các nhóm báo cáo còn lại
(3, 4, 5, 9, 10) — đơn vẫn nằm nguyên trong đó.

## 5. Phạm vi sửa

### Backend (6 file)

1. `apps/orders/crm_catalog.py` — thêm `CatalogOption`. Kéo theo tự động: API
   `/api/crm/catalogs`, whitelist AI auto-fill (`assessment_fields.py`), nhãn
   xuất Excel, timeline liên hệ, màn Call quality, hồ sơ khách.
2. `apps/orders/sheet_rules.py` — `POSITIVE_SALES_STATUSES`.
3. `apps/orders/domain/contact_rules.py` — `_CODE_TO_ACTION`.
4. `apps/orders/_repositories_reports.py` — `NURTURE_SALES_STATUS_CODES`.
5. `apps/customers/segmentation.py` — `NURTURE_SALES_STATUS_CODES`.
6. `apps/customers/behavior_classify.py` — `CONSULTING_CODES`.

### Frontend (3 file)

Ba nơi chép tay danh sách trạng thái (đã tồn tại từ trước, không phát sinh mới):

1. `features/orders/OrdersTable/constants.ts` — `SALES_STATUS_OPTIONS` +
   `SALES_STATUS_TINT` (xanh dương).
2. `features/orders/orders-page/use-orders-filter-options.ts` — bộ lọc trang Đơn.
3. `features/orders/BlockCrmRating.tsx` — danh sách fallback khi catalog API lỗi.

Không đụng `ResaleTrackingPage.tsx` (danh sách resale tách biệt).

## 6. Test

Bám đúng cách commit `f889fd6a` (thêm `underlying_condition`) đã làm:

- `orders/tests/test_contact_rules.py` — `wants_store_visit` → `follow_up`.
- `orders/tests/test_dashboard_summary_api.py` — đơn ở trạng thái này **không**
  cộng vào doanh thu chốt và **vẫn nằm trong mẫu số** tỷ lệ chốt.

Không thêm test lẻ cho từng file hằng số — các tập hợp đã được phủ gián tiếp
qua 2 test trên.

## 7. Rủi ro

Ba danh sách FE chép tay có thể lệch backend nếu lần sau ai đó chỉ sửa một
phía. Đây là nợ kỹ thuật sẵn có; sửa triệt để (FE luôn đọc catalog API, bỏ
hằng số tĩnh) nằm ngoài phạm vi lần này vì đụng nhiều màn hình.
