# Thiết kế: Trường `shipping_type` — tách đơn giao vận và đơn bán trực tiếp

- Ngày: 2026-08-12
- Nhánh: `feat/order-shipping-type`
- Trạng thái: đã triển khai, chờ merge

## 1. Vấn đề

Hệ thống hiện chỉ có một luồng xuất hàng: đơn được đẩy sang KiotViet, lên vận đơn, kho
quét xuất, tồn trừ khi trạng thái giao vận sang `dispatched`.

Thực tế còn luồng thứ hai: **bán trực tiếp** — khách lấy hàng ngay tại quầy, nhân viên
dùng chức năng "Tạo nhanh (bán nhanh)" để tạo hoá đơn KiotViet. Đơn này không bao giờ
được quét xuất, nên tồn không bao giờ bị trừ. Hai hệ quả:

1. Tồn kho phồng so với thực tế, sai đúng bằng lượng hàng bán tại quầy.
2. Không có cách nào tách hai luồng khi làm báo cáo — mọi đơn trông như nhau.

## 2. Phạm vi

Trong phạm vi:

- Thêm `Order.shipping_type` với 3 trạng thái: `NULL` (chưa xác định), `shipment`
  (đi giao vận), `direct` (bán trực tiếp).
- Hệ thống tự gắn cờ theo hành vi thật, không cho sửa tay.
- Đơn `direct` trừ tồn ngay khi tạo hoá đơn KiotViet thành công, theo kho xuất đã chọn
  lúc tạo đơn.
- Đơn `direct` bị huỷ thì hoàn tồn ngay, không phải chờ quét nhập hoàn.
- Hiển thị cột, bộ lọc và cột trong file xuất Excel.

Ngoài phạm vi:

- Không backfill đơn cũ. Đơn tạo trước khi deploy giữ `NULL` vĩnh viễn.
- Không đổi luật tồn kho của đơn giao vận.
- Không thêm màn hình mới.

## 3. Dữ liệu

`apps/orders/models.py`:

```python
class ShippingType(models.TextChoices):
    """Hình thức xuất hàng của đơn."""

    SHIPMENT = "shipment", "Shipment"  # Gửi vận chuyển
    DIRECT = "direct", "Direct"        # Bán trực tiếp tại quầy
```

```python
shipping_type = models.CharField(
    max_length=16, choices=ShippingType.choices, null=True, blank=True, default=None
)
```

`NULL` là trạng thái hợp lệ và có nghĩa riêng: "chưa biết đơn này đi đường nào". Không
dùng chuỗi rỗng — chuỗi rỗng lẫn với dữ liệu nhập thiếu và không phân biệt được với
đơn cũ.

### Migration

Hai migration tách rời, vì bước 2 không chạy được trong transaction:

1. `AddField` cột nullable. PostgreSQL không rewrite bảng với cột nullable không
   default → an toàn trên bảng `orders` của prod.
2. `AddIndexConcurrently` cho `(shipping_type, created_at DESC)`, file đặt
   `atomic = False`. Bộ lọc luôn đi kèm khoảng thời gian nên index ghép đúng hình dạng
   truy vấn của danh sách đơn.

Vẫn chạy vào giờ thấp điểm: `CREATE INDEX CONCURRENTLY` quét toàn bảng.

## 4. Ai gắn cờ

Hệ thống tự gắn, người dùng không sửa được. `shipping_type` không nằm trong
`OrderInlineUpdateSerializer` và không nằm trong `OrderCreateSerializer`; nó chỉ
xuất hiện ở serializer đọc.

| Mốc nghiệp vụ | Giá trị |
|---|---|
| "Tạo nhanh (bán nhanh)" tạo hoá đơn KV thành công (`job.action == "create_invoice"`, có `invoice_code`) | `direct` |
| Push đơn sang KiotViet thành công (`job.action == "push"`, có `order_code`) | `shipment` nếu đang `NULL` |
| Tạo vận đơn KShip thành công (`job.action == "create_kship_delivery"`, có `shipping_code`) | `shipment` nếu đang `NULL` |

Cả ba mốc nằm cùng một chỗ: khối hậu xử lý job trong
`apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py`, ngay cạnh
đoạn tự lật `sales_status` đã có sẵn — nó đang tính đúng ba điều kiện này. Đặt ở đây
thay vì rải trong `apps/kiotviet/.../service_actions.py` được ba thứ: logic bán trực
tiếp nằm gọn trong app `orders` (không phải import ngược từ `kiotviet` sang service của
`orders`), chạy ngoài vòng gọi HTTP KiotViet, và có sẵn `job.requested_by_id` làm actor.

Hệ quả có chủ đích: cờ chỉ được gắn khi đi qua job. Đơn được đồng bộ ngầm từ webhook
hoặc cron sẽ giữ `NULL` cho tới khi có một thao tác thật của người dùng.

**Không ghi đè giá trị đã có.** Đơn đã `direct` (đã trừ tồn) mà sau đó có ai push lại
sang KiotViet thì cờ giữ nguyên `direct`. Ghi đè sẽ tạo ra đơn mang nhãn `shipment`
nhưng tồn đã trừ theo luật `direct` — nhãn nói dối sổ.

**Chiều ngược lại cũng bị chặn.** Đơn đã rời kho qua đường vận chuyển mà lọt được tới
job `create_invoice` — nhân viên bấm quick-sale ngay trước khi guard API kịp chặn, hoặc
job đã nằm hàng đợi từ trước lúc đơn được push — thì worker KHÔNG lật cờ sang `direct`
và KHÔNG trừ tồn: giữ nguyên giá trị hiện tại, chỉ log warning. Lật cờ ở ca này rồi đơn
bị huỷ sẽ khiến subscriber cộng trả tồn NGAY theo luật `direct` trong khi hàng thật đang
ở đơn vị vận chuyển, chưa hề về kho — tồn ảo. Đây là phòng thủ chiều sâu cho guard API
mô tả ở "Guard 'đơn đã đẩy đi vận chuyển'" (§5).

**Sửa ở đợt review cuối (fix round 2): guard này ban đầu chỉ kiểm `shipping_type ==
shipment`, nhưng MỌI đơn tạo trước khi tính năng này deploy đều mang cờ `NULL` — nên
kiểm mỗi cờ thì lớp phòng thủ chiều sâu này không chặn được BẤT KỲ đơn nào trong dữ liệu
hiện có. Điều kiện thoát sớm giờ đọc SỰ THẬT tươi thay vì chỉ cờ: `shipping_type ==
shipment` HOẶC `order_code` khác rỗng HOẶC `shipping_code` khác rỗng. Đơn đã có
`order_code` (đã push sang KiotViet) hoặc `shipping_code` (đã lên vận đơn KShip) là đã
có bằng chứng rời kho qua đường vận chuyển, bất kể cờ đang là gì.

Lý do chọn "chỉ hệ thống tự set": cờ này quyết định thời điểm trừ tồn và luật hoàn tồn.
Cho sửa tay nghĩa là cho phép đổi luật kho của một đơn đã ghi sổ.

## 5. Trừ tồn cho đơn `direct`

### Cơ chế

Trong khối hậu xử lý job (`ingest_job_tasks.py`), khi job `create_invoice` thành công và
đơn đã có `invoice_code`:

1. Gán `order.shipping_type = ShippingType.DIRECT`.
2. Gọi `OrderServiceCommands.update_order_inline(order, {"crm": {"delivery_business_status_code": "dispatched"}}, actor, verdict=CancelVerdict.OK, occurred_at=<ngày mua trên hoá đơn KV>)`.

`verdict=CancelVerdict.OK` bỏ được một round-trip HTTP hỏi lại KiotViet "đơn này có bị
huỷ không" — KiotViet vừa trả về một hoá đơn sống cho chính đơn này vài giây trước, đó
là bằng chứng dương. Lớp kiểm tra DB tươi (`_is_cancelled_in_db`, có `select_for_update`)
vẫn chạy nguyên vẹn, nên webhook huỷ rơi vào đúng khe đó vẫn chặn được.

Bước 2 tái dùng nguyên đoạn trừ tồn đã có (`order_service_commands.py`, nhánh
`delivery_business_status_code == "dispatched"`): nó gọi `issue_for_order` theo
`order.fulfillment_warehouse_id`, đã có guard đơn huỷ (`_assert_dispatch_allowed`),
đã idempotent, và đã ghi sổ `StockMovement` nên màn "Lịch sử nhập xuất" thấy được đơn
bán trực tiếp như mọi đơn khác.

`occurred_at` lấy `kiotviet_purchase_date` của hoá đơn (đã được parse sẵn ở ngay trên
trong cùng nhánh), fallback `timezone.now()`. Ngày ghi sổ phải là ngày bán thật, không
phải ngày worker chạy.

`actor` là `OrderKiotVietJob.requested_by` — người bấm "Tạo nhanh". Bút toán kho phải
truy được về người thao tác, không để `None` như tác vụ nền vô danh.

Hai phương án bị loại:

- **Subscriber nghe `OrderStatusChanged → DELIVERED_SUCCESS`**: trạng thái 3 cũng đến
  từ luồng sync trạng thái KiotViet của đơn giao vận → sẽ trừ tồn nhầm hàng loạt đơn ship.
- **Viết hàm trừ tồn riêng cho `direct`**: phải nhân bản guard huỷ đơn, guard trừ đôi và
  phần ghi sổ. Nhánh trừ tồn thứ hai là nợ kỹ thuật, không đáng.

**Tính nguyên tử + không làm hỏng hoá đơn đã tạo thật.** Gán cờ (bước 1) và trừ tồn
(bước 2) chạy trong CÙNG MỘT `transaction.atomic()`: nếu bước 2 nổ (đơn bị huỷ chen
ngang, thiếu kho, deadlock...), cờ `shipping_type = direct` phải rollback theo — nhãn
`direct` mà không có `StockMovement` đi kèm là đúng thứ "nhãn nói dối sổ" đang cố tránh.
Toàn bộ khối này lại được bọc trong `try/except` riêng ở nơi gọi
(`process_kiotviet_job`): tại thời điểm nó chạy, hoá đơn KiotViet (`invoice_code`) đã
tạo THẬT trên KiotViet và job đã commit `SUCCESS` ở khối transaction trước đó — một lỗi
ở đây không được phép đè job thành `FAILED`, nếu không UI báo "tạo hoá đơn thất bại"
trong khi hoá đơn đang sống, nhân viên bấm lại → hoá đơn TRÙNG trên KiotViet. Lỗi chỉ
được log kèm cảnh báo "STOCK CHƯA ĐƯỢC TRỪ, cần xử lý tay", job vẫn giữ `SUCCESS`.

### Guard "đơn thiếu kho xuất" — 3 lớp

Đơn `direct` không có kho xuất thì **không được tạo hoá đơn KiotViet**. Hoá đơn đã tạo
mà tồn không trừ là ca tệ nhất: nghiệp vụ coi như xong, kho sai âm thầm.

1. **FE** — nút "Tạo nhanh (bán nhanh)": kiểm tra các đơn đã chọn, đơn nào thiếu
   `fulfillment_warehouse` thì toast liệt kê và không gọi API.
2. **API** `run_kiotviet_action`, action `quick_sale_selected`: trả 400 kèm danh sách đơn
   thiếu kho, không tạo job. Cùng khuôn với check "Đơn hàng chưa được gán kênh KiotViet"
   đã có sẵn ngay cạnh.
3. **Worker** — trong `process_kiotviet_job`, ngay sau khi load `Order` và **trước**
   `_execute_kiotviet_action`: job `create_invoice` mà đơn thiếu kho thì raise, job vào
   FAILED với thông báo rõ và chưa hề gọi API KiotViet. Task không cấu hình autoretry
   nên lỗi này không lặp vô hạn. Lớp này bắt ca kho bị gỡ trong lúc job nằm hàng đợi;
   không có nó thì hai lớp trên chỉ là kiểm tra tại thời điểm bấm nút.

Việc chọn kho lúc tạo đơn đã hoạt động từ MR !1221 (`fulfillment_warehouse` trước đó bị
`OrderCreateSerializer` nuốt im lặng).

### Guard "đơn đã đẩy đi vận chuyển" — 2 lớp

Đơn đã có `order_code` (đã push sang KiotViet hoặc lên đơn KShip) không được đi qua
đường quick-sale: hàng đang trên đường vận chuyển, chưa hề nằm trong kho để "bán tại
quầy".

1. **API** `run_kiotviet_action`, action `quick_sale_selected`: đơn nào đã có
   `order_code` thì trả 400 kèm danh sách (`orders_already_pushed`), không tạo job.
   Cùng khuôn với guard "thiếu kho xuất" ngay cạnh. Chỉ kiểm `order_code` — KHÔNG kiểm
   thêm `shipping_code` ở lớp này (quyết định nghiệp vụ hoãn sang MR sau).
2. **Worker** — trong `_apply_shipping_type_after_job`: nếu vẫn lọt tới đây với đơn đã
   `shipping_type == shipment` HOẶC đã có `order_code` HOẶC đã có `shipping_code`, không
   lật cờ, không trừ tồn (xem §4 "Chiều ngược lại cũng bị chặn" — sửa ở fix round 2 để
   đọc sự thật tươi thay vì chỉ cờ, vì mọi đơn trước deploy đều mang cờ `NULL`).

**Chỉ 2 lớp, KHÔNG có FE precheck** — khác với guard "thiếu kho xuất" (3 lớp). Lý do:
`order_code` không có mặt trong payload `OrderListSerializer`
(`LIST_EXCLUDED_ORDER_FIELDS`), nên `OrdersTable` không có dữ liệu để tiền kiểm. Có một
proxy rẻ trong payload list — `shipping_type === "shipment"` — nhưng KHÔNG chính xác
100%: đơn tạo qua import XLSX có thể đã mang `order_code` (đẩy vận chuyển) trong khi
`shipping_type` vẫn `null` (chưa từng chạy qua luồng gắn cờ ở §4), dùng proxy sẽ lọt
lưới đúng ca cần chặn. Chấp nhận: nhân viên biết đơn bị chặn SAU khi bấm (qua toast hiển
thị nguyên văn `detail` lỗi từ response), không phải TRƯỚC — backend vẫn chặn cứng bằng
dữ liệu thật.

## 6. Huỷ đơn `direct` — hoàn tồn ngay

`apps/inventory/subscribers.py::_on_order_status_changed`, nhánh đơn chuyển sang huỷ:

- Đơn `shipping_type == direct` → gọi `StockSalesService.release_for_order()` cộng trả
  tồn ngay, **không** mở `AwaitingReturnService` watch.
- Mọi đơn còn lại → giữ nguyên luật hiện hành (giữ outstanding, chờ kho quét nhập hoàn).

Lý do lệch luật: hàng bán tại quầy nếu huỷ thì đang nằm ngay trong kho, không có khâu
"hàng đang trên đường về" để chờ. Bắt kho quét nhập hoàn cho đơn quầy là tạo ra một
hàng đợi không bao giờ có ai xử lý.

Thứ tự trong handler: nhánh chuyển sổ sang đơn xuất lại (`find_live_reissued_sibling`)
vẫn chạy trước, không đổi.

**Bút toán hoàn tồn PHẢI mang đúng `CANCEL_RELEASE_NOTE`** — bản triển khai đầu tiên tự
chế chuỗi note riêng (`"Hoàn tồn do huỷ đơn bán trực tiếp"`), và đó là SAI, đã sửa lại.
Bốn nơi lọc đúng theo hằng số này để nhận ra "đây là bút toán hoàn tồn do huỷ":
`pending_cancel_release_by_key` — công thức DÙNG CHUNG bởi ba chỗ đọc: chính nhánh hồi
sinh của handler này (`_on_order_status_changed`), `reissue_after_cancel_release`, và
`reclaim_auto_release` khi hàng về kho; `RevivedStockAuditService` (lưới hứng chạy đêm);
và `dispatch_history_service._POST_DISPATCH_REVERSAL_NOTES` (tính sản lượng xuất ở màn
Lịch sử nhập xuất). Note lạ thì cả bốn chỗ đều mù — đơn `direct` hồi sinh sẽ KHÔNG được
trừ lại tồn mà không ai biết, tới khi kiểm kê mới lộ ra tồn phồng vĩnh viễn (bug Critical
bắt được ở vòng review 1: `625/625 passed` trước đó KHÔNG bắt được vì test chỉ assert
có/không `CancelledDispatchWatch`, không đối chiếu số dư thật trước/sau vòng
huỷ → hồi sinh; test hồi quy `test_direct_order_revival_re_deducts_stock_to_exact_balance`
đã thêm để khoá lại phát hiện này).

**Vì sao dùng lại hằng số cũ thay vì tạo hằng số riêng cho `direct`:** đúng bản chất
nghiệp vụ, "huỷ đơn bán trực tiếp" và "huỷ đơn giao vận sau khi đã xuất kho" là CÙNG MỘT
loại bút toán — hoàn tồn do huỷ một đơn đã rời kho — chỉ khác thời điểm trong vòng đời
đơn. Tạo hằng số riêng nghĩa là nhân đôi công thức lọc ở bốn nơi trên, đúng thứ mà
docstring của `pending_cancel_release_by_key` cấm thẳng: "hai công thức song song sẽ
lệch nhau và trừ tồn hai lần".

**Đánh đổi chấp nhận:** vì dùng chung note với đơn giao vận huỷ-sau-khi-xuất, đơn
`direct` bị huỷ sẽ được `dispatch_history_service` tính là "đã có một lần rời kho"
trong màn Lịch sử nhập xuất — giống hệt cách tính một đơn `shipment` huỷ sau khi xuất
(bút toán đảo mang `CANCEL_RELEASE_NOTE` KHÔNG bị trừ khỏi sản lượng xuất, vì hàng đã
đi thật). Với bán tại quầy đó là cách hiểu ĐÚNG (hàng đã thật sự rời kho một lần, dù chỉ
ra tới quầy tính tiền rồi bị huỷ ngay) — nhưng cần biết trước: sản lượng xuất trong báo
cáo không tự động trừ đơn `direct` bị huỷ ra, dù tồn kho đã được hoàn lại đủ.

Với điều kiện note đúng ở trên, nhánh hồi sinh đơn huỷ KHÔNG cần thêm code riêng —
`reissue_after_cancel_release` đọc thẳng sổ qua `pending_cancel_release_by_key` nên tự
thấy đúng bút toán mà nhánh huỷ đơn `direct` vừa ghi và trừ lại đúng. Đây chính là điều
kiện cần mà bản thiết kế đầu tiên của mục này bỏ sót: không phải "không phải sửa gì cả",
mà là "không phải sửa `reissue_after_cancel_release`, NHƯNG bút toán hoàn tồn phải ghi
đúng note để nó nhìn thấy".

**Phụ lục — phạm vi đọc cờ:** `_is_direct_sale` (dùng để rẽ nhánh ở trên) đọc qua
`Order.all_objects` chứ không phải `Order.objects` (`InternalOrderManager`, tự lọc bỏ
đơn mirror Foxia). Đây là đọc thẳng một cờ theo PK, không phải liệt kê đơn nội bộ — lọc
phạm vi ở đây chỉ khiến đơn mirror bị coi nhầm "không phải direct" và tồn của nó rơi
nhầm vào nhánh chờ hàng về.

## 7. Giao diện

- **Cột "Loại xuất hàng"**: badge `Giao vận` / `Bán trực tiếp` / `—` khi `NULL`. Bật tắt
  qua Column Picker, mặc định ẩn để không đụng bố cục bảng hiện tại.
- **Bộ lọc `shipping_type`**: Tất cả / Giao vận / Bán trực tiếp / Chưa xác định. Tham số
  API nhận `shipment`, `direct`, `none` (`none` = `shipping_type IS NULL`).
- **Xuất Excel**: thêm cột vào định nghĩa export. Lưu ý bộ lọc kho có hai nhánh parse
  riêng (màn hình và job export) — phải sửa cả hai, không thì file Excel lệch so với
  bảng đang xem.

Đơn `direct` sau khi tạo hoá đơn sẽ hiện trạng thái giao vận "Đã xuất kho". Đây là chủ
đích: nó phản ánh đúng "hàng đã rời kho" và giữ màn kho nhất quán.

**Tiền kiểm "thiếu kho" ở nút "Tạo nhanh" chỉ phủ trang đang xem.** `findOrdersWithoutWarehouse`
đọc `rows` (đơn của TRANG HIỆN TẠI) đối chiếu với `selectedIds` (cố ý không reset khi đổi
trang). Chọn đơn thiếu kho ở trang 1 rồi sang trang 2 bấm "Tạo nhanh" → đơn đó lọt qua
tiền kiểm, request vẫn gửi lên backend. Chấp nhận vì ca cùng trang (phổ biến nhất) được
chặn tức thì, còn ca xuyên trang vẫn bị backend chặn cứng (400) và toast hiển thị nguyên
văn `detail` lỗi — nhân viên biết SAU thay vì TRƯỚC.

## 8. Kiểm thử

Một file backend theo vòng đời đơn bán trực tiếp, không rải test lẻ theo từng hàm:

1. Quick-sale thành công → `shipping_type = direct`, tồn trừ đúng kho đã chọn, sổ
   `StockMovement` ghi đúng ngày mua trên hoá đơn.
2. Quick-sale trên đơn thiếu kho xuất → API trả 400, không tạo job; worker cũng chặn khi
   kho bị gỡ sau lúc enqueue; **không** có lời gọi tạo hoá đơn KiotViet nào. Cùng khuôn
   cho đơn đã đẩy đi vận chuyển (`order_code` có sẵn): API trả 400
   (`orders_already_pushed`), không tạo job.
3. Đơn `direct` bị huỷ → tồn cộng trả ngay, không sinh dòng "đơn huỷ chờ hàng về".
4. Đơn `direct` bị huỷ rồi hồi sinh → SỐ DƯ TỒN THẬT quay lại đúng mức ngay sau lúc bán,
   không hơn không kém (không chỉ assert có/không `CancelledDispatchWatch` — cách assert
   yếu này từng để lọt bug Critical vì bút toán hoàn tồn mang note sai khiến
   `pending_cancel_release_by_key` không thấy, xem §6).
5. Push đơn thường → `shipment`; push lại đơn đã `direct` → giữ nguyên `direct`. Chiều
   ngược: `create_invoice` lọt tới worker trên đơn đã `shipment` (có `order_code`) →
   KHÔNG lật cờ, KHÔNG trừ tồn, giữ nguyên `shipment`.
6. Đơn ship huỷ sau khi đã xuất → vẫn vào hàng đợi chờ hàng về (chống hồi quy luật cũ).
7. Lỗi ở bước hậu xử lý (gán cờ + trừ tồn) sau khi hoá đơn KiotViet đã tạo thật (đơn bị
   huỷ chen ngang, deadlock...) → job vẫn giữ `SUCCESS`, không bị đè thành `FAILED` (tránh
   nhân viên bấm lại tạo hoá đơn trùng trên KiotViet).

Frontend: test cột badge render đúng 3 trạng thái, bộ lọc gửi đúng tham số, và tiền kiểm
thiếu kho ở nút "Tạo nhanh" (kể cả ca đơn được chọn nằm ngoài trang hiện tại — xem §7).

## 9. Rủi ro

| Rủi ro | Cách xử lý |
|---|---|
| `CREATE INDEX CONCURRENTLY` khoá/chậm trên bảng `orders` lớn | Migration riêng `atomic=False`, chạy giờ thấp điểm |
| Worker trừ tồn xong nhưng ghi cờ thất bại (hoặc ngược lại) | Gán cờ và gọi trừ tồn trong cùng một `transaction.atomic()` |
| Nhân viên bán trực tiếp quên chọn kho → bị chặn giữa lúc bán | Chặn ngay ở FE trước khi gọi API, thông báo rõ đơn nào thiếu kho |
| Đơn `direct` bị quét xuất lần nữa ở màn kho | `issue_for_order` idempotent, no-op khi outstanding > 0 |
| Bút toán hoàn tồn của đơn `direct` mang note tự chế → 4 nơi đọc sổ (hồi sinh, reclaim, audit đêm, Lịch sử nhập xuất) không thấy, tồn phồng vĩnh viễn | PHẢI dùng đúng `CANCEL_RELEASE_NOTE` sẵn có, không tạo hằng số riêng (xem §6) |
| Quick-sale nhắm nhầm đơn đã đẩy đi vận chuyển (`order_code` có sẵn) → lật nhãn + ép "dispatched" trước khi hàng thật rời kho | Chặn ở API (400) và ở worker (phòng thủ chiều sâu); FE cố ý không tiền kiểm ca này (§5, §7) |
| Job hậu xử lý (gán cờ + trừ tồn) lỗi SAU KHI hoá đơn KiotViet đã tạo thật | Bọc try/except riêng, job giữ `SUCCESS`, lỗi log + bắn cảnh báo Lark (`send_stock_negative_alert`) kèm order_id/invoice_code để xử lý tay — tránh hoá đơn trùng khi nhân viên bấm lại (fix round 2: trước đó chỉ log, không có lưới hứng nào khác bắt được ca này) |
