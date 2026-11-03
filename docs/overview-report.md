# Báo cáo tổng (Overview Report)

**Trạng thái:** Sprint 6 hoàn thành (2026-08-11).

## Mục tiêu

Trang báo cáo tổng hợp toàn công ty cho ban lãnh đạo: doanh thu đã CHỐT theo
trạng thái sale, doanh thu theo kênh bán, chi phí Ads, giá trị hoàn/rủi ro hoàn
— lọc theo khoảng thời gian (preset + khoảng ngày tùy chọn) như trang Báo cáo
Ads.

## Layout

```
┌────────┬─────────┬─────────┬──────────┐
│ Tổng DT│ DT thuần│ Giao TC │ Chờ giao │            ← hàng KPI 1: tiền vào
└────────┴─────────┴─────────┴──────────┘
┌────────┬────────────┬────────┬─────────┐
│ Hoàn   │ Rủi ro hoàn│ CP FB  │ CP Google│           ← hàng KPI 2: tiền mất + tiền chi
└────────┴────────────┴────────┴─────────┘
┌─────────────────────────────────────────────┐
│ Cột xếp chồng — Doanh thu thuần theo TG     │  ← tab Theo ngày · giờ · thứ
└─────────────────────────────────────────────┘
┌──────────────┬──────────────┬──────────────┐
│ Donut        │ Donut        │ Donut        │
│ Doanh thu    │ Hoàn         │ Rủi ro hoàn  │        ← 3 khối theo kênh bán
│ theo kênh bán│ theo kênh bán│ theo kênh bán│
│ + bảng ▾     │ + bảng ▾     │ + bảng ▾     │
└──────────────┴──────────────┴──────────────┘
```

Dưới 3 khối theo kênh bán là 2 khối phân bổ địa lý của Tổng doanh thu, rồi
DƯỚI CÙNG là khối Top sản phẩm (thêm 2026-09-03):

```
┌───────────────────────┬───────────────────────┐
│ Donut theo vùng miền  │ Bar ngang top 10 tỉnh │
│ + bảng ▾              │ + bảng đầy đủ ▾       │
└───────────────────────┴───────────────────────┘
┌───────────────────────────────────────────────┐
│ Bar ngang top 10 SP theo DT thuần             │
│ + bảng đầy đủ có phân trang ▾                 │
└───────────────────────────────────────────────┘
```

Mỗi khối = donut tỉ trọng (legend kèm giá trị + %) và `CollapsibleTable` xổ bảng
chi tiết có dòng Tổng — cùng khuôn với khối phân bổ ở trang Báo cáo Ads.

Riêng khối tỉnh/thành cố ý dùng **bar ngang một màu** thay vì donut: 60+ tỉnh
thì donut không đọc được, và so sánh độ lớn giữa nhiều hạng mục là việc của bar
chart; mỗi cột đã có nhãn tên tỉnh nên màu không cần mang thông tin. Biểu đồ vẽ
top 10, bảng chi tiết vẫn liệt kê đủ mọi tỉnh.

Cột bảng: khối doanh thu có `Số đơn · Doanh thu · Đơn giao TC · DT giao TC`;
khối hoàn/rủi ro hoàn thay 2 cột giao TC bằng `% trên tổng` (đơn hoàn
`status ∈ {4,5,12,13}` và đơn rủi ro hoàn `status 10` không thể đồng thời là
đơn giao thành công `status 3`).

### Màu và thứ tự lát

Nguồn có **màu cố định** (`source-colors.ts`) và các lát giữ **thứ tự cố định**
theo `OVERVIEW_SOURCES`, không sort theo giá trị: một nguồn luôn cùng màu, cùng
vị trí ở cả 3 donut nên so sánh giữa các khối mới đọc được (màu theo entity,
không theo thứ hạng).

Bộ màu lấy từ `ENTITY_PALETTE` nhưng **đặt lại thứ tự** để các lát cạnh nhau
phân biệt được; thứ tự hiện tại pass toàn bộ kiểm tra bảng màu phân loại ở light
mode (deutan/protan ΔE 8.9, thị lực thường ΔE 32.3). Đổi màu hoặc đổi thứ tự
nguồn thì phải validate lại.

Hạn chế đã biết: ở dark mode 3 màu (`#f59e0b`, `#22c55e`, `#06b6d4`) hơi sáng so
với dải khuyến nghị — kế thừa từ `ENTITY_PALETTE` dùng chung toàn app (dashboard,
Báo cáo Ads cũng vậy). Sửa đúng cách là làm bảng màu theo từng theme ở mức app,
không vá riêng trang này.

## Định nghĩa số liệu

Tập đơn cơ sở **giữ nguyên**: `Order.deleted_at IS NULL`, loại chi nhánh nội bộ
(`EXCLUDED_BRANCH_IDS`) **và kho Đại Mỗ** (`OVERVIEW_EXCLUDED_BRANCH_IDS` =
1000000477 — user chốt 2026-07-20 không tính vào doanh thu công ty, khớp báo
cáo bán hàng KiotViet/Lark vốn không gồm kho này; các báo cáo khác không đổi),
loại clone khôi phục còn sống (clone kế thừa `created_at` của đơn gốc nên rơi
vào cùng kỳ — không loại thì đơn hủy gốc và clone cùng cộng vào doanh thu, đếm
đôi), lọc theo **ngày tạo đơn** (`created_at`, múi giờ +07,
`_apply_created_at_date_filters`), lọc nhóm kênh bán và lọc sản phẩm không đổi
(xem 2 mục dưới).

### Phạm vi đơn — gốc doanh thu là HÓA ĐƠN KIOTVIET (2026-08-20)

Lịch sử ba chặng: trước Sprint 6 tính theo *hóa đơn KiotViet*; Sprint 6
(2026-08-11) đổi sang *trạng thái sale*; từ 2026-08-20 quay về mốc hóa đơn
nhưng ở dạng đơn giản nhất — **mọi đơn đã lên hóa đơn KiotViet, trừ đơn hủy**:

```
TỔNG DOANH THU = đã lên hóa đơn KiotViet (`~LEAD_ORDER_Q`, tức invoice_status IS NOT NULL)
               \ đơn hủy (status = 6 ∨ invoice_status = 2, dùng lại `CANCELLED_ORDER_Q`)
```

Trong code đúng một dòng: `total_q = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q`.

**Vì sao bỏ gốc trạng thái sale** (`OVERVIEW_CLOSED_SCOPE_Q`, đã gỡ khỏi
`shared.py`): doanh thu công ty không nên phụ thuộc việc đội sale có gắn trạng
thái hay chưa, và luật cũ loại oan đơn đã có hóa đơn nhưng mang trạng thái sale
ngoài 4 mã chốt (vd "Khách bom hàng"). Luật mới có một điều kiện thay vì hai vế
chồng nhau.

**Số đo prod trước khi đổi (20/08/2026), tháng 8 (01–20/08):**

| Định nghĩa | Đơn | Tổng doanh thu |
|---|---|---|
| Gốc trạng thái sale (cũ) | 4.240 | 6,473 tỉ |
| Gốc hóa đơn KiotViet (mới) | 4.205 | **6,415 tỉ** |
| Chênh | −35 | **−58 triệu (−0,9%)** |

Phần rớt ra là lead sale đã chốt nhưng chưa kịp lên hóa đơn — nay không còn vào
doanh thu. Đây là đánh đổi đã biết và user chấp nhận.

**KHÔNG cần thêm điều kiện `status != 0`.** Mã 0 (Mới) là trạng thái nội bộ của
hệ thống mình, không phải mã của KiotViet; đơn đã lên hóa đơn luôn mang mã thật
của KiotViet. Đo prod tháng 8: **0 đơn** vừa có hóa đơn vừa `status = 0`. Thêm
điều kiện đó vào code chỉ tạo ảo giác rằng nó đang chặn cái gì đó.

**Một phương án đã cân nhắc và LOẠI:** lấy "mọi trạng thái giao hàng trừ hủy"
(bỏ luôn mốc hóa đơn). Đo prod tháng 8: tổng nhảy lên 7,81 tỉ, **+20,4%** — phần
tăng là 810 lead chưa lên hóa đơn, trong đó spam 375 tr, cần tư vấn thêm 268 tr,
không mua 257 tr, trùng đơn 50 tr, sai số điện thoại 21 tr. Đó là tiền chưa từng
chốt; ngoài ra rổ "Chờ giao" sẽ phình bằng lead không bao giờ giao (đều mang
`status = 0`), làm mất nghĩa cả % chờ giao lẫn % chi phí Ads.

`LEAD_ORDER_Q` giờ là ranh giới DUY NHẤT của doanh thu — đổi hằng số đó là đổi
mẫu số của mọi ô % trên trang. Cơ chế chống phồng số 2026-07-20 nhờ vậy trở lại
dạng mạnh nhất: không lead nào vào doanh thu, bất kể trạng thái sale.

### Bốn rổ kết quả

Sau khi loại đơn hủy, 13 mã trạng thái giao hàng còn lại (mọi `OrderStatus`
trừ `CANCELLED = 6`) chia thành 4 rổ RỜI NHAU, không sót mã nào:

| Ô KPI | Trạng thái | Hằng số |
|---|---|---|
| Doanh thu giao thành công | 3 | `DELIVERED_SUCCESS_ORDER_STATUS` |
| Doanh thu chờ giao | 0, 1, 2, 7, 8, 9, 11 | `AWAITING_DELIVERY_ORDER_STATUSES` |
| Hoàn | 4, 5, 12, 13 (đang chuyển hoàn / đã hoàn / chờ lấy hàng hoàn / chờ xác nhận hoàn) | `RETURN_ORDER_STATUSES` |
| Rủi ro hoàn | 10 (Chờ giao lại — đơn đã quay đầu một lần) | `RETURN_RISK_ORDER_STATUSES` |

`AWAITING_DELIVERY_ORDER_STATUSES` khai báo bằng PHÉP TRỪ trên toàn bộ
`OrderStatus` trong Python (mọi status trừ giao thành công, trừ rổ Hoàn, trừ
rổ Rủi ro hoàn, trừ Hủy), không liệt kê tay — thêm trạng thái `OrderStatus`
mới về sau thì nó tự rơi vào rổ này thay vì lọt khỏi doanh thu. CỐ Ý không tái
dùng `IN_DELIVERY_ORDER_STATUSES` (nhóm 5a, gộp cả mã 10 vào "đang giao") vì ở
đây mã 10 phải tách riêng thành ô Rủi ro hoàn: đơn đã quay đầu một lần thì
nguy cơ thành hoàn cao hơn hẳn đơn đang giao bình thường.

**Bất biến:** `giao thành công + chờ giao + hoàn + rủi ro hoàn = Tổng doanh
thu`, đúng cả về tiền lẫn số đơn, với mọi tổ hợp bộ lọc. Test
`test_four_buckets_sum_to_total_revenue` và
`test_four_buckets_partition_every_order_status` chốt bất biến này — cái sau
bắt được cả trường hợp thêm mã `OrderStatus` mới mà quên xếp rổ.

### Mẫu số của % Hoàn / % Rủi ro hoàn (user chốt 2026-08-17)

Hai ô này KHÔNG chia cho Tổng doanh thu mà chia cho **doanh thu đã có kết quả
giao**:

```
settledRevenue = total_revenue − awaiting_revenue
               = delivered_revenue + returned_revenue + return_risk_revenue

% hoàn        = returned_revenue   / settledRevenue
% rủi ro hoàn = return_risk_revenue / settledRevenue
```

Lý do: cả 7 mã của rổ Chờ giao (0 Mới, 1 Chờ xử lý, 2 Đang giao hàng, 7 Đang lấy
hàng, 8 Chờ lấy lại, 9 Đã lấy hàng, 11 Chờ chuyển hàng) đều **chưa có kết quả
với khách**. Để chúng trong mẫu số làm % hoàn tụt xuống giả tạo, lệch mạnh nhất
ở đầu kỳ khi phần lớn đơn còn trên đường.

Tính TOÀN BỘ ở frontend (`computeOverviewKpis`) bằng phép TRỪ trên payload sẵn
có — không thêm field BE, không thêm query. Trừ (chứ không cộng 3 rổ kia) là cố
ý: mã trạng thái lạ do KiotViet thêm mới rơi vào rổ Chờ giao ở BE và tự động bị
loại khỏi mẫu số, không phải sửa FE.

**Bất biến mới:** `deliveredSettledPct + returnedPct + returnRiskPct = 100%`.
Bốn ô % của hàng KPI thì KHÔNG cộng thành 100% nữa — trang TỪNG có dòng chú thích ngay dưới
hàng tile để người đọc không tự cộng rồi thắc mắc.

Kỳ mà mọi đơn còn đang trên đường → `settledRevenue = 0` → hai ô hiện `—` thay
vì chia 0. Ô Chờ giao vẫn đọc được vì mẫu số của nó là Tổng doanh thu.

**Hệ quả khi deploy:** % hoàn hiển thị sẽ TĂNG so với trước (mẫu số nhỏ đi) —
không phải hồi quy. Con số cũ và mới không so sánh trực tiếp được.

- **Tổng doanh thu** (hàng 1) = `sum(amount)` của `đơn đã có hóa đơn \ đơn hủy`
  (`amount` = `total_after_discount`, hoặc tiền riêng sản phẩm khi có lọc SP —
  xem mục Bộ lọc sản phẩm).
- **Doanh thu giao thành công** = đơn `status=3` — tập con của Tổng doanh thu.
- **Phần trăm — HAI mẫu số** (user chốt 2026-08-17, xem mục dưới): ô Giao thành
  công / Chờ giao / hai ô Chi phí Ads chia cho **Tổng doanh thu**; ô **Hoàn** và **Rủi
  ro hoàn** chia cho **Doanh thu đã có kết quả giao** = Tổng doanh thu − rổ Chờ
  giao. Vì thế bốn ô % KHÔNG còn cộng lại đúng 100%.
- **Phân bổ theo NHÓM KÊNH BÁN — NĂM nhóm** (user chốt 2026-08-20, thay 2 nhóm
  nội/ngoại của 2026-07-20). Mỗi bucket chia đúng 5 nhóm, khớp LẦN LƯỢT và
  dừng ở nhánh đầu tiên đúng:

  | Khóa | Nhãn | Nhận diện |
  |---|---|---|
  | `shopee` | Shopee | `order_source = shopee` **hoặc** mã hóa đơn prefix `HDSPE_` |
  | `tiktok` | TikTok | `order_source = tiktok` **hoặc** mã hóa đơn prefix `HDTTS_` |
  | `facebook` | Facebook | code kênh bắt đầu bằng `facebook` |
  | `web` | Web | code kênh `google-ads-web` (prod: channel id 7) |
  | `off_other` | Ngoại sàn khác | phần còn lại — Resale, KOC, bán tại quầy KiotViet |

  **NHÓM THEO TẦNG** (user chốt 2026-08-23, thay cho việc luôn hiện đủ 5 nhóm):
  biểu đồ chia theo đúng tầng dưới của thứ đang lọc, không sâu hơn.

  | Ô "Nguồn" | Nhóm hiển thị |
  |---|---|
  | Tất cả | Nội sàn · Ngoại sàn |
  | Nội sàn | Shopee · TikTok |
  | Ngoại sàn | Facebook · Web · Ngoại sàn khác |

  Ô **"Kênh bán"** (thêm 2026-08-24) chọn MỘT nhóm trong tầng đó — khi đó cả 3
  donut lẫn biểu đồ diễn biến co lại còn đúng nhóm ấy, một lát 100%.

  KHÔNG có SQL điều kiện: annotate vẫn luôn là `Case` 5 nhánh như bảng dưới,
  tầng "Tất cả" gộp lên ở Python bằng `SALE_CHANNEL_ROLLUP`. Khi đã lọc nội
  hoặc ngoại sàn thì `base` đã loại sẵn phía kia nên chỉ còn đúng các nhóm con.
  Một đường tính duy nhất, và biểu đồ diễn biến dùng CHUNG `Case` đó nên hai
  biểu đồ trên cùng màn hình không bao giờ chia nhóm khác nhau.

  `SALE_CHANNEL_GROUPS` (5 nhóm lá) không còn được dùng để sắp lát nữa, nhưng
  GIỮ LẠI làm khoá: `OverviewSaleChannelLevelTests` bắt buộc mọi nhóm lá phải
  thuộc đúng một tầng con, nên thêm nhóm thứ 6 mà quên xếp tầng là đỏ ngay.

  **Thứ tự khớp là nghiệp vụ, không phải cú pháp: sàn xét TRƯỚC kênh.** Kênh
  `delax-chinh-hang` mang 786 đơn Shopee và prefix `HDSPE_` xuất hiện trên 6
  kênh khác nhau (đo prod 08/2026) — đảo thứ tự là gán nhầm ~1,8 tỉ doanh thu
  Shopee sang Facebook mà không có gì báo lỗi. Test
  `test_marketplace_wins_over_channel` chốt điều này.

  Dùng cả prefix mã hóa đơn để nhận sàn vì đơn sàn đồng bộ về vẫn có thể sót
  `order_source` — 2026-07 có 3 đơn như vậy.

  **Facebook/Web nhận theo CODE kênh, KHÔNG theo id.** Lý do đã ghi sẵn ở
  `Channel.is_koc`: id channel sinh tự động nên khác nhau giữa các môi trường,
  code thì ổn định. Dùng id chính là cái bẫy đã cắn báo cáo Ads (id hard-code
  KOC=15, SALE_FB=24). Hệ quả tốt: mở kênh `facebook-<tên>` mới là kênh đó TỰ
  vào nhóm Facebook, không phải nhớ sửa danh sách nào.

  **`off_other` BẮT BUỘC phải có.** Tháng 8/2026 nó gom 167 đơn ≈ 306 triệu
  (Resale 120tr, KOC 99tr, hóa đơn KiotViet tại quầy 87tr) — không có nó thì
  tổng 3 khối biểu đồ không còn bằng ô KPI, đúng cái bất biến đã vi phạm 2 lần
  và cả 2 lần đều lọt tới tay user.

  Thứ tự lát cố định nội → ngoại (không sort theo giá trị) nên một nhóm luôn
  cùng vị trí, cùng màu ở cả 3 biểu đồ.

- **Phân bổ địa lý**: cơ sở giờ là **Tổng doanh thu** (gồm cả hoàn/rủi ro hoàn
  — trước đây chỉ tính bucket "net"). Vùng miền gom theo
  `apps/orders/vietnam_regions.py` — **7 vùng**, trong đó Đông Bắc gộp chung Tây
  Bắc thành "Trung du & miền núi phía Bắc" (chốt business 2026-07-20).

  Tỉnh của một đơn lấy theo thứ tự: `Order.province` → suy từ
  `Order.kiotviet_location` ("&lt;Tỉnh&gt; - &lt;Quận/Huyện&gt;") → nhóm
  **"Chưa có địa chỉ"**. Không đơn nào bị loại vì thiếu địa chỉ, nhờ vậy tổng 2
  khối địa lý LUÔN bằng ô **Doanh thu thuần** — có test chốt bất biến này.

  **CƠ SỞ CỦA 2 KHỐI ĐỊA LÝ LÀ DOANH THU THUẦN** (tổng trừ rổ hoàn, user chốt
  2026-08-22) — KHÁC với 3 khối theo kênh bán vốn ở cơ sở tổng. Lý do: người
  xem lấy số vùng miền để so với "doanh thu thuần" bên KiotViet, còn khối kênh
  bán dùng để đối chiếu với 4 rổ. Đơn **rủi ro hoàn (mã 10) VẪN nằm trong**:
  hàng chưa quay về kho nên KiotViet chưa lập phiếu trả.

  Hai khối địa lý PHẢI cùng cơ sở với nhau — tỉnh cộng lên đúng bằng vùng. Đổi
  một bên là hai donut cạnh nhau lệch nhau đúng bằng tiền hoàn.

  Nhãn phải nói rõ: tiêu đề là "Doanh thu **thuần** theo vùng miền / tỉnh
  thành", cột giá trị và tâm donut dùng key i18n RIÊNG
  (`netRevenueHeader` / `kpi.netRevenueShort`), không dùng lại
  `revenueHeader` của khối kênh bán — dùng chung key là hai số khác nhau đội
  cùng một nhãn.

  Khi suy tỉnh từ location phải khớp **tên tỉnh dài nhất trước**: cắt
  "Bà Rịa - Vũng Tàu - Thành phố Phú Mỹ" theo dấu gạch đầu tiên sẽ ra "Bà Rịa"
  (sai tỉnh).
- **Chi phí Ads — HAI ô rời** (user chốt 2026-08-20, trước đó là một ô gộp
  "FB + Google"): `ads_cost_facebook` = spend FB Ads × hệ số FB (**1.08** nếu kỳ
  xem bắt đầu trong **tháng 8/2026**, **1.1** cho trước đó và từ **01/09/2026** —
  xem `facebook_ads_cost_multiplier`) và `ads_cost_google` = spend Google Ads × 1.08
  (cùng hệ số với Báo cáo Ads) của **toàn bộ** ad account
  trong khoảng ngày (`spend.date`), không giới hạn theo kênh gán. Đây là tiền
  chi ra, **không** tham gia phép cộng của 4 rổ. Key `ads_cost` vẫn còn trong
  payload = tổng hai ô, giữ cho consumer cũ của endpoint.
- **Chỉ có số khi phạm vi đang xem là "Ngoại sàn"** (cùng lần chốt): tiền
  Facebook / Google chỉ đẻ ra đơn ngoại sàn, đặt cạnh doanh thu có cả
  Shopee/TikTok là sai mẫu số. Ngoài phạm vi đó BE trả `0` và **không** truy vấn
  bảng spend. FE vẫn render hai ô (giá trị 0) thay vì ẩn — đổi bộ lọc không làm
  hàng KPI nhảy cột.
- **Ô "Kênh bán" thu hẹp tiếp** (user chốt 2026-08-24): phạm vi lấy theo ô SÂU
  NHẤT đang chọn, kênh nào thì chỉ ô chi phí của kênh đó có số.

  | Đang xem | Ô FB Ads | Ô Google Ads |
  |---|---|---|
  | Ngoại sàn (chưa chọn kênh) | có số | có số |
  | Ngoại sàn → Facebook | có số | 0 |
  | Ngoại sàn → Web | 0 | có số |
  | Ngoại sàn → Ngoại sàn khác | 0 | 0 |
  | Nội sàn / mọi kênh nội sàn | 0 | 0 |
- Mỗi tài khoản quảng cáo **THẬT** chỉ được tính **một lần cho mỗi ngày**. Cùng
  một TKQC tồn tại nhiều dòng (`FacebookAdAccount` unique `(app, account_id)`,
  `GoogleAdsAccount` unique `(config, customer_id)`) và job sync chạy vòng qua
  mọi app/config nên mỗi bản sao có một dòng daily spend đầy đủ của **cùng** số
  tiền — cộng thô cả bảng sẽ nhân số lên đúng bằng số bản sao. Đo prod
  2026-08-19: 240 dòng / 60 TKQC thật → ô này từng hiển thị **2,89 tỉ** cho kỳ
  01–19/08 trong khi chi phí thật là **807 triệu** (×4,000), và % chi phí /
  doanh thu là 47,8% thay vì ~13,4%. Phép gộp lấy `max` các bản sao trong ngày
  vì chi tiêu trong ngày chỉ tăng, nên bản lớn nhất là bản sync gần nhất.

### Biểu đồ diễn biến theo thời gian

Cột xếp chồng, cơ sở **doanh thu thuần**, nhóm theo đúng tầng của bộ lọc. Ba
tab **Theo ngày · Theo giờ · Theo thứ** — KHÔNG có bộ lọc kỳ riêng, dùng chung
`DateFilter` của trang (user chốt 2026-08-23).

Backend trả `revenue_timeseries` gồm `by_day` / `by_hour` / `by_weekday`, mỗi
mức là mảng `{bucket, sale_channel, revenue, orders}`. `bucket` là chuỗi:
"YYYY-MM-DD", "0".."23", hoặc "1".."7" (isoweekday, 1 = Thứ Hai).

- **MỘT query duy nhất** ở grain `(ngày, giờ, nhóm)` rồi cuộn ra cả 3 mức ở
  Python — không phải 3 query. Trần 31 × 24 × 5 = 3.720 dòng.
- Ba mức dùng CÙNG một tập đơn nên tổng của cả ba luôn bằng nhau và bằng ô KPI
  "Doanh thu thuần". Đổi tab không gọi lại API.
- `TruncDate`/`ExtractHour` cắt theo `settings.TIME_ZONE` (Asia/Bangkok, UTC+7),
  KHÔNG phải UTC — nếu không, đơn tạo lúc 23h VN rơi sang ngày hôm trước và cột
  "theo giờ" lệch đúng 7 tiếng.
- BE chỉ trả bucket CÓ dữ liệu. **FE dựng trục** (24 giờ, 7 thứ, mọi ngày trong
  kỳ) và điền 0 — nó có sẵn khoảng ngày đang lọc, còn BE thì khỏi sinh tích chéo
  bucket × nhóm. Không điền 0 thì trục ngày nhảy cóc qua ngày không có đơn và
  người đọc tưởng kỳ ngắn hơn thực tế.
- **Tooltip: dòng đầu là TỔNG doanh thu của cột đang trỏ, KHÔNG phải nhãn trục X**
  (user chốt 2026-08-23). Cài bằng cách BỌC `ChartTooltip` trong
  `ColumnTotalTooltip`, không sửa thẳng `ChartTooltip` — component đó dùng chung
  với Báo cáo Ads, đổi ở đó là đổi luôn tooltip của trang khác.
- FE dựng mốc ngày bằng `new Date(y, m - 1, d)`, KHÔNG `new Date("2026-08-01")`:
  chuỗi ISO chỉ có ngày được parse theo UTC nên ở múi +7 lùi về hôm trước và cả
  trục lệch một ô. Có test chốt riêng chuyện này.

**⚠️ Đọc tab "Theo giờ" cẩn thận — nó là giờ đơn được TẠO trong hệ thống, không
phải giờ khách đặt.** Đo prod tháng 8/2026: giờ 8 có 1.221 đơn và giờ 13 có 742
đơn, trong khi mọi giờ khác chỉ 100–250 đơn. Hai đỉnh đó là nhịp đồng bộ
KiotViet đổ đơn về theo lô, không phải hành vi mua hàng. Muốn biết giờ khách
thật thì phải lấy mốc khác (`kiotviet_purchase_date`) — chưa làm.

- **Line tổng đè lên cột** (user chốt 2026-09-03): một line nối đỉnh các cột
  (tổng các nhóm của cột đó) để thấy xu hướng tăng/giảm giữa các mốc — cột xếp
  chồng đổi màu theo nhóm nên mắt khó so đỉnh cột với nhau. Cùng trục tiền,
  KHÔNG có trục phụ; màu `--foreground` để không tranh màu nhóm. FE tính
  `total` và `changePct` (so với cột LIỀN TRƯỚC trên trục, kể cả cột 0 do FE
  điền) trong `buildRevenueTimeseries`; không thêm field BE. Tooltip: dòng đầu
  "tổng · +12,3% so với ngày trước" (giờ/thứ theo tab), bỏ dòng % khi cột trước
  = 0 hoặc là cột đầu kỳ; mục của line bị lọc khỏi tooltip để tổng không đội đôi.

### Top sản phẩm (2026-09-03)

Khối dưới cùng trang: bar ngang top 10 SP theo **doanh thu thuần cộng theo
dòng hàng** + bảng đầy đủ có phân trang (SKU · Tên · Số lượng · Số đơn · DT
thuần · % trên tổng · Đơn giao TC · DT giao TC · dòng Tổng).

Backend trả `top_products` = mảng `{product_id, sku, name, quantity, orders,
revenue, delivered_orders, delivered_revenue}` đã sort giảm dần theo `revenue`
(rồi SKU), đủ mọi SP có dòng hàng — FE tự cắt top 10 cho biểu đồ.

- Tập đơn là `net_revenue_q` (đã lên hóa đơn, trừ hủy, trừ hoàn; rủi ro hoàn
  vẫn tính) — CÙNG tập với 2 khối địa lý và biểu đồ diễn biến, nên đổi bộ lọc
  Nguồn / Kênh bán / kỳ thì khối này co theo y hệt.
- Tiền cộng theo `OrderItem.line_total` của từng SP; số ĐƠN đếm distinct theo
  đơn chứa SP (đơn mua kèm 2 SP đếm 1 ở mỗi SP). Cột giao TC lọc
  `order.status = 3` ở mức dòng hàng.
- **Tổng các SP KHÔNG buộc bằng ô KPI Doanh thu thuần**: ô KPI cộng
  `total_after_discount` của ĐƠN, còn dòng hàng không gánh chiết khấu cấp đơn /
  phí ship. Khác biệt cố ý — dòng chú thích dưới tiêu đề khối nói rõ. Đừng
  "sửa" bằng cách phân bổ chiết khấu về dòng hàng: bộ lọc sản phẩm của trang
  (mục dưới) cũng đang ở cơ sở `line_total`, hai chỗ phải cùng cơ sở.
- Đang lọc SP: chỉ còn đúng các SP đó — tiền của SP khác mua kèm trong cùng
  đơn không lẫn vào bảng (`base` chỉ lọc ĐƠN chứa SP, nên phải lọc lại
  `product_id__in` ở mức dòng hàng).
- FE (`buildTopProducts`) bỏ dòng doanh thu ≤ 0 (quà tặng/kèm theo có số lượng
  nhưng không có tiền) — khối xếp hạng theo tiền không cần chúng.

### Bỏ khỏi báo cáo (Sprint 6)

Ô **Hủy** và ô **Đơn chờ chốt** (lead chưa lên hóa đơn) rời khỏi hàng KPI;
`gross_revenue`, `pending_*`, `cancelled_*` rời khỏi response. Đơn hủy vẫn hiển
thị bình thường ở tab "Đã hủy" trên màn đơn hàng — trang Báo cáo tổng chỉ
không còn ô riêng cho nó.

### Bộ lọc Nguồn + Kênh bán

Hai ô phụ thuộc nhau, áp cho TOÀN TRANG như bộ lọc sản phẩm. Giá trị lạ coi như
không lọc thay vì trả rỗng.

- `sale_channel` — ô **"Nguồn"** (tên cũ "Kênh bán", đổi 2026-08-24):
  `marketplace` | `off_marketplace`, bỏ trống = tất cả. Chính ô này quyết định
  TẦNG của biểu đồ.
- `sale_channel_group` — ô **"Kênh bán"** (thêm 2026-08-24): một nhóm TRONG tầng
  đó. Danh sách lấy thẳng từ `sale_channel_groups_for()` — cùng hàm quyết định
  các lát biểu đồ, nên ô lọc không thể lệch với dữ liệu đang hiển thị. Nhóm
  không thuộc tầng đang chọn bị bỏ qua ở CẢ hai phía (`sale_channel_group_filter`
  ở BE, `saleChannelGroupFor` ở FE).

**Điều kiện lọc là PHẦN BÙ LUỸ TIẾN**, không phải Q của nhánh: `Case` dừng ở
nhánh khớp đầu tiên nên "thuộc nhóm facebook" = "không phải Shopee, không phải
TikTok, VÀ code kênh bắt đầu bằng facebook". Cả hai dựng từ một danh sách duy
nhất `SALE_CHANNEL_CASE_BRANCHES` — lệch một nhánh là tổng donut hết bằng ô KPI.

**Hệ quả CỐ Ý:** Nguồn = Tất cả + Kênh bán = Ngoại sàn cho số y hệt Nguồn =
Ngoại sàn, nhưng donut chỉ một lát "Ngoại sàn" thay vì tách Facebook/Web/Khác.
Muốn xem tách thì chọn ở ô Nguồn.

FE giữ hai bảng nhãn rời: `SALE_CHANNEL_SOURCE_LABELS` (2 giá trị của ô Nguồn)
và `SALE_CHANNEL_LABELS` (nhãn mọi nhóm) — trộn hai thứ này là nguồn lỗi.

### Bộ lọc sản phẩm

`product_ids` (multi-select trên thanh lọc) đổi ĐỊNH NGHĨA số tiền của cả trang:
mọi khoản là **tiền của riêng các sản phẩm được chọn** — tổng `OrderItem.line_total`
của các dòng khớp, không phải giá trị cả đơn (user chốt 2026-07-20).

- Đơn mua kèm A+B, lọc A → chỉ cộng phần tiền của A. Nhờ vậy **cộng số liệu của
  mọi sản phẩm lại đúng bằng tổng không lọc**, không đếm trùng đơn.
- Số ĐƠN vẫn đếm theo đơn (đơn có chứa sản phẩm), không đếm theo dòng hàng.
- Đơn không chứa sản phẩm được lọc bị loại khỏi mọi khối.
- **Chi phí Ads không tách được theo sản phẩm** nên giữ nguyên tổng chi tiêu của
  kỳ (đúng cho cả hai ô FB / Google); FE hiện ghi chú ngay dưới ô lọc để không
  ai đọc nhầm ô "% chi phí".

Kỹ thuật: `summary_overview` annotate `amount` = `total_after_discount` khi
không lọc, hoặc `Subquery(Sum(line_total))` của các dòng khớp khi có lọc; toàn bộ
phép cộng phía dưới dùng `amount` nên chỉ có MỘT đường tính, không nhánh đôi.

### Bất biến phải giữ

**Tổng của mọi khối chi tiết luôn bằng ô KPI tương ứng của nó.** Không được loại
đơn ra khỏi khối vì thiếu thuộc tính phân nhóm (nguồn, tỉnh) — thiếu thì cho vào
nhóm gộp ("Ngoại sàn khác" / "Chưa có địa chỉ"). Hai lần vi phạm bất biến này
đều lọt tới tay user (2026-07-20: khối địa lý bỏ đơn trống tỉnh, rồi khối nguồn
bỏ đơn trống `order_source`).

Từ 2026-08-22 "ô KPI tương ứng" KHÔNG còn là một ô duy nhất:

| Khối | Khớp ô |
|---|---|
| Doanh thu theo kênh bán | **Tổng doanh thu** |
| Hoàn theo kênh bán | Hoàn |
| Rủi ro hoàn theo kênh bán | Rủi ro hoàn |
| Doanh thu thuần theo vùng miền | **Doanh thu thuần** |
| Doanh thu thuần theo tỉnh/thành | **Doanh thu thuần** |

Test `test_orders_without_source_stay_in_bucket_totals`, các test ở
`test_overview_report_sale_channel.py` và `test_revenue_by_region_and_province`
chốt bất biến cho cả 5 khối, mỗi khối với đúng vế phải của nó.

**Bốn rổ kết quả cộng lại đúng bằng ô Tổng doanh thu** (tiền và số đơn). Test
`test_four_buckets_sum_to_total_revenue` và
`test_four_buckets_partition_every_order_status` chốt bất biến này — cái sau bắt
được cả trường hợp thêm mã `OrderStatus` mới mà quên xếp rổ.

### Đã đóng — đếm đôi khi sale chốt trên CRM rồi lên hóa đơn thẳng trên KiotViet

**Rủi ro này biến mất từ 2026-08-20** cùng lúc với việc đổi phạm vi đơn. Ghi lại
để không ai "sửa" luật phạm vi rồi mở lại nó.

Kịch bản cũ: sale chốt khách trên CRM (`sales_status_code` ∈ 4 mã chốt), đơn
chưa lên hóa đơn nên `invoice_status IS NULL`; rồi thay vì đẩy đơn đó lên hóa
đơn từ CRM, có người tạo hóa đơn MỚI thẳng trên KiotViet cho cùng giao dịch.
Cùng một giao dịch thành 2 bản ghi `Order`, và cả hai đều lọt vào phạm vi cũ —
bản lead qua vế 1 (có trạng thái sale chốt), bản hóa đơn qua vế 2 (có hóa đơn,
chưa gắn trạng thái) → doanh thu và số đơn cộng đôi.

Luật mới chỉ nhận đơn ĐÃ có hóa đơn, nên bản lead không còn vào doanh thu ở bất
kỳ điều kiện nào — hết đường đếm đôi, kể cả hai trường hợp mà job
`close_converted_leads` không bao giờ vá được: lead lệch SĐT với hóa đơn, và
hóa đơn phát sinh ngoài cửa sổ khớp 7 ngày.

Job `close_converted_leads` (`LeadCloseService`, Sprint 3b, beat 02:10 hằng
ngày) vẫn giữ nguyên — nó phục vụ việc dọn lead trên màn đơn hàng, không còn là
chốt chặn của báo cáo này nữa.

## Backend

- `GET /api/orders/reports/overview?date_from&date_to&product_ids&sale_channel` —
  `require_section("overview-report")`, view `overview_report_summary` trong
  `dashboard_report_views.py`.
- `OrderRepository.summary_overview(date_from, date_to)`
  (`_repositories_reports.py`): cấu trúc query không đổi qua Sprint 6 — 1 query
  aggregate tổng + **1 query group-by nhóm kênh bán duy nhất cho cả 3 bucket**
  (FILTER trên từng cột, không quét bảng 3 lần) + 1 query group-by địa lý + 2
  query sum spend. Gốc doanh thu KHÔNG còn join `order_assessments` (Sprint 9 bỏ
  điều kiện trạng thái sale). Query group-by nhóm kênh bán join sang `channels`
  cho 2 nhánh Facebook/Web — FK non-null nên là INNER JOIN, không mất dòng, và
  bảng `channels` vài chục dòng nên chi phí không đáng kể.
- Response (Decimal → str như các report khác):

```json
{
  "total_revenue": "0",     "total_orders": 0,
  "delivered_revenue": "0", "delivered_orders": 0,
  "awaiting_revenue": "0",  "awaiting_orders": 0,
  "returned_revenue": "0",  "returned_orders": 0,
  "return_risk_revenue": "0", "return_risk_orders": 0,
  "ads_cost": "0", "ads_cost_facebook": "0", "ads_cost_google": "0",
  "revenue_by_sale_channel":     [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0,
                                    "delivered_revenue": "0", "delivered_orders": 0 }],
  "returned_by_sale_channel":    [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0 }],
  "return_risk_by_sale_channel": [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0 }],
  "revenue_by_region":   [{ "region": "red_river_delta", "revenue": "0", "orders": 0,
                            "delivered_revenue": "0", "delivered_orders": 0 }],
  "revenue_by_province": [{ "province": "Hà Nội", "revenue": "0", "orders": 0,
                            "delivered_revenue": "0", "delivered_orders": 0 }]
}
```

`net_*` đổi tên thành `total_*` (thay vì giữ tên cũ): "net" nghĩa là sau hoàn
hủy, còn số mới thì gồm cả hoàn — giữ tên cũ sẽ đánh lừa người đọc code sau
này. Endpoint chỉ có trang này gọi nên không có consumer nào khác phải sửa.

Nhóm kênh bán 0 đơn trong một bucket bị loại khỏi mảng của bucket đó (donut
không có lát rỗng).

- RBAC: section mới `overview-report` = `("view",)` — sửa đủ 3 chỗ backend:
  `rbac_catalog.PERMISSION_CATALOG`, `models.VALID_SECTIONS`, migration seed
  `0031_seed_overview_report_permissions` (theo mẫu 0030 contact_flow).

## Frontend

- `features/overview-report/`:
  - `pages/OverviewReportPage.tsx` — `DateFilter` (mặc định **Tháng này**, đổi
    từ Hôm nay ở Sprint 6 — trang này để lãnh đạo nhìn cả tháng đang chạy) +
    hàng KPI + 3 khối donut.
  - `components/OverviewKpiTiles.tsx` — 8 tile căn giữa, chia HAI hàng **4 ô**
    (user chốt 2026-08-22). Hàng 1 (tiền vào): Tổng doanh thu · Doanh thu thuần ·
    Giao thành công · Chờ giao. Hàng 2 (tiền mất + tiền chi): Hoàn · Rủi ro hoàn ·
    Chi phí Facebook Ads · Chi phí Google Ads.

    Ranh giới hai hàng TRÙNG ranh giới màu: đúng 4 tile hàng 2 mang
    `tone="danger"` tô đỏ GIÁ TRỊ chính (`text-rose-500`), hàng 1 không có ô đỏ
    nào. Thêm ô đỏ mới thì xếp xuống hàng 2. Dòng `sub` chứa % luôn giữ màu
    `text-muted-foreground`, không đổi theo tone. Test
    `"8 ô chia đúng 2 dòng, mỗi dòng 4 ô"` chốt cả hai điều này.

    TÁCH HAI HÀNG chứ không nhồi 8 ô một hàng: ở màn 1280px mỗi ô chỉ còn
    ~128px, không đủ chỗ cho số tiền 13 chữ số.
  - `components/OverviewBreakdownCard.tsx` — donut + bảng chi tiết, `variant`
    quyết định bộ cột (`revenue` có cột giao TC, `amount` có cột %).
  - `components/ProvinceRevenueCard.tsx` — bar ngang top 10 + bảng đủ tỉnh.
  - `source-colors.ts` / `regions.ts` — màu cố định theo nguồn / theo vùng (xem
    mục Màu ở trên); `regions.ts` là GƯƠNG của `vietnam_regions.py`, thêm vùng
    phải sửa cả hai nơi.
  - `queries.ts` — React Query, `refetchInterval` 30 phút + refetch on focus
    (đồng bộ hành vi `useAdsReport`).
  - `overview-selectors.ts` — parse payload → view model + tính %.
- API: `lib/api/overview-report.ts` typed từ `api/schema.d.ts` (chạy `gen:api`
  + prettier).
- Wiring: `AppSection` + `SECTION_TO_PATH` (`/reports/overview-report`) trong
  `lib/routes.ts`; route + Guard trong `app/routes.tsx`; mục trong nhóm Báo cáo
  `app/modules.ts`; nhãn + nhóm trong `RolesPage.tsx`; i18n key.

## Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 | BE endpoint + RBAC + FE trang KPI + wiring + test | ✅ Done 2026-07-20 |
| 2 | Doanh thu giao thành công + 3 donut theo nguồn + bảng chi tiết | ✅ Done 2026-07-20 |
| 3 | Doanh thu theo vùng miền + theo tỉnh/thành | ✅ Done 2026-07-20 |
| 3b | Fix phồng số: tách lead khỏi doanh thu (bucket pending + tile) + status 12/13 vào Hoàn + job đóng lead trùng | ✅ Done 2026-07-20 |
| 4 | Bộ lọc theo sản phẩm | ✅ Done 2026-07-20 |
| 5 (nếu cần) | Bộ lọc kênh, export Excel, so sánh kỳ trước, biểu đồ diễn biến theo ngày | Chưa làm |
| 6 | Đổi gốc doanh thu sang trạng thái sale: 4 rổ kết quả + ô Rủi ro hoàn, bỏ ô Hủy/Đơn chờ chốt, mặc định Tháng này | ✅ Done 2026-08-11 |
| 7 | % Hoàn / % Rủi ro hoàn đổi mẫu số sang doanh thu đã có kết quả giao (loại rổ Chờ giao) | ✅ Done 2026-08-17 |
| 8 | Tách chi phí Ads thành 2 ô Facebook / Google, chỉ có số khi lọc Ngoại sàn | ✅ Done 2026-08-20 |
| 9 | Gốc doanh thu về mốc hóa đơn KiotViet: `~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q` | ✅ Done 2026-08-20 |
| 10 | Biểu đồ chia 5 nhóm kênh bán: Shopee · TikTok · Facebook · Web · Ngoại sàn khác | ✅ Done 2026-08-20 |
| 11 | Ô "Doanh thu thuần" + 2 khối địa lý đổi sang cơ sở thuần | ✅ Done 2026-08-22 |
| 12 | Hàng KPI chia 2 dòng × 4 ô, bỏ 2 dòng chú thích dưới ô | ✅ Done 2026-08-22 |
| 13 | Nhóm kênh bán theo TẦNG + biểu đồ diễn biến theo ngày/giờ/thứ | ✅ Done 2026-08-23 |
| 14 | Ô lọc cũ đổi tên thành "Nguồn" + thêm ô "Kênh bán" lọc sâu một tầng | ✅ Done 2026-08-24 |

### Note sau Sprint 14 (2026-08-24)

Ô lọc cũ "Kênh bán" đổi tên thành **"Nguồn"** (giá trị không đổi), thêm ô
**"Kênh bán"** mới lọc sâu một tầng. Ba điểm phải biết trước khi deploy:

1. **Chọn một kênh thì donut còn ĐÚNG một lát 100%** — đúng nguyên tắc "biểu đồ
   là dữ liệu đang lọc", nhưng người xem lần đầu dễ tưởng hỏng.
2. **Nguồn = Tất cả + Kênh bán = Ngoại sàn** cho số y hệt Nguồn = Ngoại sàn mà
   donut KHÔNG tách Facebook/Web/Khác. Hệ quả cố ý của việc ô Kênh bán bám đúng
   tầng biểu đồ; muốn xem tách thì đổi ô Nguồn.
3. **Chi phí Ads thu hẹp theo kênh**: xem riêng Facebook thì ô Google về 0 (và
   ngược lại). Đây là ĐỔI HÀNH VI so với luật cũ "cứ ngoại sàn là hiện cả hai".

`SALE_CHANNEL_FILTER_LABELS` đổi tên thành `SALE_CHANNEL_SOURCE_LABELS` để
không lẫn với ô Kênh bán mới.

`schema.yaml` sửa TAY (+7 dòng cho param mới, không xoá gì) rồi ĐỐI CHIẾU với
bản `manage.py spectacular` sinh trong container: hunk khớp từng byte. Vẫn không
commit bản regen toàn file vì file trên master đã cũ sẵn.

### Note sau Sprint 13 (2026-08-23)

Đảo một phần Sprint 10: donut không còn luôn hiện 5 nhóm mà chia theo tầng của
bộ lọc. **Sau deploy, trang mặc định ("Tất cả") tụt từ 5 lát xuống 2 lát** —
phải báo trước, người xem vừa quen 5 lát từ 20/08. Muốn xem Shopee/TikTok tách
nhau thì chọn bộ lọc Nội sàn.

Bảng màu: mỗi TẦNG là một bảng phân loại riêng, đã chạy validator riêng ở light
mode — Tất cả (CVD ΔE 24.7), Nội sàn (29.5), Ngoại sàn (9.1). `marketplace` dùng
lại hex của `facebook` và `off_marketplace` dùng lại hex của `shopee`: hợp lệ vì
hai tầng không bao giờ hiện cùng lúc, và cặp xanh/cam này đúng là cặp nội/ngoại
sàn của trang trước 20/08 nên người xem cũ thấy quen. Test kiểm khác màu TRONG
TỪNG TẦNG, không phải trên cả 7 khóa.

`schema.yaml` sửa TAY (+38 dòng, không xoá gì) thay vì regen: chạy
`manage.py spectacular` trên nhánh này ra diff 336+/685− vì file trên master đã
cũ sẵn so với code — regen ở đây là kéo nhiễu của feature khác vào MR. Trả nợ
đó bằng một MR chore riêng.

### Note sau Sprint 12 (2026-08-22)

Chỉ đụng `OverviewKpiTiles.tsx`. Bỏ HAI dòng chú thích (`returnBaseNote` giải
thích vì sao 4 số % không cộng thành 100%, và `netRevenueNote` giải thích vì sao
"thuần" vẫn cao hơn KiotViet). Cả hai từng được đánh dấu là load-bearing trong
chính file này; user cân nhắc và vẫn cho bỏ để màn gọn.

`settledRevenue` / `settledOrders` KHÔNG bị gỡ khỏi `OverviewKpis` dù không còn
hiển thị ở đâu: chúng vẫn là mẫu số của `returnedPct` / `returnRiskPct`, và test
bất biến `deliveredSettledPct + returnedPct + returnRiskPct = 100` vẫn dùng.

### Note sau Sprint 11 (2026-08-22)

Sinh ra từ một câu hỏi của user: KiotViet báo "doanh thu thuần" ~5,95 tỉ còn
trang này báo 6,83 tỉ. Truy ra **ba** nguyên nhân, không phải một, và không cái
nào là bug:

| Bước | Số tiền |
|---|---:|
| Tổng doanh thu trên trang (theo `created_at`) | 6.834.925.913 |
| ➕ chênh trục ngày (đổi sang ngày lập hóa đơn) | +42.283.000 |
| ➖ tiền đơn hoàn của chính kỳ | −460.411.700 |
| ➖ trả hàng của đơn kỳ TRƯỚC, xử lý trong kỳ này | ~−507.889.000 |
| = ước tính theo công thức KiotViet | 5.908.908.213 |
| KiotViet thực tế | 5.954.091.358 (lệch 0,76%) |

Ô "Doanh thu thuần" đóng được khoản giữa. Hai khoản còn lại KHÔNG đóng được ở
phía mình. Trang từng có dòng chú thích nói thẳng điều đó ngay dưới hàng KPI;
**user cho bỏ 2026-08-22** để màn gọn, chấp nhận rủi ro câu hỏi quay lại. Ai bị
hỏi thì tra bảng bắc cầu ngay trên đây.

Đã loại hai giả thuyết bằng số liệu: **phí ship** (`total_after_discount` =
tiền hàng − giảm giá, không gồm ship; 2.618/4.470 đơn có `total_amount` khớp
chính xác tổng `line_total`) và **hai chi nhánh bị loại** (tháng 8 cả hai đóng
góp 0 đồng).

Nợ dữ liệu phát hiện được: cột `Order.return_date` **hỏng** — 173/364 đơn hoàn
tháng 8 để trống. Vì vậy KHÔNG thể dựng đúng công thức KiotViet (trừ trả hàng
theo ngày trả) cho tới khi vá cột này. Phải dùng `updated_at` làm proxy khi
điều tra, và đó là nguồn của 0,76% sai số ở trên.

Số prod sau deploy (tháng 8/2026): Tổng doanh thu 7.030.939.312 · Doanh thu
thuần **6.528.072.292** · tâm 2 donut địa lý tụt đúng 502.867.020 xuống bằng
con số thuần.

### Note sau Sprint 10 (2026-08-20)

- Chỉ đụng cách PHÂN NHÓM của 3 khối biểu đồ. Ô KPI, 4 rổ, bộ lọc và luật chi
  phí Ads giữ nguyên — tổng doanh thu không đổi một đồng nào.
- Bảng màu 5 nhóm lấy nguyên hex từ bảng màu phân loại chuẩn và đã chạy validator
  ở light mode theo ĐÚNG THỨ TỰ VẼ: CVD ΔE 9.1 (ngưỡng ≥8), thị lực thường ΔE
  16.3 (sàn ≥15). Hai màu `#1baf7a`/`#eda100` dưới 3:1 tương phản nền — hợp lệ vì
  mỗi donut luôn kèm bảng chi tiết có nhãn chữ; bỏ bảng đó là vi phạm. Đổi màu
  hoặc đổi thứ tự phải validate lại.
- Số tháng 8/2026 sau khi chia: Shopee 1,793 tỉ · TikTok 1,294 tỉ · Facebook
  2,625 tỉ · Web 479 tr · Ngoại sàn khác 306 tr.

### Note sau Sprint 9 (2026-08-20)

- Chỉ đụng backend, đúng một dòng logic: `total_q` ở `reporting/overview.py`.
  Payload API, FE và 4 rổ giữ nguyên.
- Gỡ hai hằng số thành code chết: `OVERVIEW_CLOSED_SCOPE_Q` và
  `NO_SALES_STATUS_Q` (kèm re-export ở `reporting/__init__.py` và
  `_repositories_reports.py`). `CLOSED_SALES_STATUS_CODES` GIỮ NGUYÊN — Báo cáo
  Sale, Báo cáo Ads và Báo cáo Khách hàng vẫn dùng.
- **Số trên prod giảm ~0,9%** sau deploy (tháng 8: 6,473 → 6,415 tỉ). Phải báo
  trước, không thì bị hiểu là mất số.
- Đối chiếu sau deploy: `total_revenue` một kỳ phải khớp
  `SELECT sum(total_after_discount) FROM orders` với cùng bộ lọc
  `invoice_status IS NOT NULL`, `NOT (status = 6 OR invoice_status = 2)`, loại
  chi nhánh `1000000139`/`1000000477`, loại clone khôi phục. Lưu ý viết
  `COALESCE(invoice_status, -1) = 2` khi tra tay: `NOT (... OR invoice_status = 2)`
  trong SQL thuần loại sạch hàng NULL, còn Django thì giữ — sai chỗ này là ra hai
  con số khác nhau rồi tưởng code hỏng.

### Note sau Sprint 7 (2026-08-17)

- Chỉ đụng frontend: `computeOverviewKpis` thêm `settledRevenue`/`settledOrders`
  /`deliveredSettledPct`, hai ô hoàn đổi mẫu số; `OverviewKpiTiles` bọc thêm một
  lớp `div` và có dòng chú thích mẫu số. Backend, payload API và mọi test BE giữ
  nguyên.
- Đã cân nhắc và BỎ: thêm field `settled_revenue` ở BE (thừa — phép trừ trên
  payload cho kết quả hệt nhau), và đổi mẫu số cho cả ô Giao thành công / Chờ
  giao (ô Chờ giao khi đó luôn 0%, vô nghĩa).
- Ba khối donut theo kênh bán/địa lý KHÔNG bị ảnh hưởng: `sharePct` ở đó vốn là
  % nội bộ trong từng khối (nội sàn vs ngoại sàn), không phải % trên tổng.

### Note sau Sprint 6 (2026-08-11)

Doanh thu không còn tính theo hóa đơn KiotViet mà theo TRẠNG THÁI SALE. Hệ quả
phải theo dõi trên prod: đơn đã có hóa đơn nhưng sale đổi trạng thái sang mã
ngoài 4 mã chốt (vd "Khách bom hàng") bị loại khỏi báo cáo theo quyết định
2026-08-11. Dấu hiệu sai: ô Hoàn gần 0 trong khi tab Hoàn ở màn đơn hàng vẫn
nhiều đơn — khi đó nới `OVERVIEW_CLOSED_SCOPE_Q` vế hai thành `~LEAD_ORDER_Q`.

> **Đã hết hiệu lực từ Sprint 9 (2026-08-20)**: gốc doanh thu không còn là trạng
> thái sale, `OVERVIEW_CLOSED_SCOPE_Q` đã gỡ khỏi code. Giữ đoạn này làm lịch sử.

### Note sau Sprint 3b — fix phồng số (2026-07-20)

User báo "số liệu nhiều hơn thực tế". Xác minh trên DB prod (restore 20/07), kỳ
01–19/07: net cũ 6,49 tỷ / 4.128 đơn nhưng trong đó **806 lead chưa lên hóa đơn
= 1,28 tỷ (~20%)**; ≥96 lead (149tr) còn ĐẾM ĐÔI vì cùng SĐT đã có hóa đơn
KiotViet cùng kỳ (sale chốt trực tiếp trên KiotViet nên lead không được cập
nhật). Nguyên nhân kỹ thuật: `~Q(invoice_status=2)` trong Django giữ lại hàng
NULL → lead lọt bucket net.

- Đã làm:
  - `LEAD_ORDER_Q` tách lead khỏi mọi bucket doanh thu; thêm bucket **pending**
    (`pending_revenue`/`pending_orders`) + tile "Đơn chờ chốt" (hàng KPI 6 ô);
    gross chỉ còn đơn có hóa đơn; lead đã đóng biến mất khỏi báo cáo.
  - Bucket Hoàn nhận thêm `status ∈ {12, 13}` (chờ lấy hàng hoàn / chờ xác nhận
    hoàn) — trước nằm nhầm ở net.
  - `LeadCloseService.close_converted` + command `close_converted_leads`
    (`--dry-run`, `--days-window`, `--lookback-days`, 0 = toàn bộ lịch sử) +
    beat `orders-close-converted-leads` 02:10 hằng ngày: đóng lead (status=6,
    journey event `case_closed` payload `converted_invoice`) khi cùng SĐT có
    hóa đơn không-hủy trong cửa sổ [lead − 1 ngày, lead + 7 ngày].
- Deploy: KHÔNG cần migration. Sau deploy chạy hồi tố một lần:
  `python manage.py close_converted_leads --lookback-days 0 --dry-run` (soát số)
  rồi bỏ `--dry-run`. Restart beat để nhận schedule mới.
- Đối chiếu nhanh khi nghi lệch số: so `net` của report với query
  `invoice_status IS NOT NULL` cùng kỳ — hai số phải trùng.

### Note sau Sprint 3

- Đã làm: `vietnam_regions.py` (map 63 tỉnh → 7 vùng, chuẩn hoá bỏ dấu nên
  "Hoà Bình"/"Hòa Bình"/"hòa Bình" về chung 1 dòng — dữ liệu prod đang có cả 3);
  2 mảng `revenue_by_region` / `revenue_by_province`; khối donut vùng + khối bar
  tỉnh ở FE.
- Rà dữ liệu 30 ngày (2026-06-20 → 07-20) khi vá lệch số: 705 đơn net trống
  `province`, trong đó 17 đơn lấy lại được tỉnh từ `kiotviet_location`, còn 688
  đơn không có địa chỉ nào — **652 đơn ở trạng thái Mới** (vừa vào, sale chưa
  nhập) và 36 đơn đã giao thành công nhưng không có địa chỉ lẫn mã vận đơn (mua
  tại quầy). Nhóm "Chưa có địa chỉ" vì thế là thông tin thật, không phải rác.
- Lưu ý dữ liệu: `Order.province` là text tự do, đang dùng bộ tên 63 tỉnh CŨ.
  Tên mới sau sáp nhập 2025 đều trùng tên tỉnh đã có trong map nên không cần sửa
  khi dữ liệu chuyển đổi; tên hoàn toàn mới thì bổ sung vào `REGION_PROVINCES`.
- Chưa làm: như Sprint 4 bên dưới.

### Note sau Sprint 2

- Đã làm: `delivered_*` ở cả mức tổng và theo nguồn; 3 mảng bucket theo nguồn;
  3 khối donut + bảng chi tiết; màu/thứ tự lát cố định theo nguồn (đã validate);
  hàng KPI rút còn 5 ô (bỏ 7 ô nguồn vì đã nằm trong khối donut).
- Chưa làm: bộ lọc kênh; export Excel; biểu đồ diễn biến theo ngày; bảng màu
  riêng cho dark mode (xem hạn chế ở mục Màu).
