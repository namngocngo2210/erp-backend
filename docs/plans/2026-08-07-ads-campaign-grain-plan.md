# Kế hoạch triển khai — Chi phí ads theo toàn bộ TKQC và toàn bộ chiến dịch

> **Cho agent thực thi:** BẮT BUỘC dùng sub-skill `superpowers:subagent-driven-development`
> để làm từng task một. Các bước dùng checkbox (`- [ ]`) để theo dõi.

**Goal:** Kéo được chi phí quảng cáo của **mọi** tài khoản quảng cáo (kể cả tài khoản nằm
dưới MCC con của Google) và của **từng chiến dịch**, hiển thị trong tab "Chiến dịch" trên
báo cáo Ads.

**Architecture:** Facebook roll-up chi phí campaign từ grain ad đã có ở nhánh
`feat/fb-ads-page-grain` (không thêm bảng, không thêm lời gọi API). Google bỏ bộ lọc
`customer_client.level <= 1` để quét toàn cây MCC, và thêm hai bảng grain campaign được
nạp bằng một truy vấn GAQL `FROM campaign` duy nhất. Hai app expose hai endpoint riêng;
frontend gộp lại trong một tab.

**Tech Stack:** Django 5 + DRF, PostgreSQL, Celery + Redis, thư viện `google-ads` (gRPC/GAQL),
React + TypeScript + shadcn/ui + vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-07-ads-campaign-grain-design.md`

## Global Constraints

- Python 3.12; `ruff check .` phải sạch theo `backend/pyproject.toml` (lint là **bắt buộc**
  từ MR !1069 — vi phạm mới làm CI đỏ).
- Mọi comment và chuỗi hiển thị viết **tiếng Việt**, theo đúng codebase hiện tại.
- Hệ số hiển thị chi phí là `ADS_DAILY_COST_MULTIPLIER` (= `Decimal("1.1")`) trong
  `apps.orders._repositories_reports` — **dùng lại, không định nghĩa lại**.
- Contract phân tầng `backend/.importlinter`: `apps.facebook_ads` và `apps.google_ads` nằm
  **trên** `apps.orders`. Import xuống được, ngược lên thì **không**. Không thêm dòng nào
  vào KHỐI B của `ignore_imports`.
- Không dùng `cache.clear()` trong test (FLUSHDB đè Redis thật).
- Chi phí lưu `Decimal`, format ra chuỗi bằng `f"{value:.2f}"`. Không dùng `or` để thay giá
  trị mặc định cho `Decimal` (vì `Decimal("0.00")` là falsy) — dùng `is None`.
- Mỗi MR là **một nhánh riêng**. Đã gửi link MR thì đóng băng nhánh đó, thay đổi mới đi MR mới.
- Commit message tiếng Anh, ngắn gọn, **không** kèm dòng `Co-Authored-By`.

## Lệnh chạy test

**Backend** (image/network đã có sẵn, `POSTGRES_DB` phải khác nhau cho mỗi lần chạy song song):

```bash
cd <worktree> && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=<db_rieng> \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=<0-15> \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

**Lint backend:**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

**Frontend** (worktree anh em `desi-order-wt-*` — dùng junction node_modules):

```powershell
New-Item -ItemType Junction -Path "<worktree>\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

rồi `npx vitest run <file>` / `npx tsc --noEmit`.

**Test đỏ sẵn trên master** (không phải do thay đổi của bạn):
`src/app/__tests__/RequireAuth.test.tsx` (2 test),
`src/features/orders/__tests__/use-orders-data.realtime-upsert-filters.test.tsx` (5 test).

---

# MR1 — Đưa nhánh `feat/fb-ads-page-grain` về master

Nhánh này đã hoàn thiện giai đoạn A từ 28/07 nhưng chưa merge, master đã đi tiếp 10 ngày.
MR này **không thêm tính năng nào** — chỉ đưa nhánh cũ chạy được trên master mới.

### Task 1: Rebase nhánh page-grain và đánh số lại migration

**Files:**
- Nhánh: `feat/fb-ads-page-grain` (worktree `C:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/feat+fb-ads-page-grain`)
- Rename: `backend/apps/orders/migrations/0111_order_fb_ad_id.py` → `0116_order_fb_ad_id.py`
- Modify: `dependencies` bên trong file migration đó

**Interfaces:**
- Produces: model `FacebookAd` (có `campaign_id`, `campaign_name`), `FacebookAdDailySpend`,
  `FacebookPage`; classmethod `OrderRepository.accessible_ads_ad_account_ids(user) -> list[int] | None`.
  Task 7 phụ thuộc trực tiếp vào ba thứ này.

- [ ] **Bước 1: Merge master mới nhất vào nhánh**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/feat+fb-ads-page-grain"
git fetch origin --prune
git merge origin/master
```

Nếu có xung đột: giải quyết theo nguyên tắc **giữ cả hai** — nhánh này chỉ *thêm* file mới
và *thêm* dòng vào file cũ, không xoá gì của master.

- [ ] **Bước 2: Kiểm tra số migration đang đụng**

```bash
ls backend/apps/orders/migrations/ | tail -6
```

Kết quả mong đợi: thấy cả `0111_order_fb_ad_id.py` (của nhánh) lẫn `0111_*`..`0115_*` của
master → hai nhánh migration song song, `migrate` sẽ báo `Conflicting migrations detected`.

- [ ] **Bước 3: Đánh số lại migration của nhánh**

```bash
git mv backend/apps/orders/migrations/0111_order_fb_ad_id.py \
       backend/apps/orders/migrations/0116_order_fb_ad_id.py
```

Rồi sửa `dependencies` trong file đó thành migration **cuối cùng** của master:

```python
    dependencies = [
        ("orders", "0115_order_milestone_snapshot"),
    ]
```

Giữ nguyên `atomic = False` và khối `CREATE INDEX CONCURRENTLY` — đó là chủ ý (spec 27/07 §14.1).

- [ ] **Bước 4: Xác nhận không còn nhánh migration song song**

```bash
docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=mr1_check \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=8 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  python manage.py makemigrations --check --dry-run
```

Expected: không báo `Conflicting migrations`, không đòi tạo migration mới.

- [ ] **Bước 5: Chạy toàn bộ test của hai app bị ảnh hưởng**

```bash
python -m pytest apps/facebook_ads apps/orders -q --create-db
```

Expected: PASS toàn bộ. Nếu đỏ ở `apps/orders/tests/test_ads_spend_port_wiring.py` thì đó
là file **mới có trên master** (commit `6d176324` routing ads spend qua contract port) —
đọc file đó rồi chỉnh nhánh cho khớp port mới, đừng sửa test.

- [ ] **Bước 6: Lint**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

- [ ] **Bước 7: Test frontend của nhánh**

```bash
npx vitest run src/features/ads
```

- [ ] **Bước 8: Commit và push**

```bash
git add -A
git commit -m "chore(facebook-ads): rebase page-grain onto master and renumber migration"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat(facebook-ads): page-grain cost (phase A)" origin feat/fb-ads-page-grain
```

Nếu nhánh đã có MR mở sẵn thì `git push origin feat/fb-ads-page-grain` là đủ — MR tự cập nhật.

---

# MR2 — Google: quét toàn bộ cây MCC + cách ly lỗi từng tài khoản

Nhánh: `feat/google-ads-full-mcc-scan` (đã tạo, worktree `desi-order-wt-gads-mcc`).
Nhánh này chứa sẵn spec và plan.

### Task 2: Quét toàn bộ cây MCC

**Files:**
- Modify: `backend/apps/google_ads/services.py:84-139` (`sync_accounts_for_config`)
- Test: `backend/apps/google_ads/tests/test_account_sync.py` (tạo mới)

**Interfaces:**
- Produces: `services._enum_name(value) -> str` — dùng lại ở Task 5 để đọc `campaign.status`.
- Produces: `sync_accounts_for_config(config) -> dict` giữ nguyên chữ ký và các khoá
  `fetched/created/updated/removed/synced_at`.

- [ ] **Bước 1: Viết test thất bại**

Tạo `backend/apps/google_ads/tests/test_account_sync.py`:

```python
"""Quét danh sách tài khoản dưới MCC.

Bug được sửa: `WHERE customer_client.level <= 1` chỉ lấy con TRỰC TIẾP của MCC nên
mọi tài khoản nằm dưới một MCC con đều vô hình — báo cáo vẫn ra số, chỉ là thiếu tiền
của những tài khoản không ai biết là tồn tại.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.google_ads import services
from apps.google_ads.models import GoogleAdsAccount, GoogleAdsConfig


def _row(customer_id, name="Acc", *, manager=False, level=1, status="ENABLED"):
    """Giả một dòng GAQL. `status` truyền chuỗi cho gọn; test riêng phủ dạng enum."""
    return SimpleNamespace(
        customer_client=SimpleNamespace(
            id=customer_id,
            descriptive_name=name,
            currency_code="VND",
            status=status,
            manager=manager,
            level=level,
        )
    )


class SyncAccountsTreeTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )

    def _sync(self, rows):
        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_search", return_value=rows) as mock_search:
            result = services.sync_accounts_for_config(self.config)
        return result, mock_search

    def test_takes_accounts_at_every_depth(self):
        rows = [
            _row("1000000000", "MCC gốc", manager=True, level=0),
            _row("2000000000", "MCC con", manager=True, level=1),
            _row("3000000000", "TKQC dưới MCC con", level=2),
            _row("4000000000", "TKQC dưới MCC con con", level=3),
        ]
        result, mock_search = self._sync(rows)

        # Truy vấn KHÔNG được giới hạn độ sâu nữa.
        query = mock_search.call_args[0][2]
        self.assertNotIn("level", query.split("WHERE")[-1] if "WHERE" in query else "")

        # Chỉ 2 tài khoản chạy ads được lưu; 2 MCC bị loại.
        self.assertEqual(result["created"], 2)
        self.assertEqual(
            sorted(GoogleAdsAccount.objects.values_list("customer_id", flat=True)),
            ["3000000000", "4000000000"],
        )

    def test_same_customer_under_two_managers_stored_once(self):
        rows = [_row("3000000000", "TKQC", level=2), _row("3000000000", "TKQC", level=3)]
        self._sync(rows)
        self.assertEqual(GoogleAdsAccount.objects.filter(customer_id="3000000000").count(), 1)

    def test_cancelled_customer_marked_removed(self):
        rows = [_row("3000000000", "TKQC đã đóng", level=2, status="CANCELED")]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_REMOVED)

    def test_reads_proto_enum_status(self):
        # Thư viện google-ads trả enum proto-plus, không phải chuỗi.
        rows = [_row("3000000000", level=2, status=SimpleNamespace(name="ENABLED"))]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_ACTIVE)

    def test_account_missing_from_tree_marked_removed(self):
        GoogleAdsAccount.objects.create(
            config=self.config, name="Cũ", customer_id="9000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        result, _ = self._sync([_row("3000000000", level=2)])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(
            GoogleAdsAccount.objects.get(customer_id="9000000000").status,
            GoogleAdsAccount.STATUS_REMOVED,
        )
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_account_sync.py -q --create-db`
Expected: FAIL — `test_takes_accounts_at_every_depth` chỉ tạo 1 account (level 2 bị loại
bởi `level <= 1`... thực tế `level<=1` loại cả 2), `test_cancelled_customer_marked_removed`
fail vì code hiện tại luôn gán `active`.

- [ ] **Bước 3: Sửa `sync_accounts_for_config`**

Trong `backend/apps/google_ads/services.py`, thêm hằng và helper phía trên hàm:

```python
# Google trả trạng thái customer dạng enum proto-plus; chỉ ENABLED là còn chạy ads.
ACTIVE_CUSTOMER_STATUS = "ENABLED"


def _enum_name(value) -> str:
    """Tên enum dạng chuỗi.

    Thư viện google-ads (use_proto_plus) trả enum có `.name`; test và một số phiên
    bản trả thẳng chuỗi. Đọc cả hai để tầng gọi không phải biết đang chạy với dạng nào.
    """
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)
```

Rồi thay thân hàm:

```python
@transaction.atomic
def sync_accounts_for_config(config: GoogleAdsConfig) -> dict:
    """Liệt kê MỌI customer dưới cây MCC và upsert vào GoogleAdsAccount.

    KHÔNG lọc `customer_client.level`: cây tài khoản Google thường là
    MCC → MCC con → TKQC, giới hạn độ sâu 1 làm mọi tài khoản dưới MCC con vô hình.
    `login_customer_id` đã trỏ MCC gốc nên gọi API cho tài khoản lồng sâu vẫn hợp lệ.
    """
    client = _build_client(config)
    query = """
        SELECT
            customer_client.id,
            customer_client.descriptive_name,
            customer_client.currency_code,
            customer_client.status,
            customer_client.manager,
            customer_client.level
        FROM customer_client
    """
    rows = _search(client, config.login_customer_id, query)

    now = timezone.now()
    seen_ids: set[str] = set()
    created = 0
    updated = 0
    for row in rows:
        cc = row.customer_client
        # Bỏ qua tài khoản quản lý (MCC gốc và MCC con) — chúng không tự chạy ads.
        if cc.manager:
            continue
        customer_id = str(cc.id).strip()
        if not customer_id:
            continue
        # Cùng một customer có thể xuất hiện dưới nhiều MCC con; update_or_create
        # theo (config, customer_id) tự khử trùng.
        seen_ids.add(customer_id)
        status = (
            GoogleAdsAccount.STATUS_ACTIVE
            if _enum_name(cc.status) == ACTIVE_CUSTOMER_STATUS
            else GoogleAdsAccount.STATUS_REMOVED
        )
        defaults = {
            "name": cc.descriptive_name or customer_id,
            "currency": cc.currency_code or "",
            "status": status,
            "last_synced_at": now,
        }
        _, was_created = GoogleAdsAccount.objects.update_or_create(
            config=config, customer_id=customer_id, defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    removed = (
        GoogleAdsAccount.objects.filter(config=config)
        .exclude(customer_id__in=seen_ids)
        .update(status=GoogleAdsAccount.STATUS_REMOVED, last_synced_at=now)
    )

    return {
        "fetched": len(seen_ids),
        "created": created,
        "updated": updated,
        "removed": removed,
        "synced_at": now.isoformat(),
    }
```

- [ ] **Bước 4: Chạy lại test**

Run: `python -m pytest apps/google_ads/tests/test_account_sync.py -q --create-db`
Expected: PASS (5 test).

- [ ] **Bước 5: Commit**

```bash
git add backend/apps/google_ads/services.py backend/apps/google_ads/tests/test_account_sync.py
git commit -m "fix(google-ads): scan whole MCC tree and map customer status"
```

### Task 3: Cách ly lỗi từng tài khoản khi kéo chi phí

**Files:**
- Modify: `backend/apps/google_ads/services.py:186-229` (`sync_daily_spend_for_config`)
- Modify: `backend/apps/google_ads/tasks.py:21-35` (`sync_all_daily_spend_google`)
- Test: `backend/apps/google_ads/tests/test_daily_spend_sync.py` (tạo mới)

**Interfaces:**
- Consumes: `services._enum_name` từ Task 2 (không dùng trực tiếp, chỉ cùng file).
- Produces: `sync_daily_spend_for_config(...) -> dict` **thêm khoá `failed_accounts: int`**.
  Task 5 sao chép đúng hình dạng summary này cho grain campaign.

- [ ] **Bước 1: Viết test thất bại**

Tạo `backend/apps/google_ads/tests/test_daily_spend_sync.py`:

```python
"""Cách ly lỗi theo từng tài khoản khi kéo chi phí.

Trước fix, một tài khoản lỗi (CUSTOMER_NOT_ENABLED, mất quyền...) làm hỏng cả lần
chạy: mọi tài khoản đứng sau nó không được đồng bộ và không ai biết. Với 5 tài khoản
thì hiếm gặp; quét cả cây MCC lên vài chục tài khoản thì gần như chắc chắn dính.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.google_ads import services
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountDailySpend,
    GoogleAdsConfig,
)


class DailySpendErrorIsolationTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.bad = GoogleAdsAccount.objects.create(
            config=self.config, name="Hỏng", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        self.good = GoogleAdsAccount.objects.create(
            config=self.config, name="Tốt", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def test_failing_account_does_not_block_the_next_one(self):
        def fake_fetch(client, customer_id, since, until):
            if customer_id == "3000000000":
                raise services.GoogleAdsApiError("CUSTOMER_NOT_ENABLED")
            return [{"date": "2026-08-01", "spend": "120.00", "impressions": 10, "clicks": 2}]

        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_fetch_daily_rows", side_effect=fake_fetch):
            result = services.sync_daily_spend_for_config(self.config)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(
            GoogleAdsAccountDailySpend.objects.filter(ad_account=self.good).exists()
        )
        self.assertFalse(
            GoogleAdsAccountDailySpend.objects.filter(ad_account=self.bad).exists()
        )

    def test_explicit_range_also_isolates_errors(self):
        def fake_fetch(client, customer_id, since, until):
            raise services.GoogleAdsApiError("boom")

        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_fetch_daily_rows", side_effect=fake_fetch):
            result = services.sync_daily_spend_for_config(
                self.config, since="2026-08-01", until="2026-08-07",
            )

        self.assertEqual(result["failed_accounts"], 2)
        self.assertEqual(result["rows_upserted"], 0)
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_daily_spend_sync.py -q --create-db`
Expected: FAIL với `GoogleAdsApiError: CUSTOMER_NOT_ENABLED` thoát ra ngoài hàm.

- [ ] **Bước 3: Sửa `sync_daily_spend_for_config`**

Thêm logger ở đầu `services.py` (ngay sau phần import):

```python
import logging

logger = logging.getLogger(__name__)
```

Thay toàn bộ thân hàm `sync_daily_spend_for_config`:

```python
def sync_daily_spend_for_config(
    config: GoogleAdsConfig,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Đồng bộ chi phí theo ngày cho mọi customer active của config.

    - Có since/until → dùng đúng khoảng đó cho mọi account.
    - Không truyền → account chưa có data backfill 365 ngày, account đã có refresh 7 ngày.

    Lỗi được cách ly THEO TỪNG ACCOUNT: một tài khoản mất quyền không được phép làm
    những tài khoản còn lại mất luôn lượt đồng bộ. Nhưng cũng KHÔNG nuốt im lặng —
    số tài khoản hỏng trả về trong `failed_accounts` và ghi log, đúng bài học sự cố
    token Facebook 25–27/07: lỗi bị bỏ qua sẽ hiện thành "chi tiêu 0đ" trên báo cáo,
    không phân biệt được với "hôm nay không chạy quảng cáo".
    """
    accounts = list(
        GoogleAdsAccount.objects.filter(config=config, status=GoogleAdsAccount.STATUS_ACTIVE)
    )
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0, "failed_accounts": 0}

    client = _build_client(config)

    today = timezone.localdate()
    if since and until:
        windows = {account.id: (since, until) for account in accounts}
    else:
        backfill_since = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
        recent_since = (today - timedelta(days=RECENT_DAYS)).isoformat()
        until_str = today.isoformat()
        has_data_ids = set(
            GoogleAdsAccountDailySpend.objects.filter(ad_account__in=accounts)
            .values_list("ad_account_id", flat=True)
            .distinct()
        )
        windows = {
            account.id: (
                recent_since if account.id in has_data_ids else backfill_since,
                until_str,
            )
            for account in accounts
        }

    upserted = 0
    failed = 0
    for account in accounts:
        start, end = windows[account.id]
        try:
            rows = _fetch_daily_rows(client, account.customer_id, start, end)
        except GoogleAdsApiError as exc:
            failed += 1
            logger.warning(
                "google_ads sync_daily_spend bỏ qua customer %s (config %s): %s",
                account.customer_id, config.id, exc,
            )
            continue
        upserted += _upsert_daily_rows(account, rows)

    if failed:
        logger.warning(
            "google_ads sync_daily_spend: %d/%d tài khoản lỗi (config %s)",
            failed, len(accounts), config.id,
        )

    return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}
```

- [ ] **Bước 4: Chạy lại test**

Run: `python -m pytest apps/google_ads/tests/test_daily_spend_sync.py -q --create-db`
Expected: PASS (2 test).

- [ ] **Bước 5: Cộng dồn `failed_accounts` lên summary của task**

Trong `backend/apps/google_ads/tasks.py`, hàm `sync_all_daily_spend_google`:

```python
    summary: dict = {"configs": 0, "rows_upserted": 0, "failed_accounts": 0, "errors": []}
    for config in configs:
        try:
            result = sync_daily_spend_for_config(config)
            summary["configs"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            summary["failed_accounts"] += result.get("failed_accounts", 0)
```

Không có dòng này thì người trực đọc kết quả task thấy "thành công sạch" trong khi vài
tài khoản im lặng hỏng.

- [ ] **Bước 6: Chạy toàn bộ test app + lint**

```bash
python -m pytest apps/google_ads -q --create-db
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

- [ ] **Bước 7: Commit và push MR2**

```bash
git add -A
git commit -m "fix(google-ads): isolate per-account failures in daily spend sync"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="fix(google-ads): scan full MCC tree, isolate per-account errors" \
  origin feat/google-ads-full-mcc-scan
```

---

# MR3 — Google: chi phí theo chiến dịch

Nhánh: `feat/google-ads-campaign-grain`, tạo **từ nhánh MR2** (phụ thuộc `_enum_name` và
hình dạng summary):

```bash
git worktree add "C:/Users/MYPC/Desktop/workspace/desi-order-wt-gads-campaign" \
  -b feat/google-ads-campaign-grain feat/google-ads-full-mcc-scan
```

### Task 4: Hai bảng grain campaign

**Files:**
- Modify: `backend/apps/google_ads/models.py` (thêm vào cuối file)
- Create: `backend/apps/google_ads/migrations/0002_campaign_grain.py` (sinh bằng makemigrations)
- Test: `backend/apps/google_ads/tests/test_campaign_sync.py` (tạo mới, phần model)

**Interfaces:**
- Produces: `GoogleAdsCampaign(ad_account, campaign_id, name, status, last_seen_at)`,
  `GoogleAdsCampaignDailySpend(campaign, date, spend, impressions, clicks, last_synced_at)`.
  Task 5 và Task 8 đọc trực tiếp hai model này.

- [ ] **Bước 1: Viết test thất bại cho ràng buộc unique**

Tạo `backend/apps/google_ads/tests/test_campaign_sync.py` với phần đầu:

```python
"""Grain campaign của Google Ads: danh mục + chi phí theo ngày."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)


class CampaignModelTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def test_campaign_id_unique_per_account_only(self):
        other = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC 2", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        GoogleAdsCampaign.objects.create(ad_account=self.account, campaign_id="99", name="A")
        # Cùng campaign_id ở account khác là hợp lệ — id chỉ unique trong 1 customer.
        GoogleAdsCampaign.objects.create(ad_account=other, campaign_id="99", name="B")

        with transaction.atomic(), self.assertRaises(IntegrityError):
            GoogleAdsCampaign.objects.create(ad_account=self.account, campaign_id="99", name="C")

    def test_one_spend_row_per_campaign_and_date(self):
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.account, campaign_id="99", name="A",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("10.00"),
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            GoogleAdsCampaignDailySpend.objects.create(
                campaign=campaign, date=date(2026, 8, 1), spend=Decimal("20.00"),
            )
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_campaign_sync.py -q --create-db`
Expected: FAIL — `ImportError: cannot import name 'GoogleAdsCampaign'`.

- [ ] **Bước 3: Thêm hai model**

Thêm vào cuối `backend/apps/google_ads/models.py`:

```python
class GoogleAdsCampaign(models.Model):
    """Danh mục chiến dịch của một customer.

    Nạp từ cùng truy vấn GAQL với chi phí (`FROM campaign` trả cả hai) nên không tốn
    thêm quota. `status` lưu thô như Google trả (ENABLED/PAUSED/REMOVED) — không ánh xạ
    về active/removed như account vì chiến dịch tạm dừng vẫn là chiến dịch có ý nghĩa
    trên báo cáo lịch sử.
    """

    ad_account = models.ForeignKey(
        GoogleAdsAccount,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    campaign_id = models.CharField(max_length=32, help_text="ID chiến dịch bên Google")
    name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Campaign"
        verbose_name_plural = "Google Ads Campaigns"
        db_table = "google_ads_campaigns"
        constraints = [
            models.UniqueConstraint(
                fields=["ad_account", "campaign_id"], name="gads_camp_acc_cid_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["ad_account", "status"], name="gads_camp_acc_status_idx"),
        ]

    def __str__(self):
        return f"{self.name or self.campaign_id}"


class GoogleAdsCampaignDailySpend(models.Model):
    """Chi phí theo ngày của một chiến dịch.

    KHÔNG có cột `reach`: Google Ads API không trả reach ở grain campaign cho mọi loại
    chiến dịch, một cột luôn bằng 0 trông y hệt "reach thật bằng 0".
    """

    campaign = models.ForeignKey(
        GoogleAdsCampaign,
        on_delete=models.CASCADE,
        related_name="daily_spends",
    )
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Campaign Daily Spend"
        verbose_name_plural = "Google Ads Campaign Daily Spends"
        db_table = "google_ads_campaign_daily_spends"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "date"], name="gads_camp_spend_camp_date_uniq",
            ),
        ]
        indexes = [
            # Báo cáo luôn lọc khoảng ngày trước tiên.
            models.Index(fields=["date"], name="gads_camp_spend_date_idx"),
            models.Index(fields=["campaign", "date"], name="gads_camp_spend_cd_idx"),
        ]

    def __str__(self):
        return f"{self.campaign_id} @ {self.date}: {self.spend}"
```

- [ ] **Bước 4: Sinh migration**

```bash
docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=gads_mig \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  python manage.py makemigrations google_ads --name campaign_grain
```

Kiểm tra file sinh ra tên `0002_campaign_grain.py` và **chỉ** chứa `CreateModel` cho hai
bảng mới. Nếu nó đòi sửa gì khác → dừng lại, có drift từ MR2.

- [ ] **Bước 5: Chạy lại test**

Run: `python -m pytest apps/google_ads/tests/test_campaign_sync.py -q --create-db`
Expected: PASS (2 test).

- [ ] **Bước 6: Commit**

```bash
git add backend/apps/google_ads/models.py backend/apps/google_ads/migrations/0002_campaign_grain.py backend/apps/google_ads/tests/test_campaign_sync.py
git commit -m "feat(google-ads): add campaign catalog and daily spend tables"
```

### Task 5: Đồng bộ chi phí theo chiến dịch

**Files:**
- Create: `backend/apps/google_ads/infrastructure/__init__.py` (rỗng)
- Create: `backend/apps/google_ads/infrastructure/campaign_sync.py`
- Test: `backend/apps/google_ads/tests/test_campaign_sync.py` (thêm class mới)

**Interfaces:**
- Consumes: `services._build_client`, `services._search`, `services._safe_iso_date`,
  `services._enum_name`, `services.MICROS_PER_UNIT`, `services.BACKFILL_DAYS`,
  `services.RECENT_DAYS`, `services.GoogleAdsApiError` (helper nội bộ cùng app).
- Produces: `sync_campaign_spend_for_config(config, *, since=None, until=None) -> dict`
  với khoá `accounts`, `rows_upserted`, `failed_accounts`. Task 6 gọi hàm này.

- [ ] **Bước 1: Viết test thất bại**

Thêm vào `backend/apps/google_ads/tests/test_campaign_sync.py`:

```python
from apps.google_ads.infrastructure import campaign_sync


def _gaql_row(campaign_id, name, day, cost_micros, *, status="ENABLED", impressions=100, clicks=5):
    return SimpleNamespace(
        campaign=SimpleNamespace(id=campaign_id, name=name, status=status),
        segments=SimpleNamespace(date=day),
        metrics=SimpleNamespace(
            cost_micros=cost_micros, impressions=impressions, clicks=clicks,
        ),
    )


class CampaignSyncTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def _sync(self, rows_by_customer, **kwargs):
        def fake_search(client, customer_id, query):
            if isinstance(rows_by_customer, Exception):
                raise rows_by_customer
            value = rows_by_customer.get(customer_id, [])
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(campaign_sync, "_build_client", return_value=object()), \
             patch.object(campaign_sync, "_search", side_effect=fake_search) as mock_search:
            result = campaign_sync.sync_campaign_spend_for_config(self.config, **kwargs)
        return result, mock_search

    def test_one_query_fills_catalog_and_spend(self):
        rows = {
            "3000000000": [
                _gaql_row("99", "CD Tháng 8", "2026-08-01", 1_500_000),
                _gaql_row("99", "CD Tháng 8", "2026-08-02", 2_000_000),
                _gaql_row("77", "CD Khác", "2026-08-01", 500_000),
            ]
        }
        result, mock_search = self._sync(rows)

        # Đúng MỘT lời gọi API cho cả danh mục lẫn số liệu.
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(result["rows_upserted"], 3)
        self.assertEqual(GoogleAdsCampaign.objects.count(), 2)
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.count(), 3)

        campaign = GoogleAdsCampaign.objects.get(campaign_id="99")
        self.assertEqual(campaign.name, "CD Tháng 8")
        self.assertEqual(campaign.status, "ENABLED")
        spend = GoogleAdsCampaignDailySpend.objects.get(campaign=campaign, date=date(2026, 8, 1))
        # cost_micros / 1_000_000
        self.assertEqual(spend.spend, Decimal("1.50"))

    def test_rerun_updates_instead_of_duplicating(self):
        rows = {"3000000000": [_gaql_row("99", "CD cũ", "2026-08-01", 1_000_000)]}
        self._sync(rows)
        rows = {"3000000000": [_gaql_row("99", "CD đổi tên", "2026-08-01", 3_000_000)]}
        self._sync(rows)

        self.assertEqual(GoogleAdsCampaign.objects.count(), 1)
        self.assertEqual(GoogleAdsCampaign.objects.get().name, "CD đổi tên")
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.count(), 1)
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.get().spend, Decimal("3.00"))

    def test_backfill_window_for_account_without_data(self):
        _, mock_search = self._sync({"3000000000": []})
        query = mock_search.call_args[0][2]
        today = timezone.localdate()
        expected_since = (today - timedelta(days=campaign_sync.BACKFILL_DAYS)).isoformat()
        self.assertIn(expected_since, query)

    def test_recent_window_for_account_with_data(self):
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.account, campaign_id="99", name="A",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("1.00"),
        )
        _, mock_search = self._sync({"3000000000": []})
        query = mock_search.call_args[0][2]
        today = timezone.localdate()
        expected_since = (today - timedelta(days=campaign_sync.RECENT_DAYS)).isoformat()
        self.assertIn(expected_since, query)

    def test_failing_account_does_not_block_the_next_one(self):
        good = GoogleAdsAccount.objects.create(
            config=self.config, name="Tốt", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        rows = {
            "3000000000": campaign_sync.GoogleAdsApiError("CUSTOMER_NOT_ENABLED"),
            "4000000000": [_gaql_row("55", "CD tốt", "2026-08-01", 1_000_000)],
        }
        result, _ = self._sync(rows)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(GoogleAdsCampaign.objects.filter(ad_account=good).exists())

    def test_removed_account_is_skipped(self):
        self.account.status = GoogleAdsAccount.STATUS_REMOVED
        self.account.save(update_fields=["status"])
        result, mock_search = self._sync({})
        self.assertEqual(result["accounts"], 0)
        self.assertEqual(mock_search.call_count, 0)
```

Thêm import cần thiết ở đầu file test: `from datetime import date, timedelta` và
`from django.utils import timezone`.

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_campaign_sync.py -q --create-db`
Expected: FAIL — `ModuleNotFoundError: apps.google_ads.infrastructure`.

- [ ] **Bước 3: Viết module đồng bộ**

Tạo `backend/apps/google_ads/infrastructure/__init__.py` (file rỗng) và
`backend/apps/google_ads/infrastructure/campaign_sync.py`:

```python
"""Đồng bộ chi phí Google Ads ở grain chiến dịch.

Một truy vấn GAQL `FROM campaign` trả CẢ danh mục lẫn số liệu, nên catalog và chi phí
được upsert trong cùng vòng lặp — không tốn thêm quota so với việc chỉ lấy chi phí.

Nhịp đồng bộ mirror nguyên grain account đang chạy ổn định: account chưa có dòng nào
thì backfill 365 ngày, account đã có thì refresh 7 ngày gần nhất.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)

# Helper nội bộ của app — dùng lại thay vì chép, để logic dựng client, chặn GAQL
# injection và đọc enum chỉ tồn tại ở MỘT chỗ.
from apps.google_ads.services import (
    BACKFILL_DAYS,
    MICROS_PER_UNIT,
    RECENT_DAYS,
    GoogleAdsApiError,
    _build_client,
    _enum_name,
    _safe_iso_date,
    _search,
)

logger = logging.getLogger(__name__)


def _fetch_campaign_rows(client, customer_id: str, since: str, until: str) -> list[dict]:
    """Lấy chi phí theo (chiến dịch, ngày) của 1 customer."""
    # Phòng GAQL injection: date được nội suy vào query nên phải parse chặt.
    since = _safe_iso_date(since)
    until = _safe_iso_date(until)
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    out: list[dict] = []
    for row in _search(client, customer_id, query):
        out.append(
            {
                "campaign_id": str(row.campaign.id),
                "name": row.campaign.name or "",
                "status": _enum_name(row.campaign.status),
                "date": row.segments.date,
                "spend": (
                    Decimal(row.metrics.cost_micros) / MICROS_PER_UNIT
                ).quantize(Decimal("0.01")),
                "impressions": int(row.metrics.impressions or 0),
                "clicks": int(row.metrics.clicks or 0),
            }
        )
    return out


def _upsert_campaign_rows(account: GoogleAdsAccount, rows: list[dict]) -> int:
    """Upsert danh mục + chi phí. Trả số dòng chi phí đã ghi."""
    now = timezone.now()
    # Cache trong một lượt: backfill 365 ngày sinh 365 dòng CHUNG một chiến dịch,
    # không cache thì mỗi dòng là một update_or_create thừa lên danh mục.
    seen: dict[str, GoogleAdsCampaign] = {}
    upserted = 0
    for row in rows:
        campaign = seen.get(row["campaign_id"])
        if campaign is None:
            campaign, _ = GoogleAdsCampaign.objects.update_or_create(
                ad_account=account,
                campaign_id=row["campaign_id"],
                defaults={
                    "name": row["name"],
                    "status": row["status"],
                    "last_seen_at": now,
                },
            )
            seen[row["campaign_id"]] = campaign
        GoogleAdsCampaignDailySpend.objects.update_or_create(
            campaign=campaign,
            date=row["date"],
            defaults={
                "spend": row["spend"],
                "impressions": row["impressions"],
                "clicks": row["clicks"],
            },
        )
        upserted += 1
    return upserted


def sync_campaign_spend_for_config(
    config: GoogleAdsConfig,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Đồng bộ chi phí grain chiến dịch cho mọi customer active của config.

    Lỗi cách ly theo từng account (giống `sync_daily_spend_for_config`): một tài khoản
    mất quyền không được làm những tài khoản còn lại mất lượt, nhưng cũng không được
    biến mất khỏi log — đếm vào `failed_accounts`.
    """
    accounts = list(
        GoogleAdsAccount.objects.filter(config=config, status=GoogleAdsAccount.STATUS_ACTIVE)
    )
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0, "failed_accounts": 0}

    client = _build_client(config)
    today = timezone.localdate()

    if since and until:
        windows = {account.id: (since, until) for account in accounts}
    else:
        backfill_since = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
        recent_since = (today - timedelta(days=RECENT_DAYS)).isoformat()
        until_str = today.isoformat()
        has_data_ids = set(
            GoogleAdsCampaignDailySpend.objects.filter(campaign__ad_account__in=accounts)
            .values_list("campaign__ad_account_id", flat=True)
            .distinct()
        )
        windows = {
            account.id: (
                recent_since if account.id in has_data_ids else backfill_since,
                until_str,
            )
            for account in accounts
        }

    upserted = 0
    failed = 0
    for account in accounts:
        start, end = windows[account.id]
        try:
            rows = _fetch_campaign_rows(client, account.customer_id, start, end)
        except GoogleAdsApiError as exc:
            failed += 1
            logger.warning(
                "google_ads campaign sync bỏ qua customer %s (config %s): %s",
                account.customer_id, config.id, exc,
            )
            continue
        upserted += _upsert_campaign_rows(account, rows)

    if failed:
        logger.warning(
            "google_ads campaign sync: %d/%d tài khoản lỗi (config %s)",
            failed, len(accounts), config.id,
        )

    return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}
```

- [ ] **Bước 4: Chạy lại test**

Run: `python -m pytest apps/google_ads/tests/test_campaign_sync.py -q --create-db`
Expected: PASS (8 test).

- [ ] **Bước 5: Commit**

```bash
git add backend/apps/google_ads/infrastructure backend/apps/google_ads/tests/test_campaign_sync.py
git commit -m "feat(google-ads): sync daily spend at campaign grain"
```

### Task 6: Task Celery, lịch beat và lệnh backfill

**Files:**
- Modify: `backend/apps/google_ads/tasks.py`
- Modify: `backend/config/settings_parts/celery.py`
- Create: `backend/apps/google_ads/management/commands/backfill_gads_campaign_spend.py`
- Test: `backend/apps/google_ads/tests/test_campaign_task.py` (tạo mới)

**Interfaces:**
- Consumes: `sync_campaign_spend_for_config` từ Task 5.
- Produces: task `apps.google_ads.tasks.sync_all_campaign_spend_google`.

- [ ] **Bước 1: Viết test thất bại**

Tạo `backend/apps/google_ads/tests/test_campaign_task.py`:

```python
"""Task định kỳ đồng bộ chi phí grain chiến dịch."""

from unittest.mock import patch

from django.test import TestCase

from apps.google_ads import tasks
from apps.google_ads.models import GoogleAdsConfig
from apps.google_ads.services import GoogleAdsApiError


class SyncAllCampaignSpendTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )

    def test_aggregates_summary_across_configs(self):
        with patch.object(
            tasks, "sync_campaign_spend_for_config",
            return_value={"accounts": 3, "rows_upserted": 42, "failed_accounts": 1},
        ):
            summary = tasks._sync_all_campaign_spend_google()

        self.assertEqual(summary["configs"], 1)
        self.assertEqual(summary["rows_upserted"], 42)
        self.assertEqual(summary["failed_accounts"], 1)
        self.assertEqual(summary["errors"], [])

    def test_api_error_on_one_config_does_not_stop_the_rest(self):
        other = GoogleAdsConfig.objects.create(
            name="MCC 2", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )

        def fake_sync(config):
            if config.id == self.config.id:
                raise GoogleAdsApiError("token hỏng")
            return {"accounts": 1, "rows_upserted": 5, "failed_accounts": 0}

        with patch.object(tasks, "sync_campaign_spend_for_config", side_effect=fake_sync):
            summary = tasks._sync_all_campaign_spend_google()

        self.assertEqual(summary["configs"], 1)
        self.assertEqual(summary["rows_upserted"], 5)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["config_id"], self.config.id)
        self.assertTrue(other.pk)

    def test_skips_when_another_run_holds_the_lock(self):
        with patch.object(tasks, "single_flight") as mock_lock:
            mock_lock.return_value.__enter__.return_value = False
            result = tasks.sync_all_campaign_spend_google()
        self.assertEqual(result, {"skipped": "already_running"})
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_campaign_task.py -q --create-db`
Expected: FAIL — `AttributeError: module 'apps.google_ads.tasks' has no attribute '_sync_all_campaign_spend_google'`.

- [ ] **Bước 3: Thêm task**

Thêm vào `backend/apps/google_ads/tasks.py` — import thêm ở đầu file:

```python
from apps.common.db_guards import single_flight

from .infrastructure.campaign_sync import sync_campaign_spend_for_config
```

và task ở cuối file:

```python
@shared_task(
    bind=True,
    name="apps.google_ads.tasks.sync_all_campaign_spend_google",
    # KHÔNG dùng 300s như grain account: mỗi account trả (số chiến dịch × số ngày)
    # dòng thay vì (số ngày) dòng, và lần chạy đầu là backfill 365 ngày cho cả cây MCC.
    soft_time_limit=900,
    time_limit=960,
)
def sync_all_campaign_spend_google(self) -> dict:
    """Cửa sổ trailing 7 ngày ở grain chiến dịch. Chạy độc lập với grain account."""
    # TTL khoá (1000s) PHẢI > time_limit (960s): ngắn hơn thì worker đang bị hard-kill
    # mà khoá đã hết hạn → tick sau chen vào, hai lượt cùng upsert một tập dòng.
    with single_flight("lock:gads_sync_all_campaign_spend", 1000) as acquired:
        if not acquired:
            logger.info("sync_all_campaign_spend_google already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_campaign_spend_google()


def _sync_all_campaign_spend_google() -> dict:
    configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
    summary: dict = {"configs": 0, "rows_upserted": 0, "failed_accounts": 0, "errors": []}
    for config in configs:
        try:
            result = sync_campaign_spend_for_config(config)
            summary["configs"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            summary["failed_accounts"] += result.get("failed_accounts", 0)
        except GoogleAdsApiError as exc:
            logger.warning("campaign sync failed for google config %s: %s", config.id, exc)
            summary["errors"].append({"config_id": config.id, "error": str(exc)})
        except Exception:
            logger.exception("Unexpected error syncing google campaign spend for config %s", config.id)
            summary["errors"].append({"config_id": config.id, "error": "unexpected"})
    return summary
```

- [ ] **Bước 4: Chạy lại test**

Run: `python -m pytest apps/google_ads/tests/test_campaign_task.py -q --create-db`
Expected: PASS (3 test).

- [ ] **Bước 5: Đăng ký queue + lịch beat**

Trong `backend/config/settings_parts/celery.py`, thêm vào `CELERY_TASK_ROUTES` cạnh dòng
`sync_all_daily_spend_google`:

```python
    "apps.google_ads.tasks.sync_all_campaign_spend_google": {"queue": CELERY_TASK_DEFAULT_QUEUE},
```

và vào `CELERY_BEAT_SCHEDULE`, ngay sau `google-ads-sync-daily-spend`:

```python
    # Grain chiến dịch: cùng nhịp 300s với grain account để hai bảng luôn phản ánh
    # cùng một thời điểm — so hai con số mới có nghĩa.
    "google-ads-sync-campaign-spend": {
        "task": "apps.google_ads.tasks.sync_all_campaign_spend_google",
        "schedule": 300.0,
    },
```

`300.0` là đúng giá trị `google-ads-sync-daily-spend` đang dùng (`celery.py:129`). Nếu đọc
file thấy khác, dùng theo file — hai entry phải cùng nhịp.

- [ ] **Bước 6: Viết lệnh backfill**

Tạo `backend/apps/google_ads/management/commands/backfill_gads_campaign_spend.py`:

```python
"""Backfill chi phí grain chiến dịch theo khoảng ngày, chia nhỏ theo tháng.

Chạy TAY, không đưa vào cron: một lượt 365 ngày × cả cây MCC vượt xa soft_time_limit
của task định kỳ và dễ đụng quota Google Ads API.

    python manage.py backfill_gads_campaign_spend --since 2025-08-07 --until 2026-08-07
"""

import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.google_ads.infrastructure.campaign_sync import sync_campaign_spend_for_config
from apps.google_ads.models import GoogleAdsConfig


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
    help = "Backfill chi phí Google Ads ở grain chiến dịch theo khoảng ngày."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="YYYY-MM-DD")
        parser.add_argument("--until", required=True, help="YYYY-MM-DD")
        parser.add_argument("--config-id", type=int, default=None, help="Chỉ chạy 1 config")

    def handle(self, *args, **options):
        try:
            since = date.fromisoformat(options["since"])
            until = date.fromisoformat(options["until"])
        except ValueError as exc:
            raise CommandError(f"Ngày không hợp lệ: {exc}") from exc
        if since > until:
            raise CommandError("--since phải trước hoặc bằng --until")

        configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
        if options["config_id"]:
            configs = configs.filter(pk=options["config_id"])

        chunks = month_chunks(since, until)
        total_rows = 0
        for config in configs:
            for chunk_since, chunk_until in chunks:
                result = sync_campaign_spend_for_config(
                    config, since=chunk_since, until=chunk_until,
                )
                total_rows += result["rows_upserted"]
                self.stdout.write(
                    f"config {config.pk} {chunk_since}..{chunk_until}: "
                    f"{result['rows_upserted']} dòng, "
                    f"{result['failed_accounts']} tài khoản lỗi"
                )
        self.stdout.write(self.style.SUCCESS(f"Xong. Tổng {total_rows} dòng."))
```

- [ ] **Bước 7: Test cho lệnh backfill**

Thêm vào `backend/apps/google_ads/tests/test_campaign_task.py`:

```python
from datetime import date
from io import StringIO

from django.core.management import call_command

from apps.google_ads.management.commands.backfill_gads_campaign_spend import month_chunks


class BackfillCommandTests(TestCase):
    def test_month_chunks_never_cross_a_month_boundary(self):
        chunks = month_chunks(date(2026, 1, 15), date(2026, 3, 3))
        self.assertEqual(
            chunks,
            [
                ("2026-01-15", "2026-01-31"),
                ("2026-02-01", "2026-02-28"),
                ("2026-03-01", "2026-03-03"),
            ],
        )

    def test_command_calls_sync_once_per_chunk(self):
        GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        target = (
            "apps.google_ads.management.commands."
            "backfill_gads_campaign_spend.sync_campaign_spend_for_config"
        )
        with patch(target, return_value={"rows_upserted": 2, "failed_accounts": 0}) as mock_sync:
            call_command(
                "backfill_gads_campaign_spend",
                "--since", "2026-01-15", "--until", "2026-03-03",
                stdout=StringIO(),
            )
        self.assertEqual(mock_sync.call_count, 3)
```

- [ ] **Bước 8: Chạy toàn bộ test app + lint**

```bash
python -m pytest apps/google_ads -q --create-db
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: PASS toàn bộ.

- [ ] **Bước 9: Commit và push MR3**

```bash
git add -A
git commit -m "feat(google-ads): add campaign spend task, beat schedule and backfill command"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat(google-ads): campaign-grain cost" \
  origin feat/google-ads-campaign-grain
```

---

# MR4 — Endpoint chi phí theo chiến dịch + tab "Chiến dịch"

Nhánh: `feat/ads-campaign-report`, tạo **từ master sau khi MR1 và MR3 đã merge**:

```bash
git fetch origin && git worktree add \
  "C:/Users/MYPC/Desktop/workspace/desi-order-wt-campaign-report" \
  -b feat/ads-campaign-report origin/master
```

Xác nhận trước khi bắt đầu: `git log --oneline -20 origin/master | grep -i "page-grain\|campaign"`
phải thấy cả hai MR. Nếu chưa, **dừng lại** — Task 7 cần model `FacebookAdDailySpend`.

### Task 7: Endpoint chi phí campaign của Facebook

**Files:**
- Create: `backend/apps/facebook_ads/campaign_report.py`
- Create: `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/campaign_report_view.py`
- Modify: `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/__init__.py`
- Modify: `backend/apps/facebook_ads/views.py`, `backend/apps/facebook_ads/urls.py`
- Test: `backend/apps/facebook_ads/tests/test_campaign_report_api.py`

**Interfaces:**
- Consumes: `FacebookAd`, `FacebookAdDailySpend` (MR1);
  `OrderRepository.accessible_ads_ad_account_ids(user) -> list[int] | None` (MR1).
- Produces: `aggregate_campaign_cost(since, until, ad_account_ids) -> list[dict]`;
  endpoint `GET /api/facebook-ads/reports/campaign?since=&until=`.
  Task 9 gọi endpoint này.

- [ ] **Bước 1: Viết test thất bại**

Tạo `backend/apps/facebook_ads/tests/test_campaign_report_api.py`:

```python
"""Báo cáo chi phí theo chiến dịch — roll-up từ grain ad, không thêm bảng."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.facebook_ads.campaign_report import UNKNOWN_CAMPAIGN_LABEL, aggregate_campaign_cost
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.users.models import User


class AggregateCampaignCostTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="TKQC A", account_id="9", fb_id="act_9",
        )

    def _ad(self, fb_ad_id, campaign_id, campaign_name):
        return FacebookAd.objects.create(
            fb_ad_id=fb_ad_id, ad_account=self.account, name=f"Ad {fb_ad_id}",
            campaign_id=campaign_id, campaign_name=campaign_name,
        )

    def _spend(self, ad, day, amount):
        FacebookAdDailySpend.objects.create(
            ad=ad, date=day, spend=Decimal(amount), impressions=100, clicks=5,
        )

    def test_sums_ads_of_the_same_campaign_and_applies_multiplier(self):
        ad_a = self._ad("1", "c1", "CD 1")
        ad_b = self._ad("2", "c1", "CD 1")
        self._spend(ad_a, date(2026, 8, 1), "100.00")
        self._spend(ad_b, date(2026, 8, 1), "100.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(len(rows), 1)
        # (100 + 100) * 1.1 — nhân TRÊN TỔNG, không nhân từng dòng.
        self.assertEqual(rows[0]["cost"], "220.00")
        self.assertEqual(rows[0]["campaign_name"], "CD 1")
        self.assertEqual(rows[0]["platform"], "facebook")
        self.assertEqual(rows[0]["impressions"], 200)
        self.assertIsNone(rows[0]["revenue"])

    def test_ad_without_campaign_gets_explicit_label(self):
        ad = self._ad("3", "", "")
        self._spend(ad, date(2026, 8, 1), "50.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(rows[0]["campaign_name"], UNKNOWN_CAMPAIGN_LABEL)

    def test_zero_cost_keeps_two_decimals(self):
        ad = self._ad("4", "c2", "CD 2")
        self._spend(ad, date(2026, 8, 1), "0.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(rows[0]["cost"], "0.00")

    def test_filters_by_ad_account(self):
        other_account = FacebookAdAccount.objects.create(
            app=self.app, name="TKQC B", account_id="8", fb_id="act_8",
        )
        mine = self._ad("5", "c1", "CD 1")
        theirs = FacebookAd.objects.create(
            fb_ad_id="6", ad_account=other_account, campaign_id="c9", campaign_name="CD 9",
        )
        self._spend(mine, date(2026, 8, 1), "10.00")
        self._spend(theirs, date(2026, 8, 1), "999.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), [self.account.id])

        self.assertEqual([r["campaign_name"] for r in rows], ["CD 1"])

    def test_outside_date_range_excluded(self):
        ad = self._ad("7", "c1", "CD 1")
        self._spend(ad, date(2026, 7, 31), "10.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 31), None)

        self.assertEqual(rows, [])


class CampaignReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="admin", password="x", is_staff=True, is_superuser=True,
        )
        self.client.force_authenticate(self.user)

    def test_requires_iso_dates(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "01/08/2026", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 400)

    def test_rejects_reversed_range(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-07", "until": "2026-08-01"})
        self.assertEqual(response.status_code, 400)

    def test_returns_rows_and_truncated_flag(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-01", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rows"], [])
        self.assertFalse(response.data["truncated"])
        self.assertEqual(response.data["dimension"], "campaign")
```

Nếu `User.objects.create_user` ở codebase cần tham số khác (kiểm tra
`apps/users/models.py`), sửa cho khớp — không đổi ý nghĩa test.

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/facebook_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: FAIL — `ModuleNotFoundError: apps.facebook_ads.campaign_report`.

- [ ] **Bước 3: Viết module tổng hợp**

Tạo `backend/apps/facebook_ads/campaign_report.py`:

```python
"""Tổng hợp chi phí quảng cáo theo chiến dịch.

Roll-up thẳng từ grain ad (`FacebookAdDailySpend` + `FacebookAd.campaign_id`) — không
có bảng riêng cho campaign và không gọi thêm Graph API. Đổi chiều báo cáo sang adset
sau này cũng chỉ là đổi cột `values()`.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.facebook_ads.models import FacebookAdDailySpend

# TÁI SỬ DỤNG hằng số của báo cáo theo kênh/page, KHÔNG định nghĩa lại: ba tab nằm
# trên CÙNG một màn hình, lệch hệ số là hai con số chênh 10% không lời giải thích.
# Chiều import hợp lệ theo `.importlinter`: apps.facebook_ads nằm TRÊN apps.orders.
from apps.orders._repositories_reports import ADS_DAILY_COST_MULTIPLIER

PLATFORM = "facebook"

# Ad chưa gắn được campaign (ad đã xoá trên FB vẫn còn chi tiêu lịch sử). Không gộp
# im lặng vào tổng: giấu đi thì tổng theo chiến dịch không khớp tổng theo TKQC mà
# không ai biết vì sao.
UNKNOWN_CAMPAIGN_LABEL = "Không rõ chiến dịch"


def aggregate_campaign_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo chiến dịch trong khoảng ngày.

    KHÔNG trả `reach` dù grain ad có lưu: reach không cộng được giữa các ngày, và
    Google không có cột tương đương — một cột chỉ có số ở nửa số dòng đọc như lỗi.
    """
    qs = FacebookAdDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(ad__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values("ad__campaign_id", "ad__campaign_name", "ad__ad_account__name")
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    result: list[dict] = []
    for row in rows:
        campaign_id = row["ad__campaign_id"] or ""
        # `is None` chứ không `or`: Decimal("0.00") falsy nên `or` sẽ nuốt mất 2 số
        # thập phân của trường hợp chi phí đúng bằng 0.
        raw_cost = row["cost"] if row["cost"] is not None else Decimal("0")
        # Nhân hệ số trên TỔNG đã cộng, không nhân từng dòng — không tích luỹ sai số
        # làm tròn từng ngày.
        cost = raw_cost * ADS_DAILY_COST_MULTIPLIER
        result.append({
            "platform": PLATFORM,
            "account_name": row["ad__ad_account__name"] or "",
            "campaign_id": campaign_id,
            "campaign_name": row["ad__campaign_name"] or UNKNOWN_CAMPAIGN_LABEL,
            "cost": f"{cost:.2f}",
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            # Chưa có attribution theo chiến dịch — null chứ không phải 0.
            "revenue": None,
        })
    return result
```

- [ ] **Bước 4: Viết view**

Tạo `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/campaign_report_view.py`:

```python
"""Endpoint báo cáo chi phí quảng cáo theo chiến dịch (Facebook)."""

from datetime import date as date_cls

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.facebook_ads.campaign_report import aggregate_campaign_cost
from apps.orders.repositories import OrderRepository
from apps.users.permissions import require_section

# Chặn payload phình khi quét hết TKQC. Cắt mà KHÔNG nói ra thì bảng "thiếu chiến
# dịch" trông y hệt bảng "chiến dịch không tiêu tiền" — nên trả kèm cờ `truncated`.
MAX_ROWS = 1000


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def campaign_cost_report(request):
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
    rows = aggregate_campaign_cost(since, until, account_ids)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "campaign",
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
    })
```

- [ ] **Bước 5: Đấu dây view vào urls**

`.../facebook_ads_views_impl/__init__.py` — thêm import và tên vào `__all__`:

```python
from .campaign_report_view import campaign_cost_report
```

`backend/apps/facebook_ads/views.py` — thêm `campaign_cost_report` vào import và `__all__`.

`backend/apps/facebook_ads/urls.py` — thêm vào `urlpatterns`:

```python
    path("reports/campaign", campaign_cost_report, name="fb-campaign-cost-report"),
```

và thêm `campaign_cost_report` vào khối import ở đầu file.

- [ ] **Bước 6: Chạy lại test**

Run: `python -m pytest apps/facebook_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: PASS (8 test).

- [ ] **Bước 7: Commit**

```bash
git add backend/apps/facebook_ads
git commit -m "feat(facebook-ads): add campaign cost report endpoint"
```

### Task 8: Endpoint chi phí campaign của Google

**Files:**
- Create: `backend/apps/google_ads/campaign_report.py`
- Create: `backend/apps/google_ads/transport/views/google_ads_views_impl/campaign_report_view.py`
- Modify: `.../google_ads_views_impl/__init__.py`, `backend/apps/google_ads/views.py`,
  `backend/apps/google_ads/urls.py`
- Modify: `backend/apps/orders/_repositories_reports.py` (thêm
  `accessible_google_ads_account_ids`)
- Test: `backend/apps/google_ads/tests/test_campaign_report_api.py`

**Interfaces:**
- Consumes: `GoogleAdsCampaignDailySpend` (Task 4).
- Produces: `OrderRepository.accessible_google_ads_account_ids(user) -> list[int] | None`;
  endpoint `GET /api/google-ads/reports/campaign?since=&until=` trả **cùng hình dạng
  payload** với Task 7 (`platform` = `"google"`).

- [ ] **Bước 1: Viết test thất bại**

Tạo `backend/apps/google_ads/tests/test_campaign_report_api.py`:

```python
"""Báo cáo chi phí Google Ads theo chiến dịch — cùng hình dạng payload với Facebook."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.google_ads.campaign_report import aggregate_campaign_cost
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)
from apps.users.models import User


class AggregateGoogleCampaignCostTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC G", customer_id="3000000000",
        )

    def _spend(self, campaign_id, name, day, amount):
        campaign, _ = GoogleAdsCampaign.objects.get_or_create(
            ad_account=self.account, campaign_id=campaign_id, defaults={"name": name},
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=day, spend=Decimal(amount), impressions=50, clicks=3,
        )

    def test_sums_days_and_applies_multiplier(self):
        self._spend("c1", "CD G", date(2026, 8, 1), "100.00")
        self._spend("c1", "CD G", date(2026, 8, 2), "100.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 2), None)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost"], "220.00")
        self.assertEqual(rows[0]["platform"], "google")
        self.assertEqual(rows[0]["account_name"], "TKQC G")
        self.assertEqual(rows[0]["impressions"], 100)
        self.assertIsNone(rows[0]["revenue"])

    def test_zero_cost_keeps_two_decimals(self):
        self._spend("c2", "CD 0đ", date(2026, 8, 1), "0.00")
        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)
        self.assertEqual(rows[0]["cost"], "0.00")

    def test_filters_by_account(self):
        other = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC khác", customer_id="4000000000",
        )
        self._spend("c1", "CD G", date(2026, 8, 1), "10.00")
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=other, campaign_id="c9", name="CD khác",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("999.00"),
        )

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), [self.account.id])

        self.assertEqual([r["campaign_name"] for r in rows], ["CD G"])


class GoogleCampaignReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="admin2", password="x", is_staff=True, is_superuser=True,
        )
        self.client.force_authenticate(self.user)

    def test_requires_iso_dates(self):
        url = reverse("gads-campaign-cost-report")
        response = self.client.get(url, {"since": "hôm qua", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 400)

    def test_returns_same_payload_shape_as_facebook(self):
        url = reverse("gads-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-01", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data.keys()),
            ["dimension", "rows", "since", "truncated", "until"],
        )
        self.assertEqual(response.data["dimension"], "campaign")
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `python -m pytest apps/google_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: FAIL — `ModuleNotFoundError: apps.google_ads.campaign_report`.

- [ ] **Bước 3: Thêm hàm scope TKQC Google vào OrderRepository**

Trong `backend/apps/orders/_repositories_reports.py`, ngay dưới
`accessible_ads_ad_account_ids`, thêm:

```python
    @staticmethod
    def _accessible_google_ads_account_ids(user) -> list[int]:
        """Union id TKQC Google (bảng nối ChannelAdAccount) qua các kênh user thấy."""
        qs = Channel.objects.filter(deleted_at__isnull=True).exclude(id__in=_ads_excluded_channel_ids())
        if not _user_sees_all_ads_channels(user):
            qs = qs.filter(user_links__user=user, user_links__is_active=True).distinct()
        return list(
            ChannelAdAccount.objects.filter(
                channel__in=qs, provider=ChannelAdAccount.Provider.GOOGLE
            )
            .order_by()  # bỏ Meta.ordering để DISTINCT chỉ áp lên account_id
            .values_list("account_id", flat=True)
            .distinct()
        )

    @classmethod
    def accessible_google_ads_account_ids(cls, user) -> list[int] | None:
        """TKQC Google user được xem. ``None`` = không cần lọc.

        Cùng luật với bản Facebook: user xem-toàn-bộ trả None thay vì list, vì list
        chỉ gồm TKQC ĐÃ map vào kênh — dùng thẳng sẽ làm biến mất chi phí của TKQC
        chưa map.
        """
        if _user_sees_all_ads_channels(user):
            return None
        return cls._accessible_google_ads_account_ids(user)
```

- [ ] **Bước 4: Viết module tổng hợp**

Tạo `backend/apps/google_ads/campaign_report.py`:

```python
"""Tổng hợp chi phí Google Ads theo chiến dịch.

Cùng chữ ký và cùng hình dạng dòng trả về với `apps.facebook_ads.campaign_report` —
frontend gộp hai nguồn nên không được để hai payload lệch nhau.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.google_ads.models import GoogleAdsCampaignDailySpend

# Dùng chung hằng số với báo cáo theo kênh/page — xem ghi chú ở facebook_ads/campaign_report.py.
from apps.orders._repositories_reports import ADS_DAILY_COST_MULTIPLIER

PLATFORM = "google"

UNKNOWN_CAMPAIGN_LABEL = "Không rõ chiến dịch"


def aggregate_campaign_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo chiến dịch trong khoảng ngày."""
    qs = GoogleAdsCampaignDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(campaign__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values(
            "campaign__campaign_id", "campaign__name", "campaign__ad_account__name",
        )
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    result: list[dict] = []
    for row in rows:
        raw_cost = row["cost"] if row["cost"] is not None else Decimal("0")
        cost = raw_cost * ADS_DAILY_COST_MULTIPLIER
        result.append({
            "platform": PLATFORM,
            "account_name": row["campaign__ad_account__name"] or "",
            "campaign_id": row["campaign__campaign_id"] or "",
            "campaign_name": row["campaign__name"] or UNKNOWN_CAMPAIGN_LABEL,
            "cost": f"{cost:.2f}",
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            "revenue": None,
        })
    return result
```

- [ ] **Bước 5: Viết view và đấu dây url**

Tạo `backend/apps/google_ads/transport/views/google_ads_views_impl/campaign_report_view.py`
— **giống hệt** view của Task 7 nhưng đổi ba chỗ:

```python
from apps.google_ads.campaign_report import aggregate_campaign_cost
...
    account_ids = OrderRepository.accessible_google_ads_account_ids(request.user)
```

và tên hàm giữ nguyên `campaign_cost_report`. Copy toàn bộ phần còn lại (validate ngày,
`MAX_ROWS = 1000`, khối `Response`) y nguyên để hai payload không lệch.

Đấu dây: thêm import vào `.../google_ads_views_impl/__init__.py`, vào
`backend/apps/google_ads/views.py`, và vào `backend/apps/google_ads/urls.py`:

```python
    path("reports/campaign", campaign_cost_report, name="gads-campaign-cost-report"),
```

- [ ] **Bước 6: Chạy lại test + toàn bộ hai app + lint**

```bash
python -m pytest apps/google_ads apps/facebook_ads apps/orders -q --create-db
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: PASS toàn bộ.

- [ ] **Bước 7: Kiểm tra contract phân tầng không bị phá**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'pip show import-linter >/dev/null 2>&1 && lint-imports || echo "import-linter chưa cài trong image — bỏ qua, CI sẽ chạy"'
```

- [ ] **Bước 8: Commit**

```bash
git add backend
git commit -m "feat(google-ads): add campaign cost report endpoint"
```

### Task 9: Tab "Chiến dịch" trên AdsReportPage

**Files:**
- Create: `frontend/src/lib/api/ads_campaign_report.ts`
- Create: `frontend/src/features/ads/components/CampaignCostTable.tsx`
- Modify: `frontend/src/features/ads/queries.ts`
- Modify: `frontend/src/features/ads/pages/AdsReportPage.tsx`
- Test: `frontend/src/features/ads/__tests__/campaign-report.test.ts`

**Interfaces:**
- Consumes: `GET /api/facebook-ads/reports/campaign` và `GET /api/google-ads/reports/campaign`
  (Task 7, 8).
- Produces: `mergeCampaignRows`, `computeCpm`, `computeCpc`, component `CampaignCostTable`.

- [ ] **Bước 1: Viết test thất bại**

Tạo `frontend/src/features/ads/__tests__/campaign-report.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import {
  computeCpc,
  computeCpm,
  mergeCampaignRows,
  type CampaignCostRow,
} from "@/lib/api/ads_campaign_report";

const fbRow = (overrides: Partial<CampaignCostRow> = {}): CampaignCostRow => ({
  platform: "facebook",
  account_name: "TKQC A",
  campaign_id: "c1",
  campaign_name: "CD 1",
  cost: "100.00",
  impressions: 1000,
  clicks: 10,
  revenue: null,
  ...overrides,
});

describe("mergeCampaignRows", () => {
  it("gộp hai nền tảng và xếp theo chi phí giảm dần", () => {
    const rows = mergeCampaignRows(
      [fbRow({ cost: "50.00" })],
      [fbRow({ platform: "google", campaign_id: "g1", cost: "300.00" })],
    );

    expect(rows.map((r) => r.campaign_id)).toEqual(["g1", "c1"]);
  });

  it("một nền tảng lỗi vẫn hiện nền tảng còn lại", () => {
    const rows = mergeCampaignRows([fbRow()], null);
    expect(rows).toHaveLength(1);
    expect(rows[0].platform).toBe("facebook");
  });

  it("hai nền tảng cùng rỗng trả mảng rỗng", () => {
    expect(mergeCampaignRows(null, null)).toEqual([]);
  });
});

describe("CPM/CPC", () => {
  it("tính đúng khi có mẫu số", () => {
    expect(computeCpm("100.00", 1000)).toBe(100);
    expect(computeCpc("100.00", 10)).toBe(10);
  });

  it("mẫu số 0 trả null chứ không phải 0 hay NaN", () => {
    expect(computeCpm("100.00", 0)).toBeNull();
    expect(computeCpc("100.00", 0)).toBeNull();
  });
});
```

- [ ] **Bước 2: Chạy test để chắc chắn nó đỏ**

Run: `npx vitest run src/features/ads/__tests__/campaign-report.test.ts`
Expected: FAIL — không resolve được `@/lib/api/ads_campaign_report`.

- [ ] **Bước 3: Viết api client + hàm thuần**

Tạo `frontend/src/lib/api/ads_campaign_report.ts`:

```typescript
import { fetchJson } from "@/services/http";

export type AdsPlatform = "facebook" | "google";

export type CampaignCostRow = {
  platform: AdsPlatform;
  account_name: string;
  campaign_id: string;
  campaign_name: string;
  /** Chuỗi thập phân đã nhân hệ số 1.1 ở backend. */
  cost: string;
  impressions: number;
  clicks: number;
  /** null = chưa có attribution theo chiến dịch. Không bao giờ là 0 giả. */
  revenue: string | null;
};

export type CampaignCostReport = {
  since: string;
  until: string;
  dimension: "campaign";
  rows: CampaignCostRow[];
  /** true = backend đã cắt bớt dòng. Bảng thiếu ≠ bảng không có gì. */
  truncated: boolean;
};

export async function getFacebookCampaignCost(params: {
  since: string;
  until: string;
}): Promise<CampaignCostReport> {
  const query = new URLSearchParams(params).toString();
  return fetchJson(`/api/facebook-ads/reports/campaign?${query}`);
}

export async function getGoogleCampaignCost(params: {
  since: string;
  until: string;
}): Promise<CampaignCostReport> {
  const query = new URLSearchParams(params).toString();
  return fetchJson(`/api/google-ads/reports/campaign?${query}`);
}

/**
 * Gộp hai nguồn, xếp theo chi phí giảm dần.
 *
 * Nhận `null` cho nguồn lỗi: một nền tảng hỏng không được làm trắng cả bảng — số
 * của nền tảng còn lại vẫn đúng và vẫn dùng được.
 */
export function mergeCampaignRows(
  facebook: CampaignCostRow[] | null,
  google: CampaignCostRow[] | null,
): CampaignCostRow[] {
  return [...(facebook ?? []), ...(google ?? [])].sort(
    (a, b) => Number(b.cost) - Number(a.cost),
  );
}

/** null khi không có hiển thị — hiện "—" thay vì 0 hay NaN. */
export function computeCpm(cost: string, impressions: number): number | null {
  if (!impressions) return null;
  return (Number(cost) / impressions) * 1000;
}

/** null khi không có click. */
export function computeCpc(cost: string, clicks: number): number | null {
  if (!clicks) return null;
  return Number(cost) / clicks;
}
```

- [ ] **Bước 4: Chạy lại test**

Run: `npx vitest run src/features/ads/__tests__/campaign-report.test.ts`
Expected: PASS (5 test).

- [ ] **Bước 5: Thêm query hook**

Trong `frontend/src/features/ads/queries.ts`, thêm (đọc file để bám đúng kiểu
`useQuery` sẵn có, kể cả `placeholderData: keepPreviousData`):

```typescript
export function useCampaignCostReport(params: { since: string; until: string }) {
  const facebook = useQuery({
    queryKey: ["ads", "campaign-cost", "facebook", params],
    queryFn: () => getFacebookCampaignCost(params),
    placeholderData: keepPreviousData,
  });
  const google = useQuery({
    queryKey: ["ads", "campaign-cost", "google", params],
    queryFn: () => getGoogleCampaignCost(params),
    placeholderData: keepPreviousData,
  });
  return { facebook, google };
}
```

- [ ] **Bước 6: Viết bảng**

Tạo `frontend/src/features/ads/components/CampaignCostTable.tsx`. Yêu cầu cụ thể:

- Nhận props `{ rows, loading, errors: { facebook: string | null; google: string | null }, truncated }`.
- Bộ lọc nền tảng: `Tất cả` / `Facebook` / `Google` (state cục bộ, lọc trên `rows`).
- Bộ lọc TKQC: `MultiSelect` từ `@/components/ui/multi-select`, options suy ra từ
  `account_name` có trong `rows`.
- Cột: Nền tảng (badge) · Tài khoản QC · Chiến dịch · Chi phí · Hiển thị · Click · CPM · CPC.
- `loading` → `LogoSpinner` (như `overviewPanel` trong `AdsReportPage`).
- Mỗi lỗi trong `errors` → một dòng cảnh báo màu destructive **phía trên bảng**, bảng vẫn
  hiện dữ liệu của nền tảng còn lại.
- `truncated` → dòng ghi chú "Đã hiển thị 1000 chiến dịch chi nhiều nhất."
- Rỗng → "Chưa có dữ liệu chiến dịch trong khoảng đã chọn."
- CPM/CPC `null` → hiện `—`.
- Tiền format bằng đúng helper mà `PageCostTable.tsx` / các bảng ads khác đang dùng — đọc
  file đó trước, **không** tự viết formatter mới.

- [ ] **Bước 7: Gắn tab vào AdsReportPage**

Trong `frontend/src/features/ads/pages/AdsReportPage.tsx`, thêm phần tử thứ tư vào mảng
`tabs` (sau `digital-detail`):

```tsx
    {
      key: "campaign",
      label: t("adsReport.tabCampaign", "Chiến dịch"),
      content: (
        <div className="pt-4">
          <CampaignCostTable
            rows={campaignRows}
            loading={campaignQuery.facebook.isLoading || campaignQuery.google.isLoading}
            errors={{
              facebook: toErrorMessage(campaignQuery.facebook.error),
              google: toErrorMessage(campaignQuery.google.error),
            }}
            truncated={
              Boolean(campaignQuery.facebook.data?.truncated) ||
              Boolean(campaignQuery.google.data?.truncated)
            }
          />
        </div>
      ),
    },
```

với

```tsx
  const campaignQuery = useCampaignCostReport({
    since: dateFilter.from,
    until: dateFilter.to,
  });
  const campaignRows = useMemo(
    () =>
      mergeCampaignRows(
        campaignQuery.facebook.data?.rows ?? null,
        campaignQuery.google.data?.rows ?? null,
      ),
    [campaignQuery.facebook.data, campaignQuery.google.data],
  );
```

`toErrorMessage` là helper cục bộ chuyển `unknown` → `string | null`, viết cùng kiểu với
khối `const error = reportQuery.error ? ... : null` đang có sẵn trong file.

Tab này dùng bộ lọc ngày chung nên **không** thêm vào điều kiện `showSharedFilters`.

- [ ] **Bước 8: Chạy test + typecheck frontend**

```bash
npx vitest run src/features/ads
npx tsc --noEmit
npm run lint:strict
```

Expected: PASS. `RequireAuth.test.tsx` và `use-orders-data.realtime-upsert-filters.test.tsx`
đỏ sẵn trên master — không liên quan.

- [ ] **Bước 9: Commit và push MR4**

```bash
git add -A
git commit -m "feat(ads-report): add campaign dimension tab merging facebook and google"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat(ads-report): campaign cost tab (facebook + google)" \
  origin feat/ads-campaign-report
```

---

## Triển khai lên production

Thứ tự bắt buộc, **không** gộp bước:

1. Merge MR1 → chạy migration `0116_order_fb_ad_id` (index `CONCURRENTLY`, không khoá bảng
   `orders`). Nếu bước tạo index hỏng giữa chừng, gỡ theo spec 27/07 §14.1 trước khi chạy lại.
2. Chạy **tay** một lượt `python manage.py sync_all_ad_catalog` (hoặc gọi task tương ứng)
   trước khi bật beat `facebook-ads-sync-ad-spend` — catalog rỗng làm task đầu tiên phải
   lazy-fetch vài trăm ad và chắc chắn quá `soft_time_limit`.
3. Merge MR2 → lượt `sync_all_accounts_google` kế tiếp sẽ phát hiện thêm tài khoản. **Kiểm
   tra số tài khoản trước/sau** để biết thực sự có bao nhiêu TKQC từng bị bỏ sót.
4. Merge MR3 → chạy migration `0002_campaign_grain`, rồi chạy tay
   `python manage.py backfill_gads_campaign_spend --since <hôm nay - 365> --until <hôm nay>`
   vào **giờ thấp điểm**. Chỉ bật beat sau khi backfill xong.
5. Merge MR4 → không có migration, không cần thao tác vận hành.

Không có biến môi trường mới nào trong đợt này, nên không phải cập nhật file compose.
