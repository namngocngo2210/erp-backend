# §7 Gửi đơn mới về khách: phiếu tạo đơn Best liền mạch

**Ngày:** 2026-07-22
**Trạng thái:** User đã chốt design (Phần 2)
**Liên quan:** `2026-07-22-warranty-journey-conditional-flow-design.md` (Phần 1 — quyết định *khi nào* §7 hiện),
`2026-07-18-warranty-best-create-order-design.md` (dialog tạo đơn Best gốc)

## Bối cảnh

Khối §7 "Gửi đơn mới về khách" hiện là 4 ô nhập rời (ĐVVC, phí vận chuyển, ngày lên đơn,
mã vận đơn) cộng một nút icon mở dialog tạo đơn Best. CSKH phải rời khỏi ngữ cảnh hành
trình, điền một dialog lớn, rồi quay lại tự điền nốt ĐVVC và ngày lên đơn.

Mục tiêu: §7 trở thành phiếu tạo đơn liền mạch — bấm một nút là form Best bung ra ngay
tại chỗ, tạo xong tự điền những gì suy được, và hiển thị trạng thái vận chuyển tự động
(badge đã có sẵn từ luồng cron/webhook).

## Phạm vi

Chỉ khối §7 và phần tách dùng chung phục vụ nó. KHÔNG đổi cách chặng gửi đi / thu hồi
tạo đơn — hai chặng đó là ô nhỏ nằm trong một hàng dày đặc, dialog vẫn hợp; §7 là cả một
khối nên bung inline mới hợp.

## Kiến trúc

`BestCreateOrderDialog` hiện gộp ba trách nhiệm: giữ state form, dựng thân form, và vỏ
dialog. Tách làm ba để §7 dùng lại thân form thay vì copy:

| File | Trách nhiệm |
| --- | --- |
| `components/best/useBestOrderForm.ts` (mới) | Hook: khởi tạo form theo chặng, auto-parse địa chỉ khách, chọn tài khoản Best, submit (gọi Best → PATCH → báo về) |
| `components/best/BestOrderFormBody.tsx` (mới) | Thuần trình bày: nhận `form` + `setForm`, render mọi ô (loại đơn, hàng hoá, kích thước, 3 khối địa chỉ, ghi chú + chip) |
| `components/best/BestCreateOrderDialog.tsx` (sửa) | Chỉ còn vỏ dialog + footer, dùng lại hook và body. Giữ nguyên hành vi cho chặng gửi đi / thu hồi |
| `components/journey/journey-sections-return.tsx` (sửa) | §7 dùng cùng hook + body, bày inline |

Đây là refactor có mục đích: không tách thì §7 phải nhân bản toàn bộ thân form, và mọi
sửa đổi sau này phải làm hai lần ở hai chỗ.

Hook trả về đúng những gì cả hai nơi cần:

```ts
type UseBestOrderForm = {
  form: BestOrderFormState | null;
  setForm: React.Dispatch<React.SetStateAction<BestOrderFormState | null>>;
  /** Đã lọc `is_active` — nơi gọi chỉ việc render, không lọc lại. */
  accounts: BestAccount[];
  accountId: string;
  setAccountId: (id: string) => void;
  customReturn: boolean;
  setCustomReturn: (value: boolean) => void;
  submit: () => void;
  isPending: boolean;
};

function useBestOrderForm(args: {
  journey: WarrantyJourney;
  leg: BestLeg;
  active: boolean;              // dialog đang mở / form inline đang bung
  onCreated: (code: string) => void;   // gọi SAU khi PATCH thành công
}): UseBestOrderForm;
```

`active` thay cho `open` của dialog: cùng ý nghĩa "form đang sống, cần khởi tạo lại".

## Ba trạng thái của §7

| Trạng thái | Hiển thị |
| --- | --- |
| Chưa có mã, chưa bung | 4 ô như hiện nay + nút **"Tạo đơn Best"** |
| Đang bung | Form Best inline thay chỗ 4 ô, kèm nút **Hủy** và **Tạo đơn** |
| Đã có mã | 4 ô, ô mã kèm badge VC, không còn nút tạo |

Bấm **Hủy** thì thu lại, không đụng gì tới dữ liệu đã có.

Nút "Tạo đơn Best" chỉ hiện khi ô mã đang trống — giữ nguyên quy tắc của
`BestCreateOrderButton` hiện tại (đã có mã mà tạo thêm là vô nghĩa).

## Tự điền sau khi tạo đơn

Tạo đơn thành công thì PATCH **một lần, ba field**:

```ts
patch: {
  return_tracking_code: code,
  return_carrier: "best",
  return_order_date: <hôm nay, định dạng YYYY-MM-DD>,
}
```

Rồi `setSynced` cả ba vào bản nháp.

Phí vận chuyển KHÔNG tự điền: `BestShipment.total_fee` bằng 0 lúc mới tạo, phí thật chỉ
về sau qua webhook hoặc trace. CSKH tự nhập như hiện nay.

Cả ba field vẫn sửa tay được sau đó.

**Vì sao phải nằm trong cùng PATCH:** `setSynced` dành cho giá trị ĐÃ lưu server (nó cố ý
không bật cờ "chưa lưu"). Nếu chỉ đổ ĐVVC/ngày vào bản nháp mà server chưa có, CSKH đóng
trang là mất, và trong lúc đó form hiển thị dữ liệu server không hề có.

## Điều KHÔNG đổi

- Payload `Order/Add` và mọi logic trong `best-order-form.ts` giữ nguyên.
- Chặng gửi đi (`new`) và thu hồi (`recall`) vẫn dùng `BestCreateOrderButton` + dialog.
- Badge trạng thái VC giữ nguyên nguồn (webhook realtime + cron 10 phút).
- Quy tắc *khi nào* §7 hiện thuộc Phần 1, không đụng.

## Test

- Unit hook `useBestOrderForm`: khởi tạo lại form mỗi lần `active` chuyển false→true;
  `submit` gọi tạo đơn với payload đúng chặng; thành công → PATCH mang đủ **ba** field
  (`return_tracking_code`, `return_carrier: "best"`, `return_order_date` hôm nay) →
  rồi mới gọi `onCreated`; PATCH lỗi → KHÔNG gọi `onCreated`.
- Component `BestCreateOrderDialog`: hành vi hiện có không đổi (chạy lại bộ test sẵn có,
  không sửa kỳ vọng).
- Component §7: mã trống → hiện nút "Tạo đơn Best", chưa hiện form; bấm → form bung, 4 ô
  tạm ẩn; bấm Hủy → thu lại, dữ liệu 4 ô nguyên vẹn; tạo thành công → form thu lại, ô mã
  có giá trị + badge, ĐVVC = BEST, ngày lên đơn = hôm nay; đã có mã sẵn → không hiện nút.

## Không làm (YAGNI)

- Không chuyển chặng gửi đi / thu hồi sang inline.
- Không tự điền phí vận chuyển (cần đường đồng bộ `total_fee` riêng — việc khác).
- Không đụng `best-order-form.ts`, không đụng payload gửi Best.
- Không thêm field model, không migration.
