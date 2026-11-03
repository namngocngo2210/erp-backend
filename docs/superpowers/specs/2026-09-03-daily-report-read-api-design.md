# Spec — API chỉ đọc cho trợ lý Daily Report (Sở Hiên / Hermes)

> Mở một cửa REST **chỉ đọc**, xác thực bằng token dịch vụ riêng, để trợ lý Daily
> Report chạy trên Mac mini kéo số liệu lead / đơn / doanh thu / pipeline theo ngày.
> Không cấp quyền truy cập database production cho máy ngoài.
>
> - Ngày tạo: 2026-09-03
> - Nhánh dự kiến: `feat/daily-report-api`
> - Nền tảng kế thừa: API transcript Hermes (`docs/hermes-call-transcript-api.md`,
>   `apps/callcenter/transport/views/hermes_views_impl.py`) cho cửa xác thực;
>   kho mart `ai.*` (`docs/superpowers/specs/2026-08-28-ai-mart-hermes-design.md`,
>   `apps/ai_mart/`) cho dữ liệu; luật gom nhóm của Báo cáo tổng
>   (`apps/orders/reporting/overview.py`) và Báo cáo sale (`reporting/shared.py`)
>   cho định nghĩa số liệu.

---

## 0. Bài toán

Trợ lý Daily Report (agent Hermes, profile `lich-task`, chạy trên Mac mini) cần mỗi
ngày lấy: số lead về, số đơn chốt, doanh thu, tình trạng pipeline. Yêu cầu từ phía
vận hành:

1. Tài khoản dịch vụ riêng, **chỉ đọc**, thu hồi được mà không đụng phân quyền nhân sự.
2. Tài liệu Swagger/OpenAPI **không chứa bí mật** để sếp gửi thẳng cho trợ lý.
3. Số liệu trả về phải có **múi giờ** và **định nghĩa trạng thái** rõ ràng.
4. Có health check.
5. Credential nhập tại máy, không đi qua chat.

Hiện trạng liên quan:

- Codebase **không có** `/api/v1`; mọi app nằm dưới `/api/<app>/`. Tài liệu phía trợ
  lý ghi `/api/v1` chỉ là ví dụ — dùng `/api/daily-report/`.
- Cơ chế máy-tới-máy duy nhất đang chạy là **token tĩnh trong env** + `AllowAny` +
  `secrets.compare_digest`, fail-closed trên production (Hermes, landing ingest,
  warranty ingest, metrics). Không có bảng API key, không có "service account" trong DB.
- Hermes trước đây đọc thẳng DB prod qua MCP với role `hermes_ro` trên schema `ai`.
  Spec này **thay** đường đó bằng REST, nhưng **tái dùng** schema `ai` làm nguồn.

## 1. Quyết định kiến trúc

| # | Quyết định | Lý do |
|---|---|---|
| **Q1** | **Nguồn dữ liệu là mart `ai.don_hang`** (+ `ai.tu_dien_ma`, `ai.tinh_trang_du_lieu`), không query thẳng `orders`. | Mart đã có ngày nghiệp vụ `ngay_tao_ngay` quy về `Asia/Ho_Chi_Minh`, nhãn tiếng Việt, cờ `la_don_gtc`, SĐT đã che, và **độ tươi đo được**. Query gộp trên bảng phẳng có index theo ngày rẻ hơn nhiều so với các mixin báo cáo (Báo cáo tổng từng phải precompute vì timeout — `docs/report-precompute-timeout-2026-07-22.md`). Độ trễ ≤ 30 phút chấp nhận được với báo cáo ngày. |
| **Q2** | **Định nghĩa số liệu chép đúng luật Báo cáo tổng / Báo cáo sale**, tham chiếu hằng số từ `apps/orders/reporting/shared.py` thay vì tự bịa. | Sếp đối chiếu số của trợ lý với màn hình CRM. Hai nguồn lệch định nghĩa là lỗi tệ nhất: không ai phát hiện, chỉ mất niềm tin. |
| **Q3** | **Xác thực bằng token tĩnh riêng `DAILY_REPORT_API_TOKEN`**, không dùng chung `HERMES_API_TOKEN`. | Cùng pattern đã chạy, không thêm hệ thống mới. Token riêng để thu hồi từng kênh độc lập: đổi token Daily Report không làm gãy kênh transcript. "Service account chỉ đọc" trong codebase này **chính là** token này — mọi view đều `GET`, không có đường ghi. |
| **Q4** | **App Django mới `apps/daily_report`**, tầng trên cùng của `.importlinter`. | Không nhét vào `ai_mart` (app đó là ETL, không có transport) hay `callcenter` (đang chứa Hermes transcript vì dữ liệu ở đó). Tách app để biên giới rõ: đọc mart + hằng số định nghĩa, không import internals của `orders`. |
| **Q5** | **OpenAPI tách riêng thành file tĩnh** `docs/openapi/daily-report.yaml`, sinh từ urlconf của app; các path này bị **loại khỏi** schema FE↔BE chính. | Giữ quy ước "schema chính là hợp đồng FE↔BE" (D7). File tĩnh không chứa token, sếp gửi được. Test drift đảm bảo file luôn khớp code. |
| **Q6** | **Tiền trả về là số nguyên VND, không phải chuỗi.** | Người tiêu thụ là máy, cần cộng trừ. Các mixin báo cáo trả chuỗi vì phục vụ UI — không kế thừa quy ước đó ra ngoài. |
| **Q7** | **Mọi response kèm `data_as_of` + `data_status`** đọc từ `ai.tinh_trang_du_lieu`. | ETL trễ âm thầm là chế độ hỏng nguy hiểm nhất. Trợ lý phải nói được "số liệu tính đến 07:37" thay vì tưởng là số hiện tại. |
| **Q8** | Cache kết quả 30 phút trong Redis, key chứa `du_lieu_toi_moc` của mart. | Mart chỉ đổi 2 lần/giờ; key gắn mốc dữ liệu nên cache tự vô hiệu đúng lúc, không cần invalidation. Trợ lý gọi lặp / thử lại không đội tải DB. |

**Phương án đã cân nhắc và loại:**

- **B — tái dùng `OrderRepository.summary_overview` / `summary_sale_daily_by_staff` trên OLTP.** Ưu: số khớp UI tuyệt đối, thời gian thực. Nhược: query nặng trên DB đang thiếu RAM, các hàm ads nhận `user=` để RBAC (không có user máy), và app mới phải với vào internals của `orders`. Q2 lấy được cái "khớp UI" mà không phải trả giá này.
- **C — giữ MCP `prod-db` với role `hermes_ro`.** Bị loại theo đúng tiền đề của yêu cầu: không mở DB production cho máy ngoài.

## 2. Kiến trúc

```
Mac mini (Hermes, profile lich-task)
   │  GET /api/daily-report/...  X-Api-Token: <DAILY_REPORT_API_TOKEN>
   ▼
nginx (tuỳ chọn: allow <IP Tailscale Mac mini>; deny all cho location này)
   ▼
apps/daily_report  ── transport: token gate + throttle scope `daily_report`
                   ── application: StatsService (kỳ, gom nhóm, envelope, cache)
                   ── infrastructure: SQL đọc ai.don_hang / ai.tu_dien_ma / ai.tinh_trang_du_lieu
   ▼
Postgres schema `ai` (do apps/ai_mart nạp mỗi 30 phút)
```

```
apps/daily_report/
  domain/
    definitions.py     # bucket trạng thái sale/đơn, nhóm kênh; import hằng số từ orders.reporting.shared
    periods.py         # parse from/to/date, giới hạn khoảng, ngày mặc định (hôm qua giờ VN)
  application/
    stats_service.py   # leads_stats / orders_stats / revenue / pipeline / daily_summary / health
  infrastructure/
    queries.py         # SQL tham số hoá trên ai.*; bọc statement_timeout
    freshness.py       # đọc ai.tinh_trang_du_lieu cho ai.don_hang
  transport/
    auth.py            # extract_api_token + require_api_token + daily_report_view (bản sao có chủ đích như Hermes)
    serializers.py     # chỉ để mô tả schema (drf-spectacular), không validate input
    views.py
  openapi.py           # SPECTACULAR_SETTINGS riêng cho file tĩnh
  urls.py
  tests/
```

`DailyReportApiThrottle` (scope `daily_report`) đặt trong `apps/common/throttling.py`
cạnh `HermesApiThrottle` — lớp nền `_FixedWindowThrottle` là private của module đó.

`.importlinter`: thêm tầng `daily_report` **trên** `ai_mart`. App được import xuống
`common` (health, throttling, db_guards), `analytics.sql.tz` (`BUSINESS_TZ`),
`orders.reporting.shared` (hằng số bucket), `ai_mart.domain.dictionary` (nếu cần nhãn).

Đăng ký `re_path(r"^api/daily-report/?", include("apps.daily_report.urls"))` trong
`config/urls.py` **trước** catch-all của `audit`.

## 3. Xác thực, giới hạn, mạng

### 3.1 Token

- Env `DAILY_REPORT_API_TOKEN` (khai trong `config/settings_parts/base.py` cạnh
  `HERMES_API_TOKEN`, thêm vào `.env.example` và 3 file compose).
- Header `X-Api-Token: <token>` hoặc `Authorization: Bearer <token>`.
- So khớp `secrets.compare_digest` trên bytes đã `.encode()` (tránh `TypeError` với
  ký tự non-ASCII → 500, bài học ở `warranty_codes/transport/portal_views.py`).
- **Fail-closed:** production mà token rỗng → `503` cho mọi endpoint. Dev không đặt
  token → mở để thử.
- JWT của nhân viên CRM **không** mở được các endpoint này (test khẳng định).
- Sinh token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Nhập vào
  env trên server và vào profile `lich-task` trên Mac; không gửi qua chat.
- Thu hồi: đổi env + restart. Không có rotation hai-token (YAGNI — chỉ một consumer).

### 3.2 Throttle

Scope mới `daily_report` trong `DEFAULT_THROTTLE_RATES`, env `DAILY_REPORT_THROTTLE_RATE`,
mặc định `300/hour` theo IP client (`ClientIpMixin`). Một phiên báo cáo gọi ≤ 10
request; trần này là van chống vòng lặp hỏng bên agent, không phải hạn ngạch.

### 3.3 Giới hạn nguồn (tuỳ chọn, tầng nginx)

Thêm block `location ^~ /api/daily-report/` trong 3 file nginx (giữ đồng bộ như
quy ước ở `nginx/default.conf:30-32`) với `allow <IP Tailscale của Mac mini>; deny all;`
**để sẵn dạng comment**. Bật khi Mac mini có IP cố định qua Tailscale/VPN tới VPS.
Không làm ở tầng Django: codebase không có allowlist IP ở app, và IP thật đã phải đi
qua `THROTTLE_TRUSTED_PROXIES` — thêm một lớp nữa là thêm một chỗ sai.

### 3.4 Audit

Không miễn trừ khỏi `AuditLogMiddleware` (ghi `user_id=None`, path, IP). Đây là dấu vết
duy nhất cho biết token bị dùng từ IP lạ.

## 4. Hợp đồng API

Tiền tố: `/api/daily-report/`. Tất cả là `GET`, JSON, `Content-Type: application/json; charset=utf-8`.

### 4.0 Quy ước chung

**Tham số thời gian**

| Param | Dạng | Mặc định | Ghi chú |
|---|---|---|---|
| `from`, `to` | `YYYY-MM-DD` | hôm qua (giờ VN) cho cả hai | Ngày nghiệp vụ theo `Asia/Ho_Chi_Minh`, **bao gồm cả hai đầu**. Chỉ nhận ngày, không nhận datetime — mart đã cắt theo ngày, nhận datetime chỉ tạo ảo giác chính xác. |
| `date` | `YYYY-MM-DD` | hôm qua | Chỉ dùng ở `/summary`. |
| `group_by` | enum theo endpoint | `none` | Sai giá trị → `400`. |

Khoảng tối đa **92 ngày**; `from > to` hoặc quá dài → `400` với `detail` nói rõ.
Ngày tương lai không lỗi, chỉ trả 0.

**Envelope**

```json
{
  "meta": {
    "timezone": "Asia/Ho_Chi_Minh",
    "range": {"from": "2026-09-02", "to": "2026-09-02"},
    "data_as_of": "2026-09-03T07:37:12+07:00",
    "data_status": "Bình thường",
    "definitions_url": "/api/daily-report/definitions",
    "generated_at": "2026-09-03T08:00:03+07:00"
  },
  "data": { ... }
}
```

- `data_as_of` = `du_lieu_toi_moc` của `ai.don_hang` trong `ai.tinh_trang_du_lieu`
  (mốc watermark ETL). `data_status` = `trang_thai_text` cùng view
  (`Bình thường` / `Trễ — số liệu có thể cũ` / `Lỗi ở lần chạy gần nhất`).
- Mọi timestamp ISO-8601 kèm offset `+07:00`. Mọi ngày là chuỗi `YYYY-MM-DD` theo giờ VN.
- Tiền: số nguyên VND. Tỷ lệ: số thực trong `[0, 1]`, 4 chữ số thập phân, `null` khi mẫu số bằng 0.
- Mã trạng thái trả **cả mã lẫn nhãn** (`code` + `label`), nhãn lấy từ `ai.tu_dien_ma`.

**Lỗi**: `400` tham số sai, `401` token sai/thiếu, `503` chưa cấu hình token trên prod
hoặc DB/mart chết. Body `{"detail": "<tiếng Việt, nói rõ trường nào>"}`.

### 4.1 `GET /health`

Yêu cầu token (không mở public: endpoint tiết lộ độ tươi mart, và không có lý do gì
cho máy lạ probe nó).

```json
{
  "status": "ok",
  "checks": {
    "db": true,
    "redis": true,
    "mart": {"ok": true, "data_as_of": "2026-09-03T07:37:12+07:00", "lag_minutes": 23, "status": "Bình thường"}
  }
}
```

`200` khi cả ba ok; `503` khi DB/Redis chết hoặc `mart.status` ≠ `Bình thường`
(ETL lỗi hoặc trễ > 90 phút — ngưỡng đã định trong view). Tái dùng `_check_db` /
`_check_redis` của `apps/common/health.py` (nâng thành hàm public, không chép).

### 4.2 `GET /definitions`

Từ điển để trợ lý hiểu số liệu mà không cần đoán. Không có tham số. Cache 1 giờ.

```json
{
  "data": {
    "order_status":   [{"code": 3, "label": "Giao thành công", "bucket": "delivered"}, ...],
    "invoice_status": [{"code": 1, "label": "Hoàn thành"}, {"code": 2, "label": "Đã hủy"}, {"code": 3, "label": "Đang xử lý"}],
    "sales_status":   [{"code": "closed", "label": "Đã chốt", "bucket": "closed"},
                       {"code": "no_answer_3", "label": "Không nghe máy lần 3", "bucket": "unreached"}, ...],
    "order_source":   [{"code": "landing", "label": "Landing"}, ...],
    "channel_group":  ["shopee", "tiktok", "facebook", "web", "off_other"],
    "metrics": {
      "lead": "Đơn nội bộ (không tính đơn mirror foxia/cnv) tạo trong kỳ, tính theo ngày tạo giờ VN.",
      "closed": "Lead có trạng thái sale thuộc bucket closed.",
      "closed_revenue": "Tổng tiền sau giảm của lead có trạng thái sale thuộc closed, closed_express, created_order (không tính closed_scheduled_date — đúng luật Báo cáo sale).",
      "total_orders": "Đơn đã lên hoá đơn KiotViet (invoice_status khác null) và không huỷ (status ≠ 6 và invoice_status ≠ 2), tạo trong kỳ.",
      "total_revenue": "Tổng tiền sau giảm của total_orders. delivered + awaiting + returned + return_risk = total (bất biến).",
      "net_revenue": "total_revenue trừ đơn đang/đã chuyển hoàn (status 4, 5, 12, 13).",
      "close_rate": "closed / (lead − underlying_condition − wants_store_visit). Cùng CÔNG THỨC mẫu số với Báo cáo sale nhưng tính trên toàn bộ đơn nội bộ (xem `lead`), nên có thể THẤP hơn tỷ lệ chốt trên màn hình Báo cáo sale. null khi mẫu số bằng 0."
    }
  }
}
```

Nguồn nhãn: `ai.tu_dien_ma`. Nguồn bucket: `apps/orders/reporting/shared.py`
(`CLOSED_SALES_STATUS_CODES`, `CLOSED_REVENUE_SALES_STATUS_CODES`, `NURTURE_*`,
`UNREACHABLE_*`, `DIRTY_*`, `RETURN_ORDER_STATUSES`, `RETURN_RISK_ORDER_STATUSES`,
`SALE_CHANNEL_*`). `domain/definitions.py` chỉ **ghép** hai nguồn này, không chép mã.

### 4.3 `GET /leads/stats?from&to&group_by=none|day|source|staff`

Lead = dòng `ai.don_hang` có `ngay_tao_ngay` trong kỳ (mart đã loại đơn mirror).
Phạm vi là TOÀN BỘ đơn nội bộ trong kỳ — mọi kênh, mọi nhân viên, kể cả lead chưa
gán — rộng hơn phạm vi Báo cáo sale (chỉ kênh có nhân viên Ads phụ trách và nhân
viên phòng Sale). `close_rate` dùng cùng công thức mẫu số với Báo cáo sale nhưng
tính trên tập rộng hơn này (§4.2), nên có thể thấp hơn tỷ lệ chốt hiển thị trên màn
hình Báo cáo sale.

Mỗi nhóm:

| Trường | Định nghĩa |
|---|---|
| `leads` | tổng dòng |
| `unassigned` | `nhan_vien_id IS NULL` |
| `unassessed` | `trang_thai_sale_ma IS NULL` |
| `closed`, `closed_revenue` | bucket closed / tiền theo `CLOSED_REVENUE_SALES_STATUS_CODES` |
| `calling` | không nghe máy lần 1..5 — còn nhịp gọi tiếp |
| `unreached` | không nghe máy lần 6, không liên hệ được |
| `nurture` | tư vấn thêm / hẹn lịch / gọi lại sau (KHÔNG gồm `wants_store_visit`, để các bucket rời nhau) |
| `wants_store_visit`, `dirty`, `not_buying`, `underlying_condition`, `warranty`, `cancelled`, `archived` | mỗi mã/nhóm mã một bucket |
| `other` | có trạng thái nhưng mã không thuộc từ điển hiện tại |
| `close_rate` | như §4.2 |

Bất biến: `unassessed + Σ(11 bucket) + other = leads`. Mọi mã trong
`crm_catalog.SALES_STATUS_OPTIONS` phải thuộc đúng một bucket (test khoá).

`group_by=day` → key `date`; `source` → `source: {code, label}`; `staff` →
`staff: {id, name}` (tên từ `nhan_vien_ten`; `null` gom thành một dòng `staff: null`).
Kết quả sắp theo key nhóm, không phân trang (tối đa 92 ngày × vài chục nhân viên).

### 4.4 `GET /orders/stats?from&to&group_by=none|day|channel_group|staff`

Đơn = `ai.don_hang` với `trang_thai_hoa_don_ma IS NOT NULL` và không huỷ
(`trang_thai_don_ma ≠ 6 AND trang_thai_hoa_don_ma IS DISTINCT FROM 2`), `ngay_tao_ngay`
trong kỳ, **loại chi nhánh** trong `EXCLUDED_BRANCH_IDS` ∪ `OVERVIEW_EXCLUDED_BRANCH_IDS`
(cùng phạm vi Báo cáo tổng).

Mỗi nhóm: `total_orders`, `delivered`, `awaiting`, `returned`, `return_risk` (số đơn),
đúng 4 bucket rời nhau của `overview.py:174-192`. `channel_group` tính từ
`nguon_don_ma` + tiền tố mã hoá đơn theo luật `shared.py:218-243`.

### 4.5 `GET /revenue?from&to&group_by=none|day|channel_group`

Cùng tập đơn và bucket như §4.4, nhưng trả **tiền** (`tong_sau_giam`):
`total_revenue`, `delivered_revenue`, `awaiting_revenue`, `returned_revenue`,
`return_risk_revenue`, `net_revenue`, cộng thêm `closed_revenue` (góc nhìn sale, từ tập
lead §4.3 cùng kỳ) để trợ lý so được "sale chốt bao nhiêu" với "kế toán ghi nhận bao nhiêu".

Hai endpoint 4.4 và 4.5 dùng **một** hàm query, chỉ khác cột chiếu ra — không hai
bản SQL.

### 4.6 `GET /pipeline?as_of=YYYY-MM-DD&lookback_days=30`

Ảnh chụp lead **đang mở** tính đến cuối ngày `as_of` (mặc định hôm nay, giờ VN):

- tạo trong `[as_of − lookback_days, as_of]` (`lookback_days` 1..92, mặc định 30);
- còn là lead (`trang_thai_hoa_don_ma IS NULL`) và `trang_thai_don_ma ≠ 6`;
- `trang_thai_sale_ma` **không** thuộc closed ∪ dirty ∪ {`not_buying`, `cancelled`,
  `archived`, `warranty`, `underlying_condition`} — tức còn có thể chốt (khách bệnh
  nền là chống chỉ định, không còn là cơ hội).

Trả:

```json
{
  "open_leads": 412,
  "unassigned": 17,
  "unassessed": 63,
  "by_status": [{"code": "call_back_later", "label": "Gọi lại sau", "count": 120}, ...],
  "by_age":    [{"bucket": "0-1d", "count": 90}, {"bucket": "2-3d", ...}, {"bucket": "4-7d", ...}, {"bucket": "8d+", ...}],
  "by_staff":  [{"staff": {"id": 12, "name": "..."}, "open_leads": 40, "unassessed": 5}, ...]
}
```

Tuổi = `as_of − ngay_tao_ngay` (ngày). Mart không có `next_followup_at` nên spec cố ý
**không** có "quá hạn follow-up"; thêm sau nếu cần và phải thêm cột vào mart trước.

### 4.7 `GET /summary?date=YYYY-MM-DD`

Gói cho báo cáo ngày, gọi **một** request là đủ. Ghép từ các hàm trên với kỳ
`[date, date]`, cộng thêm kỳ so sánh:

```json
{
  "date": "2026-09-02",
  "leads":    { ...như §4.3 group_by=none... },
  "orders":   { ...như §4.4 group_by=none... },
  "revenue":  { ...như §4.5 group_by=none... },
  "pipeline": { ...như §4.6 as_of=date, lookback 30... },
  "compare": {
    "previous_day":   {"date": "2026-09-01", "leads": 385, "closed": 91, "total_revenue": 152300000},
    "same_weekday_last_week": {"date": "2026-08-26", "leads": 402, "closed": 97, "total_revenue": 160100000},
    "month_to_date":  {"from": "2026-09-01", "to": "2026-09-02", "leads": 797, "closed": 188, "total_revenue": 310400000}
  },
  "top_staff_by_closed_revenue": [{"staff": {"id": 12, "name": "..."}, "closed": 14, "closed_revenue": 21500000}, ...]
}
```

`top_staff_by_closed_revenue` tối đa 5 dòng. Không trả top sản phẩm — cần
`ai.dong_don_hang` và chưa có yêu cầu; thêm sau là thêm một trường, không sửa gì cũ.

## 5. Thay đổi cần thiết ở mart `ai.don_hang`

Hai điểm mart hiện thiếu để §4.4 khớp Báo cáo tổng:

1. **Thêm cột `chi_nhanh_id text`** (từ `o.branch_id`) — hiện `la_don_gtc` đã gói luật
   loại chi nhánh nhưng các bucket awaiting/returned/return_risk cần lọc chi nhánh mà
   không dính điều kiện GTC. Migration `ai_mart` + sửa transform + `COMMENT` + seed
   test contract.
2. **Loại đơn gốc đã được khôi phục thành bản sao** — Báo cáo tổng loại bằng
   `Exists(restored_from=OuterRef("pk"))` (`overview.py:133`). Transform `don_hang`
   phải áp cùng luật (bỏ dòng có bản sao khôi phục chưa xoá) và candidate của sync
   tăng dần phải nhặt cả `restored_from_id` để dòng gốc được dọn. Test equivalence
   của `ai_mart` hiện có phải xanh sau thay đổi.
3. **Thêm cột `nhom_kenh_ban text`** (shopee / tiktok / facebook / web / off_other)
   vật chất hoá luật `SALE_CHANNEL_CASE_BRANCHES` ngay trong transform — mart không
   có mã kênh lẫn mã hoá đơn, và luật "sàn xét trước kênh" nên sống ở một chỗ.

Cả ba đều nhỏ, nằm trong app `ai_mart`, làm ở task đầu trước khi viết query.

## 6. Hiệu năng & an toàn truy vấn

- Mọi SQL bọc `apps.common.db_guards.statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS)`;
  timeout → `503` với `detail` "Truy vấn quá lâu", **không** nuốt.
- Query chỉ đi qua index `don_hang_ngay_tao_ngay_idx`; khoảng tối đa 92 ngày ⇒ vài
  chục nghìn dòng, gộp trong Postgres, không kéo về Python.
- Cache Redis: key `daily_report:<endpoint>:<params-hash>:<du_lieu_toi_moc>`, TTL 30 phút.
  `/health` không cache. `/definitions` TTL 1 giờ, key không cần gắn mốc mart — từ điển
  chỉ đổi khi deploy, TTL 1 giờ là đủ.
- Không truy cập `ai.cuoc_goi`, `ai.khach_hang` — API này không trả PII nào ngoài tên
  nhân viên (đã là dữ liệu nội bộ hiển thị khắp CRM).

## 7. OpenAPI

- View dùng `@extend_schema(tags=["daily-report"], parameters=[...], responses={...})` với
  serializer mô tả (`transport/serializers.py`) để schema có shape thật, không phải `{}`.
- Security scheme riêng `ApiKeyAuth` (`type: apiKey, in: header, name: X-Api-Token`).
- Sinh file bằng lệnh có sẵn của drf-spectacular, không viết command mới:
  `manage.py spectacular --urlconf apps.daily_report.urls --custom-settings
  apps.daily_report.openapi.SPECTACULAR_SETTINGS --file docs/openapi/daily-report.yaml`.
  `servers` dùng biến OpenAPI `{base}/api/daily-report` (mặc định placeholder domain) —
  file tĩnh, không phụ thuộc env, trợ lý điền domain thật khi cấu hình.
- Schema chính (`/api/schema`) thêm `PREPROCESSING_HOOKS` loại path bắt đầu bằng
  `/api/daily-report/` — giữ D7: schema chính là hợp đồng FE↔BE.
- Test drift: sinh schema trong test và so với file đã commit; lệch → fail kèm hướng dẫn chạy lệnh.
- Tài liệu người đọc: `docs/daily-report-api.md` (anh em với `docs/hermes-call-transcript-api.md`):
  hợp đồng, định nghĩa, cạm bẫy, cấu hình, cách thu hồi token.

## 8. Kiểm thử

Harness: container test (`pg:5433`, `redis:6380`) theo ghi chú workspace; không dùng `.env` prod.

| Nhóm | Nội dung |
|---|---|
| Cửa xác thực | thiếu token 401; sai token 401; Bearer đúng 200; JWT admin CRM → 401; prod không token → 503 cho mọi endpoint kể cả `/health`. |
| Tham số | `from > to` 400; > 92 ngày 400; `group_by` lạ 400; mặc định = hôm qua giờ VN (freeze time ở 00:30 VN để bắt lỗi múi giờ). |
| Ngữ nghĩa | Seed đơn qua ORM với đủ trạng thái, chạy `ai_mart` rebuild (helpers sẵn ở `apps/ai_mart/tests/helpers.py`), rồi **so kết quả `/orders/stats` và `/revenue` với `OrderRepository.summary_overview` cùng kỳ**. Đây là test quan trọng nhất — nó khoá Q2. `/leads/stats` cố ý tính trên toàn bộ đơn nội bộ (ruling khi review cuối) — không so tương đương với Báo cáo sale; test khoá bucket/bất biến thay thế. |
| Bất biến | `delivered + awaiting + returned + return_risk == total_orders` và tương tự cho tiền, trên dữ liệu ngẫu nhiên. |
| Độ tươi | mart trễ > 90 phút → `/health` 503, các endpoint khác vẫn 200 với `data_status` cảnh báo. |
| Cache | hai request liên tiếp chỉ chạy SQL một lần; đổi watermark mart → chạy lại. |
| Throttle | vượt `daily_report` rate → 429 (scope này không bị `conftest` tắt). |
| OpenAPI | file commit khớp schema sinh; schema chính không chứa `/api/daily-report/`. |
| Layering | `lint-imports` xanh với tầng mới. |

## 9. Triển khai & bàn giao

1. Thêm `DAILY_REPORT_API_TOKEN`, `DAILY_REPORT_THROTTLE_RATE` vào `.env.example`,
   `docker-compose.{local,staging,prod}.yml`.
2. Deploy staging → chạy `curl` 7 endpoint với token staging, đối chiếu `/summary` một
   ngày với màn Báo cáo tổng + Báo cáo sale.
3. Production: sinh token, đặt env, restart web, chạy `manage.py ai_mart_rebuild`
   một lần sau `migrate`. Ghi token vào profile `lich-task` trên Mac mini.
4. Gửi cho trợ lý (không nhạy cảm): URL gốc `https://<domain>/api/daily-report`,
   file `docs/openapi/daily-report.yaml`, link `docs/daily-report-api.md`.
5. Bật allowlist nginx khi có IP Tailscale cố định.

## 10. Không làm (YAGNI)

- Không bảng API key / nhiều token / rotation hai pha — một consumer, đổi env là đủ.
- Không phân trang, không export, không endpoint theo sản phẩm hay theo khách.
- Không `POST`/`PUT` nào. Không endpoint đọc SĐT, địa chỉ, transcript.
- Không đổi lịch ETL `ai_mart` (30 phút đủ cho báo cáo ngày). Nếu sau này cần "số lúc
  9h sáng chính xác tới phút", đường đi là gọi `sync_hot_marts` ngay trước giờ báo cáo,
  không phải query OLTP từ API này.
- Giờ gửi báo cáo và nội dung tin nhắn là việc của trợ lý (Hermes), ngoài phạm vi.

## 11. Ghi chú tiến độ

Commit theo task (`git log --oneline 20a53f85..HEAD`, cũ → mới; `20a53f85` là tip
`master` lúc bắt đầu nhánh):

| Task | Commit |
|---|---|
| Chuẩn bị | `3176b7a9` docs(daily_report): design spec and implementation plan for read-only Daily Report API |
| 1 — mart `ai.don_hang` | `1286916f` feat(ai_mart): add chi_nhanh_id, nhom_kenh_ban to don_hang; drop originals with live restored clone |
| 2 — app skeleton, xác thực, throttle, `/health` | `f4d499be` feat(daily_report): app skeleton with static-token gate, throttle scope and /health |
| 3 — parsing kỳ + định nghĩa chỉ số | `e1848ee7` feat(daily_report): period parsing and metric definitions composed from report constants; `277ec66d` fix(daily_report): reject non-YYYY-MM-DD date params |
| 4 — `/leads/stats` | `31fb5f40` feat(daily_report): /leads/stats with envelope, mart-stamped cache and bucketed metrics |
| 5 — `/orders/stats`, `/revenue` | `64c15dc7` feat(daily_report): /orders/stats and /revenue mirroring overview report buckets; `d995dd17` fix(daily_report): keep /revenue money-only; cover lead-only groups |
| 6 — `/pipeline` | `5030d677` feat(daily_report): /pipeline snapshot of open leads by status, age and staff |
| 7 — `/summary` | `bfe5cd34` feat(daily_report): /summary composing leads, orders, revenue, pipeline and comparisons |
| 8 — `/definitions` | `8e1473a4` feat(daily_report): /definitions exposing status dictionaries, buckets and metric glossary; `10025be0` test(daily_report): clear cache before /definitions test |
| 9 — OpenAPI | `0a171e5b` feat(daily_report): standalone OpenAPI document with ApiKeyAuth; keep main schema FE-only |
| 10 — tài liệu, env, compose, nginx, kiểm tra cuối | `docs(daily_report): API contract, env wiring and nginx allowlist template` (commit này) |

Sinh lại OpenAPI khi đổi view/serializer:

```
python manage.py spectacular --urlconf apps.daily_report.urls \
  --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS \
  --file ../docs/openapi/daily-report.yaml
```

### Chệch khỏi kế hoạch (đã ruling trong lúc làm)

- **Ngày `from`/`to`/`date`/`as_of` bắt buộc đúng `YYYY-MM-DD`** (không nhận ISO có
  giờ, không nhận định dạng khác) — regex chặt hơn plan gốc, fix ở `277ec66d`.
- **`/revenue` chỉ trả các trường tiền** (`total_revenue, delivered_revenue,
  awaiting_revenue, returned_revenue, return_risk_revenue, net_revenue,
  closed_revenue`), không lẫn số lượng đơn — assertion tương ứng trong plan gốc dời
  sang test của `/orders/stats`, fix ở `d995dd17`.
- **OpenAPI đánh dấu xác thực bằng `extend_schema(auth=[{"ApiKeyAuth": []}])`** trên
  từng view thay vì dựa vào `SECURITY` toàn cục — để mỗi endpoint tự khai rõ trong
  file tĩnh gửi trợ lý, không phụ thuộc cấu hình ẩn.

Fix wave sau review cuối: `39d1f566` fix(daily_report): degrade to 503 on DB errors; document population of lead metrics
