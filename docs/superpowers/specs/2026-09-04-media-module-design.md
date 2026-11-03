# Spec — Module Media (tích hợp ads-crm qua REST `/api/v1`)

> Ngày tạo: 2026-09-04. Branch: `feat/media-module`.
> Chuẩn áp dụng: `docs/architecture-standard.md` (clean architecture backend, feature-slice
> frontend), `docs/erp-app-shell.md` (registry module/sidebar). App tham chiếu cho tích hợp
> hệ thống ngoài: `apps/foxia`.

---

## 1. Mục tiêu & phạm vi

**Mục tiêu**
- Thêm module **Media** vào sidebar desi-order, cho người dùng thao tác thư viện nội dung,
  đánh giá ads, đề xuất nội dung, danh mục và báo cáo của hệ thống **ads-crm** mà không
  phải đăng nhập sang hệ thống đó.
- Phân quyền **theo từng người**, mô phỏng ma trận role của ads-crm (`viewer` /
  `marketing` / `uploader` / `admin`), theo mô hình **kết hợp**: user có API key ads-crm
  riêng thì dùng key riêng (ads-crm tự phân quyền, ghi đúng tác giả); user chưa có key thì
  dùng **key chung** của hệ thống cộng với **vai trò Media** admin gán trên desi-order,
  backend desi-order tự chặn theo ma trận.
- API key **không bao giờ** đi ra trình duyệt. Mọi request sang ads-crm đi qua backend
  desi-order (`/api/media/...`).

**Ngoài phạm vi (quyết định của chủ dự án 2026-09-04)**
- Lưu video lên Google Drive (`save-to-gdrive`, `gdrive/*`) — bỏ.
- Xuất CSV (`/contents/export`) — bỏ.
- Quản trị của ads-crm: `users`, `api-keys`, `audit-logs`, `video-cache/stats`,
  `notifications`, `auth/login|logout|change-password`, `ws`. Quản lý tại ads-crm.
- Phát video trực tiếp (`GET /contents/:id/video`) — ads-crm chỉ nhận JWT của họ, key
  không dùng được. Chỉ có nút **tải file**.
- Đồng bộ mirror dữ liệu ads-crm về DB nội bộ — không làm; mọi thao tác đọc/ghi là
  thời gian thực. Nếu sau này cần join với đơn hàng/sản phẩm desi-order sẽ mở spec khác.

---

## 2. Quyết định đã chốt

| # | Quyết định |
|---|---|
| D1 | Tên module hiển thị: **"Media"** (cả vi/en). |
| D2 | Kiến trúc: **backend proxy mỏng**, allowlist endpoint, không lưu dữ liệu nghiệp vụ ads-crm. |
| D3 | Xác thực sang ads-crm theo thứ tự: **key riêng của user** → **key chung + vai trò Media** → từ chối (`403 media_not_provisioned`). |
| D4 | Với key chung, backend enforce ma trận role ads-crm (§5.2) **trước khi** forward; endpoint không nằm trong allowlist → 404 (fail-closed). |
| D5 | Menu Media hiện cho user **đã được cấp quyền** (có key riêng hoặc có vai trò Media). Không thêm section Media vào `PERMISSION_CATALOG`; `allowed_sections` được backend bổ sung khi user đã cấp quyền. |
| D6 | Cấu hình kết nối (host + key chung) lưu **DB**, sửa qua trang Cài đặt → Media, không qua env. |
| D7 | Làm **toàn bộ** 3 giai đoạn (đọc, ghi, tệp) trong một branch, chia commit theo giai đoạn. |
| D8 | Thumbnail: `<img>` hotlink thẳng endpoint công khai của ads-crm (`{base_url}/api/v1/contents/:id/thumbnail`), có placeholder khi lỗi. Không proxy ảnh. |
| D9 | Tải video: backend stream (`StreamingHttpResponse`) từ `GET /contents/:id/download`; frontend fetch blob có token rồi lưu file. |
| D10 | Chặn **toàn bộ** outbound sang ads-crm khi `APP_ENV=staging` (giống Foxia): staging chạy snapshot prod, không được đụng hệ thống đối tác bằng khoá thật. |

---

## 3. Sidebar & route (frontend registry)

Thêm module vào `frontend/src/app/modules.ts`, zone **ops**, đứng sau `marketing`:

```ts
{
  key: "media",
  labelKey: "sidebar.group.media",       // "Media"
  shortLabelKey: "module.media",         // "Media"
  icon: Clapperboard,                    // lucide-react
  accent: { chip: "bg-indigo-400", tile: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400" },
  basePath: "/media",
  sections: [
    { key: "media-contents",  labelKey: "topnav.tabs.mediaContents",  icon: Film },
    { key: "media-proposals", labelKey: "topnav.tabs.mediaProposals", icon: Lightbulb },
    { key: "media-reports",   labelKey: "topnav.tabs.mediaReports",   icon: BarChart3 },
    { key: "media-taxonomy",  labelKey: "topnav.tabs.mediaTaxonomy",  icon: Tags },
  ],
}
```

Module `settings` thêm section `{ key: "media-settings", labelKey: "topnav.tabs.mediaSettings",
icon: Plug, requires: "users" }` — trang cấu hình kết nối, chỉ admin (cùng cách gate với
`departments`).

| Section | Path | Nhãn vi | Nhãn en |
|---|---|---|---|
| `media-contents` | `/media/contents`, chi tiết `/media/contents/:id` | Thư viện nội dung | Content library |
| `media-proposals` | `/media/proposals` | Đề xuất nội dung | Content proposals |
| `media-reports` | `/media/reports` | Báo cáo Media | Media reports |
| `media-taxonomy` | `/media/taxonomy` | Danh mục Media | Media taxonomy |
| `media-settings` | `/settings/media` | Kết nối Media | Media connection |

Cần sửa kèm: `AppSection` + `SECTION_TO_PATH` (`lib/routes.ts`), `sectionFromPath` nhận
`/media/contents/:id` về `media-contents` (thêm một dòng regex như đã làm cho
`/catalog/products`), `routes.tsx` (route lazy + `Guard`), `VALID_SECTIONS` backend (5 key
mới — bắt buộc để superuser không bị ẩn menu, xem chú thích `salaries` trong file đó),
i18n `sidebar.ts`/`topnav.ts`/`app.ts`.

---

## 4. Backend — app `apps/media`

### 4.1 Cấu trúc

```
apps/media/
├── models.py                 # MediaConnection, MediaContentOwner, MediaProposalOwner
├── domain/
│   ├── roles.py              # MediaRole enum + ma trận ROLE_MATRIX (thuần Python)
│   └── errors.py             # map mã lỗi ads-crm → (http_status, message vi)
├── contracts/dtos.py         # Credential(kind, key, role), MediaIdentity
├── application/services/
│   ├── credential_resolver.py  # user → Credential | raise NotProvisioned
│   ├── access_policy.py        # (Credential, route, method, ownership) → allow/deny
│   ├── proxy_service.py        # gọi client, cache taxonomy, ghi ownership
│   └── connection_service.py   # test kết nối (/auth/me), lưu settings
├── infrastructure/
│   └── ads_crm_client.py     # HttpClient + X-API-Key + X-Request-ID, parse lỗi
├── transport/
│   ├── api_views.py          # /api/media/* (mỏng)
│   ├── settings_views.py     # /api/media/settings*
│   └── serializers.py
├── urls.py
├── migrations/
└── tests/
```

Đăng ký: `INSTALLED_APPS` thêm `apps.media`; `config/urls.py` thêm
`re_path(r"^api/media/", include("apps.media.urls"))`; `.importlinter` thêm `apps.media` vào
tầng ngang hàng `apps.foxia`.

### 4.2 Model

**`MediaConnection`** (một dòng, giống `FoxiaConnection`):

| Field | Kiểu | Ghi chú |
|---|---|---|
| `base_url` | CharField | VD `https://media.example.com` (không kèm `/api/v1`) |
| `shared_api_key` | `EncryptedCharField(1024)` | Key chung, nên gắn tài khoản `admin` bên ads-crm |
| `is_active` | Bool | Tắt = mọi request Media trả `503 media_disabled` |
| `last_check_at`, `last_check_ok`, `last_check_error` | | Kết quả lần "Kiểm tra kết nối" cuối |
| `shared_key_role`, `shared_key_username` | Char | Role/username trả về từ `/auth/me` với key chung |
| `created_at`, `updated_at` | | |

**`User`** (app `users`, migration 0046) thêm hai field:

| Field | Kiểu | Ghi chú |
|---|---|---|
| `media_role` | CharField choices `""`/`viewer`/`marketing`/`uploader`/`admin`, default `""` | Vai trò Media khi dùng key chung |
| `media_api_key` | `EncryptedCharField(1024)`, blank | Key ads-crm riêng của user. **Write-only** qua API; serializer chỉ trả `media_api_key_set: bool` và `media_api_key_prefix` (12 ký tự đầu, để admin nhận diện) |

**`MediaContentOwner`** / **`MediaProposalOwner`**: `(remote_id: BigInt unique, user FK,
created_at)`. Ghi khi user tạo/nhân bản nội dung hoặc tạo đề xuất **qua key chung**. Dùng
để mô phỏng kiểm tra `not_content_owner` / `not_proposal_owner` cho role `uploader` và cho
`PATCH /proposals/:id` (ads-crm chỉ thấy tài khoản service là chủ, không phân biệt được
người thật). Không ghi khi dùng key riêng — ads-crm đã tự biết chủ sở hữu.

### 4.3 Client `AdsCrmClient`

- Khởi tạo với `base_url`, `api_key`, `timeout` (mặc định `MEDIA_HTTP_TIMEOUT=30`, riêng
  download 120s). Hai `HttpClient`: `_reader` (retries=3, backoff, retry 429/5xx) cho GET;
  `_writer` (retries=0) cho POST/PATCH/DELETE — theo cảnh báo trong
  `apps/common/http_client.py`, mutation không được tự retry.
- Header: `X-API-Key`, `Content-Type: application/json`, `X-Request-ID` = uuid4 (log kèm
  để đối soát với ads-crm).
- Trả về `(status_code, json_or_bytes, headers)`. Nếu body là `{"error": code}` với status
  ≥ 400 → raise `AdsCrmApiError(code, status)`. Lỗi mạng → `AdsCrmUnavailable`.
- Guard staging: `settings.IS_STAGING` → raise `AdsCrmBlocked` trước khi ra mạng (D10).
- `download(content_id)` trả `requests.Response` với `stream=True` để view stream tiếp.

### 4.4 Credential resolver (D3)

```
resolve(user) -> Credential:
  conn = MediaConnection.get_active() or raise MediaDisabled (503)
  if user.media_api_key:    return Credential(kind="personal", key=user.media_api_key, role=None)
  if user.media_role:       return Credential(kind="shared",   key=conn.shared_api_key, role=user.media_role)
  raise NotProvisioned (403 media_not_provisioned)
```

Với `personal`, role hiệu lực để **hiển thị** lấy từ `GET /auth/me` của ads-crm, cache Redis
10 phút theo `sha256(key)`; ads-crm là nơi enforce thật. Với `shared`, role hiệu lực =
`media_role`, enforce tại `access_policy`.

### 4.5 Endpoint proxy `/api/media/*`

Mirror path ads-crm 1:1 (bỏ tiền tố `/api/v1`) để type frontend map thẳng tài liệu.
Query param được pass-through **theo allowlist từng route**, body JSON pass-through sau khi
qua serializer kiểm kiểu tối thiểu (backend không lặp lại validation nghiệp vụ của ads-crm;
lỗi `400` của họ được map và trả nguyên).

| Method | Path desi-order | Path ads-crm | Quyền (§5.2) |
|---|---|---|---|
| GET | `me` | tổng hợp | đã cấp quyền |
| GET | `contents` | `/contents` (+ mọi filter §3.4 tài liệu) | read |
| GET | `contents/:id` | `/contents/:id` | read |
| POST | `contents` | `/contents` | content.create |
| POST | `contents/:id/duplicate` | cùng | content.create |
| POST | `contents/import` | — (đọc xlsx rồi lặp `POST /contents`) | content.create |
| GET | `contents/import-template` | — (file mẫu sinh tại chỗ) | read |
| GET | `contents/export` | — (gom `GET /contents` rồi ghi xlsx) | read |
| PATCH | `contents/:id/upload-info` | cùng | content.edit_upload (owner) |
| PATCH | `contents/:id/marketing-info` | cùng | content.edit_marketing (owner nếu uploader) |
| DELETE | `contents/:id` | cùng | content.delete |
| GET | `contents/:id/download` | cùng (stream) | read |
| POST | `contents/:id/video/cache` (`?force=1`) | cùng | read; `force` chỉ admin |
| GET | `contents/:id/ad-evaluations` | cùng | read |
| POST | `contents/:id/ad-evaluations` | cùng | evaluation.write |
| PATCH/DELETE | `ad-evaluations/:id` | cùng | evaluation.write |
| GET | `{taxonomy}` (5 nhóm, `?include_inactive`) | cùng | read (include_inactive: admin) |
| POST | `{taxonomy}` | cùng | taxonomy.write (`content-sources`: cả uploader) |
| PATCH/DELETE | `{taxonomy}/:id` | cùng | taxonomy.write |
| GET | `proposals`, `proposals/:id` | cùng | read |
| POST | `proposals` | cùng | proposal.create |
| PATCH | `proposals/:id` | cùng | proposal.edit (owner hoặc admin) |
| DELETE | `proposals/:id` | cùng | proposal.delete |
| GET | `reports/overview`, `reports/ratings-by-channel`, `reports/content-quality` | cùng | read |
| GET/PATCH | `settings` | — (DB nội bộ) | `users:edit` |
| POST | `settings/test` | `/auth/me` với key chung | `users:edit` |

Mọi path khác dưới `/api/media/` → 404. Không có route "catch-all" forward.

**`GET /api/media/me`** trả:
```json
{
  "provisioned": true,
  "mode": "personal" | "shared",
  "role": "uploader",
  "remote_user": { "id": 7, "username": "erp_bot", "name": "..." },
  "public_base_url": "https://media.example.com",
  "connection_ok": true
}
```
Chưa cấp quyền → `200 {"provisioned": false, ...}` (không 403, để trang hiện hướng dẫn).

**Excel (bổ sung 2026-09-10)**: ads-crm không có API tạo hàng loạt (chỉ có `GET
/contents/export` ra CSV), nên ba route trên KHÔNG phải proxy 1:1: desi-order tự đọc/ghi
xlsx (`infrastructure/content_excel.py`) và gọi `POST /contents` từng dòng qua `MediaProxy`
(`application/services/excel_service.py`). File import khớp theo TÊN nguồn/loại/sản phẩm/
danh mục (không cần id), tối đa 500 dòng, ≤5MB; dòng sai bị bỏ qua và trả về kèm số dòng,
dòng hợp lệ vẫn được tạo (ads-crm không có transaction). Lỗi hạ tầng thì dừng cả file.
File xuất giữ nguyên 7 cột của file mẫu ở đầu để sửa xong import ngược lại được.

**Tải video**: view gọi `client.download()` stream, forward `Content-Type`,
`Content-Disposition`, `Content-Length` nếu có. Lỗi ads-crm (`422 photo_post`, `503 busy`,
`502 download_failed`) map như §4.6.

**Cache**: 5 danh sách taxonomy cache Redis 5 phút, key `media:tax:{group}:{include_inactive}`;
mọi POST/PATCH/DELETE vào nhóm đó xoá cả hai biến thể. `/auth/me` cache 10 phút/key. Không
cache contents/proposals/reports.

### 4.6 Map lỗi

Response lỗi của proxy theo format desi-order: `{"detail": "<tiếng Việt>", "code": "<mã>"}`,
giữ **nguyên HTTP status** của ads-crm. Bảng `domain/errors.py` dịch toàn bộ mã ở §5 tài
liệu (`invalid_url` → "URL không hợp lệ (cần scheme + host)", `duplicate_record` → "Bản ghi
đã tồn tại", `photo_post` → "Bài dạng ảnh, không có video", `busy` → "Nguồn tải đang bận,
thử lại sau vài giây", ...). Mã lạ → `detail` = mã gốc.

Lỗi riêng của proxy:

| HTTP | code | Khi nào |
|---|---|---|
| 403 | `media_not_provisioned` | User chưa có key riêng lẫn vai trò Media |
| 403 | `media_forbidden` | Vai trò Media (key chung) không đủ theo ma trận |
| 403 | `media_not_owner` | Uploader sửa nội dung/đề xuất không do mình tạo (key chung) |
| 503 | `media_disabled` | Chưa cấu hình hoặc `is_active=false` |
| 503 | `media_staging_blocked` | Chạy trên staging |
| 502 | `media_upstream_unavailable` | ads-crm không phản hồi / trả body không phải JSON |
| 401 → 502 | `media_key_rejected` | ads-crm trả `invalid_token`: key hỏng/hết hạn. Đổi thành 502 để frontend **không** logout user desi-order (interceptor 401 của `fetchJson` sẽ hiểu nhầm là phiên hết hạn) |

### 4.7 Bổ sung ở app `users`

- `UserSerializer` trả thêm `media_role`, `media_api_key_set`, `media_api_key_prefix`.
- `AdminUserCreateSerializer` / `AdminUserUpdateSerializer` nhận `media_role`,
  `media_api_key` (write-only; chuỗi rỗng ở update = **giữ nguyên**, gửi `null` = xoá key).
  Validate key có prefix `dak_` nếu không rỗng.
- `get_allowed_sections`: sau khi tính như hiện tại, nếu `user.media_api_key or
  user.media_role` thì union thêm 4 section `media-contents|proposals|reports|taxonomy`
  (D5). Superuser: `VALID_SECTIONS` đã chứa 5 key Media, nhưng 4 trang nội dung vẫn cần
  provisioning để gọi được ads-crm — superuser chưa cấp quyền sẽ thấy menu và nhận màn
  "chưa được cấp quyền" của trang; đây là chủ ý, tránh đặc-cách làm hai luồng.
- Đổi `media_api_key`/`media_role` ghi audit log như các field user khác.

---

## 5. Phân quyền

### 5.1 Hai lớp

1. **desi-order**: chỉ hai câu hỏi — user đã cấp quyền Media chưa (D3/D5), và trang Cài đặt
   Media (`users:edit`). Không thêm section vào `PERMISSION_CATALOG`.
2. **Ma trận role Media** (§5.2): với key riêng do ads-crm enforce; với key chung do
   `access_policy` enforce trên desi-order. Frontend dùng cùng ma trận (từ `/api/media/me`)
   để ẩn nút — UX, không phải enforcement.

### 5.2 Ma trận `ROLE_MATRIX` (mirror §3.3 tài liệu ads-crm)

| Hành động | viewer | marketing | uploader | admin |
|---|:--:|:--:|:--:|:--:|
| `read` (mọi GET, download, video/cache) | ✅ | ✅ | ✅ | ✅ |
| `content.create` (POST contents, duplicate) | ❌ | ❌ | ✅ | ✅ |
| `content.edit_upload` (upload-info) | ❌ | ❌ | ✅ chủ sở hữu | ✅ |
| `content.edit_marketing` (marketing-info) | ❌ | ✅ | ✅ chủ sở hữu | ✅ |
| `content.delete` | ❌ | ❌ | ❌ | ✅ |
| `video.cache_force` | ❌ | ❌ | ❌ | ✅ |
| `evaluation.write` (ad-evaluations C/U/D) | ❌ | ✅ | ❌ | ✅ |
| `taxonomy.write` (4 nhóm) | ❌ | ❌ | ❌ | ✅ |
| `taxonomy.write_sources` (POST content-sources) | ❌ | ❌ | ✅ | ✅ |
| `taxonomy.include_inactive` | ❌ | ❌ | ❌ | ✅ |
| `proposal.create` | ✅ | ✅ | ✅ | ✅ |
| `proposal.edit` | chủ sở hữu | chủ sở hữu | chủ sở hữu | ✅ |
| `proposal.delete` | ❌ | ❌ | ❌ | ✅ |

"Chủ sở hữu" với key chung = có dòng trong `MediaContentOwner`/`MediaProposalOwner` trỏ
tới user; không có dòng → từ chối (`media_not_owner`). Với key riêng, ads-crm tự trả
`403 not_content_owner`/`not_proposal_owner`, proxy map nguyên.

### 5.3 Giới hạn ghi nhận (không sửa trong spec này)

- Với **key chung**, phía ads-crm ghi `created_by`, `evaluator_name`, audit log là tài khoản
  service — không phải người thật. Muốn đúng tác giả thì cấp key riêng cho người đó.
- API ads-crm chưa cho đặt `channel_id` khi tạo đánh giá → báo cáo "rating theo kênh" có
  thể rỗng. Frontend hiển thị ghi chú này trên trang báo cáo.
- Với key chung, nếu bảng owner bị lệch (nội dung tạo trực tiếp trên web ads-crm) thì
  uploader không sửa được từ desi-order — cần admin Media.

---

## 6. Frontend — slice `features/media/`

```
features/media/
├── access.ts              # MediaRole, mediaCan(role, action) — cùng ma trận §5.2
├── queries.ts             # react-query hooks; key prefix ["media", ...]
├── filters.ts             # ContentFilters ↔ URLSearchParams (list param lặp `a=1&a=2`)
├── components/
│   ├── MediaAccessGate.tsx        # bọc trang: chưa provisioned → EmptyState hướng dẫn
│   ├── ContentFilterBar.tsx
│   ├── ContentTable.tsx           # thumbnail, mã, tiêu đề, nguồn, loại, tag, rating, ngày
│   ├── ContentDetailSheet.tsx     # drawer /media/contents/:id
│   ├── ContentFormDialog.tsx      # tạo / sửa upload-info
│   ├── ContentTagsDialog.tsx      # marketing-info (loại + sản phẩm + danh mục)
│   ├── AdEvaluationList.tsx       # list + form thêm/sửa/xoá inline
│   ├── ProposalTable.tsx / ProposalFormDialog.tsx
│   ├── TaxonomyTabs.tsx / TaxonomyFormDialog.tsx
│   └── ReportKpiRow.tsx / ReportTables.tsx
└── pages/
    ├── MediaContentsPage.tsx
    ├── MediaProposalsPage.tsx
    ├── MediaReportsPage.tsx
    ├── MediaTaxonomyPage.tsx
    └── MediaSettingsPage.tsx      # /settings/media
lib/api/media.ts           # type theo §4 tài liệu ads-crm + hàm fetch /api/media/*
```

Giữ mỗi file ≤ 400 dòng; dùng bộ `components/ui` sẵn có (`table`, `tabs`, `sheet`,
`dialog`, `date-filter`, `multi-select`, `searchable-select`, `pagination`,
`empty-state`, `page-header`). Không thêm thư viện.

### 6.1 Hành vi từng trang

**Thư viện nội dung** (`/media/contents`)
- Filter: nguồn, loại, rating tổng, sản phẩm, danh mục (multi-select từ taxonomy); kênh ads
  + điểm ads (ô điểm chỉ bật khi đã chọn kênh — tài liệu §3.4); khoảng ngày upload; sort
  `uploaded_at|title|rating` + thứ tự. Filter lưu trên URL query để share link.
- Bảng phân trang `page_size` 20/50/100. Thumbnail hotlink (D8) với `onError` → placeholder.
- Nút "Thêm nội dung" (`content.create`). Hàng: mở chi tiết; menu: nhân bản, xoá (`content.delete`, có confirm).
- Chi tiết (Sheet, URL `/media/contents/:id`): thông tin upload + nút sửa; tag + nút sửa;
  đánh giá ads (danh sách, thêm/sửa/xoá theo `evaluation.write`, rating step 0.5 bằng
  select 0..5); badge `video_status`; nút "Tải video" (fetch blob → lưu file, hiện
  spinner, thông báo lỗi `photo_post`/`busy`); nút "Cache video" cho link TikTok khi
  `video_status != success`, `force` chỉ admin.

**Đề xuất nội dung** (`/media/proposals`)
- Filter trạng thái (multi), sản phẩm (multi), ô tìm `q`. Bảng có sản phẩm, hạn, trạng
  thái, người tạo, nội dung liên kết (link sang chi tiết).
- Tạo/sửa dialog: `main_key` (bắt buộc), `idea`, `due_date`, `status`, `product_id`
  (sản phẩm còn active), `content_id` (tuỳ chọn, chọn từ ô tìm nhanh theo mã/tiêu đề).
  PATCH gửi đủ field (tài liệu §2.3 — ghi đè).
- Xoá: admin.

**Báo cáo Media** (`/media/reports`)
- `DateFilter` → `uploaded_from/uploaded_to`. KPI: tổng nội dung, thiếu tag, thiếu đánh
  giá. Bảng "theo loại", bảng "rating theo kênh" (kèm ghi chú §5.3), hai bảng chất lượng
  (mỗi dòng link sang chi tiết; ghi rõ "tối đa 50 dòng").

**Danh mục Media** (`/media/taxonomy`)
- 5 tab. Mỗi tab bảng `id/name(+code/group)/is_active`, nút thêm, sửa (dialog), xoá
  (confirm; ghi rõ "soft delete, nội dung đang gắn không đổi"). Toggle "hiện cả đã tắt"
  chỉ admin. `content-sources`: uploader cũng được thêm.

**Kết nối Media** (`/settings/media`)
- Form `base_url`, `shared_api_key` (ô password, để trống = giữ nguyên), `is_active`;
  nút "Kiểm tra kết nối" gọi `settings/test` và hiện username/role của key chung + cảnh
  báo nếu role ≠ `admin` ("người dùng key chung sẽ bị 403 khi sửa nội dung không do key
  tạo").
- Dialog Người dùng (create/edit, cả dialog tạo tài khoản nhân sự) thêm hai ô: **Vai trò
  Media** (select: Không / Viewer / Marketing / Uploader / Admin) và **API key Media
  riêng** (password; hiển thị prefix + nút "Xoá key" khi đã có). Ghi chú ngay dưới ô:
  "Có key riêng thì bỏ qua vai trò Media; ads-crm tự phân quyền".

### 6.2 Trạng thái lỗi/không quyền
- `MediaAccessGate`: `provisioned=false` → EmptyState "Bạn chưa được cấp quyền Media. Nhờ
  admin gán vai trò Media hoặc nhập API key riêng." `connection_ok=false` → banner đỏ như
  trang Foxia.
- Lỗi mutation hiện `detail` từ backend qua toast (`sonner`), không parse lại code.

---

## 7. Cấu hình & vận hành

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `MEDIA_HTTP_TIMEOUT` | `30` | Timeout giây cho request thường |
| `MEDIA_DOWNLOAD_TIMEOUT` | `120` | Timeout tải video |
| `MEDIA_TAXONOMY_CACHE_TTL` | `300` | TTL cache taxonomy |

Host + key chung nằm trong DB (D6). Rate limit ads-crm 120 req/phút/key: với key chung,
mọi user không có key riêng gộp vào một trần — admin nên đặt `rate_per_min` cao hơn cho
key chung bên ads-crm; proxy trả nguyên `429 rate_limited` để frontend hiện "Hệ thống
Media đang giới hạn tốc độ, thử lại sau".

Metric: đếm request theo `(route, mode, status)` bằng prometheus_client như `apps/foxia/metrics.py`.

---

## 8. Kiểm thử

**Backend (`apps/media/tests/`, pytest, mock `HttpClient`)**
- `credential_resolver`: ba nhánh D3 + connection tắt.
- `access_policy`: bảng tham số hoá toàn bộ §5.2 × 4 role, kể cả ownership.
- `ads_crm_client`: parse `{"error": code}`, lỗi mạng, staging block, header `X-API-Key`
  và `X-Request-ID`, writer không retry.
- `proxy views`: allowlist (path lạ → 404), pass-through query list, map lỗi (đặc biệt
  `invalid_token` → 502), cache taxonomy + invalidate, ghi `MediaContentOwner` khi tạo qua
  key chung và **không** ghi khi key riêng, stream download forward header.
- `users`: serializer không lộ `media_api_key`; update rỗng giữ nguyên, `null` xoá;
  `allowed_sections` có/không 4 section Media theo provisioning.
- `settings`: chỉ `users:edit`; test kết nối lưu `last_check_*`.

**Frontend (vitest)**
- `nav-layout`: module Media xuất hiện khi allowed_sections có `media-contents`; ẩn khi không.
- `access.ts`: ma trận `mediaCan` khớp §5.2.
- `filters.ts`: round-trip filter ↔ query string, tham số lặp.
- Smoke render 4 trang với `/api/media/me` provisioned/unprovisioned (msw hoặc mock fetchJson).
- Sidebar snapshot test hiện có cập nhật.

Chạy theo `memory: running-backend-tests` (container pg:5433/redis:6380, `--create-db` vì có
migration mới) và docker node cho vitest/typecheck.

---

## 9. Thứ tự triển khai (một branch, commit theo giai đoạn)

1. **Nền + đọc**: model + migration (`media` 0001, `users` 0046), client, resolver,
   policy, proxy GET (contents, taxonomy, proposals, reports, me), settings API; frontend
   registry/route/i18n, `MediaAccessGate`, 4 trang chế độ đọc, trang Cài đặt Media, ô
   Media trong dialog Người dùng.
2. **Ghi**: POST/PATCH/DELETE contents, tags, đánh giá ads, proposals, taxonomy + ownership
   table; dialog/form tương ứng; invalidate cache.
3. **Tệp**: tải video stream, cache video.

Mỗi giai đoạn: test xanh, `typecheck` + `lint`, regen `schema.yaml`/`schema.d.ts` nếu đổi
serializer `users`.

---

## 10. Nguồn tham chiếu

- Tài liệu API ads-crm: mục "Tích hợp API đối ngoại" (do chủ dự án cung cấp 2026-09-04);
  hợp đồng `docs/openapi.yaml` nằm ở repo ads-crm, không có trong repo này. Khi có file
  đó, sinh `lib/api/media.ts` bằng `openapi-typescript` thay cho viết tay.
- Mẫu tích hợp: `apps/foxia` (connection model, client, staging guard, trang báo cáo).
- Mẫu field per-user: commit `b50e9aa8` ("Email Zenify").
