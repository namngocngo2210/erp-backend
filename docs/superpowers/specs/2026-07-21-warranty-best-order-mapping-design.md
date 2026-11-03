# Sửa mapping payload Best Express + địa chỉ 3 cấp cho form tạo đơn bảo hành

**Ngày:** 2026-07-21
**Trạng thái:** User đã chốt design
**Liên quan:** `2026-07-18-warranty-best-create-order-design.md` (form v1)

## Bối cảnh

Form "Tạo đơn Best" trong hành trình bảo hành (`BestCreateOrderDialog`) build payload
`Order/Add` ở FE. So lại với form web của Best (`best-inc.com`) và tài liệu V3, phát
hiện hai vấn đề:

1. **Sai tên trường tiền** — FE gửi `DeclareValue` / `CollectionValue`, trong khi
   backend và smoke test (đã tạo đơn thật thành công trên env test) dùng
   `ProductPrice` / `CollectAmount`. Hệ quả: mọi đơn bảo hành tạo qua form đang lưu
   `cod = 0`, `product_price = 0` trong `BestShipment`, và nhiều khả năng Best cũng
   bỏ qua COD/khai giá.
2. **Địa chỉ thiếu cấp Quận/Huyện** — FE gửi `*District: ""` cứng. Picker của Best
   có hai tab "Địa chỉ cũ" (3 cấp) và "Địa chỉ mới"; tài liệu V3 ghi rõ `SourceDistrict`
   *"Nếu là đơn vị hành chính 2 cấp mới, trường này để trống"* — tức trường vẫn tồn tại
   và phải điền với địa chỉ 3 cấp. Ngoài ra CSKH đang phải gõ tay Tỉnh/Phường vì form
   chỉ prefill được `receiver_raw_address`.

## Phạm vi

### 1. Sửa mapping payload (`best-order-form.ts`)

| Hiện tại | Sửa thành |
| --- | --- |
| `DeclareValue` | `ProductPrice` |
| `CollectionValue` | `CollectAmount` |
| *(không gửi)* | `PickupType: 2` (gửi tường minh thay vì dựa default của BE) |
| *(không gửi)* | `Note` (ghi chú cho bưu tá) |
| *(không gửi)* | `Length` / `Width` / `Height` (cm, chỉ gửi khi > 0) |
| `*District: ""` cứng | `*District` lấy từ form, để trống khi địa chỉ 2 cấp |
| `JourneyType: 1` cứng | `JourneyType` chọn được trên form (xem §1b) |

Cân nặng giữ đơn vị **gram** (`Weight`), mặc định 500 — form web Best hiển thị kg là
quy ước UI của họ, API vẫn nhận gram.

**Không** phơi select cho `PickupType` / `ServiceId`: mới biết chắc một giá trị mỗi
trường (`2` / `12491`), đoán enum còn lại thì rủi ro sai tuyến hoặc sai cước. Giữ
hằng số như hiện tại.

### 1b. Loại đơn hàng (`JourneyType`) chọn được

Enum theo tài liệu V3: **1 = Giao hàng, 2 = Thu hồi, 3 = Đơn đổi hàng**.

Source/Dest trong payload **cố định về ngữ nghĩa** (Source = bên đưa hàng cho bưu tá),
chỉ **nhãn hiển thị** đổi theo loại đơn — giống form web của Best:

| JourneyType | Khối Source | Khối Dest |
| --- | --- | --- |
| 1 — Giao hàng | Người gửi | Người nhận |
| 2 — Thu hồi | Người mua | Shop |
| 3 — Đơn đổi hàng | Shop | Người mua |

Mặc định theo chặng bảo hành: gửi đi → 1, thu hồi → 2, gửi lại khách → 1. Logic đảo
Source/Dest cho chặng thu hồi hiện có là **đúng** (Source = khách, Dest = kho), giữ
nguyên.

Khi người dùng đổi loại đơn, vai trò shop/khách có thể hoán vị → nội dung hai khối
phải swap theo, nếu không nhãn đổi mà dữ liệu đứng yên là sai bên:

- Đổi **1 ↔ 3**: shop vẫn ở phía Source → chỉ đổi nhãn, không swap.
- Đổi **sang hoặc rời 2**: shop chuyển phía → swap nội dung khối Source và Dest.

Tách thành hàm thuần `shopSideOf(journeyType) -> "from" | "to"` (tên hai khối
trong form state, ứng với Source/Dest của payload) và
`applyJourneyType(form, nextType) -> BestOrderFormState` để test trực tiếp.

Khối Return (hàng không phát được) không đổi theo loại đơn — luôn là địa chỉ kho.

### 2. Địa chỉ 3 cấp + tự parse

- `AddressBlock` thêm field `district`.
- Mỗi khối địa chỉ (gửi / nhận / hoàn) có thêm ô **"Địa chỉ đầy đủ"**: nhập hoặc dán
  vào → `onBlur` gọi `parseAddress()` → điền Tỉnh / Quận / Phường / Địa chỉ chi tiết.
  Cùng pattern `OrderFormDialog` đang dùng.
- Khối khách được parse **tự động khi mở dialog** từ `journey.receiver_raw_address`
  (bất đồng bộ; trong lúc chờ vẫn hiển thị form với các ô rỗng, không chặn thao tác).
- Bốn ô kết quả luôn sửa tay được — parser sai thì người dùng đè lên.
- Ô Quận/Huyện **không bắt buộc**: địa chỉ 2 cấp mới để trống là đúng theo tài liệu.

Hàm thuần `applyParsedAddress(block, parsed) -> AddressBlock` để test được ngoài React.

Địa chỉ chi tiết dùng nguyên chuỗi `address` mà endpoint trả về (đã format chuẩn
"đường, phường, quận, tỉnh"). Best cho `SourceAddress` tới 1024 ký tự và bưu tá đọc
địa chỉ đầy đủ nên phần lặp lại phường/quận/tỉnh là chấp nhận được; đơn giản hơn việc
thêm field `street` riêng vào response.

### 3. Nới quyền endpoint parse

`parse_raw_address` đang gate `require_section(*ORDER_SECTIONS)`. CSKH bảo hành chỉ có
`warranty:view` sẽ nhận 403 → tính năng auto-parse chết câm. Đổi thành
`require_section(*ORDER_SECTIONS, "warranty")`.

Endpoint chỉ chuẩn hoá chuỗi địa chỉ người dùng tự nhập, không đọc dữ liệu đơn nào —
nới quyền không mở thêm bề mặt dữ liệu.

FE: mở rộng interface `ParsedAddress` (`lib/api/orders.ts`) thêm `ward` / `district` /
`province` — backend đã trả sẵn ba trường này, chỉ TS chưa khai báo.

### 4. Ô thêm vào form

- Loại đơn hàng (select 3 lựa chọn, mặc định theo chặng — §1b)
- Quận/Huyện + Địa chỉ đầy đủ (×3 khối địa chỉ)
- Dài / Rộng / Cao (cm) — tùy chọn
- Ghi chú cho bưu tá + hàng chip bấm nhanh (mượn nguyên các câu của form Best:
  "cho khách xem hàng, không cho thử", "gọi điện cho khách trước khi giao", …)

## Kiến trúc

Không đổi kiến trúc. Vẫn là: helper thuần `best-order-form.ts` build payload →
`useCreateBestShipment` → `POST /api/best-express/shipments` (BE passthrough) → PATCH
mã Best vào journey.

Backend chỉ sửa đúng một dòng permission. `shipment_service.create_shipment` đọc
`CollectAmount` / `ProductPrice` sẵn rồi — sau khi FE gửi đúng tên là tự khớp.

## Test

- Unit `best-order-form`: payload gửi đúng `ProductPrice` / `CollectAmount` /
  `PickupType` / `Note`; `Length`/`Width`/`Height` bỏ qua khi rỗng hoặc 0;
  `*District` lấy từ form và cho phép rỗng; chiều source/dest vẫn đảo đúng theo chặng;
  `JourneyType` mặc định 1/2/1 theo chặng gửi đi/thu hồi/gửi lại.
- Unit `applyJourneyType`: 1 → 3 giữ nguyên nội dung hai khối; 1 → 2 và 2 → 1 swap
  Source/Dest; khối Return không đổi trong mọi trường hợp.
- Unit `applyParsedAddress`: map `province → city`, `district → district`,
  `ward → ward`, `address → address`; field parser trả rỗng thì giữ giá trị cũ.
- Component `BestCreateOrderDialog`: mở dialog → gọi `parseAddress` với
  `receiver_raw_address` và điền Tỉnh/Quận/Phường vào khối khách; nhập tay đè lên
  được; parse lỗi thì form vẫn dùng bình thường.
- Backend: user chỉ có `warranty:view` gọi `POST /api/orders/parse-address` → 200.

## Xác minh sau khi merge

Chạy `python manage.py best_smoke_test --account-id <test> --create` và đối chiếu đơn
trên trang V9: COD và khai giá phải khác 0. Đây là bằng chứng duy nhất khẳng định
`CollectAmount` / `ProductPrice` đúng — repo không lưu tài liệu V3 đầy đủ.

## Không làm (YAGNI)

- Không thêm thời gian lấy hàng, loại hàng hoá, phương thức thanh toán (người gửi /
  người nhận trả phí) — chưa biết tên trường trong API.
- Không thêm select loại dịch vụ (`ServiceId`) / phương thức gửi hàng (`PickupType`)
  — chưa biết đủ enum.
- Không dựng dropdown cascading tỉnh/quận/phường từ catalog: parse + sửa tay đã đủ,
  dropdown 3 tầng làm dialog nặng thêm mà lợi ích biên thấp.
- Không đụng `Order/Update` / `UpdateAddress`.
