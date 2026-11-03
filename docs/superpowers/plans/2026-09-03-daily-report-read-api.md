# Daily Report Read API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở API REST chỉ đọc `/api/daily-report/` (token tĩnh riêng) cho trợ lý Daily Report kéo số lead / đơn / doanh thu / pipeline theo ngày, kèm file OpenAPI tĩnh không chứa bí mật.

**Architecture:** App Django mới `apps/daily_report` (tầng trên cùng) đọc mart `ai.don_hang` bằng SQL tham số hoá, gom nhóm theo đúng hằng số bucket của `apps/orders/reporting/shared.py`, bọc cache Redis theo mốc dữ liệu mart. Cửa xác thực chép pattern Hermes (`AllowAny` + token env + `compare_digest`, fail-closed prod). Mart được bổ sung 2 cột (`chi_nhanh_id`, `nhom_kenh_ban`) và luật loại đơn gốc đã khôi phục để số khớp Báo cáo tổng.

**Tech Stack:** Django 6 + DRF 3.17 + drf-spectacular 0.29, Postgres (schema `ai` do `apps/ai_mart` nạp), Redis cache, import-linter.

**Spec:** `docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md`

## Global Constraints

- Tiền tố URL: `/api/daily-report/` (codebase không có `/api/v1`). Đăng ký trong `config/urls.py` **trước** catch-all `apps.audit.urls`.
- Token env: `DAILY_REPORT_API_TOKEN`; header `X-Api-Token` hoặc `Authorization: Bearer`; prod thiếu token → `503` mọi endpoint; JWT nhân viên CRM **không** mở được.
- Throttle scope `daily_report`, env `DAILY_REPORT_THROTTLE_RATE`, mặc định `300/hour`, key theo IP client.
- Múi giờ nghiệp vụ `Asia/Ho_Chi_Minh` (`apps/analytics/sql/tz.py:BUSINESS_TZ`). Tham số ngày chỉ nhận `YYYY-MM-DD`, bao gồm cả hai đầu, mặc định = hôm qua giờ VN, tối đa **92 ngày**.
- Tiền trả về là **số nguyên VND**; tỷ lệ là số thực 4 chữ số thập phân, `null` khi mẫu số 0.
- Mọi response có envelope `{"meta": {...}, "data": {...}}`; `meta.data_as_of` + `meta.data_status` đọc từ `ai.tinh_trang_du_lieu` (dòng `ai.don_hang`).
- Layering (`backend/.importlinter`): thêm tầng `daily_report` trên `ai_mart`. App chỉ import xuống: `common`, `analytics.sql.tz`, `orders.reporting.shared`, `orders.models`, `orders.crm_catalog`, `ai_mart` (helpers test, etl_service).
- Không `POST`/`PUT`; không trả SĐT, địa chỉ, transcript.
- Làm việc trong **worktree riêng** (`/home/desi/desi-order-daily-report`, nhánh `feat/daily-report-api`) — working tree chính dùng chung giữa nhiều phiên.
- **Chạy test bằng container test, KHÔNG dùng `.env` prod.** Mọi lệnh `manage.py test` trong plan viết tắt là `TESTENV manage.py test ...`; thay `TESTENV` bằng đúng khối sau (chạy từ `<worktree>/backend`):

```bash
POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= LOG_FORMAT=text \
APP_ENV=development DJANGO_DEBUG=0 \
ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
/home/desi/desi-order/.venv/bin/python
```

  Ví dụ: `TESTENV manage.py test apps.daily_report -v2` nghĩa là chạy khối trên rồi nối `manage.py test apps.daily_report --noinput -v2` vào cuối (luôn thêm `--noinput`: test DB cũ còn sót sẽ hỏi xác nhận và treo). Lệnh `manage.py spectacular` và `lint-imports` cũng dùng cùng khối env.

- Commit message tiếng Anh, prefix `feat(daily_report):` / `feat(ai_mart):` / `test(...)` / `docs(...)`, kết bằng trailer:

```
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NBp6xpEYsyrei54ZUrhP5E
```

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/ai_mart/migrations/0013_don_hang_chi_nhanh_nhom_kenh.py` | Thêm 2 cột + COMMENT vào `ai.don_hang` |
| `backend/apps/ai_mart/infrastructure/sql/don_hang.py` | Transform: 2 cột mới, loại đơn gốc có bản khôi phục sống, candidate thêm `restored_from_id` |
| `backend/apps/daily_report/apps.py`, `__init__.py` | AppConfig |
| `backend/apps/daily_report/domain/periods.py` | Parse `from`/`to`/`date`/`group_by`/int; ngày hôm qua giờ VN; `ParamError` |
| `backend/apps/daily_report/domain/definitions.py` | Bucket trạng thái sale/đơn, chi nhánh loại trừ, glossary — **ghép** từ `orders.reporting.shared`, không chép mã |
| `backend/apps/daily_report/infrastructure/freshness.py` | Đọc `ai.tinh_trang_du_lieu` → `Freshness` |
| `backend/apps/daily_report/infrastructure/queries.py` | SQL trên `ai.don_hang` / `ai.tu_dien_ma`, bọc `statement_timeout` |
| `backend/apps/daily_report/application/stats_service.py` | Payload từng endpoint: envelope, cache, tính tỷ lệ, ghép nhóm |
| `backend/apps/daily_report/transport/auth.py` | `extract_api_token`, `require_api_token`, `daily_report_view` |
| `backend/apps/daily_report/transport/serializers.py` | Serializer **chỉ để mô tả schema** |
| `backend/apps/daily_report/transport/views.py` | 7 view GET |
| `backend/apps/daily_report/urls.py` | Route + tên URL `daily-report-*` |
| `backend/apps/daily_report/openapi.py` | `SPECTACULAR_SETTINGS` riêng cho file OpenAPI tĩnh |
| `backend/apps/daily_report/tests/helpers.py` | `client()`, `seed_order()`, `rebuild()` |
| `backend/apps/daily_report/tests/test_*.py` | auth, periods, definitions, leads, orders_revenue, pipeline, summary, definitions_api, health, cache, openapi |
| `backend/apps/common/throttling.py` | Thêm `DailyReportApiThrottle` cạnh `HermesApiThrottle` |
| `backend/apps/common/health.py` | Đổi `_check_db`/`_check_redis` thành public `check_db`/`check_redis` |
| `backend/config/settings_parts/base.py` | INSTALLED_APPS, `DAILY_REPORT_API_TOKEN`, rate `daily_report`, `PREPROCESSING_HOOKS` |
| `backend/config/openapi_hooks.py` | Hook loại `/api/daily-report/*` khỏi schema chính |
| `backend/config/urls.py`, `backend/.importlinter` | Đăng ký route + tầng |
| `docs/openapi/daily-report.yaml` | File OpenAPI tĩnh sinh ra, commit |
| `docs/daily-report-api.md` | Hợp đồng đọc cho người |
| `.env.example`, `docker-compose.{local,staging,prod}.yml`, `nginx/default.conf`, `nginx/conf.d/default.conf` | Env + allowlist để sẵn dạng comment |

---

### Task 0: Worktree

- [ ] **Step 1: Tạo worktree và nhánh**

```bash
cd /home/desi/desi-order
git worktree add /home/desi/desi-order-daily-report -b feat/daily-report-api master
cd /home/desi/desi-order-daily-report/backend
```

- [ ] **Step 2: Xác nhận test harness chạy được**

Run: `TESTENV manage.py test apps.ai_mart.tests.test_freshness -v2`
Expected: `OK` (2 test).

---

### Task 1: Mart `ai.don_hang` — thêm `chi_nhanh_id`, `nhom_kenh_ban`, loại đơn gốc đã khôi phục

**Files:**
- Create: `backend/apps/ai_mart/migrations/0013_don_hang_chi_nhanh_nhom_kenh.py`
- Modify: `backend/apps/ai_mart/infrastructure/sql/don_hang.py`
- Test: `backend/apps/ai_mart/tests/test_don_hang.py`

**Interfaces:**
- Produces: cột `ai.don_hang.chi_nhanh_id text` (NULL nếu rỗng), `ai.don_hang.nhom_kenh_ban text` ∈ {`shopee`,`tiktok`,`facebook`,`web`,`off_other`}; đơn có bản khôi phục còn sống **không** còn trong mart.

- [ ] **Step 1: Viết test đỏ**

Thêm vào cuối class `DonHangTransformTests` trong `backend/apps/ai_mart/tests/test_don_hang.py`:

```python
    def test_chi_nhanh_and_nhom_kenh_ban(self):
        """Nhóm kênh bán chép luật Báo cáo tổng: sàn xét TRƯỚC kênh."""
        fb = Channel.objects.create(code="facebook-page-x", name="FB X")
        web = Channel.objects.create(code="google-ads-web", name="Web")
        o_fb = mk_order(status=0, channel=fb, branch_id="231174")
        o_shopee_on_fb = mk_order(status=0, channel=fb, invoice_code="HDSPE_000001")
        o_tiktok = mk_order(status=0, order_source="tiktok")
        o_web = mk_order(status=0, channel=web)
        o_other = mk_order(status=0)  # kênh test-ch-N, không nguồn, không mã HĐ
        loaders.full_rebuild(MART)
        got = {}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT don_id, chi_nhanh_id, nhom_kenh_ban FROM ai.don_hang WHERE don_id = ANY(%s)",
                [[o_fb.id, o_shopee_on_fb.id, o_tiktok.id, o_web.id, o_other.id]],
            )
            for don_id, chi_nhanh, nhom in cur.fetchall():
                got[don_id] = (chi_nhanh, nhom)
        self.assertEqual(got[o_fb.id], ("231174", "facebook"))
        self.assertEqual(got[o_shopee_on_fb.id], (None, "shopee"))
        self.assertEqual(got[o_tiktok.id], (None, "tiktok"))
        self.assertEqual(got[o_web.id], (None, "web"))
        self.assertEqual(got[o_other.id], (None, "off_other"))

    def test_original_with_live_restored_clone_excluded(self):
        """Cùng luật overview.py: đơn gốc bị huỷ mà có bản khôi phục còn sống thì
        không vào mart — nếu không, gốc + bản sao cùng rơi vào một kỳ báo cáo."""
        original = mk_order(status=6)
        clone = mk_order(status=0, restored_from=original)
        loaders.full_rebuild(MART)
        self.assertIsNone(_row(original.id))
        self.assertIsNotNone(_row(clone.id))
```

Thêm import ở đầu file: `from apps.channels_core.models import Channel`.

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `TESTENV manage.py test apps.ai_mart.tests.test_don_hang -v2`
Expected: 2 test mới FAIL (`column "chi_nhanh_id" does not exist`; `assertIsNone` fail).

- [ ] **Step 3: Viết migration**

`backend/apps/ai_mart/migrations/0013_don_hang_chi_nhanh_nhom_kenh.py`:

```python
"""Hai cột phục vụ API Daily Report (spec 2026-09-03 §5).

Cột mới nối vào CUỐI bảng: loader INSERT-select không liệt kê cột nên thứ tự
SELECT trong don_hang.py phải khớp thứ tự cột — thêm cột = thêm ở cuối cả hai.
"""

from django.db import migrations

DDL = """
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS chi_nhanh_id text;
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS nhom_kenh_ban text;
COMMENT ON COLUMN ai.don_hang.chi_nhanh_id IS
 'Mã chi nhánh KiotViet của đơn (orders.branch_id) — NULL nếu đơn không gắn chi nhánh. Báo cáo tổng loại chi nhánh nội bộ 1000000139 và kho Đại Mỗ 1000000477 khỏi doanh thu';
COMMENT ON COLUMN ai.don_hang.nhom_kenh_ban IS
 'Nhóm kênh bán 5 mức của Báo cáo tổng: shopee / tiktok / facebook / web / off_other. Sàn xét TRƯỚC kênh: đơn Shopee treo trên kênh facebook-* vẫn là shopee';
CREATE INDEX IF NOT EXISTS don_hang_nhom_kenh_ban_idx ON ai.don_hang (ngay_tao_ngay, nhom_kenh_ban);
"""

DROP = """
DROP INDEX IF EXISTS ai.don_hang_nhom_kenh_ban_idx;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS nhom_kenh_ban;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS chi_nhanh_id;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0012_chi_phi_quang_cao")]
    operations = [migrations.RunSQL(DDL, DROP)]
```

- [ ] **Step 4: Sửa transform**

Trong `backend/apps/ai_mart/infrastructure/sql/don_hang.py`:

(a) Thêm import sau các import hiện có:

```python
from apps.orders.models import INVOICE_CODE_SOURCE_PREFIXES, OrderSource
from apps.orders.reporting.shared import FACEBOOK_CHANNEL_CODE_PREFIX, WEB_CHANNEL_CODE
```

(b) Thay `_SCOPE` bằng:

```python
# Đơn gốc bị huỷ mà có bản khôi phục còn sống: loại, cùng luật summary_overview
# (overview.py) — bản sao kế thừa created_at nên gốc + sao rơi cùng kỳ báo cáo.
_SCOPE = (
    f"o.deleted_at IS NULL AND {sql_exclude_mirror_orders('o')}"
    " AND NOT EXISTS (SELECT 1 FROM orders r"
    "                 WHERE r.restored_from_id = o.id AND r.deleted_at IS NULL)"
)
```

(c) Thêm vào `_CANDIDATES` một nhánh UNION nữa (để khi bản sao xuất hiện thì dòng gốc được dọn):

```python
UNION
SELECT r.restored_from_id FROM orders r
 WHERE r.dw_updated_at > %(cutoff)s AND r.restored_from_id IS NOT NULL
```

(d) Thêm trên hàm `_select` phần dựng CASE nhóm kênh. KHÔNG dùng `LIKE ... %` — full_rebuild gọi `cursor.execute` không params còn upsert có params, nên ký tự `%` sẽ được hiểu hai kiểu khác nhau:

```python
def _marketplace_cond(source: str) -> str:
    """order_source = sàn HOẶC mã hoá đơn mang prefix của sàn (chép _marketplace_order_q)."""
    conds = [f"o.order_source = '{source}'"]
    for prefix, prefix_source in INVOICE_CODE_SOURCE_PREFIXES.items():
        if prefix_source == source:
            conds.append(f"left(coalesce(o.invoice_code, ''), {len(prefix)}) = '{prefix}'")
    return " OR ".join(conds)


# Thứ tự nhánh là luật nghiệp vụ (SALE_CHANNEL_CASE_BRANCHES): sàn trước, kênh sau.
_NHOM_KENH_BAN = f"""CASE
        WHEN {_marketplace_cond(OrderSource.SHOPEE.value)} THEN 'shopee'
        WHEN {_marketplace_cond(OrderSource.TIKTOK.value)} THEN 'tiktok'
        WHEN left(lower(coalesce(ch.code, '')), {len(FACEBOOK_CHANNEL_CODE_PREFIX)})
             = '{FACEBOOK_CHANNEL_CODE_PREFIX}' THEN 'facebook'
        WHEN lower(coalesce(ch.code, '')) = '{WEB_CHANNEL_CODE}' THEN 'web'
        ELSE 'off_other'
    END"""
```

(e) Trong SELECT, ngay sau dòng `now() AS dong_bo_luc` thêm (thứ tự PHẢI là cuối cùng, khớp ALTER):

```python
    now() AS dong_bo_luc,
    NULLIF(btrim(coalesce(o.branch_id, '')), '') AS chi_nhanh_id,
    {_NHOM_KENH_BAN} AS nhom_kenh_ban
```

- [ ] **Step 5: Chạy toàn bộ test ai_mart**

Run: `TESTENV manage.py test apps.ai_mart -v2`
Expected: OK — gồm `test_contract` (COMMENT đủ), `test_etl_equivalence`, 2 test mới.

- [ ] **Step 6: Commit**

```bash
git add apps/ai_mart/migrations/0013_don_hang_chi_nhanh_nhom_kenh.py apps/ai_mart/infrastructure/sql/don_hang.py apps/ai_mart/tests/test_don_hang.py
git commit -m "feat(ai_mart): add chi_nhanh_id, nhom_kenh_ban to don_hang; drop originals with live restored clone"
```

---

### Task 2: App skeleton, token gate, throttle, `/health`

**Files:**
- Create: `backend/apps/daily_report/{__init__,apps,urls}.py`, `backend/apps/daily_report/{domain,application,infrastructure,transport,tests}/__init__.py`, `backend/apps/daily_report/transport/auth.py`, `backend/apps/daily_report/transport/views.py`, `backend/apps/daily_report/infrastructure/freshness.py`, `backend/apps/daily_report/tests/helpers.py`, `backend/apps/daily_report/tests/test_auth.py`, `backend/apps/daily_report/tests/test_health.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS sau `"apps.ai_mart"`; token cạnh `HERMES_API_TOKEN` ~dòng 607; rate cạnh `"hermes"` ~dòng 900), `backend/config/urls.py`, `backend/.importlinter`, `backend/apps/common/throttling.py`, `backend/apps/common/health.py`, `backend/apps/common/tests/test_http_layer.py:240`

**Interfaces:**
- Produces: `transport.auth.daily_report_view(view_func)` decorator (GET, no CRM auth, AllowAny, throttle `daily_report`, token gate); `infrastructure.freshness.read_freshness() -> Freshness(data_as_of, lag_minutes, status, ok)`; `tests.helpers.client()`, `TOKEN`; URL names `daily-report-health`.

- [ ] **Step 1: Test đỏ — cửa xác thực và health**

`backend/apps/daily_report/tests/helpers.py`:

```python
"""Helper dùng chung cho test API Daily Report."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from rest_framework.test import APIClient

from apps.ai_mart.application import etl_service
from apps.ai_mart.tests.helpers import mk_order
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.orders.models import Order, OrderAssessment

TOKEN = "daily-report-test-token"
TZ = ZoneInfo(BUSINESS_TZ)


def client() -> APIClient:
    c = APIClient()
    c.credentials(HTTP_X_API_TOKEN=TOKEN)
    return c


def seed_order(day: date, *, sales_status: str | None = None, amount: int = 100_000, **kw) -> Order:
    """Đơn nội bộ tạo lúc 10:00 giờ VN của ``day``. ``kw`` đi thẳng vào mk_order
    (status, invoice_status, staff, branch_id, order_source, invoice_code, channel...)."""
    order = mk_order(total_after_discount=amount, **kw)
    Order.objects.filter(id=order.id).update(
        created_at=datetime.combine(day, time(10, 0), tzinfo=TZ)
    )
    if sales_status is not None:
        OrderAssessment.objects.update_or_create(
            order=order, defaults={"sales_status_code": sales_status}
        )
    return order


def rebuild() -> None:
    """Nạp lại toàn bộ mart + ghi watermark/nhật ký → freshness 'Bình thường'."""
    etl_service.rebuild_all()
```

`backend/apps/daily_report/tests/test_auth.py`:

```python
"""Cửa xác thực của API Daily Report — chép bộ test Hermes: đây là chỗ dễ hỏng
nhất và hỏng thì lộ số liệu công ty ra ngoài."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .helpers import TOKEN

URL = "/api/daily-report/health"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class DailyReportAuthTests(TestCase):
    def test_missing_token_is_rejected(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_wrong_token_is_rejected(self):
        self.assertEqual(APIClient().get(URL, HTTP_X_API_TOKEN="sai").status_code, 401)

    def test_non_ascii_token_is_401_not_500(self):
        self.assertEqual(APIClient().get(URL, HTTP_X_API_TOKEN="tôken").status_code, 401)

    def test_accepts_bearer_header(self):
        resp = APIClient().get(URL, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertIn(resp.status_code, (200, 503))  # 503 = mart chưa chạy, vẫn là đã qua cửa

    def test_jwt_of_a_crm_user_is_not_enough(self):
        from apps.users.test_helpers import make_admin

        c = APIClient()
        c.force_authenticate(make_admin("admin-daily"))
        self.assertEqual(c.get(URL).status_code, 401)


class DailyReportUnconfiguredTests(TestCase):
    @override_settings(DAILY_REPORT_API_TOKEN="", IS_PRODUCTION=True)
    def test_production_without_token_fails_closed(self):
        resp = APIClient().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertIn("chưa được cấu hình", resp.json()["detail"])

    @override_settings(DAILY_REPORT_API_TOKEN="", IS_PRODUCTION=False)
    def test_dev_without_token_is_open(self):
        self.assertIn(APIClient().get(URL).status_code, (200, 503))


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class DailyReportThrottleTests(TestCase):
    def test_scope_rate_is_enforced(self):
        from django.conf import settings

        rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        self.assertIn("daily_report", rates)
        rates["daily_report"] = "2/hour"
        # IP riêng cho test: bộ đếm nằm trong Redis thật, dùng chung giữa các lần chạy.
        import random

        ip = f"10.77.{random.randint(0, 255)}.{random.randint(1, 254)}"
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}):
            c = APIClient()
            codes = [
                c.get(URL, HTTP_X_API_TOKEN=TOKEN, REMOTE_ADDR=ip).status_code for _ in range(3)
            ]
        self.assertEqual(codes[2], 429, codes)
```

`backend/apps/daily_report/tests/test_health.py`:

```python
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings

from .helpers import TOKEN, client, rebuild

URL = "/api/daily-report/health"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class HealthTests(TestCase):
    def test_ok_after_mart_run(self):
        rebuild()
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["checks"]["db"])
        self.assertTrue(body["checks"]["redis"])
        self.assertTrue(body["checks"]["mart"]["ok"])
        self.assertEqual(body["checks"]["mart"]["status"], "Bình thường")
        self.assertIsNotNone(body["checks"]["mart"]["data_as_of"])
        self.assertIsInstance(body["checks"]["mart"]["lag_minutes"], int)

    def test_503_when_mart_never_ran(self):
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["mart"]["ok"])

    def test_503_when_mart_is_late(self):
        rebuild()
        with connection.cursor() as cur:
            cur.execute("UPDATE ai_meta.nhat_ky_chay SET ket_thuc = ket_thuc - interval '2 hours'")
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["checks"]["mart"]["status"], "Trễ — số liệu có thể cũ")

    def test_503_when_db_check_fails(self):
        rebuild()
        with patch("apps.daily_report.transport.views.check_db", return_value=False):
            resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["db"])
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `TESTENV manage.py test apps.daily_report -v2`
Expected: lỗi import (`No module named apps.daily_report`).

- [ ] **Step 3: Tạo app + đăng ký**

`backend/apps/daily_report/__init__.py`: rỗng. Tương tự cho `domain/`, `application/`, `infrastructure/`, `transport/`, `tests/` (`__init__.py` rỗng).

`backend/apps/daily_report/apps.py`:

```python
from django.apps import AppConfig


class DailyReportConfig(AppConfig):
    name = "apps.daily_report"
    label = "daily_report"
    verbose_name = "Daily Report API — kênh đọc cho trợ lý báo cáo ngày"
```

`backend/config/settings_parts/base.py`:
- INSTALLED_APPS: thêm `"apps.daily_report",` ngay sau `"apps.ai_mart",`.
- Ngay dưới `HERMES_API_TOKEN = ...` thêm:

```python
# Kênh máy-tới-máy cho trợ lý Daily Report (Mac mini) đọc số liệu ngày từ mart ai.*.
# Token RIÊNG, không dùng chung HERMES_API_TOKEN: thu hồi kênh này không được làm
# gãy kênh transcript. Rỗng trên production = ĐÓNG (503).
DAILY_REPORT_API_TOKEN = os.getenv("DAILY_REPORT_API_TOKEN", "").strip()
```

- Trong `DEFAULT_THROTTLE_RATES`, ngay sau dòng `"hermes": ...` thêm:

```python
        # Trợ lý Daily Report: một phiên báo cáo ≤ 10 request. Van chống vòng lặp
        # hỏng bên agent, không phải hạn ngạch.
        "daily_report": os.getenv("DAILY_REPORT_THROTTLE_RATE", "300/hour"),
```

`backend/config/urls.py`: thêm dòng sau `re_path(r"^api/analytics/?", ...)` và TRƯỚC `re_path(r"^api/", include("apps.audit.urls"))`:

```python
    re_path(r"^api/daily-report/?", include("apps.daily_report.urls")),
```

`backend/.importlinter`: trong `layers =`, thêm dòng `    daily_report` ngay TRÊN dòng `    ai_mart`.

- [ ] **Step 4: Throttle + health public**

`backend/apps/common/throttling.py` — thêm ngay sau class `HermesApiThrottle`:

```python
class DailyReportApiThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần riêng cho trợ lý Daily Report (apps.daily_report). Cùng lý do tách
    scope như Hermes: không nhét agent vào bucket ``anon`` dùng chung."""

    scope = "daily_report"

    def get_cache_key(self, request, view) -> str | None:
        ident = self.get_ident(request)
        return f"thr:daily_report:{ident}" if ident else None
```

`backend/apps/common/health.py`: đổi tên `_check_db` → `check_db`, `_check_redis` → `check_redis` (định nghĩa + 2 chỗ gọi trong `healthz`). Sửa docstring đầu hàm: "Public: apps.daily_report tái dùng cho /api/daily-report/health."
`backend/apps/common/tests/test_http_layer.py` dòng ~240: đổi `"_check_db"` thành `"check_db"`.

- [ ] **Step 5: Auth decorator, freshness, view, urls**

`backend/apps/daily_report/transport/auth.py`:

```python
"""Cửa xác thực token tĩnh cho API Daily Report.

Bản sao CÓ CHỦ ĐÍCH của helper bên Hermes (callcenter) — không import ngang
module private của app khác. Vì sao token tĩnh chứ không JWT: bên gọi là agent
chạy nền, không có phiên đăng nhập; thu hồi = đổi env + restart.
"""

from __future__ import annotations

import secrets
from functools import wraps

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.common.throttling import DailyReportApiThrottle


def extract_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def require_api_token(view_func):
    """Prod thiếu token → 503 (fail-closed). Dev thiếu token → mở. Sai token → 401."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        expected = getattr(settings, "DAILY_REPORT_API_TOKEN", "")
        if not expected:
            if getattr(settings, "IS_PRODUCTION", False):
                return Response(
                    {"detail": "API Daily Report chưa được cấu hình token (DAILY_REPORT_API_TOKEN)."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return view_func(request, *args, **kwargs)
        provided = extract_api_token(request)
        # .encode(): compare_digest với str non-ASCII ném TypeError → 500 thay vì 401.
        if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
            return Response({"detail": "Token không hợp lệ."}, status=status.HTTP_401_UNAUTHORIZED)
        return view_func(request, *args, **kwargs)

    return wrapped


def daily_report_view(view_func):
    """GET + không auth CRM + AllowAny + throttle scope riêng + token gate.

    Thứ tự: token gate nằm TRONG DRF view (sau throttle) để request rác vẫn bị
    đếm vào trần. ``@extend_schema`` phải đặt TRÊN decorator này.
    """
    return api_view(["GET"])(
        authentication_classes([])(
            permission_classes([AllowAny])(
                throttle_classes([DailyReportApiThrottle])(require_api_token(view_func))
            )
        )
    )
```

`backend/apps/daily_report/infrastructure/freshness.py`:

```python
"""Độ tươi của mart ai.don_hang — đọc từ view ai.tinh_trang_du_lieu.

Đây là thuốc giải cho chế độ hỏng nguy hiểm nhất: ETL trễ âm thầm. Mọi response
kèm mốc này để trợ lý nói "số liệu tính đến 07:37" thay vì tưởng là số hiện tại.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import connection

OK_STATUS = "Bình thường"
NO_DATA_STATUS = "Chưa có dữ liệu — mart chưa từng chạy"
MART_TABLE = "ai.don_hang"


@dataclass(frozen=True)
class Freshness:
    data_as_of: datetime | None
    lag_minutes: int | None
    status: str

    @property
    def ok(self) -> bool:
        return self.status == OK_STATUS

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data_as_of": self.data_as_of.isoformat() if self.data_as_of else None,
            "lag_minutes": self.lag_minutes,
            "status": self.status,
        }


def read_freshness() -> Freshness:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT du_lieu_toi_moc, do_tre_phut, trang_thai_text"
            " FROM ai.tinh_trang_du_lieu WHERE ten_bang = %s",
            [MART_TABLE],
        )
        row = cur.fetchone()
    if row is None:
        return Freshness(None, None, NO_DATA_STATUS)
    return Freshness(row[0], row[1], row[2])
```

`backend/apps/daily_report/transport/views.py`:

```python
"""7 endpoint GET của API Daily Report. Mọi view đi qua ``daily_report_view``."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from apps.common.health import check_db, check_redis
from apps.daily_report.infrastructure.freshness import read_freshness
from apps.daily_report.transport.auth import daily_report_view


@daily_report_view
def health(request):
    mart = read_freshness()
    checks = {"db": check_db(), "redis": check_redis(), "mart": mart.as_dict()}
    ok = checks["db"] and checks["redis"] and mart.ok
    return Response(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
```

`backend/apps/daily_report/urls.py`:

```python
from django.urls import path

from .transport.views import health

urlpatterns = [
    path("health", health, name="daily-report-health"),
]
```

- [ ] **Step 6: Chạy test xanh + lint tầng**

Run: `TESTENV manage.py test apps.daily_report apps.common.tests.test_http_layer apps.common.tests.test_throttling -v2`
Expected: OK.

Run: `cd /home/desi/desi-order-daily-report/backend && /home/desi/desi-order/.venv/bin/lint-imports`
Expected: `Contracts: 1 kept, 0 broken.` (hoặc số contract hiện có, không broken).

Cần `timezone.now()` trong `date_as_of`? Chưa — ở task này `Freshness.data_as_of` là timestamptz từ DB, Django trả `datetime` aware UTC; `.isoformat()` cho `+00:00`. Quy về giờ VN ở Task 4 (envelope).

- [ ] **Step 7: Commit**

```bash
git add apps/daily_report apps/common/throttling.py apps/common/health.py apps/common/tests/test_http_layer.py config/settings_parts/base.py config/urls.py .importlinter
git commit -m "feat(daily_report): app skeleton with static-token gate, throttle scope and /health"
```

---

### Task 3: Domain — `periods.py` và `definitions.py`

**Files:**
- Create: `backend/apps/daily_report/domain/periods.py`, `backend/apps/daily_report/domain/definitions.py`
- Test: `backend/apps/daily_report/tests/test_periods.py`, `backend/apps/daily_report/tests/test_definitions.py`

**Interfaces:**
- Produces (`periods`): `TZ`, `MAX_RANGE_DAYS = 92`, `ParamError(ValueError)`, `today_vn(now=None) -> date`, `yesterday_vn(now=None) -> date`, `parse_date_param(params, name, *, default) -> date`, `parse_range(params, *, now=None) -> tuple[date, date]`, `parse_choice(params, name, allowed, *, default) -> str`, `parse_int(params, name, *, default, minimum, maximum) -> int`, `iso(d: date) -> str`.
- Produces (`definitions`): `BUCKET_CODES: tuple[tuple[str, tuple[str, ...]], ...]`, `LEAD_BUCKETS`, `ALL_BUCKETED_CODES`, `bucket_of(code) -> str`, `CLOSED_CODES`, `CLOSED_REVENUE_CODES`, `CLOSE_RATE_EXCLUDED_CODES`, `PIPELINE_TERMINAL_CODES`, `DELIVERED_STATUS`, `RETURNED_STATUSES`, `RETURN_RISK_STATUSES`, `AWAITING_STATUSES`, `CANCELLED_STATUS`, `CANCELLED_INVOICE`, `EXCLUDED_BRANCHES`, `CHANNEL_GROUPS`, `METRIC_GLOSSARY`.

- [ ] **Step 1: Test đỏ**

`backend/apps/daily_report/tests/test_periods.py`:

```python
from datetime import date, datetime, timezone as dt_tz

from django.test import SimpleTestCase

from apps.daily_report.domain import periods
from apps.daily_report.domain.periods import ParamError


class PeriodTests(SimpleTestCase):
    def test_yesterday_uses_vietnam_day_not_utc(self):
        # 2026-09-03 00:30 giờ VN = 2026-09-02 17:30 UTC. Hôm qua theo VN là 02/09.
        now = datetime(2026, 9, 2, 17, 30, tzinfo=dt_tz.utc)
        self.assertEqual(periods.today_vn(now), date(2026, 9, 3))
        self.assertEqual(periods.yesterday_vn(now), date(2026, 9, 2))

    def test_default_range_is_yesterday(self):
        now = datetime(2026, 9, 2, 17, 30, tzinfo=dt_tz.utc)
        self.assertEqual(periods.parse_range({}, now=now), (date(2026, 9, 2), date(2026, 9, 2)))

    def test_explicit_range(self):
        got = periods.parse_range({"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual(got, (date(2026, 8, 1), date(2026, 8, 31)))

    def test_from_after_to_rejected(self):
        with self.assertRaises(ParamError):
            periods.parse_range({"from": "2026-08-02", "to": "2026-08-01"})

    def test_range_over_92_days_rejected(self):
        with self.assertRaises(ParamError):
            periods.parse_range({"from": "2026-01-01", "to": "2026-04-03"})  # 93 ngày
        periods.parse_range({"from": "2026-01-01", "to": "2026-04-02"})  # 92 ngày: ok

    def test_bad_date_rejected_with_field_name(self):
        with self.assertRaises(ParamError) as ctx:
            periods.parse_range({"from": "01/08/2026"})
        self.assertIn("`from`", str(ctx.exception))

    def test_choice_and_int(self):
        self.assertEqual(periods.parse_choice({}, "group_by", ("none", "day"), default="none"), "none")
        self.assertEqual(periods.parse_choice({"group_by": "day"}, "group_by", ("none", "day"), default="none"), "day")
        with self.assertRaises(ParamError):
            periods.parse_choice({"group_by": "week"}, "group_by", ("none", "day"), default="none")
        self.assertEqual(periods.parse_int({"lookback_days": "45"}, "lookback_days", default=30, minimum=1, maximum=92), 45)
        with self.assertRaises(ParamError):
            periods.parse_int({"lookback_days": "0"}, "lookback_days", default=30, minimum=1, maximum=92)
        with self.assertRaises(ParamError):
            periods.parse_int({"lookback_days": "abc"}, "lookback_days", default=30, minimum=1, maximum=92)
```

`backend/apps/daily_report/tests/test_definitions.py`:

```python
from django.test import SimpleTestCase

from apps.daily_report.domain import definitions as d
from apps.orders import crm_catalog


class DefinitionTests(SimpleTestCase):
    def test_every_catalog_sales_status_has_a_bucket(self):
        """Mã sale nào trong crm_catalog cũng phải rơi vào ĐÚNG một bucket — thêm mã
        mới mà quên xếp bucket thì test này đỏ, thay vì mã đó âm thầm thành 'other'."""
        codes = [o.code for o in crm_catalog.SALES_STATUS_OPTIONS]
        for code in codes:
            self.assertNotEqual(d.bucket_of(code), "other", code)

    def test_buckets_are_disjoint(self):
        seen = set()
        for bucket, codes in d.BUCKET_CODES:
            for code in codes:
                self.assertNotIn(code, seen, f"{code} nằm ở 2 bucket")
                seen.add(code)
        self.assertEqual(seen, set(d.ALL_BUCKETED_CODES))

    def test_order_buckets_cover_all_statuses_except_cancelled(self):
        from apps.orders.models import OrderStatus

        covered = {d.DELIVERED_STATUS, *d.RETURNED_STATUSES, *d.RETURN_RISK_STATUSES, *d.AWAITING_STATUSES}
        self.assertEqual(covered, {s.value for s in OrderStatus} - {d.CANCELLED_STATUS})

    def test_excluded_branches_include_overview_ones(self):
        self.assertIn("1000000139", d.EXCLUDED_BRANCHES)
        self.assertIn("1000000477", d.EXCLUDED_BRANCHES)

    def test_pipeline_terminal_codes(self):
        for code in ("closed", "spam", "not_buying", "cancelled", "archived", "warranty", "underlying_condition"):
            self.assertIn(code, d.PIPELINE_TERMINAL_CODES)
        for code in ("no_answer_3", "call_back_later", "scheduled", "wants_store_visit"):
            self.assertNotIn(code, d.PIPELINE_TERMINAL_CODES)

    def test_glossary_covers_every_metric_key(self):
        for key in ("lead", "closed", "closed_revenue", "close_rate", "total_orders", "total_revenue", "net_revenue", "open_leads"):
            self.assertIn(key, d.METRIC_GLOSSARY)
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `TESTENV manage.py test apps.daily_report.tests.test_periods apps.daily_report.tests.test_definitions -v2`
Expected: ImportError.

- [ ] **Step 3: Viết `periods.py`**

```python
"""Kỳ báo cáo theo ngày nghiệp vụ Việt Nam.

Chỉ nhận ngày (YYYY-MM-DD), không nhận datetime: mart đã cắt theo ngày
``ngay_tao_ngay``; nhận datetime chỉ tạo ảo giác chính xác tới phút.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.analytics.sql.tz import BUSINESS_TZ

TZ = ZoneInfo(BUSINESS_TZ)
MAX_RANGE_DAYS = 92


class ParamError(ValueError):
    """Tham số query sai — view trả 400 kèm nguyên văn để agent tự sửa."""


def today_vn(now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(TZ).date()


def yesterday_vn(now: datetime | None = None) -> date:
    return today_vn(now) - timedelta(days=1)


def iso(d: date) -> str:
    return d.isoformat()


def parse_date_param(params, name: str, *, default: date) -> date:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ParamError(f"Tham số `{name}` phải là ngày dạng YYYY-MM-DD.") from exc


def parse_range(params, *, now: datetime | None = None) -> tuple[date, date]:
    default = yesterday_vn(now)
    d_from = parse_date_param(params, "from", default=default)
    d_to = parse_date_param(params, "to", default=default)
    if d_from > d_to:
        raise ParamError("Tham số `from` phải nhỏ hơn hoặc bằng `to`.")
    if (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise ParamError(f"Khoảng `from`..`to` tối đa {MAX_RANGE_DAYS} ngày.")
    return d_from, d_to


def parse_choice(params, name: str, allowed: tuple[str, ...], *, default: str) -> str:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    if raw not in allowed:
        raise ParamError(f"Tham số `{name}` phải là một trong: {', '.join(allowed)}.")
    return raw


def parse_int(params, name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ParamError(f"Tham số `{name}` phải là số nguyên.") from exc
    if value < minimum or value > maximum:
        raise ParamError(f"Tham số `{name}` phải trong khoảng {minimum}..{maximum}.")
    return value
```

- [ ] **Step 4: Viết `definitions.py`**

```python
"""Định nghĩa số liệu của API Daily Report — GHÉP từ hằng số Báo cáo tổng / Báo
cáo sale (apps/orders/reporting/shared.py), không chép mã. Đổi luật ở đó là API
đổi theo, số của trợ lý không thể lệch màn hình CRM một cách im lặng.
"""

from __future__ import annotations

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.orders.models import OrderStatus
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS
from apps.orders.reporting.shared import (
    AWAITING_DELIVERY_ORDER_STATUSES,
    CLOSED_REVENUE_SALES_STATUS_CODES,
    CLOSED_SALES_STATUS_CODES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    DIRTY_SALES_STATUS_CODES,
    NOT_BUYING_SALES_STATUS_CODE,
    NURTURE_SALES_STATUS_CODES_FOR_CAPACITY,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    SALE_CHANNEL_GROUPS,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    UNREACHABLE_SALES_STATUS_CODES,
    UNREACHED_SALES_STATUS_CODES,
    WANTS_STORE_VISIT_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
)

# ── Trạng thái sale → bucket (rời nhau, phủ hết crm_catalog.SALES_STATUS_OPTIONS) ──
CLOSED_CODES = CLOSED_SALES_STATUS_CODES
CLOSED_REVENUE_CODES = CLOSED_REVENUE_SALES_STATUS_CODES
# no_answer_1..5: còn nhịp gọi tiếp — "đang gọi", chưa phải không tiếp cận được.
CALLING_CODES = tuple(c for c in UNREACHABLE_SALES_STATUS_CODES if c not in UNREACHED_SALES_STATUS_CODES)
CANCELLED_SALE_CODE = "cancelled"
ARCHIVED_SALE_CODE = "archived"

BUCKET_CODES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("closed", CLOSED_CODES),
    ("calling", CALLING_CODES),
    ("unreached", UNREACHED_SALES_STATUS_CODES),
    ("nurture", NURTURE_SALES_STATUS_CODES_FOR_CAPACITY),
    ("wants_store_visit", (WANTS_STORE_VISIT_SALES_STATUS_CODE,)),
    ("dirty", DIRTY_SALES_STATUS_CODES),
    ("not_buying", (NOT_BUYING_SALES_STATUS_CODE,)),
    ("underlying_condition", (UNDERLYING_CONDITION_SALES_STATUS_CODE,)),
    ("warranty", (WARRANTY_SALES_STATUS_CODE,)),
    ("cancelled", (CANCELLED_SALE_CODE,)),
    ("archived", (ARCHIVED_SALE_CODE,)),
)
LEAD_BUCKETS: tuple[str, ...] = tuple(bucket for bucket, _ in BUCKET_CODES)
_CODE_TO_BUCKET = {code: bucket for bucket, codes in BUCKET_CODES for code in codes}
ALL_BUCKETED_CODES: tuple[str, ...] = tuple(_CODE_TO_BUCKET)


def bucket_of(code: str | None) -> str:
    return _CODE_TO_BUCKET.get(code or "", "other")


# Mẫu số tỷ lệ chốt (summary_close_rate_by_staff): loại 2 mã ngoài tầm sale online.
CLOSE_RATE_EXCLUDED_CODES = (UNDERLYING_CONDITION_SALES_STATUS_CODE, WANTS_STORE_VISIT_SALES_STATUS_CODE)

# Lead KHÔNG còn có thể chốt → rời pipeline.
PIPELINE_TERMINAL_CODES: tuple[str, ...] = (
    *CLOSED_CODES,
    *DIRTY_SALES_STATUS_CODES,
    NOT_BUYING_SALES_STATUS_CODE,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
    CANCELLED_SALE_CODE,
    ARCHIVED_SALE_CODE,
)

# ── Đơn (Báo cáo tổng) ──
DELIVERED_STATUS = DELIVERED_SUCCESS_ORDER_STATUS
RETURNED_STATUSES = RETURN_ORDER_STATUSES
RETURN_RISK_STATUSES = RETURN_RISK_ORDER_STATUSES
AWAITING_STATUSES = AWAITING_DELIVERY_ORDER_STATUSES
CANCELLED_STATUS = OrderStatus.CANCELLED.value
CANCELLED_INVOICE = CANCELLED_INVOICE_STATUS
EXCLUDED_BRANCHES: tuple[str, ...] = tuple(EXCLUDED_BRANCH_IDS) + tuple(OVERVIEW_EXCLUDED_BRANCH_IDS)
CHANNEL_GROUPS = SALE_CHANNEL_GROUPS

ORDER_BUCKET_LABELS = {
    "delivered": "Giao thành công",
    "awaiting": "Chờ giao (mọi trạng thái chưa có kết quả cuối)",
    "returned": "Hoàn (đang/đã chuyển hoàn)",
    "return_risk": "Rủi ro hoàn (chờ giao lại)",
}

METRIC_GLOSSARY: dict[str, str] = {
    "lead": "Đơn nội bộ (không tính đơn mirror foxia/cnv, không tính đơn gốc đã được khôi phục thành bản sao) tạo trong kỳ, theo ngày tạo giờ Việt Nam.",
    "unassigned": "Lead chưa gán nhân viên sale.",
    "unassessed": "Lead sale chưa nhập trạng thái chăm sóc.",
    "closed": "Lead có trạng thái sale thuộc bucket closed (closed, closed_express, closed_scheduled_date, created_order).",
    "closed_revenue": "Tổng tiền sau giảm (VND) của lead có trạng thái closed, closed_express, created_order — KHÔNG tính closed_scheduled_date, đúng luật Báo cáo sale.",
    "close_rate": "closed / (lead − underlying_condition − wants_store_visit). null khi mẫu số bằng 0.",
    "calling": "Không nghe máy lần 1..5 — còn nhịp gọi tiếp.",
    "unreached": "Không nghe máy lần 6 hoặc không liên hệ được.",
    "nurture": "Tư vấn thêm / hẹn lịch / gọi lại sau.",
    "dirty": "Spam, lặp, sai số, khách bom hàng.",
    "other": "Có trạng thái sale nhưng mã không thuộc từ điển hiện tại.",
    "total_orders": "Đơn đã lên hoá đơn KiotViet (invoice_status khác null) và không huỷ (status ≠ 6, invoice_status ≠ 2), tạo trong kỳ, loại chi nhánh nội bộ và kho Đại Mỗ.",
    "total_revenue": "Tổng tiền sau giảm (VND) của total_orders. delivered + awaiting + returned + return_risk = total (bất biến).",
    "net_revenue": "total_revenue trừ tiền đơn hoàn (status 4, 5, 12, 13).",
    "channel_group": "shopee / tiktok (theo nguồn đơn hoặc prefix mã hoá đơn HDSPE_/HDTTS_) → facebook (kênh facebook-*) → web (google-ads-web) → off_other. Sàn xét trước kênh.",
    "open_leads": "Lead tạo trong lookback_days ngày, chưa lên hoá đơn, chưa huỷ, trạng thái sale chưa thuộc nhóm kết thúc (closed, dirty, not_buying, underlying_condition, warranty, cancelled, archived).",
}
```

Kiểm tra `apps/orders/order_status_constants.py` có `CANCELLED_INVOICE_STATUS` (agent khảo sát đã thấy). Nếu tên khác, dùng đúng tên trong file đó.

- [ ] **Step 5: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report.tests.test_periods apps.daily_report.tests.test_definitions -v2`
Expected: OK.

```bash
git add apps/daily_report/domain apps/daily_report/tests/test_periods.py apps/daily_report/tests/test_definitions.py
git commit -m "feat(daily_report): period parsing and metric definitions composed from report constants"
```

---

### Task 4: `/leads/stats` — queries, service (envelope + cache), view

**Files:**
- Create: `backend/apps/daily_report/infrastructure/queries.py`, `backend/apps/daily_report/application/stats_service.py`
- Modify: `backend/apps/daily_report/transport/views.py`, `backend/apps/daily_report/urls.py`
- Test: `backend/apps/daily_report/tests/test_leads.py`, `backend/apps/daily_report/tests/test_cache.py`

**Interfaces:**
- Produces (`queries`): `GROUP_COLUMNS`, `run(sql, params) -> list[dict]`, `leads_rows(d_from, d_to, group_by) -> list[dict]`.
- Produces (`stats_service`): `envelope(data, *, d_from, d_to, fresh) -> dict`, `cached(name, params, fresh, build)`, `leads_stats(d_from, d_to, group_by) -> dict|list`, `leads_payload(d_from, d_to, group_by) -> dict`, `format_key(group_by, row) -> dict`, `LEAD_GROUPS = ("none","day","source","staff","channel_group")`.
- Consumes: Task 1 cột mart; Task 3 `periods`, `definitions`; Task 2 `read_freshness`, `daily_report_view`.

- [ ] **Step 1: Test đỏ**

`backend/apps/daily_report/tests/test_leads.py`:

```python
from datetime import date

from django.test import TestCase, override_settings

from apps.daily_report.domain import definitions as d
from apps.users.test_helpers import make_admin

from .helpers import TOKEN, client, rebuild, seed_order

URL = "/api/daily-report/leads/stats"
D = date(2026, 9, 2)


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class LeadStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sale_a = make_admin("sale-a")
        cls.sale_a.last_name, cls.sale_a.first_name = "Nguyễn", "A"
        cls.sale_a.save(update_fields=["last_name", "first_name"])
        seed_order(D, sales_status="closed", amount=300_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="closed_scheduled_date", amount=200_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="no_answer_2", staff=cls.sale_a, order_source="messenger")
        seed_order(D, sales_status="unreachable", order_source="messenger")
        seed_order(D, sales_status="call_back_later")
        seed_order(D, sales_status="spam")
        seed_order(D, sales_status="underlying_condition")
        seed_order(D, sales_status="wants_store_visit")
        seed_order(D)  # chưa đánh giá, chưa gán
        seed_order(date(2026, 9, 1), sales_status="closed", amount=999_000)  # ngoài kỳ
        seed_order(D, sales_status="closed", order_source="foxia")  # mirror: mart loại
        rebuild()

    def _get(self, **params):
        resp = client().get(URL, {"from": "2026-09-02", "to": "2026-09-02", **params})
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_envelope_meta(self):
        body = self._get()
        meta = body["meta"]
        self.assertEqual(meta["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(meta["range"], {"from": "2026-09-02", "to": "2026-09-02"})
        self.assertEqual(meta["data_status"], "Bình thường")
        self.assertTrue(meta["data_as_of"].endswith("+07:00"))
        self.assertTrue(meta["generated_at"].endswith("+07:00"))
        self.assertEqual(meta["definitions_url"], "/api/daily-report/definitions")

    def test_totals_and_buckets(self):
        data = self._get()["data"]
        self.assertEqual(data["leads"], 9)
        self.assertEqual(data["unassigned"], 6)
        self.assertEqual(data["unassessed"], 1)
        self.assertEqual(data["closed"], 2)
        self.assertEqual(data["closed_revenue"], 300_000)  # closed_scheduled_date không tính
        self.assertEqual(data["calling"], 1)
        self.assertEqual(data["unreached"], 1)
        self.assertEqual(data["nurture"], 1)
        self.assertEqual(data["dirty"], 1)
        self.assertEqual(data["underlying_condition"], 1)
        self.assertEqual(data["wants_store_visit"], 1)
        self.assertEqual(data["other"], 0)
        # Bất biến: unassessed + mọi bucket == leads
        self.assertEqual(data["unassessed"] + sum(data[b] for b in d.LEAD_BUCKETS) + data["other"], data["leads"])
        # close_rate = 2 / (9 - 1 - 1)
        self.assertAlmostEqual(data["close_rate"], round(2 / 7, 4))
        self.assertIsInstance(data["closed_revenue"], int)

    def test_group_by_staff(self):
        rows = self._get(group_by="staff")["data"]
        by_name = {(r["staff"] or {}).get("name") if r["staff"] else None: r for r in rows}
        self.assertEqual(by_name["Nguyễn A"]["leads"], 3)
        self.assertEqual(by_name["Nguyễn A"]["closed"], 2)
        self.assertEqual(by_name[None]["leads"], 6)

    def test_group_by_source_has_code_and_label(self):
        rows = self._get(group_by="source")["data"]
        landing = next(r for r in rows if r["source"] and r["source"]["code"] == "landing")
        self.assertEqual(landing["source"]["label"], "Landing")
        self.assertEqual(landing["leads"], 2)

    def test_group_by_day(self):
        rows = client().get(URL, {"from": "2026-09-01", "to": "2026-09-02", "group_by": "day"}).json()["data"]
        self.assertEqual([r["date"] for r in rows], ["2026-09-01", "2026-09-02"])
        self.assertEqual(rows[0]["closed_revenue"], 999_000)

    def test_empty_period_returns_zeros_not_404(self):
        data = client().get(URL, {"from": "2025-01-01", "to": "2025-01-01"}).json()["data"]
        self.assertEqual(data["leads"], 0)
        self.assertIsNone(data["close_rate"])

    def test_bad_params_are_400(self):
        self.assertEqual(client().get(URL, {"from": "2026-09-03", "to": "2026-09-01"}).status_code, 400)
        self.assertEqual(client().get(URL, {"group_by": "week"}).status_code, 400)
        self.assertEqual(client().get(URL, {"from": "2026-01-01", "to": "2026-06-30"}).status_code, 400)
        self.assertIn("detail", client().get(URL, {"from": "x"}).json())
```

`backend/apps/daily_report/tests/test_cache.py`:

```python
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai_mart.infrastructure import loaders
from apps.daily_report.application import stats_service

from .helpers import TOKEN, client, rebuild, seed_order

URL = "/api/daily-report/leads/stats"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class CacheTests(TestCase):
    def setUp(self):
        cache.clear()
        seed_order(date(2026, 9, 2), sales_status="closed")
        rebuild()

    def test_second_call_does_not_hit_sql(self):
        with patch.object(stats_service.queries, "leads_rows", wraps=stats_service.queries.leads_rows) as spy:
            client().get(URL, {"from": "2026-09-02", "to": "2026-09-02"})
            client().get(URL, {"from": "2026-09-02", "to": "2026-09-02"})
        self.assertEqual(spy.call_count, 1)

    def test_new_mart_watermark_invalidates(self):
        with patch.object(stats_service.queries, "leads_rows", wraps=stats_service.queries.leads_rows) as spy:
            client().get(URL, {"from": "2026-09-02", "to": "2026-09-02"})
            from datetime import timedelta

            from django.utils import timezone

            loaders.write_watermark("__hot__", timezone.now() + timedelta(minutes=1))
            client().get(URL, {"from": "2026-09-02", "to": "2026-09-02"})
        self.assertEqual(spy.call_count, 2)
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `TESTENV manage.py test apps.daily_report.tests.test_leads apps.daily_report.tests.test_cache -v2`
Expected: ImportError / 404.

- [ ] **Step 3: Viết `queries.py`**

```python
"""SQL đọc mart ai.* cho API Daily Report. Mọi số gộp trong Postgres; Python chỉ
định dạng. Tham số đi qua psycopg (list → ARRAY), KHÔNG format chuỗi từ input.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.daily_report.domain import definitions as d

GROUP_COLUMNS: dict[str, tuple[str, ...]] = {
    "none": (),
    "day": ("ngay_tao_ngay",),
    "source": ("nguon_don_ma", "nguon_don_text"),
    "staff": ("nhan_vien_id", "nhan_vien_ten"),
    "channel_group": ("nhom_kenh_ban",),
}


def run(sql: str, params: dict) -> list[dict]:
    with statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS):
        with connection.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _group_clauses(group_by: str) -> tuple[str, str]:
    cols = GROUP_COLUMNS[group_by]
    if not cols:
        return "", ""
    joined = ", ".join(cols)
    return f"{joined}, ", f"GROUP BY {joined} ORDER BY {joined} NULLS LAST"


def _bucket_select() -> tuple[str, dict]:
    parts, params = [], {}
    for bucket, codes in d.BUCKET_CODES:
        params[f"b_{bucket}"] = list(codes)
        parts.append(f"count(*) FILTER (WHERE trang_thai_sale_ma = ANY(%(b_{bucket})s))::int AS {bucket}")
    params["all_codes"] = list(d.ALL_BUCKETED_CODES)
    parts.append(
        "count(*) FILTER (WHERE trang_thai_sale_ma IS NOT NULL"
        " AND NOT (trang_thai_sale_ma = ANY(%(all_codes)s)))::int AS other"
    )
    return ",\n       ".join(parts), params


def leads_rows(d_from: date, d_to: date, group_by: str) -> list[dict]:
    select_cols, group_sql = _group_clauses(group_by)
    bucket_sql, params = _bucket_select()
    params.update(
        {
            "from": d_from,
            "to": d_to,
            "closed_revenue": list(d.CLOSED_REVENUE_CODES),
        }
    )
    sql = f"""
SELECT {select_cols}
       count(*)::int AS leads,
       count(*) FILTER (WHERE nhan_vien_id IS NULL)::int AS unassigned,
       count(*) FILTER (WHERE trang_thai_sale_ma IS NULL)::int AS unassessed,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE trang_thai_sale_ma = ANY(%(closed_revenue)s)), 0))::bigint AS closed_revenue,
       {bucket_sql}
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s
{group_sql}
"""
    return run(sql, params)
```

- [ ] **Step 4: Viết `stats_service.py`**

```python
"""Payload từng endpoint: envelope + cache + định dạng. Không có SQL ở đây."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date

from django.core.cache import cache
from django.utils import timezone

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain.periods import TZ, iso
from apps.daily_report.infrastructure import queries
from apps.daily_report.infrastructure.freshness import Freshness, read_freshness

DEFINITIONS_URL = "/api/daily-report/definitions"
CACHE_TTL_SECONDS = 30 * 60
LEAD_GROUPS = ("none", "day", "source", "staff", "channel_group")


def envelope(data, *, d_from: date, d_to: date, fresh: Freshness) -> dict:
    return {
        "meta": {
            "timezone": str(TZ.key),
            "range": {"from": iso(d_from), "to": iso(d_to)},
            "data_as_of": fresh.data_as_of.astimezone(TZ).isoformat() if fresh.data_as_of else None,
            "data_status": fresh.status,
            "definitions_url": DEFINITIONS_URL,
            "generated_at": timezone.now().astimezone(TZ).isoformat(),
        },
        "data": data,
    }


def cached(name: str, params: dict, fresh: Freshness, build: Callable[[], object]):
    """Key gắn mốc dữ liệu mart: mart nạp xong là key mới, không cần invalidation."""
    stamp = fresh.data_as_of.isoformat() if fresh.data_as_of else "none"
    digest = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    key = f"daily_report:{name}:{digest}:{stamp}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = build()
    cache.set(key, value, CACHE_TTL_SECONDS)
    return value


def format_key(group_by: str, row: dict) -> dict:
    if group_by == "day":
        return {"date": iso(row["ngay_tao_ngay"])}
    if group_by == "source":
        code = row["nguon_don_ma"]
        return {"source": {"code": code, "label": row["nguon_don_text"]} if code else None}
    if group_by == "staff":
        sid = row["nhan_vien_id"]
        return {"staff": {"id": sid, "name": row["nhan_vien_ten"]} if sid is not None else None}
    if group_by == "channel_group":
        return {"channel_group": row["nhom_kenh_ban"]}
    return {}


def _close_rate(row: dict) -> float | None:
    denom = row["leads"] - row["underlying_condition"] - row["wants_store_visit"]
    return round(row["closed"] / denom, 4) if denom > 0 else None


def _lead_metrics(row: dict) -> dict:
    out = {
        "leads": row["leads"],
        "unassigned": row["unassigned"],
        "unassessed": row["unassessed"],
        "closed_revenue": int(row["closed_revenue"]),
    }
    for bucket in d.LEAD_BUCKETS:
        out[bucket] = row[bucket]
    out["other"] = row["other"]
    out["close_rate"] = _close_rate(row)
    return out


def leads_stats(d_from: date, d_to: date, group_by: str = "none"):
    rows = queries.leads_rows(d_from, d_to, group_by)
    if group_by == "none":
        return _lead_metrics(rows[0])
    return [{**format_key(group_by, r), **_lead_metrics(r)} for r in rows]


def leads_payload(d_from: date, d_to: date, group_by: str) -> dict:
    fresh = read_freshness()
    params = {"from": d_from, "to": d_to, "group_by": group_by}
    data = cached("leads", params, fresh, lambda: leads_stats(d_from, d_to, group_by))
    return envelope(data, d_from=d_from, d_to=d_to, fresh=fresh)
```

- [ ] **Step 5: View + route**

Thêm vào `transport/views.py`:

```python
from apps.daily_report.application import stats_service
from apps.daily_report.domain import periods
from apps.daily_report.domain.periods import ParamError


def _bad_request(exc: ParamError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _run_payload(build):
    """Gọi service; statement_timeout / DB chết → 503 rõ ràng thay vì 500 vô danh."""
    try:
        return Response(build())
    except OperationalError:
        logger.exception("daily_report: truy vấn thất bại")
        return Response(
            {"detail": "Truy vấn quá lâu hoặc DB lỗi — thử lại sau."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@daily_report_view
def leads_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        group_by = periods.parse_choice(
            request.query_params, "group_by", stats_service.LEAD_GROUPS, default="none"
        )
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.leads_payload(d_from, d_to, group_by))
```

Thêm import đầu `views.py`: `import logging`, `from django.db import OperationalError`, và `logger = logging.getLogger(__name__)`. Các view dữ liệu ở Task 5–8 đều trả qua `_run_payload(lambda: ...)` thay vì `Response(...)` trực tiếp.

`urls.py`: import `leads_stats`, thêm `path("leads/stats", leads_stats, name="daily-report-leads-stats"),`.

- [ ] **Step 6: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report -v2`
Expected: OK.

```bash
git add apps/daily_report
git commit -m "feat(daily_report): /leads/stats with envelope, mart-stamped cache and bucketed metrics"
```

---

### Task 5: `/orders/stats` và `/revenue` + test tương đương với Báo cáo tổng

**Files:**
- Modify: `backend/apps/daily_report/infrastructure/queries.py`, `backend/apps/daily_report/application/stats_service.py`, `backend/apps/daily_report/transport/views.py`, `backend/apps/daily_report/urls.py`
- Test: `backend/apps/daily_report/tests/test_orders_revenue.py`

**Interfaces:**
- Produces: `queries.orders_rows(d_from, d_to, group_by) -> list[dict]` (cột: `total_orders, delivered, awaiting, returned, return_risk, total_revenue, delivered_revenue, awaiting_revenue, returned_revenue, return_risk_revenue, net_revenue`); `stats_service.orders_stats(d_from, d_to, group_by)`, `revenue(d_from, d_to, group_by)`, `orders_payload(...)`, `revenue_payload(...)`, `ORDER_GROUPS = ("none","day","channel_group","staff")`, `REVENUE_GROUPS = ("none","day","channel_group")`.

- [ ] **Step 1: Test đỏ**

`backend/apps/daily_report/tests/test_orders_revenue.py`:

```python
"""Khoá Q2 của spec: số của API phải BẰNG OrderRepository.summary_overview cùng kỳ."""

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.channels_core.models import Channel
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository

from .helpers import TOKEN, client, rebuild, seed_order

ORDERS_URL = "/api/daily-report/orders/stats"
REVENUE_URL = "/api/daily-report/revenue"
D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}


def _money(s: str | None) -> int:
    return int(Decimal(s or "0"))


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class OrdersRevenueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fb = Channel.objects.create(code="facebook-page-t", name="FB T")
        seed_order(D, status=3, invoice_status=1, amount=500_000, channel=fb, sales_status="closed")
        seed_order(D, status=2, invoice_status=1, amount=200_000, sales_status="closed")   # awaiting
        seed_order(D, status=5, invoice_status=1, amount=150_000)                          # returned
        seed_order(D, status=10, invoice_status=1, amount=120_000)                         # return risk
        seed_order(D, status=99, invoice_status=3, amount=70_000)                          # mã lạ → awaiting
        seed_order(D, status=6, invoice_status=1, amount=999)                              # huỷ POS
        seed_order(D, status=3, invoice_status=2, amount=999)                              # huỷ web KV
        seed_order(D, status=3, invoice_status=None, amount=999, sales_status="closed")    # lead, chưa HĐ
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000139")      # chi nhánh ẩn
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000477")      # Đại Mỗ
        seed_order(D, status=3, invoice_status=1, amount=999, order_source="foxia")        # mirror
        original = seed_order(D, status=6, invoice_status=1, amount=999)
        seed_order(D, status=3, invoice_status=1, amount=80_000, restored_from=original)   # bản khôi phục sống
        seed_order(D, status=3, invoice_status=1, amount=60_000, invoice_code="HDSPE_777", channel=fb)  # shopee trên kênh FB
        rebuild()

    def test_matches_summary_overview(self):
        api = client().get(ORDERS_URL, P).json()["data"]
        rev = client().get(REVENUE_URL, P).json()["data"]
        ref = OrderRepository.summary_overview("2026-09-02", "2026-09-02")
        self.assertEqual(api["total_orders"], ref["total_orders"])
        self.assertEqual(api["delivered"], ref["delivered_orders"])
        self.assertEqual(api["awaiting"], ref["awaiting_orders"])
        self.assertEqual(api["returned"], ref["returned_orders"])
        self.assertEqual(api["return_risk"], ref["return_risk_orders"])
        self.assertEqual(rev["total_revenue"], _money(ref["total_revenue"]))
        self.assertEqual(rev["delivered_revenue"], _money(ref["delivered_revenue"]))
        self.assertEqual(rev["awaiting_revenue"], _money(ref["awaiting_revenue"]))
        self.assertEqual(rev["returned_revenue"], _money(ref["returned_revenue"]))
        self.assertEqual(rev["return_risk_revenue"], _money(ref["return_risk_revenue"]))
        self.assertEqual(rev["net_revenue"], _money(ref["net_revenue"]))

    def test_expected_numbers(self):
        api = client().get(ORDERS_URL, P).json()["data"]
        self.assertEqual(api["total_orders"], 7)
        self.assertEqual(api["delivered"], 3)
        self.assertEqual(api["awaiting"], 2)
        self.assertEqual(api["returned"], 1)
        self.assertEqual(api["return_risk"], 1)
        rev = client().get(REVENUE_URL, P).json()["data"]
        self.assertEqual(rev["total_revenue"], 500_000 + 200_000 + 150_000 + 120_000 + 70_000 + 80_000 + 60_000)
        self.assertEqual(rev["net_revenue"], rev["total_revenue"] - 150_000)
        self.assertEqual(rev["closed_revenue"], 500_000 + 200_000 + 999)  # góc nhìn sale: lead chưa HĐ vẫn tính

    def test_bucket_invariant(self):
        api = client().get(ORDERS_URL, P).json()["data"]
        self.assertEqual(api["delivered"] + api["awaiting"] + api["returned"] + api["return_risk"], api["total_orders"])
        rev = client().get(REVENUE_URL, P).json()["data"]
        self.assertEqual(
            rev["delivered_revenue"] + rev["awaiting_revenue"] + rev["returned_revenue"] + rev["return_risk_revenue"],
            rev["total_revenue"],
        )

    def test_group_by_channel_group(self):
        rows = client().get(REVENUE_URL, {**P, "group_by": "channel_group"}).json()["data"]
        by = {r["channel_group"]: r for r in rows}
        self.assertEqual(by["facebook"]["total_revenue"], 500_000)
        self.assertEqual(by["shopee"]["total_revenue"], 60_000)
        self.assertEqual(by["off_other"]["total_orders"], 5)

    def test_group_by_staff_only_on_orders(self):
        self.assertEqual(client().get(ORDERS_URL, {**P, "group_by": "staff"}).status_code, 200)
        self.assertEqual(client().get(REVENUE_URL, {**P, "group_by": "staff"}).status_code, 400)
```

Lưu ý `status=99`: `Order.status` là `PositiveSmallIntegerField(choices=...)` — `objects.create` không validate choices nên ghi được, đúng như luồng import KiotViet.

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `TESTENV manage.py test apps.daily_report.tests.test_orders_revenue -v2`
Expected: 404 → AssertionError.

- [ ] **Step 3: Query đơn**

Thêm vào `queries.py`:

```python
def orders_rows(d_from: date, d_to: date, group_by: str) -> list[dict]:
    """Tập đơn = có hoá đơn, không huỷ, loại chi nhánh ẩn — đúng total_q của Báo cáo tổng.
    Rổ 'awaiting' là PHẦN BÙ (mã trạng thái lạ tự rơi vào đây, không rớt khỏi tổng)."""
    select_cols, group_sql = _group_clauses(group_by)
    params = {
        "from": d_from,
        "to": d_to,
        "delivered": d.DELIVERED_STATUS,
        "returned": list(d.RETURNED_STATUSES),
        "return_risk": list(d.RETURN_RISK_STATUSES),
        "cancelled": d.CANCELLED_STATUS,
        "cancelled_invoice": d.CANCELLED_INVOICE,
        "excluded_branches": list(d.EXCLUDED_BRANCHES),
    }
    is_delivered = "trang_thai_don_ma = %(delivered)s"
    is_returned = "trang_thai_don_ma = ANY(%(returned)s)"
    is_risk = "trang_thai_don_ma = ANY(%(return_risk)s)"
    is_awaiting = f"NOT ({is_delivered} OR {is_returned} OR {is_risk})"
    sql = f"""
SELECT {select_cols}
       count(*)::int AS total_orders,
       count(*) FILTER (WHERE {is_delivered})::int AS delivered,
       count(*) FILTER (WHERE {is_awaiting})::int AS awaiting,
       count(*) FILTER (WHERE {is_returned})::int AS returned,
       count(*) FILTER (WHERE {is_risk})::int AS return_risk,
       round(coalesce(sum(tong_sau_giam), 0))::bigint AS total_revenue,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE {is_delivered}), 0))::bigint AS delivered_revenue,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE {is_awaiting}), 0))::bigint AS awaiting_revenue,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE {is_returned}), 0))::bigint AS returned_revenue,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE {is_risk}), 0))::bigint AS return_risk_revenue,
       round(coalesce(sum(tong_sau_giam) FILTER (WHERE NOT ({is_returned})), 0))::bigint AS net_revenue
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s
  AND trang_thai_hoa_don_ma IS NOT NULL
  AND trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND trang_thai_hoa_don_ma IS DISTINCT FROM %(cancelled_invoice)s
  AND coalesce(chi_nhanh_id, '') <> ALL(%(excluded_branches)s)
{group_sql}
"""
    return run(sql, params)
```

- [ ] **Step 4: Service**

Thêm vào `stats_service.py`:

```python
ORDER_GROUPS = ("none", "day", "channel_group", "staff")
REVENUE_GROUPS = ("none", "day", "channel_group")
_COUNT_KEYS = ("total_orders", "delivered", "awaiting", "returned", "return_risk")
_MONEY_KEYS = ("total_revenue", "delivered_revenue", "awaiting_revenue", "returned_revenue", "return_risk_revenue", "net_revenue")


def _key_tuple(group_by: str, row: dict) -> tuple:
    return tuple(row[c] for c in queries.GROUP_COLUMNS[group_by])


def orders_stats(d_from: date, d_to: date, group_by: str = "none"):
    rows = queries.orders_rows(d_from, d_to, group_by)
    metrics = lambda r: {k: r[k] for k in _COUNT_KEYS}  # noqa: E731
    if group_by == "none":
        return metrics(rows[0])
    return [{**format_key(group_by, r), **metrics(r)} for r in rows]


def revenue(d_from: date, d_to: date, group_by: str = "none"):
    """Tiền của 4 rổ + closed_revenue (góc nhìn sale, từ tập lead cùng kỳ)."""
    order_rows = queries.orders_rows(d_from, d_to, group_by)
    lead_rows = queries.leads_rows(d_from, d_to, group_by)
    closed_by_key = {_key_tuple(group_by, r): int(r["closed_revenue"]) for r in lead_rows}

    def metrics(r: dict) -> dict:
        out = {k: int(r[k]) for k in _MONEY_KEYS}
        out["closed_revenue"] = closed_by_key.get(_key_tuple(group_by, r), 0)
        return out

    if group_by == "none":
        return metrics(order_rows[0])
    # Nhóm chỉ có lead (chưa có đơn hoá đơn) vẫn phải xuất hiện với closed_revenue.
    seen = {_key_tuple(group_by, r) for r in order_rows}
    result = [{**format_key(group_by, r), **metrics(r)} for r in order_rows]
    for r in lead_rows:
        if _key_tuple(group_by, r) not in seen and int(r["closed_revenue"]):
            result.append({**format_key(group_by, r), **{k: 0 for k in _MONEY_KEYS}, "closed_revenue": int(r["closed_revenue"])})
    return result


def orders_payload(d_from: date, d_to: date, group_by: str) -> dict:
    fresh = read_freshness()
    params = {"from": d_from, "to": d_to, "group_by": group_by}
    data = cached("orders", params, fresh, lambda: orders_stats(d_from, d_to, group_by))
    return envelope(data, d_from=d_from, d_to=d_to, fresh=fresh)


def revenue_payload(d_from: date, d_to: date, group_by: str) -> dict:
    fresh = read_freshness()
    params = {"from": d_from, "to": d_to, "group_by": group_by}
    data = cached("revenue", params, fresh, lambda: revenue(d_from, d_to, group_by))
    return envelope(data, d_from=d_from, d_to=d_to, fresh=fresh)
```

- [ ] **Step 5: View + route**

Thêm vào `views.py`:

```python
@daily_report_view
def orders_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        group_by = periods.parse_choice(request.query_params, "group_by", stats_service.ORDER_GROUPS, default="none")
    except ParamError as exc:
        return _bad_request(exc)
    return Response(stats_service.orders_payload(d_from, d_to, group_by))


@daily_report_view
def revenue(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        group_by = periods.parse_choice(request.query_params, "group_by", stats_service.REVENUE_GROUPS, default="none")
    except ParamError as exc:
        return _bad_request(exc)
    return Response(stats_service.revenue_payload(d_from, d_to, group_by))
```

`urls.py`: thêm `path("orders/stats", orders_stats, name="daily-report-orders-stats")`, `path("revenue", revenue, name="daily-report-revenue")`.

- [ ] **Step 6: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report -v2`
Expected: OK. Nếu `test_matches_summary_overview` lệch: sai ở phía mart (Task 1) hoặc điều kiện WHERE — KHÔNG sửa số kỳ vọng cho khớp; đối chiếu từng đơn seed với `overview.py:130-192`.

```bash
git add apps/daily_report
git commit -m "feat(daily_report): /orders/stats and /revenue mirroring overview report buckets"
```

---

### Task 6: `/pipeline`

**Files:**
- Modify: `queries.py`, `stats_service.py`, `views.py`, `urls.py`
- Test: `backend/apps/daily_report/tests/test_pipeline.py`

**Interfaces:**
- Produces: `queries.open_lead_rows(as_of, lookback_days) -> list[dict]` (cột `nhan_vien_id, nhan_vien_ten, trang_thai_sale_ma, trang_thai_sale_text, tuoi`); `stats_service.pipeline(as_of, lookback_days) -> dict`, `pipeline_payload(as_of, lookback_days)`, `AGE_BUCKETS`.

- [ ] **Step 1: Test đỏ**

```python
from datetime import date

from django.test import TestCase, override_settings

from apps.users.test_helpers import make_admin

from .helpers import TOKEN, client, rebuild, seed_order

URL = "/api/daily-report/pipeline"
AS_OF = date(2026, 9, 2)


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class PipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sale = make_admin("sale-p")
        seed_order(date(2026, 9, 2), sales_status="call_back_later", staff=sale)   # tuổi 0
        seed_order(date(2026, 8, 31), sales_status="no_answer_3", staff=sale)     # tuổi 2
        seed_order(date(2026, 8, 27), sales_status=None)                           # tuổi 6, chưa gán, chưa đánh giá
        seed_order(date(2026, 8, 20), sales_status="scheduled")                    # tuổi 13
        seed_order(date(2026, 9, 1), sales_status="closed")                        # kết thúc: không tính
        seed_order(date(2026, 9, 1), sales_status="spam")                          # kết thúc
        seed_order(date(2026, 9, 1), sales_status="call_back_later", invoice_status=1)  # đã lên HĐ: không còn là lead
        seed_order(date(2026, 9, 1), sales_status="call_back_later", status=6)     # huỷ
        seed_order(date(2026, 7, 1), sales_status="call_back_later")               # ngoài lookback 30
        seed_order(date(2026, 9, 3), sales_status="call_back_later")               # sau as_of
        rebuild()

    def test_snapshot(self):
        resp = client().get(URL, {"as_of": "2026-09-02"})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["meta"]["range"], {"from": "2026-08-03", "to": "2026-09-02"})
        data = body["data"]
        self.assertEqual(data["open_leads"], 4)
        self.assertEqual(data["unassigned"], 2)
        self.assertEqual(data["unassessed"], 1)
        by_status = {r["code"]: r for r in data["by_status"]}
        self.assertEqual(by_status["call_back_later"]["count"], 1)
        self.assertEqual(by_status["call_back_later"]["label"], "Gọi lại sau")
        self.assertEqual(by_status[None]["count"], 1)
        self.assertEqual([r["bucket"] for r in data["by_age"]], ["0-1d", "2-3d", "4-7d", "8d+"])
        self.assertEqual([r["count"] for r in data["by_age"]], [1, 1, 1, 1])
        staff_rows = {(r["staff"] or {}).get("id") if r["staff"] else None: r for r in data["by_staff"]}
        self.assertEqual(staff_rows[None]["open_leads"], 2)
        self.assertEqual(staff_rows[None]["unassessed"], 1)

    def test_lookback_param(self):
        data = client().get(URL, {"as_of": "2026-09-02", "lookback_days": "5"}).json()["data"]
        self.assertEqual(data["open_leads"], 2)
        self.assertEqual(client().get(URL, {"lookback_days": "0"}).status_code, 400)
        self.assertEqual(client().get(URL, {"lookback_days": "93"}).status_code, 400)
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `TESTENV manage.py test apps.daily_report.tests.test_pipeline -v2` → 404.

- [ ] **Step 3: Query + service + view**

`queries.py`:

```python
def open_lead_rows(as_of: date, lookback_days: int) -> list[dict]:
    """Lead còn có thể chốt tính đến cuối ngày as_of. Gộp trong Python vì cần 3
    chiều (trạng thái / tuổi / nhân viên) từ cùng một tập ≤ vài nghìn dòng."""
    from datetime import timedelta

    params = {
        "from": as_of - timedelta(days=lookback_days),
        "as_of": as_of,
        "cancelled": d.CANCELLED_STATUS,
        "terminal": list(d.PIPELINE_TERMINAL_CODES),
    }
    sql = """
SELECT nhan_vien_id, nhan_vien_ten, trang_thai_sale_ma, trang_thai_sale_text,
       (%(as_of)s::date - ngay_tao_ngay)::int AS tuoi
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(as_of)s
  AND trang_thai_hoa_don_ma IS NULL
  AND trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND (trang_thai_sale_ma IS NULL OR NOT (trang_thai_sale_ma = ANY(%(terminal)s)))
"""
    return run(sql, params)
```

`stats_service.py`:

```python
from collections import Counter, defaultdict
from datetime import timedelta

AGE_BUCKETS = (("0-1d", 0, 1), ("2-3d", 2, 3), ("4-7d", 4, 7), ("8d+", 8, None))
DEFAULT_LOOKBACK_DAYS = 30


def _age_bucket(days: int) -> str:
    for name, lo, hi in AGE_BUCKETS:
        if days >= lo and (hi is None or days <= hi):
            return name
    return AGE_BUCKETS[-1][0]


def pipeline(as_of: date, lookback_days: int) -> dict:
    rows = queries.open_lead_rows(as_of, lookback_days)
    by_status: dict[tuple, int] = Counter()
    by_age = Counter()
    by_staff: dict[tuple, dict] = defaultdict(lambda: {"open_leads": 0, "unassessed": 0})
    for r in rows:
        by_status[(r["trang_thai_sale_ma"], r["trang_thai_sale_text"])] += 1
        by_age[_age_bucket(r["tuoi"])] += 1
        s = by_staff[(r["nhan_vien_id"], r["nhan_vien_ten"])]
        s["open_leads"] += 1
        if r["trang_thai_sale_ma"] is None:
            s["unassessed"] += 1
    return {
        "as_of": iso(as_of),
        "lookback_days": lookback_days,
        "open_leads": len(rows),
        "unassigned": sum(1 for r in rows if r["nhan_vien_id"] is None),
        "unassessed": sum(1 for r in rows if r["trang_thai_sale_ma"] is None),
        "by_status": [
            {"code": code, "label": label if code else "Chưa đánh giá", "count": n}
            for (code, label), n in sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0][0] or ""))
        ],
        "by_age": [{"bucket": name, "count": by_age.get(name, 0)} for name, _, _ in AGE_BUCKETS],
        "by_staff": [
            {"staff": {"id": sid, "name": name} if sid is not None else None, **agg}
            for (sid, name), agg in sorted(by_staff.items(), key=lambda kv: (-kv[1]["open_leads"], kv[0][1] or ""))
        ],
    }


def pipeline_payload(as_of: date, lookback_days: int) -> dict:
    fresh = read_freshness()
    params = {"as_of": as_of, "lookback_days": lookback_days}
    data = cached("pipeline", params, fresh, lambda: pipeline(as_of, lookback_days))
    return envelope(data, d_from=as_of - timedelta(days=lookback_days), d_to=as_of, fresh=fresh)
```

`views.py`:

```python
@daily_report_view
def pipeline(request):
    try:
        as_of = periods.parse_date_param(request.query_params, "as_of", default=periods.today_vn())
        lookback = periods.parse_int(
            request.query_params, "lookback_days",
            default=stats_service.DEFAULT_LOOKBACK_DAYS, minimum=1, maximum=periods.MAX_RANGE_DAYS,
        )
    except ParamError as exc:
        return _bad_request(exc)
    return Response(stats_service.pipeline_payload(as_of, lookback))
```

`urls.py`: `path("pipeline", pipeline, name="daily-report-pipeline")`.

- [ ] **Step 4: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report -v2` → OK.

```bash
git add apps/daily_report
git commit -m "feat(daily_report): /pipeline snapshot of open leads by status, age and staff"
```

---

### Task 7: `/summary`

**Files:**
- Modify: `stats_service.py`, `views.py`, `urls.py`
- Test: `backend/apps/daily_report/tests/test_summary.py`

**Interfaces:**
- Produces: `stats_service.daily_summary(day) -> dict`, `summary_payload(day)`.
- Consumes: `leads_stats`, `orders_stats`, `revenue`, `pipeline` từ Task 4–6.

- [ ] **Step 1: Test đỏ**

```python
from datetime import date

from django.test import TestCase, override_settings

from apps.users.test_helpers import make_admin

from .helpers import TOKEN, client, rebuild, seed_order

URL = "/api/daily-report/summary"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class SummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        a = make_admin("sale-s1")
        b = make_admin("sale-s2")
        seed_order(date(2026, 9, 2), sales_status="closed", amount=300_000, staff=a, invoice_status=1, status=3)
        seed_order(date(2026, 9, 2), sales_status="closed", amount=100_000, staff=b)
        seed_order(date(2026, 9, 2), sales_status="call_back_later", staff=b)
        seed_order(date(2026, 9, 1), sales_status="closed", amount=50_000, staff=a)        # hôm trước
        seed_order(date(2026, 8, 26), sales_status="closed", amount=70_000, staff=a)       # cùng thứ tuần trước
        rebuild()

    def test_summary_shape(self):
        resp = client().get(URL, {"date": "2026-09-02"})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["date"], "2026-09-02")
        self.assertEqual(data["leads"]["leads"], 3)
        self.assertEqual(data["leads"]["closed_revenue"], 400_000)
        self.assertEqual(data["orders"]["total_orders"], 1)
        self.assertEqual(data["revenue"]["total_revenue"], 300_000)
        self.assertEqual(data["pipeline"]["open_leads"], 1)
        cmp = data["compare"]
        self.assertEqual(cmp["previous_day"], {"date": "2026-09-01", "leads": 1, "closed": 1, "total_revenue": 0})
        self.assertEqual(cmp["same_weekday_last_week"]["date"], "2026-08-26")
        self.assertEqual(cmp["same_weekday_last_week"]["leads"], 1)
        self.assertEqual(cmp["month_to_date"], {"from": "2026-09-01", "to": "2026-09-02", "leads": 4, "closed": 3, "total_revenue": 300_000})
        top = data["top_staff_by_closed_revenue"]
        self.assertEqual([t["closed_revenue"] for t in top], [300_000, 100_000])
        self.assertEqual(top[0]["closed"], 1)
        self.assertLessEqual(len(top), 5)

    def test_bad_date(self):
        self.assertEqual(client().get(URL, {"date": "2/9/2026"}).status_code, 400)
```

- [ ] **Step 2: Chạy đỏ** → 404.

- [ ] **Step 3: Service + view**

`stats_service.py`:

```python
TOP_STAFF = 5


def _compare_block(d_from: date, d_to: date) -> dict:
    leads = leads_stats(d_from, d_to)
    rev = revenue(d_from, d_to)
    return {"leads": leads["leads"], "closed": leads["closed"], "total_revenue": rev["total_revenue"]}


def daily_summary(day: date) -> dict:
    prev = day - timedelta(days=1)
    last_week = day - timedelta(days=7)
    month_start = day.replace(day=1)
    staff_rows = [r for r in leads_stats(day, day, "staff") if r["staff"] is not None]
    top = sorted(staff_rows, key=lambda r: (-r["closed_revenue"], -r["closed"], r["staff"]["name"] or ""))[:TOP_STAFF]
    return {
        "date": iso(day),
        "leads": leads_stats(day, day),
        "orders": orders_stats(day, day),
        "revenue": revenue(day, day),
        "pipeline": pipeline(day, DEFAULT_LOOKBACK_DAYS),
        "compare": {
            "previous_day": {"date": iso(prev), **_compare_block(prev, prev)},
            "same_weekday_last_week": {"date": iso(last_week), **_compare_block(last_week, last_week)},
            "month_to_date": {"from": iso(month_start), "to": iso(day), **_compare_block(month_start, day)},
        },
        "top_staff_by_closed_revenue": [
            {"staff": r["staff"], "closed": r["closed"], "closed_revenue": r["closed_revenue"]} for r in top
        ],
    }


def summary_payload(day: date) -> dict:
    fresh = read_freshness()
    data = cached("summary", {"date": day}, fresh, lambda: daily_summary(day))
    return envelope(data, d_from=day, d_to=day, fresh=fresh)
```

`views.py`:

```python
@daily_report_view
def summary(request):
    try:
        day = periods.parse_date_param(request.query_params, "date", default=periods.yesterday_vn())
    except ParamError as exc:
        return _bad_request(exc)
    return Response(stats_service.summary_payload(day))
```

`urls.py`: `path("summary", summary, name="daily-report-summary")`.

- [ ] **Step 4: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report -v2` → OK.

```bash
git add apps/daily_report
git commit -m "feat(daily_report): /summary composing leads, orders, revenue, pipeline and comparisons"
```

---

### Task 8: `/definitions`

**Files:**
- Modify: `queries.py`, `stats_service.py`, `views.py`, `urls.py`
- Test: `backend/apps/daily_report/tests/test_definitions_api.py`

**Interfaces:**
- Produces: `queries.dictionary_rows(groups) -> list[dict]` (cột `nhom_ma, ma, nhan`), `stats_service.definitions() -> dict`, `definitions_payload()`.

- [ ] **Step 1: Test đỏ**

```python
from django.test import TestCase, override_settings

from .helpers import TOKEN, client

URL = "/api/daily-report/definitions"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class DefinitionsApiTests(TestCase):
    def test_dictionaries_and_glossary(self):
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        order_status = {r["code"]: r for r in data["order_status"]}
        self.assertEqual(order_status[3]["label"], "Giao thành công")
        self.assertEqual(order_status[3]["bucket"], "delivered")
        self.assertEqual(order_status[10]["bucket"], "return_risk")
        self.assertEqual(order_status[6]["bucket"], "cancelled")
        sales = {r["code"]: r for r in data["sales_status"]}
        self.assertEqual(sales["no_answer_5"], {"code": "no_answer_5", "label": "Không nghe máy lần 5", "bucket": "calling"})
        self.assertEqual(sales["closed"]["bucket"], "closed")
        self.assertEqual({r["code"] for r in data["invoice_status"]}, {1, 2, 3})
        self.assertIn({"code": "landing", "label": "Landing"}, data["order_source"])
        self.assertEqual(data["channel_group"], ["shopee", "tiktok", "facebook", "web", "off_other"])
        self.assertIn("close_rate", data["metrics"])
        self.assertEqual(data["excluded_branches"], ["1000000139", "1000000477"])
        self.assertIn("timezone", resp.json()["meta"])
```

- [ ] **Step 2: Chạy đỏ** → 404.

- [ ] **Step 3: Query + service + view**

`queries.py`:

```python
DICTIONARY_GROUPS = ("trang_thai_don", "trang_thai_hoa_don", "trang_thai_sale", "nguon_don")


def dictionary_rows() -> list[dict]:
    return run(
        "SELECT nhom_ma, ma, nhan FROM ai.tu_dien_ma WHERE nhom_ma = ANY(%(groups)s)"
        " ORDER BY nhom_ma, thu_tu, ma",
        {"groups": list(DICTIONARY_GROUPS)},
    )
```

`stats_service.py`:

```python
def _order_bucket(code: int) -> str:
    if code == d.CANCELLED_STATUS:
        return "cancelled"
    if code == d.DELIVERED_STATUS:
        return "delivered"
    if code in d.RETURNED_STATUSES:
        return "returned"
    if code in d.RETURN_RISK_STATUSES:
        return "return_risk"
    return "awaiting"


def definitions() -> dict:
    groups: dict[str, list[dict]] = {g: [] for g in queries.DICTIONARY_GROUPS}
    for r in queries.dictionary_rows():
        groups[r["nhom_ma"]].append({"code": r["ma"], "label": r["nhan"]})
    return {
        "order_status": [
            {"code": int(x["code"]), "label": x["label"], "bucket": _order_bucket(int(x["code"]))}
            for x in groups["trang_thai_don"]
        ],
        "invoice_status": [{"code": int(x["code"]), "label": x["label"]} for x in groups["trang_thai_hoa_don"]],
        "sales_status": [{**x, "bucket": d.bucket_of(x["code"])} for x in groups["trang_thai_sale"]],
        "order_source": groups["nguon_don"],
        "channel_group": list(d.CHANNEL_GROUPS),
        "order_buckets": d.ORDER_BUCKET_LABELS,
        "lead_buckets": list(d.LEAD_BUCKETS) + ["other"],
        "excluded_branches": list(d.EXCLUDED_BRANCHES),
        "metrics": d.METRIC_GLOSSARY,
    }


def definitions_payload() -> dict:
    fresh = read_freshness()
    today = timezone.now().astimezone(TZ).date()
    data = cache.get_or_set("daily_report:definitions", definitions, 60 * 60)
    return envelope(data, d_from=today, d_to=today, fresh=fresh)
```

`views.py`:

```python
@daily_report_view
def definitions(request):
    return Response(stats_service.definitions_payload())
```

`urls.py`: `path("definitions", definitions, name="daily-report-definitions")`.

- [ ] **Step 4: Chạy xanh, commit**

Run: `TESTENV manage.py test apps.daily_report -v2` → OK.

```bash
git add apps/daily_report
git commit -m "feat(daily_report): /definitions exposing status dictionaries, buckets and metric glossary"
```

---

### Task 9: OpenAPI — file tĩnh riêng, loại khỏi schema chính, test drift

**Files:**
- Create: `backend/apps/daily_report/openapi.py`, `backend/apps/daily_report/transport/serializers.py`, `backend/config/openapi_hooks.py`, `docs/openapi/daily-report.yaml` (sinh)
- Modify: `backend/apps/daily_report/transport/views.py` (thêm `@extend_schema`), `backend/config/settings_parts/base.py` (`PREPROCESSING_HOOKS`)
- Test: `backend/apps/daily_report/tests/test_openapi.py`

**Interfaces:**
- Produces: `apps.daily_report.openapi.SPECTACULAR_SETTINGS`, lệnh sinh file; `config.openapi_hooks.drop_daily_report_paths`.

- [ ] **Step 1: Test đỏ**

`backend/apps/daily_report/tests/test_openapi.py`:

```python
"""File OpenAPI tĩnh là thứ sếp gửi cho trợ lý — lệch code là lệch hợp đồng."""

from io import StringIO
from pathlib import Path

import yaml
from django.core.management import call_command
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator

SCHEMA_FILE = Path(__file__).resolve().parents[4] / "docs" / "openapi" / "daily-report.yaml"
REGEN = (
    "python manage.py spectacular --urlconf apps.daily_report.urls"
    " --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS"
    " --file ../docs/openapi/daily-report.yaml"
)


def _generate() -> str:
    out = StringIO()
    call_command(
        "spectacular",
        urlconf="apps.daily_report.urls",
        custom_settings="apps.daily_report.openapi.SPECTACULAR_SETTINGS",
        stdout=out,
    )
    return out.getvalue()


class OpenApiTests(SimpleTestCase):
    def test_committed_file_matches_code(self):
        self.assertTrue(SCHEMA_FILE.exists(), f"thiếu {SCHEMA_FILE}; chạy: {REGEN}")
        self.assertEqual(yaml.safe_load(SCHEMA_FILE.read_text()), yaml.safe_load(_generate()), f"lệch; chạy: {REGEN}")

    def test_all_seven_endpoints_documented_with_api_key(self):
        schema = yaml.safe_load(_generate())
        self.assertEqual(
            set(schema["paths"]),
            {"/health", "/definitions", "/leads/stats", "/orders/stats", "/revenue", "/pipeline", "/summary"},
        )
        self.assertIn("ApiKeyAuth", schema["components"]["securitySchemes"])
        for path, ops in schema["paths"].items():
            self.assertEqual(ops["get"]["security"], [{"ApiKeyAuth": []}], path)
            self.assertIn("200", ops["get"]["responses"], path)
        self.assertEqual(schema["servers"][0]["url"], "{base}/api/daily-report")

    def test_main_schema_excludes_daily_report(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        leaked = [p for p in schema["paths"] if p.startswith("/api/daily-report")]
        self.assertEqual(leaked, [])
```

- [ ] **Step 2: Chạy đỏ**

Run: `TESTENV manage.py test apps.daily_report.tests.test_openapi -v2`
Expected: FAIL (file thiếu; paths chưa có security; schema chính chứa `/api/daily-report/*`).

- [ ] **Step 3: Custom settings + hook**

`backend/apps/daily_report/openapi.py`:

```python
"""Cấu hình OpenAPI RIÊNG cho file tĩnh gửi trợ lý (drf-spectacular --custom-settings).

Sinh: python manage.py spectacular --urlconf apps.daily_report.urls
        --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS
        --file ../docs/openapi/daily-report.yaml
"""

SPECTACULAR_SETTINGS = {
    "TITLE": "CRM Daily Report API",
    "DESCRIPTION": (
        "API chỉ đọc cho trợ lý báo cáo ngày. Xác thực bằng header X-Api-Token. "
        "Ngày theo múi giờ Asia/Ho_Chi_Minh; tiền là số nguyên VND; mọi response có "
        "meta.data_as_of là mốc dữ liệu (mart cập nhật mỗi 30 phút). "
        "Định nghĩa trạng thái và chỉ số: GET /definitions."
    ),
    "VERSION": "1.0.0",
    "SERVERS": [
        {
            "url": "{base}/api/daily-report",
            "variables": {"base": {"default": "https://crm.example.vn", "description": "Domain CRM"}},
        }
    ],
    "SECURITY": [{"ApiKeyAuth": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Token"},
        }
    },
    "PREPROCESSING_HOOKS": [],
    "SCHEMA_PATH_PREFIX": "",
}
```

`backend/config/openapi_hooks.py`:

```python
"""Hook cho schema FE↔BE chính (SPECTACULAR_SETTINGS.PREPROCESSING_HOOKS)."""

DAILY_REPORT_PREFIX = "/api/daily-report"


def drop_daily_report_paths(endpoints):
    """API Daily Report có file OpenAPI riêng (docs/openapi/daily-report.yaml);
    giữ schema chính đúng là hợp đồng FE↔BE (D7)."""
    return [ep for ep in endpoints if not ep[0].startswith(DAILY_REPORT_PREFIX)]
```

`base.py` `SPECTACULAR_SETTINGS`: thêm `"PREPROCESSING_HOOKS": ["config.openapi_hooks.drop_daily_report_paths"],`.

- [ ] **Step 4: Serializer mô tả + `@extend_schema` cho 7 view**

`backend/apps/daily_report/transport/serializers.py`:

```python
"""Serializer CHỈ để drf-spectacular vẽ schema. Không dùng để validate/serialize ở runtime."""

from rest_framework import serializers

from apps.daily_report.domain import definitions as d


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


class MetaSerializer(serializers.Serializer):
    timezone = serializers.CharField()
    range = serializers.DictField(child=serializers.DateField())
    data_as_of = serializers.DateTimeField(allow_null=True, help_text="Mốc dữ liệu mart, giờ VN")
    data_status = serializers.CharField()
    definitions_url = serializers.CharField()
    generated_at = serializers.DateTimeField()


class StaffRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True)


class CodeLabelSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField(allow_null=True)


class LeadMetricsSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    source = CodeLabelSerializer(required=False, allow_null=True)
    staff = StaffRefSerializer(required=False, allow_null=True)
    channel_group = serializers.CharField(required=False)
    leads = serializers.IntegerField()
    unassigned = serializers.IntegerField()
    unassessed = serializers.IntegerField()
    closed_revenue = serializers.IntegerField(help_text="VND")
    other = serializers.IntegerField()
    close_rate = serializers.FloatField(allow_null=True)


for _bucket in d.LEAD_BUCKETS:
    LeadMetricsSerializer._declared_fields[_bucket] = serializers.IntegerField()


class OrderMetricsSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    staff = StaffRefSerializer(required=False, allow_null=True)
    channel_group = serializers.CharField(required=False)
    total_orders = serializers.IntegerField()
    delivered = serializers.IntegerField()
    awaiting = serializers.IntegerField()
    returned = serializers.IntegerField()
    return_risk = serializers.IntegerField()


class RevenueMetricsSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    channel_group = serializers.CharField(required=False)
    total_revenue = serializers.IntegerField(help_text="VND")
    delivered_revenue = serializers.IntegerField()
    awaiting_revenue = serializers.IntegerField()
    returned_revenue = serializers.IntegerField()
    return_risk_revenue = serializers.IntegerField()
    net_revenue = serializers.IntegerField()
    closed_revenue = serializers.IntegerField(help_text="Góc nhìn sale, từ tập lead cùng kỳ")


class CountRowSerializer(serializers.Serializer):
    code = serializers.CharField(allow_null=True)
    label = serializers.CharField(allow_null=True)
    count = serializers.IntegerField()


class AgeRowSerializer(serializers.Serializer):
    bucket = serializers.CharField()
    count = serializers.IntegerField()


class StaffPipelineRowSerializer(serializers.Serializer):
    staff = StaffRefSerializer(allow_null=True)
    open_leads = serializers.IntegerField()
    unassessed = serializers.IntegerField()


class PipelineSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    lookback_days = serializers.IntegerField()
    open_leads = serializers.IntegerField()
    unassigned = serializers.IntegerField()
    unassessed = serializers.IntegerField()
    by_status = CountRowSerializer(many=True)
    by_age = AgeRowSerializer(many=True)
    by_staff = StaffPipelineRowSerializer(many=True)


class CompareRowSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    from_ = serializers.DateField(required=False, source="from")
    to = serializers.DateField(required=False)
    leads = serializers.IntegerField()
    closed = serializers.IntegerField()
    total_revenue = serializers.IntegerField()


class TopStaffSerializer(serializers.Serializer):
    staff = StaffRefSerializer()
    closed = serializers.IntegerField()
    closed_revenue = serializers.IntegerField()


class SummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    leads = LeadMetricsSerializer()
    orders = OrderMetricsSerializer()
    revenue = RevenueMetricsSerializer()
    pipeline = PipelineSerializer()
    compare = serializers.DictField(child=CompareRowSerializer())
    top_staff_by_closed_revenue = TopStaffSerializer(many=True)


class DefinitionsSerializer(serializers.Serializer):
    order_status = serializers.ListField(child=serializers.DictField())
    invoice_status = serializers.ListField(child=serializers.DictField())
    sales_status = serializers.ListField(child=serializers.DictField())
    order_source = CodeLabelSerializer(many=True)
    channel_group = serializers.ListField(child=serializers.CharField())
    order_buckets = serializers.DictField(child=serializers.CharField())
    lead_buckets = serializers.ListField(child=serializers.CharField())
    excluded_branches = serializers.ListField(child=serializers.CharField())
    metrics = serializers.DictField(child=serializers.CharField())


class MartCheckSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    data_as_of = serializers.DateTimeField(allow_null=True)
    lag_minutes = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class HealthChecksSerializer(serializers.Serializer):
    db = serializers.BooleanField()
    redis = serializers.BooleanField()
    mart = MartCheckSerializer()


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "degraded"])
    checks = HealthChecksSerializer()


def envelope_of(data_serializer, *, many: bool = False, name: str):
    """Envelope {meta, data} — sinh class động để mỗi endpoint có tên component riêng."""
    return type(
        name,
        (serializers.Serializer,),
        {"meta": MetaSerializer(), "data": data_serializer(many=many)},
    )
```

Trong `views.py`, thêm import:

```python
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from apps.daily_report.transport import serializers as s

_ERRORS = {
    400: OpenApiResponse(s.ErrorSerializer, description="Tham số sai — detail nói rõ trường nào"),
    401: OpenApiResponse(s.ErrorSerializer, description="Thiếu/sai X-Api-Token"),
    503: OpenApiResponse(s.ErrorSerializer, description="Prod chưa cấu hình token, hoặc DB/mart lỗi"),
}
_FROM = OpenApiParameter("from", str, description="YYYY-MM-DD giờ VN, mặc định hôm qua, bao gồm")
_TO = OpenApiParameter("to", str, description="YYYY-MM-DD giờ VN, mặc định hôm qua, bao gồm; tối đa 92 ngày")


def _group_param(choices):
    return OpenApiParameter("group_by", str, enum=list(choices), description="Mặc định none")
```

và gắn decorator (đặt TRÊN `@daily_report_view`):

```python
@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Health: DB, Redis, độ tươi mart",
               responses={200: s.HealthSerializer, 503: s.HealthSerializer, 401: _ERRORS[401]})
@daily_report_view
def health(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Từ điển trạng thái + định nghĩa chỉ số",
               responses={200: s.envelope_of(s.DefinitionsSerializer, name="DefinitionsEnvelope"), **_ERRORS})
@daily_report_view
def definitions(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Thống kê lead theo kỳ",
               parameters=[_FROM, _TO, _group_param(stats_service.LEAD_GROUPS)],
               responses={200: s.envelope_of(s.LeadMetricsSerializer, name="LeadStatsEnvelope"), **_ERRORS})
@daily_report_view
def leads_stats(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Số đơn có hoá đơn theo 4 rổ giao hàng",
               parameters=[_FROM, _TO, _group_param(stats_service.ORDER_GROUPS)],
               responses={200: s.envelope_of(s.OrderMetricsSerializer, name="OrderStatsEnvelope"), **_ERRORS})
@daily_report_view
def orders_stats(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Doanh thu theo 4 rổ + doanh thu chốt",
               parameters=[_FROM, _TO, _group_param(stats_service.REVENUE_GROUPS)],
               responses={200: s.envelope_of(s.RevenueMetricsSerializer, name="RevenueEnvelope"), **_ERRORS})
@daily_report_view
def revenue(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Ảnh chụp lead đang mở",
               parameters=[OpenApiParameter("as_of", str, description="YYYY-MM-DD, mặc định hôm nay giờ VN"),
                           OpenApiParameter("lookback_days", int, description="1..92, mặc định 30")],
               responses={200: s.envelope_of(s.PipelineSerializer, name="PipelineEnvelope"), **_ERRORS})
@daily_report_view
def pipeline(request): ...

@extend_schema(auth=["ApiKeyAuth"], tags=["daily-report"], summary="Gói báo cáo ngày (một request)",
               parameters=[OpenApiParameter("date", str, description="YYYY-MM-DD, mặc định hôm qua giờ VN")],
               responses={200: s.envelope_of(s.SummarySerializer, name="SummaryEnvelope"), **_ERRORS})
@daily_report_view
def summary(request): ...
```

Lưu ý: response cho `group_by ≠ none` là mảng; schema mô tả object (dạng `none`). Ghi rõ trong `description` của `_group_param`: "Khi khác none, `data` là MẢNG các object cùng shape kèm khoá nhóm (date | source | staff | channel_group)."

- [ ] **Step 5: Sinh file, chạy test**

```bash
mkdir -p ../docs/openapi
TESTENV manage.py spectacular --urlconf apps.daily_report.urls --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS --file ../docs/openapi/daily-report.yaml --validate
TESTENV manage.py spectacular --file schema.yaml
git diff --stat schema.yaml
```

Expected: file mới có 7 path; `schema.yaml` **không đổi** (hook đã loại). Nếu `schema.yaml` đổi vì lý do khác (spectacular sort), commit kèm và ghi trong message.

Run: `TESTENV manage.py test apps.daily_report -v2` → OK.

- [ ] **Step 6: Commit**

```bash
git add apps/daily_report config/openapi_hooks.py config/settings_parts/base.py ../docs/openapi/daily-report.yaml schema.yaml
git commit -m "feat(daily_report): standalone OpenAPI document with ApiKeyAuth; keep main schema FE-only"
```

---

### Task 10: Tài liệu, env, compose, nginx, kiểm tra cuối

**Files:**
- Create: `docs/daily-report-api.md`
- Modify: `.env.example`, `docker-compose.local.yml`, `docker-compose.staging.yml`, `docker-compose.prod.yml`, `nginx/default.conf`, `nginx/conf.d/default.conf`, `docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md` (§ tiến độ)

- [ ] **Step 1: Env + compose**

`.env.example`, ngay sau `HERMES_THROTTLE_RATE=600/hour`:

```
# Token cho trợ lý Daily Report (Mac mini) đọc số liệu ngày qua /api/daily-report/.
# BẮT BUỘC trên production — để trống thì endpoint trả 503 (fail-closed).
# Sinh: python -c "import secrets; print(secrets.token_urlsafe(32))"
DAILY_REPORT_API_TOKEN=
DAILY_REPORT_THROTTLE_RATE=300/hour
```

Ba file compose: ngay dưới dòng `HERMES_THROTTLE_RATE: ${HERMES_THROTTLE_RATE:-600/hour}` (local:93, staging:67, prod:132) thêm:

```yaml
  DAILY_REPORT_API_TOKEN: ${DAILY_REPORT_API_TOKEN:-}
  DAILY_REPORT_THROTTLE_RATE: ${DAILY_REPORT_THROTTLE_RATE:-300/hour}
```

- [ ] **Step 2: nginx allowlist để sẵn (comment, không đổi hành vi)**

Trong `nginx/default.conf` và `nginx/conf.d/default.conf`, ngay TRƯỚC `location /api/` thêm:

```nginx
    # API Daily Report (trợ lý trên Mac mini). Bật khối này khi Mac mini có IP cố
    # định qua Tailscale/VPN tới VPS — thay <TAILSCALE_IP>. Ngoài allowlist: 404
    # (giống cách gate docs), không phải 403, để không lộ sự tồn tại của endpoint.
    # location ^~ /api/daily-report/ {
    #     allow <TAILSCALE_IP>;
    #     deny all;
    #     error_page 403 =404 /404.html;
    #     limit_req zone=desi_api_general burst=20 nodelay;
    #     proxy_pass http://backend;
    #     include /etc/nginx/proxy_params;   # dùng đúng các directive proxy của block /api/ bên dưới
    # }
```

Mở block `/api/` hiện có, chép đúng các directive `proxy_*` của nó vào comment thay cho dòng `include` nếu file không có `proxy_params`. Chạy `docker run --rm -v $PWD/nginx:/etc/nginx:ro nginx:alpine nginx -t` chỉ khi có đủ include; nếu không, bỏ qua bước kiểm tra vì khối là comment.

- [ ] **Step 3: Viết `docs/daily-report-api.md`**

Nội dung bắt buộc (anh em với `docs/hermes-call-transcript-api.md`):

```markdown
# API Daily Report cho trợ lý báo cáo ngày

Kênh **máy-tới-máy chỉ đọc** cho trợ lý Daily Report (Hermes, profile `lich-task`,
Mac mini) kéo số lead / đơn / doanh thu / pipeline. Nguồn: mart `ai.don_hang`
(apps/ai_mart, cập nhật :07 và :37 mỗi giờ). Thiết kế:
docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md.

## Hợp đồng

Tiền tố `/api/daily-report/`, header `X-Api-Token: <DAILY_REPORT_API_TOKEN>` (hoặc
`Authorization: Bearer`). File OpenAPI: `docs/openapi/daily-report.yaml` — gửi được
cho trợ lý, không chứa bí mật.

| Endpoint | Tham số | Trả về |
|---|---|---|
| `GET /health` | — | 200 ok / 503 khi DB, Redis hoặc mart lỗi/trễ > 90 phút |
| `GET /definitions` | — | từ điển trạng thái đơn / hoá đơn / sale (+bucket), nguồn, nhóm kênh, glossary chỉ số |
| `GET /leads/stats` | `from`, `to`, `group_by=none|day|source|staff|channel_group` | leads, unassigned, unassessed, 11 bucket, other, closed_revenue, close_rate |
| `GET /orders/stats` | `from`, `to`, `group_by=none|day|channel_group|staff` | total_orders, delivered, awaiting, returned, return_risk |
| `GET /revenue` | `from`, `to`, `group_by=none|day|channel_group` | tiền 4 rổ, net_revenue, closed_revenue |
| `GET /pipeline` | `as_of`, `lookback_days` (1..92, mặc định 30) | open_leads, unassigned, unassessed, by_status, by_age, by_staff |
| `GET /summary` | `date` | gói: leads + orders + revenue + pipeline + compare (hôm trước / cùng thứ tuần trước / luỹ kế tháng) + top 5 sale |

Ngày `YYYY-MM-DD` theo `Asia/Ho_Chi_Minh`, bao gồm hai đầu, mặc định hôm qua, tối đa
92 ngày. Tiền: số nguyên VND. Mọi response: `{"meta": {timezone, range, data_as_of,
data_status, definitions_url, generated_at}, "data": ...}`. `group_by ≠ none` → `data`
là mảng, mỗi phần tử kèm khoá nhóm.

## Định nghĩa số liệu (khớp màn hình CRM)
- Lead, đơn, doanh thu, bucket, close_rate: xem `GET /definitions` → `metrics`.
  Nguồn luật: `apps/orders/reporting/shared.py`; API chỉ ghép lại.
- `orders/stats` và `revenue` khớp Báo cáo tổng (`summary_overview`) — test
  `apps/daily_report/tests/test_orders_revenue.py::test_matches_summary_overview` khoá điều này.

## Những quyết định cố ý
- Token tĩnh riêng, không JWT, không dùng chung HERMES_API_TOKEN (thu hồi độc lập).
- Fail-closed trên production.
- Đọc mart, không đọc OLTP: độ trễ ≤ 30 phút, đổi lại query rẻ và có `data_as_of`.
- Cache 30 phút, key gắn mốc mart → không cần invalidation.
- Không trả SĐT/địa chỉ/transcript.

## Cạm bẫy đã biết
- `data_status` ≠ "Bình thường" nghĩa là số có thể cũ — trợ lý phải nói rõ mốc `data_as_of`.
- `closed_revenue` (sale chốt) và `total_revenue` (đã lên hoá đơn) là hai góc nhìn khác nhau, không cộng vào nhau.
- `ngay_chot` chưa backfill — API cố ý không dùng, chỉ dùng ngày tạo.

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
`apps/daily_report/tests/` — cửa xác thực, tham số, tương đương Báo cáo tổng, bất
biến 4 rổ, độ tươi, cache, throttle, drift OpenAPI. `apps/ai_mart/tests/test_don_hang.py`
— 2 cột mới + loại đơn gốc khôi phục.
```

- [ ] **Step 4: Kiểm tra cuối**

```bash
TESTENV manage.py test apps.daily_report apps.ai_mart apps.common apps.orders.tests.test_overview_report -v2
/home/desi/desi-order/.venv/bin/lint-imports
TESTENV manage.py makemigrations --check --dry-run
```

Expected: tất cả OK; `lint-imports` 0 broken; không migration thiếu. (Nếu `apps.orders.tests.test_overview_report` không tồn tại, chạy `apps.orders.tests` module có chữ `overview` — `ls apps/orders/tests | grep -i overview`.)

- [ ] **Step 5: Ghi tiến độ vào spec, commit**

Thêm vào cuối spec mục `## 11. Ghi chú tiến độ` liệt kê các commit theo task và lệnh sinh OpenAPI.

```bash
cd /home/desi/desi-order-daily-report
git add .env.example docker-compose.local.yml docker-compose.staging.yml docker-compose.prod.yml nginx/default.conf nginx/conf.d/default.conf docs/daily-report-api.md docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md docs/superpowers/plans/2026-09-03-daily-report-read-api.md
git commit -m "docs(daily_report): API contract, env wiring and nginx allowlist template"
```

Sau đó dùng skill `superpowers:finishing-a-development-branch` để chốt cách gộp nhánh.

---

## Self-review

**Spec coverage**
- §3 auth/throttle/nginx/audit → Task 2, Task 10. Audit: không miễn trừ (không làm gì thêm) ✔.
- §4.0 envelope, tham số, lỗi → Task 3, 4. §4.1 health → Task 2. §4.2 definitions → Task 3 + 8. §4.3 leads → 4. §4.4/4.5 → 5. §4.6 pipeline → 6. §4.7 summary → 7.
- §5 mart → Task 1. §6 timeout/cache → Task 4 (`run()` bọc `statement_timeout`; `cached`). §7 OpenAPI → Task 9. §8 test → mỗi task. §9 triển khai → Task 10 (env/compose/docs); bước deploy staging/prod là việc vận hành sau merge.
- Timeout → 503 "Truy vấn quá lâu": `run()` để `OperationalError` thoát, view bọc bằng `_run_payload` (Task 4 Step 5), áp cho cả 6 view dữ liệu.

**Placeholder scan**: không còn TBD/TODO; mọi bước code có mã.

**Type consistency**: `format_key` dùng ở Task 4/5; `_key_tuple` chỉ Task 5; `Freshness.as_dict` Task 2 dùng ở view health; `LEAD_BUCKETS` khớp giữa definitions, queries (`_bucket_select`), service (`_lead_metrics`) và serializers. `periods.MAX_RANGE_DAYS` dùng làm trần `lookback_days` (92) — khớp spec.
