# Gán chiến dịch quảng cáo ↔ sản phẩm

**Ngày:** 2026-08-24
**Mục tiêu:** lưu chiến dịch quảng cáo (Facebook + Google) thành dữ liệu có cấu trúc,
gán mỗi chiến dịch về một sản phẩm, để tính **chi phí ads theo từng sản phẩm bằng số
thật** thay vì phân bổ theo tỉ trọng.

Yêu cầu gốc: bổ sung bộ lọc sản phẩm cho 2 tab "Báo cáo theo digital" và "Báo cáo chi
tiết theo digital" (tổng tất cả sản phẩm / từng SP một).

---

## 1. Vì sao không phân bổ chi phí theo tỉ trọng doanh thu

Phương án rẻ là chia chi phí kênh theo tỉ trọng doanh thu của từng SP trong ngày. Bỏ vì:

- Con số ra là **số phân bổ, không phải chi tiêu thật**. Phòng Ads không dùng nó để ra
  quyết định tắt/bật chiến dịch được.
- Chi phí một SP sẽ **nhảy mỗi ngày theo doanh thu**, không đối chiếu được với Ads Manager.

Đo trên dữ liệu prod tháng 8/2026: tên chiến dịch đã mã hoá sẵn mã SP, đủ để lấy số thật.

| Nền tảng | Chi phí T8 (spend gốc) | Ra đúng 1 SKU | Ra nhiều SKU | Chưa nhận dạng |
|---|---:|---:|---:|---:|
| Facebook | 823,9tr | **95,6%** (592 CD) | 1,7% (12 CD) | 2,7% (17 CD) |
| Google | 147,0tr | 40,9% (6 CD) | 18,9% (1 CD) | 40,3% (20 CD) |
| **Tổng** | **970,9tr** | — | — | **8,4% (81,5tr)** |

## 2. Luật đọc tên chiến dịch

> **Sửa lớn ngày 24/08 (sau khi deploy, trước khi parser kịp chạy lần đầu).** Bản đầu
> khớp **SKU đầy đủ**. Sai: danh mục có SKU `TMS01` — tên đúng bằng mã, doanh thu 90 ngày
> 1,7tr, tức SKU rác — bên cạnh `TMS01-PROMAX` 47,5 tỉ. Luật cũ sẽ gán **657 chiến dịch**
> thảm massage về cái rác đó. Nay gom theo **mã gốc** rồi chọn biến thể **bán chạy nhất
> trong 90 ngày**; heuristic này tự cho ra đúng hai mã nghiệp vụ đã xác nhận
> (`SQA03`→`SQA03-1.7M`, `TMS01`→`TMS01-PROMAX`). Bù lại `SQA03` — vốn không phải SKU nào
> cả — nay khớp được, vớt thêm 172 chiến dịch.


Tách tên theo ký tự không phải chữ-số → mỗi token **bỏ số 0 đứng đầu** → đối chiếu
**CHÍNH XÁC** với `products.sku` đầy đủ.

Khớp theo mã gốc (phần trước dấu `-`) đã bị loại: `MSC02` ứng với 6 SKU khác nhau
(`MSC02`, `MSC02-CAOCAP`, `MSC02-CAOCAP{DEL}`…), đoán bừa một trong số đó là tạo số sai
âm thầm. Đo lại thì khớp SKU đầy đủ đã phủ 95,5% chi phí, còn nhánh mã gốc chỉ thêm
0,1% — không đáng đánh đổi.

Giới hạn đã biết: token tách theo dấu `-` nên SKU có gạch không bao giờ khớp được
(`Camp MSC02-CAOCAP` ra `MSC02`). Trường hợp cần phân biệt biến thể thì gán tay, và bản
gán tay không bao giờ bị parser ghi đè.

| Tên chiến dịch thật | Token khớp | Ra |
|---|---|---|
| `Desiglobal_BonNgamChan_XYZ_LDP_0DC30_030826_Page 2` | `0DC30` → `DC30` | Bồn ngâm chân DC30 |
| `DMG - MSC06 - Audience -Tin tức chính trị` | `MSC06` | Máy massage bàn chân |
| `LDP_0BNC02_300726_TK 7_BÀI 2` | `0BNC02` → `BNC02` | Bồn ngâm chân BNC02 |
| `Shopping Thảm Massage` | — | **Chưa gán** |
| `06/05 bài 3` | — | **Chưa gán** |

**Đính chính một hiểu lầm đã mắc khi khảo sát:** `0DC30`/`0DG20` ban đầu bị đoán là mã
landing page vì tra `products.sku LIKE '%0DC%'` không ra gì. Thực tế chúng **là SKU, chỉ
thừa số 0 đứng đầu**. Luật bỏ số 0 kéo độ phủ Facebook từ 65% lên 95,6%.

### Google cố ý phủ thấp

`Shopping Thảm Massage`, `Search-Tủ sấy quần áo`, `Pmax 3`, `Shopping-All Máy Massage`
là chiến dịch **cấp nhóm hàng** — Google Shopping/Pmax chạy cả feed nhiều SP cùng lúc.
Ép gán về 1 SP là bịa số, nên **để rổ "Chưa gán"**. Nhóm này chiếm 40% chi phí Google
nhưng chỉ ~6% tổng chi phí toàn công ty.

## 3. Quyết định nghiệp vụ đã chốt

| Mã | Quyết định | Hệ quả thiết kế |
|---|---|---|
| D1 | **1 chiến dịch chỉ chạy 1 sản phẩm** | Bỏ bảng mapping riêng + cột tỉ trọng. Chỉ cần 1 cột `product_id` trên chính bảng chiến dịch |
| D2 | Chi phí chưa gán phải **hiện ra**, không được giấu | Dòng chú thích dưới bảng digital (xem §6 — đã đổi từ "dòng trong bảng"). Đồng thời là danh sách việc cho phòng Ads |
| D3 | **Chỉ phòng Ads được sửa** | Thêm action `edit` vào section `ads-report` |
| — | Chiến dịch có 2+ mã SP trong tên (13 CD) | Máy **không chọn bừa**, để trống chờ người gán |

## 4. Mô hình dữ liệu

### 4.1 Bảng mới `facebook_ad_campaigns`

Đối xứng với `google_ads_campaigns` đã có sẵn. Hiện Facebook không có bảng campaign —
`campaign_id`/`campaign_name` là 2 cột phi chuẩn hoá nằm trên từng dòng `facebook_ads`.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `fb_campaign_id` | varchar(64) | **UNIQUE toàn cục** — xem §4.2 |
| `ad_account` | FK → `facebook_ad_accounts`, nullable | ad đã xoá vẫn còn chi tiêu lịch sử nhưng mất TKQC |
| `name` | varchar(255) | tên hiện hành |
| `product` | FK → `products`, nullable | SP được gán; NULL = chưa gán |
| `product_source` | varchar(8) | `auto` (máy đọc tên) / `manual` (người xác nhận) |
| `product_updated_by` | FK → user, nullable | dấu vết ai gán |
| `product_updated_at` | timestamptz, nullable | |
| `first_seen_at` / `last_seen_at` | timestamptz | |

`facebook_ads` thêm FK `campaign → facebook_ad_campaigns` (nullable) và **bỏ hẳn hai cột
chuỗi `campaign_id`/`campaign_name`**.

> **Đổi so với bản duyệt.** Kế hoạch ban đầu là giữ song song hai cột cũ theo lối
> expand/contract. Không làm được: attname của FK `campaign` đúng bằng `campaign_id` nên
> Django chặn thẳng (`models.E006`). Hai đường vòng đều tệ hơn — đặt tên FK kiểu
> `campaign_ref` thì cái tên xấu đó sống mãi trong codebase, còn giữ cả hai nguồn thì
> sync chỉ ghi một chỗ nên bản còn lại trôi lệch ngay. Đổi lại: migration này là
> **BREAKING**, phải **migrate + roll code cùng nhịp**.

### 4.2 Vì sao UNIQUE trên `fb_campaign_id`, KHÔNG phải `(TKQC, campaign_id)`

Khảo sát prod: **21 `campaign_id` xuất hiện trên nhiều `ad_account_id`**. Tra sâu thì cả
21 đều là **bản sao TKQC** — cùng `facebook_ad_accounts.account_id` thật, khác PK. Không
có chiến dịch nào thật sự chạy trên 2 tài khoản.

Đây đúng lớp lỗi của MR !1199 (một TKQC thật có nhiều dòng bản sao, mỗi bản có daily
spend đầy đủ của cùng số tiền → cộng đôi hoặc hụt về 0). Đặt UNIQUE trên
`fb_campaign_id` khiến bẫy bản sao **tự chết ở tầng chiến dịch**: mọi bản sao TKQC quy về
đúng một dòng campaign.

### 4.3 Bẫy khi migrate tên chiến dịch

**9 `campaign_id` đang có nhiều `campaign_name` khác nhau** — tên đã bị đổi bên Facebook,
các ad cũ giữ tên cũ. Migrate phải lấy tên của ad có `last_seen_at` **mới nhất**, KHÔNG
dùng `MAX(name)` (ra tên theo thứ tự alphabet, vô nghĩa).

### 4.4 `google_ads_campaigns` — thêm 4 cột

`product`, `product_source`, `product_updated_by`, `product_updated_at`. Cùng ngữ nghĩa
với bảng Facebook. Không đụng gì tới luồng sync đang chạy.

### 4.5 Vì sao không gộp 2 nền tảng vào một bảng chung

Đã cân nhắc bảng `ad_campaigns` thống nhất. Bỏ vì:

- FK tài khoản quảng cáo hai nền tảng khác nhau (`facebook_ad_accounts` vs
  `google_ads_accounts`) → vẫn phải để 2 cột nullable, "thống nhất" chỉ là danh nghĩa.
- Phải migrate `google_ads_campaigns` đang chạy ổn định + sửa luồng sync Google. Rủi ro
  thật, lợi ích danh nghĩa.
- Hai nền tảng vốn khác grain: Facebook lưu chi tiêu ở **tầng ad**, Google ở **tầng
  campaign**.

## 5. Parser

Chạy sau mỗi lượt `facebook-ads-sync-ad-catalog` (cron mỗi giờ, phút 5) và
`google-ads-sync-campaign-spend`.

**Luật sắt: parser KHÔNG BAO GIỜ ghi đè dòng `product_source='manual'`.** Máy sai một
lần, người sửa một lần, không bao giờ phải sửa lại. Chỉ ghi khi `product` đang NULL hoặc
`product_source='auto'`.

Khi tên chiến dịch chứa 2+ mã SP: **không chọn**, để NULL. Chọn bừa một trong hai là tạo
số sai mà không ai biết.

## 6. Ảnh hưởng lên báo cáo (Sprint 3)

Hai tab digital nhận thêm ô lọc sản phẩm: **"Tất cả sản phẩm"** (mặc định, số y hệt hiện
tại) hoặc từng SP một.

Khi lọc 1 SP:
- **Doanh thu / số đơn / số data**: theo dòng `OrderItem` của SP đó — cùng ngữ nghĩa
  `product_ids` mà Báo cáo tổng đang dùng ([overview.py](../backend/apps/orders/reporting/overview.py)).
- **Chi phí**: tổng chi tiêu các chiến dịch đã gán về SP đó. **Số thật, không phân bổ.**
- **ME/RE, CPL**: tính trên 2 số thật ở trên → đọc được, không bị thổi phồng.

Ở chế độ "Tất cả sản phẩm", dưới bảng có **dòng chú thích** "Trong tổng chi phí có X
(Y%) chưa gán được về sản phẩm nào".

> **Đổi so với bản duyệt.** Ban đầu định làm một DÒNG trong bảng. Sai: mỗi dòng của bảng
> digital là một KÊNH và các kênh đã cộng đủ thành tổng, thêm một dòng nữa vào đó là cộng
> trùng. Dòng chú thích giữ nguyên ý nghĩa (nhìn thấy được, là danh sách việc cần làm) mà
> không phá số học của bảng.

Con số đó = **tổng chi phí của bảng − `assigned_ads_cost`**. Dùng phép TRỪ chứ không cộng
riêng phần `product_id IS NULL`, để hai vế luôn khớp tổng: chi tiêu Facebook đọc ở grain
ad không khớp tuyệt đối với grain account (chốt so khớp ~0,98), và phần chênh grain đó
đúng là "chưa quy được về sản phẩm nào".

Một hành vi đáng chú ý: lọc một SP mà kỳ đó SP chưa ra đơn nào thì dòng vẫn hiện với
doanh thu 0 và chi phí thật. **Cố ý** — đó chính là thứ phòng Ads cần thấy nhất.

## 7. Phân quyền

Thêm action `edit` vào section `ads-report` trong
[rbac_catalog.py](../backend/apps/users/rbac_catalog.py) — cùng khuôn với
`foxia-report: ("view", "edit")`, nơi `edit` cũng chỉ dùng cho việc gán nguồn vào kênh.

**KHÔNG grandfather** cho mọi role đang có `ads-report:view` (khác cách migration 0039 đã
làm với `call-quality:edit`). Yêu cầu là chỉ phòng Ads sửa được, nên migration chỉ cấp
cho role của phòng Ads.

---

## 7b. Backfill chạy tay + ba mức tin cậy

Lịch beat chạy mỗi giờ (phút 12 và 17). Không muốn chờ thì chạy tay — mặc định XEM THỬ,
in mẫu để soát trước khi ghi:

```bash
python manage.py backfill_ads_campaign_products --provider facebook                 # xem thử, luật chặt
python manage.py backfill_ads_campaign_products --provider facebook --loose --apply # ghi thật, thêm luật đoán
python manage.py backfill_ads_campaign_products --provider google --loose --apply
```

Một lệnh cho cả hai nguồn (từ 2026-09-23): lệnh sống ở `apps/orders`, mỗi app quảng
cáo cắm provider qua `apps/orders/contracts/ads_campaign_backfill_port.py` vì hai
app đó nằm cùng tầng, không import lẫn nhau được. Tên cũ
`backfill_fb_campaign_products` / `backfill_google_campaign_products` vẫn chạy
(alias, deprecated).

`product_source` có ba giá trị, để người soát chỉ phải xem lại phần đáng ngờ:

| Nguồn | Nghĩa | Ai xử lý |
|---|---|---|
| `auto` | Mã gốc chỉ có MỘT sản phẩm — không phải chọn gì | tin được |
| `auto2` | Máy **ĐOÁN**: mã gốc nhiều biến thể (chọn theo doanh thu), tên nhắc nhiều mã (lấy mã đầu), hoặc gán theo bí danh học được | **cần soát** — tab Gán sản phẩm có bộ lọc riêng |
| `manual` | Người gán | parser không bao giờ đụng |

Luật nới lỏng (`--loose`) thêm 2 đường:
- Tên nhắc nhiều mã → lấy mã **xuất hiện đầu tiên** (thường là sản phẩm chính).
- **Học bí danh từ chiến dịch đã gán**: token nào luôn đi cùng một sản phẩm và xuất hiện
  ≥3 lần thì thành bí danh. Nhờ đó `Desiglobal_BonNgamChanGapGon_...` (không mang mã SKU)
  vẫn gán được. Đo trên prod: học được 39 token, vớt thêm 61 chiến dịch.

Độ phủ cộng dồn trên 3.014 chiến dịch chưa gán của prod: 2.294 (chặt) + 172 (biến thể theo
doanh thu) + 9 (nhiều mã lấy cái đầu) + 61 (bí danh) ≈ **84%**. Khoảng 478 chiến dịch còn
lại mang tên không có thông tin gì (`06/05 bài 3`, `remarketing`, `14/07 video3`) — chỉ gán
tay được.

## 8. Sprint

### Sprint 1 — Nền móng dữ liệu (expand thuần, số không đổi)

- [x] Model + migration `facebook_ad_campaigns`, UNIQUE `fb_campaign_id`
- [x] Migration dữ liệu: dựng campaign từ `facebook_ads`, lấy tên theo `last_seen_at` mới nhất
- [x] FK `facebook_ads.campaign` + backfill, bỏ 2 cột chuỗi cũ
- [x] Sync ad catalog + lazy fetch của sync chi tiêu cùng ghi qua `resolve_campaign`
- [x] `facebook_ads/campaign_report.py` đọc bảng mới
- [x] Test: bản sao TKQC quy về 1 campaign; tên rỗng không xoá tên thật; tên lấy đúng ad mới nhất

**Trạng thái:** XONG (147 test `apps/facebook_ads` xanh).

Migration 0008 đã chạy thử trên DB scratch với dữ liệu mô phỏng đúng 3 bẫy của prod
(bản sao TKQC, chiến dịch đổi tên, ad không gắn chiến dịch) — cả chiều thuận lẫn chiều
rollback đều đúng.

**Bẫy đã xử lý:** `makemigrations` xếp `RemoveField` LÊN ĐẦU. Chạy nguyên như thế là
mất sạch dữ liệu chiến dịch trước khi kịp chép sang bảng mới. File 0008 xếp tay lại
thứ tự, và có test `makemigrations --check` chốt để không ai regenerate đè lên.

### Sprint 2 — Gán sản phẩm

- [x] 4 cột `product*` cho cả 2 bảng campaign
- [x] Parser `apps/catalog/sku_text_match.py` + 2 task theo lịch (phút 12 và 17), tôn trọng `manual`
- [x] Không cần bước backfill thủ công — task theo lịch quét cả chiến dịch cũ
- [x] `GET/PATCH /api/{facebook-ads,google-ads}/campaign-products`, gate bằng `require_method_permission`
- [x] `ads-report:edit` vào rbac_catalog + migration 0043 seed quyền
- [x] Tab "Gán sản phẩm" trên trang Báo cáo ads: lọc chưa gán/đã gán, tìm theo tên, hiện chi phí chưa gán
- [x] Test: parser không đè `manual`; 2+ mã SP để trống; Shopping/Pmax để trống; gỡ gán vẫn giữ `manual`

**Trạng thái:** XONG.

**Đo lại độ phủ với luật cuối (khớp CHÍNH XÁC full SKU):** Facebook **95,5%** chi phí
T8, Google 40,9%. Chỉ 0,1% rơi vào mã gốc mơ hồ nên bỏ hẳn nhánh khớp mã gốc — đơn giản
hơn và không có chỗ nào đoán bừa.

**Còn nợ:** admin phải cấp `ads-report:edit` cho role phòng Ads sau khi deploy.
Migration CỐ Ý không tự cấp cho ai.

### Sprint 3 — Bộ lọc sản phẩm trên báo cáo digital

- [x] Backend: `product_ids` cho `/api/orders/reports/ads` (chỉ ảnh hưởng khối `daily_breakdown`)
- [x] Port `spend_rows_by_account_product` + adapter 2 nền tảng
- [x] `assigned_ads_cost` trong payload → FE dựng rổ "Chưa gán chiến dịch"
- [x] FE: ô lọc SP ở cả 2 tab digital, dùng `useAllProductOptions`
- [x] Test: doanh thu chỉ tính dòng SP được lọc; chi phí chỉ từ chiến dịch đã gán; tổng − đã gán = chưa gán

**Trạng thái:** XONG.

**Bẫy đã xử lý:** chi phí theo sản phẩm phải khử bản sao TKQC NGAY TRONG adapter vì hai
nền tảng khác grain. Facebook đọc ở grain ad (`fb_ad_id` unique toàn cục) nên chỉ được
CỘNG — dùng `Max` như đường chi phí theo tài khoản sẽ làm HỤT tiền, prod có 5 TKQC thật
mà ad nằm rải trên nhiều dòng tài khoản. Google đọc ở grain campaign, cùng một campaign
nằm dưới nhiều dòng tài khoản nên PHẢI `Max` trước rồi mới cộng.

**Phạm vi cố ý bỏ:** `product_ids` chỉ tác động khối `daily_breakdown` (khối duy nhất
nuôi 2 tab digital). Các khối khác giữ nguyên nên tab Tổng quan không đổi số.

### Sprint 4 — Bộ lọc TKQC trên tab Gán sản phẩm

- [x] Backend: tham số `account` cho `GET /api/{facebook-ads,google-ads}/campaign-products`
- [x] Payload trả thêm `accounts` — danh sách TKQC có chiến dịch, KHÔNG đổi theo bộ lọc đang chọn
- [x] FE: ô "Tài khoản QC" gộp hai nền tảng, rỗng = tất cả
- [x] Test: gom bản sao TKQC theo `account_id`; chọn TKQC nền tảng kia → rỗng; danh sách không rụng theo bộ lọc

**Trạng thái:** XONG.

**Hai quyết định đáng nhớ:**

1. **Lọc theo id BÊN NỀN TẢNG, không theo pk.** Một TKQC thật nằm ở nhiều dòng
   `facebook_ad_accounts` — mỗi `FacebookApp` một dòng (lớp lỗi MR !1199). Lọc theo pk
   sẽ giấu mất chính chiến dịch của TKQC đang chọn ở dòng còn lại. Facebook gom theo
   `account_id`, Google gom theo `customer_id`.
2. **Giá trị bộ lọc mang tiền tố nền tảng** (`facebook:<id>` / `google:<id>`) và được gửi
   cho CẢ hai endpoint. Lý do gửi cả hai thay vì tắt request bên kia: `accounts` của ô lọc
   lấy từ chính response, tắt đi là danh sách rụng mất một nửa. Lý do phải có tiền tố:
   id TKQC Facebook và `customer_id` Google đều là chuỗi số, so id thô có thể trùng.

**Đổi hành vi cần biết:** ô "Chi phí chưa gán" thu hẹp theo TKQC đang lọc (đúng ý — nó
trả lời "TKQC này còn bao nhiêu tiền chưa gán"), nên nó KHÔNG còn khớp với dòng
"Chưa gán chiến dịch" của báo cáo digital khi có lọc TKQC.

## 9. Rủi ro

| Rủi ro | Xử lý |
|---|---|
| Sprint 3 deploy xong, chi phí ở tab digital tách nhỏ + hiện rổ "Chưa gán" | **Báo phòng Ads trước.** Nhìn như số bị hụt nếu không nói trước |
| Đổi tên chiến dịch bên nền tảng làm mất dấu vết tên cũ | Ngoài phạm vi đợt này. Bảng campaign là chỗ để về sau thêm bảng lịch sử tên |
| Chiến dịch mới có spend trước khi sync catalog kịp (trễ tới 1h) | Rơi tạm vào "Chưa gán", tự hết sau lượt sync |
| `facebook_ad_daily_spends` chỉ có dữ liệu từ 01/08/2026 | Lọc kỳ trước tháng 8 sẽ không có phần Facebook. Không phải lỗi đợt này, nhưng cần biết khi đối chiếu |
