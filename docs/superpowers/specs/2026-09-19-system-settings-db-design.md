# Spec — Chuyển cài đặt vận hành từ `.env` sang DB (Cấu hình hệ thống)

> Ngày tạo: 2026-09-19. Branch dự kiến: `feat/system-settings-db`.
> Chuẩn áp dụng: `docs/architecture-standard.md`, `docs/erp-app-shell.md`.
> Khuôn tham chiếu: `FoxiaConnection`/`PancakeConnection` (secret mã hoá bằng
> `apps.common.fields.EncryptedCharField`), `FloatingPoolConfig` (cấu hình nghiệp vụ
> trong DB), trang `PancakeSettingsPage` (trang cài đặt superuser-only),
> `apps.common.cache_backend` (triết lý fail-soft khi Redis lỗi),
> `docs/staging-environment.md` (`staging_neutralize`).

---

## 1. Mục tiêu & phạm vi

**Mục tiêu**
- Các **credential đối tác** (Lark, Gemini, Zenify, token API vào…) và **tham số
  nghiệp vụ / cờ bật-tắt** (bật đẩy Foxia, ngưỡng chấm QA, thông tin công ty trên
  portal…) nằm trong DB, sửa được trên **trang "Cấu hình hệ thống"** trong app,
  có hiệu lực trong vài giây, **không cần sửa `.env` hay restart container**.
- Sau khi chuyển, **DB là nguồn duy nhất** cho các khóa này: code không đọc env
  nữa, `.env` prod xoá các khóa đó (quyết định D2).
- Có lịch sử ai đổi khóa nào lúc nào.

**Ngoài phạm vi (cố ý)**
- Khóa hạ tầng/boot (Postgres, Redis, secret key Django, JWT, `ENCRYPTION_KEY`,
  concurrency worker, tên queue, MinIO, Grafana/Prometheus…) — cần trước khi có DB
  hoặc do compose/Go service/Grafana đọc.
- `KIOTVIET_API_MODE` — gate lúc boot ở 5 chỗ trong settings + guard staging; đổi
  mode là việc deploy.
- `FB_SYNC_INTERVAL_SECONDS` và mọi nhịp lịch beat — đã sửa được trong
  `django_celery_beat` (admin).
- 6 khóa throttle rate (`HERMES_THROTTLE_RATE`, `DAILY_REPORT_THROTTLE_RATE`,
  `WARRANTY_PORTAL_{ACTIVATE,READ,MEDIA}_RATE`, …) — nằm trong dict `REST_FRAMEWORK`
  đọc lúc boot; DRF không đọc lại khi chạy.
- Credential Best Express và CNV SSO — **đã có bảng riêng** (`BestExpressAccount`,
  `CnvConnection`); `.env` chỉ là seed cho lệnh `best_sync_account` /
  `cnv_sync_connection`. Xử lý ở §7: xoá khỏi `.env` prod, không đưa vào bảng mới.
- `LARK_ALERT_WEBHOOK_URL` — Django không đọc; service `lark-alert-adapter` đọc.
- Nút "Kiểm tra kết nối" cho từng tích hợp — YAGNI, Lark/Gemini đã có log + ops alert.
- Trang lịch sử thay đổi riêng — chỉ popover lịch sử theo khóa.

---

## 2. Quyết định đã chốt (2026-09-19)

| # | Quyết định |
|---|---|
| D1 | Chuyển **cả hai nhóm**: credential đối tác (B) và tham số nghiệp vụ/cờ (C). |
| D2 | **DB là nguồn duy nhất.** Không có dòng trong DB → dùng `default` của registry trong code; **không** rơi về env. `.env` xoá các khóa đã chuyển. |
| D3 | Nơi sửa: **trang trong app** (frontend), không phải Django admin. |
| D4 | Kiến trúc: **một bảng key-value chung + registry trong code** (không tạo model riêng cho từng tích hợp). |
| D5 | Quyền: section `system-settings` **superuser-only**, cùng khuôn `foxia-link`/`pancake`/`media-settings` (không vào `PERMISSION_CATALOG`). |
| D6 | Secret lưu cột riêng mã hoá Fernet; khóa thường lưu plaintext để còn `psql` kiểm chứng được. |
| D7 | Tests cũ dùng `override_settings(KEY=…)` tiếp tục chạy nhờ `SYSCONFIG_TEST_MODE`; test mới dùng `override_sysconfig`. |

---

## 3. Kiểm kê khóa chuyển sang DB

Registry nhóm thành 8 nhóm = 8 tab trên trang. Cột **Secret** = lưu mã hoá, API không
trả giá trị. Cột **Cấm staging** = `staging_forbidden=True` (§5.4).

| Nhóm (`group`) | Khóa | Kiểu | Secret | Cấm staging |
|---|---|---|---|---|
| `lark_bots` — Lark bot | `LARK_ORDER_BOT_WEBHOOK_URL`, `LARK_SALE_REMINDER_WEBHOOK_URL`, `LARK_VAT_BOT_WEBHOOK_URL`, `LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL`, `LARK_OPS_ALERT_WEBHOOK_URL` | url | — | ✔ |
| | `LARK_ORDER_BOT_SECRET`, `LARK_SALE_REMINDER_SECRET`, `LARK_VAT_BOT_SECRET`, `LARK_CANCEL_DISPATCH_BOT_SECRET`, `LARK_OPS_ALERT_SECRET` | str | ✔ | ✔ |
| | `LARK_ORDER_BOT_TIMEOUT_SECONDS`, `LARK_CANCEL_DISPATCH_BOT_TIMEOUT_SECONDS` | float | — | — |
| | `LARK_ORDER_BOT_MESSAGE` | str | — | — |
| `lark_open` — Lark OpenAPI | `LARK_OPEN_BASE_URL` | url | — | — |
| | `LARK_APP_ID`, `LARK_CHECKIN_REMINDER_APP_ID` | str | — | ✔ |
| | `LARK_APP_SECRET`, `LARK_CHECKIN_REMINDER_APP_SECRET`, `LARK_BITABLE_APP_TOKEN` | str | ✔ | ✔ |
| | `LARK_BITABLE_REVENUE_TABLE_ID`, `LARK_BITABLE_ADS_CHANNEL_TABLE_ID`, `LARK_BITABLE_ADS_PRODUCT_TABLE_ID` | str | — | ✔ |
| `ai` — Gemini / AI | `GEMINI_API_KEY`, `CHAT_CRAWLER_DB_PASSWORD` | str | ✔ | ✔ |
| | `GEMINI_TRANSCRIBE_MODEL`, `GEMINI_EXTRACT_MODEL` | str | — | — |
| | `GEMINI_EXTRACT_ENABLED`, `AI_CHAT_EXTRACT_ENABLED` | bool | — | — |
| | `GEMINI_MAX_RPM`, `GEMINI_MAX_RPD` | int | — | — |
| `call_qa` — Chấm chất lượng cuộc gọi | `QA_SCORING_ENABLED` | bool | — | — |
| | `QA_SCORING_MODEL` | str | — | — |
| | `QA_MIN_DURATION_SECONDS`, `QA_MIN_TRANSCRIPT_CHARS`, `QA_PASS_SCORE` | int | — | — |
| `zenify` — Zenify | `ZENIFY_API_KEY`, `ZENIFY_API_SECRET` | str | ✔ | ✔ |
| | `ZENIFY_DOMAIN` | str | — | — |
| | `ZENIFY_CALL_SYNC_ENABLED` | bool | — | — |
| `integrations` — Foxia / Pancake / CNV | `FOXIA_PUSH_ENABLED`, `PANCAKE_POLL_ENABLED`, `CNV_SYNC_ENABLED` | bool | — | — |
| | `FOXIA_PUSH_BASE_URL`, `CNV_SSO_REDIRECT_URI`, `CNV_SSO_SERVER_URI`, `CNV_API_BASE_URL` | url | — | — |
| | `FOXIA_PUSH_TIMEOUT`, `PANCAKE_HTTP_TIMEOUT` | float | — | — |
| | `CNV_ACCOUNT_NAME`, `CNV_ACCOUNT_ID` | str | — | — |
| `inbound_tokens` — Token API vào | `HERMES_API_TOKEN`, `DAILY_REPORT_API_TOKEN`, `ORDERS_INGEST_API_TOKEN`, `KIOTVIET_WEBHOOK_SECRET`, `BEST_EXPRESS_WEBHOOK_TOKEN`, `WARRANTY_PORTAL_ZALO_TOKEN` | str | ✔ | — |
| `orders_portal` — Đơn hàng / Portal | `ORDERS_LANDING_INGEST_DRY_RUN`, `RECONCILE_DELIVERY_EXACT_MIRROR` | bool | — | — |
| | `SALE_REMINDER_FOLLOWUP_HOUR`, `FB_TOKEN_EXPIRY_WARN_DAYS`, `WARRANTY_PORTAL_MONTHS`, `WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS` | int | — | — |
| | `WARRANTY_QR_BASE_URL`, `PORTAL_COMPANY_ZALO_URL`, `PORTAL_COMPANY_FACEBOOK_URL` | url | — | — |
| | `PORTAL_COMPANY_NAME`, `PORTAL_COMPANY_ADDRESS`, `PORTAL_COMPANY_TAX_CODE`, `PORTAL_COMPANY_HOTLINE` | str | — | — |

Ghi chú:
- `KIOTVIET_WEBHOOK_SECRET_B64` **bỏ khóa riêng** — suy ra từ `KIOTVIET_WEBHOOK_SECRET`
  bằng `base64` tại chỗ dùng (2 chỗ trong `apps/kiotviet`).
- `default` của từng khóa lấy đúng giá trị mặc định hiện tại trong
  `config/settings_parts/base.py` (VD `QA_PASS_SCORE=80`, `GEMINI_MAX_RPM=60`,
  `FOXIA_PUSH_BASE_URL=https://desiglobal.foxia.vn/duongdankhachhang`). Cờ `*_ENABLED`
  mặc định `False`; secret mặc định `""`.
- Token vào (`inbound_tokens`) **không** cấm staging: staging cần token riêng để gọi thử
  API; `staging_neutralize` vẫn xoá để buộc đặt lại (§5.4).

**Khóa chết, xoá hẳn khỏi `.env` + `.env.example`** (không còn chỗ nào đọc):
`SALE_REMINDER_SHIFT_HOURS`, `SALE_REMINDER_NO_ANSWER_STALE_MINUTES`,
`SALE_REMINDER_NO_ANSWER_THROTTLE_MINUTES`, `KIOTVIET_AUTO_OLD_INVOICE_INTERVAL_SECONDS`,
`KIOTVIET_AUTO_INVOICE_SCAN_INTERVAL_SECONDS`.

### Cập nhật khi triển khai (2026-09-21)

Bảng kiểm kê trên viết lúc thiết kế; registry thật (`backend/apps/sysconfig/registry.py`)
có **76 khóa** (không phải 77), lệch ba điểm phát sinh lúc code va vào consumer thật:

- **Bỏ**: `BEST_EXPRESS_WEBHOOK_TOKEN` khỏi nhóm `inbound_tokens` — token này đã có
  sẵn cột `BestExpressAccount.webhook_token`, không cần một bản sao ở sysconfig.
- **Thêm 8 khóa Lark có consumer thật, sót khỏi bảng kiểm kê ban đầu**:
  `LARK_INVENTORY_BOT_WEBHOOK_URL`, `LARK_INVENTORY_BOT_SECRET`,
  `LARK_INVENTORY_BOT_TIMEOUT_SECONDS` (nhóm `lark_bots`); `LARK_SALE_REMINDER_TIMEOUT_SECONDS`
  (`lark_bots`); `LARK_VAT_BOT_TIMEOUT_SECONDS` (`lark_bots`);
  `LARK_CHECKIN_REMINDER_CHAT_ID`, `LARK_CHECKIN_REMINDER_LINK`,
  `LARK_OPEN_API_TIMEOUT_SECONDS` (nhóm `lark_open`).
- **Bốn khóa Best Express (`ENVIRONMENT`/`USERNAME`/`PASSWORD`/`EMS_KEY`) và hai khóa
  `CNV_SSO_CLIENT_ID`/`CNV_SSO_CLIENT_SECRET` không vào registry** — mục "Ngoài phạm
  vi" ở §1 đã nêu hướng này, thực thi bằng cách xoá seed từ `.env` (lệnh
  `best_sync_account` / `cnv_sync_connection` bị xoá) và quản lý trực tiếp trên
  `BestExpressAccount` / `CnvConnection` (Django admin).
- `KIOTVIET_WEBHOOK_SECRET_B64` đúng như ghi chú — không vào registry, suy ra từ
  `KIOTVIET_WEBHOOK_SECRET`.

Chi tiết + hướng dẫn deploy: `docs/system-settings.md`.

---

## 4. Kiến trúc tổng thể

```
frontend  SystemSettingsPage ──GET/PATCH──▶ /api/system-settings/
                                                  │
                                        apps/sysconfig/transport (DRF, superuser)
                                                  │
                                        apps/sysconfig/service.set_many()
                                                  │  transaction: upsert rows + SystemSettingChange
                                                  ▼
                       Postgres system_settings ──▶ Redis  sysconfig:version += 1
                                                  ▲
   web / worker-* / beat   sysconfig.get("KEY") ──┘  snapshot in-process, so version ≤2s/lần
```

App mới **`apps/sysconfig`** — tầng đáy, chỉ import `apps.common` (`fields`,
`cache_backend` không cần — dùng `django.core.cache`). `apps.common` không có model nên
không đặt ở đó. Mọi app khác import `from apps.sysconfig import sysconfig`.

Cấu trúc thư mục theo `docs/architecture-standard.md`:

```
apps/sysconfig/
  __init__.py          # export `sysconfig` (facade get/get_many)
  apps.py
  registry.py          # SETTINGS: tuple[Setting, ...] + GROUPS
  models.py            # SystemSetting, SystemSettingChange
  service.py           # snapshot/cache/version, set_many, override_sysconfig
  admin.py             # read-only (giá trị secret che) — chỉ để tra cứu
  migrations/0001_initial.py
  management/commands/sysconfig_import_env.py
  transport/views.py, transport/serializers.py, transport/urls.py
  tests/
```

---

## 5. Backend

### 5.1 Registry (`registry.py`)

```python
@dataclass(frozen=True)
class Setting:
    key: str                     # trùng tên env cũ, viết hoa
    type: Literal["bool", "int", "float", "str", "url"]
    default: bool | int | float | str
    group: str                   # một trong GROUPS
    label: str                   # nhãn hiển thị (tiếng Việt)
    help: str = ""               # mô tả ngắn hiển thị cạnh ô nhập
    secret: bool = False         # lưu cột mã hoá, API không trả giá trị
    staging_forbidden: bool = False  # §5.4

GROUPS = (("lark_bots", "Lark bot"), ("lark_open", "Lark OpenAPI"), ("ai", "Gemini / AI"),
          ("call_qa", "Chấm chất lượng cuộc gọi"), ("zenify", "Zenify"),
          ("integrations", "Foxia / Pancake / CNV"), ("inbound_tokens", "Token API vào"),
          ("orders_portal", "Đơn hàng / Portal"))
SETTINGS: tuple[Setting, ...] = (...)      # theo §3, thứ tự = thứ tự hiển thị
BY_KEY: dict[str, Setting]
```

- Registry là **whitelist**: khóa không có trong registry → API trả 400, `get()` raise
  `KeyError` (lỗi lập trình, phát hiện ngay trong test).
- Import-time check: key duy nhất, `group` hợp lệ, `default` đúng kiểu, secret phải là
  `str` — sai thì `ImproperlyConfigured` lúc boot.
- Ép kiểu khi đọc: `bool` chấp nhận `1/0/true/false/yes/no` (không phân biệt hoa
  thường), `int`/`float` qua `int()`/`float()`, `url` là `str` đã `strip()` và phải
  bắt đầu bằng `http://`/`https://` hoặc rỗng. Giá trị trong DB không ép được kiểu →
  log `warning` + trả `default` (không làm sập request/task).

### 5.2 Model (`models.py`)

```python
class SystemSetting(models.Model):
    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True, default="")          # khóa thường
    secret_value = EncryptedCharField(max_length=4096, blank=True, default="")  # khóa secret
    updated_by = FK(AUTH_USER_MODEL, SET_NULL, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: db_table = "system_settings"

class SystemSettingChange(models.Model):
    key = models.CharField(max_length=64, db_index=True)
    old_value = models.TextField(blank=True, default="")      # secret → "***" hoặc "" nếu chưa đặt
    new_value = models.TextField(blank=True, default="")      # secret → "***" hoặc "" nếu xoá
    changed_by = FK(AUTH_USER_MODEL, SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    class Meta: db_table = "system_setting_changes"; ordering = ("-changed_at",)
```

- Một khóa chỉ dùng **một** trong hai cột theo `Setting.secret`; cột kia luôn rỗng —
  service đảm bảo, không để caller chọn.
- "Xoá" secret = xoá dòng (không lưu chuỗi rỗng mã hoá) → `is_set=False`.

### 5.3 Đọc (`service.py`)

```python
class _Store:
    _snapshot: dict[str, str]     # key → raw string (đã decrypt với secret)
    _version: int                 # version lúc nạp snapshot
    _checked_at: float            # monotonic lần so version gần nhất

    def get(self, key) -> value:
        spec = BY_KEY[key]                     # KeyError nếu không có
        if SYSCONFIG_TEST_MODE and hasattr(django_settings, key):   # D7
            return coerce(spec, getattr(django_settings, key))
        if spec.staging_forbidden and IS_STAGING:                    # §5.4
            return spec.default
        self._maybe_refresh()
        raw = self._snapshot.get(key)
        return spec.default if raw is None else coerce(spec, raw)
```

- `_maybe_refresh()`: nếu `monotonic() - _checked_at < 2s` → dùng snapshot. Ngược lại
  `GET sysconfig:version` từ Redis (`django.core.cache`): khác `_version` (hoặc snapshot
  chưa nạp) → `SELECT` toàn bảng, decrypt secret, thay snapshot nguyên khối. Redis lỗi
  (`cache.get` raise/None do fail-soft của `InstrumentedRedisCache`) → giữ snapshot cũ;
  nếu chưa có snapshot nào thì query DB thẳng. `_checked_at` cập nhật ở mọi nhánh để
  không dội Redis khi Redis đang chết.
- Thread-safe bằng `threading.Lock` quanh nạp lại (web chạy 4 thread); worker
  prefork mỗi child là process riêng, tự có snapshot.
- Celery `worker_process_init` / gunicorn `post_fork`: không cần hook — snapshot nạp
  lazy ở lần `get()` đầu.
- `get_many(keys) -> dict` cho chỗ cần nhiều khóa cùng lúc (VD gửi Lark: URL + secret +
  timeout) — một lần `_maybe_refresh()`.
- Facade: `sysconfig = _Store()`; `apps/sysconfig/__init__.py` export `sysconfig`.

### 5.4 Ghi & staging

```python
def set_many(values: dict[str, str | None], *, user) -> None:
    # validate: key trong registry, ép kiểu thử (ValueError → ValidationError),
    # None với secret = xoá dòng, None với khóa thường = về default (xoá dòng)
    with transaction.atomic():
        for key, raw in values.items():
            old = ...; upsert/delete SystemSetting; SystemSettingChange(...)
        transaction.on_commit(_bump_version)   # cache.incr("sysconfig:version") (set 1 nếu chưa có)
```

- `_bump_version` chạy **sau commit** để process khác không nạp snapshot trước khi
  dữ liệu thấy được. Redis lỗi lúc bump → log `error`; snapshot process khác sẽ lệch
  tới lần Redis sống lại + có ghi mới. Đây là hạn chế chấp nhận (Redis chết thì cả
  Celery đã chết).
- **Staging** (`IS_STAGING`):
  - `get()` trả `default` cho mọi khóa `staging_forbidden` bất kể DB — lớp fail-safe
    tại điểm đọc, không phụ thuộc lệnh dọn có chạy hay không.
  - `staging_neutralize` (đã có ở `apps/users/management/commands/`) thêm bước: xoá
    mọi dòng `system_settings` có `staging_forbidden=True` **và** mọi dòng thuộc nhóm
    `inbound_tokens` (buộc đặt token riêng cho staging), ghi `SystemSettingChange` với
    `changed_by=None`.
  - Guard boot `STAGING_FORBIDDEN_OUTBOUND_ENVS` + prefix `LARK_` trong `base.py` giữ
    nguyên: nó vẫn bắt được `.env` staging lỡ mang khóa cũ (phòng người vận hành copy
    `.env` prod). Tuy nhiên bỏ các tên đã chuyển khỏi tuple tường minh
    (`GEMINI_API_KEY`, `ZENIFY_*`, `CHAT_CRAWLER_DB_PASSWORD`) — thay bằng **guard
    registry**: lúc boot, env còn mang khóa nào có trong registry → `ImproperlyConfigured`
    ở mọi môi trường, để không ai tưởng `.env` còn tác dụng. Cửa thoát tạm cho cửa sổ
    chuyển đổi (§7): `SYSCONFIG_ALLOW_ENV_KEYS=1` hạ guard thành `warning`; biến này
    phải bị gỡ ở §7 bước 5.

### 5.5 Thay chỗ gọi

- Xoá ~55 dòng gán trong `config/settings_parts/base.py` (và comment kèm — chuyển
  nội dung comment hữu ích sang `help` của registry).
- ~100 chỗ `settings.X` trong `apps/*` → `sysconfig.get("X")`. Quy tắc:
  - Đọc **tại thời điểm dùng**, không cache ở module-level (mất tác dụng "có hiệu lực
    ngay"). Rà `grep -n "^[A-Z_]* = settings\."` để bắt hằng module-level.
  - Hàm gửi Lark (`apps/common/lark_open_api.py`, các notifier bot) nhận URL/secret qua
    tham số như hiện tại; chỉ chỗ gọi đổi nguồn.
  - `KIOTVIET_WEBHOOK_SECRET_B64` → `base64.b64encode(sysconfig.get("KIOTVIET_WEBHOOK_SECRET").encode()).decode()`.
  - Cờ `*_ENABLED` được task tự kiểm tra lúc chạy (đã vậy, comment trong
    `settings_parts/celery.py`) — **không** đụng `CELERY_BEAT_SCHEDULE`.
- `SYSCONFIG_TEST_MODE` đặt `True` trong cấu hình test (`pytest.ini`/settings test),
  `False` mọi nơi khác. 155 chỗ `override_settings(GEMINI_API_KEY=…)` trong 52 file
  test tiếp tục chạy; test mới trong `apps/sysconfig/tests` dùng
  `override_sysconfig(**values)` (context manager + decorator, patch thẳng snapshot,
  không chạm Redis/DB). Dọn 155 chỗ cũ là việc sau, không thuộc spec này.

### 5.6 Lệnh `sysconfig_import_env`

```
manage.py sysconfig_import_env            # in bảng: key | env có? | DB có? | hành động
manage.py sysconfig_import_env --apply    # ghi
```

- Với mỗi khóa registry: env rỗng/không có → bỏ qua; env có, DB chưa có → tạo; cả hai
  có và khác nhau → **không ghi đè** (in cảnh báo), trừ khi `--force`. Secret in `***`.
- Ghi qua `set_many(user=None)` để có lịch sử + bump version.
- Khóa Best Express / CNV / khóa chết không thuộc registry → lệnh không đụng.

---

## 6. API & Frontend

### 6.1 Quyền

- Thêm `"system-settings"` vào `VALID_SECTIONS` (`apps/users/models.py`) với comment
  cùng khuôn `pancake` — superuser-only, **cố ý không** vào `PERMISSION_CATALOG`.
- View gate `IsAuthenticated` + `IsSuperUser` (`user.is_superuser`) — không dùng
  `require_section` vì section không có trong catalog (khớp `pancake` views).

### 6.2 Endpoint (`/api/system-settings/`)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/system-settings/` | `{groups: [{key, label, settings: [SettingOut]}]}` theo thứ tự registry |
| PATCH | `/api/system-settings/` | body `{values: {KEY: string \| null}}` → 200 trả lại GET; 400 `{errors: {KEY: msg}}` |
| GET | `/api/system-settings/history/?key=KEY&limit=50` | `[{key, old_value, new_value, changed_by: {id, name}, changed_at}]` |

`SettingOut`:
```json
{"key": "GEMINI_API_KEY", "label": "Gemini API key", "help": "...", "type": "str",
 "is_secret": true, "is_set": true, "value": null, "hint": "…rUfZ",
 "default": "", "updated_at": "...", "updated_by": {"id": 1, "name": "Hung Tran"}}
```
- Khóa thường: `value` là chuỗi raw (FE tự render theo `type`), `hint` null.
- Khóa secret: `value` luôn `null`; `hint` = 4 ký tự cuối khi `is_set`, null khi chưa
  đặt. Không có endpoint nào trả secret ra ngoài.
- PATCH: khóa secret gửi chuỗi = đặt mới, `null` = xoá, không gửi = giữ; khóa thường
  `null` = về default. Ghi được nhiều khóa một lần (một tab = một lần Lưu). Validate
  từng khóa, lỗi bất kỳ → không ghi khóa nào (transaction).
- Audit: PATCH đi qua middleware audit chung như mọi endpoint ghi; body có secret → thêm
  path vào danh sách mask của `apps/common/masking.py` để `audit_logs.curl_command`
  không lưu plaintext.

### 6.3 Frontend `features/system-settings/`

- `modules.ts`: thêm section `{ key: "system-settings", labelKey: "topnav.tabs.systemSettings", icon: SlidersHorizontal }` vào module `settings`; `nav-layout.ts` zone `settings` thêm `settings: ["system-settings"]` để nó nằm trong dropdown "Cài đặt" của zone. `routes.ts` thêm `AppSection`. i18n vi/en.
- `queries.ts`: `useSystemSettings()`, `useUpdateSystemSettings()`, `useSettingHistory(key)` (react-query, invalidate sau PATCH).
- `pages/SystemSettingsPage.tsx`:
  - Layout 2 cột: **tab dọc trái** = 8 nhóm; phải = form của nhóm đang chọn.
  - Mỗi khóa một hàng: nhãn + control theo `type` (`bool` → Switch; `int`/`float` →
    Input number; `str` → Input; `url` → Input kèm validate `http(s)://`; secret →
    Input password trống, bên cạnh badge "Đã đặt (…rUfZ)" hoặc "Chưa đặt", nút
    **Xoá** khi đã đặt). Dưới control: `help` + "Sửa lần cuối: tên, thời gian" + nút
    "Lịch sử" mở popover 50 dòng gần nhất.
  - Nút **Lưu** ở cuối tab, disabled khi form chưa đổi; chỉ gửi khóa đã đổi; secret
    chỉ gửi khi người dùng gõ hoặc bấm Xoá. Lưu xong toast + refetch. Đổi tab khi còn
    thay đổi chưa lưu → hỏi xác nhận.
  - Lỗi 400 hiển thị dưới đúng ô.
- Tests (vitest, chạy suite riêng): render đủ nhóm; đổi giá trị bật nút Lưu và PATCH
  đúng payload (chỉ khóa đã đổi); secret không hiện giá trị, Xoá gửi `null`; lỗi 400
  gắn vào ô.

---

## 7. Chuyển dữ liệu & deploy prod

Thứ tự bắt buộc — tránh khoảng trống "container mới lên, DB chưa có dòng" (khi đó
Gemini/Lark tắt, token API vào rỗng → Hermes/Daily Report 503 fail-closed):

0. Thêm `SYSCONFIG_ALLOW_ENV_KEYS=1` vào `.env` (hạ guard registry thành warning trong
   cửa sổ chuyển — §5.4).
1. Pull image mới, chạy `migrate` (`sysconfig.0001`) — service migrate nối thẳng
   Postgres như thường (không qua pgbouncer).
2. `docker compose run --rm web python manage.py sysconfig_import_env` → xem diff;
   rồi `--apply`. Lúc này `.env` vẫn còn đủ khóa.
3. Recreate `web`, `worker*`, `beat`.
4. Kiểm tra: mở Cấu hình hệ thống, 8 tab có giá trị; tạo thử 1 đơn → Lark báo; gọi
   Hermes / Daily Report bằng token cũ → 200; `docker logs` không có `KeyError`/
   `warning` ép kiểu của sysconfig.
5. Backup `.env` → `.env.bak-<ngày>`; xoá khỏi `.env`: 55 khóa đã chuyển, 5 khóa chết,
   `BEST_EXPRESS_{USERNAME,PASSWORD,EMS_KEY,ENVIRONMENT,WEBHOOK_TOKEN}`,
   `CNV_SSO_CLIENT_{ID,SECRET}`, `KIOTVIET_WEBHOOK_SECRET_B64`, và gỡ
   `SYSCONFIG_ALLOW_ENV_KEYS`.
6. Recreate lần nữa để container không còn cầm env cũ. Không còn cửa thoát, guard
   registry raise `ImproperlyConfigured` nếu bước 5 dọn sót — đây là chủ ý.
7. Xoá 2 lệnh seed `best_sync_account`, `cnv_sync_connection` và các dòng
   `BEST_EXPRESS_*`/`CNV_SSO_CLIENT_*` trong `base.py` (giá trị đã nằm trong
   `BestExpressAccount`/`CnvConnection`); `CNV_SSO_CLIENT_SECRET`,
   `BEST_EXPRESS_USERNAME/PASSWORD` rời `STAGING_FORBIDDEN_OUTBOUND_ENVS` vì không
   còn đọc env — bảng của chúng đã do `staging_neutralize` dọn.

Rollback: image cũ + `.env.bak-<ngày>`; bảng `system_settings` nằm yên, không cản gì.

**Staging:** `scripts/staging-refresh.sh` không đổi (đã gọi `staging_neutralize`);
`.env.staging.example` bỏ các khóa đã chuyển; sau refresh, superuser vào trang Cấu hình
hệ thống đặt token vào riêng cho staging nếu cần.

**Tài liệu:**
- `docs/system-settings.md` mới: cách thêm khóa (1 dòng registry, không migration), cơ chế
  snapshot/version, quy trình chuyển khóa env → DB, ràng buộc staging.
- `.env.example`: xoá các khóa đã chuyển, thay bằng 1 khối ghi chú "đã chuyển vào DB —
  Cài đặt → Cấu hình hệ thống, xem docs/system-settings.md".
- `docs/staging-environment.md`: thêm dòng bảng `system_settings` vào mục neutralize.

---

## 8. Kiểm thử

**Backend** (`apps/sysconfig/tests`, pytest với container test pg:5433 / redis:6380):
- Registry: key trùng / group lạ / default sai kiểu → `ImproperlyConfigured`; mọi khóa
  §3 có mặt và đúng cờ `secret`/`staging_forbidden`.
- `get()`: không có dòng → default; ép kiểu bool/int/float/url; giá trị hỏng → default +
  warning; khóa lạ → `KeyError`.
- Snapshot/version: `set_many` → version tăng sau commit; store thứ hai (giả lập process
  khác) đọc được giá trị mới sau khi hết cửa sổ 2s (patch `monotonic`); Redis raise →
  giữ snapshot cũ, không raise.
- Secret: lưu vào `secret_value` đã mã hoá (đọc thẳng cột ≠ plaintext); `value` rỗng;
  xoá = xoá dòng.
- Staging: `IS_STAGING=True` + DB có `GEMINI_API_KEY` → `get()` trả `""`;
  `staging_neutralize` xoá đúng tập dòng và ghi lịch sử.
- API: non-superuser → 403; GET không chứa secret (assert `value is None`, `hint` 4 ký
  tự); PATCH nhiều khóa, `null`, khóa lạ → 400 và không ghi gì; history theo key.
- Lệnh import: dry-run không ghi; `--apply` tạo dòng; có sẵn khác giá trị → không ghi
  đè nếu thiếu `--force`.
- Guard registry: env còn khóa registry → `ImproperlyConfigured`; có
  `SYSCONFIG_ALLOW_ENV_KEYS=1` → chỉ warning.
- `override_sysconfig` + `SYSCONFIG_TEST_MODE`: cả hai đường override hoạt động.
- Toàn bộ suite cũ pass (đặc biệt 52 file dùng `override_settings` các khóa này).

**Frontend** (vitest, chạy suite riêng vì timeout 5s sát nút): như §6.3.

---

## 9. Rủi ro & cách xử lý

| Rủi ro | Xử lý |
|---|---|
| Khoảng trống giá trị khi deploy | Thứ tự §7: import trước recreate; guard boot fail-fast nếu `.env` chưa dọn. |
| Chỗ gọi cache giá trị ở module-level → đổi trên UI không có tác dụng | Rà `grep` khi thay chỗ gọi; test "đổi rồi đọc lại" cho các notifier Lark và cờ `*_ENABLED`. |
| Secret lọt vào `audit_logs.curl_command` | Thêm path vào mask list `apps/common/masking.py`, test khẳng định body đã mask. |
| Staging snapshot mang secret prod | Ba lớp: `get()` chặn theo `staging_forbidden`, `staging_neutralize` xoá, `ENCRYPTION_KEY` khác → decrypt ra rác. |
| Redis chết → process không thấy giá trị mới | Fail-soft dùng snapshot cũ; ghi log error khi bump thất bại. Redis chết thì Celery đã chết, mức độ chấp nhận được. |
| Người vận hành sửa `.env` theo thói quen cũ | Boot fail-fast khi env còn khóa registry; `.env.example` ghi rõ. |
