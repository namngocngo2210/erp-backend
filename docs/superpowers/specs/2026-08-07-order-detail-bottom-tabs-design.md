# Tab hoá khối cuối trang chi tiết đơn — Hành vi mua hàng & Lịch sử thao tác

Ngày: 2026-08-07
Trạng thái: Đã duyệt thiết kế

## Vấn đề

Cuối `OrderDetailPage` đang xếp dọc ba card độc lập:

1. `BlockCrmRating` → SectionCard "Hành vi mua hàng" + `AiOpenInsights` "AI ghi nhận thêm"
   (đơn KOC thì thay bằng `BlockRecall` "Thu hồi đơn")
2. `BlockCallRecords` → "Cuộc gọi CSKH"

Ba card này cùng trả lời một câu hỏi nghiệp vụ ("khách này thế nào") nhưng chiếm
ba khung riêng, đẩy trang dài ra và không có chỗ đặt thêm nội dung mới.

Song song đó, `order_journey_events` — nguồn duy nhất ghi lại ai đã sửa gì trên
đơn — đã có endpoint `GET /api/orders/:id/journey-events` nhưng trang chi tiết
chưa hiển thị; chỉ sheet Tư vấn dùng qua `Sheet3ContactTimeline`.

## Giải pháp

Thay khối cuối bằng một khung tab hai tab:

| Tab | Nội dung |
| --- | --- |
| "Hành vi mua hàng" (KOC: "Thu hồi đơn") | CRM/Recall + AI ghi nhận thêm + Cuộc gọi CSKH, ba mục phẳng cách nhau bằng divider |
| "Lịch sử thao tác" | Timeline `order_journey_events`, tải khi mở tab |

Khung tab đóng vai trò card duy nhất; ba mục bên trong bỏ khung card.

## Thành phần

### 1. `SectionCard` — thêm `variant`

`features/orders/order-detail-shared.tsx`

```ts
variant?: "card" | "plain"   // mặc định "card"
```

- `"card"`: giữ nguyên hiện tại (border + bg-card + shadow + padding).
- `"plain"`: bỏ khung, heading chỉ còn icon + title với gạch dưới mảnh, nội dung
  không padding ngang.

**Bắt buộc mặc định là `"card"`**: `Sheet3AssessmentPanel` (board Tư vấn) tái
dùng `BlockCrmRating` và vẫn cần khung card.

### 2. Bốn block nhận `variant` pass-through

`BlockCrmRating` (truyền tiếp cho `AiOpenInsights`), `BlockCallRecords`,
`BlockRecall`. Chỉ thêm prop và chuyển xuống `SectionCard`, không đụng logic.

### 3. `journey-event-format.ts` — helper thuần

`features/orders/journey-event-format.ts`

```ts
describeJourneyEvent(event, catalog): { title: string; change: string }
```

- `title` = `event_type_label` (backend đã map từ `EVENT_TYPE_LABELS`), fallback
  `event_type` raw.
- `change` = diễn giải `from_code → to_code`. Khi `payload_json.field` trỏ tới
  một danh mục CRM đã có trong `crmCatalog` (`sales_status_code`,
  `customer_classification_code`, `not_closed_reason_code`,
  `delivery_business_status_code`) thì đổi mã sang nhãn tiếng Việt; không map
  được thì hiện raw. Thiếu cả `from_code` lẫn `to_code` → chuỗi rỗng.

Đây là nơi duy nhất có logic thật → unit test đặt ở đây.

### 4. `BlockJourneyEvents` — tab 2

`features/orders/BlockJourneyEvents.tsx`

- Hook `useOrderJourneyEvents(orderId, enabled)` trong `features/orders/queries.ts`,
  gọi `getOrderJourneyEvents` đã có sẵn. **Không đụng backend.**
- `enabled` = tab 2 đang mở → giữ đúng ý đồ "list view cố tình không kèm journey
  events để query nhẹ".
- Mỗi dòng: `title` · thời gian tuyệt đối (`fmtDate`) · actor. Không có actor →
  "Hệ thống". Dòng dưới là `change` và `note` nếu có.
- Hiển thị **mọi** loại event, sắp xếp mới nhất trước (backend đã
  `order_by("-created_at")`).
- Trạng thái: đang tải → spinner; lỗi → thông báo lỗi; rỗng → "Chưa có thao tác
  nào được ghi nhận."

### 5. `OrderDetailPage` — lắp tab

- Dùng `components/ui/tabs.tsx` sẵn có, bọc trong một `div` bo góc + border +
  `overflow-hidden` để tab bar trở thành mép trên của card.
- **Giữ nguyên ranh giới `fieldset disabled={isReadOnly}`**: hiện `fieldset` bọc
  lưới 2 cột + khối CRM nhưng cố ý chừa `BlockCallRecords` ra ngoài để đơn đã hủy
  vẫn nghe được ghi âm. Sau khi tab hoá: đóng `fieldset` ngay sau lưới 2 cột;
  trong tab 1 chỉ bọc riêng phần CRM/Recall bằng `fieldset` mới. Tab 2 read-only
  nên nằm ngoài.
- Tab đang chọn giữ trong state cục bộ, reset khi đổi đơn (`orderId` thay đổi).

## Không làm

- Không đổi backend: endpoint, serializer, quyền đều giữ nguyên.
- Không thêm bộ lọc theo loại event ở tab 2 (YAGNI — thêm khi đơn thực tế có quá
  nhiều event).
- Không sửa `Sheet3ContactTimeline`; sheet Tư vấn giữ nguyên hành vi.
- Không refactor nội dung bên trong `BlockCrmRating`.

## Kiểm thử

- `journey-event-format.test.ts`: map nhãn theo `payload_json.field`, fallback
  raw khi mã ngoài danh mục, chuỗi rỗng khi thiếu from/to.
- `BlockJourneyEvents.test.tsx`: render timeline, trạng thái rỗng, trạng thái lỗi,
  không fetch khi `enabled=false`.

## Rủi ro

- `Tabs` root dùng `h-full min-h-0 flex-col`; ở đây nó nằm trong container cuộn
  của trang với chiều cao auto nên panel `flex-1` co theo nội dung, không sinh
  scroll lồng. Đã kiểm tra, không cần sửa `Tabs`.
- `BlockCallRecords` `return null` khi `isPending`/`isError` — giữ nguyên; tab 1
  sẽ trống phần cuộc gọi trong lúc tải.
