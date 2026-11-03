# Form tạo đơn Best Express từ hành trình bảo hành

**Ngày:** 2026-07-18
**Trạng thái:** User giao quyền chốt chi tiết ("cứ dựng form lên tôi sẽ điều chỉnh")

## Mục tiêu

Nút "Tạo đơn Best" cạnh 3 ô mã vận đơn của hành trình BH (gửi đi / thu hồi / gửi lại)
→ dialog tạo vận đơn qua API `POST /api/best-express/shipments` (đã đấu nối, payload
Order/Add passthrough) → thành công thì tự PATCH `code_best` vào đúng field mã.

## Hành vi

- Nút icon nhỏ (PackagePlus) cạnh ô mã, CHỈ hiện khi ô mã đang trống (đã có mã thì
  tạo thêm là vô nghĩa).
- Chiều theo chặng:
  - Gửi đi (`new_tracking_code`) + Gửi lại (`return_tracking_code`): Source = kho,
    Dest = khách (prefill receiver_name/phone/raw_address của hành trình).
  - Thu hồi (`recall_code`): đảo — Source = khách, Dest = kho.
  - Khối Return (hàng không phát được) luôn = địa chỉ kho.
- Prefill:
  - Mã đơn (Code): gợi ý `BH<claimId>J<seq><N|R|S>` (N=gửi đi, R=thu hồi, S=gửi lại)
    + hậu tố ngẫu nhiên 4 ký tự (merchant_order_code unique) — sửa được.
  - Tên hàng: `product_sku` hoặc `product_name` của hành trình; Cân nặng mặc định 500g.
  - Khai giá/COD: chặng gửi đi + thu hồi → `declared_value`/`cod_amount`; chặng gửi
    lại → `resolution_declared_value`/`resolution_cod`.
  - Địa chỉ kho (Name/Phone/City/Ward/Address): localStorage key
    `warranty-best-sender-address`, tự lưu mỗi lần tạo thành công.
- Địa chỉ: mỗi khối gồm Tên / SĐT / Tỉnh (City) / Phường (Ward) / Địa chỉ — TEXT
  input v1 (District để trống theo chuẩn 2 cấp; Best validate, lỗi passthrough
  nguyên văn). Checkbox "Hàng hoàn về địa chỉ gửi" (mặc định bật) — tắt thì hiện
  khối Return riêng.
- Account: `GET /api/best-express/accounts` — nếu >1 account thì select, mặc định
  account active đầu tiên; gửi `account_id` trong request.
- JourneyType=1, ServiceId=12491 (default hệ thống, không phơi ra form v1).
- Thành công (201): toast mã Best, PATCH ngay `{<field mã>: code_best}` vào journey
  qua mutation hiện có (không đợi bấm Lưu hành trình; draft các field khác giữ nguyên),
  đóng dialog. Lỗi (`BestExpressError` → 4xx) hiển thị message nguyên văn.

## Kiến trúc FE (convention repo)

- API module mới `frontend/src/lib/api/bestExpress.ts` — typed từ `schema.d.ts`
  (payload là DictField passthrough nên type object tự build theo tài liệu Best V3).
- Mutation React Query trong `features/warranty-claims/queries.ts` (useCreateBestShipment)
  + query accounts (useBestAccounts, staleTime dài).
- Component `features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
  (FormDialogShell chuẩn) + helper thuần `best-order-form.ts` (build payload, suggest
  code, localStorage sender) để test được.
- Nút gắn trong `journey-sections-send.tsx` (2 ô) + `journey-sections-return.tsx` (1 ô).

## Backend

KHÔNG sửa gì — API + permission (`shipments:view`/`shipments:edit`) đã đủ.

## Test

- Unit helper: build payload đúng chiều từng chặng (source/dest đảo cho thu hồi),
  required fields đủ, suggest code đúng format, sender lưu/đọc localStorage.
- Component: dialog mở từ nút cạnh ô mã trống, submit gọi API đúng payload, thành công
  gọi update journey với đúng field mã; ô đã có mã không hiện nút.

## Không làm (YAGNI)

- Không estimate-fee, không in vận đơn, không select catalog tỉnh/phường (v1 text),
  không trang quản lý Best shipments, không sửa BE.
