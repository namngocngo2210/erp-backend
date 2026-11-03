# Tích hợp chi phí Google Ads (gộp chung với Facebook Ads)

> Trạng thái: **Đang thiết kế** · Branch: `feature/google-ads-cost-integration` · Ngày tạo: 2026-06-08

## 1. Mục tiêu

Kéo chi phí (cost) quảng cáo từ **Google Ads** về hệ thống, **gộp chung** vào báo cáo
`/api/orders/reports/ads` hiện tại theo từng `Channel` — cộng dồn với spend Facebook đã có.
Admin có **nút "Thêm tài khoản Google Ads"** trên UI để khai báo credential và đồng bộ.

Nguyên tắc: **nhân bản pattern `apps/facebook_ads/`** (đã chạy ổn định) để giảm rủi ro và
giữ codebase nhất quán. Chỉ khác ở tầng lấy dữ liệu (Google Ads API + GAQL) và credential.

## 2. Hiện trạng (baseline)

- `apps/facebook_ads/`: `FacebookApp` → `FacebookAdAccount` → `FacebookAdAccountDailySpend`.
- Mapping kênh: `channels_core.Channel.fb_ads_accounts` (ArrayField[int]).
- Tính cost: `orders/_repositories_reports.py::summary_ads_daily_breakdown()` (dòng 560),
  hằng số `ADS_DAILY_COST_MULTIPLIER = 1.1` (dòng 69).
- Đồng bộ: Celery task `sync_all_daily_spend` (60s) + Celery Beat (`config/settings_parts/base.py:181`).
- Credential mã hóa bằng `EncryptedCharField` (dùng `ENCRYPTION_KEY` / Fernet).
- Quyền: decorator `require_section(...)`; section mới đề xuất `"google-ads"`.

## 3. Khác biệt cốt lõi Google Ads vs Facebook

| Khía cạnh | Facebook | Google Ads |
|---|---|---|
| Auth | 1 access_token dài hạn | OAuth2: `developer_token` + `client_id` + `client_secret` + `refresh_token` + `login_customer_id` (MCC) |
| Lấy quyền | Tạo app là dùng được | **Developer token phải được Google duyệt Basic access** (vài ngày → blocker tiến độ) |
| Lấy dữ liệu | REST `/insights` | Thư viện `google-ads` (gRPC) + truy vấn **GAQL** |
| Đơn vị tiền | đơn vị tiền thật | **`cost_micros`** — phải chia 1.000.000 |
| Đa tài khoản | nhiều `FacebookAdAccount`/app | MCC liệt kê nhiều customer con qua 1 credential |

> ⚠️ **Prerequisite ngoài code (làm song song):** tạo Google Cloud Project → bật Google Ads API
> → tạo OAuth Client → apply **Basic access** cho developer token → chạy OAuth consent 1 lần lấy
> `refresh_token`. Không có các thứ này thì Sprint 2 không test thật được (chỉ test với tài khoản test).

## 4. Thiết kế kỹ thuật

### 4.1 Module mới `apps/google_ads/`

Models (mirror Facebook, db_table tiền tố `google_ads_`):

- **`GoogleAdsConfig`** (≈ `FacebookApp`):
  `name`, `developer_token`(enc), `client_id`, `client_secret`(enc), `refresh_token`(enc),
  `login_customer_id` (MCC, unique), `note`, `deleted_at`, timestamps.
- **`GoogleAdsAccount`** (≈ `FacebookAdAccount`):
  `config`(FK), `name`, `customer_id`(10 chữ số), `currency`, `status`(active/removed),
  `last_synced_at`. Unique `(config, customer_id)`.
- **`GoogleAdsAccountDailySpend`** (≈ `FacebookAdAccountDailySpend`) — **giữ y hệt schema**:
  `ad_account`(FK→GoogleAdsAccount), `date`, `spend` DECIMAL(18,2), `impressions`, `clicks`,
  `last_synced_at`. Unique `(ad_account, date)`. Index `(date)`.

### 4.2 Service `apps/google_ads/services.py`

- `_build_client(config)` → `GoogleAdsClient` từ dict credential (không dùng file YAML).
- `sync_accounts_for_config(config)` → `ListAccessibleCustomers` + `customer_client` query
  để liệt kê customer con dưới MCC → upsert `GoogleAdsAccount`.
- `sync_daily_spend_for_config(config, since, until)` → GAQL trên từng customer:
  ```sql
  SELECT segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks
  FROM customer
  WHERE segments.date BETWEEN '{since}' AND '{until}'
  ```
  `spend = cost_micros / 1_000_000` → `update_or_create` theo `(ad_account, date)`.
- Lỗi → raise `GoogleAdsApiError` (mirror `FacebookGraphError`), view trả 502.

### 4.3 Channel mapping

Thêm cột `Channel.google_ads_accounts = ArrayField(IntegerField, default=list)` —
1 migration (mirror `0013_channel_fb_ads_accounts`).

### 4.4 Gộp cost vào báo cáo (thay đổi DUY NHẤT ở logic cũ)

Trong `summary_ads_daily_breakdown()`:
- Lấy thêm spend từ `GoogleAdsAccountDailySpend` theo `Channel.google_ads_accounts`.
- **Cộng dồn** vào cùng `cost` của channel theo `(date, channel_id)` — frontend không đổi.
- Hệ số: hằng `GOOGLE_ADS_DAILY_COST_MULTIPLIER = 1.08` (VAT 8% Google Ads áp tại VN),
  tách riêng khỏi FB `1.1`.

### 4.5 API (mirror `facebook_ads/urls.py`, prefix `^api/google-ads/`)

| Method | Path | Chức năng |
|---|---|---|
| GET/POST | `/api/google-ads/configs` | List / tạo config (POST = nút "Thêm TK Google Ads") |
| PATCH/DELETE | `/api/google-ads/configs/{id}` | Sửa / xóa mềm |
| POST | `/api/google-ads/configs/{id}/sync` | Đồng bộ danh sách customer |
| POST | `/api/google-ads/configs/{id}/sync-daily-spend` | Đồng bộ spend theo ngày |
| GET | `/api/google-ads/configs/{id}/accounts` | List customer con |

Serializer: write_only cho `developer_token`/`client_secret`/`refresh_token` (không trả ra response).
Quyền: `require_section("google-ads")`, nút chỉ hiện khi `isAdmin`.

### 4.6 Celery

- Task `apps.google_ads.tasks.sync_all_daily_spend_google`.
- Beat: thêm entry `google-ads-sync-daily-spend` (đề xuất 300s, Google API quota chặt hơn FB —
  không nên để 60s như FB) vào `CELERY_BEAT_SCHEDULE` + route vào `CELERY_ORDERS_QUEUE`.

### 4.7 Frontend

- `frontend/src/lib/api/google_ads.ts` (mirror `facebook_ads.ts`): create/list/update/delete/sync.
- Trang `GoogleAdsPage.tsx` (mirror `FacebookAdsPage.tsx`): bảng config + Dialog form với field
  `name, developer_token, client_id, client_secret, refresh_token, login_customer_id, note`;
  nút **"Thêm TK Google Ads"**, "Đồng bộ TKQC", "Đồng bộ chi tiêu", Sửa, Xóa.
- Route `google-ads` + Guard `section="google-ads"` (routes.tsx, routed-pages.tsx, routes.ts).
- Sidebar: thêm mục trong group "marketing" (icon lucide, vd `BadgeDollarSign`).
- Channel form: thêm multiselect map `google_ads_accounts` cạnh `fb_ads_accounts`.

### 4.8 Cấu hình / dependency

- `backend/requirements.txt`: thêm `google-ads==<latest>`.
- `.env.example`: không cần key cố định (credential lưu DB/enc); chỉ tái dùng `ENCRYPTION_KEY`.

## 5. Phân chia Sprint

### Sprint 1 — Backend nền tảng (model + service + API, chưa cần token thật) ✅ HOÀN TẤT
- [x] Module `apps/google_ads/`: models + migration `0001_initial` + admin.
- [x] Service tích hợp lib `google-ads`, GAQL, chia `cost_micros` (lazy import để app load khi chưa cài lib).
- [x] Endpoints CRUD config + sync + sync-daily-spend + serializer (secret write-only).
- [x] Đăng ký: INSTALLED_APPS, urls, VALID_SECTIONS `google-ads`, Celery beat (300s) + route, requirements.
- [x] Unit test: `test_services.py` (mock client, cost_micros, lọc manager, idempotent) + `test_api.py` (CRUD, quyền, write-only). **12/12 pass.**
- **Note sau sprint:** `manage.py check` sạch. CHƯA làm (để Sprint 2/3): map `Channel.google_ads_accounts`,
  gộp cost vào `summary_ads_daily_breakdown()`, frontend. Cần `pip install -r requirements.txt` để có lib `google-ads`
  trước khi sync thật. Endpoint `GET /api/google-ads/configs/{id}/daily-spend` (summary) chưa làm — thêm ở Sprint 2 nếu cần.

### Sprint 2 — Đồng bộ thật + gộp báo cáo ✅ HOÀN TẤT (phần code, không cần token)
- [x] Celery task `sync_all_daily_spend_google` + Beat entry (300s) — đã làm ở Sprint 1.
- [x] `Channel.google_ads_accounts` (ArrayField) + migration `0019` + serializer/repository/views wiring.
- [x] Sửa `summary_ads_daily_breakdown()`: helper `_apply_cost` cộng dồn spend FB (×1.1) + Google (×1.08) theo ngày/kênh.
- [x] Hằng số `GOOGLE_ADS_DAILY_COST_MULTIPLIER = 1.08`.
- [x] Frontend: form Channel (create + manage) thêm multiselect "Tài khoản quảng cáo Google".
- [x] Test `test_ads_report_daily_breakdown_merges_facebook_and_google_cost` (FB 55000 + Google 108000 = 163000).
- [ ] Prerequisite còn lại (việc ngoài code): developer token Basic access + refresh_token để sync số liệu thật.
- **Note sau sprint:** Toàn bộ code Sprint 1→3 xong, type-clean (tsc/check). Khi có credential thật → nhập ở trang
  Google Ads → "Đồng bộ TK" → map account vào kênh → cost Google tự gộp vào AdsReportPage.

### Sprint 3 — Frontend ✅ HOÀN TẤT (phần quản lý tài khoản)
- [x] API client `lib/api/google_ads.ts` (CRUD config + sync accounts + sync daily spend + list accounts).
- [x] `GoogleAdsPage.tsx`: bảng config + **nút "Thêm TK Google Ads"** + form (developer_token/client_id/client_secret/refresh_token/login_customer_id) + nút Đồng bộ TK / Đồng bộ chi tiêu / Sửa / Xóa; bảng customer con.
- [x] Wiring: section `google-ads` (routes.ts), route + Guard (routes.tsx), `GoogleAdsRoute` (routed-pages.tsx), sidebar group marketing (icon BadgeDollarSign), title (App.tsx), i18n (topnav + page).
- [x] Verify: `eslint` 0 errors, `tsc --noEmit` _(đang chạy)_.
- **Note sau sprint:** Channel form map `google_ads_accounts` GỘP vào Sprint 2 (cần làm cùng lúc với cột DB + sửa report).
  Phần xem cost gộp trong AdsReportPage hiển thị sau khi Sprint 2 cộng dồn spend FB+Google.

## 6. Quyết định đã chốt
1. **`GOOGLE_ADS_DAILY_COST_MULTIPLIER` = 1.08** (VAT 8%).
2. Tần suất Beat sync Google: 300s (quota API chặt hơn FB).
3. Developer token: **chưa có** → cần setup từ đầu (xem `docs/google-ads-setup-guide.md`).
   Sprint 1 + 3 code trước không cần token; Sprint 2 sync thật chạy sau khi token Basic access được duyệt.
