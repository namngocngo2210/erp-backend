# Landing ingest — bên gửi chốt tiền trong payload

**Ngày:** 2026-08-28
**Nhánh:** `feat/ingest-explicit-amounts`
**Endpoint:** `POST /api/orders/ingest`

## Bài toán

Payload landing ingest chỉ nhận `total_amount`. Không có đường nào gửi giảm giá,
nên `orders.discount_amount` của đơn ingest luôn bằng 0 và
`orders.total_after_discount` luôn bằng tổng tiền — sai với các nguồn đổ đơn đã
tự chốt giá khuyến mãi từ trước.

Kèm theo đó là một lỗ hổng có sẵn: `create_order_from_landing` không truyền
`total_after_discount` lúc `Order.objects.create`, chỉ đặt nó gián tiếp qua
`_sync_order_totals_from_items`. Đơn **không khớp sản phẩm nào** trong catalog
(SKU sai, `product_id` không tồn tại) không đi qua khối đó, nên lưu
`total_after_discount = 0` trong khi `total_amount > 0`. Mọi báo cáo đọc cột này
— `fct_order_enriched`, Customer 360, doanh thu theo sale — đều hụt đúng nhóm đơn
đó.

## Hợp đồng mới

Ba ô tiền, tất cả optional, decimal(12,2), `min_value=0`:

| Field | Trạng thái | Ý nghĩa |
|---|---|---|
| `total_amount` | đã có | Tổng tiền trước giảm giá |
| `discount_amount` | **mới** | Số tiền giảm |
| `total_after_discount` | **mới** | Số tiền sau giảm giá, bên gửi tự chốt |

Không ô nào khai `default` ở serializer: phải phân biệt được *không gửi* (CRM
tính bù) với *gửi số 0* (bên gửi chốt bằng 0). Đây là lý do các ô này khác
`OrderCreateSerializer`, nơi `total_after_discount` khai `default=0` và làm nhánh
"tự tính bù" của `create_order_manual` thành code không bao giờ chạy tới.

Gửi ô nào áp ô đó, thiếu thì tính bù:

| Payload gửi | `total_amount` | `discount_amount` | `total_after_discount` |
|---|---|---|---|
| Đủ 3 | payload | payload | **payload nguyên văn** |
| total + discount | payload | payload | total − discount |
| total + TAD | payload | 0 | payload |
| chỉ TAD | suy ra từ SP/text | 0 | payload |
| không gửi ô nào | suy ra từ SP/text | 0 | = `total_amount` |

**Đủ 3 mà lệch nhau thì tin payload.** `total=1.000.000, discount=300.000,
TAD=900.000` lưu đúng ba số đó, không tính lại thành 700.000. Bên gửi là nguồn
sự thật về tiền của chính họ; CRM sửa số của họ mới là hành vi khó truy hơn.

## Luật từ chối

`400` khi `discount_amount > total_amount` **và** payload không gửi
`total_after_discount`. Có TAD tường minh thì bỏ kiểm tra — bên gửi đã tự chốt số
cuối.

Đây là ngoại lệ duy nhất của endpoint vốn lenient (số điện thoại sai vẫn nhận,
tên sale sai vẫn nhận): 400 ở ingest nghĩa là **mất hẳn lead**, nên chỉ dùng khi
payload tự mâu thuẫn rõ ràng.

**Chỗ 400 không với tới được:** payload không gửi `total_amount`, để hệ thống suy
ra tổng từ text `product` hoặc bảng giá kênh của `product_id`. Lúc validate chưa
có con số nào để so; tổng chỉ có trong task chạy nền, sau khi đã trả 200. Ca đó
`resolve_landing_total_after_discount` kẹp về 0 + `logger.warning`, không để số âm
chảy vào các mart. Muốn chặn cả ca này thì phải chuyển ingest sang tính tiền đồng
bộ trong request — đổi kiến trúc, ngoài phạm vi.

## Thiết kế

### `resolve_landing_total_after_discount`

Hàm module-level trong `order_service_commands.py`, một luật dùng cho cả hai
nhánh (đơn có dòng hàng và đơn không có): có `explicit` → trả nguyên văn; không
có → `total − discount`, âm thì kẹp 0 + log.

### Vì sao phải rẽ nhánh quanh `_sync_order_totals_from_items`

Helper này dùng chung cho nhiều luồng và **luôn** tính lại
`total_after_discount = Σ line_total − discount_amount − booking_voucher_discount`.
Gọi nó khi payload đã chốt TAD sẽ xoá số vừa chốt ngay trong cùng transaction. Nên
khối đồng bộ sau khi tạo `OrderItem` rẽ hai:

- có TAD tường minh → gán thẳng `total_amount` = tổng đã chốt/Σ line_total và
  `total_after_discount` = số payload, **không** gọi helper;
- không có → gọi helper như cũ (helper vốn đọc `order.discount_amount`, nên
  discount tự được trừ, không phải sửa hàm dùng chung), rồi đi qua
  `resolve_landing_total_after_discount` một lần để kẹp âm cho cùng luật.

### `OrderItem` cố ý lệch với `total_amount`

Khi payload ép tiền mà `product_id` vẫn khớp catalog, dòng hàng vẫn được tạo theo
bảng giá kênh, nên `Σ line_total` lệch với `orders.total_amount`. Chấp nhận lệch:
items còn cần cho trừ tồn và nở combo, bỏ chúng đi thì kho không xuất được đơn.

### `landing_confirmed_amount` giữ nguyên nghĩa

Vẫn là tổng **trước** giảm giá (spec 2026-08-04), không trừ `discount_amount`.
Snapshot mốc "đơn đổ về" (`capture_created_snapshot`) đọc `order.total_after_discount`
sau khối đồng bộ nên tự mang số mới; `amount_source` nay tính cả ca chỉ gửi
`total_after_discount` là `"payload"`.

## Rủi ro đã biết

**Số ép sẽ bị xoá nếu đơn được sửa sau này.** Mọi thao tác chạm tới dòng hàng của
đơn (sale sửa đơn trên CRM, thêm/bớt sản phẩm) đều gọi
`_sync_order_totals_from_items` và tính lại toàn bộ từ items — số bên gửi ép biến
mất, thay bằng Σ bảng giá. MR này **không** chặn đường đó: chặn thì sale mất khả
năng sửa tiền. Muốn số ép bất biến thì cần một cột đánh dấu "tiền do nguồn ngoài
chốt" và cho `_sync` tôn trọng cờ đó — phạm vi riêng.

## Ảnh hưởng sau deploy

Doanh thu theo `total_after_discount` sẽ **tăng**: nhóm đơn landing không khớp sản
phẩm trước đây đóng góp 0 nay có số thật. Phải báo trước, kẻo bị đọc thành số
nhảy sai. Không migration, không regen `schema.yaml` (endpoint
`@extend_schema(exclude=True)`).

## Test

`apps/orders/tests/test_ingest_api.py`:

- `LandingOrderSerializerFieldTests` — nhận `discount_amount`/`total_after_discount`;
  400 khi discount vượt tổng mà thiếu TAD; cho qua khi có TAD; từ chối số âm.
- `LandingIngestExplicitAmountsTests` — đủ 3 số lệch nhau lưu nguyên văn và dòng
  hàng vẫn tạo; qua được vòng JSON round-trip của queue; thiếu TAD thì tính bù;
  chỉ gửi TAD; đơn không khớp SP; 400 không tạo đơn và trace `VALIDATION_FAILED`;
  không gửi ô tiền nào thì giữ hành vi cũ.
