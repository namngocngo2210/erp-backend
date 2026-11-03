# Vòng đời token Facebook Ads — thiết kế & sprint

**Ngày:** 2026-07-27
**Trạng thái:** đã hoàn thành phần code — chờ cấp lại token thủ công (mục 5)
**Nguồn:** sự cố prod 25–27/07/2026 — `sync_daily_spend failed for app 2/3: Batch error: Error validating access token: Session has expired`

## 1. Bối cảnh sự cố

Log worker lặp lại mỗi 60 giây:

```
[2026-07-27 09:25:24 WARNING/ForkPoolWorker-581] sync_daily_spend failed for app 2:
  Batch error: Error validating access token: Session has expired on Saturday, 25-Jul-26 02:58:53 PDT.
[2026-07-27 09:25:24 WARNING/ForkPoolWorker-581] sync_daily_spend failed for app 3:
  Batch error: Error validating access token: Session has expired on Saturday, 25-Jul-26 03:01:59 PDT.
```

Đường đi của lỗi: `tasks.py::_sync_all_daily_spend` bắt `FacebookGraphError` ném từ
`services.py::fetch_insights_batch` ở nhánh **batch POST trả non-200** — tức token cấp App
bị Graph API từ chối, không phải lỗi lẻ của một ad account (nhánh đó xử lý ở sentinel
`_errors`).

### Nguyên nhân gốc

`FacebookApp.access_token` là **user access token** (thông điệp `Session has expired` chỉ
xuất hiện với token gắn phiên đăng nhập người dùng). Hai app hết hạn cách nhau 3 phút →
cùng được lấy tay trong một lượt, cùng đáo hạn sau 60 ngày.

Model `FacebookApp` chỉ có đúng một trường `access_token`: **không** lưu hạn, **không** có
trạng thái, **không** có cơ chế gia hạn, **không** có cảnh báo. Đây là lỗ hổng thiết kế
chứ không phải sự cố ngẫu nhiên — trong chính codebase này:

| Tích hợp | Cơ chế token | Có theo dõi hạn? |
|---|---|---|
| `google_ads` | OAuth `refresh_token` (không hết hạn) | không cần |
| `best_express` | `token_expires_at` + tự refresh (`best_client.py`) | có |
| `kiotviet` | `expires_in` + `is_expired(buffer)` | có |
| **`facebook_ads`** | **access_token thô, nhập tay** | **không** |

### Hệ quả đã xảy ra

1. Chi tiêu quảng cáo app 2 & 3 đứng im từ 25/07 ~17:00 giờ VN (~2 ngày).
2. `sync_ads_report_lark` chạy mỗi 30 phút **vẫn đẩy số thiếu** sang Lark Bitable → cột
   "Chi phí" và "Tỷ lệ CP/DT" sai theo hướng *đẹp hơn thực tế*. Sai kiểu này nguy hiểm hơn
   báo cáo trống vì không ai nghi ngờ.
3. Cảnh báo duy nhất là `logger.warning` — 1.440 dòng/ngày/app chìm trong log worker,
   không ai đọc. Sentry chỉ bật khi có `SENTRY_DSN`.

## 2. Nguyên tắc thiết kế

- **Token máy-gọi-máy phải là System User token** (Business Manager), không hết hạn. Token
  người dùng chỉ là phương án chữa cháy.
- Hệ thống phải **biết** hạn token, không đợi FB từ chối mới biết.
- Lỗi xác thực (OAuth) khác về bản chất với lỗi tạm thời (429/5xx): lỗi tạm thời thì retry,
  lỗi xác thực thì **ngắt mạch + gọi người**.
- Dữ liệu suy giảm **không được** âm thầm chảy vào báo cáo lãnh đạo.

## 3. Thiết kế

### 3.1 Model — vòng đời token

`FacebookApp` bổ sung:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `token_status` | choices `unknown/ok/expiring/invalid` | trạng thái hiện tại, mặc định `unknown` |
| `token_expires_at` | datetime null | `null` = không hết hạn (System User token) |
| `data_access_expires_at` | datetime null | FB cắt quyền đọc insights 90 ngày sau lần user cuối dùng app |
| `token_checked_at` | datetime null | lần cuối gọi `/debug_token` |
| `token_invalid_since` | datetime null | mốc bắt đầu hỏng — để tính "đã chết bao lâu" |
| `token_error` | text | thông điệp lỗi cuối |

Index `(token_status)` để cron lọc nhanh.

**Reset tự động:** signal `pre_save` so sánh `access_token` với giá trị trong DB; token đổi
→ `token_status = unknown`, xoá `token_error` / `token_invalid_since`. Áp dụng cho cả
Django admin lẫn `PATCH /facebook-ads/apps/{id}` mà không phải sửa từng call site.

### 3.2 Phân loại lỗi Graph API

`services.py` thêm `FacebookTokenError(FacebookGraphError)`. Bộ phân loại đọc
`error.code` / `error.type` từ body thay vì chỉ lấy `message`:

- `code ∈ {102, 190}` (session key hết hạn / access token không hợp lệ), hoặc
  `error_subcode ∈ {458, 459, 460, 463, 464, 467, 492}` → `FacebookTokenError`
- còn lại → `FacebookGraphError` như cũ

**Không** phân loại theo `type`: Facebook trả `type="OAuthException"` cho cả rate
limit (code 4/17/613) lẫn lỗi tạm thời (code 1/2), bắt theo `type` sẽ khoá nhầm app
đang khoẻ và làm mất dữ liệu nhiều hơn chính sự cố.

Áp dụng cho cả 3 điểm gọi (`_fetch_all_ad_accounts`, `fetch_ad_account_insights`,
`fetch_insights_batch`) **và** cho sub-error trong batch: một sub-request lỗi 190 nghĩa là
token cấp app hỏng chứ không phải riêng account đó.

### 3.3 `token_service.py` — kiểm tra & gia hạn

```
debug_token(app)            GET /debug_token?input_token=<token>&access_token=<app_id>|<app_secret>
                            → is_valid, expires_at (0 = never), data_access_expires_at, type, scopes
refresh_token_metadata(app) gọi debug_token, ghi vào model, trả TokenInfo
try_extend_token(app)       GET /oauth/access_token?grant_type=fb_exchange_token&...
                            chỉ chạy khi token CÒN SỐNG và sắp hết hạn; System User token bỏ qua
mark_token_invalid(app, e)  set invalid + token_invalid_since (giữ mốc cũ nếu đã hỏng)
mark_token_ok(app)          xoá trạng thái hỏng sau một lần sync thành công
```

> Token đã hết hạn thì `fb_exchange_token` **vô dụng** — API đó yêu cầu token còn sống.
> Không có cách nào tự phục hồi; bắt buộc con người cấp lại. Đó là lý do phần cảnh báo
> sớm (mục 3.5) quan trọng hơn phần tự gia hạn.

### 3.4 Ngắt mạch trong task đồng bộ

`_sync_all_daily_spend` bỏ qua app có `token_status == invalid` (log gộp 1 lần/giờ qua
Redis key thay vì 1.440 dòng/ngày), và khi bắt `FacebookTokenError` thì đánh dấu invalid +
bắn cảnh báo. Sync thành công → `mark_token_ok`.

### 3.5 Cron kiểm tra token + cảnh báo

Task mới `check_facebook_tokens` (chạy 08:00 hằng ngày):

1. Với mỗi app còn sống: `refresh_token_metadata`.
2. Token hỏng → `invalid` + cảnh báo mức nghiêm trọng.
3. Còn ≤ `FB_TOKEN_EXPIRY_WARN_DAYS` (mặc định 7) → thử `try_extend_token`; vẫn sắp hết hạn
   thì đánh dấu `expiring` + cảnh báo.
4. `expires_at = 0` (System User token) → `ok`, không cảnh báo bao giờ.

`apps/common/ops_alerts.py` (mới, dùng chung được cho google_ads / kiotviet sau này):
gửi Lark custom-bot webhook (`LARK_OPS_ALERT_WEBHOOK_URL`, ký HMAC nếu có secret),
env-gated no-op giống Sentry, dedupe qua Redis để không spam, và **luôn** `logger.error`
để Sentry/Grafana bắt được kể cả khi chưa cấu hình webhook.

### 3.6 Chặn báo cáo Lark khi dữ liệu suy giảm

`sync_ads_report_to_lark()` kiểm tra trước khi đẩy: còn app nào `token_status` thuộc
`{invalid}` → **bỏ qua lượt đẩy**, trả `{"skipped": True, "reason": "facebook_token_invalid"}`
và cảnh báo. Giữ số cũ (stale) tốt hơn ghi đè số thiếu — nhất là khi luồng zero-out sẽ ghi
`Chi phí = 0` cho những ngày không có dữ liệu.

Tham số `force=True` cho phép đẩy tay khi đã hiểu rõ tình trạng.

**Đánh đổi đã cân nhắc:** chặn cả lượt đẩy nghĩa là cột *Doanh thu* (lấy từ `orders`,
không liên quan Facebook) cũng đứng im theo. Chấp nhận vì doanh thu và chi phí nằm
CÙNG một record Bitable — đẩy doanh thu mới kèm chi phí cũ/thiếu thì "Tỷ lệ CP/DT"
sai, mà Bitable không có cách biểu diễn "chi phí chưa biết" trong cột số. Bảng đứng
im + cảnh báo là trạng thái trung thực nhất còn lại; đẩy lại tự động ngay lượt cron
sau khi token được cấp lại.

### 3.7 Nhịp beat

| Task | Trước | Sau | Lý do |
|---|---|---|---|
| `facebook-ads-sync-daily-spend` | 60s | 300s (`FB_SYNC_INTERVAL_SECONDS`) | FB không cập nhật insights theo phút; 60s đốt rate limit và nhân log lỗi lên 5 lần. Bằng nhịp Google Ads. |
| `facebook-ads-check-tokens` | — | mỗi ngày | mới |

## 4. Sprint

### Sprint 1 — Nền tảng vòng đời token
- [x] S1.1 Model + migration `0004_facebookapp_token_lifecycle`
- [x] S1.2 `FacebookTokenError` + phân loại lỗi Graph API
- [x] S1.3 `token_service.py` (`debug_token`, `refresh_token_metadata`, `try_extend_token`)
- [x] S1.4 Signal reset trạng thái khi token đổi

### Sprint 2 — Ngắt mạch & cảnh báo
- [x] S2.1 `apps/common/ops_alerts.py`
- [x] S2.2 Ngắt mạch trong `_sync_all_daily_spend` + log gộp
- [x] S2.3 Task `check_facebook_tokens` + beat hằng ngày

### Sprint 3 — Bảo vệ báo cáo & vận hành
- [x] S3.1 Chặn `sync_ads_report_to_lark` khi token hỏng
- [x] S3.2 Hạ nhịp beat FB xuống 300s (env-configurable)
- [x] S3.3 Phơi trạng thái token ra API + Django admin
- [x] S3.4 Management command `fb_token_status` để vận hành kiểm tra tay
- [x] S3.5 Cột "Token" (badge trạng thái) trên màn hình Facebook Ads

## 5. Việc con người phải làm (không code nào thay được)

1. Vào **Business Manager → System Users → Generate New Token**, chọn app tương ứng,
   quyền `ads_read` (thêm `ads_management` nếu sau này cần ghi), **bỏ tick expiration**.
2. Assign các ad account cần đọc cho System User đó.
3. Dán token mới: `PATCH /api/facebook-ads/apps/{id}` với `{"access_token": "..."}`
   hoặc sửa trong Django admin.
4. Chạy `python manage.py fb_token_status --check` để xác nhận `token_status = ok` và
   `token_expires_at = null` (không hết hạn).

Sau bước này class lỗi "token hết hạn lúc nửa đêm" biến mất vĩnh viễn.

## 6. Env mới

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LARK_OPS_ALERT_WEBHOOK_URL` | rỗng (tắt) | webhook custom bot nhận cảnh báo vận hành |
| `LARK_OPS_ALERT_SECRET` | rỗng | secret ký HMAC nếu bot bật xác thực |
| `FB_TOKEN_EXPIRY_WARN_DAYS` | `7` | ngưỡng cảnh báo sớm |
| `FB_SYNC_INTERVAL_SECONDS` | `300` | nhịp beat đồng bộ chi tiêu FB |

Cả 4 biến đều **tuỳ chọn** — không đặt gì thì hệ thống vẫn chạy đúng: cảnh báo rơi
về `logger.error` (Sentry bắt), ngưỡng cảnh báo 7 ngày, nhịp beat 300s. Tiền tố
`LARK_` nằm trong `_STAGING_FORBIDDEN_PREFIXES` nên staging tự động không gửi được
cảnh báo ra ngoài.

## 7. Tổng kết đợt làm (2026-07-27)

**Đã làm:** toàn bộ Sprint 1–3 (13 mục), 24 test mới, `schema.yaml` + `schema.d.ts`
đã sinh lại.

**Chưa làm / cố ý để ngoài phạm vi:**

- Chưa áp `ops_alerts` cho `google_ads` và `kiotviet` — hai tích hợp đó có cơ chế
  token bền hơn, chưa gây sự cố. Module đã viết sẵn để dùng chung khi cần.
- Chưa có endpoint bấm "Kiểm tra token" từ UI — hiện phải chờ cron 08:00 hoặc chạy
  `fb_token_status --check`. Thêm sau nếu vận hành thấy vướng.
- Chưa backfill dữ liệu chi tiêu bị thiếu của app 2 & 3 trong 25–27/07: sau khi có
  token mới, nhánh recurring chỉ refresh 7 ngày gần nhất (`RECENT_DAYS`) nên sẽ tự
  vá; nếu để lâu hơn 7 ngày mới cấp token thì phải bấm đồng bộ tay với `since/until`.
- 2 test frontend `src/app/__tests__/RequireAuth.test.tsx` đang đỏ **từ trước** đợt
  này (đã kiểm chứng bằng cách stash toàn bộ thay đổi frontend) — không xử lý ở đây.
