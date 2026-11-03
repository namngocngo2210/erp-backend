# API Daily Report cho trợ lý báo cáo ngày

Kênh **máy-tới-máy chỉ đọc** cho trợ lý Daily Report (Hermes, profile `lich-task`,
Mac mini). Nguồn: mart `ai.don_hang` / `ai.dong_don_hang` (apps/ai_mart, cập nhật :07 và
:37 mỗi giờ) và OLTP chi tiêu quảng cáo. Thiết kế gốc (số gộp, 03/09/2026):
docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md. **Từ 16/09/2026 API trả
TỪNG DÒNG, không trả số gộp** — quyết định chủ dự án, spec
docs/superpowers/specs/2026-09-16-daily-report-list-api-design.md. Hermes tự đếm/cộng.

## Hợp đồng

Tiền tố `/api/daily-report/`, header `X-Api-Token: <DAILY_REPORT_API_TOKEN>` (hoặc
`Authorization: Bearer`). File OpenAPI: `docs/openapi/daily-report.yaml` — gửi được
cho trợ lý, không chứa bí mật.

| Endpoint | 1 dòng = | Tham số | Trần `limit` |
|---|---|---|---|
| `GET /health` | — | — | — |
| `GET /definitions` | — | — | — |
| `GET /leads/stats` | 1 lead = đơn nội bộ tạo trong kỳ, mọi trạng thái | `from`, `to`, `cursor`, `limit` | 1000 |
| `GET /orders/stats` | 1 đơn có hoá đơn, không huỷ, loại chi nhánh ẩn (tập total_orders cũ) | `from`, `to`, `cursor`, `limit` | 1000 |
| `GET /revenue` | 1 dòng hàng (sản phẩm trong đơn) của tập `/orders/stats` | `from`, `to`, `cursor`, `limit` | 2000 |
| `GET /pipeline` | 1 lead còn mở tính đến `as_of`, kèm `age_days` | `as_of`, `lookback_days` (1..92, mặc định 30), `cursor`, `limit` | 1000 |
| `GET /ads` | 1 (ngày × nền tảng × TKQC × chiến dịch) + 1 dòng dư mỗi TKQC/ngày; dòng chiến dịch FB kèm `leads`/`closed`/`orders`/`revenue` quy về chiến dịch | `from`, `to`, `cursor`, `limit` | 2000 |
| `GET /summary` | — | luôn **410 Gone** | — |

Ngày `YYYY-MM-DD` theo `Asia/Ho_Chi_Minh`, bao gồm hai đầu, mặc định hôm qua, tối đa
92 ngày. Tiền: số nguyên VND. `group_by` **không còn** — gửi lên là 400.

### Phân trang (keyset)

```
{"meta": {timezone, range, data_as_of, data_status, definitions_url, generated_at[, spend_as_of, attribution]},
 "data": [ {...dòng..., "cursor": ...} ],
 "limit": 200,
 "next_cursor": <giá trị | null>}
```

`limit` mặc định 200; vượt trần bị kẹp về trần (không lỗi). Kéo trang sau bằng
`cursor=<next_cursor>`; `null` = hết. Cursor là `id` dòng (số) — riêng `/ads` là chuỗi
`day|platform|account_id|campaign_id`. Keyset chứ không offset: mart nạp thêm dòng giữa lúc
kéo không làm sót/lặp trang đã lấy. Throttle 300 request/giờ theo IP: 92 ngày ≈ 30 trang
đơn, đủ; `/ads` không cache, mỗi trang tính lại cả kỳ — kéo `/ads` theo tuần thay vì 92 ngày.

### Tính lại số gộp cũ từ dòng

| Số cũ | Cách tính từ dòng |
|---|---|
| `leads`, `unassigned`, `unassessed` | `len(/leads/stats)`; đếm `staff == null`; đếm `sales_status.bucket == null` |
| 11 rổ lead, `other` | đếm `/leads/stats` theo `sales_status.bucket` |
| `closed_revenue` | Σ `amounts.total` của `/leads/stats` có `sales_status.code` ∈ {closed, closed_express, created_order} |
| `close_rate` | closed / (leads − dòng có code ∈ {underlying_condition, wants_store_visit}) |
| `total_orders`, `delivered`, `awaiting`, `returned`, `return_risk` | `len(/orders/stats)`; đếm theo `order_status.bucket` |
| `total_revenue`, 4 rổ tiền, `net_revenue` | Σ `amounts.total` của `/orders/stats` (theo bucket); net = total − returned |
| doanh thu theo sản phẩm | Σ `amount` của `/revenue` theo `sku` |
| `open_leads`, `by_status`, `by_age`, `by_staff` | `/pipeline`: đếm; rổ tuổi 0-1d/2-3d/4-7d/8d+ từ `age_days` |
| `ads.by_account[].cost` | Σ `spend_adjusted` mọi dòng `/ads` theo (`platform`, `account_id`) |
| `ads.by_campaign[].cost` | Σ `spend_adjusted` dòng có `campaign_id` |
| `ads.total.cost_outside_campaigns` | Σ `spend_adjusted` dòng `is_remainder` |
| theo Digital | nhóm `/orders/stats` theo `channel`; chi phí: dòng `/ads` có mã kênh trong `channels` (hoặc `definitions.ad_account_channels`) |
| theo chiến dịch (CPA / ROAS) | dòng `/ads` có `campaign_id`: `spend_adjusted / closed` (hoặc `/ orders`), `revenue / spend_adjusted`; hoặc tự nhóm `/leads/stats`, `/orders/stats` theo `fb_campaign_id` — cùng số. Luôn nêu độ phủ `meta.attribution` |
| `summary` | không còn; ghép từ ba endpoint trên |

Chi tiết từng luật: `GET /definitions` → `metrics`.

## Định nghĩa số liệu (khớp màn hình CRM)
- Rổ `order_status.bucket`, `sales_status.bucket` tính ở server từ `apps/orders/reporting/shared.py`;
  API chỉ ghép lại. Nguồn luật không đổi so với bản số gộp.
- Dòng `/orders/stats` cộng lại khớp Báo cáo tổng (`summary_overview`) — test
  `apps/daily_report/tests/test_orders_revenue.py::test_matches_summary_overview` khoá điều này.
- `/ads`: dòng chiến dịch + dòng dư cộng lại khớp `by_account` / `by_campaign` cũ — test
  `apps/daily_report/tests/test_ads.py`. `by_channel` (bảng "Báo cáo theo digital" của app)
  **không còn**: TKQC map nhiều kênh thì app chia đều, API để nguyên mảng `channels`.

## Những quyết định cố ý
- Token tĩnh riêng, không JWT, không dùng chung HERMES_API_TOKEN (thu hồi độc lập).
- Fail-closed trên production.
- Đọc mart, không đọc OLTP: độ trễ ≤ 30 phút, đổi lại query rẻ và có `data_as_of`.
- Không trả SĐT/địa chỉ/transcript.

## Cạm bẫy đã biết
- `data_status` ≠ "Bình thường" nghĩa là số có thể cũ — trợ lý phải nói rõ mốc `data_as_of`.
- `closed_revenue` (sale chốt, từ `/leads/stats`) và `total_revenue` (đã lên hoá đơn, từ
  `/orders/stats`) là hai góc nhìn khác nhau, không cộng vào nhau.
- `sales_status.bucket = null` là chưa đánh giá, KHÔNG phải rổ `other` (`other` = có mã nhưng
  mã lạ). `order_status.bucket = cancelled` không xuất hiện trong `/orders/stats` (đã lọc) nhưng
  có trong `/leads/stats`.
- `/revenue` lọc theo ngày tạo ĐƠN (không theo ngày dòng hàng) để cùng tập với `/orders/stats`.
  Σ `amount` theo `order_id` ≠ `amounts.total` là bình thường (ship/giảm giá cấp đơn).
- `/ads`: dòng dư có thể ÂM (grain ad và grain TKQC sync lệch nhịp) — trả nguyên, không kẹp 0.
  Hệ số FB chọn theo NGÀY BẮT ĐẦU kỳ (docs/ads-facebook-cost-multiplier.md), khác mart
  `ai.chi_phi_quang_cao` áp theo ngày; `meta.spend_as_of` là mốc sync chi tiêu.
- **Quy đơn về chiến dịch (21/09/2026) chỉ phủ một phần.** `fb_campaign_id` bóc từ `utm_id`
  Meta điền trên URL landing (`orders.url_page`, xem `apps/orders/meta_attribution.py`). Chỉ đơn
  nguồn `landing` mà form landing gửi URL và URL có utm mới nối được — tháng 9/2026 ≈ 36% đơn
  landing (kênh Thành/Lợi/Cường ~90%; Tài/Đức ~0% vì form không gửi `url_page`; Google Ads/Web
  không gắn tag). Messenger/hotline/zalo/sàn không bao giờ nối được. Vì thế CPA/ROAS theo
  chiến dịch luôn kèm `meta.attribution` (`leads_with_campaign / leads`,
  `landing_leads_with_campaign / landing_leads`); tổng `orders`/`revenue` trên dòng `/ads` LUÔN
  nhỏ hơn `/orders/stats` — thiếu là do không nối được, không phải campaign không ra đơn.
  Quy theo ngày TẠO lead (lead 30/09 chốt 02/10 tính cho 30/09). Dòng có `campaign_id` mà
  `spend = 0`, `impressions = 0`: chiến dịch có đơn nhưng ngày đó không tiêu (tạm dừng, sync
  trễ); `account_id` rỗng: chiến dịch chưa có trong danh mục đã sync.
- **"Kênh" = Digital, không phải fanpage.** `channel` trên dòng đơn và `channels` trên dòng ads
  đều là mã Digital (`facebook-cuong` = Facebook Cường…). Bảng tra câu hỏi → endpoint nằm ở
  `docs/ai-schema-hermes-brief.md` mục 0.7.
- `ngay_chot` chưa backfill — `closed_at` thường null; nhóm theo `created_date`.
- Staging có `IS_PRODUCTION=false` → không fail-closed khi thiếu token (mở, không
  503). Vẫn phải đặt `DAILY_REPORT_API_TOKEN` ở staging để không lộ số liệu.
- Allowlist IP Tailscale nằm ở `nginx/default.conf` và `nginx/conf.d/default.conf`
  — KHÔNG phải trong các vhost portal `*.conf.prod-template`.

## Triển khai

**21/09/2026 (quy đơn về chiến dịch):** migration `orders.0143` (cột `fb_campaign_id`, index
CONCURRENTLY, không khoá bảng) + `ai_mart.0021`; rồi `python manage.py backfill_fb_attribution
--apply` (điền id cho đơn cũ từ 07/2026, chỉ ghi cột rỗng, chạy lại vô hại) và
`python manage.py ai_mart_rebuild` để mart nhận `chien_dich_fb_id` lịch sử. Bỏ backfill/rebuild
thì `/ads` chỉ có lead/đơn từ lúc deploy trở đi.

Sau `migrate`, chạy `python manage.py ai_mart_rebuild` MỘT LẦN trước khi trỏ trợ lý
vào API. Migration `0013_don_hang_chi_nhanh_nhom_kenh` thêm `chi_nhanh_id` và
`nhom_kenh_ban` là NULL cho các dòng đã có sẵn trong mart; sync định kỳ (`sync_hot`)
chỉ đụng vào đơn thay đổi, không tự backfill lịch sử. Thiếu bước rebuild này:
`/orders/stats` và `/revenue` đếm THỪA các ngày cũ (bộ lọc chi nhánh ẩn là no-op
trên `NULL`) và `channel_group` trả `null` cho toàn bộ lịch sử.

## Cấu hình
| Env | Mặc định | Ghi chú |
|---|---|---|
| `DAILY_REPORT_API_TOKEN` | rỗng | Bắt buộc trên production |
| `DAILY_REPORT_THROTTLE_RATE` | `300/hour` | Theo IP client |

Sinh token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Nhập vào env
server và profile `lich-task` trên Mac; không gửi qua chat. Thu hồi: đổi env + restart.
Allowlist IP Tailscale: khối comment trong `nginx/default.conf`.

## Sinh lại OpenAPI
```
python manage.py spectacular --urlconf apps.daily_report.urls \
  --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS \
  --file ../docs/openapi/daily-report.yaml
```

## Test
`apps/daily_report/tests/` — cửa xác thực, tham số, phân trang keyset, tương đương Báo
cáo tổng từ dòng, dòng dư /ads, shape payload, drift OpenAPI. `apps/ai_mart/tests/test_don_hang.py`
— 2 cột mới + loại đơn gốc khôi phục.
