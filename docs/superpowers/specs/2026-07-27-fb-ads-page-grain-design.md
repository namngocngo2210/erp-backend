# Chi phí & doanh thu Facebook Ads theo Page (grain ad)

> Ngày: 2026-07-27 · Branch: `feat/fb-ads-page-grain`

## 1. Bối cảnh & vấn đề

Hệ thống đang kéo chi tiêu Facebook Ads ở **một grain duy nhất**: tài khoản quảng cáo × ngày.

- `services.py:392` gọi insights với `level="account"`, `time_increment=1`.
- Kết quả đổ vào `facebook_ad_account_daily_spends` (`models.py:110`) — khoá `(ad_account, date)`.
- Báo cáo ghép chi phí ↔ doanh thu qua bảng nối `channel_ad_accounts` (Channel ↔ TKQC).

Toàn bộ codebase **không có khái niệm Page** — không một tham chiếu `page_id` / fanpage nào.

Hệ quả nghiệp vụ nghiêm trọng nhất nằm ở cách phân bổ chi phí trong
`lark_ads_report_sync.py:129`:

```sql
COALESCE(SUM(s.spend::numeric / acc_count.n) * 1.1, 0) AS cost
```

Một TKQC map vào N kênh thì chi phí bị **chia đều cho N**. Đây không phải chi phí thật của
kênh mà là số ước lượng — nhưng nó hiển thị trên báo cáo và đẩy sang Lark y như số thật.
Cùng một TKQC chạy cho 3 page với ngân sách 70/20/10 vẫn ra 33/33/33.

Mục tiêu: đo chi phí và doanh thu **theo từng page**, bằng số liệu thật thay vì phân bổ đều.

## 2. Quyết định đã chốt

| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Định danh nối chi phí ↔ đơn | **`ad_id`** | FB tự điền qua URL dynamic param `{{ad.id}}`, không phải sửa tay từng ad. Từ `ad_id` suy ra được page, adset, campaign, TKQC → roll-up mọi cấp từ một cột |
| Nguồn attribution | Đơn **landing**, qua query param trên URL đích | Luồng messenger/hotline không mang danh tính page, nằm ngoài phạm vi đợt này |
| Grain lưu trữ | **ad × ngày** | <50 TKQC, vài trăm ad active ⇒ ~110k dòng/năm, Postgres xử lý thoải mái |
| Giai đoạn lịch sử chưa gắn `ad_id` | **Doanh thu** chỉ tính từ ngày go-live; **chi phí** có đủ lịch sử | Tránh vùng "chi phí có, doanh thu 0" làm tỷ lệ CP/DT sai lệch |
| Thứ tự triển khai | **Hai giai đoạn** — chi phí theo page ra trước, doanh thu ghép sau (§2.1) | Chi phí không phụ thuộc landing; ra sớm là dùng được sớm |
| Đầu ra | Thêm chiều **Page** vào `AdsReportPage` sẵn có | Tái dùng KPI row / biểu đồ / bảng gộp tháng, không thêm màn hình mới |

Ba điểm **không** làm đợt này (YAGNI):

- Không map page cho đơn messenger/hotline/sàn.
- Không suy ngược page cho đơn lịch sử từ `url_page` — đó lại là một con số ước lượng
  trông như số thật, đúng căn bệnh đang muốn chữa.
- Không xoá bảng account-grain cũ (xem §6).

### 2.1 Hai giai đoạn triển khai

Chi phí theo page **không phụ thuộc gì** vào landing. Tách đôi để giá trị ra sớm:

| | Giai đoạn A — Chi phí | Giai đoạn B — Doanh thu |
|---|---|---|
| Nội dung | Catalog page/ad + chi tiêu grain ad + backfill 365 ngày | Landing gắn `ad_id` → `Order.fb_ad_id` → ghép doanh thu |
| Báo cáo hiện | Chi phí, hiển thị, clicks, CPM/CPC theo page | Thêm doanh thu, số đơn chốt, tỷ lệ CP/DT |
| Phụ thuộc ngoài | Không | Landing sửa URL đích + gửi `ad_id` |
| Giá trị ngay | Biết page nào đốt bao nhiêu tiền, có lịch sử 1 năm — hiện chưa biết | Đo hiệu quả thật của từng page |

Ghép tự động, không thao tác tay: join là `fa.fb_ad_id = o.fb_ad_id`. Landing bật `ad_id`
ngày nào thì đơn từ ngày đó tự vào báo cáo.

**Giai đoạn A ẩn hẳn cột doanh thu và tỷ lệ CP/DT** — không hiện 0, không hiện ô trống.
Bảng chỉ có chi phí thì người đọc hiểu đúng là bảng chi phí. Hiện cột doanh thu rỗng bên
cạnh chi phí thật là mời người ta tự kết luận "page này tiêu tiền mà không ra đơn".

## 3. Kiến trúc tổng thể

```
FB Ads Manager                 Landing                    ERP
──────────────                 ───────                    ───
URL đích:                      nhận ?ad_id=...            Order.fb_ad_id
 ?ad_id={{ad.id}}      ───►    gửi kèm khi POST    ───►   (cột MỚI, index bộ phận)
                               /api/orders/ingest/landing
                                                             │
Graph API                                                    │ join ad_id
 /{acct}/ads          ───►     FacebookAd (danh mục)  ◄──────┘
   creative→page_id             + FacebookPage
 /{acct}/insights                    │
   level=ad          ───►     FacebookAdDailySpend
                               (ad × ngày)
```

`ad_id` là khoá nối duy nhất giữa hai phía. Chi phí và doanh thu gặp nhau ở bảng danh mục
`FacebookAd`; page chỉ là một thuộc tính suy ra từ đó, nên đổi chiều báo cáo sang campaign
hay adset sau này không phải đụng schema.

## 4. Schema

### 4.1 Bảng mới — app `facebook_ads`

**`facebook_pages`** — danh mục page

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `fb_page_id` | `CharField(64)`, unique | ID page bên FB. Unique toàn cục, không gắn app: một page có thể được nhiều BM chạy quảng cáo |
| `name` | `CharField(255)` | Lấy từ `/{fb_page_id}?fields=name` |
| `last_synced_at` | `DateTimeField` | |

**`facebook_ads`** — danh mục quảng cáo

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `fb_ad_id` | `CharField(64)`, unique | ID ad bên FB, unique toàn cục |
| `ad_account` | FK `FacebookAdAccount` | Suy ra app/token để gọi API |
| `page` | FK `FacebookPage`, **nullable** | Null = chưa resolve được (xem §5.3) |
| `name` | `CharField(255)` | |
| `campaign_id` / `campaign_name` | `CharField(64)` / `CharField(255)` | |
| `adset_id` / `adset_name` | `CharField(64)` / `CharField(255)` | |
| `last_seen_at` | `DateTimeField` | Lần cuối còn xuất hiện trong `/ads` |

Index: `(ad_account, page)`, `(page,)`.

**`facebook_ad_daily_spends`** — chi tiêu

| Cột | Kiểu |
|---|---|
| `ad` | FK `FacebookAd` |
| `date` | `DateField` |
| `spend` | `DecimalField(18, 2)` |
| `impressions` / `clicks` / `reach` | `BigIntegerField` |
| `last_synced_at` | `DateTimeField(auto_now=True)` |

Constraint: unique `(ad, date)`. Index: `(date,)`, `(ad, date)`.

**Không denormalize page xuống bảng chi tiêu.** Ở mức ~110k dòng/năm, join qua
`facebook_ads` rẻ; denorm chỉ tạo nguy cơ hai nguồn sự thật lệch nhau khi catalog cập nhật.

### 4.1.1 Quy ước đặt tên — tránh va chạm `ad_id` / `page_id`

Django sinh cột FK tên `<field>_id`. Nếu đặt field định danh FB là `ad_id` thì
`facebook_ad_daily_spends.ad_id` (FK trỏ `facebook_ads.id`) và `facebook_ads.ad_id`
(chuỗi ID bên FB) trùng tên nhưng khác hoàn toàn về nghĩa — nguồn lỗi join âm thầm.

Quy ước bắt buộc:

| Tên | Nghĩa |
|---|---|
| `facebook_ads.fb_ad_id` | ID ad **bên Facebook** (chuỗi số) |
| `facebook_ads.page_id` | FK → `facebook_pages.id` (khoá nội bộ) |
| `facebook_pages.fb_page_id` | ID page **bên Facebook** (chuỗi số) |
| `facebook_ad_daily_spends.ad_id` | FK → `facebook_ads.id` (khoá nội bộ) |
| `orders.fb_ad_id` | ID ad bên Facebook — join thẳng với `facebook_ads.fb_ad_id` |

Tiền tố `fb_` = định danh của Facebook; không tiền tố = khoá nội bộ. Trùng với quy ước
sẵn có ở `FacebookAdAccount.fb_id` (`models.py:87`).

### 4.2 Sửa bảng `orders`

```python
fb_ad_id = models.CharField(max_length=64, blank=True, default="", db_default="")
```

Index bộ phận — tập đơn có `ad_id` nhỏ hơn nhiều toàn bảng:

```python
models.Index(fields=["fb_ad_id"], condition=Q(fb_ad_id__gt=""), name="ord_fb_ad_id_idx")
```

`db_default=""` là bắt buộc: Postgres 11+ thêm cột có default không-volatile mà không
rewrite bảng, nên migration chạy trên `orders` production không khoá bảng lâu.

### 4.3 Cấu hình

`FB_PAGE_REPORT_START_DATE` (`settings_parts/base.py`) — mốc go-live. Báo cáo theo page
từ chối mọi khoảng ngày bắt đầu trước mốc này.

## 5. Đồng bộ dữ liệu

Task hiện tại `sync_all_daily_spend` có `soft_time_limit=300` (`tasks.py:47`) và nhánh
backfill 365 ngày (`services.py:410`). Nhét thêm việc ở grain ad vào đó sẽ vỡ. Tách 3 nhịp:

### 5.1 `sync_fb_ad_catalog` — cron 1 giờ/lần

```
GET /{ad_account.fb_id}/ads
  ?fields=id,name,
          campaign{id,name},
          adset{id,name,promoted_object},
          creative{id,effective_object_story_id,object_story_spec{page_id}}
  &limit=200
```

Thứ tự resolve `page_id` (dừng ở cái đầu tiên có giá trị):

1. `creative.effective_object_story_id` — dạng `{page_id}_{post_id}`, cắt trước dấu `_`.
2. `creative.object_story_spec.page_id`.
3. `adset.promoted_object.page_id` — áp cho campaign tin nhắn / lead.

Tên ad / campaign / adset về ngay trong chính lời gọi này và được ghi đè mỗi lượt sync —
đổi tên trên Ads Manager thì sau tối đa 1 giờ báo cáo hiện tên mới.

Tên page phải gọi riêng: `/{fb_page_id}?fields=name`, gộp Batch API. Chỉ gọi cho page mới
phát hiện hoặc page có `last_synced_at` cũ hơn 24 giờ — tên page hiếm khi đổi, không đáng
đốt quota mỗi giờ.

Dùng lại nguyên cơ chế phân trang + `_error_from_response` + phân loại `FacebookTokenError`
đã có trong `services.py`.

### 5.2 `sync_fb_ad_daily_spend` — theo cron chi tiêu hiện tại, cửa sổ 7 ngày

Batch API như `fetch_insights_batch` nhưng `level="ad"`:

```
fields=ad_id,spend,impressions,clicks,reach
level=ad&time_increment=1
time_range={"since": today-7, "until": today}
```

Giữ nguyên `RECENT_DAYS = 7` và lý do đã ghi ở `services.py:383` — preset `last_7d` của FB
**không** gồm hôm nay, nên phải dùng `time_range`.

Upsert bằng `bulk_create(..., update_conflicts=True)` trên `(ad, date)` thay vì
`update_or_create` từng dòng như `_upsert_daily_rows` hiện tại — số dòng mỗi lượt tăng
khoảng hai bậc.

### 5.3 `backfill_fb_ad_daily_spend` — management command, chạy tay

Tham số `--since --until --chunk-months=1`. **Không** đưa vào cron. Chia theo tháng để mỗi
lượt gọi nằm trong giới hạn payload của Batch API.

### 5.4 Ad đã xoá trên FB

Insights vẫn trả chi tiêu lịch sử của ad đã xoá, nhưng `/ads` không liệt kê nữa → catalog
thiếu, chi tiêu không map được page.

Xử lý: gặp `ad_id` lạ trong insights thì lazy-fetch `/{ad_id}?fields=...` bổ sung vào
catalog. Không lấy được nữa thì tạo `FacebookAd` với `page = NULL` — chi tiêu vẫn được ghi
nhận, chỉ rơi vào nhóm "Không rõ page" trên báo cáo. Mất page còn hơn mất chi phí.

## 6. Giữ nguyên bảng account-grain cũ

**Tổng chi phí cộng từ grain ad sẽ thấp hơn `level=account`.** Facebook có các khoản không
gán được về ad cụ thể (điều chỉnh hoá đơn, credit, chi tiêu của ad đã bị xoá khỏi hệ thống).
Nếu bỏ bảng cũ để dùng grain ad làm nguồn duy nhất, tổng chi phí toàn công ty sẽ tự nhiên
hụt đi mà không ai biết.

Vì vậy:

- Giữ `facebook_ad_account_daily_spends` và luồng đồng bộ hiện tại **nguyên vẹn**. Báo cáo
  kênh và 2 bảng Lark tiếp tục đọc từ đó.
- Thêm chốt so khớp trong `sync_fb_ad_daily_spend`: nếu
  `SUM(ad_spend) / account_spend < 0.98` cho một `(ad_account, date)` thì `send_ops_alert`.

Chốt này **chỉ áp cho ngày đã đóng** (từ hôm qua trở về trước). Chi tiêu hôm nay ở hai
grain có độ trễ khác nhau nên luôn lệch — kiểm ngày đang chạy sẽ sinh cảnh báo rác mỗi
lượt cron, và cảnh báo rác thì chẳng ai đọc nữa. Dedupe theo
`key=f"fb:spend_variance:{ad_account_id}:{date}"` như các alert hiện có.

Chốt này cùng tinh thần với chốt chặn báo cáo khi token hỏng (`lark_ads_report_sync.py:331`)
dựng sau sự cố 25–27/07: sai số im lặng nguy hiểm hơn báo cáo đứng yên.

## 7. Attribution phía đơn hàng

### 7.1 Cấu hình trên FB Ads Manager (việc vận hành, không phải code)

URL đích của mọi ad phải mang `?ad_id={{ad.id}}`. Facebook thay thế token này lúc người
dùng click.

**Chỉ gắn ID, tuyệt đối không gắn tên.** Facebook có sẵn `{{ad.name}}`, `{{campaign.name}}`
và nhìn qua thì tiện — nhưng gắn vào URL là sai vì ba lẽ:

- Tên ad ở đây có dấu tiếng Việt, khoảng trắng, ký tự `|` `-` `#` → URL-encode ra chuỗi
  rác dài, dễ bị proxy/redirect cắt hỏng.
- Tên đổi lúc nào cũng được. Đổi tên ad hôm nay là đơn hôm qua và đơn hôm nay mang hai
  nhãn khác nhau cho **cùng một quảng cáo** — báo cáo tự tách đôi.
- Tên đã có sẵn trong catalog lấy từ Graph API (§5.1), lấy thêm lần nữa qua URL là tạo
  nguồn sự thật thứ hai cho cùng một dữ liệu.

ID là bất biến, ngắn, toàn chữ số. Tên tra từ catalog.

### 7.2 Nhận `ad_id` khi ingest

`LandingOrderSerializer` (`kiotviet_landing_serializers.py:186`) nhận thêm field `ad_id`
tuỳ chọn. Thứ tự ưu tiên:

1. Field `ad_id` gửi thẳng trong body — nếu landing chủ động gửi.
2. Parse query string từ `link` — fallback khi landing chỉ chuyển tiếp URL.

Hàm thuần `extract_ad_id(link: str) -> str`:

- `link` có thể là **nhiều URL nối bằng dấu phẩy** — `validate()` hiện tại làm
  `attrs["url_page"] = ",".join(link_list)` (dòng 223). Luật: lấy `ad_id` hợp lệ **đầu tiên**.
- Hợp lệ = chuỗi toàn chữ số, độ dài ≤ 64.
- Không có → trả `""`, đơn vẫn được tạo bình thường.

`LandingIngestTrace.payload` đã lưu nguyên body (`order_ingest_service.py:113`) nên mọi
trường hợp parse hụt đều truy lại được — không cần thêm log riêng.

### 7.3 Đo sức khoẻ attribution

Counter Prometheus `landing_ingest_ad_id_total{present="true"|"false"}`, đặt cạnh
`landing_ingest_validated_total` sẵn có trong `apps/orders/metrics.py`. Landing quên gắn
tag là nhìn thấy ngay, không phải chờ báo cáo cuối tháng mới phát hiện.

## 8. Báo cáo

### 8.1 API

Mở rộng `GET /api/orders/reports/ads` (`orders/urls.py:124` → `ads_report_summary`) bằng
query param `dimension=channel|page`, mặc định `channel` để không phá contract hiện tại.

Repository (`_repositories_reports.py`) thêm:

- `summary_ads_revenue_by_page(...)`
- `summary_ads_daily_breakdown_by_page(...)`

### 8.2 SQL

Doanh thu — giữ nguyên bộ lọc đơn chốt của báo cáo kênh (`sales_status_code`, `status <> 6`,
`invoice_status <> 2`, loại branch và kênh KOC/Sale FB):

```sql
FROM orders o
JOIN order_assessments oa  ON oa.order_id = o.id
JOIN facebook_ads fa       ON fa.fb_ad_id = o.fb_ad_id   -- ID bên FB ↔ ID bên FB
LEFT JOIN facebook_pages p ON p.id = fa.page_id          -- FK nội bộ
WHERE o.fb_ad_id <> ''
  AND ...
GROUP BY d, p.id
```

Chi phí:

```sql
FROM facebook_ad_daily_spends s
JOIN facebook_ads fa       ON fa.id = s.ad_id            -- FK nội bộ
LEFT JOIN facebook_pages p ON p.id = fa.page_id
WHERE s.date BETWEEN %s AND %s
GROUP BY s.date, p.id
```

**Không còn `SUM(spend)/acc_count.n`.** Đây là giá trị lớn nhất của cả đợt việc — lớn hơn
bản thân chiều page.

Giữ `ADS_COST_MULTIPLIER = 1.1` để hai báo cáo nhất quán.

### 8.3 Phân quyền

Báo cáo hiện scope theo TKQC user được thấy — `_accessible_ads_ad_account_ids(user)`
(`_repositories_reports.py:752`). Chiều page dùng lại đúng hàm đó rồi lọc
`facebook_ads.ad_account_id IN (...)`. User chỉ thấy page thuộc TKQC mình được gán.

### 8.4 Hai dòng đặc biệt bắt buộc có

| Dòng | Nghĩa | Vì sao phải lộ ra |
|---|---|---|
| **"Chưa gắn ad_id"** | Đơn landing sau go-live nhưng thiếu `ad_id` | Thước đo sức khoẻ attribution. Gộp im lặng vào tổng là che mất việc landing quên gắn tag |
| **"Không rõ page"** | Chi tiêu của ad không resolve được page | Bảo toàn tổng chi phí. Bỏ đi thì tổng báo cáo page không khớp tổng TKQC |

### 8.5 Tên hiển thị

Mọi dòng báo cáo trả về **cặp `(id, name)`**, không bao giờ chỉ một trong hai. ID để nhóm
và tra cứu, tên để người đọc hiểu.

```json
{ "page_id": "1234567890", "page_name": "Desi Chính Hãng", "spend": "...", ... }
```

**Nhóm theo ID, hiển thị theo tên.** Không được `GROUP BY name` — hai page khác nhau
hoàn toàn có thể trùng tên, và tên đổi thì nhóm vỡ.

**Tên hiển thị là tên hiện tại**, không snapshot theo ngày. Đổi tên page thì toàn bộ lịch
sử hiện tên mới. Đây là lựa chọn có chủ ý: người dùng tìm "page X" theo tên họ đang thấy
trên Facebook hôm nay, không phải tên hồi tháng 3. Muốn giữ lịch sử tên phải làm bảng biến
thiên theo thời gian — không tương xứng với lợi ích, để dành khi nào thực sự cần.

Quy tắc dự phòng khi thiếu tên, theo thứ tự:

| Tình huống | Nhãn hiển thị |
|---|---|
| Có tên | Tên |
| Catalog có page nhưng `name` rỗng (API trả thiếu) | `Page 1234567890` |
| Ad chưa resolve được page | `Không rõ page` |
| Đơn không có `ad_id` | `Chưa gắn ad_id` |

Không bao giờ để ô trống — ô trống khiến người đọc tưởng lỗi hiển thị và tự bỏ qua dòng đó,
trong khi hai dòng cuối chính là dòng cần chú ý nhất.

Cùng quy tắc áp cho `campaign_name` / `adset_name` / `ad.name` khi khoan sâu về sau.

### 8.6 Vùng chưa có dữ liệu doanh thu

Chi phí luôn trả về cho mọi khoảng ngày. Doanh thu thì không — và cơ chế ở đây khác với
bản thiết kế đầu (từ chối cả request bằng HTTP 400). Chặn cứng như vậy sẽ chặn luôn chi phí,
mâu thuẫn với giai đoạn A ở §2.1.

Cơ chế thay thế — response mang một trường mô tả chính nó:

```json
{
  "dimension": "page",
  "revenue_available_from": "2026-08-15",   // null = chưa go-live, giai đoạn A
  "rows": [ { "page_id": "...", "cost": "...", "revenue": null, "ratio_pct": null } ]
}
```

Quy tắc:

- `FB_PAGE_REPORT_START_DATE` chưa set → `revenue_available_from = null`, mọi `revenue`
  và `ratio_pct` trả `null`.
- Đã set → ngày < mốc trả `null`; từ mốc trở đi trả số thật.
- **`null` chứ không phải `0`.** Không có dữ liệu và doanh thu bằng không là hai chuyện
  khác nhau; gộp làm một là đúng lỗi mà `lark_ads_report_sync.py:331` phải dựng chốt chặn
  để tránh sau sự cố 25–27/07.

FE: `revenue_available_from = null` → ẩn hẳn cột doanh thu, số đơn, tỷ lệ CP/DT. Có mốc mà
khoảng chọn vắt qua mốc → hiện cột, các ngày trước mốc để `—` kèm chú thích một dòng.

Quyết định `null` nằm ở backend, không phải ở FE — gọi thẳng API cũng không lấy được số 0
giả.

### 8.7 Frontend

`AdsReportPage.tsx` thêm bộ chọn chiều **Kênh / Page**. Tái dùng `AdsKpiRow`,
`DailyTrendChart`, `DailyBreakdownTable`, `report-grouping.ts` — cấu trúc dữ liệu trả về
giữ nguyên hình dạng, chỉ đổi nhãn nhóm từ `channel_*` sang `page_*`.

## 9. Cảnh báo về chỉ số `reach`

`reach` **không cộng được** giữa các ngày — cùng một người xem quảng cáo 3 ngày liên tiếp
được đếm 1 lần trong reach của cả kỳ, nhưng 3 lần khi cộng reach từng ngày.

Code hiện tại đã cộng dồn (`spend_report_view.py:95`: `reach=Sum("reach")`). Ở grain ad,
sai số này **lớn hơn nhiều** vì cùng một người thường thấy nhiều ad trong cùng page.

Quyết định: **không hiển thị tổng `reach`** trên báo cáo theo page. Vẫn lưu trong DB
(chi phí lưu bằng 0) để phân tích từng ad khi cần. Hành vi bảng account-grain cũ giữ nguyên,
không sửa trong phạm vi này.

## 10. Kiểm thử

Viết test trước theo TDD. Phần thuần logic tách khỏi I/O để test không cần mock Graph API:

| Hàm thuần | Ca kiểm thử |
|---|---|
| `extract_ad_id(link)` | có param; nhiều link lấy cái đầu; không có param; giá trị không phải số; chuỗi rỗng |
| `resolve_page_id(ad_payload)` | lấy từ `effective_object_story_id`; fallback `object_story_spec`; fallback `promoted_object`; không có gì → `None` |
| `spend_variance_ratio(...)` | dưới ngưỡng → cảnh báo; bằng/trên ngưỡng → im lặng; `account_spend = 0` → không chia cho 0 |

Test tích hợp:

- Upsert chi tiêu grain ad idempotent (chạy 2 lần không nhân đôi dòng).
- Insights trả `ad_id` không có trong catalog → lazy-fetch, không mất chi tiêu.
- Ingest landing có `ad_id` → `Order.fb_ad_id` được set; không có → đơn vẫn tạo được.
- SQL báo cáo page: doanh thu + chi phí + hai dòng đặc biệt.
- `FB_PAGE_REPORT_START_DATE` chưa set → `revenue_available_from = null`, mọi `revenue`
  và `ratio_pct` là `null` (không phải `0`); chi phí vẫn trả đủ.
- Khoảng ngày vắt qua mốc go-live → ngày trước mốc `revenue = null`, từ mốc trở đi có số.
- Scope theo TKQC: user chỉ thấy page thuộc TKQC được gán.
- Sub-request lỗi xác thực trong batch level=ad → `FacebookTokenError`, không ghi 0 lặng lẽ
  (giữ đúng bài học ở `services.py:312`).

## 11. Rủi ro

| Rủi ro | Mức | Xử lý |
|---|---|---|
| Param `ad_id` bị nuốt qua redirect / link rút gọn | **Cao** | Sprint B0 — kiểm chứng đầu-cuối trên môi trường thật **trước** khi viết B1. Nếu landing có redirect trung gian, phải sửa để giữ query string |
| Ad xoá trên FB → mất page | Trung bình | Lazy-fetch; không được thì vào "Không rõ page", chi tiêu vẫn giữ (§5.4) |
| Rate limit Graph API khi backfill | Trung bình | Backfill là command chạy tay, chia theo tháng, ngoài cron |
| Tổng grain ad hụt so với account | Trung bình | Chốt so khớp 0.98 + alert (§6) |
| Người dùng đọc nhầm vùng chưa có dữ liệu | Thấp | `revenue = null` ở backend + FE ẩn cột khi chưa go-live (§8.6) |

## 12. Lộ trình sprint

Chia theo hai giai đoạn ở §2.1. Giai đoạn A ra production và dùng được **trước khi** landing
động vào bất cứ thứ gì.

### Giai đoạn A — Chi phí theo page

| Sprint | Nội dung | Phụ thuộc | Trạng thái |
|---|---|---|---|
| **A1** | Schema: `facebook_pages`, `facebook_ads`, `facebook_ad_daily_spends`, migration `orders.fb_ad_id` | — | ☑ Xong |
| **A2** | `sync_fb_ad_catalog` — kéo ad + resolve page + tên page, cron 1h | A1 | ☑ Xong |
| **A3** | `sync_fb_ad_daily_spend` (cửa sổ 7 ngày) + backfill command + chốt so khớp 0.98 | A2 | ☑ Xong |
| **A4** | API `dimension=page` — **chỉ chi phí**, `revenue_available_from = null`, phân quyền theo TKQC | A3 | ☑ Xong |
| **A5** | FE: bộ chọn chiều Kênh/Page, ẩn cột doanh thu khi chưa go-live | A4 | ☑ Xong |

**Đã làm trong nhánh `feat/fb-ads-page-grain`** (A1–A5 + vòng sửa cuối trước khi merge):
bảng page/ad/daily-spend ở grain ad × ngày, cron catalog 1h và cron chi tiêu grain ad,
backfill command theo tháng, chốt so khớp 0.98 có cảnh báo ops, API `reports/page` scope
theo TKQC, tab "Theo page" trên màn hình Báo cáo ads (chỉ chi phí). Migration `orders`
tách `AddField` khỏi `CREATE INDEX CONCURRENTLY`; hệ số `ADS_DAILY_COST_MULTIPLIER = 1.1`
dùng chung với báo cáo theo kênh (§8.2).

**Chưa làm, thuộc giai đoạn B**: toàn bộ phần doanh thu — `extract_ad_id` phía landing,
set `Order.fb_ad_id` khi ingest, SQL doanh thu grain page, hai dòng đặc biệt (§8.4), mốc
`FB_PAGE_REPORT_START_DATE` và các cột doanh thu/đơn chốt/CP-DT trên FE. Cột `orders.fb_ad_id`
đã có sẵn từ A1 nên B1 không phải đụng lại schema bảng `orders`.

`orders.fb_ad_id` nằm ở A1 dù giai đoạn A không dùng đến — gom mọi thay đổi schema vào một
lần migrate thay vì đụng lại bảng `orders` ở giai đoạn B.

**Chốt ra giai đoạn A**: backfill xong, tổng chi phí theo page khớp tổng theo TKQC trong
ngưỡng 0.98, không có alert so khớp treo.

### Giai đoạn B — Doanh thu theo page

| Sprint | Nội dung | Phụ thuộc | Trạng thái |
|---|---|---|---|
| **B0** | *(vận hành, không phải code)* Kiểm chứng `ad_id` sống sót đầu-cuối: gắn `{{ad.id}}` cho 1 ad thật → click → xem param còn nguyên khi landing nhận | — | ☐ Chưa làm |
| **B1** | `extract_ad_id`, serializer nhận `ad_id`, set `Order.fb_ad_id`, metric attribution | A1 + B0 | ☐ Chưa làm |
| **B2** | SQL doanh thu grain page, hai dòng đặc biệt, mốc `FB_PAGE_REPORT_START_DATE` | A4 + B1 | ☐ Chưa làm |
| **B3** | FE: hiện cột doanh thu / số đơn / tỷ lệ CP/DT, xử lý khoảng vắt qua mốc | B2 | ☐ Chưa làm |

**B0 phải xong trước B1.** Đây là rủi ro "Cao" duy nhất trong §11: nếu landing có redirect
trung gian hoặc dùng link rút gọn nuốt query string thì toàn bộ giai đoạn B vô nghĩa. Một
buổi kiểm chứng thủ công rẻ hơn nhiều so với viết xong rồi phát hiện không có dữ liệu vào.

**Cột mốc go-live doanh thu**: chỉ set `FB_PAGE_REPORT_START_DATE` sau khi B1 chạy
production và số liệu metric `landing_ingest_ad_id_total{present="true"}` cho thấy tỷ lệ
gắn tag đã ổn định. Set mốc lúc landing mới gắn được nửa số ad là tạo ra một vùng dữ liệu
thiếu mà báo cáo lại tuyên bố là đầy đủ.

### Chạy song song

A1 xong thì B0 làm được ngay (việc vận hành, không đụng code). Toàn bộ nhánh A2–A5 và
nhánh B1 độc lập nhau — hai người làm song song được.

## 13. Triển khai

Thứ tự bật KHÔNG được đảo:

1. **Chạy tay `sync_all_ad_catalog` một lượt** trước khi để beat của
   `sync_all_ad_spend` chạy lần đầu:

   ```bash
   docker compose exec backend python manage.py shell -c      "from apps.facebook_ads.tasks import _sync_all_ad_catalog; print(_sync_all_ad_catalog())"
   ```

   Vì sao bắt buộc: khi catalog còn rỗng, MỌI ad xuất hiện trong cửa sổ 7 ngày đều là ad
   lạ, và `_lazy_fetch_ad` phải bắn một GET tuần tự cho từng ad (timeout 30s, có retry).
   Vài trăm ad là vài trăm request nối đuôi nhau — vượt xa `soft_time_limit` của task, task
   bị hard-kill giữa chừng và giữ khoá `single_flight` tới hết TTL, nên tick sau cũng
   không chạy. Catalog ấm rồi thì lazy-fetch chỉ còn là trường hợp hiếm (ad đã xoá trên FB).

2. **Backfill lịch sử** bằng command riêng, chạy tay, KHÔNG đưa vào cron:

   ```bash
   python manage.py backfill_fb_ad_spend --since 2025-07-27 --until 2026-07-27
   ```

3. **Kiểm chốt ra** theo §12 (khớp ngưỡng 0.98, không alert treo, tỷ lệ "Không rõ page"
   dưới 5%) rồi mới mở màn hình cho người dùng.

Giới hạn thời gian của `sync_all_ad_spend` (`soft_time_limit=900`, `time_limit=960`, TTL
khoá 1000s) được đặt theo khối lượng grain ad, KHÔNG dùng chung con số 300s của grain
account. TTL khoá luôn phải lớn hơn `time_limit`, nếu không tick sau chen vào lúc worker
cũ còn đang bị hard-kill.

## 14. Nợ kỹ thuật ghi nhận (ra từ vòng review cuối)

Ba mục dưới đây đã được cân nhắc và **cố ý hoãn** — không mục nào chặn merge. Ghi ra đây
để chúng không chết theo ghi chú tạm của phiên làm việc.

**14.1 Migration `0110` không chạy lại được sau khi build index concurrent thất bại.**
Vì `atomic = False`, `AddField` tự commit. Nếu `CREATE INDEX CONCURRENTLY` hỏng sau đó thì
`django_migrations` chưa có dòng nào, chạy lại sẽ chết ở `ADD COLUMN fb_ad_id` — *column
already exists*. Cách gỡ (một bước, lỗi nổ to nên không sợ bỏ sót):

```sql
DROP INDEX CONCURRENTLY IF EXISTS ord_fb_ad_id_idx;
ALTER TABLE orders DROP COLUMN fb_ad_id;
```

rồi `migrate` lại. Đây là đánh đổi có chủ ý: thêm `IF NOT EXISTS` cho `AddField` sẽ khiến
lần chạy lại **bỏ qua** việc dựng lại index INVALID còn sót — một index vô dụng nhưng vẫn
bị Postgres duy trì mỗi lần ghi, và không ai biết. Lỗi to hơn lỗi im.

**14.2 Hằng số 1.1 tồn tại ở hai nơi với hai kiểu dữ liệu.**
`_repositories_reports.ADS_DAILY_COST_MULTIPLIER` là `Decimal`, còn
`lark_ads_report_sync.ADS_COST_MULTIPLIER` là `float` được bind thẳng làm tham số SQL —
tức `numeric * float8`. Đổi vế sau sang `Decimal` sẽ thành `numeric * numeric` và có thể
xê dịch chữ số cuối của báo cáo Lark đang chạy. Đó là thay đổi cần đo riêng, không gộp vào
vòng sửa trước merge. Nhánh này chỉ thêm một *consumer*, không thêm định nghĩa thứ ba.

Cách đóng lỗ trôi dạt với rủi ro bằng không: một test khẳng định
`float(ADS_DAILY_COST_MULTIPLIER) == ADS_COST_MULTIPLIER`.

**14.3 `_sync_all_daily_spend` (grain account) vẫn nuốt `SoftTimeLimitExceeded`.**
Hàm này kế thừa nguyên từ master và nhánh này cố ý không đụng vào — nó là đường chạy
production đã đúc từ sự cố token 25–27/07. Hệ quả bị chặn: chạy quá ~60s rồi bị hard-kill,
mất một tick, khoá tự hết hạn ở 400s. Nhưng sau nhánh này **2 trong 3 task xử lý tín hiệu
đúng còn 1 thì không** — chính sự bất đối xứng đó là thứ mục ruỗng dần. Nên trả nợ ở một
MR riêng, không kèm theo tính năng.
