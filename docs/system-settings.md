# Cấu hình hệ thống trong DB (apps.sysconfig)

Spec: `docs/superpowers/specs/2026-09-19-system-settings-db-design.md`.

## Đọc trong code
```python
from apps.sysconfig import sysconfig
if not sysconfig.get("FOXIA_PUSH_ENABLED"): ...
```
- Đọc **tại lúc dùng**, không cache ở module-level (mất tác dụng "đổi là có hiệu lực").
- `apps.common` không import sysconfig — dùng `apps.common.runtime_config.get(KEY)`.
- Cơ chế: snapshot toàn bảng trong process; mỗi ≥2s so `sysconfig:version` trên Redis,
  lệch thì nạp lại. Redis lỗi → giữ snapshot cũ. Ghi qua `set_many()` tăng version sau commit.

## Registry hiện có 76 khóa, 8 nhóm

Nguồn chân lý: `backend/apps/sysconfig/registry.py` (`SETTINGS`). Khác với bảng kiểm kê
ở spec §3 (viết lúc thiết kế, trước khi code va vào các consumer thật):

- **Bỏ khỏi registry**: `BEST_EXPRESS_WEBHOOK_TOKEN` — token này xác thực webhook Best
  Express, nhưng đã có sẵn cột `BestExpressAccount.webhook_token` trong DB nên không cần
  một bản sao thứ hai ở sysconfig; sinh/xem token qua account đó.
- **Thêm vào registry (8 khóa Lark có consumer thật, không có trong bản kiểm kê ban đầu)**:
  `LARK_INVENTORY_BOT_WEBHOOK_URL`, `LARK_INVENTORY_BOT_SECRET`,
  `LARK_INVENTORY_BOT_TIMEOUT_SECONDS`, `LARK_SALE_REMINDER_TIMEOUT_SECONDS`,
  `LARK_VAT_BOT_TIMEOUT_SECONDS`, `LARK_CHECKIN_REMINDER_CHAT_ID`,
  `LARK_CHECKIN_REMINDER_LINK`, `LARK_OPEN_API_TIMEOUT_SECONDS`.
- **Không vào registry, đã chuyển sang bảng riêng (không phải sysconfig)**: bốn khóa
  `BEST_EXPRESS_{ENVIRONMENT,USERNAME,PASSWORD,EMS_KEY}` và `CNV_SSO_CLIENT_ID/SECRET`.
  Tài khoản Best Express nằm trên `BestExpressAccount` (Django admin); client CNV nằm
  trên `CnvConnection` (Django admin + `GET /api/cnv/connect` để nối SSO). Lệnh
  `best_sync_account` / `cnv_sync_connection` (seed từ `.env` vào các bảng đó) đã bị xoá —
  không còn `.env` để seed từ nữa.
- **Không vào registry, khóa chết**: `KIOTVIET_WEBHOOK_SECRET_B64` — suy ra bằng
  `base64` từ `KIOTVIET_WEBHOOK_SECRET` ngay tại chỗ dùng, không lưu riêng.

## Thêm cài đặt mới
Thêm một `Setting(...)` vào `SETTINGS` trong `apps/sysconfig/registry.py` (key viết hoa,
kiểu, default đúng kiểu, group, label, help, `secret=True` nếu là credential,
`staging_forbidden=True` nếu cầm nó là ghi được lên hệ thống thật). Không migration.
Registry được kiểm tra lúc boot. KHÔNG thêm `os.getenv` cho khóa vận hành nữa.

## Boot guard — env còn khóa registry là fail-fast
`assert_no_registry_env_leftovers` trong `config/settings_parts/base.py` quét toàn bộ
env lúc nạp settings: nếu bất kỳ khóa nào trong registry vẫn mang giá trị non-empty ở
env, boot raise `ImproperlyConfigured` (mọi môi trường, không chỉ staging/production) —
ai đó tưởng `.env` còn tác dụng sẽ chết ngay, không chạy âm thầm với giá trị sai nguồn.
`SYSCONFIG_ALLOW_ENV_KEYS=1` hạ guard này thành `warnings.warn` — CHỈ dùng trong cửa sổ
deploy chuyển đổi (bước 0–5 dưới), phải gỡ lại sau khi xoá `.env`.

## Test
Toàn suite chạy ở test mode (conftest): `override_sysconfig(KEY=...)` (ưu tiên) hoặc
`override_settings(KEY=...)` (tương thích cũ); không có → default. Test cần bảng thật:
`settings.SYSCONFIG_TEST_MODE = False; sysconfig.reset()`.

## Staging
`sysconfig.get()` trả default cho khóa `staging_forbidden` khi `APP_ENV=staging` bất kể DB.
Ngoài credential outbound, 7 cờ bật tích hợp gọi ra ngoài cũng `staging_forbidden`
(`FOXIA_PUSH_ENABLED`, `PANCAKE_POLL_ENABLED`, `CNV_SYNC_ENABLED`, `ZENIFY_CALL_SYNC_ENABLED`,
`QA_SCORING_ENABLED`, `GEMINI_EXTRACT_ENABLED`, `AI_CHAT_EXTRACT_ENABLED`) → staging luôn đọc
`False`; `FoxiaOrderPushService.push` còn chặn thêm theo `settings.IS_STAGING`.
`manage.py staging_neutralize --yes` xoá các dòng đó + nhóm `inbound_tokens` (xem
`docs/staging-environment.md`).

## Deploy prod lần đầu (chuyển từ .env)

Từ MR này compose (`docker-compose.prod.yml` / `staging` / `local`, anchor `x-backend-env`)
**không còn inject bất kỳ khóa registry nào** vào container (test đối chứng
`apps/sysconfig/tests/test_compose_no_registry_keys.py` đọc thẳng 3 file YAML). Hệ quả
cho lần deploy chuyển đổi: container mới KHÔNG tự thấy giá trị trong `.env` của host, nên
bước import phải nạp `.env` vào container một cách tường minh (bước 2).

0. Thêm `SYSCONFIG_ALLOW_ENV_KEYS=1` vào `.env` — compose giờ passthrough biến này, guard
   hạ thành warning trong cửa sổ chuyển đổi (chỉ có tác dụng ở bước 2, vì từ bước 3 container
   không còn thấy khóa registry nữa).
1. Pull image mới, chạy `migrate` (sysconfig.0001) bằng cách thường lệ.
2. Import từ `.env` của host — chạy TRONG container, nạp `.env` bằng `--env-from-file`
   (compose v5.5 hỗ trợ; đây là cách duy nhất để giá trị registry tới được lệnh vì anchor
   không còn passthrough):
   ```bash
   cd /home/desi/desi-order
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     run --rm --env-from-file .env web python manage.py sysconfig_import_env          # dry-run, xem diff
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     run --rm --env-from-file .env web python manage.py sysconfig_import_env --apply
   ```
   Lệnh in đủ giá trị khóa thường (secret che `***`); dòng `LỖI: …` = giá trị env không
   ép kiểu được (vd bool `maybe`) → khóa đó bị bỏ qua, `--apply` vẫn ghi các khóa hợp lệ
   rồi thoát mã 1 để không bỏ sót — sửa `.env` và chạy lại cho khóa đó.
   **Lưu ý 5 khóa trước nay có trong `.env` prod nhưng compose cũ KHÔNG passthrough**
   (container chưa từng thấy, code luôn dùng default): `LARK_VAT_BOT_WEBHOOK_URL`,
   `LARK_VAT_BOT_SECRET`, `LARK_ORDER_BOT_TIMEOUT_SECONDS`, `ORDERS_LANDING_INGEST_DRY_RUN`,
   `SALE_REMINDER_FOLLOWUP_HOUR`. Bước này SẼ import chúng từ `.env` → sau cutover chúng
   có hiệu lực thật lần đầu. Đặc biệt bot VAT trước nay thực tế chưa bao giờ chạy trên prod
   (webhook không tới container); sau import nó sẽ bắt đầu bắn. Kiểm tra 5 khóa này trên
   trang Cấu hình hệ thống ngay sau bước 4 và tắt/sửa nếu không muốn (giá trị `.env` ngày
   21/09: `ORDERS_LANDING_INGEST_DRY_RUN=0`, `SALE_REMINDER_FOLLOWUP_HOUR=9`,
   `LARK_ORDER_BOT_TIMEOUT_SECONDS=5` — vô hại; VAT webhook + secret có giá trị).
3. Recreate web / worker* / beat (`up -d`) với compose mới.
4. Kiểm tra: trang Cài đặt → Cấu hình hệ thống (8 nhóm, secret hiện `…xxxx`); tạo thử
   một đơn → bot Lark báo đơn; gọi Hermes / Daily Report bằng token cũ (200, không 503);
   soát 5 khóa ở lưu ý bước 2.
5. Backup `.env` → `.env.bak-<ngày>`, rồi xoá khỏi `.env`:
   - 76 khóa registry (`backend/apps/sysconfig/registry.py`);
   - khóa đã chuyển sang bảng riêng: `BEST_EXPRESS_{ENVIRONMENT,USERNAME,PASSWORD,EMS_KEY}`,
     `CNV_SSO_CLIENT_ID/SECRET`; khóa chết `KIOTVIET_WEBHOOK_SECRET_B64`;
   - 5 khóa chết CHỈ từng tồn tại trong `.env` prod (chưa bao giờ có trong `.env.example`,
     code không đọc): `SALE_REMINDER_SHIFT_HOURS`, `SALE_REMINDER_NO_ANSWER_STALE_MINUTES`,
     `SALE_REMINDER_NO_ANSWER_THROTTLE_MINUTES`, `KIOTVIET_AUTO_OLD_INVOICE_INTERVAL_SECONDS`,
     `KIOTVIET_AUTO_INVOICE_SCAN_INTERVAL_SECONDS`;
   - `SYSCONFIG_ALLOW_ENV_KEYS`.
6. Recreate lần nữa. Guard fail-fast nếu `.env` còn khóa registry mà ai đó lại passthrough
   (cố ý); với compose mới thì `.env` còn sót khóa cũng vô hại — nhưng dọn để không ai
   tưởng `.env` còn tác dụng.

### Rollback
Image cũ + `.env.bak-<ngày>` chép lại thành `.env` + **compose của image cũ** (checkout
`docker-compose.prod.yml` ở commit cũ — compose mới không passthrough khóa registry nên
image cũ chạy với compose mới sẽ mất toàn bộ cấu hình Lark/Gemini/token). Bảng
`system_settings` để nguyên, image cũ không đọc. **Mọi giá trị đã đổi trên UI sau cutover
sẽ mất khi rollback** — image cũ chỉ biết `.env.bak`; muốn giữ thì chép tay giá trị từ
trang Cấu hình hệ thống (hoặc lịch sử thay đổi) sang `.env` trước khi rollback.
