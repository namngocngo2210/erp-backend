# Pancake POS ↔ CRM: vận hành kho trên Pancake, sổ 2 cấp trên CRM

**Ngày:** 2026-09-30 · Chia 3 MR (A, B, C — §7). Phụ thuộc: MR !1508 (đồng bộ sản phẩm, app
`pancake_pos`), MR !1504 (kho × tính chất). Phần đồng bộ sản phẩm (S1–S2) ở `docs/pancake-pos-product-sync.md`.

## 1. Quyết định của chủ sản phẩm (30/09/2026)

1. **Pancake chỉ có 3 kho lớn**: Đại Mỗ 1, Đại Mỗ 2, Văn phòng. **CRM xem theo phân cấp**
   kho × tính chất (MR !1504).
2. Đơn Shopee/TikTok đã có sẵn trên Pancake. Đơn **landing / messenger / zalo / hotline**
   (tạo trên CRM) và **đơn Foxia** (kéo về CRM theo `cohoi_id`) được **CRM đẩy sang Pancake**.
3. **Kho thao tác trên Pancake**: quét xuất, nhận hoàn, nhập mua, chuyển kho, kiểm kê.
   **CRM ghi lại** qua webhook và đối soát, rồi dựng sổ 2 cấp.
4. Có cơ chế **ghép (mapping)** Pancake ↔ CRM rõ ràng cho sản phẩm, kho, đơn, trạng thái.

## 2. Chủ sở hữu từng thao tác

| Thao tác | Làm ở | CRM ghi sổ (`stock_movements`) |
|---|---|---|
| Quét xuất đơn (mọi kênh) | Pancake | `SALE` từ **Hàng hoá**, reference = đơn CRM đã ghép |
| Nhận hàng hoàn | Pancake | `ReturnEntry` + `RETURN_IN` vào **Hoàn chưa xử lý** (luồng !1504) |
| Nhập mua | Pancake (phiếu nhập) | Phiếu kho CRM `RECEIPT` Hàng hoá, tự sinh, trạng thái đã ghi sổ, nguồn = pancake |
| Chuyển kho ĐM1↔ĐM2 | Pancake (phiếu chuyển) | `TRANSFER` Hàng hoá, tự sinh |
| Kiểm kê | Pancake (phiếu kiểm) | `ADJUSTMENT` Hàng hoá theo số Pancake, tự sinh |
| **Xử lý hoàn từng đơn, chuyển tính chất, bảo hành** | **CRM** (chỉ CRM có tính chất) | Như !1504; **đẩy phần ảnh hưởng số bán được sang Pancake** (§4) |

Nguyên tắc: một thao tác chỉ có **một** chủ sở hữu. Sự kiện do CRM gây ra trên Pancake khi
quay về qua webhook phải được **nhận diện và bỏ qua** (§5.3), không ghi sổ lần hai.

## 3. Cơ chế ghép (app `apps/pancake_pos`)

| Bảng | Khoá | Ghi chú |
|---|---|---|
| `pancake_pos_variation_links` (đã có S1) | product_id ↔ variation_id | Khớp theo SKU |
| `pancake_pos_warehouse_links` | warehouse_id CRM ↔ warehouse_id Pancake | 3 dòng, cấu hình trên UI |
| `pancake_pos_order_links` | order_id CRM ↔ order_id Pancake (UNIQUE cả hai), `origin` (crm_push / pancake_native), `pushed_hash`, `status` | Đơn CRM đẩy thì sinh lúc đẩy. Đơn sàn/page thì sinh khi webhook về: tạo đơn CRM nếu chưa có, khớp mã đơn sàn nếu có |
| `pancake_pos_status_map` | status Pancake → tên tiếng Việt + mã "Trạng thái vận chuyển thực" (`crm_delivery_code`, MR D) | Seed mặc định, sửa được. `crm_status` không còn dùng để ghi `orders.status` |
| `pancake_pos_webhook_events` | (type, id bản ghi Pancake, thời điểm/phiên bản) UNIQUE | Lưu thô, xử lý bất đồng bộ, chống trùng, cho phép xử lý lại |
| `pancake_pos_inventory_snapshots` | (warehouse, variation, ngày) | Số tồn Pancake mỗi đêm, dùng cho đối soát và cho lúc chuyển hệ thống |

## 4. CRM → Pancake

### 4.1 Đẩy đơn

- **Đủ điều kiện đẩy**: đơn CRM thuộc nguồn `landing/messenger/zalo/hotline/foxia`, ở trạng thái
  **đã chốt** (xác định trạng thái cụ thể ở S4 theo `OrderStatus` hiện có), chưa có
  `order_link`, và có đủ dòng hàng đã ghép SKU.
- **Chống giao trùng landing ↔ Foxia (bắt buộc)**: đơn landing đã đẩy thành cơ hội Foxia
  (`FoxiaLeadLink`) thì **đơn Foxia là đơn thật**, đơn landing gốc **không đẩy**. Nếu đơn landing
  đã đẩy mà sau đó mới khớp được đơn Foxia, thì không đẩy đơn Foxia nữa, cảnh báo Lark để người
  xử lý. S4 phải đo dữ liệu thật (read-only) để xác nhận mối quan hệ này trước khi code.
- Payload: khách (tên, SĐT **không che**, vì đây là dữ liệu vận hành gửi đi; chỉ gửi qua kênh
  API), địa chỉ, dòng hàng (variation_id, SL, giá, giảm), phí ship, COD, ghi chú, nguồn
  (`order_source` Pancake), kho xuất (theo `warehouse_links`, mặc định cấu hình).
- Sửa hoặc huỷ trên CRM trước khi Pancake xuất thì `PUT` hoặc huỷ trên Pancake. Sau khi Pancake
  đã xuất thì chỉ cảnh báo, không sửa.
- Idempotent: `order_link` tạo trong cùng transaction với việc đánh dấu "đang đẩy". POST không
  retry khi lỗi 5xx hay ReadTimeout (như S1); lượt sau tìm lại đơn trên Pancake theo mã tham
  chiếu CRM rồi mới quyết định tạo.

### 4.2 Hàng không bán được trên Pancake = "hàng lỗi" của chính kho đó (chốt 30/09/2026)

Pancake không có cài đặt chặn cộng tồn khi nhận hoàn (người dùng đã kiểm tra: Cấu hình → Vận hành
kho, Cấu hình → Đơn hàng). Nhưng Pancake có **hàng lỗi** theo từng kho (`defect_quantity`), điều
khiển được qua API **phiếu xuất hàng lỗi**: `POST /shops/{SHOP_ID}/export` với `type=1`.
- `status=0` (Xuất hàng lỗi): chuyển SL từ bán được sang hàng lỗi.
- `status=1` (Xuất bỏ): loại hẳn khỏi kho.
- `status=2` (Huỷ).
- `PUT /export/{id}`: đổi SL từng dòng hoặc đổi trạng thái.

Ghép tính chất CRM → Pancake (Pancake vẫn đúng **3 kho**):

| Tính chất CRM | Trên Pancake |
|---|---|
| Hàng hoá | Bán được của kho |
| Hoàn chưa xử lý, Lỗi, Đã qua sử dụng | **Hàng lỗi** của cùng kho (một phiếu xuất hàng lỗi / nhóm đơn hoàn) |
| Bảo hành (máy của khách), Linh kiện | Không đưa lên Pancake |

Luồng:
1. Pancake nhận hoàn và tự cộng vào bán được. Webhook về CRM, CRM ghi `return_pending` và **ngay lập tức**
   tạo phiếu xuất hàng lỗi `type=1,status=0` đúng SL × kho, lưu `export_id` ↔ `ReturnEntry`.
2. CRM xử lý hoàn:
   - Sang Hàng hoá: `PUT` giảm SL dòng tương ứng (hoặc `status=2` nếu cả phiếu), để hàng về bán được.
   - Sang Lỗi hoặc Đã qua SD: giữ nguyên.
   - Gửi lại hoặc Huỷ: `status=1` (xuất bỏ) phần đó.
3. Hoàn tác xử lý thì đảo lại thao tác tương ứng.

**S3 phải kiểm chứng bằng key thật**: huỷ hoặc giảm SL phiếu hàng lỗi có trả về bán được không; xuất
bỏ có trừ tổng không; webhook tồn phát ra cho các thao tác này mang `type` gì (để chống vòng lặp §5.3).
Nếu không đạt thì dùng phương án dự phòng: thêm một kho ẩn "Hàng hoàn".
Khoảng hở rủi ro: vài giây giữa bước Pancake cộng tồn và bước CRM tạo phiếu hàng lỗi. Đối soát bắt các ca sót.

## 5. Pancake → CRM

### 5.1 Webhook

- Endpoint `POST /api/pancake-pos/webhook`, xác thực bằng **header bí mật** tự đặt (Pancake không
  ký payload), so sánh hằng thời gian. Chỉ lưu thô và trả 200 thật nhanh; worker xử lý sau.
- Loại cần bật: `orders`, `variations_warehouses`, `products`.
- `orders`: **KHÔNG ghi `orders.status`** (quyết định 01/10/2026 — trạng thái đơn vẫn do
  KiotViet). Ghi trạng thái vận chuyển thực (§5.4): 2 field `partner_*` + mã "Trạng thái vận
  chuyển thực"; điền mã vận đơn/ĐVVC khi CRM còn trống. Đơn sàn/page chưa có trên CRM thì tạo
  đơn CRM (cờ, mặc định tắt; `status` luôn "Chờ xử lý") để báo cáo và sổ kho có tham chiếu.
- `variations_warehouses`: dùng `type` + `change_quantity` để phân loại thành xuất bán / nhập
  mua / chuyển / kiểm kê / hoàn và ghi sổ CRM theo §2.

### 5.4 Trạng thái vận chuyển thực (MR D, 01/10/2026)

Quyết định người dùng: `Order.status` **tiếp tục do KiotViet**; Pancake không bao giờ ghi `status`.

| Field CRM | Nguồn Pancake | Ghi bằng |
|---|---|---|
| `orders.partner_delivery_status` | Tên trạng thái đơn Pancake **tiếng Việt** (`status_map.pancake_name`; API trả `status_name` là mã tiếng Anh `shipped`/`delivered`… nên không dùng nguyên văn) | 1 câu UPDATE có điều kiện (chỉ khi đổi), không `save()`, không đẩy `updated_at` (mốc A2 phát hiện sửa đơn), không phát event |
| `orders.partner_dvvc_delivery_status` | ĐVVC mới nhất: `partner.partner_status` (enum → nhãn spec); không có thì mục mới nhất của `partner.extend_update` theo `update_time_millis` → nhãn theo `action_code`; mã lạ → câu mô tả đã che SĐT, cắt 64 ký tự | như trên |
| `order_assessments.delivery_business_status_code` ("Trạng thái vận chuyển thực") | `status_map.crm_delivery_code` (seed, sửa được) | `OrderService.update_order_inline(crm=…)` — journey `delivery_business_status_changed`, `payload_json.source = "pancake_pos"`, actor rỗng; `dispatched` chỉ qua `dispatch_orders_batch` |

- **Đơn mirror Foxia/CNV** không bị ghi 2 field `partner_*` (của hệ đối tác tương ứng).
- **Đơn tách N kiện**: field hiện **kiện chậm nhất** (bỏ kiện huỷ/xoá; mọi kiện huỷ → "Đã huỷ";
  thứ tự: Mới < Chờ xác nhận < Chờ hàng < Đã đặt hàng < Đã xác nhận < Chờ in < Đã in < Đang đóng
  hàng < Chờ chuyển hàng < Đã gửi hàng < Đang hoàn < {Đã nhận, Đã thu tiền, Hoàn một phần, Đã
  hoàn}; trạng thái lạ = chậm nhất; hoà → kiện tạo trước). Mã vận đơn chỉ điền cho đơn một kiện.
  Hai kiện cùng đơn xử lý tuần tự (khoá dòng `orders`).
- **Chống bản cũ đè bản mới**: giữ nguyên B1 (`pancake_updated_at` của từng đơn Pancake).
- **Mốc** trên `pancake_pos_inbound_orders`: `shipped_at` (= sent), `delivered_at`, `returned_at`,
  `status_changed_at` (vào trạng thái hiện tại, từ `status_history`), `last_partner_status_at`
  (cập nhật ĐVVC gần nhất), kho Pancake (`pancake_warehouse_id`, `warehouse_name`).
- **Seed `crm_delivery_code`**: Mới/Đã xác nhận/Chờ in/Đã in/Đang đóng hàng/Chờ chuyển hàng →
  `warehouse_processing`; Đã gửi hàng → `dispatched`; Đã nhận/Đã thu tiền → `received`; Đang
  hoàn/Đã hoàn → `not_received` (khách không nhận; `kiotviet_validation` xếp mã này vào nhóm
  "đang giao" — chặn tạo đơn trùng SĐT, hợp nghĩa); Hoàn một phần (khách đã nhận một phần), Chờ
  xác nhận/Chờ hàng/Đã đặt hàng, Đã huỷ/Đã xoá → không đổi. Mã cũ `delivered_success`/`delivering`/
  `bombed` (`customers/services.py`) không còn trong catalog → không ghi.
- **Luật ghi mã** (`domain.delivery_status.decide_delivery_code`): chỉ tiến (rỗng < kho đang xử lý
  < đã xuất kho < đã nhận | chưa nhận); không ghi đè `stalled_complaint`/`post_office_lost`, và
  `received`/`not_received` khi mã đó không do Pancake đặt (journey đổi mã gần nhất không có
  `source=pancake_pos`); hai mã kết thúc đổi ngang chỉ khi Pancake đã đặt mã hiện tại.
- **Tồn kho — cờ `PANCAKE_POS_DISPATCH_ENABLED`** (mặc định **tắt**, staging cấm bật):
  - Tắt (kho chạy trên Pancake, CRM không giữ tồn — chốt 05/10/2026): không bao giờ ghi
    `dispatched` (dấu hiệu đã trừ tồn CRM) — Pancake Đã gửi mà CRM chưa xuất kho → chỉ ghi
    `partner_*` + báo cáo lệch (luật `pancake_shipped_crm_not_dispatched`). Mã kết thúc
    Đã nhận/Đã thu tiền → `received`, Đang hoàn/Đã hoàn → `not_received` **ghi thẳng** (kể cả
    backfill, kể cả đơn đang `dispatched`), KHÔNG trừ tồn.
  - Bật (sau cutover, kho quét trên Pancake): Pancake Đã gửi hàng (mọi kiện) → task
    `sync_delivery_code` gọi **đúng** `OrderService.dispatch_orders_batch` (đường quét kho / cron
    khớp mã chờ: xác minh huỷ KiotViet ngoài transaction, `issue_for_order` idempotent, chặn đơn
    huỷ) với kho CRM ghép từ kho Pancake (`warehouse_links`, thiếu thì kho của đơn), ngày nghiệp
    vụ = mốc gửi Pancake. Đi tắt tới Đã nhận → xuất kho rồi ghi `received`. Xác minh KiotViet lỗi
    tạm → thử lại 3 lần × 5 phút, còn lại nằm ở báo cáo lệch.
  - ⚠ **Khi bật cờ này, B2 (MR !1511) PHẢI coi sự kiện tồn loại bán hàng (xuất theo đơn) của
    Pancake là echo** — sổ CRM đã trừ qua `dispatch_orders_batch`, ghi thêm từ `variations_warehouses`
    là trừ đôi.
- **Không có event mới**: consumer duy nhất của `OrderStatusChanged` là `inventory` (cân tồn theo
  `status`) — không cần biết trạng thái partner; lịch sử nằm ở journey. B1 thêm
  `StatusChangeSource.PANCAKE_POS` đã gỡ.
- **Báo cáo** (chỉ đọc; API + thẻ trang Pancake POS):
  - `GET delivery/stuck`: đơn Pancake (từng kiện) **chưa gửi** (Mới/Đã xác nhận/Chờ in/Đã in/Đang
    đóng hàng/Chờ chuyển hàng) quá `PANCAKE_POS_STUCK_NOT_SENT_HOURS` (24) giờ từ lúc tạo, cửa sổ 30
    ngày; **đã gửi** quá `PANCAKE_POS_STUCK_NOT_DELIVERED_DAYS` (5) ngày từ mốc gửi chưa giao, cửa
    sổ 60 ngày; tổng theo kho Pancake. Beat mỗi giờ (phút 1) → Lark một lần/ngày lúc
    `PANCAKE_POS_STUCK_ALERT_HOUR` (9, -1 = tắt).
  - `GET delivery/mismatches`: đơn CRM có kiện Pancake cập nhật trong 30 ngày mà `status` KiotViet
    mâu thuẫn rõ: CRM huỷ ↔ Pancake đã gửi/nhận; CRM giao thành công ↔ Pancake hoàn; CRM hoàn ↔
    Pancake đã nhận; CRM đang giao/đã giao ↔ Pancake huỷ; CRM chưa giao ↔ Pancake đã nhận; cộng
    Pancake đã gửi mà CRM chưa xuất kho. Không sửa gì.
  - `GET orders/<id>/delivery`: 2 field + từng kiện (trang chi tiết đơn).
- **Backfill**: `manage.py pancake_pos_backfill_delivery_status --days N [--dry-run]` — GET đơn
  Pancake cập nhật trong N ngày (≤ 90, read-only), lưu như sự kiện `orders` rồi đi đúng pipeline
  webhook; **không bao giờ xuất kho** (kể cả cờ bật). Chạy lại an toàn (payload trùng bị bỏ).
- **UI**: cột "Trạng thái vận chuyển thực" có dòng phụ `partner_delivery_status · partner_dvvc…`
  + tooltip; chi tiết đơn có mục "Vận chuyển (Pancake)"; trang Pancake POS có thẻ "Đơn treo" và
  "Lệch KiotViet ↔ Pancake"; thẻ Webhook hiện cờ xuất kho.

### 5.2 Đối soát (beat đêm + chạy tay)

- Kéo `orders` cập nhật trong 48h và `inventory_histories` để bù webhook bị mất (dựa vào bảng
  chống trùng).
- So **Hàng hoá CRM** với **số bán được Pancake** theo (kho, SKU). Lệch thì báo Lark; tuỳ chọn tự
  sinh phiếu điều chỉnh CRM theo Pancake (Pancake là nơi đếm hàng vật lý).

### 5.3 Chống vòng lặp

Mọi thao tác CRM gửi sang Pancake được ghi vào `pancake_pos_outbound_ops` (loại, variation,
kho, số lượng, thời điểm, id trả về nếu có). Webhook tồn khớp một outbound op trong cửa sổ thời
gian thì đánh dấu `echo` và không ghi sổ.

## 6. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Giao trùng landing + Foxia | Luật §4.1 + đo dữ liệu thật ở S4 + cảnh báo |
| Webhook mất hoặc sai thứ tự | Lưu thô + chống trùng + đối soát đêm |
| Phân loại sai `type` tồn của Pancake | Bảng map cấu hình được; loại lạ thì ghi `unclassified`, báo, không ghi sổ |
| Hàng hoàn bị bán trước khi xử lý | §4.2, kiểm chứng S3 trước khi bật |
| Cutover (hôm chuyển) | Runbook: đẩy tồn đầu kỳ, khoá quét CRM, bật webhook, chạy đối soát ngay |

## 6b. Bổ sung đã chốt trong trao đổi 30/09/2026

- **Nhân viên kho dùng 100% Pancake**, kể cả bảo hành và linh kiện:
  - CSKH khai báo phiếu bảo hành trên CRM kèm mã vận đơn ngoài. CRM tạo **"đơn bảo hành" 0đ**
    trên Pancake (thẻ BẢO HÀNH, gắn mã vận đơn đó), kho quét trên Pancake.
  - Gửi linh kiện: đơn chứa SKU linh kiện. Linh kiện **được đưa lên Pancake** ở danh mục
    "Linh kiện", ẩn, không nối sàn. Điều này thay luật loại linh kiện ở !1508.
  - Máy của khách (nhận về / gửi trả): mã dịch vụ `BH-MAYKHACH`, CRM tự làm sạch tồn của mã này,
    loại khỏi đối soát và báo cáo. Cần kiểm chứng Pancake có cho đơn chứa hàng không quản lý tồn.
  - Đổi lỗi sản phẩm nguyên: dùng **đơn đổi trả** của Pancake.
- **Trừ tồn trên CRM theo SKU × kho từ sự kiện tồn Pancake**, không phụ thuộc việc khớp đơn.
  Khớp đơn chỉ phục vụ trạng thái và báo cáo, theo thứ tự: liên kết đẩy → mã đơn sàn → mã vận đơn
  → tự tạo đơn CRM. Mã không khớp hoặc khớp nhiều đơn thì vào màn "chờ khớp" + cảnh báo Lark.
- **Hoàn một phần**: Pancake có trạng thái "Hoàn một phần" + `returned_quantity` theo mẫu mã. CRM
  tạo `ReturnEntry` đúng SL từng SKU.
- **Một đơn Pancake = một kho** (`warehouse_id` cấp đơn). CRM chọn kho có đủ hàng theo thứ tự ưu
  tiên cấu hình. Không kho nào đủ thì đưa đơn vào hàng đợi **"Cần chọn cách xuất"** để người điều
  phối chọn: chuyển kho rồi gửi 1 kiện, hoặc tách N đơn Pancake. `order_links` là **1 CRM ↔ N Pancake**.
- **Hàng lỗi có thẻ**: phiếu xuất hàng lỗi mang thẻ `HOÀN CHƯA XỬ LÝ` / `LỖI` / `ĐÃ QUA SỬ DỤNG`.
  CRM đổi thẻ hoặc tách phiếu khi xử lý (chờ người dùng chọn cách 1; kiểm chứng thẻ phiếu qua API).
- **Câu hỏi còn mở** (hỏi người dùng): KiotViet còn đẩy đơn sàn về CRM không; thứ tự ưu tiên kho;
  gom hay tách kiện; Văn phòng có bán online; ĐVVC bảo hành; hàng đã qua sử dụng có bán thanh lý không.

## 7. Chia MR và sprint

| MR | Sprint | Nội dung | Cần | Trạng thái |
|---|---|---|---|---|
| !1508 | — | Đồng bộ sản phẩm (xong code). Sau deploy: nhập key, kiểm chứng API thật | API key | ✅ code |
| **A** `feat/pancake-pos-orders-stock` | A1 | Ghép kho (`warehouse_links`, cấu hình UI) + **đẩy tồn đầu kỳ** Hàng hoá (+ Linh kiện) theo (kho, SKU), dry-run + bảng so sánh, lệnh chạy | — (chạy thật cần key) | ✅ Xong (`d88ccec`; chạy thật cần ghép kho + dry-run trên prod) |
| | A2 | **Đẩy đơn CRM → Pancake**: landing/mess/zalo/hotline + Foxia, luật chống trùng landing↔Foxia (đo dữ liệu thật), chọn kho, hàng đợi "Cần chọn cách xuất", 1↔N, sửa/huỷ, `status_map` | — (chạy thật cần key + bật cờ) | ✅ Xong (`adafa65`; chạy thật cần key, ghép nguồn đơn, bật cờ) |
| **B** (sau A, sau !1504 merge) | B1 | Webhook: nhận, lưu thô, chống trùng; `orders` → trạng thái đơn, đơn sàn → CRM | Bật webhook | ✅ Xong (`29564f4` (trên nhánh D: `b80f924`); bật: đặt secret + URL, chạy `pancake_pos_webhook configure`) |
| **D** `feat/pancake-pos-delivery-status` (trên !1509 + B1) | D | Trạng thái vận chuyển thực từ Pancake: `partner_*`, mã "Trạng thái vận chuyển thực" (+ xuất kho theo cờ), báo cáo đơn treo + lệch, backfill (§5.4) | Bật webhook; cờ xuất kho sau cutover | ✅ Xong (MR D) |
| | B2 | Sự kiện tồn → sổ CRM 2 cấp (xuất, nhập, chuyển, kiểm kê, hoàn, hoàn một phần), chống vòng lặp | — | ⏳ |
| | B3 | CRM → Pancake: xử lý hoàn qua phiếu hàng lỗi có thẻ, chuyển tính chất | — | ⏳ |
| | B4 | Đối soát đêm, snapshot, cảnh báo lệch, runbook cutover | — | ⏳ |
| **C** (sau B) | C1 | Bảo hành và linh kiện trên Pancake: đơn bảo hành 0đ, `BH-MAYKHACH`, đơn đổi trả | — | ⏳ |

## 8. Nhật ký

- **2026-09-30**: Chốt mô hình: Pancake 3 kho vận hành, CRM sổ 2 cấp; nhập, chuyển, kiểm kê
  trên Pancake; đơn CRM (kể cả Foxia) đẩy sang Pancake.
- **2026-09-30**: Tách khỏi MR !1508; chia MR A/B/C; ghi các quyết định bổ sung ở §6b.

- **2026-09-30 — A1 xong (`d88ccec`).**
  - Đã làm:
    - **Ghép kho**: bảng `pancake_pos_warehouse_links` (warehouse_id UNIQUE ↔
      pancake_warehouse_id UNIQUE, tên Pancake cache, `is_default_for_push` — partial UNIQUE
      tối đa 1 kho mặc định, `priority` nhỏ = trước; dùng ở A2). Client `list_warehouses`.
      API `GET/PUT /api/pancake-pos/warehouse-links` (PUT thay toàn bộ trong 1 transaction:
      xoá rồi tạo → đổi chéo không vướng UNIQUE; kiểm trùng/nhiều mặc định/kho CRM không
      tồn tại → 400), `GET /api/pancake-pos/pancake-warehouses` (qua client, không lộ key).
      Kho CRM đọc bằng SQL (`infrastructure/stock_reader.py`), không import ORM app khác.
    - **Linh kiện (§6b, thay luật !1508)**: `SqlCatalogReader` kiểm cột `products.product_type`
      qua `pg_attribute` (1 lần/reader); có cột → SELECT, không → NULL. Cờ sysconfig
      `PANCAKE_POS_INCLUDE_COMPONENTS` (mặc định bật). Linh kiện vẫn theo luật tồn/bán, mẫu mã
      luôn `is_hidden=true`; nhóm TOÀN linh kiện gắn `category_ids=[id "Linh kiện"]` (tìm theo
      tên trong cây danh mục, không có thì tạo; id cache ở bảng nhỏ `pancake_pos_categories`;
      dry-run không tạo → `components.category=would_create`). Không có cột → báo cáo
      `components.status=unsupported` + log. Hash hàng hoá không đổi (`is_hidden=false` như cũ)
      → merge !1504 không kéo theo PUT hàng loạt.
    - **Tồn đầu kỳ**: `domain/initial_stock.py` (kế hoạch thuần) + `InitialStockPushService`:
      2 câu SQL (tồn `state='sellable'` ≠ 0 theo (kho, SKU) trong kho đã ghép; SKU đã có
      variation id); tồn thực Pancake (`variations_warehouses[].actual_remain_quantity`) — có
      `--sku` thì tìm theo SKU, không thì duyệt cả shop; vũ trụ dòng = SKU đã liên kết × kho đã
      ghép (không có dòng CRM = 0); âm → 0 + liệt kê; chỉ ghi dòng số đặt ≠ số Pancake hoặc chưa
      rõ trên Pancake → chạy lại = 0 dòng ghi (idempotent). Nhật ký `sync_runs` mode
      `initial_stock_dry` / `initial_stock`, report: scope, kho, totals, diffs (≤ 5000),
      negatives, missing_links, errors (lô), line_errors (dòng).
    - Lệnh `pancake_pos_initial_stock --dry-run --warehouse <id|mã> --sku` (khoá single_flight).
      Task `run_initial_stock` (queue `foxia`). API `POST /api/pancake-pos/initial-stock`
      (`dry_run` với warehouse_ids/skus; `apply` bắt buộc `dry_run_id` = lượt dry-run GẦN NHẤT,
      thành công, ≤ 30 phút — phạm vi lấy từ lượt đó; cờ ENABLED; 409 nếu đang chạy),
      `GET /api/pancake-pos/initial-stock/status`; `GET sync-runs?mode=` nhận nhiều mode.
    - UI trang Pancake POS: thẻ "Ghép kho CRM ↔ Pancake" (bảng kho CRM, select kho Pancake,
      ưu tiên, mặc định radio) + thẻ "Tồn đầu kỳ" (chọn kho/SKU, Chạy thử, Ghi — khoá tới khi
      có dry-run hợp lệ, hiện số phút còn lại; 5 lượt gần nhất; dialog báo cáo: tổng, bảng lệch
      lọc được + tải CSV có BOM/chặn công thức, âm→0, thiếu liên kết, dòng lỗi). Bảng lượt chạy
      đồng bộ SP chỉ lọc mode SP.
    - Test BE mới: `test_warehouse_links.py` (UNIQUE DB, 1 mặc định, validate, đổi chéo, API,
      quyền, kho Pancake), `test_components.py` (eligibility, payload, hash, có/không cột,
      cache danh mục, dry-run không tạo, cờ tắt, lỗi danh mục không chặn), `test_initial_stock.py`
      (kế hoạch thuần, SQL không N+1, dry-run không ghi, idempotent, lô 20/trần 100, lô 500 → chia
      từng dòng, dòng lỗi trong `results`, thiếu ghép = fatal, API dry-run/apply 409 các ca, lệnh),
      client (kho, danh mục phẳng, update_quantity, results, chống lặp trang). FE vitest logic
      `warehouse-initial-stock-logic.test.ts`.
  - **Phát hiện từ API thật (30/09, người điều phối kiểm)** → đã đổi thiết kế:
    - `POST variations/update_quantity` lô 100 dòng → HTTP 500; lô 1 và lô 20 chạy tốt
      (407/407 đọc lại khớp). `is_actual_remain_quantity=true` → `actual_remain_quantity` =
      `remain_quantity` = số gửi. ⇒ Lô mặc định **20** (`PANCAKE_POS_STOCK_BATCH_SIZE`, trần 100);
      lô nhiều dòng KHÔNG retry 5xx/ReadTimeout (lỗi ổn định) mà **chia nhỏ gửi từng dòng**; lô 1
      dòng retry như GET (đặt tuyệt đối = idempotent). 429 hết lượt thì bỏ cả lô (không chia).
    - Response `{"success": true, "results": [{"success": ..., "variation_id": ...}]}` — `success`
      gốc true vẫn có thể có dòng lỗi ⇒ client trả danh sách dòng lỗi, service ghi `line_errors`.
    - `GET marketplace/products` bỏ qua `page_size/page_number` (trả hết 1 trang); với
      `products/variations` chưa rõ ⇒ `iter_variations` chống lặp: trang > 1 không có mẫu mã mới
      thì dừng, không trả trùng id.
  - Lệch so với design / ghi chú:
    - Hai mode nhật ký (`initial_stock_dry`, `initial_stock`) thay vì một mode + cờ, để danh sách
      lượt (không nạp report) phân biệt được và tra "dry-run gần nhất" không cần lọc JSON.
    - Cột đếm `sync_runs` cho lượt tồn: `updated` = dòng cần ghi (dry-run) / đã ghi, `skipped` =
      SKU có tồn chưa liên kết, `errors` = dòng lỗi.
    - `mode` của API tồn là chuỗi (không enum) để drf-spectacular không đổi tên `ModeEnum` FE đang dùng.
    - Lệnh CLI ghi thật không đòi dry-run 30 phút (người vận hành chủ động); UI/API thì đòi.
  - Chưa làm / cần kiểm chứng bằng key thật:
    - `products/variations` có trả mẫu mã **ẩn** (linh kiện) không — nếu không, dòng của chúng
      luôn "chưa rõ trên Pancake" và luôn được ghi lại (vẫn đúng, chỉ tốn request).
    - Danh mục: `POST categories` trả `data.id` như spec; tên "Linh kiện" trùng khác hoa/thường
      được coi là một.
    - A2 (đẩy đơn) chưa làm; `priority`/`is_default_for_push` mới lưu, chưa dùng.

- **2026-09-30 — A2 xong (`adafa65`, đẩy đơn CRM → Pancake).**
  - **Số đo prod (read-only, 30/09)** — quyết định luật:
    - Nguồn đơn 90 ngày: landing 10.231, shopee 6.873, tiktok 4.728, messenger 3.231, zalo 546,
      (trống) 542, hotline 469, koc 352; **foxia 0** (kéo đơn Foxia chưa chạy: `foxia_order_links`
      = 0 dòng).
    - "Đã chốt": `OrderStatus` không có mốc chốt; `Order.status` do KiotViet/ĐVVC đẩy về nên đơn chốt
      chưa xuất luôn ở `0`. Mốc chốt là `order_assessments.sales_status_code` ∈ {closed,
      closed_express, created_order}; `created_order` = đã lên KiotViet (30 ngày: 100% có
      `invoice_code`). Đơn chốt chưa có hoá đơn 30 ngày: closed 26, closed_express 5,
      closed_scheduled_date 9. ~136 đơn chốt/ngày. ⇒ đủ điều kiện = sales ∈ {closed,
      closed_express, created_order} **và** status ∈ {0, 1, 11} ~~**và chưa có `invoice_code`**
      (đơn đã lên KiotViet không bao giờ đẩy → an toàn giai đoạn song song)~~ (bỏ 30/09 — xem
      mục "KiotViet chỉ xuất hoá đơn" bên dưới). `closed_scheduled_date`
      KHÔNG đẩy (hẹn ngày) — khi sale chuyển sang `closed` thì `sales_closed_at` không đổi (đi trong
      nhóm chốt), nên quét bắt thêm theo `order_assessments.updated_at` (2 ca/30 ngày chốt lại > 72h).
    - Landing ↔ Foxia: 2.352 cơ hội `FoxiaLeadLink` (từ 10/09): landing 1.682, messenger 528,
      hotline 75, zalo 67. Trễ tạo cơ hội: trung vị 0,9 phút, p90 1,7 phút (CRM đẩy **mọi** đơn
      thành cơ hội). 1.482 đơn có cơ hội vẫn do sale CRM chốt + xuất KiotViet; 36 chốt chưa xuất;
      789 chưa chốt. **Thành đơn Foxia: 0** (chưa kéo đơn) → **giao trùng thực tế: 0**, chưa đo được
      độ trễ cơ hội → đơn Foxia. ⇒ Luật: KHÔNG bỏ đơn landing chỉ vì có cơ hội Foxia (sẽ mất 63% đơn
      thật); chỉ bỏ khi cơ hội **đã thành đơn Foxia còn sống** (`foxia_lead_links.cohoi_id` =
      `foxia_order_links.cohoi_id`, đơn Foxia chưa huỷ/xoá). Đơn Foxia mà đơn gốc đã đẩy → bỏ +
      Lark. Cả hai ca: `skipped_duplicate` + cảnh báo Lark (khoá chống lặp theo đơn).
  - Đã làm:
    - Bảng `pancake_pos_order_links` (1 CRM ↔ N Pancake: UNIQUE (order, part_no), `pancake_order_id`
      UNIQUE; status pending/pushing/pushed/updated/cancelled/error/needs_routing/skipped_duplicate;
      `payload_hash`, `attempts`, `last_error`, `needs_lookup`, `items` của kiện tách, ảnh chụp kho;
      3 index: partial hàng đợi, partial đơn sống theo `pushed_at`, danh sách `-updated_at`) +
      `pancake_pos_status_map` seed 17 trạng thái Pancake (spec) ↔ `OrderStatus`, `locks_edit` cho
      13/8/9/2/3/16/4/15/5 (đã in trở đi).
    - Domain thuần `domain/order_push.py`: `evaluate` (nguồn cấu hình, chốt, chưa hoá đơn, cửa sổ,
      chống trùng, thiếu dòng/thiếu ghép SKU), `choose_warehouse` (priority nhỏ trước, cùng ưu tiên
      kho mặc định trước, combo nở mã lẻ), `validate_parts`/`split_lines` (tách kiện, dòng tặng giữ
      giá), `part_money` (giảm cấp đơn/trả trước/COD chia theo giá trị, tổng luôn khớp),
      `build_payload`, `plan_change`.
    - Payload: `bill_full_name`, `bill_phone_number` (SĐT đầy đủ), `shipping_address` **dạng chữ**
      (`address`/`full_address`, không `province_id` — CRM không có id địa giới Pancake; Pancake tự
      tách, cần kiểm bằng key thật), `warehouse_id`, items (`variation_id`, SL, `discount_each_product`,
      `retail_price`, dòng giá 0 = `is_bonus_product`), `shipping_fee=0` (phí ĐVVC CRM không thu
      khách), `total_discount`, `cod` = tổng sau giảm − trả trước, `transfer_money`, `order_sources`/
      `account` theo `PANCAKE_POS_ORDER_SOURCE_MAP`, `note` (CRM #id · nguồn · kênh · kiện · HỎA TỐC),
      `custom_id` = `<id>` / `<id>-<kiện>` (mã đơn hiển thị trên Pancake + mã tra lại; trước 06/10/2026 có tiền tố `CRM-`).
    - `OrderPushService` 3 pha: `discover` (quét / theo id → pending | needs_routing | error |
      skipped_duplicate), `push` (claim `FOR UPDATE SKIP LOCKED` + đặt `pushing` trước khi gọi;
      POST không retry 5xx/ReadTimeout → `error`+`needs_lookup`, lượt sau `GET orders?search=<SĐT>`
      lọc `custom_id` rồi mới tạo; 429 chắc chắn chưa tạo; `pushing` > 10 phút = worker chết → tra
      lại; 4xx tự thử 3 lần/5 phút), `sync_changes` (hash payload đổi hoặc CRM huỷ → GET trạng thái
      Pancake: chưa `locks_edit` → PUT / `status=6`; đã khoá → chỉ Lark; đã huỷ/xoá bên Pancake →
      `cancelled`).
    - Trigger: `OrderStatusChanged` + `OrderSaved` có `sales_closed_at` → task `push_orders_for`;
      beat `push_orders` 2 phút (single_flight). Cờ: chạy khi **cả** `PANCAKE_POS_ENABLED` và
      `PANCAKE_POS_PUSH_ORDERS_ENABLED` (mặc định **False**, staging cấm). Sysconfig thêm
      `PANCAKE_POS_PUSH_ORDER_SOURCES`, `PANCAKE_POS_ORDER_SOURCE_MAP`, `PANCAKE_POS_PUSH_LOOKBACK_HOURS` (72).
    - API: `GET order-links` (lọc nhiều trạng thái, tìm mã CRM/Pancake/tên), `POST order-links/{id}/retry`
      (`force=true` = "Vẫn đẩy" chỉ cho `skipped_duplicate`, log user), `GET/POST orders/{id}/routing`
      (tồn từng kho; chọn 1 kho hoặc tách ≤ 10 kiện, 409 nếu đã/đang đẩy), `GET orders/{id}/refs`
      (quyền Pancake POS **hoặc** quyền xem đơn); `status` thêm `push_orders_enabled`, `order_link_counts`.
    - UI: thẻ "Đẩy đơn CRM → Pancake" ở trang Pancake POS (lọc mặc định "Cần xử lý", đếm theo trạng
      thái, Thử lại / Vẫn đẩy (xác nhận) / Chọn cách xuất), dialog "Chọn cách xuất" (bảng tồn SKU × kho,
      một kho hoặc tách kiện, "Gợi ý theo tồn", báo phần còn phải chia); trang chi tiết đơn CRM hiện
      badge "Pancake: <mã>" (ẩn khi chưa đẩy/không quyền).
    - Test BE: `test_order_push_domain.py` (eligibility, chống trùng, chọn kho, combo, tách, tiền,
      payload, source map, plan_change), `test_order_push_service.py` (DB thật: chọn kho, needs_routing,
      combo, bỏ qua, quét hẹn ngày → chốt muộn, chưa ghép SKU/kho, idempotent discover, không N+1,
      landing ↔ Foxia 4 ca + Vẫn đẩy, mất response 5xx/ReadTimeout không tạo trùng, tra lại lỗi,
      4xx/429, 2 worker, pushing quá hạn, huỷ trước đẩy, tách N đơn, chọn kho chỉ định, sửa/huỷ trước
      và sau khi kho xử lý, GET lỗi, seed status map), `test_order_push_api.py` (quyền, lọc, refs,
      routing 400/409, thử lại/Vẫn đẩy, event chỉ xếp task khi bật cờ, job no-op khi tắt, client
      không retry POST 5xx/ReadTimeout, find lọc `custom_id`). FE vitest `order-push-logic.test.ts`.
  - Lệch so với design:
    - "Đã chốt" không dựa `OrderStatus` (§4.1 để ngỏ) mà dựa đánh giá sale (số đo trên); điều kiện
      "chưa hoá đơn KiotViet" đã bỏ (mục dưới).
    - Luật landing ↔ Foxia đổi từ "có cơ hội" sang "có đơn Foxia" (số đo trên); thêm "Vẫn đẩy" có xác nhận.
    - Thêm trạng thái `pushing` (claim trước khi gọi) ngoài danh sách brief.
    - Đơn tách kiện mà dòng hàng CRM đổi sau đó: không tự PUT (chia lại tiền/dòng không an toàn) → báo Lark, sửa tay.
  - Chưa làm / cần kiểm chứng bằng key thật:
    - `GET orders?search=<SĐT>` có trả `custom_id` không (nếu không, tra lại phải đổi sang lọc `note`).
    - Địa chỉ chữ có được Pancake tách tỉnh/huyện/xã để giao ĐVVC không; nếu cần id địa giới → thêm bảng ghép.
    - `PUT` có nhận đủ body như `POST` (items) và `status=6` là huỷ; giá trị `order_sources` của shop
      (`GET /order_source`) để điền `PANCAKE_POS_ORDER_SOURCE_MAP`.
    - Kéo đơn Foxia chưa chạy trên prod → luật trùng chưa có ca thật; đo lại độ trễ cơ hội → đơn Foxia sau khi bật.
    - Webhook trạng thái Pancake → CRM (`status_map.crm_status`) để MR B dùng; A2 chỉ GET trước khi PUT.

- **2026-09-30 — Sửa A2: KiotViet chỉ xuất hoá đơn, chạy song song với Pancake.**
  - Quyết định (người dùng xác nhận): sau cutover kho sang Pancake, **KiotViet vẫn dùng để xuất hoá
    đơn**. 100% đơn `created_order` có `invoice_code` ngay khi chốt ⇒ `invoice_code` KHÔNG còn là tín
    hiệu "đã xuất kho"; giữ luật cũ thì không đẩy được đơn nào.
  - Đã làm:
    - Bỏ điều kiện "chưa có `invoice_code`" ở `evaluate` và 2 câu quét (`_SCAN_CRM_SQL`,
      `_SCAN_FOXIA_SQL`); `CrmOrder` bỏ `invoice_code`. Đủ điều kiện = nguồn cấu hình + đánh giá sale
      ∈ {closed, closed_express, created_order} (vẫn loại `closed_scheduled_date`) + status ∈ {0, 1, 11}
      + chưa có liên kết + đủ ghép mẫu mã + luật chống trùng.
    - Chống đẩy dồn đơn cũ: sysconfig `PANCAKE_POS_PUSH_ORDERS_SINCE` (kiểu mới `datetime`, kiểm hợp lệ
      khi lưu; thiếu múi giờ = giờ hệ thống UTC+7). Trống = lần **đầu** bật
      `PANCAKE_POS_PUSH_ORDERS_ENABLED` theo `system_setting_changes` (`sysconfig.first_enabled_at`).
      Không có cả hai (cờ bật bằng env) → chỉ còn cửa sổ `PANCAKE_POS_PUSH_LOOKBACK_HOURS`.
    - So mốc với **mốc chốt** (`first_closed_at` = `sales_closed_at`; đơn Foxia = `created_at`), không
      với lần sửa đánh giá gần nhất → đơn chốt trước mốc rồi sửa đánh giá sau vẫn không đẩy (lý do
      `before_push_since`). Cửa sổ quét SQL = max(lookback, mốc). Người vận hành chủ động (Thử lại /
      Chọn cách xuất) bỏ qua mốc như bỏ qua lookback.
    - Sửa/huỷ sau khi đẩy vốn đã dựa trạng thái Pancake (GET order + `status_map.locks_edit`), không
      dùng `invoice_code` → không phải đổi.
    - Test: đơn có `invoice_code` vẫn đẩy; đơn chốt trước mốc (kể cả vừa sửa đánh giá) không đẩy, đẩy tay
      thì được; mốc lấy từ cấu hình / lần đầu bật cờ; sysconfig kiểu `datetime` + `first_enabled_at`.
  - Hệ quả / còn để ngỏ:
    - Đơn hẹn ngày (`closed_scheduled_date`) chốt trước mốc, tới hạn sau mốc → không tự đẩy (mốc chốt
      cũ); kho dùng "Chọn cách xuất" để đẩy tay.
    - `Order.status` vẫn do webhook KiotViet cập nhật: nếu KiotViet đổi trạng thái hoá đơn (vd sang
      đang giao) trước khi CRM kịp đẩy thì đơn rơi khỏi {0, 1, 11} — cần theo dõi sau cutover.

- **2026-09-30 — B1 xong (`29564f4` (trên nhánh D: `b80f924`), webhook Pancake → CRM + sự kiện đơn).**
  - **Phát hiện từ API thật (GET read-only, 300 đơn gần nhất, 30/09)**:
    - Đơn sàn: `id` Pancake = **mã đơn sàn** (Shopee 14 ký tự, TikTok 18 số); `order_sources` =
      `marketplace_id` = "-3" Shopee / "-9" TikTok ("-1" Facebook, `GET /order_source`). KiotViet lưu
      cùng mã có tiền tố: `invoice_code` `HDSPE_/HDTTS_<mã>`, `order_code` `DHSPE_/DHTTS_<mã>` (prod
      30 ngày: 5.045/5.052 đơn sàn có mã) → khớp mã sàn chính xác, không cần trường riêng.
    - `account_name` = tên shop, trùng tên (hoặc mã slug) kênh CRM với 6/7 shop; "DELAX Việt Nam" (TikTok)
      đang về kênh KiotViet cũ → cần `PANCAKE_POS_INBOUND_CHANNEL_MAP` nếu bật tạo đơn.
    - `status_name` là mã tiếng Anh (`submitted`, `shipped`, `delivered`, `returning`, `canceled`,
      `wait_print`); `status` khớp đúng 17 giá trị seed `status_map` (không cần sửa seed). `sub_status`
      luôn null trong mẫu.
    - Mã vận đơn `partner.extend_code` (rỗng tới khi ĐVVC nhận), ĐVVC `partner.partner_name`
      ("Giao hàng nhanh", "Shopee Xpress", "J&T", "Best Inc", "VTP"…) → map sang mã `shipping_provider`
      CRM (GHN/SPX/J&T/BEST/VTP). Thời gian không kèm múi giờ = UTC.
    - **Dòng hàng đơn sàn có `variation_id = null`** (sàn chưa ghép SKU trên Pancake — bước 5 checklist
      cutover) → tạo đơn tra SKU `variation_info.display_id`, không có thì bỏ dòng + ghi lý do.
    - Spec không có `GET /shops/{id}` → không đọc được cấu hình webhook hiện tại; `GET /shops` không
      trả trường webhook.
  - Đã làm:
    - Bảng `pancake_pos_webhook_events` (loại, `record_id`, `record_version`, payload jsonb, hash,
      status pending/processed/ignored/error, error, attempts; UNIQUE (loại, record_id, hash); index
      partial hàng đợi + `received_at`) và `pancake_pos_inbound_orders` (1 dòng / đơn Pancake: đơn CRM
      khớp, `match_status` matched/waiting/unmatched/ignored, `match_method`, ứng viên, lý do,
      `pancake_updated_at` = mốc thứ tự, mốc gửi/giao/hoàn từ `status_history`, mã vận đơn, ĐVVC;
      index partial "chờ khớp"). `order_links` thêm trạng thái `linked` (đơn gốc Pancake,
      origin `pancake_native`) — ngoài `LIVE_STATUSES` nên `sync_changes` A2 không PUT ngược.
    - Endpoint `POST /api/pancake-pos/webhook` (không JWT; header `X-Desi-Webhook-Secret` so hằng thời
      gian với `PANCAKE_POS_WEBHOOK_SECRET`; secret trống/< 16 ký tự → 503, sai → 401; > 2 MB → 413;
      throttle riêng `pancake_pos_webhook` 30k/giờ/IP; nhận object hoặc mảng ≤ 100). Loại suy từ hình
      dạng payload (Pancake không gửi header loại). Lưu thô → 200 `{stored, duplicate}`; sự kiện đơn
      xếp task `process_webhook_event` sau commit. Metric `desi_pancake_pos_webhook_received_total
      {event_type,result}` + `…_processed_total{event_type,status}`.
    - Worker (`InboundOrderService`): mỗi sự kiện một transaction, khoá dòng inbound → cùng đơn chạy
      nối tiếp; bản `updated_at` cũ hơn bản đã áp → `ignored`. Khớp: liên kết A2 (`pancake_order_id`,
      hoặc `custom_id` `<id>` khi response POST chưa kịp lưu) → mã đơn sàn → mã vận đơn → tạo đơn
      (cờ). Khớp nhiều đơn / đơn CRM đã đẩy mã Pancake khác (nghi trùng) → `unmatched` ngay; không khớp
      quá `PANCAKE_POS_INBOUND_UNMATCHED_HOURS` (6) → `unmatched`; cả hai báo Lark (khoá theo đơn).
      Đơn huỷ/xoá chưa khớp → `ignored`. Beat 1 phút `process_webhook_events`: vớt pending/lỗi (≤ 5
      lượt, cách 5 phút), khớp lại đơn chờ (đơn KiotViet về sau webhook), dọn sự kiện đã xử lý > 30 ngày.
    - Ghi đơn CRM: trạng thái theo `status_map` (đơn tách N kiện: kiện chậm nhất — chỉ "Đang giao"
      khi mọi kiện đã gửi; kiện huỷ bỏ qua); mã vận đơn + ĐVVC (đơn 1 kiện). CRM đã huỷ mà Pancake chưa
      xử lý kho → giữ huỷ (lệnh huỷ A2 đang sang). `orders` không có service ghi trạng thái cho luồng
      hệ thống → adapter theo đúng khuôn KiotViet webhook / nhập hoàn: khoá dòng, UPDATE cột đổi +
      `updated_at`, `OrderStatusChanged(source="pancake_pos")` sau commit (thêm
      `StatusChangeSource.PANCAKE_POS`, nhãn journey "Pancake POS").
    - Tạo đơn (cờ `PANCAKE_POS_INBOUND_CREATE_ORDERS`, mặc định **tắt** — đơn sàn còn về qua
      KiotViet): `order_source` theo `PANCAKE_POS_INBOUND_SOURCE_MAP` (-3 shopee, -9 tiktok, -1
      messenger), kênh theo `PANCAKE_POS_INBOUND_CHANNEL_MAP` rồi tên/mã kênh; khách theo SĐT (SĐT sàn
      bị che → tạo khách giữ chuỗi thô như import KiotViet); dòng hàng theo variation đã ghép rồi SKU;
      `bulk_create` (không post_save / order.created — ràng buộc Foxia); `external_order_id` = id
      Pancake; liên kết `pancake_native`.
    - Cờ `PANCAKE_POS_INBOUND_UPDATE_KIOTVIET_ORDERS` (mặc định tắt): đơn đã có hoá đơn KiotViet chỉ
      được ghép + lưu mốc, KHÔNG ghi trạng thái (KiotViet còn đồng bộ → hai nguồn giằng co). Bật lúc
      cutover.
    - Cấu hình webhook: lệnh `pancake_pos_webhook show|configure [--dry-run] [--url] [--types] [--yes]`
      + API `POST webhook/configure` (`dry_run` mặc định; ghi thật cần `PANCAKE_POS_ENABLED`, quyền
      edit) → `PUT /shops/{id}` `{shop: {webhook_enable, webhook_url, webhook_types:[orders, products,
      variations_warehouses], webhook_headers: {X-Desi-Webhook-Secret}}}`; secret luôn che trong output.
      API `GET webhook/status`, `POST webhook/retry-errors`, `GET inbound-orders`, `POST
      inbound-orders/{id}/resolve` (match + order_id / ignore).
    - UI trang Pancake POS: thẻ "Webhook" (URL/secret/cờ, bảng sự kiện 24h loại × trạng thái, lỗi gần
      nhất + "Xử lý lại sự kiện lỗi", "Xem cấu hình sẽ gửi" / "Cấu hình trên Pancake" có xác nhận), thẻ
      "Chờ khớp đơn Pancake" (lọc, ứng viên, khớp tay theo mã CRM, bỏ qua). Trạng thái liên kết `linked`
      ở thẻ đẩy đơn.
    - `variations_warehouses` / `products`: chỉ lưu thô `pending` (worker không lấy) — chờ B2.
    - Test BE: `test_inbound_domain.py` (phân loại, snapshot theo payload thật, mã sàn, `custom_id`,
      thứ tự, mốc, gộp kiện, luật huỷ, dựng đơn), `test_inbound_service.py` (DB thật: liên kết A2 + event,
      idempotent + bản cũ, `custom_id`, giữ huỷ, tách kiện, mã sàn cờ tắt/bật, mã vận đơn, nhiều ứng
      viên + Lark, nghi trùng, chờ → quá hạn, khớp lại, huỷ chưa khớp, tạo đơn cờ bật (SKU + variation,
      không tạo lần 2), thiếu kênh, map kênh, lỗi → error/attempts + savepoint, khớp tay),
      `test_webhook_api.py` (503/401/413/400, chống trùng + xếp task, tồn/sản phẩm pending, quyền, trạng
      thái, cấu hình dry-run che secret / 400 / 409 / ghi thật, client PUT, xử lý lại lỗi, chờ khớp,
      lệnh). FE vitest `webhook-logic.test.ts`.
  - Lệch so với design / ghi chú:
    - `OrderSource` không có `facebook` → đơn page Pancake map `messenger` (cấu hình được); thêm enum
      mới ảnh hưởng báo cáo toàn hệ thống nên không làm ở B1.
    - Thêm cờ `PANCAKE_POS_INBOUND_UPDATE_KIOTVIET_ORDERS` (ngoài brief) để giai đoạn song song không
      giằng co trạng thái với KiotViet.
    - Dedupe theo hash payload (không theo `updated_at`): hai bản cùng `updated_at` khác nội dung vẫn
      được lưu; thứ tự áp do `pancake_updated_at` (bằng nhau vẫn áp — idempotent).
    - Mốc gửi/giao/hoàn lưu ở `pancake_pos_inbound_orders` (CRM `orders` không có cột riêng).
  - Chưa làm / cần kiểm chứng bằng key thật:
    - Chưa gọi `PUT /shops/{id}`: kiểm PUT có giữ `webhook_email` khi không gửi; Pancake có gửi đúng
      header tự đặt; webhook đơn có bọc `data` hay phẳng như spec (bộ phân loại nhận cả hai).
    - Webhook `orders` có kèm `status_history` / `partner` đầy đủ không (thiếu thì mốc suy từ trạng thái).
    - Tạo đơn khi cờ bật chưa chạy dữ liệu thật; SĐT sàn bị che → mỗi đơn một khách mới (như KiotViet).
    - B2: xử lý `variations_warehouses` (đang tích `pending`), chống vòng lặp; hoàn một phần.
- **2026-10-01 — MR D: trạng thái vận chuyển thực từ Pancake** (nhánh `feat/pancake-pos-delivery-status`,
  trên !1509 + cherry-pick B1 `29564f4`; KHÔNG có !1504).
  - Quyết định người dùng: `Order.status` do KiotViet; Pancake ghi `partner_delivery_status` /
    `partner_dvvc_delivery_status` và mã "Trạng thái vận chuyển thực" (`delivery_business_status_code`)
    — thiết kế §5.4.
  - Phát hiện từ API thật (GET orders read-only, 800 đơn cập nhật gần nhất, 01/10):
    - `status` gặp: 3 Đã nhận 494, 2 Đã gửi 126, 4 Đang hoàn 88, 12 Chờ in 42, 6 Huỷ 33, 1 Đã xác
      nhận 13, 0 Mới 4. `sub_status` luôn null; `status_history` có ở mọi đơn.
    - `partner.partner_status` chỉ có ở ~43% đơn (Shopee Xpress, VTP, một số GHN): `delivered`,
      `on_the_way`, `request_received`, `returning`, `cancelled` (spec ghi `canceled`).
    - Đơn TikTok (GHN/J&T/Best) để `partner_status` null, hành trình ở `extend_update[]` mới nhất
      đứng đầu, khoá thật `action_code`/`description`/`status`/`updated_at`/`update_time_millis`
      (spec ghi `key`/`update_at`). `status` là câu tiếng Anh kèm địa danh, có khi kèm **SĐT tài
      xế** → không lưu nguyên văn; nhãn theo `action_code` (bảng ở `domain/delivery_status.py`).
    - Kho: 100% mẫu `warehouse_info.name` = "Đại Mỗ 1".
  - Đã làm: sửa luồng B1 (bỏ ghi `status`, bỏ cờ `PANCAKE_POS_INBOUND_UPDATE_KIOTVIET_ORDERS`, bỏ
    `StatusChangeSource.PANCAKE_POS`), migration `0007_delivery_status` (cột kiện + 3 index partial
    /thường + `status_map.crm_delivery_code` seed), `DeliverySyncService` + task
    `sync_delivery_code`, `journey_source` cho `update_order_inline`/`dispatch_orders_batch`
    (orders), báo cáo + beat `alert_stuck_deliveries`, 3 API, lệnh backfill, FE (dòng phụ cột,
    mục chi tiết đơn, 2 thẻ, cờ xuất kho ở thẻ Webhook). Sysconfig: thêm
    `PANCAKE_POS_DISPATCH_ENABLED`, `PANCAKE_POS_STUCK_NOT_SENT_HOURS`,
    `PANCAKE_POS_STUCK_NOT_DELIVERED_DAYS`, `PANCAKE_POS_STUCK_ALERT_HOUR` (100 khoá).
  - Lệch / ghi chú:
    - Theo đúng yêu cầu "cờ tắt không đụng đơn đã `dispatched`": trước cutover Pancake Đã nhận
      KHÔNG ghi `received` lên đơn kho đã quét trên CRM (an toàn tồn, nhưng mã kết thúc chỉ có sau
      khi bật cờ). Nới được: `dispatched → received/not_received` không động tồn.
    - Ghi mã qua `update_order_inline` có đẩy `orders.updated_at` → đơn A2 được đối chiếu lại (GET
      Pancake, thường không PUT). Ghi `partner_*` thì không.
    - Không thêm filter danh sách theo `partner_delivery_status` (người dùng chốt dùng filter
      "Trạng thái vận chuyển thực" có sẵn) → không thêm index trên `orders`.
  - Chưa làm / cần kiểm chứng: webhook `orders` thật có kèm `partner.extend_update` không (thiếu
    thì giữ giá trị ĐVVC đã biết); bật cờ xuất kho chỉ sau cutover + B2 coi xuất bán là echo.
