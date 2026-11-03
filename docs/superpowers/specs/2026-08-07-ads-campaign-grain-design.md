# Chi phí quảng cáo theo toàn bộ TKQC và toàn bộ chiến dịch (Facebook + Google)

> Ngày: 2026-08-07 · Nối tiếp `2026-07-27-fb-ads-page-grain-design.md`

## 1. Bối cảnh & vấn đề

Hệ thống đang kéo chi phí quảng cáo ở **một grain duy nhất: tài khoản quảng cáo × ngày**,
cho cả hai nền tảng.

| | Nguồn | Bảng | Grain |
|---|---|---|---|
| Facebook | `/me/adaccounts` + `/{acct}/insights?level=account` | `facebook_ad_account_daily_spends` | TKQC × ngày |
| Google | GAQL `FROM customer_client` + `FROM customer` | `google_ads_account_daily_spends` | Customer × ngày |

Hai lỗ hổng cụ thể:

**1.1 Google bỏ sót tài khoản.** `services.py:96` lọc `WHERE customer_client.level <= 1` —
chỉ lấy **con trực tiếp** của MCC. Cây tài khoản Google Ads thường là MCC → MCC con →
TKQC; mọi tài khoản nằm dưới một MCC con đều vô hình với hệ thống. Không có cảnh báo nào:
báo cáo vẫn ra số, chỉ là thiếu tiền của những tài khoản không ai biết là tồn tại.

**1.2 Không nền tảng nào lưu chi phí theo chiến dịch.** Facebook có endpoint xem trực tiếp
`fetch_ad_account_insights(level="campaign")` nhưng dữ liệu **không đi vào DB** — mỗi lần
xem là một lời gọi Graph API, không có lịch sử, không lọc/gộp được. Google thì query
`FROM customer` nên không có khái niệm campaign ở tầng nào.

Hệ quả nghiệp vụ: khi một TKQC chạy nhiều chiến dịch cho nhiều sản phẩm, con số duy nhất
đọc được là tổng chi của cả tài khoản. Không trả lời được câu hỏi cơ bản nhất của người
chạy ads — *chiến dịch nào đang đốt tiền*.

## 2. Quyết định đã chốt

| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Facebook lấy chi phí campaign từ đâu | **Roll-up từ grain ad** của nhánh `feat/fb-ads-page-grain` | `FacebookAd` đã mang sẵn `campaign_id/campaign_name`. `GROUP BY` là xong — không thêm bảng, không thêm một lời gọi Graph API nào |
| Google lưu grain nào | **campaign × ngày** (không xuống ad) | Google Ads API trả campaign + metrics trong *cùng một* truy vấn `FROM campaign`. Xuống grain ad là thêm 2 tầng bảng cho một nhu cầu chưa ai đặt ra |
| Nơi đặt endpoint đọc | **Mỗi app một endpoint**, FE gộp | `apps.orders` nằm *dưới* hai app ads trong contract phân tầng nên không được import lên; gộp trong `facebook_ads` thì phải nhét Google vào app Facebook. Xem §7 |
| Hệ số hiển thị chi phí | **Dùng lại 1.1** (`ADS_DAILY_COST_MULTIPLIER`) | Tab Chiến dịch nằm cùng màn hình với tab Kênh/Page. Lệch hệ số = hai con số chênh 10% không lời giải thích |
| Doanh thu theo campaign | **Không làm đợt này** | Attribution `ad_id` là giai đoạn B của spec 27/07. Hiện cột doanh thu rỗng cạnh chi phí thật là mời người đọc kết luận sai |
| Tài khoản Google đã đóng | Đánh dấu `removed`, **giữ nguyên lịch sử chi phí** | Ngừng tốn quota gọi API cho tài khoản chết, nhưng báo cáo quá khứ không được thủng lỗ |

Ba điểm **không** làm (YAGNI):

- Không thêm grain adset cho cả hai nền tảng.
- Không đẩy grain campaign sang báo cáo Lark (`lark_ads_report_sync`) — đó là luồng
  production đang chạy, đụng vào phải đo riêng.
- Không hợp nhất hai bảng chi phí FB/Google thành một bảng chung. Hai nền tảng có tập
  metric khác nhau (FB có `reach`, Google không); gộp sớm là ép một schema chung lên hai
  thứ chưa chắc hội tụ.

## 3. Kiến trúc

```
Facebook                                Google
────────                                ──────
Graph /me/adaccounts    ─► FacebookAdAccount    GAQL FROM customer_client
  (đã lấy TOÀN BỘ)                      ─►      (BỎ lọc level<=1 → TOÀN BỘ cây)
                                                    │
Graph /{acct}/ads       ─► FacebookAd                ▼
  creative→page_id         ├ campaign_id     GoogleAdsAccount
                           └ adset_id                │
                                │                    │ GAQL FROM campaign
Graph /{acct}/insights          │                    │  (1 query = catalog + metrics)
  level=ad              ─► FacebookAdDailySpend      ▼
                                │           GoogleAdsCampaign
                                │                    │
                                │           GoogleAdsCampaignDailySpend
                                │                    │
                         GROUP BY campaign_id        │ GROUP BY campaign
                                │                    │
                                ▼                    ▼
                    GET /api/facebook-ads/    GET /api/google-ads/
                        campaign-cost/            campaign-cost/
                                └────────┬───────────┘
                                         ▼
                             AdsReportPage › tab "Chiến dịch"
```

Bất đối xứng có chủ ý: Facebook lưu grain **ad**, Google lưu grain **campaign**. Facebook
buộc phải xuống grain ad vì page chỉ suy ra được từ creative của ad (spec 27/07); Google
không có nhu cầu page nên dừng ở campaign. Cả hai cùng *đọc ra* được ở mức campaign, đó là
điều duy nhất tầng báo cáo cần biết.

## 4. Schema

### 4.1 Facebook — không có bảng mới

Toàn bộ nằm ở nhánh `feat/fb-ads-page-grain` (migration `0005_page_grain`). Đợt này chỉ
thêm một module đọc `campaign_report.py`.

### 4.2 Google — hai bảng mới (`google_ads/migrations/0002_campaign_grain.py`)

**`google_ads_campaigns`** — danh mục chiến dịch

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `ad_account` | FK `GoogleAdsAccount` | |
| `campaign_id` | `CharField(32)` | ID campaign bên Google |
| `name` | `CharField(255)` | |
| `status` | `CharField(32)` | `ENABLED` / `PAUSED` / `REMOVED` — lưu thô như Google trả |
| `last_seen_at` | `DateTimeField` | Lần cuối còn xuất hiện trong truy vấn |

- Unique: `(ad_account, campaign_id)` — campaign id chỉ unique trong phạm vi một customer.
- Index: `(ad_account, status)`.

**`google_ads_campaign_daily_spends`** — chi phí

| Cột | Kiểu |
|---|---|
| `campaign` | FK `GoogleAdsCampaign` |
| `date` | `DateField` |
| `spend` | `DecimalField(18, 2)` |
| `impressions` / `clicks` | `BigIntegerField` |
| `last_synced_at` | `DateTimeField(auto_now=True)` |

- Unique: `(campaign, date)`.
- Index: `(date)` — báo cáo luôn lọc theo khoảng ngày trước tiên.

Không thêm `reach`: Google Ads API không trả reach ở grain campaign cho mọi loại chiến
dịch. Một cột luôn bằng 0 trông y hệt một cột "reach thật bằng 0".

### 4.3 Bảng cũ giữ nguyên

`google_ads_account_daily_spends` **không bị bỏ**. Tổng theo campaign có thể nhỏ hơn tổng
theo account (chi phí ở mức tài khoản như phí dịch vụ không thuộc campaign nào), nên hai
bảng không thay thế được cho nhau — đúng lý do §6 của spec 27/07 giữ bảng account-grain
của Facebook.

## 5. Đồng bộ

### 5.1 Google — quét toàn bộ cây MCC

```sql
SELECT customer_client.id, customer_client.descriptive_name,
       customer_client.currency_code, customer_client.status,
       customer_client.manager, customer_client.level
FROM customer_client
```

Bỏ `WHERE customer_client.level <= 1`. Truy vấn `customer_client` chạy từ MCC gốc trả về
**toàn bộ hậu duệ ở mọi độ sâu**; client đã set `login_customer_id` = MCC gốc nên gọi API
cho tài khoản lồng sâu vẫn hợp lệ, không cần đổi credential.

Quy tắc lọc và ánh xạ trạng thái:

- `manager = true` → bỏ qua (MCC con không tự chạy ads).
- Cùng một `customer_id` xuất hiện dưới nhiều MCC con → `update_or_create` theo
  `(config, customer_id)` tự khử trùng.
- Trạng thái: chỉ hạ xuống `removed` khi đọc ra **đúng** một giá trị Google định nghĩa là
  đã đóng (`CANCELED`, `SUSPENDED`, `CLOSED`); mọi giá trị khác — kể cả giá trị không nhận
  ra — giữ `active`. Trước đây mọi account trả về đều thành `active`.

  Hướng fail-safe này có chủ ý và **ngược** với trực giác "không chắc thì loại". Nếu đọc
  status hỏng (thư viện đổi kiểu trả về, `use_proto_plus` bị tắt, Google thêm status mới)
  mà mặc định là `removed` thì toàn bộ tài khoản biến mất khỏi lượt đồng bộ chi phí và
  báo cáo âm thầm về 0 — đúng loại hỏng cả đợt này sinh ra để chặn. Đoán nhầm sang
  `active` chỉ tốn ít quota gọi API cho một tài khoản đã đóng, và lỗi đó đã được cách ly
  theo từng tài khoản ở §5.2.

### 5.2 Google — cách ly lỗi từng tài khoản

`sync_daily_spend_for_config` hiện gọi tuần tự **không try/except**: một tài khoản lỗi làm
hỏng cả lần chạy, những tài khoản phía sau không được đồng bộ. Với 5 tài khoản thì hiếm
gặp; quét cả cây lên vài chục tài khoản thì gần như chắc chắn dính một tài khoản bị
`CUSTOMER_NOT_ENABLED` hoặc mất quyền.

Mỗi tài khoản được bọc riêng: lỗi → `logger.warning` kèm `customer_id`, cộng vào
`failed_accounts` của summary, đi tiếp. **Lỗi không được nuốt im lặng** — đây đúng bài học
của sự cố token Facebook 25–27/07: một tài khoản lỗi bị bỏ qua lặng lẽ sẽ hiển thị thành
"chi tiêu 0đ" trên báo cáo, không ai phân biệt được với "hôm nay không chạy quảng cáo".

Áp dụng cho cả `sync_accounts_for_config`? **Không.** Hàm đó chỉ có *một* lời gọi API; lỗi
ở đó là lỗi credential/quyền của cả config, phải nổ lên chứ không phải đếm.

### 5.3 Google — chi phí theo campaign

```sql
SELECT campaign.id, campaign.name, campaign.status,
       segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks
FROM campaign
WHERE segments.date BETWEEN '{since}' AND '{until}'
```

Một truy vấn trả cả danh mục lẫn số liệu — upsert catalog và spend trong cùng vòng lặp,
không tốn thêm quota. `since`/`until` đi qua `_safe_iso_date` sẵn có (chặn GAQL injection).

Nhịp đồng bộ **mirror nguyên nhịp của grain account** đang chạy ổn định:

- Tài khoản chưa có dòng campaign nào → backfill `BACKFILL_DAYS = 365`.
- Tài khoản đã có → refresh `RECENT_DAYS = 7` ngày gần nhất (bắt cả chi phí hôm nay lẫn
  số liệu Google chốt muộn).

Task Celery riêng `sync_all_campaign_spend`, **không gộp** vào task grain account: khối
lượng khác hẳn nhau nên `soft_time_limit` khác nhau, và gộp thì một bên hỏng kéo bên kia
chết theo. Khoá Redis riêng, TTL > `time_limit` (đúng ghi chú §13 spec 27/07).

Kèm management command `backfill_gads_campaign_spend --months N` để chạy tay theo tháng —
lần backfill đầu tiên cho cả cây MCC là việc nặng, phải chủ động chạy giờ thấp điểm chứ
không để cron tự nuốt.

### 5.4 Facebook — không thay đổi luồng đồng bộ

Grain ad của nhánh `feat/fb-ads-page-grain` đã đủ. Đợt này không đụng vào
`ad_spend_sync.py` / `ad_catalog_sync.py`.

## 6. Đọc & tổng hợp

### 6.1 `facebook_ads/campaign_report.py`

```python
FacebookAdDailySpend.objects
    .filter(date__gte=since, date__lte=until)
    .values("ad__campaign_id", "ad__campaign_name", "ad__ad_account__name")
    .annotate(cost=Sum("spend"), impressions=Sum("impressions"), clicks=Sum("clicks"))
```

Đặt cạnh `page_report.py` và dùng lại đúng các quy ước của nó:

- Nhân `ADS_DAILY_COST_MULTIPLIER` **trên tổng đã SUM**, không nhân từng dòng (tránh tích
  luỹ sai số làm tròn).
- **Không** trả `reach`, dù grain ad của Facebook có lưu: reach không cộng được giữa các
  ngày, và Google không có cột tương đương — một cột chỉ có số ở nửa số dòng thì đọc như
  lỗi dữ liệu.
- Ad chưa có `campaign_id` → nhãn `"Không rõ chiến dịch"`, không gộp im lặng vào tổng.
- `cost` là `Decimal` format `f"{cost:.2f}"`, không dùng `or` với giá trị `Decimal("0.00")`.

### 6.2 `google_ads/campaign_report.py`

Cùng chữ ký, đọc từ `GoogleAdsCampaignDailySpend`. Trả thêm `revenue = None` để hai payload
đồng dạng — FE không phải viết hai nhánh xử lý.

### 6.3 Hình dạng payload chung

```json
{
  "since": "2026-08-01", "until": "2026-08-07",
  "rows": [
    {"platform": "facebook", "account_name": "TKQC A", "campaign_id": "23851...",
     "campaign_name": "CD Tháng 8", "cost": "1250000.00",
     "impressions": 91234, "clicks": 812, "revenue": null}
  ],
  "truncated": false
}
```

`platform` do **backend** điền, không phải FE gán thêm sau khi nhận. FE gán là mở đường cho
hai chỗ gọi khác nhau gán hai giá trị khác nhau.

`truncated`: mỗi endpoint chặn ở 1000 dòng (sắp theo chi phí giảm dần) theo đúng pattern
soft-limit clamp của codebase. Cắt mà không nói ra thì bảng "thiếu chiến dịch" trông y hệt
bảng "chiến dịch không tiêu tiền".

## 7. Vì sao hai endpoint chứ không một

Contract phân tầng (`backend/.importlinter`) xếp `apps.orders` **dưới** `apps.facebook_ads`
và `apps.google_ads`. Ba lựa chọn đã cân nhắc:

| | Cách | Vì sao loại |
|---|---|---|
| A | Endpoint hợp nhất trong `apps.orders` | Orders phải import ngược lên hai app ads — vi phạm contract, thêm nợ vào KHỐI B đang chỉ được phép giảm |
| B | Endpoint hợp nhất trong `apps.facebook_ads`, import `google_ads` | Lint cho phép (cùng tầng) nhưng đặt Google trong app Facebook là sai ngữ nghĩa; người đọc sau sẽ tìm code Google ở đúng chỗ và không thấy |
| **C** | **Hai endpoint, FE gộp** | **Chọn.** Zero coupling, đúng pattern `page_report` đã đi. Gộp/sắp xếp ~vài trăm dòng ở FE là rẻ |

Ngưỡng phải xem lại lựa chọn này: khi số campaign vượt mức FE gộp nổi trong một lần tải
(cỡ >5000 dòng), hoặc khi cần phân trang server-side thật. Lúc đó lời giải đúng là app
`apps.ads_reporting` ở tầng trên, không phải nhét chéo giữa hai app.

## 8. Giao diện — tab "Chiến dịch"

Thêm tab thứ tư vào `AdsReportPage`, dùng lại `DateFilter` chung của trang.

| Cột | Ghi chú |
|---|---|
| Nền tảng | Badge Facebook / Google |
| Tài khoản QC | |
| Chiến dịch | Rỗng → `"Không rõ chiến dịch"`, không bao giờ để trống |
| Chi phí | Đã nhân 1.1, format tiền VN |
| Hiển thị / Click | |
| CPM / CPC | Tính ở FE từ 3 cột trên; mẫu số 0 → `—` chứ không phải `0` hay `NaN` |

Bộ lọc riêng của tab: nền tảng (Tất cả / Facebook / Google) và TKQC (multi-select). Sắp xếp
mặc định theo chi phí giảm dần. Không có cột doanh thu và tỷ lệ CP/DT — bảng chỉ có chi phí
thì người đọc hiểu đúng đó là bảng chi phí.

Trạng thái: đang tải → `LogoSpinner`; lỗi → thẻ đỏ kèm thông báo; rỗng → dòng "Chưa có dữ
liệu chiến dịch trong khoảng đã chọn". Một trong hai nền tảng lỗi → vẫn hiện dữ liệu của
nền tảng kia kèm cảnh báo, **không** làm trắng cả bảng.

## 9. Kiểm thử

Theo cấu trúc test suite đã tái cấu trúc 05/08 — gộp theo chủ đề, không tạo file rời cho
từng hàm.

| File | Phủ |
|---|---|
| `google_ads/tests/test_account_sync.py` (mở rộng) | Cây MCC nhiều tầng; MCC con bị loại; trùng customer dưới 2 MCC; ánh xạ `CANCELED → removed` |
| `google_ads/tests/test_campaign_sync.py` (mới) | Upsert catalog + spend từ một truy vấn; `cost_micros` → Decimal; chọn nhánh backfill 365 vs refresh 7; một account lỗi không chặn account sau; `failed_accounts` đếm đúng |
| `google_ads/tests/test_campaign_report_api.py` (mới) | Tổng hợp theo khoảng ngày; hệ số 1.1; lọc theo TKQC; cờ `truncated`; chặn phân quyền section |
| `facebook_ads/tests/test_campaign_report_api.py` (mới) | Roll-up từ grain ad khớp tổng grain account trong cùng khoảng; nhãn "Không rõ chiến dịch"; chi phí đúng 0 hiển thị `"0.00"` |
| `frontend/src/features/ads/__tests__/campaign-report.test.ts` | Gộp hai nền tảng, sắp xếp, CPM/CPC mẫu số 0, một nền tảng lỗi vẫn hiện nền tảng kia |

Mock ở tầng `_search` / `_client` như các test hiện có — không gọi API thật.

## 10. Rủi ro & cách chặn

| Rủi ro | Chặn bằng |
|---|---|
| Backfill 365 ngày × cả cây MCC làm nghẽn quota Google | Management command chạy tay theo tháng, giờ thấp điểm; cron chỉ chạy nhịp 7 ngày |
| Số campaign tăng vọt sau khi quét hết cây MCC | `truncated` + cap 1000 dòng/nền tảng ở tầng đọc |
| Tổng theo campaign ≠ tổng theo account, người đọc tưởng sai số | Ghi rõ trong spec §4.3; hai tab tách bạch, không đặt cạnh nhau như hai cách tính của cùng một số |
| Nhánh `feat/fb-ads-page-grain` đứng 10 ngày, migration `0111` đụng `0115` của master | MR1 tách riêng: chỉ rebase + đánh số lại migration, không kèm tính năng mới |

## 11. Thứ tự triển khai

| MR | Nội dung | Phụ thuộc |
|---|---|---|
| 1 | Rebase `feat/fb-ads-page-grain` lên master, đánh số lại migration | — |
| 2 | Google: quét toàn cây MCC + cách ly lỗi từng account (+ spec, plan) | — |
| 3 | Google: bảng campaign + sync + backfill command + cron | MR2 |
| 4 | Hai endpoint campaign-cost + tab "Chiến dịch" | MR1, MR3 |

MR1 và MR2 độc lập, chạy song song được. MR4 là MR duy nhất cần cả hai nhánh đã về master.
