# Thiết kế: Gỡ hàng đợi "Chờ xuất kho", thay bằng hai cửa xuất kho mới

- Ngày: 2026-08-29
- Nhánh: `feat/warehouse-dispatch-rework`
- Trạng thái: thiết kế đã duyệt, chưa triển khai
- Tiền đề: [2026-08-25-direct-sale-deferred-dispatch-design.md](2026-08-25-direct-sale-deferred-dispatch-design.md)
  (tài liệu đó mô tả chính hàng đợi mà MR này gỡ bỏ)

## 1. Bối cảnh

Hàng đợi "Chờ xuất kho" mới lên prod ngày 28/08 (MR !1386, commit `08639f47`). Ý tưởng:
đơn bán trực tiếp lên hoá đơn KiotViet trước, kho vào một màn riêng bấm xác nhận thì mới
trừ tồn. Thực tế vận hành cho thấy màn riêng đó là một bước thừa: kho đã có sẵn thao tác
quét mã ở màn quét xuất, và người cần bấm xuất một đơn lẻ thì đang đứng ở chính màn chi
tiết đơn. Bắt họ đi sang một màn thứ ba chỉ để tìm lại đúng đơn đó là ma sát không đổi
lấy được gì.

Bốn thay đổi trong MR này là một câu chuyện liền mạch, không phải bốn việc rời:

1. Gỡ hàng đợi "Chờ xuất kho" và toàn bộ tính năng quanh nó.
2. Đơn "Tạo nhanh" trên KiotViet chỉ lên hoá đơn, không làm gì thêm.
3. Vì (1) + (2), đơn bán trực tiếp mất chỗ trừ tồn → **cửa thứ nhất**: màn quét xuất của
   kho khớp thêm theo **mã hoá đơn**, không chỉ mã vận đơn.
4. **Cửa thứ hai**: nút "Xuất kho" ngay trên chi tiết đơn, popup chọn kho xuất.

## 2. Phạm vi

Trong phạm vi:

- Xoá service/endpoint/trang/route/test của hàng đợi "Chờ xuất kho".
- Màn quét XUẤT khớp đơn theo `invoice_code` bên cạnh `shipping_code`.
- Endpoint + service xuất kho tay theo kho chỉ định, và nút gọi nó ở chi tiết đơn.
- Viết lại bộ test vòng đời cho luật mới.

Ngoài phạm vi:

- **Không** đụng luồng quét HOÀN (`kind=RETURN`). Lý do ở §5.3.
- **Không** đổi luật trừ tồn của đơn giao vận đi qua mã vận đơn.
- **Không** migration, **không** backfill, **không** đụng dữ liệu đơn đang tồn đọng.
- **Không** đổi guard "quick-sale bắt buộc có kho xuất" — chính kho đó nuôi §5.2.

## 3. Các quyết định đã chốt

| Quyết định | Lý do |
|---|---|
| Giữ cột `direct_dispatch_deferred` và giữ việc ghi cờ `=True` | Là thứ DUY NHẤT phân biệt hai lớp đơn `direct` trong DB — xem §4, đây là bẫy lớn nhất của MR |
| Đơn quick-sale vẫn gắn nhãn `shipping_type='direct'` | Báo cáo và bộ lọc trang đơn đang đọc nhãn này để nhận ra đơn bán quầy |
| Ưu tiên `shipping_code` tuyệt đối khi khớp | Không một đơn giao vận nào được đổi hành vi vì tính năng mới |
| Khớp qua `invoice_code` thì trừ ở kho CỦA ĐƠN | Kho xuất do sale chọn lúc tạo đơn là chủ đích nghiệp vụ; người quét có thể đang để kho mặc định |
| Chỉ áp mã hoá đơn cho quét XUẤT | Đường quét HOÀN ghi `scan.code` thẳng vào `ReturnEntry.shipping_code` ở 4 chỗ |
| Nút xuất tay GHI ĐÈ `fulfillment_warehouse` của đơn | Sổ kho và đơn phải luôn nói cùng một chuyện, khỏi phải đối chiếu về sau |
| Đổi tên `DirectDispatchService` → `ManualDispatchService`, không xoá đi viết lại | Giữ nguyên khối xử lý verdict KiotViet đã tôi luyện (§6.2) |
| Đơn đang tồn đọng trong hàng đợi: để nguyên | Chúng và đơn mới cùng một luật, không có gì để backfill |

## 4. Cột `direct_dispatch_deferred` — vì sao phải giữ

Sau MR này, cột này không còn nuôi màn hình nào. Trực giác đầu tiên là xoá nó đi. **Đừng.**

Trong bảng `orders` đang có hai lớp đơn `shipping_type='direct'` với hai luật huỷ khác nhau:

| Lớp đơn | Cờ | Đã trừ tồn lúc lên hoá đơn? | Huỷ đơn thì làm gì |
|---|---|---|---|
| Tạo trước 28/08/2026 | `False` | Có | Cộng trả tồn NGAY (`CANCEL_RELEASE_NOTE`) |
| Từ 28/08/2026 trở đi | `True` | Không | Đi đường "chờ hàng về", phán quyết bằng sổ |

`apps/inventory/subscribers._is_direct_sale` lọc đúng `direct_dispatch_deferred=False` để
tách hai lớp đó. Ngừng ghi cờ `=True` cho đơn mới, hoặc xoá cột, thì mọi đơn mới rơi vào
nhánh lớp cũ và được **cộng trả tồn cho lượng hàng chưa bao giờ bị trừ** — tồn phồng, im
lặng, không lưới nào hứng.

Vì vậy: giữ nguyên cột, giữ nguyên `order_action_view` ghi cờ, giữ nguyên `_is_direct_sale`
không sửa một dòng. Chỉ viết lại docstring — nghĩa mới của cờ là **"đơn bán trực tiếp
không trừ tồn tại thời điểm lên hoá đơn"**, vẫn đúng với tên cột.

Hệ quả tốt: đơn đang nằm trong hàng đợi trên prod (cờ `True`, chưa trừ tồn) và đơn tạo
sau MR này là **cùng một lớp**. Không cần phân biệt, không cần backfill, không cần lệnh
dọn. Kho xử lý chúng bằng đúng hai cửa mới như mọi đơn khác.

## 5. Cửa thứ nhất — quét mã hoá đơn

### 5.1 Chỗ sửa

Đúng một chỗ dò đơn, `pending_scan_service._process_one` (dòng ~606):

```python
orders = list(
    Order.objects.filter(
        Q(shipping_code=scan.code) | Q(invoice_code=scan.code),
        deleted_at__isnull=True,
    )
    .select_related("kiotviet_channel", "fulfillment_warehouse")
    .prefetch_related("items")
    .annotate(_by_shipping=Case(When(shipping_code=scan.code, then=1), default=0))
    .order_by("-_by_shipping", "-id")[:2]
)
```

Khớp CHÍNH XÁC, không `ilike`/`contains`: hoá đơn phát hành lại mang mã `HD001.01` phải
không bao giờ bị mã `HD001` chạm tới.

Không cần migration: `order_invoice_code_idx` (partial index trên `invoice_code`) đã có
sẵn từ migration 0057.

### 5.2 Kho trừ tồn tách theo đường khớp

`_complete_dispatch` hiện luôn truyền `warehouse_id=scan.warehouse_id` (kho chọn trên màn
quét). Giữ nguyên cho đường `shipping_code`. Với đường `invoice_code`, truyền
`order.fulfillment_warehouse_id`.

Đơn khớp qua `invoice_code` mà **không có** kho xuất → scan chuyển `FAILED` với lý do
"Đơn thiếu kho xuất — bổ sung kho trên đơn rồi quét lại". Tuyệt đối **không** âm thầm rơi
về `scan.warehouse_id`: trừ nhầm kho là sai số im lặng, còn `FAILED` thì kho nhìn thấy.

Rủi ro chấp nhận có chủ đích: nếu hàng thật xuất từ kho khác kho sale đã chọn trên đơn, sổ
ghi theo đơn chứ không theo người quét. Đây là quyết định nghiệp vụ của chủ dự án; lối
thoát là sửa kho trên đơn, hoặc dùng nút xuất tay ở §6 (nút đó cho chọn kho tại chỗ).

### 5.3 Vì sao không áp cho quét HOÀN

Đường `_complete_return` ghi `scan.code` thẳng vào `ReturnEntry.shipping_code`, và có 4 chỗ
khác đọc lại `ReturnEntry.objects.filter(shipping_code=...)` để chống ghi nhận trùng. Cho
mã hoá đơn đi vào cột mã vận đơn sẽ phá đối chiếu phiếu hoàn theo một cách chỉ lộ ra ở
tháng sau. Nếu sau này thật sự cần, đó là một MR riêng có cột riêng.

### 5.4 Không đụng khớp tay

`flow_order_lookup` (panel khớp tay) **đã** tìm theo `invoice_code` / `order_code` /
`shipping_code` từ trước. Không sửa gì ở đó.

**Bổ sung 03/09/2026 — luật kho của `manual_match`.** Bản vá review 29/08 (`36f78f4d`) cho
khớp tay đi qua cùng helper chọn kho với cron, nhưng suy "mã quét ≠ `shipping_code`" =
"đường mã hoá đơn". Sai với đơn sàn giao **hoả tốc** (Shopee → Grab/Ahamove): tem in **mã
đơn sàn** (`260901TYHC0T2V`), đơn lưu `shipping_code` = mã Grab (`205544542`) và
`invoice_code` = `HDSPE_260901TYHC0T2V` — mã quét không bằng mã nào, cron không tự khớp, kho
khớp tay; đơn sàn lại không có `fulfillment_warehouse` → FAILED "Đơn thiếu kho xuất" (prod
01/09, scan #1508/#1527/#1532). Luật đúng: chỉ khi mã quét **đúng bằng** `invoice_code` mới
trừ ở kho của đơn; mọi đường khác (mã vận đơn, mã đơn sàn) trừ ở kho màn quét như trước.

## 6. Cửa thứ hai — nút "Xuất kho" ở chi tiết đơn

### 6.1 Phạm vi áp dụng

Nút hiện với **mọi đơn chưa xuất**, kể cả đơn giao vận chưa quét mã. "Chưa xuất" dùng lại
đúng định nghĩa 2 tín hiệu đang có (`_dispatched_order_ids`): có outstanding trong sổ,
**hoặc** `OrderAssessment.delivery_business_status_code == DISPATCHED_DELIVERY_CODE`.

Kho chọn trong popup được **ghi vào `order.fulfillment_warehouse_id`** trước khi ghi sổ.

### 6.2 Service

Đổi tên `direct_dispatch_service.py` → `manual_dispatch_service.py`,
`DirectDispatchService` → `ManualDispatchService`. Bỏ `list_pending`. `confirm` →
`dispatch(*, order_ids, warehouse_id, actor)`.

Giữ nguyên toàn bộ khối xử lý verdict KiotViet — đây là lý do đổi tên thay vì viết lại:

- `_prefetch_dispatch_verdicts` chạy **NGOÀI** transaction, ngay trước vòng ghi sổ (bằng
  chứng phải tươi: hoá đơn có thể đã bị huỷ trên KiotViet trong đêm).
- Verdict là `Exception` → **skip ngay**, không truyền xuống `update_order_inline`. Hàm đó
  chạy trong `@transaction.atomic` và đang giữ row lock khi tới guard; verdict lạ khiến
  guard tự đi hỏi lại KiotViet — HTTP TRONG transaction, đúng thứ tham số `verdict` sinh ra
  để tránh (dạng sự cố 2026-07-31).
- Mỗi đơn một lời gọi độc lập; một đơn hỏng không kéo cả lô. Trả `{confirmed, skipped}` kèm
  lý do, không im lặng bỏ dòng.
- **KHÔNG** truyền `occurred_at`: mặc định `now()` chính là mốc ta cần.

Bỏ hai guard cũ `shipping_type == DIRECT` và `direct_dispatch_deferred` (nút nay dùng cho
mọi đơn). Giữ guard huỷ, guard đã-xuất, guard `deleted_at`.

Thêm guard `warehouse_id`: phải là kho tồn tại và đang hoạt động, kiểm ở serializer.

Đơn chưa từng đẩy sang KiotViet (không có `invoice_code` hoặc không có kênh) → verifier trả
`UNVERIFIABLE`, DB là nguồn sự thật → vẫn xuất được, không kẹt.

### 6.3 Endpoint

| Method | Path | Quyền |
|---|---|---|
| POST | `/api/inventory/orders/dispatch` | `stock:edit` |

Body `{"order_ids": [int], "warehouse_id": int}`. Không đẻ permission mới — permission mới
lại phải nhớ gán role.

### 6.4 Frontend

- `frontend/src/features/inventory/manual-dispatch-queries.ts` thay cho
  `direct-dispatch-queries.ts`.
- `ManualDispatchDialog.tsx`: chọn kho (mặc định = kho xuất hiện tại của đơn), liệt kê dòng
  hàng sẽ trừ, cảnh báo rõ **"thao tác này trừ tồn kho"**.
- Nút "Xuất kho" ở topbar `pages/OrderDetailPage.tsx`, hiện khi có `stock:edit` và
  `delivery_business_status_code !== 'dispatched'`. Cờ này CHỈ để dựng UI — chốt chặn thật
  là guard backend, trả `skipped` với lý do `already_dispatched`.

## 7. Gỡ bỏ

Backend:

- Đổi tên `application/services/direct_dispatch_service.py` → `manual_dispatch_service.py`
  và `transport/views/direct_dispatch_views.py` → `manual_dispatch_views.py` (giữ nội dung
  theo §6.2, không xoá đi viết lại).
- Xoá hẳn `DirectDispatchService.list_pending` — không còn ai gọi.
- Xoá 2 route `direct-dispatch/pending` + `direct-dispatch/confirm` trong `urls.py`.
- Xoá setting `DIRECT_DISPATCH_WARN_DAYS` (`config/settings_parts/base.py`).
- Sửa 4 dòng trong `backend/.importlinter` đang gọi tên module cũ (3 dòng
  `direct_dispatch_service -> apps.orders.*` ở khối ~249, 1 dòng
  `apps.orders.tests.test_direct_deferred_dispatch -> apps.kiotviet.models` ở dòng 72).
  Bỏ sót thì import-linter đỏ ngay ở lần chạy đầu — và CI đang phải chạy tay nên không ai
  bắt hộ.
- Cập nhật docstring `_apply_shipping_type_after_job` và thông điệp cảnh báo Lark ở nhánh
  lỗi hậu xử lý — cả hai đang trỏ tới màn "Chờ xuất kho" sắp không còn.

Frontend:

- Xoá `pages/DirectDispatchPage.tsx`, `direct-dispatch-queries.ts`,
  `__tests__/direct-dispatch.test.tsx`.
- Gỡ entry ở `lib/routes.ts`, `app/routes.tsx`, `app/routed-pages.tsx`, `app/modules.ts`
  (và test `app/__tests__/modules.test.ts`).

Giữ lại, **có chủ đích**:

- Ô `pending_direct` trên form tạo đơn (`StockQueryService.availability` +
  `OrderFormStockSummary.tsx`) — **đổi nhãn** từ "chờ xuất" thành "đã lên HĐ, chưa xuất".
  Bỏ hàng đợi làm quãng chờ dài ra chứ không ngắn lại; đây là lớp cảnh báo bán trùng duy
  nhất còn lại. Query vẫn phải nở combo qua `ComboExpander` như cũ.
- Bí danh action `quick_sale_selected` (tab đang mở của người dùng vẫn bắn action này).

`schema.yaml` + `frontend/src/api/schema.d.ts` phải regen. File đã cũ sẵn ~444 dòng nên
regen sẽ kéo theo nhiễu của feature khác — **chỉ giữ lại phần thuộc MR này**.

## 8. Test

Không rải test lẻ. Thay bộ test cũ bằng hai file theo vòng đời.

`backend/apps/inventory/tests/test_manual_dispatch.py`:

1. Quick-sale thành công → có `invoice_code`, `shipping_type='direct'`, cờ `True`, **chưa**
   có bút toán SALE, **chưa** `dispatched`.
2. Quét mã hoá đơn → trừ tồn ở **kho của đơn**, `dispatched` được set.
3. Quét lại cùng mã → **không** trừ tồn lần hai.
4. Quét mã hoá đơn của đơn **thiếu kho xuất** → scan `FAILED`, không bút toán nào.
5. Một mã vừa là `shipping_code` đơn A vừa là `invoice_code` đơn B → khớp **đơn A**.
6. Quét mã vận đơn như cũ → vẫn trừ ở `scan.warehouse_id` (khoá hành vi cũ không vỡ).
7. Quét mã hoá đơn ở luồng HOÀN → **không** khớp (giữ nguyên phạm vi §5.3).
8. Nút xuất tay: ghi đè `fulfillment_warehouse`, trừ tồn đúng kho chọn, mốc là lúc bấm.
9. Nút xuất tay trên đơn đã xuất → `skipped: already_dispatched`, không trừ hai lần.
10. Nút xuất tay trên đơn không có hoá đơn KiotViet → vẫn xuất được (verdict UNVERIFIABLE).
11. Huỷ đơn direct cờ `True` khi chưa xuất → tồn không đổi, **không** mở
    `CancelledDispatchWatch`.
12. Huỷ đơn direct cờ `True` sau khi đã xuất → outstanding giữ nguyên, **có** mở watch.
13. **Huỷ đơn direct CŨ (cờ `False`) → vẫn cộng trả tồn ngay với `CANCEL_RELEASE_NOTE`.**
    Đây là test khoá lại §4; hỏng test này là dấu hiệu tồn sắp phồng trên prod.
14. `availability` vẫn trả `pending_direct` đúng, **có nở combo**.
15. Endpoint xuất tay thiếu `stock:edit` → 403.

`frontend/src/features/inventory/__tests__/manual-dispatch.test.tsx`: dialog chọn kho, mặc
định là kho của đơn, hộp cảnh báo trừ tồn, ẩn nút khi thiếu quyền, ẩn nút khi đơn đã xuất.
Chốt viewport bằng `src/test/viewport.ts` — jsdom mặc định rơi vào nhánh thẻ mobile.

## 9. Thứ tự deploy

**Không có migration.** Roll code backend, rồi frontend. Không có bước nào phải chạy giờ
thấp điểm.

Nhưng đây là **đổi quy trình kho**, không phải đổi kỹ thuật thuần:

1. Báo trước cho bộ phận kho: màn "Chờ xuất kho" sẽ biến mất.
2. Hướng dẫn hai cửa thay thế: quét **mã hoá đơn** ở màn quét xuất, hoặc bấm **"Xuất kho"**
   ở chi tiết đơn.
3. Đơn đang nằm trong hàng đợi tại thời điểm deploy sẽ **không mất đi** — chúng vẫn là đơn
   chưa xuất và xử lý được bằng cả hai cửa. Trước khi roll code nên chụp lại danh sách đơn
   đang chờ (`GET /api/inventory/direct-dispatch/pending`) để kho có bản đối chiếu.

**Không đảo thứ tự deploy ở trên** (backend rồi frontend). Riêng bước 2 — thông báo cho kho
rằng quét mã hoá đơn đã dùng được — **không được công bố trước khi frontend đã roll xong**
trên mọi máy quét. Lý do KHÔNG phải để tránh race giữa hai lần roll: `lookup_by_shipping_code`
chỉ mở nhánh khớp mã hoá đơn khi request mang tham số `invoice_match=1`
(`backend/apps/orders/transport/views/shipping_lookup_view.py`), và chỉ bundle FE MỚI mới biết
gửi tham số đó (`lookupByShippingCode` trong `scan-queries.ts`). Một tab quét cũ để mở qua lúc
deploy — SPA này không có cơ chế force-reload theo build version — sẽ không bao giờ gửi cờ và
vì vậy không bao giờ nhận một đơn khớp theo mã hoá đơn, dù backend đã roll xong từ lâu. Nói
cách khác: **chính cờ query-param mới là thứ khiến một tab cũ an toàn, không phải việc chờ rồi
mới thông báo** — thứ tự thông báo chỉ là hàng rào phụ (tránh nhân viên kho HỎI về một tính
năng mà máy họ chưa có), không phải cơ chế chống lỗi chính.

## 10. Rủi ro và nợ chấp nhận

- **Bán trùng trong quãng chờ.** Không đổi so với hôm nay: sổ chưa trừ nên tồn khả dụng vẫn
  tính hàng đó là còn. `pending_direct` là cảnh báo, không phải khoá.
- **Kho quên xuất.** Mất màn hàng đợi cũng là mất danh sách tập trung "đơn nào còn trên
  kệ". Đổi lại: bộ lọc trang đơn theo `shipping_type='direct'` cộng cột trạng thái giao vận
  đã dựng được đúng danh sách đó. Nếu sau một tháng vận hành thấy thiếu, làm bộ lọc sẵn
  (saved filter) chứ không dựng lại màn riêng.
- **Kho lệch giữa đơn và thực tế** ở đường quét mã hoá đơn (§5.2) — lối thoát là nút xuất
  tay cho chọn kho tại chỗ.
- **Không đo được quy mô trước khi làm.** MCP `prod-db` không kết nối được lúc thiết kế.
  Sau deploy cần đếm mỗi ngày: bao nhiêu đơn xuất qua mã hoá đơn, bao nhiêu qua nút tay,
  bao nhiêu đơn direct nằm quá 2 ngày chưa xuất.
