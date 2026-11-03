# Thiết kế: Đơn bán trực tiếp "xuất sau" — tách mốc tạo hoá đơn và mốc rời kho

- Ngày: 2026-08-25
- Nhánh: `feat/direct-sale-deferred-dispatch`
- Trạng thái: thiết kế, chưa triển khai
- Tiền đề: [2026-08-12-order-shipping-type-design.md](2026-08-12-order-shipping-type-design.md)

> **CẬP NHẬT 2026-08-28 — nhánh `feat/quick-sale-always-deferred`.** Nhánh "trừ tồn ngay"
> mô tả trong tài liệu này ĐÃ BỊ GỠ. Mọi đơn quick-sale nay đều là đơn "xuất sau": UI chỉ
> còn MỘT nút "Tạo nhanh", tầng API luôn ghi `direct_dispatch_deferred=True`, và worker
> (`_apply_shipping_type_after_job`) chỉ gắn nhãn `shipping_type=direct` chứ không ghi sổ.
> Cột `direct_dispatch_deferred` được GIỮ vì đơn direct tạo trước mốc này mang cờ False và
> đã bị trừ tồn ngay — `_is_direct_sale` vẫn phải nhận ra chúng để giữ nguyên luật huỷ
> ("huỷ là cộng trả tồn ngay") cho riêng nhóm đơn cũ đó. Phần "hai nhánh" bên dưới đọc như
> lịch sử thiết kế, không phải hành vi hiện tại.

## 1. Vấn đề

MR !1223 gắn `Order.shipping_type` và cho đơn `direct` (tạo qua "Tạo nhanh") trừ tồn
NGAY khi hoá đơn KiotViet tạo thành công. Giả định nền: khách lấy hàng tại quầy, hàng
rời kho cùng lúc với hoá đơn.

Thực tế có nhóm đơn phá giả định đó: **hoá đơn lên hôm nay, hôm sau hàng mới rời kho**
(khách hẹn qua lấy, hoặc shipper nội bộ mang đi ngày hôm sau). Nhóm này không có mã vận
đơn nên kho không có gì để quét. Bốn hệ quả:

1. **Sổ kho trừ trước hàng thật ít nhất một ngày.** Kiểm kê cuối ngày D thấy tồn sổ
   thấp hơn tồn vật lý đúng bằng lượng đơn loại này.
2. **Ngày trên Lịch sử nhập xuất là ngày tạo hoá đơn, không phải ngày xuất thật.** Sản
   lượng xuất theo ngày lệch giữa D và D+1.
3. **Đơn đeo sẵn cờ `dispatched` ngay khi tạo** nên không còn khâu xác nhận xuất thật.
   Kho không có danh sách nào cho biết đơn nào còn nằm trên kệ chờ giao.
4. **Nhánh huỷ đơn đang dựa vào một giả định sai.** `_is_direct_sale` cho cộng trả tồn
   NGAY khi huỷ đơn `direct`, với lý do "hàng còn ngay trong kho"
   (`apps/inventory/subscribers.py`). Với đơn đã được shipper nội bộ mang đi rồi mới
   huỷ, hàng đang ở ngoài — tồn sẽ phồng và đơn không xuất hiện ở màn "chờ hàng về".

## 2. Phạm vi

Trong phạm vi:

- Thêm hành động hàng loạt thứ hai trên trang Đơn hàng: "Tạo nhanh (xuất sau)".
- Đơn "xuất sau" tạo hoá đơn KiotViet như thường nhưng **chưa** trừ tồn, **chưa** gắn
  `dispatched`.
- Màn hàng đợi mới trong phần Kho: "Chờ xuất kho" — kho chọn dòng, bấm xác nhận đã xuất,
  hệ thống trừ tồn với `occurred_at` = thời điểm bấm.
- Vá nhánh huỷ đơn để đơn "xuất sau" đã xuất thật đi đúng đường "chờ hàng về".
- Bổ sung số "đang chờ xuất" vào endpoint tồn khả dụng của form đơn.

Ngoài phạm vi:

- **Không** thêm khái niệm giữ chỗ / reservation vào sổ kho. `MovementType` giữ nguyên.
- **Không** cho chọn ngày xuất lùi ở v1 (đã chốt với chủ dự án). Sổ ghi đúng thời điểm
  bấm xác nhận.
- **Không** backfill đơn cũ. Mọi đơn hiện có mang cờ `false` nên hành vi không đổi.
- **Không** đổi luật tồn kho của đơn giao vận và đơn bán quầy giao ngay.

## 3. Các quyết định đã chốt

| Quyết định | Lý do |
|---|---|
| Dời thời điểm ghi sổ, không sửa lùi `occurred_at` | Sửa lùi làm số báo cáo ngày đã chốt tự đổi, và im lặng khi kho quên bấm |
| Không sinh phiếu kho riêng | Sẽ có hai đường ghi sổ song song cho cùng một đơn; mọi luồng huỷ/hoàn/resync đọc `outstanding_by_key` với `reference_type='order'` sẽ mù |
| Cột boolean mới, KHÔNG thêm giá trị vào `ShippingType` | Có nhiều chỗ so `== DIRECT`; thêm enum thứ ba làm chúng mù im lặng |
| Job giữ nguyên `action="create_invoice"` | 4 chỗ đang so `job.action == "create_invoice"`; thêm action mới làm cả 4 bỏ sót đơn xuất sau |
| Người xác nhận là KHO, trên màn riêng | Kho là nơi nhìn thấy hàng thật rời đi |
| Ngưỡng cảnh báo chờ quá lâu: 2 ngày | Hằng số trong settings, không qua env (khỏi đụng docker) |

## 4. Dữ liệu

`apps/orders/models.py` — thêm vào `Order`:

```python
direct_dispatch_deferred = models.BooleanField(default=False)
```

Ý nghĩa: "đơn bán trực tiếp này được tạo với thoả thuận xuất hàng sau". Cờ chỉ có
nghĩa khi đi kèm `shipping_type='direct'`; đứng một mình nó vô hại.

Cờ này **không** trả lời câu hỏi "đã xuất chưa" — câu đó vẫn do sổ và
`delivery_business_status_code` trả lời. Nó chỉ trả lời "đơn này đi theo luật nào".

### Migration

Hai migration tách rời, theo đúng khuôn 0121/0122:

1. `AddField` cột (nhanh, `default=False` nên Postgres 11+ không rewrite bảng).
2. `AddIndexConcurrently` **partial index** cho hàng đợi:

```python
models.Index(
    fields=["-created_at"],
    name="order_direct_deferred_idx",
    condition=Q(direct_dispatch_deferred=True),
)
```

Partial vì tập `true` rất nhỏ so với bảng đơn. Migration index dùng `atomic = False`,
**chạy giờ thấp điểm**; đứt giữa chừng để lại index INVALID (kiểm `pg_index.indisvalid`,
`DROP INDEX CONCURRENTLY` rồi migrate lại).

## 5. Luồng backend

### 5.1 Đặt lệnh (trang Đơn hàng)

`OrderKiotVietActionSerializer` thêm `ACTION_QUICK_SALE_DEFERRED_SELECTED =
"quick_sale_deferred_selected"`, và thêm vào **mọi** danh sách mà
`ACTION_QUICK_SALE_SELECTED` đang có mặt trong file đó (`choices`, nhánh validate
"phải chọn ít nhất 1 đơn").

`order_action_view.py`: gộp hai action vào chung khối guard hiện có của quick-sale —
bắt buộc có kênh KiotViet, bắt buộc có kho xuất, chặn đơn đã có `order_code`. Không
được nới lỏng guard nào.

Trước khi tạo job, ghi cờ **tường minh cả hai chiều**:

```python
Order.objects.filter(id__in=order_ids).update(
    direct_dispatch_deferred=(action == ACTION_QUICK_SALE_DEFERRED_SELECTED)
)
```

Ghi cả chiều `False` là bắt buộc: một đơn từng bấm "xuất sau" mà job hỏng, sau đó bấm
"tạo nhanh" thường, sẽ mang cờ mồ côi và bị hoãn oan.

Job vẫn `action = OrderKiotVietJob.ACTION_CREATE_INVOICE`. Không thêm giá trị enum.

### 5.2 Worker (`_apply_shipping_type_after_job`)

Giữ nguyên toàn bộ guard "đã rời kho qua đường vận chuyển" ở đầu nhánh
`create_invoice`. Sau guard đó mới rẽ:

```python
with transaction.atomic():
    if order.shipping_type != ShippingType.DIRECT:
        order.shipping_type = ShippingType.DIRECT
        order.save(update_fields=["shipping_type", "updated_at"])
    if order.direct_dispatch_deferred:
        return          # chưa rời kho — chờ kho xác nhận
    OrderService.update_order_inline(...)   # nguyên như hôm nay
```

Đọc cờ từ bản ghi `order` tươi mà worker đang giữ.

### 5.3 Xác nhận đã xuất

`apps/inventory/application/services/direct_dispatch_service.py`:

```python
class DirectDispatchService:
    @staticmethod
    def list_pending(*, warehouse_id: int | None = None) -> list[dict]: ...

    @staticmethod
    def confirm(*, order_ids: list[int], user) -> dict: ...
```

`confirm` xử lý **mỗi đơn một transaction** (một đơn hỏng không kéo cả lô), mỗi
transaction:

1. `lock_order_stock(order_id)` — dùng đúng advisory lock có sẵn.
2. Kiểm còn hợp lệ: chưa huỷ, `shipping_type == DIRECT`, `direct_dispatch_deferred`,
   chưa xuất (theo định nghĩa ở §5.4). Không hợp lệ thì bỏ qua và trả lý do.
3. `OrderService.update_order_inline(order, {"crm": {"delivery_business_status_code":
   "dispatched"}}, user, occurred_at=timezone.now())`.

**Điểm bắt buộc, dễ sai:** lời gọi ở worker hiện mang `verdict=CancelVerdict.OK` vì
KiotViet vừa trả hoá đơn sống vài giây trước. Sang nhánh confirm, bằng chứng đó đã cũ
một ngày và hoá đơn có thể đã bị huỷ trên KiotViet trong đêm — nhánh confirm **KHÔNG
truyền `verdict`**, để guard hỏi lại KiotViet như đường quét bình thường.

Trả về `{"confirmed": [...], "skipped": [{"order_id": ..., "reason": ...}]}` — không
im lặng bỏ dòng.

### 5.4 Định nghĩa "đã xuất"

Dùng lại nguyên bản đang có ở `flow_order_lookup`, hai tín hiệu đúng một cái là đủ:

- có outstanding trong sổ (`order_ids_with_outstanding_stock`), **hoặc**
- `OrderAssessment.delivery_business_status_code == DISPATCHED_DELIVERY_CODE`.

Không tự chế công thức thứ ba. Đơn không khớp sản phẩm nào sẽ không sinh bút toán, chỉ
đọc sổ sẽ báo "chưa xuất" vĩnh viễn.

### 5.5 Endpoint

`apps/inventory/urls.py`:

| Method | Path | Quyền |
|---|---|---|
| GET | `direct-dispatch/pending` | `require_section("stock")` |
| POST | `direct-dispatch/confirm` | `stock:edit` |

Bám đúng luật phân quyền đã ghi ở docstring `flow_pending_views.py`: danh sách ĐỌC gate
bằng section `stock`, hành động GHI gate bằng `stock:edit`. **Không đẻ permission mới**
— permission mới lại phải nhớ gán role (đã quên một lần ở `contacts:export`).

GET trả về: `order_id`, `invoice_code`, `warehouse`, `items[]` (sku, tên, SL), người
tạo, `invoiced_at`, `waiting_days`. Số điện thoại/địa chỉ khách **không** trả về (không
cần cho thao tác xuất kho).

## 6. Nhánh huỷ đơn

Sửa đúng một hàm trong `apps/inventory/subscribers.py`:

```python
def _is_direct_sale(order_id: int) -> bool:
    """Đơn bán quầy GIAO NGAY — loại trừ đơn 'xuất sau'."""
    return Order.all_objects.filter(
        pk=order_id,
        shipping_type=ShippingType.DIRECT,
        direct_dispatch_deferred=False,
    ).exists()
```

Giữ nguyên `all_objects` (đơn mirror Foxia) và giữ nguyên `CANCEL_RELEASE_NOTE` ở nhánh
cộng trả — 4 nơi lọc theo đúng note đó.

Đơn "xuất sau" rơi xuống nhánh "chờ hàng về", và nhánh đó **tự đúng ở cả hai trạng
thái** vì nó phán quyết bằng sổ chứ không bằng nhãn:

| Tình huống | Hành vi | Đúng? |
|---|---|---|
| Chờ xuất, chưa xác nhận → huỷ | `open_for_order` gọi `order_goods_still_out` → sổ rỗng → `False`; không mở watch, không bắn Lark; tồn không đụng vì chưa từng trừ | ✅ hàng còn trên kệ |
| Đã xác nhận xuất → huỷ | Giữ outstanding, mở `CancelledDispatchWatch`, bắn Lark cho kho đi đòi hàng | ✅ hàng đang ngoài |
| Đơn quầy giao ngay → huỷ | Nguyên hành vi hôm nay: cộng trả tồn ngay | ✅ không đổi |

Hai ca biên đã soi, **không cần code thêm**:

- **Hồi sinh đơn:** `reissue_after_cancel_release` đọc sổ → không có bút toán hoàn nào
  để trừ lại → no-op; `resync_for_order` tự no-op khi outstanding = 0. Đơn quay lại
  hàng đợi chờ xuất.
- **KiotViet huỷ-xuất lại sinh đơn `.NN`:** `find_live_reissued_sibling` +
  `transfer_issue_to_sibling` chạy trước mọi nhánh và cũng đọc sổ; đơn chưa xuất không
  có gì để chuyển nên tự rơi xuống dưới.

Ca này cũng tự đúng, chỉ cần test khoá lại: **đơn chờ xuất sau đó được đẩy sang giao
vận** (có `order_code`, lên vận đơn) — luồng quét mã hiện tại set `dispatched` +
`issue_for_order`, đơn tự rơi khỏi hàng đợi, tồn trừ đúng một lần. Guard
`shipping_type is not None → không ghi đè` lo phần nhãn.

## 7. Tồn khả dụng trên form đơn

`StockQueryService.availability` hiện trả `{sku, product_name, sellable}` (2 query).
Thêm **query thứ ba** và **field mới** `pending_direct`:

- Tổng SL dòng hàng của các đơn đang chờ xuất tại chính kho đó.
- **Bắt buộc nở combo qua `ComboExpander`**: sổ trừ theo mã lẻ, cộng "đang chờ xuất"
  theo SKU combo sẽ lệch đúng ở nhóm đơn dễ sai nhất (bài học MR !1317/!1326).

**Không** sửa nghĩa của `sellable` — nhiều nơi khác đang đọc con số đó. Form đơn hiển
thị `Tồn 12 (chờ xuất 3)`.

## 8. Frontend

- `use-orders-bulk-actions.ts` + `OrdersTable.tsx`: thêm mục "Tạo nhanh (xuất sau)"
  cạnh "Tạo nhanh" hiện có, dùng chung hộp xác nhận, đổi nhãn cho rõ hậu quả
  ("hoá đơn lên ngay, tồn trừ khi kho xác nhận xuất").
- Trang mới `frontend/src/features/inventory/pages/DirectDispatchPage.tsx` + route
  `inventory/direct-dispatch` trong `routes.tsx` (guard section `stock`), queries ở
  `direct-dispatch-queries.ts`.
- Bảng: chọn nhiều dòng → nút "Xác nhận đã xuất", có hộp xác nhận nêu rõ "thao tác này
  trừ tồn kho". Dòng chờ quá ngưỡng tô cảnh báo.
- Trang phải responsive theo khuôn đã làm ở MR !1251 (bảng lồng trong `td colSpan` là
  gốc lỗi cũ), và test phải chốt viewport bằng `src/test/viewport.ts` — jsdom mặc định
  rơi vào nhánh thẻ mobile.
- Form đơn: hiển thị `pending_direct` cạnh tồn khả dụng.

## 9. Thứ tự deploy

**BẮT BUỘC: migrate TRƯỚC, roll code SAU.**

`_is_direct_sale` chạy trong handler huỷ của **mọi** đơn. Code mới lên trước cột thì
mỗi lần huỷ đơn handler nổ `ProgrammingError` **trước** `AwaitingReturnService.open_for_order`
— đơn huỷ trong cửa sổ đó vĩnh viễn không có dòng "chờ hàng về" và không tự sửa lại
được. Đây đúng là cái bẫy đã ghi lại ở MR !1223, cùng một hàm.

Các bước:

1. Chạy migration `AddField` (nhanh).
2. Chạy migration `AddIndexConcurrently` — **giờ thấp điểm**, kiểm `indisvalid` sau khi
   xong.
3. Roll code backend, rồi frontend.
4. Gán quyền: không có permission mới, nhưng xác nhận role kho đã có `stock:edit`.

Sau deploy, hành vi mặc định **không đổi** với mọi đơn hiện có và mọi đơn tạo nhanh
thường — cờ mới mặc định `false`. Chỉ đơn bấm đúng nút mới mới đi luồng mới.

## 10. Test

Không rải test case lẻ; hai file theo vòng đời, bám cấu trúc test hiện có.

`backend/apps/inventory/tests/test_direct_deferred_dispatch.py` — vòng đời đơn xuất sau:

1. Tạo nhanh (xuất sau) thành công → có `invoice_code`, `shipping_type='direct'`,
   cờ `true`, **chưa** có bút toán SALE, **chưa** `dispatched`.
2. Đơn xuất hiện ở `list_pending`; đơn quầy giao ngay **không** xuất hiện.
3. `confirm` → có bút toán SALE, `occurred_at` là thời điểm bấm (không phải
   `kiotviet_purchase_date`), `dispatched` được set, đơn rời hàng đợi.
4. `confirm` lần hai trên cùng đơn → skip có lý do, **không** trừ tồn lần hai.
5. Huỷ khi đang chờ xuất → tồn không đổi, **không** mở `CancelledDispatchWatch`.
6. Huỷ sau khi đã xác nhận xuất → outstanding giữ nguyên, **có** mở watch.
7. Huỷ đơn quầy giao ngay → cộng trả tồn ngay với `CANCEL_RELEASE_NOTE` (khoá hành vi
   cũ không bị vỡ).
8. Đơn chờ xuất được đẩy sang giao vận rồi kho quét mã → trừ tồn đúng **một** lần, rời
   hàng đợi.
9. Hồi sinh đơn chờ xuất đã huỷ → không trừ tồn oan.
10. `availability` trả `pending_direct` đúng, **có nở combo**.
11. Guard: đơn đã có `order_code` bị chặn ở API; đơn thiếu kho xuất bị chặn ở API.

`frontend/src/features/inventory/__tests__/direct-dispatch.test.tsx` — bảng hàng đợi,
chọn dòng, hộp xác nhận, trạng thái rỗng, dòng quá ngưỡng, quyền thiếu `stock:edit` thì
ẩn nút.

## 11. Rủi ro và nợ chấp nhận

- **Bán trùng trong quãng chờ.** Sổ chưa trừ nên tồn khả dụng vẫn tính hàng đó là còn.
  Giảm bằng `pending_direct` hiển thị trên form đơn — **cảnh báo, không phải khoá**.
  Chấp nhận có chủ đích: thêm reservation vào sổ là thay đổi lớn hơn nhiều lần bài toán
  đang giải.
- **Kho quên bấm xác nhận.** Đơn nằm hàng đợi vô thời hạn, tồn sổ cao hơn thực tế. Hàng
  đợi có cột số ngày chờ + tô cảnh báo. Chưa làm cảnh báo Lark tự động — nợ có chủ đích,
  đợi thấy tần suất thật rồi mới thêm.
- **Không đo được quy mô trước khi làm.** MCP `prod-db` không kết nối được lúc thiết kế
  (`ECONNREFUSED 127.0.0.1:55219`). Sau deploy phải đếm số đơn đi luồng mới mỗi ngày để
  biết thiết kế có đúng tần suất giả định không.
- **`schema.yaml`** phải regen sau khi thêm endpoint; file này đã cũ sẵn ~444 dòng nên
  regen sẽ kéo theo nhiễu của feature khác — chỉ giữ lại phần thuộc MR này.
