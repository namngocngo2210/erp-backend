# Đơn huỷ sau khi đã xuất kho: tồn chỉ cộng khi hàng thật về

Ngày: 2026-07-29
Nhánh: `feat/cancel-after-dispatch-return`

## 1. Bối cảnh và vấn đề

Nghiệp vụ có hai kiểu huỷ đơn, hôm nay hệ thống chỉ xử lý đúng một:

| | Tình huống | Mong đợi của kho | Hiện trạng |
|---|---|---|---|
| TH1 | Huỷ **trước** khi kho quét xuất | Không phải nhập hoàn; lúc quét xuất chỉ cần báo đơn đã huỷ | ✅ Đúng — `dispatch_orders_batch` chặn với `order_cancelled`, không ghi bút toán nào |
| TH2 | Huỷ **sau** khi kho đã quét xuất | Hàng đang trên đường về, khi về kho vẫn phải quét nhập hoàn | ❌ Quét nhập hoàn bị chặn |

Chuỗi gây lỗi ở TH2:

1. Đơn chuyển `status = 6` (CANCELLED) → subscriber `_on_order_status_changed`
   (`backend/apps/inventory/subscribers.py:69`) gọi ngay `release_for_order`, ghi
   `sale_reversal` **cộng tồn về sellable ngay lúc huỷ** — trong khi hàng còn ngoài kho.
2. Vài ngày sau hàng về, kho quét nhập hoàn → `_guard_double_credit`
   (`backend/apps/inventory/application/services/return_entry_service.py:110`) thấy đơn
   không còn outstanding và đang huỷ → chặn: *"Đơn X đã hủy nên tồn kho đã được tự động
   hoàn lại — không cần nhập hoàn phiếu này."*

Guard đó không sai: nó chặn cộng tồn hai lần. Sai nằm ở bước 1 — hệ thống cộng tồn theo
**sự kiện giấy tờ** thay vì theo **hàng vật lý**. Hai hệ quả:

- Kho không nhập hoàn được, mã vận đơn kẹt cứng, hàng về không có đường ghi nhận.
- Trong khoảng chờ hàng về, tồn sellable phồng hơn hàng thật trong kho, và không chỗ nào
  theo dõi "đơn đã huỷ mà hàng chưa quay về".

## 2. Nguyên tắc

> Bút toán kho phản ánh **hàng vật lý**, không phản ánh giấy tờ.
> Xuất trừ tồn khi hàng rời kho; nhập cộng tồn khi hàng về kho. Không sự kiện hành chính
> nào (huỷ đơn, đổi trạng thái) được tự cộng tồn thay cho một lần hàng về thật.

## 3. Luật ghi sổ

| Sự kiện | Hiện tại | Sau khi sửa |
|---|---|---|
| Quét xuất đơn sống | `SALE −n` | giữ nguyên |
| Quét xuất đơn đã huỷ (TH1) | chặn `order_cancelled`, không ghi sổ | giữ nguyên |
| Huỷ đơn **chưa** quét xuất | `release_for_order` no-op | giữ nguyên |
| Huỷ đơn **đã** quét xuất (TH2) | `sale_reversal +n` cộng tồn ngay | **không ghi bút toán**; mở một dòng theo dõi chờ hàng về |
| Huỷ đơn đã xuất, có đơn xuất lại `.NN` | `transfer_issue_to_sibling` chuyển sổ | giữ nguyên, vẫn thử trước |
| Quét nhập hoàn | bị chặn nếu đơn đã huỷ | `return_in +n` như đơn thường |
| Bỏ đánh dấu xuất kho (un-dispatch) | `release_for_order` | giữ nguyên — không đụng |

Thứ tự trong subscriber khi `new_status = 6` giữ nguyên như hôm nay: thử
`transfer_issue_to_sibling` trước (đơn xuất lại `.NN` đã thành dòng đơn mới → chuyển sổ,
tồn không đổi); chỉ khi không có sibling mới đi nhánh mới — **không release**, mở dòng
theo dõi nếu đơn còn outstanding.

`_guard_double_credit` **không đổi logic**. Nó chặn ở TH2 vì đơn hết outstanding; khi bỏ
auto-release thì outstanding còn nguyên nên guard tự cho qua, đồng thời vẫn chặn được ca
hoàn hai lần cho cùng một đơn. Nhánh thông điệp "đơn đã hủy nên tồn đã tự hoàn" chuyển
sang phục vụ riêng dữ liệu cũ (§6).

**Hệ quả phải chấp nhận:** từ lúc huỷ đến lúc hàng về, tồn sellable thấp hơn con số hôm
nay đúng bằng lượng hàng đang trên đường về. Đây là số đúng — hàng đó chưa bán được cho ai.

## 4. Bẫy kỹ thuật phải xử lý cùng lúc: status 6 → 5

> **Cập nhật 2026-09-14 — mục này KHÔNG còn áp dụng.** Chủ sở hữu chốt: đơn ĐÃ HUỶ mà
> hàng về kho vẫn giữ "Đã hủy"; phiếu hoàn chỉ ghi sổ tồn, `mark_order_returned` bỏ qua
> đơn đang ở 6 (không đổi status, không đẩy KiotViet, không phát sự kiện). Lý do: chuyển
> 6 → 5 bị cron đồng bộ KiotViet 30' lật lại 6 ở tick kế tiếp (hoá đơn đã huỷ, vận đơn "Đã
> hủy") — 35 đơn/14 ngày giằng qua lại, và đẩy 5 lên KiotViet luôn bị từ chối vì hoá đơn
> đã huỷ. Nhánh loại trừ 6 → 5 trong subscriber vẫn giữ làm phòng thủ cho dữ liệu cũ.
> Cùng đợt: cron chỉ được hồi sinh một đơn đang huỷ sau khi hỏi lại KiotViet theo mã hoá
> đơn (snapshot danh sách đọc theo trang có thể cũ vài phút — sự cố đơn 563397).

Ghi sổ phiếu hoàn gọi `return_order_sync` đặt `Order.status` từ `CANCELLED` (6) sang
`RETURNED` (5). Subscriber `_on_order_status_changed`
(`backend/apps/inventory/subscribers.py:88`) đọc `old_status = 6` và hiểu đó là **"đơn huỷ
được hồi sinh"** → chạy `reissue_after_cancel_release` + `_resync_to_current_items`.

Với luật mới, đơn TH2 không còn reversal-do-huỷ nên `reissue` sẽ no-op, nhưng `resync` vẫn
chạy trên một đơn vừa được hoàn — có thể ghi bút toán ngoài ý muốn.

**Sửa (bản đã triển khai):** nhánh hồi sinh loại riêng chuyển **6 → RETURNED(5)**, thay vì
lọc theo "sổ còn phần reversal-do-huỷ chưa bù" như bản thiết kế đầu.

Lý do đổi, phát hiện khi chạy test: nhánh hồi sinh làm HAI việc, không phải một. Ngoài
`reissue_after_cancel_release` (trừ lại phần đã hoàn — chỉ dữ liệu cũ mới có), nó còn gọi
`_resync_to_current_items` để kéo tồn về đúng items hiện tại. KiotViet huỷ-xuất lại hoá đơn
**chính là** cách Sale sửa dòng hàng, nên đơn hồi sinh có thể mang SKU khác hẳn. Lọc theo
reversal sẽ chặn luôn resync với mọi đơn huỷ theo luật mới → tồn kẹt ở SKU cũ, SKU mới không
bao giờ bị trừ (`test_revive_after_invoice_items_changed_resyncs_to_new_items`).

Hai hàm bên trong đều tự no-op đúng chỗ: `reissue` đọc sổ nên không thấy gì để trừ với đơn
mới; `resync` no-op khi outstanding = 0 hoặc khi items khớp. Việc cần chặn chỉ là đường
6 → 5 của phiếu hoàn.

Ca hồi sinh thật (KiotViet huỷ-xuất lại, đối soát bỏ huỷ) chuyển sang trạng thái giao hàng
(2/3/4…) nên vẫn vào nhánh và chạy đủ cả hai việc.

## 5. Hàng đợi "đơn huỷ chờ hàng về"

**Bảng mới `cancelled_dispatch_watches`** — là *hàng đợi công việc*, không phải nguồn số.
Dựng danh sách thuần từ ledger (đơn CANCELLED + outstanding > 0) sẽ phải gộp
`stock_movements` trên toàn bộ ~542k đơn mỗi lần mở tab; một dòng ghi đúng lúc subscriber
quyết định không release thì bảng luôn nhỏ.

| Cột | Vai trò |
|---|---|
| `order_id` (unique), `warehouse_id`, `cancelled_at` | định danh đơn cần theo dõi |
| `status`: `waiting` / `returned` / `closed` | vòng đời dòng |
| `closed_reason`, `closed_note`, `closed_by`, `closed_at` | dấu vết quyết định đóng |

Index: `status` + `cancelled_at` (danh sách mặc định lọc `waiting`, sắp theo ngày huỷ).

**Số lượng đang nợ không lưu trong bảng** — luôn đọc từ `outstanding_by_key` khi render,
một truy vấn gộp cho cả trang (không N+1). Bảng trả lời *"đơn nào cần theo dõi"*, ledger
trả lời *"còn nợ bao nhiêu"*. Không tạo hai nguồn số mâu thuẫn.

**Vòng đời:**

- `waiting` — khi huỷ đơn mà còn hàng ngoài kho (subscriber ghi, cùng transaction với sự kiện huỷ).
- `returned` — **tự động** khi hàng về đủ lúc ghi sổ phiếu hoàn. Không ai phải bấm gì.
- `closed` — người có quyền chủ động đóng vì hàng không bao giờ về.

**"Còn hàng ngoài kho" không phải là outstanding > 0.** Phiếu hoàn ghi `return_in` dưới
`reference_type="return_entry"`, nên nó KHÔNG làm giảm outstanding của đơn — đọc mỗi
outstanding thì dòng theo dõi kẹt `waiting` vĩnh viễn dù hàng đã về đủ. Phép tính đúng là
`Σ outstanding − Σ đã hoàn qua ReturnEntry đã ghi sổ > 0`, đóng gói trong
`order_goods_still_out()` và dùng chung cho cả `open_for_order`, `settle_if_returned`, và
cột "SL đang nợ" của danh sách. Cùng công thức mà `_guard_double_credit` vẫn dùng để tính
hạn mức.

**Đóng đơn không ghi bút toán kho.** Hàng không quay về nghĩa là tồn hiện tại đã đúng;
đóng chỉ ghi nhận quyết định và đưa dòng ra khỏi hàng đợi. Ghi thêm bút toán ở bước này sẽ
làm tồn sai đúng bằng lượng hàng đó.

**Lối ra thứ ba: sổ xuất chuyển sang đơn xuất lại (bổ sung 2026-08-20).** KiotViet huỷ-xuất
lại hoá đơn → `transfer_issue_to_sibling` chuyển outstanding sang bản `<mã>.NN`. Đơn cũ hết
nợ kho mà **hàng không hề quay về** — không được đánh `returned`, cũng không phải quyết định
của người dùng. Dòng đóng bằng `closed` + `closed_reason = reissued`, `closed_by = NULL`:

- Ghi ngay trong `transfer_issue_to_sibling`, KHÔNG ở từng call-site. Ba đường đều chuyển sổ
  (subscriber huỷ, sweep `repair_reissue_ledger`, luồng phiếu hoàn khớp parent); đặt ở caller
  là chắc chắn quên một đường — đúng lỗi đã xảy ra: sweep đêm chuyển sổ mà không ai đụng
  watch, để lại **16 dòng kẹt `waiting`** trên prod ngày 20/08 (đơn cũ nhất từ 29/07), kho đi
  đòi hàng không tồn tại.
- `reissued` KHÔNG nằm trong dropdown đóng tay (`AwaitingReturnService.MANUAL_CLOSE_REASONS`),
  và `refresh_for_order` / `settle_if_returned` không được ghi đè nhãn này thành `returned`.
  Nợ kho quay lại thì `_reopen` vẫn chạy như thường — sổ vẫn thắng nhãn.
- Dọn dữ liệu cũ: `python manage.py reconcile_awaiting_return [--dry-run]` — lệnh sống, chạy
  lại được, đối soát mọi dòng `waiting` với sổ (còn nợ → giữ; hết nợ do chuyển sổ → `reissued`;
  hết nợ do phiếu hoàn → `returned`).

**Đóng nhầm / hàng về muộn:** không cần nút mở lại. Outstanding vẫn > 0 nên quét nhập hoàn
vẫn chạy bình thường; dòng tự chuyển `closed → returned` và ghi log.

### 5.1 API

Theo convention `backend/apps/inventory/urls.py`:

| Endpoint | Quyền | Nội dung |
|---|---|---|
| `GET dispatch-history/awaiting-return` | `require_section("returns")` | danh sách, lọc theo kho / khoảng ngày huỷ / tuổi nợ / trạng thái, phân trang như các tab khác |
| `POST dispatch-history/awaiting-return/<order_id>/close` | `require_permission("returns:approve")` | body `{reason, note}` |

`reason` là enum: `customer_kept` (khách đã nhận và giữ hàng) · `lost_in_transit` (thất
lạc, shipper đền) · `disposed_outside` (huỷ tại chỗ, không trả về) · `other` (bắt buộc
`note`).

### 5.2 UI

Tab mới **"Chờ hàng hoàn về"** trong màn Lịch sử nhập xuất, badge đếm số đơn `waiting`.

Cột: mã vận đơn · mã hoá đơn · khách · kho · SKU + SL đang nợ · ngày xuất · ngày huỷ ·
**số ngày chờ** · trạng thái · nút Đóng.

Dialog đóng: chọn lý do, ô ghi chú (bắt buộc khi chọn "khác"), kèm câu xác nhận
*"Đóng đơn không cộng tồn — hàng coi như đã rời kho vĩnh viễn."*

Cảnh báo: badge đếm trên tab, cộng thông báo Lark hằng ngày cho đơn chờ quá **14 ngày**
(tái dùng `lark_notifier`).

### 5.3 Cảnh báo Lark tức thời (bổ sung 2026-08-24)

Bản tin quá hạn 14 ngày là *lưới hứng cuối*, quá muộn để đi đòi hàng: sau hai tuần khách đã
dùng hàng hoặc hãng ship đã đóng hồ sơ khiếu nại. Bổ sung một cảnh báo **ngay lúc phát hiện**:

- **Điều kiện bắn**: `open_for_order` trả `True`, tức dòng theo dõi được **mở mới**. Không
  bắn theo "đơn chuyển sang trạng thái huỷ" — chỉ dòng mở mới mới đồng nghĩa *sổ kho xác
  nhận hàng đang thật sự ngoài kho*. Sự kiện huỷ bắn trùng (webhook KiotViet) hay dòng cũ
  được `_reopen` theo sổ đều **không** báo lại: hàng đó kho đã biết.
- **Ngoài phạm vi có chủ đích**: đơn bán trực tiếp (hàng còn trong kho, tự hoàn tồn), đơn
  chuyển sổ sang bản xuất lại (hàng không mất), đơn chưa từng xuất kho. Cả ba nhánh này
  thoát trước khi tới `open_for_order` nên tự động không có cảnh báo.
- **Bot riêng** `LARK_CANCEL_DISPATCH_BOT_*`, không dùng chung webhook bot tồn âm: khác
  người xử lý, khác mức khẩn, và trộn chung thì nhóm kho bắt đầu bỏ qua cả hai. Webhook để
  trống = tắt cảnh báo, hàng đợi vẫn chạy như cũ.
- **Gửi qua Celery** (`lark.notify_cancelled_after_dispatch`, queue `lark_noti`) chứ không
  gọi HTTP tại chỗ: subscriber chạy trong luồng huỷ đơn — webhook KiotViet là đường nóng.
- **Nội dung**: mã đơn · mã vận đơn · kho giao · danh sách SKU (bổ sung 2026-08-26). Không
  kèm thông tin khách — tin vào nhóm chat chung.
- **SKU đọc từ `OrderItem`, KHÔNG từ sổ** (chốt 2026-08-26). Tin cảnh báo khớp với thứ
  Sale/kho nhìn thấy trên đơn. Đánh đổi đã biết và chấp nhận: đơn đã hoàn một phần vẫn
  liệt kê đủ SL gốc, và đơn combo hiện mã combo chứ không phải các mã lẻ đã nở lúc
  `issue_for_order` ghi sổ — số nợ chính xác vẫn tra ở tab "Chờ hàng hoàn về", nơi
  `_owed_by_order` đọc thẳng ledger. Danh sách cắt ở 10 SKU + "… và N SKU nữa" để tin dài
  bất thường không đẩy ba dòng định danh khuất tầm mắt.

## 6. Dữ liệu đã có trên prod

Đơn huỷ **trước** ngày deploy đã auto-release: tồn đã cộng, outstanding = 0, hàng có thể
nay mới về. Không đụng vào sổ cũ; chỉ mở đường cho hàng về được ghi nhận.

Phân biệt không cần cờ hay mốc ngày — đọc thẳng sổ: đơn có `SALE_REVERSAL` mang note
`Hoàn tồn do huỷ đơn` (hoặc note rỗng, dữ liệu cũ hơn) và outstanding = 0 → đúng là ca cũ.

Với ca đó, quét nhập hoàn **được nhận**, và khi ghi sổ hệ thống ghi một cặp bút toán:

- `return_in +n` — hàng về kho thật, giữ bất biến "mỗi dòng phiếu hoàn có bút toán tương ứng";
- `SALE −n` note `Thu hồi tồn đã hoàn tự động khi huỷ` — thu lại phần đã cộng sớm lúc huỷ.

Tồn ròng không đổi. Note mới vào nhóm `ADMIN_SALE_NOTES`
(`backend/apps/inventory/application/services/stock_sales_service.py:44`) nên không bị
tính là một lần hàng rời kho trong báo cáo sản lượng xuất.

**Bắt buộc đi kèm:** note thu hồi phải được `reissue_after_cancel_release` tính là **phần
đã bù**, tức thêm vào vế trừ bên cạnh `REVIVE_REISSUE_NOTE` (`stock_sales_service.py:373`).
Thiếu bước này thì một đơn cũ đã nhập hoàn (đã thu hồi phần cộng sớm) mà sau đó được hồi
sinh sẽ bị trừ tồn hai lần: reissue vẫn thấy reversal-do-huỷ còn nguyên phần dư. Đây cũng
chính là phép tính mà điều kiện vào nhánh hồi sinh ở §4 dùng chung.

Nhánh này tự tiêu biến: sau deploy không đơn nào sinh thêm reversal-do-huỷ. Đánh dấu rõ
trong code là đường tương thích dữ liệu cũ.

**Không backfill hàng đợi** cho đơn huỷ cũ: tồn của chúng đã được cộng, trên sổ không còn
khoản nợ nào để theo dõi. Hệ quả tích cực: **thời điểm deploy không có cú sốc tồn** — tồn
chỉ khác dần theo các đơn huỷ phát sinh sau đó.

## 7. Kiểm thử

**Luật ghi sổ** — huỷ đơn đã xuất không sinh reversal và tồn đứng yên · huỷ đơn chưa xuất
vẫn no-op · huỷ đơn có `.NN` vẫn chuyển sổ như cũ · quét nhập hoàn đơn huỷ cộng tồn đúng
một lần · quét nhập hoàn hai lần vẫn bị `_guard_double_credit` chặn.

**Bẫy status 6→5** — ghi sổ phiếu hoàn cho đơn huỷ không kích hoạt `reissue` / `resync` ·
ca hồi sinh thật (có reversal-do-huỷ trong sổ) vẫn trừ lại tồn đúng.

**Dữ liệu cũ** — đơn có reversal-do-huỷ khi quét nhập hoàn sinh đúng cặp `return_in` +
`SALE` thu hồi, tồn ròng không đổi · đơn cũ đã thu hồi rồi mới được hồi sinh **không** bị
trừ tồn lần hai (chốt phần bù ở §6).

**Hàng đợi** — dòng `waiting` sinh đúng lúc huỷ · tự chuyển `returned` khi hết outstanding
· đóng đơn không sinh bút toán nào · đóng đòi quyền `returns:approve` · đóng rồi hàng về
vẫn quét được và dòng quay lại `returned`.

**Frontend** — tab mới: badge đếm, cột số ngày chờ, dialog đóng bắt buộc ghi chú khi chọn
"khác".

Chạy theo cách đã dùng cho repo này (pytest trong container backend, vitest cho frontend
trong worktree).

## 8. Ngoài phạm vi

- Luồng khớp đơn (`pending_shipment_scans`, cron `match_pending_shipment_scans`) — giữ nguyên.
- Thẻ kho (`StockPage`, `StockMovementHistory`) vẫn hiện `+N / −N`: đó là sổ bút toán, dấu
  chính là chiều nhập/xuất.
- Luật hiển thị số dương ở Lịch sử nhập xuất (`docs/flow-history-positive-quantity-2026-07-28.md`)
  đã merge — chỉ rà lại, không sửa.
- Guard chặn quét xuất đơn huỷ (TH1) và thông báo "Đơn đã hủy — không thể xuất kho" đã có sẵn.
