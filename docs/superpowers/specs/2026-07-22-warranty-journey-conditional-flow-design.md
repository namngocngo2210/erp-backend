# Hành trình bảo hành: luồng hiển thị có điều kiện + khối feedback và thất lạc

**Ngày:** 2026-07-22
**Trạng thái:** User đã chốt design (Phần 1)
**Liên quan:** `2026-07-08-warranty-claim-journeys-design.md` (form gốc §2-§7),
`2026-07-18-warranty-journey-best-tracking-status-design.md` (badge VC theo Best)

## Bối cảnh

Form hành trình bảo hành hiện hiện **toàn bộ** §2-§7 ngay từ lúc tạo phiếu, kể cả các
khối chưa tới lượt (Kho nhận hàng BH, CSKH chốt phương án, Gửi đơn mới về khách). CSKH
phải tự biết khối nào đang có nghĩa, khối nào bỏ trống.

Thực tế nghiệp vụ: các khối phía sau chỉ có nghĩa khi hành trình đạt tới trạng thái vận
chuyển tương ứng, và trạng thái đó nay đã tự động theo Best. Ngoài ra thiếu hai luồng
đang phải ghi ngoài hệ thống: xin cảm nhận của khách sau khi giao thành công, và khiếu
nại / đền bù khi vận đơn thất lạc.

## Phạm vi

**Phần 1 (spec này):** quy tắc ẩn/hiện + 2 khối mới (feedback, thất lạc) + tình trạng
nhận thực.

**Phần 2 (spec riêng, làm sau):** làm lại khối §7 "Gửi đơn mới về khách" thành phiếu tạo
đơn Best liền mạch. Spec này chỉ quyết định *khi nào* §7 hiện, không đụng *bên trong* nó.

## Model — 8 field mới trên `WarrantyJourney`

### Khối "✅ Khách nhận hàng — Cảm nhận"

| Field | Kiểu | Giá trị |
| --- | --- | --- |
| `feedback_agree_review` | CharField(10) choices, blank | `yes` Có / `no` Không |
| `feedback_rating` | CharField(20) choices, blank | `good` Tốt / `average` Trung bình / `unsatisfied` Không hài lòng / `lost_trust` Mất niềm tin |
| `feedback_note` | TextField blank | Note cảm nhận của khách — tự điền |

### Khối "📮 Khiếu nại & đền bù"

| Field | Kiểu | Giá trị |
| --- | --- | --- |
| `claim_date` | DateField null | Ngày khiếu nại |
| `claim_note` | TextField blank | Ghi chú |
| `carrier_response` | CharField(20) choices, blank | `pending` Chưa phản hồi / `working_with_post` Chờ lv với bưu cục / `agreed` Chốt đền bù / `rejected` Không được đền bù |
| `compensation_status` | CharField(15) choices, blank | `unpaid` Chưa đền bù / `paid` Đã đền bù |
| `compensation_amount` | DecimalField (`_MONEY`) | Số tiền đền bù |

Enum khai bằng `models.TextChoices` cạnh các enum sẵn có trong `apps/warranty/models.py`.
Tất cả vào `JOURNEY_EDITABLE_FIELDS`, `WarrantyJourneyWriteSerializer`,
`WarrantyJourneySerializer`, và `JourneyFormState` phía FE.

### Tình trạng nhận thực — KHÔNG thêm field

Suy từ `status`: bằng `returned` → "Đã nhận", còn lại → "Chưa nhận". Hiển thị badge
**chỉ đọc** trong khối Kho nhận hàng BH.

Lý do: `status = returned` ("Đã nhận hàng hoàn") do quét kho tự đặt, và
`warehouse_received_at` là ngày nhận. Thêm field riêng cùng ý nghĩa sẽ tạo hai nguồn sự
thật lệch nhau — kho quét vào thì `status` đổi nhưng ô nhập tay vẫn "Chưa nhận".

## Quy tắc hiển thị

Logic UI thuần, không tính ở backend. File mới
`frontend/src/features/warranty-claims/components/journey/journey-visibility.ts`:

```ts
export type JourneyVcGroups = {
  new?: string | null;
  recall?: string | null;
  return?: string | null;
};

export type JourneyClaimTrace = {
  claim_date: string | null;
  carrier_response: string;
  compensation_status: string;
};

export type JourneySectionFlagsInput = {
  shippingForm: string;
  vcGroups: JourneyVcGroups | null | undefined;
  inspectionResult: string;
  /** Trạng thái nội bộ SERVER (`journey.status`), do quét kho/cron đặt — không phải bản nháp. */
  journeyStatus: string;
  claimTrace: JourneyClaimTrace;
};

export type JourneySectionFlags = {
  warehouse: boolean;   // §5 Kho nhận hàng BH
  resolution: boolean;  // §6 CSKH chốt phương án
  returnBlock: boolean; // §7 Gửi đơn mới về khách
  feedback: boolean;    // Khách nhận hàng — Cảm nhận
  claim: boolean;       // Khiếu nại & đền bù
};

export function journeySectionFlags(input: JourneySectionFlagsInput): JourneySectionFlags;
```

Chữ ký nhận MỘT object input (không phải tham số vị trí rời) — tránh dãy tham số dài
khó đọc khi thêm `journeyStatus` và `claimTrace` (xem "Sửa sau review" bên dưới).

| Điều kiện | Khối hiện |
| --- | --- |
| `one_way` + mã **gửi đi** = `delivered` | feedback |
| `exchange` + mã **gửi đi** = `delivered` | feedback |
| `exchange` + mã **thu hồi** = `delivered` **hoặc** `returned` | warehouse |
| `warranty_recall` + mã **thu hồi** = `delivered` **hoặc** `returned` | warehouse |
| `warranty_recall` + mã **thu hồi** = `delivered` (chỉ `delivered`, không tính `returned`) | returnBlock |
| `warranty_recall` + mã **gửi lại** = `delivered` | feedback |
| `journey.status` (SERVER) = `returned` (kho quét hàng hoàn) | warehouse — bất kể `shippingForm`/mã nào |
| bất kỳ mã nào = `lost` | claim |
| đã có dấu vết khiếu nại (`claim_date`/`carrier_response`/`compensation_status` khác rỗng) | claim ("dính" — giữ hiện dù mã không còn `lost`) |
| `warehouse` đang hiện **và** `inspection_result` khác rỗng | resolution |

Ghi chú:
- Mã **thu hồi** của `exchange` lẫn `warranty_recall` được Best tạo giống hệt nhau
  (`JourneyType = 2`, shop là bên NHẬN) nên có thể trả về GTC (666) hoặc Đã trả hàng
  (708) tuỳ luồng quét thực tế của ĐVVC — `warehouse` nhận cả hai để không tắc phiếu.
  `returnBlock` (§7) KHÔNG nới theo `returned` — vẫn chỉ mở khi mã thu hồi đã `delivered`.
- `journey.status === "returned"` do `mark_returned_by_tracking` (quét HÀNG HOÀN ở kho)
  đặt khi quét khớp BẤT KỲ mã nào trong 3 mã của hành trình (kể cả mã gửi đi của
  `one_way` khi khách từ chối nhận) — đây cũng là nguồn sự thật mà badge "Đã nhận"
  đang tin, nên phải mở `warehouse` độc lập với `vcGroups`.
- `claim` "dính": một khi hành trình đã ghi bất kỳ dữ liệu khiếu nại/đền bù nào thì
  khối tiếp tục hiện dù mã Best không còn ở nhóm `lost` (VD: 777 → 666 khi tìm lại
  được hàng, hoặc một event Best tới không đúng thứ tự) — tránh mất quyền xem/sửa dữ
  liệu đền bù đã lưu.

`return_recall` và `warranty_return` chưa có quy tắc nghiệp vụ — chỉ hiện `claim` khi có
mã thất lạc hoặc dấu vết khiếu nại, không hiện khối nào khác. Chốt sau khi user định
nghĩa luồng cho chúng.

Các khối §2 Người nhận, §3 SP & lý do, §4 Vận chuyển & chi phí giữ nguyên: **luôn hiện**.

`resolution` phụ thuộc `warehouse` (không thể chốt phương án khi chưa biết hàng hỏng thế
nào) nên nó không bao giờ hiện một mình.

## Frontend

- `journey-visibility.ts` — hàm thuần, không import React.
- `JourneyCard.tsx` gọi hàm này một lần rồi truyền cờ xuống, không để mỗi section tự suy.
- Khối feedback và khối claim là 2 component mới trong
  `journey-sections-return.tsx` (cùng file với các khối hậu-kỳ, tránh sinh file rời rạc).
- Enum options mới thêm vào `journey-enums.ts` theo đúng pattern hiện có.

Khối bị ẩn thì **không render**, không phải `display:none` — giá trị trong bản nháp giữ
nguyên, `buildJourneyPatch` vẫn gửi đủ mọi field như hiện tại. Người dùng đã nhập rồi mà
trạng thái VC lùi lại thì dữ liệu không mất, chỉ tạm không thấy.

## Test

- Unit `journey-visibility`: đủ 7 dòng bảng trên; 2 loại đơn chưa định nghĩa chỉ bật
  `claim` (mọi cờ khác false); `vcGroups` null hoặc cả 3 mã null → **mọi cờ false**;
  `resolution` không bao giờ true khi `warehouse` false; `shippingForm` rỗng → mọi cờ
  false trừ `claim` khi có mã thất lạc.
- Component `JourneyCard`: phiếu mới tạo (chưa mã, chưa trạng thái) KHÔNG render §5/§6/§7,
  feedback, claim; đơn 1 chiều mã gửi đi `delivered` → hiện đúng khối feedback; đơn thu
  hồi BH mã thu hồi `delivered` → hiện warehouse + returnBlock, chưa hiện resolution;
  điền `inspection_result` → hiện resolution; mã bất kỳ `lost` → hiện claim; `status`
  quét kho = `returned` mở `warehouse` dù mã chưa ở nhóm tương ứng (one_way); đơn đổi
  hàng mã thu hồi `delivered` cũng mở `warehouse`; badge tình trạng nhận thực test cả
  hai chiều "Đã nhận"/"Chưa nhận"; journey còn dấu vết khiếu nại (`claim_date`...) dù
  mã hết `lost` → khối khiếu nại vẫn hiện; khối feedback/khiếu nại bị ẩn không làm mất
  `feedback_note`/`claim_note` khi lưu.
- Backend: serializer nhận/trả 8 field mới; `JOURNEY_EDITABLE_FIELDS` chứa đủ 8; PATCH
  từng field lưu đúng.

## Không làm (YAGNI)

- Không đụng nội dung bên trong §7 (Phần 2).
- Không thêm field "Tình trạng nhận thực".
- Không tính cờ hiển thị ở backend.
- Không đổi luồng quét kho, không đổi `status` / `return_status`.
- Không định nghĩa luồng cho `return_recall` / `warranty_return`.
- Không nới `returnBlock` (§7) cho `exchange` hay cho mã thu hồi `returned`.

## Việc cần làm tiếp (ngoài phạm vi)

Phát hiện trong review sau khi làm xong Phần 1, user đã chốt để MR riêng — KHÔNG sửa
ở đây:

> **Bản nháp cũ ghi đè mất dữ liệu server.** `useJourneyForm` bỏ qua đồng bộ khi form
> đang dirty, còn `buildJourneyPatch` gửi MỌI field. Kịch bản: CSKH sửa §2 → form dirty
> → kho quét vào set `warehouse_received_at` phía server → refetch bị bỏ qua → §5 hiện
> ra với ô ngày rỗng nhưng badge "Đã nhận" → bấm Lưu là ghi `warehouse_received_at:
> null`, xoá mất ngày kho đã ghi. Áp cho cả 8 field mới. Cách sửa đúng là chỉ gửi các
> key đã thực sự thay đổi (hoặc trộn lại các key chưa động tới từ bản ghi server). Lỗi
> có sẵn từ trước, nhưng việc ẩn/hiện có điều kiện biến nó thành trường hợp thường gặp.
