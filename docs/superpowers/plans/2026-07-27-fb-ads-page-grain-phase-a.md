# Chi phí Facebook Ads theo Page (Giai đoạn A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kéo chi tiêu Facebook Ads ở grain **ad × ngày**, suy ra page của từng ad, và cho báo cáo chi phí theo page — thay cho cách chia đều `spend/N` hiện tại.

**Architecture:** Ba bảng mới trong app `facebook_ads`: `facebook_pages` (danh mục page), `facebook_ads` (danh mục ad, FK sang page), `facebook_ad_daily_spends` (chi tiêu grain ad). Hai cron tách nhau: catalog 1 giờ/lần, chi tiêu theo nhịp FB hiện có. Bảng account-grain cũ **giữ nguyên** làm đối chứng. Endpoint báo cáo đặt trong app `facebook_ads` (tầng trên `orders`) để không sinh nợ layering mới.

**Tech Stack:** Django 5 / DRF, Python 3.12, PostgreSQL, Celery + beat, pytest + Django `TestCase`, prometheus_client (optional), React + Vite + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-07-27-fb-ads-page-grain-design.md`

**Phạm vi:** Chỉ Giai đoạn A (§2.1 của spec) — chi phí. Giai đoạn B (doanh thu, attribution `ad_id` từ landing) có plan riêng, viết sau khi sprint B0 kiểm chứng xong param sống sót qua redirect.

## Global Constraints

- Python 3.12, Django 5. Migration app `facebook_ads` tiếp theo là `0005_*`; app `orders` là `0110_*`.
- **Layering (`backend/.importlinter`)**: `apps.facebook_ads` nằm TRÊN `apps.orders` → facebook_ads được import orders, **không** có chiều ngược lại. Mọi code/test mới cần cả hai app phải đặt trong `apps/facebook_ads/`. Danh sách `ignore_imports` là nợ cũ — **không thêm entry mới**.
- **Lint bắt buộc** (CI đỏ nếu vi phạm): `ruff check .` theo `backend/pyproject.toml`.
- Comment trong code viết tiếng Việt, giải thích **tại sao**, theo đúng văn phong file hiện có.
- Không dùng `cache.clear()` trong test — sẽ FLUSHDB Redis thật.
- Quy ước đặt tên (spec §4.1.1): tiền tố `fb_` = ID bên Facebook; không tiền tố = khoá nội bộ. `facebook_ads.fb_ad_id` là chuỗi ID FB, `facebook_ad_daily_spends.ad_id` là FK nội bộ.
- Timezone báo cáo: `Asia/Bangkok` (hằng `ADS_TIME_ZONE` trong `lark_ads_report_sync.py`).

**Lệnh chạy test** (thay `<db>` bằng tên DB riêng cho worktree này, ví dụ `desi_page_grain`):

```bash
cd <worktree> && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=<db> \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lần chạy đầu dùng `--create-db`; các lần sau bỏ đi cho nhanh. Lint:

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

---

## File Structure

**Backend — tạo mới:**

- `backend/apps/facebook_ads/migrations/0005_page_grain.py` — 3 bảng mới.
- `backend/apps/orders/migrations/0110_order_fb_ad_id.py` — cột `fb_ad_id` + partial index.
- `backend/apps/facebook_ads/ad_catalog.py` — hàm thuần `resolve_page_id`, `parse_ad_payload`. Không I/O, test không cần mock.
- `backend/apps/facebook_ads/infrastructure/ad_catalog_sync.py` — gọi Graph API, upsert catalog.
- `backend/apps/facebook_ads/infrastructure/ad_spend_sync.py` — insights `level=ad`, upsert chi tiêu, chốt so khớp.
- `backend/apps/facebook_ads/management/commands/backfill_fb_ad_spend.py` — backfill chia theo tháng.
- `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py` — endpoint báo cáo.
- `backend/apps/facebook_ads/page_report.py` — SQL tổng hợp grain page.
- Tests: `test_ad_catalog_parse.py`, `test_ad_catalog_sync.py`, `test_ad_spend_sync.py`, `test_spend_variance.py`, `test_backfill_command.py`, `test_page_report_api.py` (đều trong `backend/apps/facebook_ads/tests/`).

**Backend — sửa:**

- `backend/apps/facebook_ads/models.py` — 3 model mới.
- `backend/apps/orders/models.py` — `Order.fb_ad_id` + index.
- `backend/apps/facebook_ads/tasks.py` — 2 task mới.
- `backend/apps/facebook_ads/urls.py` — route báo cáo.
- `backend/config/settings_parts/celery.py` — 1 beat entry + queue routing.
- `backend/apps/orders/_repositories_reports.py` — thêm classmethod công khai `accessible_ads_ad_account_ids`.

**Frontend — tạo mới:**

- `frontend/src/lib/api/facebook_ads_page_report.ts` — client gọi endpoint mới.
- `frontend/src/features/ads/components/PageCostTable.tsx` — bảng chi phí theo page.
- `frontend/src/features/ads/__tests__/page-report.test.ts`.

**Frontend — sửa:**

- `frontend/src/features/ads/pages/AdsReportPage.tsx` — bộ chọn chiều Kênh/Page.

---

# SPRINT A1 — Schema

## Task 1: Ba bảng mới + cột `orders.fb_ad_id`

**Files:**
- Modify: `backend/apps/facebook_ads/models.py` (thêm cuối file, sau `FacebookAdAccountDailySpend`)
- Modify: `backend/apps/orders/models.py:262` (thêm `fb_ad_id` cạnh `url_page`) và block `indexes` trong `Order.Meta`
- Create: `backend/apps/facebook_ads/migrations/0005_page_grain.py` (sinh bằng `makemigrations`)
- Create: `backend/apps/orders/migrations/0110_order_fb_ad_id.py` (sinh bằng `makemigrations`)
- Test: `backend/apps/facebook_ads/tests/test_page_grain_models.py`

**Interfaces:**
- Consumes: `FacebookAdAccount` (đã có, `models.py:72`)
- Produces: `FacebookPage`, `FacebookAd`, `FacebookAdDailySpend` — mọi task sau đều dùng. `Order.fb_ad_id: str`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_page_grain_models.py`:

```python
"""Ràng buộc schema grain ad — unique key và quan hệ page nullable."""

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
    FacebookPage,
)


class PageGrainModelTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.page = FacebookPage.objects.create(fb_page_id="111", name="Page A")

    def test_ad_can_exist_without_page(self):
        """Ad chưa resolve được page vẫn phải lưu được — mất page còn hơn mất chi phí."""
        ad = FacebookAd.objects.create(fb_ad_id="222", ad_account=self.account, page=None)
        self.assertIsNone(ad.page_id)

    def test_deleting_page_keeps_ad(self):
        """SET_NULL: xoá page không được kéo theo chi tiêu của ad."""
        ad = FacebookAd.objects.create(fb_ad_id="333", ad_account=self.account, page=self.page)
        self.page.delete()
        ad.refresh_from_db()
        self.assertIsNone(ad.page_id)

    def test_daily_spend_unique_per_ad_and_date(self):
        ad = FacebookAd.objects.create(fb_ad_id="444", ad_account=self.account, page=self.page)
        FacebookAdDailySpend.objects.create(ad=ad, date=date(2026, 7, 1), spend="10.00")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacebookAdDailySpend.objects.create(ad=ad, date=date(2026, 7, 1), spend="20.00")

    def test_fb_ad_id_unique_globally(self):
        FacebookAd.objects.create(fb_ad_id="555", ad_account=self.account)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacebookAd.objects.create(fb_ad_id="555", ad_account=self.account)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_page_grain_models.py -q --create-db`
Expected: FAIL — `ImportError: cannot import name 'FacebookPage'`

- [ ] **Step 3: Thêm 3 model**

Thêm vào cuối `backend/apps/facebook_ads/models.py`:

```python
class FacebookPage(models.Model):
    """Danh mục fanpage. Không gắn FK app: một page có thể được nhiều BM chạy quảng cáo."""

    fb_page_id = models.CharField(max_length=64, unique=True, verbose_name="Page ID")
    name = models.CharField(max_length=255, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Facebook Page"
        verbose_name_plural = "Facebook Pages"
        db_table = "facebook_pages"

    def __str__(self):
        return self.name or f"Page {self.fb_page_id}"


class FacebookAd(models.Model):
    """Danh mục quảng cáo — cầu nối giữa chi tiêu (grain ad) và page.

    ``page`` nullable vì ad đã xoá trên FB vẫn còn chi tiêu lịch sử trong insights
    nhưng không còn tra được creative để suy ra page (xem spec §5.4).
    """

    fb_ad_id = models.CharField(max_length=64, unique=True, verbose_name="Ad ID")
    ad_account = models.ForeignKey(
        FacebookAdAccount, on_delete=models.CASCADE, related_name="ads",
    )
    page = models.ForeignKey(
        FacebookPage, on_delete=models.SET_NULL, null=True, blank=True, related_name="ads",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    campaign_id = models.CharField(max_length=64, blank=True, default="")
    campaign_name = models.CharField(max_length=255, blank=True, default="")
    adset_id = models.CharField(max_length=64, blank=True, default="")
    adset_name = models.CharField(max_length=255, blank=True, default="")
    # Lần cuối ad còn xuất hiện trong /ads. Ad ngừng chạy vẫn giữ lại để chi tiêu
    # lịch sử không mất chỗ bám.
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Facebook Ad"
        verbose_name_plural = "Facebook Ads"
        db_table = "facebook_ads"
        indexes = [
            models.Index(fields=["ad_account", "page"], name="fb_ad_acc_page_idx"),
            models.Index(fields=["page"], name="fb_ad_page_idx"),
        ]

    def __str__(self):
        return f"{self.name or self.fb_ad_id}"


class FacebookAdDailySpend(models.Model):
    ad = models.ForeignKey(FacebookAd, on_delete=models.CASCADE, related_name="daily_spends")
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    # Lưu nhưng KHÔNG cộng dồn khi hiển thị — reach không cộng được giữa các ngày
    # (spec §9).
    reach = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facebook Ad Daily Spend"
        verbose_name_plural = "Facebook Ad Daily Spends"
        db_table = "facebook_ad_daily_spends"
        constraints = [
            models.UniqueConstraint(fields=["ad", "date"], name="fb_ad_spend_ad_date_uniq"),
        ]
        indexes = [
            models.Index(fields=["date"], name="fb_ad_spend_date_idx"),
        ]

    def __str__(self):
        return f"{self.ad_id} @ {self.date}: {self.spend}"
```

- [ ] **Step 4: Thêm `Order.fb_ad_id`**

Trong `backend/apps/orders/models.py`, ngay dưới dòng `url_page` (dòng 262):

```python
    # ID quảng cáo Facebook sinh ra đơn này, lấy từ query param `ad_id` trên URL
    # landing. Rỗng = không xác định được (đơn trước go-live, đơn messenger/hotline,
    # hoặc landing chưa gắn tag). Join thẳng với facebook_ads.fb_ad_id.
    fb_ad_id = models.CharField(max_length=64, blank=True, default="", db_default="")
```

Thêm vào `Order.Meta.indexes`:

```python
            # Tập đơn có ad_id nhỏ hơn nhiều toàn bảng → partial index.
            models.Index(
                fields=["fb_ad_id"],
                condition=Q(fb_ad_id__gt=""),
                name="ord_fb_ad_id_idx",
            ),
```

`db_default=""` là bắt buộc: Postgres 11+ thêm cột có default không-volatile mà không rewrite bảng — migration trên `orders` production sẽ không khoá bảng lâu.

- [ ] **Step 5: Sinh migration**

```bash
docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e DJANGO_SECRET_KEY=x \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations facebook_ads orders
```

Đổi tên file sinh ra thành `0005_page_grain.py` và `0110_order_fb_ad_id.py`, sửa `name=` trong class `Migration` cho khớp nếu cần.

- [ ] **Step 6: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_page_grain_models.py -q --create-db`
Expected: 4 passed

- [ ] **Step 7: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/facebook_ads/models.py backend/apps/facebook_ads/migrations/0005_page_grain.py \
        backend/apps/orders/models.py backend/apps/orders/migrations/0110_order_fb_ad_id.py \
        backend/apps/facebook_ads/tests/test_page_grain_models.py
git commit -m "feat(facebook-ads): add page/ad/daily-spend tables and order ad_id column"
```

---

# SPRINT A2 — Đồng bộ catalog

## Task 2: Hàm thuần đọc payload ad → page

**Files:**
- Create: `backend/apps/facebook_ads/ad_catalog.py`
- Test: `backend/apps/facebook_ads/tests/test_ad_catalog_parse.py`

**Interfaces:**
- Produces: `resolve_page_id(ad: dict) -> str` (rỗng nếu không suy ra được), `parse_ad_payload(ad: dict) -> dict` với khoá `fb_ad_id, name, campaign_id, campaign_name, adset_id, adset_name, fb_page_id`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_ad_catalog_parse.py`:

```python
"""Suy page_id từ payload /ads. Ba nguồn, thử theo thứ tự ưu tiên."""

from django.test import SimpleTestCase

from apps.facebook_ads.ad_catalog import parse_ad_payload, resolve_page_id


class ResolvePageIdTests(SimpleTestCase):
    def test_from_effective_object_story_id(self):
        ad = {"creative": {"effective_object_story_id": "111_999"}}
        self.assertEqual(resolve_page_id(ad), "111")

    def test_falls_back_to_object_story_spec(self):
        ad = {"creative": {"object_story_spec": {"page_id": "222"}}}
        self.assertEqual(resolve_page_id(ad), "222")

    def test_falls_back_to_promoted_object(self):
        """Campaign tin nhắn/lead: page nằm ở adset chứ không ở creative."""
        ad = {"adset": {"promoted_object": {"page_id": "333"}}}
        self.assertEqual(resolve_page_id(ad), "333")

    def test_prefers_story_id_over_others(self):
        ad = {
            "creative": {
                "effective_object_story_id": "111_999",
                "object_story_spec": {"page_id": "222"},
            },
            "adset": {"promoted_object": {"page_id": "333"}},
        }
        self.assertEqual(resolve_page_id(ad), "111")

    def test_returns_empty_when_nothing_resolves(self):
        self.assertEqual(resolve_page_id({}), "")
        self.assertEqual(resolve_page_id({"creative": {}}), "")

    def test_ignores_non_numeric_values(self):
        """Graph API thỉnh thoảng trả chuỗi lạ — không được nhận làm page id."""
        ad = {"creative": {"effective_object_story_id": "abc_999"}}
        self.assertEqual(resolve_page_id(ad), "")


class ParseAdPayloadTests(SimpleTestCase):
    def test_extracts_all_fields(self):
        ad = {
            "id": "120210",
            "name": "Ad A",
            "campaign": {"id": "c1", "name": "Camp A"},
            "adset": {"id": "s1", "name": "Set A"},
            "creative": {"effective_object_story_id": "111_999"},
        }
        self.assertEqual(
            parse_ad_payload(ad),
            {
                "fb_ad_id": "120210",
                "name": "Ad A",
                "campaign_id": "c1",
                "campaign_name": "Camp A",
                "adset_id": "s1",
                "adset_name": "Set A",
                "fb_page_id": "111",
            },
        )

    def test_missing_optional_fields_become_empty_strings(self):
        self.assertEqual(
            parse_ad_payload({"id": "1"}),
            {
                "fb_ad_id": "1",
                "name": "",
                "campaign_id": "",
                "campaign_name": "",
                "adset_id": "",
                "adset_name": "",
                "fb_page_id": "",
            },
        )
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_parse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.facebook_ads.ad_catalog'`

- [ ] **Step 3: Viết implementation**

Tạo `backend/apps/facebook_ads/ad_catalog.py`:

```python
"""Đọc payload /ads của Graph API thành dữ liệu catalog.

Thuần hàm, không I/O — test chạy không cần mock HTTP.
"""


def resolve_page_id(ad: dict) -> str:
    """Suy page_id của một ad. Trả "" nếu không nguồn nào cho ra id hợp lệ.

    Ba nguồn, thử theo thứ tự:
      1. ``creative.effective_object_story_id`` — dạng ``{page_id}_{post_id}``.
      2. ``creative.object_story_spec.page_id``.
      3. ``adset.promoted_object.page_id`` — campaign tin nhắn/lead không có creative
         gắn bài viết nên hai nguồn trên rỗng.

    Chỉ nhận chuỗi toàn chữ số: Graph API thỉnh thoảng trả placeholder dạng text,
    lấy bừa sẽ tạo ra page rác trong danh mục.
    """
    creative = ad.get("creative") or {}

    story_id = str(creative.get("effective_object_story_id") or "")
    if "_" in story_id:
        candidate = story_id.split("_", 1)[0]
        if candidate.isdigit():
            return candidate

    spec_page = str((creative.get("object_story_spec") or {}).get("page_id") or "")
    if spec_page.isdigit():
        return spec_page

    promoted = (ad.get("adset") or {}).get("promoted_object") or {}
    promo_page = str(promoted.get("page_id") or "")
    if promo_page.isdigit():
        return promo_page

    return ""


def parse_ad_payload(ad: dict) -> dict:
    """Payload một ad → dict phẳng khớp field của model ``FacebookAd``."""
    campaign = ad.get("campaign") or {}
    adset = ad.get("adset") or {}
    return {
        "fb_ad_id": str(ad.get("id") or ""),
        "name": str(ad.get("name") or ""),
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": str(campaign.get("name") or ""),
        "adset_id": str(adset.get("id") or ""),
        "adset_name": str(adset.get("name") or ""),
        "fb_page_id": resolve_page_id(ad),
    }
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_parse.py -q`
Expected: 8 passed

- [ ] **Step 5: Lint + commit**

```bash
git add backend/apps/facebook_ads/ad_catalog.py backend/apps/facebook_ads/tests/test_ad_catalog_parse.py
git commit -m "feat(facebook-ads): parse ad payload into catalog fields"
```

## Task 3: Đồng bộ catalog từ Graph API

**Files:**
- Create: `backend/apps/facebook_ads/infrastructure/ad_catalog_sync.py`
- Test: `backend/apps/facebook_ads/tests/test_ad_catalog_sync.py`

**Interfaces:**
- Consumes: `parse_ad_payload` (Task 2); `FacebookAd`, `FacebookPage` (Task 1); `_client`, `_error_from_response`, `GRAPH_API_BASE` từ `apps.facebook_ads.services`.
- Produces: `sync_ad_catalog_for_app(app: FacebookApp) -> dict` với khoá `ads_upserted`, `pages_upserted`, `unresolved_pages`. `PAGE_NAME_REFRESH_HOURS = 24`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_ad_catalog_sync.py`:

```python
"""Đồng bộ catalog ad → page: upsert, idempotent, và không mất ad khi thiếu page."""

from unittest.mock import patch

from django.test import TestCase

from apps.facebook_ads.infrastructure import ad_catalog_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookApp,
    FacebookPage,
)


def _ad(ad_id: str, page_id: str = "111", name: str = "Ad") -> dict:
    payload = {"id": ad_id, "name": name, "campaign": {"id": "c1", "name": "Camp"}}
    if page_id:
        payload["creative"] = {"effective_object_story_id": f"{page_id}_999"}
    return payload


class SyncAdCatalogTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )

    def test_creates_ads_and_pages(self):
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1"), _ad("2")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
            result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["ads_upserted"], 2)
        self.assertEqual(FacebookPage.objects.get(fb_page_id="111").name, "Page A")
        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").page.fb_page_id, "111")

    def test_is_idempotent(self):
        for _ in range(2):
            with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
                 patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
                ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(FacebookAd.objects.count(), 1)
        self.assertEqual(FacebookPage.objects.count(), 1)

    def test_keeps_ad_when_page_unresolved(self):
        """Không suy ra page thì vẫn phải lưu ad — chi tiêu cần chỗ bám."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", page_id="")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["unresolved_pages"], 1)
        self.assertIsNone(FacebookAd.objects.get(fb_ad_id="1").page_id)

    def test_updates_name_on_rename(self):
        """Đổi tên trên Ads Manager thì lượt sync sau phải ghi đè."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", name="Cũ")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", name="Mới")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").name, "Mới")

    def test_skips_page_name_fetch_when_recently_synced(self):
        """Tên page hiếm đổi — không gọi lại API mỗi giờ."""
        FacebookPage.objects.create(fb_page_id="111", name="Page A", last_synced_at=None)
        FacebookPage.objects.filter(fb_page_id="111").update(
            last_synced_at=ad_catalog_sync.timezone.now(),
        )
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names") as fetch_names:
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        fetch_names.assert_not_called()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: ... ad_catalog_sync`

- [ ] **Step 3: Viết implementation**

Tạo `backend/apps/facebook_ads/infrastructure/ad_catalog_sync.py`:

```python
"""Đồng bộ danh mục quảng cáo và page từ Graph API.

Tách khỏi ``services.py`` (đang lo chi tiêu grain account) để hai luồng có nhịp
cron và giới hạn thời gian riêng — spec §5.
"""

import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.facebook_ads.ad_catalog import parse_ad_payload
from apps.facebook_ads.models import FacebookAd, FacebookAdAccount, FacebookApp, FacebookPage
from apps.facebook_ads.services import (
    GRAPH_API_BASE,
    _client,
    _error_from_response,
)

logger = logging.getLogger(__name__)

AD_FIELDS = (
    "id,name,"
    "campaign{id,name},"
    "adset{id,name,promoted_object},"
    "creative{id,effective_object_story_id,object_story_spec{page_id}}"
)
AD_PAGE_LIMIT = 200
# Tên page hiếm khi đổi — refresh mỗi 24h thay vì mỗi lượt cron 1h.
PAGE_NAME_REFRESH_HOURS = 24


def _fetch_ads(ad_account: FacebookAdAccount) -> list[dict]:
    """Kéo toàn bộ ad của một TKQC, đi hết phân trang."""
    url = f"{GRAPH_API_BASE}/{ad_account.fb_id}/ads"
    params = {
        "fields": AD_FIELDS,
        "access_token": ad_account.app.access_token,
        "limit": AD_PAGE_LIMIT,
    }
    results: list[dict] = []
    next_url: str | None = None
    while True:
        response = (
            _client.get(next_url, timeout=60)
            if next_url
            else _client.get(url, params=params, timeout=60)
        )
        if response.status_code != 200:
            raise _error_from_response(response, "Graph API error (ads)")
        payload = response.json()
        results.extend(payload.get("data") or [])
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url:
            break
    return results


def _fetch_page_names(app: FacebookApp, page_ids: list[str]) -> dict[str, str]:
    """Lấy tên nhiều page trong một lời gọi Batch. Page lỗi bị bỏ qua, không raise.

    Một page bị hạn chế quyền không được phép làm hỏng cả lượt sync — ad vẫn map
    được vào page, chỉ là hiển thị bằng id cho tới lượt sau.
    """
    if not page_ids:
        return {}
    names: dict[str, str] = {}
    for i in range(0, len(page_ids), 50):
        chunk = page_ids[i : i + 50]
        batch = [
            {"method": "GET", "name": f"page_{pid}", "relative_url": f"{pid}?fields=name"}
            for pid in chunk
        ]
        response = _client.post(
            f"{GRAPH_API_BASE}/",
            data={
                "access_token": app.access_token,
                "batch": json.dumps(batch),
                "include_headers": "false",
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise _error_from_response(response, "Batch error (pages)")
        for pid, sub in zip(chunk, response.json() or []):
            if not sub or sub.get("code") != 200:
                continue
            try:
                body = json.loads(sub.get("body") or "{}")
            except (TypeError, ValueError):
                continue
            if body.get("name"):
                names[pid] = str(body["name"])
    return names


def _pages_needing_name_refresh(page_ids: set[str]) -> list[str]:
    """Page chưa có trong DB, hoặc đã quá hạn refresh tên."""
    stale_before = timezone.now() - timedelta(hours=PAGE_NAME_REFRESH_HOURS)
    fresh = set(
        FacebookPage.objects.filter(
            fb_page_id__in=page_ids, last_synced_at__gte=stale_before,
        ).values_list("fb_page_id", flat=True)
    )
    return sorted(page_ids - fresh)


@transaction.atomic
def _upsert_catalog(
    ad_account: FacebookAdAccount, parsed: list[dict], page_names: dict[str, str],
) -> dict:
    now = timezone.now()

    page_by_fb_id: dict[str, FacebookPage] = {}
    for fb_page_id in {p["fb_page_id"] for p in parsed if p["fb_page_id"]}:
        defaults = {"last_synced_at": now}
        if fb_page_id in page_names:
            defaults["name"] = page_names[fb_page_id]
        page, _ = FacebookPage.objects.update_or_create(
            fb_page_id=fb_page_id, defaults=defaults,
        )
        page_by_fb_id[fb_page_id] = page

    unresolved = 0
    for item in parsed:
        if not item["fb_ad_id"]:
            continue
        page = page_by_fb_id.get(item["fb_page_id"])
        if page is None:
            unresolved += 1
        FacebookAd.objects.update_or_create(
            fb_ad_id=item["fb_ad_id"],
            defaults={
                "ad_account": ad_account,
                "page": page,
                "name": item["name"],
                "campaign_id": item["campaign_id"],
                "campaign_name": item["campaign_name"],
                "adset_id": item["adset_id"],
                "adset_name": item["adset_name"],
                "last_seen_at": now,
            },
        )

    return {
        "ads_upserted": len([p for p in parsed if p["fb_ad_id"]]),
        "pages_upserted": len(page_by_fb_id),
        "unresolved_pages": unresolved,
    }


def sync_ad_catalog_for_app(app: FacebookApp) -> dict:
    """Đồng bộ catalog ad + page cho mọi TKQC active của một app."""
    accounts = list(
        FacebookAdAccount.objects.filter(app=app, status=FacebookAdAccount.STATUS_ACTIVE)
    )
    total = {"ads_upserted": 0, "pages_upserted": 0, "unresolved_pages": 0}
    for account in accounts:
        parsed = [parse_ad_payload(ad) for ad in _fetch_ads(account)]
        wanted = {p["fb_page_id"] for p in parsed if p["fb_page_id"]}
        page_names = _fetch_page_names(app, _pages_needing_name_refresh(wanted))
        result = _upsert_catalog(account, parsed, page_names)
        for key in total:
            total[key] += result[key]
    total["accounts"] = len(accounts)
    return total
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_sync.py -q`
Expected: 5 passed

- [ ] **Step 5: Lint + commit**

```bash
git add backend/apps/facebook_ads/infrastructure/ad_catalog_sync.py \
        backend/apps/facebook_ads/tests/test_ad_catalog_sync.py
git commit -m "feat(facebook-ads): sync ad catalog and resolve page per ad"
```

## Task 4: Celery task + beat cho catalog

**Files:**
- Modify: `backend/apps/facebook_ads/tasks.py` (thêm cuối file)
- Modify: `backend/config/settings_parts/celery.py:50` (routing) và `CELERY_BEAT_SCHEDULE`
- Test: `backend/apps/facebook_ads/tests/test_ad_catalog_task.py`

**Interfaces:**
- Consumes: `sync_ad_catalog_for_app` (Task 3)
- Produces: task `apps.facebook_ads.tasks.sync_all_ad_catalog`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_ad_catalog_task.py`:

```python
"""Task catalog: bỏ qua app token hỏng, không để lỗi một app chặn app còn lại."""

from unittest.mock import patch

from django.test import TestCase

from apps.facebook_ads import tasks
from apps.facebook_ads.models import FacebookApp
from apps.facebook_ads.services import FacebookGraphError


class SyncAllAdCatalogTests(TestCase):
    def setUp(self):
        self.ok_app = FacebookApp.objects.create(
            name="OK", app_id="1", app_secret="s", access_token="t",
        )
        self.dead_app = FacebookApp.objects.create(
            name="Dead", app_id="2", app_secret="s", access_token="t",
            token_status=FacebookApp.TOKEN_INVALID,
        )

    def test_skips_app_with_invalid_token(self):
        """Token chết thì gọi API bao nhiêu lần cũng vô nghĩa — ngắt mạch."""
        with patch.object(tasks, "sync_ad_catalog_for_app", return_value={
            "accounts": 1, "ads_upserted": 3, "pages_upserted": 1, "unresolved_pages": 0,
        }) as sync:
            summary = tasks._sync_all_ad_catalog()

        self.assertEqual(summary["skipped_apps"], 1)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args[0][0].pk, self.ok_app.pk)

    def test_one_app_failing_does_not_stop_others(self):
        other = FacebookApp.objects.create(
            name="Other", app_id="3", app_secret="s", access_token="t",
        )
        calls = []

        def _side_effect(app):
            calls.append(app.pk)
            if app.pk == self.ok_app.pk:
                raise FacebookGraphError("rate limit")
            return {"accounts": 1, "ads_upserted": 1, "pages_upserted": 1, "unresolved_pages": 0}

        with patch.object(tasks, "sync_ad_catalog_for_app", side_effect=_side_effect):
            summary = tasks._sync_all_ad_catalog()

        self.assertIn(other.pk, calls)
        self.assertEqual(len(summary["errors"]), 1)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_task.py -q`
Expected: FAIL — `AttributeError: module 'apps.facebook_ads.tasks' has no attribute '_sync_all_ad_catalog'`

- [ ] **Step 3: Viết implementation**

Thêm import ở đầu `backend/apps/facebook_ads/tasks.py`:

```python
from .infrastructure.ad_catalog_sync import sync_ad_catalog_for_app
```

Thêm vào cuối file:

```python
@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_all_ad_catalog",
    soft_time_limit=600,
    time_limit=660,
)
def sync_all_ad_catalog(self) -> dict:
    """Cron 1h: làm mới danh mục ad + page. Tách khỏi luồng chi tiêu vì nhịp khác hẳn."""
    with single_flight("lock:fb_sync_ad_catalog", 700) as acquired:
        if not acquired:
            logger.info("sync_all_ad_catalog already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_ad_catalog()


def _sync_all_ad_catalog() -> dict:
    summary: dict = {
        "apps": 0, "accounts": 0, "ads_upserted": 0,
        "pages_upserted": 0, "unresolved_pages": 0,
        "skipped_apps": 0, "errors": [],
    }
    for app in FacebookApp.objects.filter(deleted_at__isnull=True):
        if app.token_status == FacebookApp.TOKEN_INVALID:
            summary["skipped_apps"] += 1
            continue
        try:
            result = sync_ad_catalog_for_app(app)
            summary["apps"] += 1
            for key in ("accounts", "ads_upserted", "pages_upserted", "unresolved_pages"):
                summary[key] += result.get(key, 0)
        except FacebookTokenError as exc:
            logger.warning("sync_ad_catalog: token app %s bị từ chối: %s", app.id, exc)
            mark_token_invalid(app, str(exc))
            _alert_token_invalid(app, str(exc))
            summary["errors"].append({"app_id": app.id, "error": str(exc), "kind": "token"})
        except FacebookGraphError as exc:
            logger.warning("sync_ad_catalog failed for app %s: %s", app.id, exc)
            summary["errors"].append({"app_id": app.id, "error": str(exc)})
        except Exception:
            logger.exception("Unexpected error syncing ad catalog for app %s", app.id)
            summary["errors"].append({"app_id": app.id, "error": "unexpected"})
    return summary
```

- [ ] **Step 4: Đăng ký routing + beat**

Trong `backend/config/settings_parts/celery.py`, thêm vào `CELERY_TASK_ROUTES` cạnh dòng 50:

```python
    "apps.facebook_ads.tasks.sync_all_ad_catalog": {"queue": CELERY_TASK_DEFAULT_QUEUE},
```

Thêm vào `CELERY_BEAT_SCHEDULE`, ngay sau entry `facebook-ads-sync-daily-spend`:

```python
    # Danh mục ad/page đổi chậm hơn chi tiêu nhiều — 1h là đủ, và giữ cho lượt
    # đồng bộ chi tiêu (nhịp 300s) không phải gánh thêm việc kéo catalog.
    "facebook-ads-sync-ad-catalog": {
        "task": "apps.facebook_ads.tasks.sync_all_ad_catalog",
        "schedule": crontab(minute=5),
    },
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_catalog_task.py -q`
Expected: 2 passed

- [ ] **Step 6: Lint + commit**

```bash
git add backend/apps/facebook_ads/tasks.py backend/config/settings_parts/celery.py \
        backend/apps/facebook_ads/tests/test_ad_catalog_task.py
git commit -m "feat(facebook-ads): schedule hourly ad catalog sync"
```

---

# SPRINT A3 — Chi tiêu grain ad

## Task 5: Đồng bộ chi tiêu level=ad

**Files:**
- Create: `backend/apps/facebook_ads/infrastructure/ad_spend_sync.py`
- Test: `backend/apps/facebook_ads/tests/test_ad_spend_sync.py`

**Interfaces:**
- Consumes: `FacebookAd`, `FacebookAdDailySpend` (Task 1); `fetch_insights_batch`, `_log_batch_errors` từ `apps.facebook_ads.services`.
- Produces: `sync_ad_spend_for_app(app, *, since=None, until=None) -> dict` với khoá `rows_upserted`, `lazy_fetched_ads`, `failed_accounts`. Hằng `AD_SPEND_RECENT_DAYS = 7`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_ad_spend_sync.py`:

```python
"""Chi tiêu grain ad: upsert idempotent, cửa sổ gồm hôm nay, không mất chi tiêu ad lạ."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.facebook_ads.infrastructure import ad_spend_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
)


class SyncAdSpendTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.ad = FacebookAd.objects.create(fb_ad_id="A1", ad_account=self.account)
        self.today = timezone.localdate()

    def _row(self, ad_id="A1", spend="100.00"):
        return {
            "date_start": self.today.isoformat(),
            "ad_id": ad_id,
            "spend": spend,
            "impressions": "10",
            "clicks": "2",
            "reach": "8",
        }

    def test_upserts_rows_for_known_ad(self):
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row()]}):
            result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(result["rows_upserted"], 1)
        spend = FacebookAdDailySpend.objects.get(ad=self.ad, date=self.today)
        self.assertEqual(str(spend.spend), "100.00")

    def test_is_idempotent(self):
        for _ in range(2):
            with patch.object(ad_spend_sync, "fetch_insights_batch",
                              return_value={self.account.id: [self._row()]}):
                ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(FacebookAdDailySpend.objects.count(), 1)

    def test_second_run_overwrites_spend(self):
        """FB cập nhật muộn số của ngày cũ — lượt sau phải ghi đè, không cộng dồn."""
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(spend="100.00")]}):
            ad_spend_sync.sync_ad_spend_for_app(self.app)
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(spend="150.00")]}):
            ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(
            str(FacebookAdDailySpend.objects.get(ad=self.ad).spend), "150.00",
        )

    def test_lazy_creates_ad_missing_from_catalog(self):
        """Ad đã xoá trên FB vẫn còn chi tiêu — tạo bản ghi page=NULL, không bỏ tiền.

        Phải patch `_client`: `_lazy_fetch_ad` gọi Graph API thật, không chặn thì test
        treo 30s rồi mới rơi vào nhánh except.
        """
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(ad_id="GHOST")]}), \
             patch.object(ad_spend_sync._client, "get", side_effect=RuntimeError("no network")):
            result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        ghost = FacebookAd.objects.get(fb_ad_id="GHOST")
        self.assertIsNone(ghost.page_id)
        self.assertEqual(result["lazy_fetched_ads"], 1)
        self.assertEqual(FacebookAdDailySpend.objects.get(ad=ghost).spend, 100)

    def test_recurring_window_includes_today(self):
        """Bài học services.py:383 — preset last_7d của FB KHÔNG gồm hôm nay."""
        captured = {}

        def _capture(app, accounts, **kwargs):
            captured.update(kwargs)
            return {self.account.id: []}

        with patch.object(ad_spend_sync, "fetch_insights_batch", side_effect=_capture):
            ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(captured["until"], self.today.isoformat())
        self.assertEqual(
            captured["since"],
            (self.today - timedelta(days=ad_spend_sync.AD_SPEND_RECENT_DAYS)).isoformat(),
        )
        self.assertEqual(captured["level"], "ad")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_spend_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: ... ad_spend_sync`

- [ ] **Step 3: Xác nhận `fields_by_level["ad"]` đã đủ — KHÔNG sửa**

```bash
grep -n '"ad":' backend/apps/facebook_ads/services.py
```

Expected: hai dòng (185 và 261), đều là

```python
        "ad": "campaign_name,adset_name,ad_id,ad_name,spend,impressions,clicks,ctr,cpm,reach",
```

`ad_id` đã có sẵn → `fetch_insights_batch(level="ad")` dùng được ngay, không cần sửa gì.

**Không được rút gọn danh sách field này.** Hai dict đó dùng chung cho endpoint
`insights_batch` mà FE đang gọi với `level` do người dùng chọn (`insights_views.py:159`);
bỏ `ctr`/`cpm` là làm hỏng màn hình đang chạy để phục vụ một luồng không cần đến chúng.
Vài field thừa trong response không tốn gì.

- [ ] **Step 4: Viết implementation**

Tạo `backend/apps/facebook_ads/infrastructure/ad_spend_sync.py`:

```python
"""Chi tiêu Facebook Ads ở grain ad × ngày.

Chạy song song với ``services.sync_daily_spend_for_app`` (grain account) chứ không
thay thế: tổng cộng từ grain ad luôn thấp hơn grain account vì FB có khoản không
gán được về ad cụ thể (spec §6).
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.facebook_ads.ad_catalog import parse_ad_payload
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.facebook_ads.services import (
    GRAPH_API_BASE,
    _client,
    _log_batch_errors,
    fetch_insights_batch,
)

logger = logging.getLogger(__name__)

# Cửa sổ refresh trailing, LUÔN gồm hôm nay (bài học services.py:383).
AD_SPEND_RECENT_DAYS = 7
AD_FIELDS_LAZY = (
    "id,name,campaign{id,name},adset{id,name,promoted_object},"
    "creative{id,effective_object_story_id,object_story_spec{page_id}}"
)


def _lazy_fetch_ad(app: FacebookApp, account: FacebookAdAccount, fb_ad_id: str) -> FacebookAd:
    """Bổ sung ad thiếu trong catalog. Không gọi được API thì vẫn tạo bản ghi trống.

    Insights trả chi tiêu của cả ad đã xoá, nhưng ``/ads`` không liệt kê chúng nữa.
    Bỏ qua những dòng này là làm hụt tổng chi phí — nguy hiểm hơn nhiều so với việc
    một page hiển thị "Không rõ".
    """
    from apps.facebook_ads.models import FacebookPage

    defaults = {"ad_account": account}
    try:
        response = _client.get(
            f"{GRAPH_API_BASE}/{fb_ad_id}",
            params={"fields": AD_FIELDS_LAZY, "access_token": app.access_token},
            timeout=30,
        )
        if response.status_code == 200:
            parsed = parse_ad_payload(response.json() or {})
            defaults.update({
                "name": parsed["name"],
                "campaign_id": parsed["campaign_id"],
                "campaign_name": parsed["campaign_name"],
                "adset_id": parsed["adset_id"],
                "adset_name": parsed["adset_name"],
            })
            if parsed["fb_page_id"]:
                page, _ = FacebookPage.objects.get_or_create(fb_page_id=parsed["fb_page_id"])
                defaults["page"] = page
    except Exception:  # noqa: BLE001 - ad rác không được làm hỏng cả lượt đồng bộ
        logger.warning("lazy fetch ad %s thất bại — tạo bản ghi trống", fb_ad_id)

    ad, _ = FacebookAd.objects.get_or_create(fb_ad_id=fb_ad_id, defaults=defaults)
    return ad


def _upsert_ad_rows(
    app: FacebookApp, account: FacebookAdAccount, rows: list[dict],
) -> tuple[int, int]:
    """Ghi các dòng insights vào DB. Trả (số dòng upsert, số ad phải lazy-fetch)."""
    if not rows:
        return 0, 0

    fb_ad_ids = {str(r.get("ad_id") or "") for r in rows if r.get("ad_id")}
    known = {a.fb_ad_id: a for a in FacebookAd.objects.filter(fb_ad_id__in=fb_ad_ids)}

    lazy_count = 0
    for fb_ad_id in sorted(fb_ad_ids - set(known)):
        known[fb_ad_id] = _lazy_fetch_ad(app, account, fb_ad_id)
        lazy_count += 1

    objs = []
    for r in rows:
        fb_ad_id = str(r.get("ad_id") or "")
        date_str = r.get("date_start")
        if not fb_ad_id or not date_str:
            continue
        objs.append(FacebookAdDailySpend(
            ad=known[fb_ad_id],
            date=date_str,
            spend=Decimal(str(r.get("spend") or "0")),
            impressions=int(r.get("impressions") or 0),
            clicks=int(r.get("clicks") or 0),
            reach=int(r.get("reach") or 0),
        ))

    if objs:
        # bulk_create + update_conflicts: số dòng mỗi lượt cao hơn grain account
        # khoảng hai bậc, update_or_create từng dòng sẽ thành N query.
        FacebookAdDailySpend.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=["spend", "impressions", "clicks", "reach", "last_synced_at"],
            unique_fields=["ad", "date"],
        )
    return len(objs), lazy_count


def sync_ad_spend_for_app(
    app: FacebookApp, *, since: str | None = None, until: str | None = None,
) -> dict:
    """Đồng bộ chi tiêu grain ad. Không truyền since/until = cửa sổ trailing 7 ngày."""
    accounts = list(
        FacebookAdAccount.objects.filter(app=app, status=FacebookAdAccount.STATUS_ACTIVE)
    )
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0, "lazy_fetched_ads": 0, "failed_accounts": 0}

    if not (since and until):
        today = timezone.localdate()
        until = today.isoformat()
        since = (today - timedelta(days=AD_SPEND_RECENT_DAYS)).isoformat()

    results = fetch_insights_batch(
        app, accounts, level="ad", since=since, until=until, time_increment="1",
    )
    failed = _log_batch_errors(results, accounts)

    upserted = 0
    lazy = 0
    for account in accounts:
        rows_upserted, lazy_count = _upsert_ad_rows(app, account, results.get(account.id) or [])
        upserted += rows_upserted
        lazy += lazy_count

    return {
        "accounts": len(accounts),
        "rows_upserted": upserted,
        "lazy_fetched_ads": lazy,
        "failed_accounts": failed,
    }
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_spend_sync.py -q`
Expected: 5 passed

- [ ] **Step 6: Chạy lại test cũ để chắc không phá `fields_by_level`**

Run: `... python -m pytest apps/facebook_ads/tests -q`
Expected: toàn bộ pass

- [ ] **Step 7: Lint + commit**

```bash
git add backend/apps/facebook_ads/infrastructure/ad_spend_sync.py \
        backend/apps/facebook_ads/services.py \
        backend/apps/facebook_ads/tests/test_ad_spend_sync.py
git commit -m "feat(facebook-ads): sync daily spend at ad grain"
```

## Task 6: Chốt so khớp tổng grain ad vs grain account

**Files:**
- Modify: `backend/apps/facebook_ads/infrastructure/ad_spend_sync.py`
- Test: `backend/apps/facebook_ads/tests/test_spend_variance.py`

**Interfaces:**
- Consumes: `FacebookAdAccountDailySpend` (đã có), `send_ops_alert` từ `apps.common.ops_alerts`.
- Produces: `spend_variance_ratio(ad_total, account_total) -> float | None`, `check_spend_variance(app) -> list[dict]`. Hằng `SPEND_VARIANCE_THRESHOLD = 0.98`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_spend_variance.py`:

```python
"""Chốt so khớp: grain ad hụt nhiều so với grain account thì phải kêu."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.facebook_ads.infrastructure import ad_spend_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdDailySpend,
    FacebookApp,
)


class SpendVarianceRatioTests(SimpleTestCase):
    def test_returns_ratio(self):
        self.assertAlmostEqual(
            ad_spend_sync.spend_variance_ratio(Decimal("95"), Decimal("100")), 0.95,
        )

    def test_returns_none_when_account_total_is_zero(self):
        """Không có mẫu số thì không có tỷ lệ — tuyệt đối không chia cho 0."""
        self.assertIsNone(ad_spend_sync.spend_variance_ratio(Decimal("0"), Decimal("0")))


class CheckSpendVarianceTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.ad = FacebookAd.objects.create(fb_ad_id="A1", ad_account=self.account)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def _seed(self, account_spend: str, ad_spend: str, day=None):
        day = day or self.yesterday
        FacebookAdAccountDailySpend.objects.create(
            ad_account=self.account, date=day, spend=Decimal(account_spend),
        )
        FacebookAdDailySpend.objects.create(ad=self.ad, date=day, spend=Decimal(ad_spend))

    def test_alerts_when_ad_total_falls_short(self):
        self._seed("100.00", "90.00")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(len(findings), 1)
        alert.assert_called_once()
        self.assertIn("fb:spend_variance", alert.call_args.kwargs["key"])

    def test_silent_when_within_threshold(self):
        self._seed("100.00", "99.00")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(findings, [])
        alert.assert_not_called()

    def test_ignores_today(self):
        """Hai grain có độ trễ khác nhau nên hôm nay luôn lệch — kiểm là sinh rác."""
        self._seed("100.00", "10.00", day=timezone.localdate())
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(findings, [])
        alert.assert_not_called()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_spend_variance.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'spend_variance_ratio'`

- [ ] **Step 3: Viết implementation**

Thêm vào `backend/apps/facebook_ads/infrastructure/ad_spend_sync.py`:

```python
from django.db.models import Sum

from apps.common.ops_alerts import send_ops_alert
from apps.facebook_ads.models import FacebookAdAccountDailySpend

# Dưới ngưỡng này coi như grain ad hụt bất thường so với grain account.
SPEND_VARIANCE_THRESHOLD = 0.98
_VARIANCE_ALERT_TTL_SECONDS = 12 * 3600


def spend_variance_ratio(ad_total, account_total) -> float | None:
    """Tỷ lệ tổng grain ad / tổng grain account. None khi không có mẫu số."""
    if not account_total:
        return None
    return float(ad_total) / float(account_total)


def check_spend_variance(app: FacebookApp) -> list[dict]:
    """So tổng hai grain theo (TKQC, ngày). Trả danh sách ngày lệch quá ngưỡng.

    CHỈ kiểm ngày đã đóng: chi tiêu hôm nay ở hai grain có độ trễ cập nhật khác
    nhau nên luôn lệch, kiểm ngày đang chạy sẽ sinh cảnh báo rác mỗi lượt cron —
    và cảnh báo rác thì không ai đọc nữa.
    """
    cutoff = timezone.localdate()
    window_start = cutoff - timedelta(days=AD_SPEND_RECENT_DAYS)

    account_totals = {
        (row["ad_account_id"], row["date"]): row["total"]
        for row in FacebookAdAccountDailySpend.objects.filter(
            ad_account__app=app, date__gte=window_start, date__lt=cutoff,
        ).values("ad_account_id", "date").annotate(total=Sum("spend"))
    }
    ad_totals = {
        (row["ad__ad_account_id"], row["date"]): row["total"]
        for row in FacebookAdDailySpend.objects.filter(
            ad__ad_account__app=app, date__gte=window_start, date__lt=cutoff,
        ).values("ad__ad_account_id", "date").annotate(total=Sum("spend"))
    }

    findings: list[dict] = []
    for key, account_total in account_totals.items():
        account_id, day = key
        ratio = spend_variance_ratio(ad_totals.get(key, 0), account_total)
        if ratio is None or ratio >= SPEND_VARIANCE_THRESHOLD:
            continue
        findings.append({"ad_account_id": account_id, "date": day, "ratio": ratio})
        send_ops_alert(
            key=f"fb:spend_variance:{account_id}:{day.isoformat()}",
            title=(
                f"[Facebook Ads] Chi tiêu theo ad hụt so với theo tài khoản "
                f"(TKQC {account_id}, ngày {day:%d/%m})"
            ),
            lines=[
                f"Tổng theo tài khoản: {account_total}",
                f"Tổng cộng từ các ad: {ad_totals.get(key, 0)} ({ratio:.1%})",
                "Báo cáo theo page sẽ thiếu phần chênh này.",
                "Kiểm tra: ad bị xoá khỏi TKQC, hoặc khoản điều chỉnh/credit ở cấp tài khoản.",
            ],
            dedupe_seconds=_VARIANCE_ALERT_TTL_SECONDS,
        )
    return findings
```

- [ ] **Step 4: Gọi chốt sau mỗi lượt đồng bộ**

Trong `sync_ad_spend_for_app`, ngay trước `return`, thêm:

```python
    # Chỉ chạy cho nhánh cửa sổ trailing; backfill tự kiểm bằng command riêng.
    variance = check_spend_variance(app) if not (since and until) else []
```

và thêm `"variance_findings": len(variance),` vào dict trả về. Đồng thời sửa dòng gán `since`/`until` ở đầu hàm để biết nhánh nào đang chạy:

```python
    custom_range = bool(since and until)
    if not custom_range:
        today = timezone.localdate()
        until = today.isoformat()
        since = (today - timedelta(days=AD_SPEND_RECENT_DAYS)).isoformat()
```

rồi dùng `if not custom_range` ở chỗ gọi `check_spend_variance`.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_spend_variance.py apps/facebook_ads/tests/test_ad_spend_sync.py -q`
Expected: 10 passed

- [ ] **Step 6: Lint + commit**

```bash
git add backend/apps/facebook_ads/infrastructure/ad_spend_sync.py \
        backend/apps/facebook_ads/tests/test_spend_variance.py
git commit -m "feat(facebook-ads): alert when ad-grain spend falls short of account total"
```

## Task 7: Celery task chi tiêu + backfill command

**Files:**
- Modify: `backend/apps/facebook_ads/tasks.py`
- Modify: `backend/config/settings_parts/celery.py`
- Create: `backend/apps/facebook_ads/management/commands/backfill_fb_ad_spend.py`
- Test: `backend/apps/facebook_ads/tests/test_backfill_command.py`

**Interfaces:**
- Consumes: `sync_ad_spend_for_app` (Task 5)
- Produces: task `apps.facebook_ads.tasks.sync_all_ad_spend`; command `backfill_fb_ad_spend --since --until [--chunk-months N]`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_backfill_command.py`:

```python
"""Backfill chia theo tháng — mỗi lượt gọi phải nằm trong giới hạn payload Batch API."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.facebook_ads.management.commands import backfill_fb_ad_spend
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp


class BackfillCommandTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )

    def test_splits_range_into_monthly_chunks(self):
        calls = []

        def _capture(app, *, since, until):
            calls.append((since, until))
            return {"rows_upserted": 0, "lazy_fetched_ads": 0, "failed_accounts": 0}

        with patch.object(backfill_fb_ad_spend, "sync_ad_spend_for_app", side_effect=_capture):
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-01-01", "--until", "2026-03-15",
                stdout=StringIO(),
            )

        self.assertEqual(
            calls,
            [
                ("2026-01-01", "2026-01-31"),
                ("2026-02-01", "2026-02-28"),
                ("2026-03-01", "2026-03-15"),
            ],
        )

    def test_skips_app_with_invalid_token(self):
        self.app.token_status = FacebookApp.TOKEN_INVALID
        self.app.save(update_fields=["token_status"])

        with patch.object(backfill_fb_ad_spend, "sync_ad_spend_for_app") as sync:
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-01-01", "--until", "2026-01-31",
                stdout=StringIO(),
            )

        sync.assert_not_called()

    def test_rejects_reversed_range(self):
        with self.assertRaises(Exception):
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-03-01", "--until", "2026-01-01",
                stdout=StringIO(),
            )
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_backfill_command.py -q`
Expected: FAIL — `ModuleNotFoundError: ... backfill_fb_ad_spend`

- [ ] **Step 3: Viết command**

Tạo `backend/apps/facebook_ads/management/commands/backfill_fb_ad_spend.py`:

```python
"""Backfill chi tiêu grain ad theo khoảng ngày, chia nhỏ theo tháng.

Chạy TAY, không đưa vào cron: một lượt 365 ngày × vài trăm ad vượt xa
soft_time_limit của task định kỳ và dễ đụng rate limit Graph API.

    python manage.py backfill_fb_ad_spend --since 2025-07-27 --until 2026-07-27
"""

import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.facebook_ads.infrastructure.ad_spend_sync import sync_ad_spend_for_app
from apps.facebook_ads.models import FacebookApp


def month_chunks(since: date, until: date) -> list[tuple[str, str]]:
    """Cắt [since, until] thành các đoạn không vượt qua ranh giới tháng."""
    chunks: list[tuple[str, str]] = []
    cursor = since
    while cursor <= until:
        last_day = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1],
        )
        end = min(last_day, until)
        chunks.append((cursor.isoformat(), end.isoformat()))
        cursor = end + timedelta(days=1)
    return chunks


class Command(BaseCommand):
    help = "Backfill chi tiêu Facebook Ads ở grain ad theo khoảng ngày."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="YYYY-MM-DD")
        parser.add_argument("--until", required=True, help="YYYY-MM-DD")
        parser.add_argument("--app-id", type=int, default=None, help="Chỉ chạy 1 FacebookApp")

    def handle(self, *args, **options):
        try:
            since = date.fromisoformat(options["since"])
            until = date.fromisoformat(options["until"])
        except ValueError as exc:
            raise CommandError(f"Ngày không hợp lệ: {exc}") from exc
        if since > until:
            raise CommandError("--since phải trước hoặc bằng --until")

        apps_qs = FacebookApp.objects.filter(deleted_at__isnull=True)
        if options["app_id"]:
            apps_qs = apps_qs.filter(pk=options["app_id"])

        chunks = month_chunks(since, until)
        total_rows = 0
        for app in apps_qs:
            if app.token_status == FacebookApp.TOKEN_INVALID:
                self.stdout.write(f"Bỏ qua app {app.pk} ({app.name}) — token hỏng.")
                continue
            for chunk_since, chunk_until in chunks:
                result = sync_ad_spend_for_app(app, since=chunk_since, until=chunk_until)
                total_rows += result["rows_upserted"]
                self.stdout.write(
                    f"app {app.pk} {chunk_since}..{chunk_until}: "
                    f"{result['rows_upserted']} dòng, "
                    f"{result['lazy_fetched_ads']} ad bổ sung"
                )
        self.stdout.write(self.style.SUCCESS(f"Xong. Tổng {total_rows} dòng."))
```

- [ ] **Step 4: Thêm task định kỳ**

Trong `backend/apps/facebook_ads/tasks.py`, thêm import:

```python
from .infrastructure.ad_spend_sync import sync_ad_spend_for_app
```

và task mới:

```python
@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_all_ad_spend",
    soft_time_limit=300,
    time_limit=360,
)
def sync_all_ad_spend(self) -> dict:
    """Cửa sổ trailing 7 ngày ở grain ad. Chạy độc lập với sync_all_daily_spend."""
    with single_flight("lock:fb_sync_all_ad_spend", 400) as acquired:
        if not acquired:
            logger.info("sync_all_ad_spend already running — skip this tick")
            return {"skipped": "already_running"}
        summary: dict = {"apps": 0, "rows_upserted": 0, "skipped_apps": 0, "errors": []}
        for app in FacebookApp.objects.filter(deleted_at__isnull=True):
            if app.token_status == FacebookApp.TOKEN_INVALID:
                summary["skipped_apps"] += 1
                continue
            try:
                result = sync_ad_spend_for_app(app)
                summary["apps"] += 1
                summary["rows_upserted"] += result.get("rows_upserted", 0)
            except FacebookTokenError as exc:
                mark_token_invalid(app, str(exc))
                _alert_token_invalid(app, str(exc))
                summary["errors"].append({"app_id": app.id, "error": str(exc), "kind": "token"})
            except FacebookGraphError as exc:
                logger.warning("sync_ad_spend failed for app %s: %s", app.id, exc)
                summary["errors"].append({"app_id": app.id, "error": str(exc)})
        return summary
```

Trong `celery.py`, thêm routing và beat:

```python
    "apps.facebook_ads.tasks.sync_all_ad_spend": {"queue": CELERY_TASK_DEFAULT_QUEUE},
```

```python
    # Cùng nhịp với grain account để hai bảng luôn phản ánh cùng một thời điểm —
    # chốt so khớp 0.98 mới có nghĩa.
    "facebook-ads-sync-ad-spend": {
        "task": "apps.facebook_ads.tasks.sync_all_ad_spend",
        "schedule": FB_SYNC_INTERVAL_SECONDS,
    },
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_backfill_command.py -q`
Expected: 3 passed

- [ ] **Step 6: Lint + commit**

```bash
git add backend/apps/facebook_ads/management/commands/backfill_fb_ad_spend.py \
        backend/apps/facebook_ads/tasks.py backend/config/settings_parts/celery.py \
        backend/apps/facebook_ads/tests/test_backfill_command.py
git commit -m "feat(facebook-ads): add ad-spend cron task and monthly backfill command"
```

---

# SPRINT A4 — API báo cáo chi phí theo page

## Task 8: SQL tổng hợp + endpoint

**Files:**
- Create: `backend/apps/facebook_ads/page_report.py`
- Create: `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py`
- Modify: `backend/apps/facebook_ads/views.py` (re-export view), `backend/apps/facebook_ads/urls.py`
- Modify: `backend/apps/orders/_repositories_reports.py` (thêm classmethod công khai)
- Test: `backend/apps/facebook_ads/tests/test_page_report_api.py`

**Interfaces:**
- Consumes: `FacebookAdDailySpend`, `FacebookAd`, `FacebookPage` (Task 1); `OrderRepository.accessible_ads_ad_account_ids(user) -> list[int] | None` (`None` = không lọc).
- Produces: `aggregate_page_cost(since: date, until: date, ad_account_ids: list[int] | None) -> list[dict]`; `page_display_name(fb_page_id: str, name: str) -> str`; endpoint `GET /api/facebook-ads/reports/page`.

**Lưu ý layering:** view và test đặt trong `apps/facebook_ads/` vì cần import `apps.orders`. Đặt ngược lại sẽ thêm entry mới vào `ignore_imports` — vi phạm Global Constraints.

- [ ] **Step 1: Công khai helper scope bên orders**

Trong `backend/apps/orders/_repositories_reports.py`, ngay sau `_accessible_ads_ad_account_ids` (dòng ~752), thêm:

```python
    @classmethod
    def accessible_ads_ad_account_ids(cls, user) -> list[int] | None:
        """TKQC user được xem trong báo cáo ads. ``None`` = không cần lọc.

        Bản công khai cho app tầng trên (báo cáo page nằm ở ``apps.facebook_ads``),
        dùng chung luật phân quyền với báo cáo theo kênh thay vì chép logic.

        Trả ``None`` cho user xem-toàn-bộ thay vì list: ``_accessible_ads_ad_account_ids``
        chỉ liệt kê TKQC ĐÃ map vào kênh, nên dùng thẳng nó sẽ làm biến mất chi phí
        của TKQC chưa map — đúng kiểu hụt số âm thầm mà báo cáo này sinh ra để chữa.
        """
        if _user_sees_all_ads_channels(user):
            return None
        return cls._accessible_ads_ad_account_ids(user)
```

`_user_sees_all_ads_channels` là hàm module-level sẵn có ở `_repositories_reports.py:153` — không cần import thêm.

- [ ] **Step 2: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_page_report_api.py`:

```python
"""Báo cáo chi phí theo page — giai đoạn A: chưa có doanh thu."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
    FacebookPage,
)
from apps.users.test_helpers import make_user_with_sections


class PageReportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Helper chuẩn của repo: gán đủ permission của section + DataScope.ALL, nên
        # `_accessible_ads_ad_account_ids` trả toàn bộ TKQC (xem test_sync_view_token_state.py).
        cls.user = make_user_with_sections("ads_ops", ["ads-report"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.page = FacebookPage.objects.create(fb_page_id="111", name="Page A")
        self.ad = FacebookAd.objects.create(
            fb_ad_id="A1", ad_account=self.account, page=self.page,
        )
        self.orphan = FacebookAd.objects.create(
            fb_ad_id="A2", ad_account=self.account, page=None,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.ad, date=date(2026, 7, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.orphan, date=date(2026, 7, 1), spend=Decimal("40.00"),
        )

    def _get(self, **params):
        params.setdefault("since", "2026-07-01")
        params.setdefault("until", "2026-07-01")
        return self.client.get("/api/facebook-ads/reports/page", params)

    def test_groups_spend_by_page(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        rows = {r["page_id"]: r for r in response.json()["rows"]}
        self.assertEqual(rows["111"]["cost"], "100.00")
        self.assertEqual(rows["111"]["page_name"], "Page A")

    def test_unresolved_ads_get_their_own_row(self):
        """Chi phí của ad chưa rõ page phải lộ ra, không được biến mất khỏi tổng."""
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertIn("", rows)
        self.assertEqual(rows[""]["page_name"], "Không rõ page")
        self.assertEqual(rows[""]["cost"], "40.00")

    def test_revenue_is_null_in_phase_a(self):
        """Chưa go-live doanh thu: null, KHÔNG phải 0."""
        body = self._get().json()
        self.assertIsNone(body["revenue_available_from"])
        for row in body["rows"]:
            self.assertIsNone(row["revenue"])
            self.assertIsNone(row["ratio_pct"])

    def test_falls_back_to_id_when_page_name_empty(self):
        FacebookPage.objects.filter(fb_page_id="111").update(name="")
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertEqual(rows["111"]["page_name"], "Page 111")

    def test_rejects_invalid_dates(self):
        self.assertEqual(self._get(since="not-a-date").status_code, 400)

    def test_rejects_reversed_range(self):
        response = self._get(since="2026-07-05", until="2026-07-01")
        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self):
        client = APIClient()
        response = client.get(
            "/api/facebook-ads/reports/page",
            {"since": "2026-07-01", "until": "2026-07-01"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_see_all_user_gets_spend_from_unmapped_accounts(self):
        """TKQC chưa map vào kênh nào vẫn phải hiện chi phí cho user xem-toàn-bộ.

        Fixture này KHÔNG tạo Channel/ChannelAdAccount nào — nếu scope trả list rỗng
        thay vì None thì toàn bộ chi phí biến mất và test này bắt được.
        """
        rows = self._get().json()["rows"]
        self.assertEqual(
            sum(float(r["cost"]) for r in rows), 140.0,
        )
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_page_report_api.py -q`
Expected: FAIL — 404 vì route chưa tồn tại

- [ ] **Step 4: Viết SQL tổng hợp**

Tạo `backend/apps/facebook_ads/page_report.py`:

```python
"""Tổng hợp chi phí quảng cáo theo page.

Khác báo cáo theo kênh ở chỗ KHÔNG chia đều spend cho số kênh dùng chung TKQC
(``lark_ads_report_sync.py:129``) — chi phí ở đây gán đúng page thật qua grain ad.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.facebook_ads.models import FacebookAdDailySpend

# Nhãn cho chi phí của ad chưa suy ra được page. Không gộp im lặng vào tổng: giấu
# đi thì tổng báo cáo page không khớp tổng theo TKQC mà không ai biết vì sao.
UNKNOWN_PAGE_LABEL = "Không rõ page"


def page_display_name(fb_page_id: str, name: str) -> str:
    """Nhãn hiển thị. Không bao giờ trả chuỗi rỗng — ô trống đọc như lỗi giao diện."""
    if not fb_page_id:
        return UNKNOWN_PAGE_LABEL
    return name or f"Page {fb_page_id}"


def aggregate_page_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo page trong khoảng ngày.

    ``reach`` cố ý KHÔNG cộng dồn (spec §9): reach không cộng được giữa các ngày,
    tổng ra sẽ lớn hơn thực tế và không có cách sửa ở tầng đọc.
    """
    qs = FacebookAdDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(ad__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values("ad__page__fb_page_id", "ad__page__name")
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    result: list[dict] = []
    for row in rows:
        fb_page_id = row["ad__page__fb_page_id"] or ""
        result.append({
            "page_id": fb_page_id,
            "page_name": page_display_name(fb_page_id, row["ad__page__name"] or ""),
            "cost": str(row["cost"] or Decimal("0")),
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            # Giai đoạn A chưa có attribution — null chứ không phải 0.
            "revenue": None,
            "closed_orders": None,
            "ratio_pct": None,
        })
    return result
```

- [ ] **Step 5: Viết view**

Tạo `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py`:

```python
"""Endpoint báo cáo chi phí quảng cáo theo page.

Đặt trong app facebook_ads (tầng TRÊN orders theo backend/.importlinter) vì cần
dùng luật phân quyền của báo cáo ads bên orders. Đặt ngược lại là thêm nợ layering.
"""

from datetime import date as date_cls

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.facebook_ads.page_report import aggregate_page_cost
from apps.orders._repositories_reports import OrderRepository
from apps.users.permissions import require_section


def _revenue_available_from() -> str | None:
    """Mốc go-live doanh thu theo page. None = giai đoạn A, chưa có attribution."""
    raw = getattr(settings, "FB_PAGE_REPORT_START_DATE", "") or ""
    return str(raw).strip() or None


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def page_cost_report(request):
    since_raw = (request.query_params.get("since") or "").strip()
    until_raw = (request.query_params.get("until") or "").strip()
    try:
        since = date_cls.fromisoformat(since_raw)
        until = date_cls.fromisoformat(until_raw)
    except ValueError:
        return Response(
            {"detail": "since/until phải theo định dạng YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if since > until:
        return Response(
            {"detail": "since phải trước hoặc bằng until."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account_ids = OrderRepository.accessible_ads_ad_account_ids(request.user)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "page",
        "revenue_available_from": _revenue_available_from(),
        "rows": aggregate_page_cost(since, until, account_ids),
    })
```

- [ ] **Step 6: Đăng ký route**

Thêm vào `backend/apps/facebook_ads/views.py` (theo đúng cách các view khác được re-export):

```python
from .transport.views.facebook_ads_views_impl.page_report_view import page_cost_report  # noqa: F401
```

Trong `backend/apps/facebook_ads/urls.py`, thêm `page_cost_report` vào khối import và thêm route:

```python
    path("reports/page", page_cost_report, name="fb-page-cost-report"),
```

Thêm setting vào `backend/config/settings_parts/base.py`:

```python
# Mốc go-live doanh thu theo page (YYYY-MM-DD). Rỗng = giai đoạn A, báo cáo chỉ có
# chi phí và mọi trường doanh thu trả null.
FB_PAGE_REPORT_START_DATE = os.getenv("FB_PAGE_REPORT_START_DATE", "")
```

- [ ] **Step 7: Chạy test, xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_page_report_api.py -q`
Expected: 7 passed

- [ ] **Step 8: Kiểm tra layering + lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'command -v lint-imports >/dev/null || pip install -q import-linter==2.12; lint-imports'
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/facebook_ads/page_report.py \
        backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py \
        backend/apps/facebook_ads/views.py backend/apps/facebook_ads/urls.py \
        backend/apps/orders/_repositories_reports.py \
        backend/config/settings_parts/base.py \
        backend/apps/facebook_ads/tests/test_page_report_api.py
git commit -m "feat(facebook-ads): add page cost report endpoint"
```

---

# SPRINT A5 — Frontend

## Task 9: Chiều Page trên trang Báo cáo ads

**Files:**
- Create: `frontend/src/lib/api/facebook_ads_page_report.ts`
- Create: `frontend/src/features/ads/components/PageCostTable.tsx`
- Create: `frontend/src/features/ads/__tests__/page-report.test.ts`
- Modify: `frontend/src/features/ads/pages/AdsReportPage.tsx`

**Interfaces:**
- Consumes: endpoint `GET /api/facebook-ads/reports/page` (Task 8)
- Produces: `getPageCostReport(params)`, type `PageCostRow`, `PageCostReport`; component `PageCostTable`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/ads/__tests__/page-report.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { hasRevenueColumns, sortRowsByCost } from "@/lib/api/facebook_ads_page_report";
import type { PageCostRow } from "@/lib/api/facebook_ads_page_report";

const row = (overrides: Partial<PageCostRow>): PageCostRow => ({
  page_id: "1",
  page_name: "Page",
  cost: "0",
  impressions: 0,
  clicks: 0,
  revenue: null,
  closed_orders: null,
  ratio_pct: null,
  ...overrides,
});

describe("hasRevenueColumns", () => {
  it("ẩn cột doanh thu khi chưa go-live", () => {
    expect(hasRevenueColumns(null)).toBe(false);
  });

  it("hiện cột doanh thu khi đã có mốc", () => {
    expect(hasRevenueColumns("2026-08-15")).toBe(true);
  });
});

describe("sortRowsByCost", () => {
  it("xếp theo chi phí giảm dần", () => {
    const rows = [row({ page_id: "a", cost: "10" }), row({ page_id: "b", cost: "50" })];
    expect(sortRowsByCost(rows).map((r) => r.page_id)).toEqual(["b", "a"]);
  });

  it("luôn đẩy dòng không rõ page xuống cuối", () => {
    const rows = [
      row({ page_id: "", cost: "999" }),
      row({ page_id: "a", cost: "10" }),
    ];
    expect(sortRowsByCost(rows).map((r) => r.page_id)).toEqual(["a", ""]);
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- page-report`
Expected: FAIL — không resolve được module

- [ ] **Step 3: Viết API client**

Tạo `frontend/src/lib/api/facebook_ads_page_report.ts`:

```typescript
import { fetchJson } from "@/services/http";

const API_BASE = "/api/facebook-ads";

export type PageCostRow = {
  page_id: string;
  page_name: string;
  cost: string;
  impressions: number;
  clicks: number;
  /** null = chưa go-live attribution (giai đoạn A). Không bao giờ là 0 giả. */
  revenue: string | null;
  closed_orders: number | null;
  ratio_pct: number | null;
};

export type PageCostReport = {
  since: string;
  until: string;
  dimension: "page";
  /** null = chưa có dữ liệu doanh thu theo page. */
  revenue_available_from: string | null;
  rows: PageCostRow[];
};

/** Chỉ hiện cột doanh thu khi backend xác nhận đã có mốc go-live. */
export function hasRevenueColumns(revenueAvailableFrom: string | null): boolean {
  return revenueAvailableFrom !== null;
}

/**
 * Xếp theo chi phí giảm dần, riêng dòng "không rõ page" luôn xuống cuối — nó là
 * ghi chú chất lượng dữ liệu, không phải một page để đem ra so sánh.
 */
export function sortRowsByCost(rows: PageCostRow[]): PageCostRow[] {
  return [...rows].sort((a, b) => {
    if (a.page_id === "") return 1;
    if (b.page_id === "") return -1;
    return Number(b.cost) - Number(a.cost);
  });
}

export async function getPageCostReport(params: {
  since: string;
  until: string;
}): Promise<PageCostReport> {
  const query = new URLSearchParams(params).toString();
  return fetchJson<PageCostReport>(`${API_BASE}/reports/page?${query}`);
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npm run test -- page-report`
Expected: 4 passed

- [ ] **Step 5: Viết bảng hiển thị**

Tạo `frontend/src/features/ads/components/PageCostTable.tsx` — bám cấu trúc `ChannelDailyTable.tsx` cùng thư mục (raw `<table>` bọc trong `Card`, `formatAdsCurrency` từ `../report-grouping`):

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type PageCostRow,
  hasRevenueColumns,
  sortRowsByCost,
} from "@/lib/api/facebook_ads_page_report";

import { formatAdsCurrency, formatRatioPct } from "../report-grouping";

type PageCostTableProps = {
  rows: PageCostRow[];
  /** null = chưa go-live attribution → ẩn hẳn nhóm cột doanh thu. */
  revenueAvailableFrom: string | null;
};

const numberFormatter = new Intl.NumberFormat("vi-VN");

export function PageCostTable({ rows, revenueAvailableFrom }: PageCostTableProps) {
  const showRevenue = hasRevenueColumns(revenueAvailableFrom);
  const sorted = sortRowsByCost(rows);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Chi phí theo Page</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-muted-foreground">
              <th className="px-3 py-2 text-left font-medium">Page</th>
              <th className="px-3 py-2 text-center font-medium">Chi phí</th>
              <th className="px-3 py-2 text-center font-medium">Hiển thị</th>
              <th className="px-3 py-2 text-center font-medium">Click</th>
              {showRevenue ? (
                <>
                  <th className="px-3 py-2 text-center font-medium">Doanh thu</th>
                  <th className="px-3 py-2 text-center font-medium">Đơn chốt</th>
                  <th className="px-3 py-2 text-center font-medium">CP/DT</th>
                </>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr
                key={row.page_id || "__unknown__"}
                className="border-b border-border/40 last:border-b-0"
              >
                <td
                  className={`px-3 py-1.5 text-xs ${
                    row.page_id ? "font-medium text-foreground" : "italic text-muted-foreground"
                  }`}
                >
                  {row.page_name}
                </td>
                <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                  {formatAdsCurrency(Number(row.cost))}
                </td>
                <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                  {numberFormatter.format(row.impressions)}
                </td>
                <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                  {numberFormatter.format(row.clicks)}
                </td>
                {showRevenue ? (
                  <>
                    <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                      {row.revenue === null ? "—" : formatAdsCurrency(Number(row.revenue))}
                    </td>
                    <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                      {row.closed_orders === null
                        ? "—"
                        : numberFormatter.format(row.closed_orders)}
                    </td>
                    <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                      {formatRatioPct(row.ratio_pct)}
                    </td>
                  </>
                ) : null}
              </tr>
            ))}
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={showRevenue ? 7 : 4}
                  className="px-3 py-6 text-center text-xs text-muted-foreground"
                >
                  Chưa có dữ liệu chi phí trong khoảng đã chọn.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
```

Nếu `formatRatioPct` chưa xử lý `null` thì bổ sung ở `report-grouping.ts` — không tự viết bản sao trong file này.

- [ ] **Step 6: Gắn bộ chọn chiều vào trang**

Trong `frontend/src/features/ads/pages/AdsReportPage.tsx`:

```tsx
// thêm vào khối import
import { useQuery } from "@tanstack/react-query";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getPageCostReport } from "@/lib/api/facebook_ads_page_report";
import { PageCostTable } from "../components/PageCostTable";

// trong component, cạnh các state lọc sẵn có
const [dimension, setDimension] = useState<"channel" | "page">("channel");

const pageReport = useQuery({
  queryKey: ["ads-page-report", dateFrom, dateTo],
  queryFn: () => getPageCostReport({ since: dateFrom, until: dateTo }),
  // Chỉ gọi khi người dùng thực sự mở chiều Page — tránh một request thừa mỗi lần
  // đổi bộ lọc ở chiều Kênh.
  enabled: dimension === "page" && Boolean(dateFrom && dateTo),
});
```

Render ngay trên vùng nội dung hiện có:

```tsx
<Tabs value={dimension} onValueChange={(v) => setDimension(v as "channel" | "page")}>
  <TabsList>
    <TabsTrigger value="channel">Theo kênh</TabsTrigger>
    <TabsTrigger value="page">Theo page</TabsTrigger>
  </TabsList>
</Tabs>

{dimension === "page" ? (
  <PageCostTable
    rows={pageReport.data?.rows ?? []}
    revenueAvailableFrom={pageReport.data?.revenue_available_from ?? null}
  />
) : (
  /* toàn bộ nội dung hiện có của trang, giữ nguyên không sửa */
)}
```

Tên biến `dateFrom` / `dateTo` phải khớp state lọc ngày đang có sẵn trong file — đọc file trước khi sửa, không đổi tên chúng.

- [ ] **Step 7: Typecheck + test + commit**

```bash
cd frontend && npm run typecheck && npm run test:ci
git add frontend/src/lib/api/facebook_ads_page_report.ts \
        frontend/src/features/ads/components/PageCostTable.tsx \
        frontend/src/features/ads/__tests__/page-report.test.ts \
        frontend/src/features/ads/pages/AdsReportPage.tsx
git commit -m "feat(ads-report): add page dimension with cost-only view"
```

---

# Kiểm tra cuối giai đoạn A

- [ ] **Chạy toàn bộ test backend liên quan**

```bash
... python -m pytest apps/facebook_ads apps/orders/tests/test_ads_report_channel_scope.py -q
```

- [ ] **Kiểm tra contract layering**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'command -v lint-imports >/dev/null || pip install -q import-linter==2.12; lint-imports'
```

Expected: `Contracts: 1 kept, 0 broken.` — nếu có contract vỡ, chuyển code/test sang `apps/facebook_ads/` thay vì thêm entry vào `ignore_imports`.

- [ ] **Lint backend + frontend**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
cd frontend && npm run lint && npm run typecheck
```

- [ ] **Chốt nghiệp vụ trước khi coi là xong** (spec §12)

Chạy backfill trên môi trường thật rồi kiểm ba điều:

1. Tổng chi phí theo page trong 30 ngày gần nhất khớp tổng theo TKQC trong ngưỡng 0.98.
2. Không có alert `fb:spend_variance` nào đang treo.
3. Tỷ lệ chi phí rơi vào dòng "Không rõ page" dưới 5%. Cao hơn nghĩa là catalog đang thiếu ad — kiểm lại `sync_all_ad_catalog` trước khi cho người dùng vào xem.
