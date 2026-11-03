# Ads Report Scope Fix + CTR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nhân viên ads thấy đúng chi phí của TKQC được gán qua kênh (đang thấy bảng trắng), đồng thời thêm cột CTR và ô tìm kiếm chiến dịch.

**Architecture:** Nguyên nhân gốc là luật phân quyền lọc theo PK `FacebookAdAccount`, trong khi cùng một TKQC thật tồn tại nhiều dòng (unique là `(app, account_id)`). Fix **tận gốc** trong `OrderRepository`: PK từ mapping kênh được trải sang mọi bản sao của cùng một tài khoản thật, thông qua `AdsSpendPort` sẵn có (mỗi app quảng cáo tự hiện thực phần truy vấn model của mình). Nhờ vậy mọi caller hiện tại và tương lai tự đúng, không ai phải nhớ opt-in. Phần còn lại là frontend thuần.

**Tech Stack:** Django 5 + DRF (backend), React 19 + TypeScript + TanStack Query + vitest (frontend), PostgreSQL 16, Docker cho test.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-08-10-ads-report-scope-duplicate-accounts-design.md`.
- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order-wt-ads-scope`, nhánh `fix/ads-report-scope-duplicate-accounts`.
- **KHÔNG** tạo migration, **KHÔNG** đổi model. Toàn bộ fix ở tầng đọc.
- **Phân tầng bắt buộc** (`backend/.importlinter`): `apps.facebook_ads` và `apps.google_ads` nằm CÙNG tầng trên cùng — hai app này **không được import lẫn nhau**. Cả hai được import xuống `apps.orders`, `apps.users`, `apps.channels_core`; chiều ngược lại thì không. Vì vậy `apps.orders` chỉ được chạm model quảng cáo **qua port** `apps/orders/contracts/ads_spend_port.py` (pattern trả nợ Lô 5 của repo), và test dựng fixture bằng model quảng cáo phải đặt trong app quảng cáo.
- **KHÔNG được thêm dòng nào vào `ignore_imports`** của `.importlinter` để làm lint xanh. File đó ghi rõ: thêm vào KHỐI B = tạo nợ mới, phải bị chặn ở review. Lint đỏ nghĩa là code đặt sai chỗ.
- Comment và docstring viết **tiếng Việt**, theo đúng văn phong file xung quanh (giải thích *tại sao*, không mô tả lại code).
- Commit message tiếng Anh, ngắn gọn, **không** kèm dòng `Co-Authored-By`.
- Ruff bắt buộc xanh theo ruleset đầy đủ trong `backend/pyproject.toml`.
- **Repo hiện KHÔNG có CI** — `.gitlab-ci.yml` đã bị xoá ở commit `eb15fe28` và không có trên `origin/master`. Không có gì chặn tự động: ruff, `lint-imports` và test **phải chạy tay** ở mỗi task, kết quả dán vào báo cáo. Bỏ qua một bước lint ở đây là bỏ qua thật, không có lưới nào đỡ.
- `import-linter` được ghim trong `backend/requirements-dev.txt` nhưng **không có sẵn** trong image `desi-backend-test:latest` — lệnh lint bên dưới đã kèm bước `pip install`.

### Lệnh chạy test backend

Hai container test đang chạy sẵn trên network `desi-test-net`: `desi-test-pg`, `desi-test-redis`. Image: `desi-backend-test:latest`.

Chạy từ Git Bash, tại thư mục worktree:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_adsscope \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Bẫy đã biết: `POSTGRES_DB` phải riêng cho mỗi lần chạy đồng thời, nếu không sẽ dính `DuplicateDatabase` và fail hàng loạt **giả**. `REDIS_CACHE_DB` chỉ nhận 0-15.

Ruff (image test không có ruff):

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Import linter:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)/backend:/app" -w //app \
  desi-backend-test:latest sh -c 'pip install -q import-linter && python -m importlinter.cli lint-imports'
```

### Lệnh chạy test frontend

Worktree này là worktree **anh em** (nằm cạnh repo chính) nên dùng được junction node_modules. Chạy MỘT LẦN trước task frontend đầu tiên:

```powershell
New-Item -ItemType Junction `
  -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-ads-scope\frontend\node_modules" `
  -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Sau đó, từ thư mục `frontend`: `npx vitest run <file>`, `npx tsc --noEmit`.

Bẫy: 2 file test đỏ **sẵn** trên master sạch — `src/app/__tests__/RequireAuth.test.tsx` (2 test) và `src/features/orders/__tests__/use-orders-data.realtime-upsert-filters.test.tsx` (5 test). Đừng nghi thay đổi của mình khi thấy chúng đỏ.

---

## File Structure

**Backend — sửa (không tạo file nguồn mới):**
- `backend/apps/orders/contracts/ads_spend_port.py` — thêm `AdAccountScope` + method `account_scope` vào Protocol.
- `backend/apps/facebook_ads/adapters.py` — hiện thực `account_scope` theo `account_id`.
- `backend/apps/google_ads/adapters.py` — hiện thực `account_scope` theo `customer_id`.
- `backend/apps/orders/_repositories_reports.py:893-933` — hai hàm phạm vi đi qua port.
- `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/campaign_report_view.py`
- `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py`
- `backend/apps/google_ads/transport/views/google_ads_views_impl/campaign_report_view.py`

**Backend — test tạo mới:**
- `backend/apps/facebook_ads/tests/test_ad_account_scope.py`
- `backend/apps/google_ads/tests/test_ad_account_scope.py`

> Test của luật phạm vi đặt ở app ads chứ **không** ở `apps/orders/tests/`: test đó phải dựng
> fixture bằng model của `facebook_ads`/`google_ads`, mà orders nằm DƯỚI hai app này. Đặt bên
> orders sẽ phải thêm dòng miễn trừ vào `.importlinter`; đặt ở app ads thì chiều import hợp lệ
> sẵn, không tạo nợ mới.

**Backend — test sửa:**
- `backend/apps/facebook_ads/tests/test_campaign_report_api.py`
- `backend/apps/facebook_ads/tests/test_page_report_api.py`
- `backend/apps/google_ads/tests/test_campaign_report_api.py`

**Frontend — tạo mới:**
- `frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
- `frontend/src/features/channels/__tests__/group-ad-account-options.test.ts`

**Frontend — sửa:**
- `frontend/src/lib/api/ads_campaign_report.ts` — `computeCtr`, field `accessible_accounts`.
- `frontend/src/features/ads/components/CampaignCostTable.tsx` — cột CTR, ô tìm kiếm, empty state.
- `frontend/src/features/ads/pages/AdsReportPage.tsx` — truyền `accessibleAccounts` xuống bảng.
- `frontend/src/features/channels/utils.ts` — `groupAdAccountOptions`.
- `frontend/src/features/channels/ChannelManageDialog.tsx`, `ChannelCreateDialog.tsx` — dùng helper gom trùng.

---

### Task 1: Port cấp phạm vi TKQC + hai adapter

**Files:**
- Modify: `backend/apps/orders/contracts/ads_spend_port.py`
- Modify: `backend/apps/facebook_ads/adapters.py`
- Modify: `backend/apps/google_ads/adapters.py`
- Test: `backend/apps/facebook_ads/tests/test_ad_account_scope.py` (tạo mới), `backend/apps/google_ads/tests/test_ad_account_scope.py` (tạo mới)

**Interfaces:**
- Consumes: `register_ads_spend_provider` / `ads_spend_provider` + `AdsSpendSource` đã có sẵn trong `ads_spend_port.py`; hai adapter đã được đăng ký ở `AppConfig.ready()` — **không** đụng phần wiring đó.
- Produces:
  - `AdAccountScope` — NamedTuple `(ids: list[int] | None, count: int | None)`, export từ `apps.orders.contracts.ads_spend_port`.
  - Method mới trên Protocol `AdsSpendPort`: `account_scope(self, account_ids: list[int]) -> AdAccountScope`.
  - `FacebookAdsSpendAdapter.account_scope` (gom theo `FacebookAdAccount.account_id`).
  - `GoogleAdsSpendAdapter.account_scope` (gom theo `GoogleAdsAccount.customer_id`) — kiểm tra tên class thật trong `backend/apps/google_ads/adapters.py` và dùng đúng tên đó.

- [ ] **Step 1: Viết test thất bại cho adapter Facebook**

Tạo `backend/apps/facebook_ads/tests/test_ad_account_scope.py`:

```python
"""Phạm vi TKQC phải tính theo id ngoài của Facebook, không theo PK.

Cùng một tài khoản quảng cáo THẬT tồn tại nhiều dòng `FacebookAdAccount`: unique là
`(app, account_id)` nên mỗi FacebookApp có quyền vào cùng Business Manager tạo một
dòng riêng. Mapping kênh chỉ trỏ được MỘT dòng, trong khi ad + chi tiêu nằm dưới dòng
của app khác — prod 2026-08-10: kênh trỏ PK của app 1, toàn bộ chi tiêu nằm dưới app
4, nhân viên ads thấy bảng trắng mà không có tín hiệu nào.
"""

from django.test import TestCase

from apps.facebook_ads.adapters import FacebookAdsSpendAdapter
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp


class FacebookAccountScopeTests(TestCase):
    def setUp(self):
        self.app1 = FacebookApp.objects.create(
            name="App 1", app_id="1", app_secret="s", access_token="t",
        )
        self.app4 = FacebookApp.objects.create(
            name="App 4", app_id="4", app_secret="s", access_token="t",
        )
        # Cùng một TKQC thật, hai dòng vì unique là (app, account_id).
        self.shell = FacebookAdAccount.objects.create(
            app=self.app1, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.real = FacebookAdAccount.objects.create(
            app=self.app4, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.other = FacebookAdAccount.objects.create(
            app=self.app4, name="TKQC khác", account_id="999", fb_id="act_999",
        )
        self.adapter = FacebookAdsSpendAdapter()

    def test_expands_to_every_duplicate_of_the_same_account(self):
        scope = self.adapter.account_scope([self.shell.id])
        self.assertEqual(sorted(scope.ids), sorted([self.shell.id, self.real.id]))

    def test_counts_real_accounts_not_duplicate_rows(self):
        # 2 dòng DB nhưng chỉ 1 TKQC thật — đếm len(ids) sẽ nói dối.
        self.assertEqual(self.adapter.account_scope([self.shell.id]).count, 1)

    def test_does_not_leak_unrelated_accounts(self):
        self.assertNotIn(self.other.id, self.adapter.account_scope([self.shell.id]).ids)

    def test_empty_input_gives_empty_scope(self):
        scope = self.adapter.account_scope([])
        self.assertEqual(scope.ids, [])
        self.assertEqual(scope.count, 0)

    def test_two_real_accounts_count_as_two(self):
        scope = self.adapter.account_scope([self.shell.id, self.other.id])
        self.assertEqual(scope.count, 2)
        self.assertEqual(
            sorted(scope.ids), sorted([self.shell.id, self.real.id, self.other.id]),
        )
```

- [ ] **Step 2: Viết test thất bại cho adapter Google**

Tạo `backend/apps/google_ads/tests/test_ad_account_scope.py`:

```python
"""Bản Google của phạm vi TKQC — xem apps/facebook_ads/tests/test_ad_account_scope.py.

`GoogleAdsAccount` unique theo `(config, customer_id)` nên cùng một tài khoản thật
được hai MCC quét sẽ thành hai dòng, đúng lớp lỗi của bản Facebook.
"""

from django.test import TestCase

from apps.google_ads.adapters import GoogleAdsSpendAdapter
from apps.google_ads.models import GoogleAdsAccount, GoogleAdsConfig


class GoogleAccountScopeTests(TestCase):
    def setUp(self):
        self.config_a = GoogleAdsConfig.objects.create(
            name="MCC A", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.config_b = GoogleAdsConfig.objects.create(
            name="MCC B", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )
        self.shell = GoogleAdsAccount.objects.create(
            config=self.config_a, name="Delax.vn", customer_id="3000000000",
        )
        self.real = GoogleAdsAccount.objects.create(
            config=self.config_b, name="Delax.vn", customer_id="3000000000",
        )
        self.other = GoogleAdsAccount.objects.create(
            config=self.config_b, name="Khác", customer_id="4000000000",
        )
        self.adapter = GoogleAdsSpendAdapter()

    def test_expands_to_every_duplicate_of_the_same_account(self):
        scope = self.adapter.account_scope([self.shell.id])
        self.assertEqual(sorted(scope.ids), sorted([self.shell.id, self.real.id]))

    def test_counts_real_accounts_not_duplicate_rows(self):
        self.assertEqual(self.adapter.account_scope([self.shell.id]).count, 1)

    def test_does_not_leak_unrelated_accounts(self):
        self.assertNotIn(self.other.id, self.adapter.account_scope([self.shell.id]).ids)

    def test_empty_input_gives_empty_scope(self):
        scope = self.adapter.account_scope([])
        self.assertEqual(scope.ids, [])
        self.assertEqual(scope.count, 0)
```

> Tên class adapter Google phải khớp file thật — mở `backend/apps/google_ads/adapters.py` đọc trước, đừng đoán.

- [ ] **Step 3: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_account_scope.py apps/google_ads/tests/test_ad_account_scope.py -q --create-db`
Expected: FAIL — `AttributeError: 'FacebookAdsSpendAdapter' object has no attribute 'account_scope'`

- [ ] **Step 4: Thêm AdAccountScope + method vào Protocol**

Trong `backend/apps/orders/contracts/ads_spend_port.py`, thêm `NamedTuple` vào import `typing` (thành `from typing import NamedTuple, Protocol`), rồi thêm ngay TRƯỚC `class AdsSpendPort`:

```python
class AdAccountScope(NamedTuple):
    """Phạm vi tài khoản quảng cáo của một user.

    `ids`: PK dùng để lọc, đã trải sang MỌI bản sao của cùng một tài khoản thật.
    `None` = không lọc gì cả (user xem toàn bộ).

    `count`: số tài khoản THẬT, đếm theo id ngoài của nền tảng. Tách khỏi `len(ids)`
    vì `ids` có cả bản sao — hiển thị `len(ids)` sẽ báo "2 tài khoản" cho người chỉ
    được gán 1.
    """

    ids: list[int] | None
    count: int | None
```

Thêm method vào Protocol `AdsSpendPort`:

```python
    def account_scope(self, account_ids: list[int]) -> AdAccountScope:
        """Trải list PK sang mọi bản sao của cùng tài khoản thật, kèm số đếm thật.

        Cùng một tài khoản quảng cáo được lưu thành nhiều dòng: `FacebookAdAccount`
        unique theo `(app, account_id)`, `GoogleAdsAccount` theo `(config,
        customer_id)`. Mapping kênh chỉ trỏ được một dòng, còn dữ liệu chi tiêu có thể
        treo dưới dòng khác — nên lọc thẳng theo PK là cách làm báo cáo trắng bảng mà
        không để lại dấu vết.

        `account_ids` rỗng → `AdAccountScope([], 0)`.
        """
        ...
```

- [ ] **Step 5: Hiện thực ở adapter Facebook**

Trong `backend/apps/facebook_ads/adapters.py`, thêm method vào `FacebookAdsSpendAdapter` (giữ nguyên kiểu import model LƯỜI như các method sẵn có):

```python
    def account_scope(self, account_ids: list[int]):
        from apps.facebook_ads.models import FacebookAdAccount
        from apps.orders.contracts.ads_spend_port import AdAccountScope

        external_ids = list(
            FacebookAdAccount.objects.filter(id__in=account_ids)
            .order_by()  # phòng thủ: model chưa có Meta.ordering, thêm sau vẫn không phá DISTINCT
            .values_list("account_id", flat=True)
            .distinct()
        )
        ids = list(
            FacebookAdAccount.objects.filter(account_id__in=external_ids)
            .order_by()
            .values_list("id", flat=True)
            .distinct()
        )
        return AdAccountScope(ids=ids, count=len(external_ids))
```

- [ ] **Step 6: Hiện thực ở adapter Google**

Trong `backend/apps/google_ads/adapters.py`, thêm vào class adapter (dùng đúng tên class có sẵn):

```python
    def account_scope(self, account_ids: list[int]):
        from apps.google_ads.models import GoogleAdsAccount
        from apps.orders.contracts.ads_spend_port import AdAccountScope

        customer_ids = list(
            GoogleAdsAccount.objects.filter(id__in=account_ids)
            .order_by()
            .values_list("customer_id", flat=True)
            .distinct()
        )
        ids = list(
            GoogleAdsAccount.objects.filter(customer_id__in=customer_ids)
            .order_by()
            .values_list("id", flat=True)
            .distinct()
        )
        return AdAccountScope(ids=ids, count=len(customer_ids))
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_account_scope.py apps/google_ads/tests/test_ad_account_scope.py -q --create-db`
Expected: PASS (9 test)

- [ ] **Step 8: Ruff + import linter**

Run: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`
Expected: `All checks passed!`

Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest sh -c 'pip install -q import-linter && python -m importlinter.cli lint-imports'`
Expected: `Contracts: 1 kept, 0 broken.` **KHÔNG** được thêm dòng nào vào `ignore_imports` để làm nó xanh — nếu đỏ nghĩa là đặt code sai chỗ.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/orders/contracts/ads_spend_port.py backend/apps/facebook_ads backend/apps/google_ads
git commit -m "feat(ads): add ad account scope to ads spend port"
```

---

### Task 2: Luật phân quyền dùng phạm vi đã trải

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py:893-933`
- Test: `backend/apps/facebook_ads/tests/test_ad_account_scope.py` (thêm class), `backend/apps/google_ads/tests/test_ad_account_scope.py` (thêm class)

**Interfaces:**
- Consumes: `AdAccountScope`, `ads_spend_provider(AdsSpendSource.FACEBOOK|GOOGLE).account_scope(...)` (Task 1). `ads_spend_provider` và `AdsSpendSource` đã được import sẵn ở đầu `_repositories_reports.py` — kiểm tra rồi thêm `AdAccountScope` vào cùng dòng import đó.
- Produces trên `OrderRepository`:
  - `ads_ad_account_scope(user) -> AdAccountScope`
  - `google_ads_account_scope(user) -> AdAccountScope`
  - `accessible_ads_ad_account_ids(user) -> list[int] | None` — **giữ nguyên chữ ký**, nay trả list đã trải.
  - `accessible_google_ads_account_ids(user) -> list[int] | None` — như trên.

> Đây là chỗ sửa quan trọng nhất của MR: vá tận gốc nên mọi caller hiện tại và tương lai đều đúng, không ai phải nhớ tự trải.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/facebook_ads/tests/test_ad_account_scope.py`. Bổ sung import:

```python
from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.orders.repositories import OrderRepository
from apps.users.models import DataScope
from apps.users.test_helpers import make_user_with_sections
```

Thêm class:

```python
class OrderRepositoryAdsScopeTests(TestCase):
    """Luật phân quyền phải trả PK đã trải, không phải PK thô của mapping kênh."""

    def setUp(self):
        self.app1 = FacebookApp.objects.create(
            name="App 1", app_id="1", app_secret="s", access_token="t",
        )
        self.app4 = FacebookApp.objects.create(
            name="App 4", app_id="4", app_secret="s", access_token="t",
        )
        self.shell = FacebookAdAccount.objects.create(
            app=self.app1, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.real = FacebookAdAccount.objects.create(
            app=self.app4, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.channel = Channel.objects.create(code="fb-loi", name="Facebook Lợi")
        # Kênh trỏ đúng bản "vỏ rỗng" của app 1 — tái hiện dữ liệu prod.
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.shell.id,
        )

    def _scoped_user(self, username="ads_scoped"):
        user = make_user_with_sections(username, ["ads-report"], scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.channel, is_active=True)
        return user

    def test_all_scope_user_is_not_filtered(self):
        user = make_user_with_sections("ads_all", ["ads-report"], scope=DataScope.ALL)
        scope = OrderRepository.ads_ad_account_scope(user)
        self.assertIsNone(scope.ids)
        self.assertIsNone(scope.count)
        self.assertIsNone(OrderRepository.accessible_ads_ad_account_ids(user))

    def test_scoped_user_gets_expanded_ids(self):
        ids = OrderRepository.accessible_ads_ad_account_ids(self._scoped_user())
        self.assertEqual(sorted(ids), sorted([self.shell.id, self.real.id]))

    def test_scope_counts_real_accounts(self):
        self.assertEqual(OrderRepository.ads_ad_account_scope(self._scoped_user()).count, 1)

    def test_user_without_channel_gets_empty_scope(self):
        user = make_user_with_sections("ads_none", ["ads-report"], scope=DataScope.CHANNEL)
        scope = OrderRepository.ads_ad_account_scope(user)
        self.assertEqual(scope.ids, [])
        self.assertEqual(scope.count, 0)
```

Thêm vào `backend/apps/google_ads/tests/test_ad_account_scope.py` cùng bộ import tương ứng và class:

```python
class OrderRepositoryGoogleAdsScopeTests(TestCase):
    def setUp(self):
        self.config_a = GoogleAdsConfig.objects.create(
            name="MCC A", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.config_b = GoogleAdsConfig.objects.create(
            name="MCC B", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )
        self.shell = GoogleAdsAccount.objects.create(
            config=self.config_a, name="Delax.vn", customer_id="3000000000",
        )
        self.real = GoogleAdsAccount.objects.create(
            config=self.config_b, name="Delax.vn", customer_id="3000000000",
        )
        self.channel = Channel.objects.create(code="g-delax", name="Google Delax")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.shell.id,
        )

    def _scoped_user(self, username="gads_scoped"):
        user = make_user_with_sections(username, ["ads-report"], scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.channel, is_active=True)
        return user

    def test_all_scope_user_is_not_filtered(self):
        user = make_user_with_sections("gads_all", ["ads-report"], scope=DataScope.ALL)
        self.assertIsNone(OrderRepository.accessible_google_ads_account_ids(user))

    def test_scoped_user_gets_expanded_ids(self):
        ids = OrderRepository.accessible_google_ads_account_ids(self._scoped_user())
        self.assertEqual(sorted(ids), sorted([self.shell.id, self.real.id]))

    def test_scope_counts_real_accounts(self):
        self.assertEqual(
            OrderRepository.google_ads_account_scope(self._scoped_user()).count, 1,
        )
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_account_scope.py apps/google_ads/tests/test_ad_account_scope.py -q --create-db`
Expected: FAIL — `AttributeError: type object 'OrderRepository' has no attribute 'ads_ad_account_scope'`, và `test_scoped_user_gets_expanded_ids` chỉ trả `[shell.id]`

- [ ] **Step 3: Sửa `_repositories_reports.py`**

Thêm `AdAccountScope` vào dòng import port sẵn có ở đầu file:

```python
from apps.orders.contracts.ads_spend_port import (
    AdAccountScope,
    AdsSpendSource,
    ads_spend_provider,
)
```

Thay toàn bộ hai classmethod công khai (`accessible_ads_ad_account_ids` ~dòng 893, `accessible_google_ads_account_ids` ~dòng 923) bằng:

```python
    @classmethod
    def ads_ad_account_scope(cls, user) -> AdAccountScope:
        """Phạm vi TKQC Facebook user được xem trong báo cáo ads.

        Trả ``ids=None`` cho user xem-toàn-bộ thay vì list: ``_accessible_ads_ad_account_ids``
        chỉ liệt kê TKQC ĐÃ map vào kênh, nên dùng thẳng nó sẽ làm biến mất chi phí
        của TKQC chưa map — đúng kiểu hụt số âm thầm mà báo cáo này sinh ra để chữa.

        Với user bị giới hạn, PK từ mapping kênh được TRẢI qua port sang mọi bản sao
        của cùng một TKQC thật. Không trải thì user vẫn mù: mapping trỏ được đúng một
        dòng, còn ad và chi tiêu có thể treo dưới dòng của app khác (prod 2026-08-10).
        """
        if _user_sees_all_ads_channels(user):
            return AdAccountScope(ids=None, count=None)
        mapped_ids = cls._accessible_ads_ad_account_ids(user)
        return ads_spend_provider(AdsSpendSource.FACEBOOK).account_scope(mapped_ids)

    @classmethod
    def accessible_ads_ad_account_ids(cls, user) -> list[int] | None:
        """TKQC user được xem. ``None`` = không cần lọc. Giữ chữ ký cũ cho call-site."""
        return cls.ads_ad_account_scope(user).ids

    @classmethod
    def google_ads_account_scope(cls, user) -> AdAccountScope:
        """Phạm vi TKQC Google. Cùng luật với bản Facebook — xem ``ads_ad_account_scope``."""
        if _user_sees_all_ads_channels(user):
            return AdAccountScope(ids=None, count=None)
        mapped_ids = cls._accessible_google_ads_account_ids(user)
        return ads_spend_provider(AdsSpendSource.GOOGLE).account_scope(mapped_ids)

    @classmethod
    def accessible_google_ads_account_ids(cls, user) -> list[int] | None:
        """TKQC Google user được xem. ``None`` = không cần lọc."""
        return cls.google_ads_account_scope(user).ids
```

Giữ nguyên hai staticmethod riêng tư `_accessible_ads_ad_account_ids` / `_accessible_google_ads_account_ids` — chúng vẫn là nguồn "TKQC nào map vào kênh của user".

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_ad_account_scope.py apps/google_ads/tests/test_ad_account_scope.py -q --create-db`
Expected: PASS (16 test)

- [ ] **Step 5: Regression — báo cáo ads theo kênh không được đổi số**

Run: `... python -m pytest apps/orders/tests/test_ads_reports.py apps/orders/tests/test_ads_report_channel_scope.py -q --create-db`
Expected: PASS. Nếu file thứ hai không tồn tại thì bỏ khỏi lệnh (kiểm tra bằng `ls apps/orders/tests/ | grep ads`).

- [ ] **Step 6: Ruff + import linter, rồi commit**

Run cả hai lệnh lint như Task 1 Step 8. Expected: sạch.

```bash
git add backend/apps/orders backend/apps/facebook_ads backend/apps/google_ads
git commit -m "fix(ads): expand scoped ad accounts to every duplicate row"
```

---

### Task 3: View báo cáo Facebook trả thêm `accessible_accounts`

**Files:**
- Modify: `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/campaign_report_view.py:40-49`
- Modify: `backend/apps/facebook_ads/transport/views/facebook_ads_views_impl/page_report_view.py:47-55`
- Test: `backend/apps/facebook_ads/tests/test_campaign_report_api.py` (thêm class), `backend/apps/facebook_ads/tests/test_page_report_api.py` (thêm 1 test vào `PageReportScopeTests`)

**Interfaces:**
- Consumes: `OrderRepository.ads_ad_account_scope(user) -> AdAccountScope` (Task 2).
- Produces: payload `/api/facebook-ads/reports/campaign` có thêm khoá `accessible_accounts: int | None`.

> `page_report_view.py` **không** đổi gì ngoài việc tiếp tục gọi `accessible_ads_ad_account_ids` — Task 2 đã sửa hàm đó nên nó tự đúng. Test ở Step 5 là để chứng minh điều đó, không phải để sửa view.

- [ ] **Step 1: Viết test thất bại cho campaign**

Thêm vào cuối `backend/apps/facebook_ads/tests/test_campaign_report_api.py`. Bổ sung import ở đầu file:

```python
from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.users.models import DataScope
```

Rồi thêm class:

```python
class CampaignReportScopeTests(TestCase):
    """NV ads phải thấy chi tiêu của TKQC được gán, kể cả khi kênh trỏ bản sao khác.

    Toàn bộ `CampaignReportApiTests` dùng user scope ALL nên nhánh lọc
    `if ad_account_ids is not None` chưa từng chạy — đúng lỗ hổng đã để lọt bug prod
    2026-08-10 (kênh map PK của app 1, chi tiêu nằm dưới PK của app 4).
    """

    def setUp(self):
        self.app1 = FacebookApp.objects.create(
            name="App 1", app_id="1", app_secret="s", access_token="t",
        )
        self.app4 = FacebookApp.objects.create(
            name="App 4", app_id="4", app_secret="s", access_token="t",
        )
        self.shell = FacebookAdAccount.objects.create(
            app=self.app1, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.real = FacebookAdAccount.objects.create(
            app=self.app4, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        # Chi tiêu treo dưới bản của app 4 — bản mà kênh KHÔNG trỏ tới.
        mine = FacebookAd.objects.create(
            fb_ad_id="M1", ad_account=self.real, campaign_id="c1", campaign_name="CD của tôi",
        )
        FacebookAdDailySpend.objects.create(
            ad=mine, date=date(2026, 8, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )

        # TKQC của kênh khác — không được lộ.
        self.other_account = FacebookAdAccount.objects.create(
            app=self.app4, name="TKQC khác", account_id="999", fb_id="act_999",
        )
        theirs = FacebookAd.objects.create(
            fb_ad_id="T1", ad_account=self.other_account,
            campaign_id="c9", campaign_name="CD người khác",
        )
        FacebookAdDailySpend.objects.create(
            ad=theirs, date=date(2026, 8, 1), spend=Decimal("999.00"),
        )

        self.channel = Channel.objects.create(code="fb-loi", name="Facebook Lợi")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.shell.id,
        )
        self.user = make_user_with_sections(
            "fb_campaign_scoped", ["ads-report"], scope=DataScope.CHANNEL,
        )
        UserChannel.objects.create(user=self.user, channel=self.channel, is_active=True)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _get(self):
        return self.client.get(
            reverse("fb-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_sees_spend_even_though_channel_maps_a_different_app_row(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["campaign_name"] for row in response.data["rows"]], ["CD của tôi"],
        )

    def test_does_not_leak_other_channel_spend(self):
        names = [row["campaign_name"] for row in self._get().data["rows"]]
        self.assertNotIn("CD người khác", names)

    def test_reports_real_account_count_not_duplicate_rows(self):
        # 2 dòng DB cho cùng 1 TKQC thật → phải trả 1.
        self.assertEqual(self._get().data["accessible_accounts"], 1)


class CampaignReportAccessibleAccountsTests(TestCase):
    """`accessible_accounts` = null nghĩa là KHÔNG bị lọc, khác hẳn 0."""

    def setUp(self):
        self.client = APIClient()

    def _get_as(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(
            reverse("fb-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_all_scope_reports_null(self):
        user = make_user_with_sections("fb_campaign_all", ["ads-report"])
        self.assertIsNone(self._get_as(user).data["accessible_accounts"])

    def test_scoped_user_without_channel_reports_zero(self):
        user = make_user_with_sections(
            "fb_campaign_nochan", ["ads-report"], scope=DataScope.CHANNEL,
        )
        self.assertEqual(self._get_as(user).data["accessible_accounts"], 0)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/facebook_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: FAIL — `KeyError: 'accessible_accounts'`. Lưu ý `test_sees_spend_even_though_channel_maps_a_different_app_row` có thể đã PASS sẵn nhờ Task 2 — đúng như mong đợi, nó là test hồi quy cho fix đó.

- [ ] **Step 3: Sửa `campaign_report_view.py`**

Thay khối cuối của view:

```python
    scope = OrderRepository.ads_ad_account_scope(request.user)
    rows = aggregate_campaign_cost(since, until, scope.ids)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "campaign",
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        # null = không bị lọc. Số 0 nghĩa là user KHÔNG được gán TKQC nào — hai
        # trạng thái này cùng cho ra bảng rỗng nên phải phân biệt được ở client,
        # nếu không "không tiêu đồng nào" và "không có quyền" trông y hệt nhau.
        "accessible_accounts": scope.count,
    })
```

Giữ nguyên import `OrderRepository` đang có.

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/facebook_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: PASS

- [ ] **Step 5: Test chứng minh báo cáo Theo page cũng đã được chữa**

Thêm vào class `PageReportScopeTests` trong `backend/apps/facebook_ads/tests/test_page_report_api.py`:

```python
    def test_sees_spend_when_channel_maps_a_duplicate_row_of_the_same_account(self):
        """Cùng lỗ hổng với báo cáo chiến dịch: hai view dùng chung luật phạm vi."""
        other_app = FacebookApp.objects.create(
            name="App khác", app_id="dup-app", app_secret="s", access_token="t",
        )
        # Bản sao của `reachable_account` dưới app khác — nơi ad thật nằm.
        duplicate = FacebookAdAccount.objects.create(
            app=other_app,
            name=self.reachable_account.name,
            account_id=self.reachable_account.account_id,
            fb_id=self.reachable_account.fb_id,
        )
        page = FacebookPage.objects.create(fb_page_id="d1", name="Dup Page")
        ad = FacebookAd.objects.create(fb_ad_id="D1", ad_account=duplicate, page=page)
        FacebookAdDailySpend.objects.create(
            ad=ad, date=date(2026, 7, 1), spend=Decimal("200.00"),
        )

        response = self.client.get(
            "/api/facebook-ads/reports/page",
            {"since": "2026-07-01", "until": "2026-07-01"},
        )
        rows = {r["page_id"]: r for r in response.json()["rows"]}
        self.assertEqual(rows["d1"]["cost"], "220.00")  # 200.00 × 1.1
```

- [ ] **Step 6: Chạy test + ruff, rồi commit**

Run: `... python -m pytest apps/facebook_ads -q --create-db`
Expected: PASS toàn bộ

Run lệnh ruff như Task 1 Step 8. Expected: sạch.

```bash
git add backend/apps/facebook_ads
git commit -m "feat(ads): report accessible account count in campaign payload"
```

---

### Task 4: View báo cáo Google trả thêm `accessible_accounts`

**Files:**
- Modify: `backend/apps/google_ads/transport/views/google_ads_views_impl/campaign_report_view.py:40-49`
- Test: `backend/apps/google_ads/tests/test_campaign_report_api.py` (thêm class)

**Interfaces:**
- Consumes: `OrderRepository.google_ads_account_scope(user) -> AdAccountScope` (Task 2).
- Produces: payload `/api/google-ads/reports/campaign` có thêm `accessible_accounts: int | None`.

- [ ] **Step 1: Viết test thất bại**

Thêm import còn thiếu ở đầu `backend/apps/google_ads/tests/test_campaign_report_api.py` (đọc file trước, đừng import trùng thứ đã có):

```python
from django.urls import reverse

from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.google_ads.models import GoogleAdsCampaign, GoogleAdsCampaignDailySpend
from apps.users.models import DataScope
```

Thêm class:

```python
class GoogleCampaignReportScopeTests(TestCase):
    """NV ads thấy đúng chi tiêu dù kênh trỏ bản sao thuộc MCC khác."""

    def setUp(self):
        self.config_a = GoogleAdsConfig.objects.create(
            name="MCC A", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.config_b = GoogleAdsConfig.objects.create(
            name="MCC B", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )
        self.shell = GoogleAdsAccount.objects.create(
            config=self.config_a, name="Delax.vn", customer_id="3000000000",
        )
        self.real = GoogleAdsAccount.objects.create(
            config=self.config_b, name="Delax.vn", customer_id="3000000000",
        )
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.real, campaign_id="c1", name="CD của tôi",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )

        self.channel = Channel.objects.create(code="g-delax", name="Google Delax")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.shell.id,
        )
        self.user = make_user_with_sections(
            "gads_campaign_scoped", ["ads-report"], scope=DataScope.CHANNEL,
        )
        UserChannel.objects.create(user=self.user, channel=self.channel, is_active=True)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_sees_spend_even_though_channel_maps_a_different_config_row(self):
        response = self.client.get(
            reverse("gads-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["campaign_name"] for row in response.data["rows"]], ["CD của tôi"],
        )
        self.assertEqual(response.data["accessible_accounts"], 1)
```

> Tên route: mở `backend/apps/google_ads/urls.py`, dùng đúng `name=` đã khai. Nếu khác `gads-campaign-cost-report` thì sửa lại cho khớp.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/google_ads/tests/test_campaign_report_api.py -q --create-db`
Expected: FAIL — `KeyError: 'accessible_accounts'`

- [ ] **Step 3: Sửa view**

Thay khối cuối:

```python
    scope = OrderRepository.google_ads_account_scope(request.user)
    rows = aggregate_campaign_cost(since, until, scope.ids)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "campaign",
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        # Xem ghi chú cùng tên ở bản Facebook: null ≠ 0.
        "accessible_accounts": scope.count,
    })
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/google_ads -q --create-db`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/google_ads
git commit -m "feat(ads): report accessible account count for google campaigns"
```

---
### Task 5: Cột CTR

**Files:**
- Modify: `frontend/src/lib/api/ads_campaign_report.ts:59-68`
- Modify: `frontend/src/features/ads/components/CampaignCostTable.tsx`
- Test: `frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx` (tạo mới)

**Interfaces:**
- Produces: `computeCtr(clicks: number, impressions: number): number | null` — trả **phần trăm** (`2.35` = 2,35%), `null` khi `impressions = 0`.

> Chạy lệnh tạo junction node_modules ở phần Global Constraints trước task này nếu chưa chạy.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx`:

```tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import type { CampaignCostRow } from "@/lib/api/ads_campaign_report";
import { computeCtr } from "@/lib/api/ads_campaign_report";
import { CampaignCostTable } from "../CampaignCostTable";

afterEach(cleanup);

const NO_ERRORS = { facebook: null, google: null };

function row(overrides: Partial<CampaignCostRow> = {}): CampaignCostRow {
  return {
    platform: "facebook",
    account_name: "LỢI_TK7_HKD",
    campaign_id: "c1",
    campaign_name: "LDP_MSC06_080726_TK 7_BÀI CŨ",
    cost: "994619.00",
    impressions: 37162,
    clicks: 691,
    revenue: null,
    ...overrides,
  };
}

function renderTable(rows: CampaignCostRow[]) {
  return render(
    <I18nProvider>
      <CampaignCostTable rows={rows} loading={false} errors={NO_ERRORS} truncated={false} />
    </I18nProvider>,
  );
}

describe("computeCtr", () => {
  it("trả phần trăm click trên hiển thị", () => {
    expect(computeCtr(50, 1000)).toBeCloseTo(5);
  });

  it("trả null khi không có hiển thị — tránh chia 0 ra Infinity", () => {
    expect(computeCtr(10, 0)).toBeNull();
  });

  it("trả 0 khi có hiển thị nhưng không có click", () => {
    expect(computeCtr(0, 1000)).toBe(0);
  });
});

describe("CampaignCostTable — cột CTR", () => {
  it("hiện tiêu đề CTR", () => {
    renderTable([row()]);
    expect(screen.getByText("CTR")).toBeInTheDocument();
  });

  it("hiện CTR dạng phần trăm 2 chữ số", () => {
    renderTable([row({ clicks: 691, impressions: 37162 })]);
    // 691 / 37162 = 1,8593...%
    expect(screen.getByText("1,86%")).toBeInTheDocument();
  });

  it("hiện gạch ngang khi không có hiển thị", () => {
    renderTable([row({ impressions: 0, clicks: 0 })]);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run (từ `frontend/`): `npx vitest run src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
Expected: FAIL — `computeCtr is not a function`

- [ ] **Step 3: Thêm computeCtr**

Vào cuối `frontend/src/lib/api/ads_campaign_report.ts`:

```ts
/**
 * Tỉ lệ click trên hiển thị, trả sẵn dạng phần trăm (2.35 = 2,35%).
 *
 * null khi không có hiển thị — cùng quy ước với computeCpm/computeCpc: ô trống đọc
 * là "không đo được", còn 0 là một khẳng định sai.
 *
 * Lưu ý nghiệp vụ: `clicks` của Facebook là TỔNG click (gồm click vào page/ảnh) nên
 * con số này nhỉnh hơn cột "CTR (link)" trong Ads Manager. Muốn khớp tuyệt đối phải
 * đồng bộ thêm inline_link_clicks.
 */
export function computeCtr(clicks: number, impressions: number): number | null {
  if (!impressions) return null;
  return (clicks / impressions) * 100;
}
```

- [ ] **Step 4: Thêm cột vào bảng**

Trong `frontend/src/features/ads/components/CampaignCostTable.tsx`:

Thêm `computeCtr` vào khối import từ `@/lib/api/ads_campaign_report`.

Thêm formatter cạnh `numberFormatter`:

```tsx
const percentFormatter = new Intl.NumberFormat("vi-VN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
```

Thêm `<th>` ngay SAU cột Click, TRƯỚC cột CPM:

```tsx
                  <th className="px-3 py-2 text-center font-medium">
                    {t("adsReport.ctrHeader", "CTR")}
                  </th>
```

Trong thân bảng, thêm `const ctr = computeCtr(row.clicks, row.impressions);` cạnh `cpm`/`cpc`, và thêm `<td>` ngay sau ô Click:

```tsx
                      <td className="px-3 py-1.5 text-center text-xs tabular-nums">
                        {ctr === null ? "—" : `${percentFormatter.format(ctr)}%`}
                      </td>
```

Đổi `colSpan={8}` của dòng empty state thành `colSpan={9}`.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
Expected: PASS (6 test)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api/ads_campaign_report.ts frontend/src/features/ads/components/CampaignCostTable.tsx frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx
git commit -m "feat(ads): add CTR column to campaign table"
```

---

### Task 6: Tìm kiếm theo tên chiến dịch

**Files:**
- Modify: `frontend/src/features/ads/components/CampaignCostTable.tsx`
- Test: `frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx` (thêm describe)

**Interfaces:**
- Consumes: `matchesSearchTerm(term: string, fields: Array<string | null | undefined>): boolean` từ `@/lib/text-search` — bỏ dấu tiếng Việt, quy `đ → d`, AND theo token.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `CampaignCostTable.test.tsx` (import thêm `fireEvent` từ `@testing-library/react`):

```tsx
describe("CampaignCostTable — tìm kiếm chiến dịch", () => {
  const ROWS = [
    row({ campaign_id: "c1", campaign_name: "LDP_MSC06_080726_TK 7_BÀI CŨ" }),
    row({
      campaign_id: "c2",
      campaign_name: "LDP_MSC06_200526_TK 6_BÀI WIN",
      account_name: "Lợi_TK6_HKD",
    }),
    row({
      campaign_id: "c3",
      campaign_name: "DMG - RMK 2 - TMS01-DT11-BNC02",
      account_name: "Delax.vn",
      platform: "google",
    }),
  ];

  function search(term: string) {
    renderTable(ROWS);
    fireEvent.change(screen.getByLabelText("Tìm chiến dịch"), { target: { value: term } });
  }

  it("ô rỗng thì không lọc gì", () => {
    renderTable(ROWS);
    expect(screen.getByText("LDP_MSC06_080726_TK 7_BÀI CŨ")).toBeInTheDocument();
    expect(screen.getByText("DMG - RMK 2 - TMS01-DT11-BNC02")).toBeInTheDocument();
  });

  it("khớp không dấu và không phân biệt hoa thường", () => {
    search("bai win");
    expect(screen.getByText("LDP_MSC06_200526_TK 6_BÀI WIN")).toBeInTheDocument();
    expect(screen.queryByText("LDP_MSC06_080726_TK 7_BÀI CŨ")).not.toBeInTheDocument();
  });

  it("khớp cả tên tài khoản quảng cáo", () => {
    search("delax");
    expect(screen.getByText("DMG - RMK 2 - TMS01-DT11-BNC02")).toBeInTheDocument();
    expect(screen.queryByText("LDP_MSC06_200526_TK 6_BÀI WIN")).not.toBeInTheDocument();
  });

  it("lọc hết dòng thì nói rõ là do bộ lọc, không nói là chưa có dữ liệu", () => {
    search("khong-ton-tai-gi-ca");
    expect(screen.getByText("Không có chiến dịch nào khớp bộ lọc.")).toBeInTheDocument();
    expect(
      screen.queryByText("Chưa có dữ liệu chiến dịch trong khoảng đã chọn."),
    ).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
Expected: FAIL — `Unable to find a label with the text of: Tìm chiến dịch`

- [ ] **Step 3: Thêm ô tìm kiếm**

Trong `CampaignCostTable.tsx`:

Thêm import:

```tsx
import { Input } from "@/components/ui/input";
import { matchesSearchTerm } from "@/lib/text-search";
```

> Kiểm tra `@/components/ui/input` có export `Input`. Nếu không có, dùng thẻ `<input>` với cùng className như ô `select` bộ lọc nền tảng: `"h-9 rounded-md border border-input bg-background px-3 text-sm"`.

Thêm state cạnh `selectedAccounts`:

```tsx
  const [searchTerm, setSearchTerm] = useState("");
```

Thêm điều kiện vào `filteredRows` (và thêm `searchTerm` vào mảng dependency):

```tsx
        if (!matchesSearchTerm(searchTerm, [row.campaign_name, row.account_name])) {
          return false;
        }
```

Thêm ô nhập vào cuối hàng bộ lọc, ngay sau khối MultiSelect tài khoản:

```tsx
          <div className="flex flex-1 flex-col gap-1">
            <label
              htmlFor="campaign-search"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("adsReport.campaignSearchLabel", "Tìm chiến dịch")}
            </label>
            <Input
              id="campaign-search"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder={t("adsReport.campaignSearchPlaceholder", "Tên chiến dịch hoặc TKQC")}
              className="h-9"
            />
          </div>
```

Sửa dòng empty state để phân biệt "lọc hết" với "không có dữ liệu" — `rows.length > 0` nghĩa là dữ liệu có, chỉ bộ lọc đang cắt hết:

```tsx
                      {rows.length > 0
                        ? t("adsReport.campaignNoMatch", "Không có chiến dịch nào khớp bộ lọc.")
                        : t(
                            "adsReport.campaignEmpty",
                            "Chưa có dữ liệu chiến dịch trong khoảng đã chọn.",
                          )}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
Expected: PASS (10 test)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/components
git commit -m "feat(ads): search campaigns by name in campaign table"
```

---

### Task 7: Băng cảnh báo khi chưa được gán TKQC

**Files:**
- Modify: `frontend/src/lib/api/ads_campaign_report.ts:18-25` (type `CampaignCostReport`)
- Modify: `frontend/src/features/ads/components/CampaignCostTable.tsx`
- Modify: `frontend/src/features/ads/pages/AdsReportPage.tsx:284-295`
- Test: `frontend/src/features/ads/components/__tests__/CampaignCostTable.test.tsx` (thêm describe)

**Interfaces:**
- Consumes: field `accessible_accounts: number | null` do Task 3 + Task 4 trả về.
- Produces: prop mới của `CampaignCostTable`:
  `accessibleAccounts?: { facebook: number | null; google: number | null }` (mặc định `{ facebook: null, google: null }`).

- [ ] **Step 1: Viết test thất bại**

Thêm vào `CampaignCostTable.test.tsx`:

```tsx
const NO_ACCOUNTS_TEXT =
  "Bạn chưa được gán tài khoản quảng cáo nào. Liên hệ quản trị để gán TKQC cho kênh bạn phụ trách.";

describe("CampaignCostTable — chưa được gán TKQC", () => {
  function renderWithScope(accessibleAccounts: {
    facebook: number | null;
    google: number | null;
  }) {
    return render(
      <I18nProvider>
        <CampaignCostTable
          rows={[]}
          loading={false}
          errors={NO_ERRORS}
          truncated={false}
          accessibleAccounts={accessibleAccounts}
        />
      </I18nProvider>,
    );
  }

  it("nói rõ lý do khi cả hai nền tảng đều trả 0 tài khoản", () => {
    renderWithScope({ facebook: 0, google: 0 });
    expect(screen.getByText(NO_ACCOUNTS_TEXT)).toBeInTheDocument();
  });

  it("null nghĩa là không bị lọc — không được cảnh báo nhầm cho admin", () => {
    renderWithScope({ facebook: null, google: null });
    expect(screen.queryByText(NO_ACCOUNTS_TEXT)).not.toBeInTheDocument();
    expect(
      screen.getByText("Chưa có dữ liệu chiến dịch trong khoảng đã chọn."),
    ).toBeInTheDocument();
  });

  it("có TKQC ở một nền tảng thì không cảnh báo", () => {
    renderWithScope({ facebook: 0, google: 3 });
    expect(screen.queryByText(NO_ACCOUNTS_TEXT)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/ads/components/__tests__/CampaignCostTable.test.tsx`
Expected: FAIL — không tìm thấy `NO_ACCOUNTS_TEXT`

- [ ] **Step 3: Thêm field vào type API**

Trong `frontend/src/lib/api/ads_campaign_report.ts`, thêm vào type `CampaignCostReport`:

```ts
  /**
   * Số TKQC user được xem. `null` = không bị lọc (scope xem-toàn-bộ).
   * 0 = bị lọc và KHÔNG được gán TKQC nào — trạng thái này cho ra bảng rỗng y hệt
   * "không tiêu đồng nào", nên phải phân biệt bằng field chứ không đoán từ rows.
   */
  accessible_accounts: number | null;
```

- [ ] **Step 4: Dùng trong bảng**

Trong `CampaignCostTable.tsx`, thêm vào type props:

```tsx
  /** Số TKQC user được xem theo từng nguồn; null = không bị lọc. */
  accessibleAccounts?: { facebook: number | null; google: number | null };
```

Thêm vào destructuring với giá trị mặc định:

```tsx
  accessibleAccounts = { facebook: null, google: null },
```

Thêm ngay trước `return`:

```tsx
  // Chỉ cảnh báo khi CẢ HAI nguồn đều báo 0: người chỉ chạy Google mà không chạy
  // Facebook vẫn có TKQC, cảnh báo lúc đó là sai.
  const hasNoAssignedAccounts =
    accessibleAccounts.facebook === 0 && accessibleAccounts.google === 0;
```

Sửa lại nhánh empty state thành ba nhánh (thay khối đã viết ở Task 6):

```tsx
                      {hasNoAssignedAccounts
                        ? t(
                            "adsReport.campaignNoAssignedAccounts",
                            "Bạn chưa được gán tài khoản quảng cáo nào. Liên hệ quản trị để gán TKQC cho kênh bạn phụ trách.",
                          )
                        : rows.length > 0
                          ? t("adsReport.campaignNoMatch", "Không có chiến dịch nào khớp bộ lọc.")
                          : t(
                              "adsReport.campaignEmpty",
                              "Chưa có dữ liệu chiến dịch trong khoảng đã chọn.",
                            )}
```

Và đổi className của ô đó để cảnh báo nổi hơn:

```tsx
                    className={`px-3 py-6 text-center text-xs ${
                      hasNoAssignedAccounts ? "text-amber-700" : "text-muted-foreground"
                    }`}
```

- [ ] **Step 5: Truyền prop từ trang**

Trong `frontend/src/features/ads/pages/AdsReportPage.tsx`, thêm vào `<CampaignCostTable ... />`:

```tsx
            accessibleAccounts={{
              facebook: campaignQuery.facebook.data?.accessible_accounts ?? null,
              google: campaignQuery.google.data?.accessible_accounts ?? null,
            }}
```

> `?? null` là cố ý: lúc chưa tải xong `data` là `undefined` → thành `null` → không cảnh báo. Cảnh báo sớm khi chưa có dữ liệu còn tệ hơn không cảnh báo.

- [ ] **Step 6: Chạy test + typecheck**

Run: `npx vitest run src/features/ads`
Expected: PASS

Run: `npx tsc --noEmit`
Expected: không lỗi

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api/ads_campaign_report.ts frontend/src/features/ads
git commit -m "feat(ads): explain empty campaign table when no ad account assigned"
```

---

### Task 8: Gom trùng TKQC ở dropdown chọn kênh

**Files:**
- Modify: `frontend/src/features/channels/utils.ts`
- Modify: `frontend/src/features/channels/ChannelManageDialog.tsx:107-118`
- Modify: `frontend/src/features/channels/ChannelCreateDialog.tsx:76-87`
- Test: `frontend/src/features/channels/__tests__/group-ad-account-options.test.ts` (tạo mới)

**Interfaces:**
- Produces:
  - `type AdAccountGroupOption = { value: string; label: string; ids: number[] }`
  - `groupAdAccountOptions<T extends { id: number; name: string }>(accounts: T[], externalIdOf: (account: T) => string): AdAccountGroupOption[]`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/channels/__tests__/group-ad-account-options.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { groupAdAccountOptions } from "../utils";

const FB_ACCOUNTS = [
  { id: 21, name: "LỢI_TK7_HKD", account_id: "25322428374101900" },
  { id: 172, name: "LỢI_TK7_HKD", account_id: "25322428374101900" },
  { id: 64, name: "LỢI_TK7_HKD", account_id: "25322428374101900" },
  { id: 30, name: "Lợi_TK6_HKD", account_id: "2007931173486188" },
];

const byAccountId = (acc: { account_id: string }) => acc.account_id;

describe("groupAdAccountOptions", () => {
  it("gộp mọi bản sao cùng id ngoài thành một dòng", () => {
    const options = groupAdAccountOptions(FB_ACCOUNTS, byAccountId);
    expect(options).toHaveLength(2);
  });

  it("giữ mọi PK của nhóm để chip đang chọn không biến mất", () => {
    const [first] = groupAdAccountOptions(FB_ACCOUNTS, byAccountId);
    expect(first.ids).toEqual([21, 64, 172]);
  });

  it("chọn PK nhỏ nhất làm đại diện để ổn định giữa các lần tải", () => {
    const [first] = groupAdAccountOptions(FB_ACCOUNTS, byAccountId);
    expect(first.value).toBe("21");
  });

  it("nhãn gồm tên và id ngoài", () => {
    const [first] = groupAdAccountOptions(FB_ACCOUNTS, byAccountId);
    expect(first.label).toBe("LỢI_TK7_HKD (25322428374101900)");
  });

  it("danh sách rỗng trả mảng rỗng", () => {
    expect(groupAdAccountOptions([], byAccountId)).toEqual([]);
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/channels/__tests__/group-ad-account-options.test.ts`
Expected: FAIL — `groupAdAccountOptions is not a function`

- [ ] **Step 3: Viết helper**

Thêm vào cuối `frontend/src/features/channels/utils.ts`:

```ts
export type AdAccountGroupOption = { value: string; label: string; ids: number[] };

/**
 * Gom các bản sao của cùng một tài khoản quảng cáo thành một lựa chọn.
 *
 * Cùng một TKQC thật được lưu thành nhiều dòng — mỗi FacebookApp / GoogleAdsConfig
 * quét được nó sẽ tạo một dòng riêng. Danh sách thô vì thế hiện cùng tên + cùng id
 * lặp lại nhiều lần, không cách nào phân biệt, và người gán kênh chọn trúng bản nào
 * là hoàn toàn ngẫu nhiên.
 *
 * `ids` giữ MỌI PK cùng nhóm: kênh đang trỏ một bản sao không phải bản đại diện thì
 * chip vẫn phải hiện, nếu không người dùng sẽ tưởng mình bị mất cấu hình.
 */
export function groupAdAccountOptions<T extends { id: number; name: string }>(
  accounts: T[],
  externalIdOf: (account: T) => string,
): AdAccountGroupOption[] {
  const groups = new Map<string, { label: string; ids: number[] }>();
  for (const account of accounts) {
    const externalId = externalIdOf(account);
    const group = groups.get(externalId);
    if (group) {
      group.ids.push(account.id);
      continue;
    }
    groups.set(externalId, {
      label: `${account.name} (${externalId})`,
      ids: [account.id],
    });
  }
  // Đại diện là PK nhỏ nhất — thứ tự API không đảm bảo nên lấy phần tử đầu sẽ làm
  // giá trị gửi lên đổi giữa các lần tải, sinh diff giả khi lưu kênh.
  return Array.from(groups.values()).map((group) => {
    const ids = [...group.ids].sort((a, b) => a - b);
    return { value: String(ids[0]), label: group.label, ids };
  });
}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/channels/__tests__/group-ad-account-options.test.ts`
Expected: PASS (5 test)

- [ ] **Step 5: Dùng trong ChannelManageDialog**

Trong `frontend/src/features/channels/ChannelManageDialog.tsx`, thêm import:

```tsx
import { groupAdAccountOptions } from "@/features/channels/utils";
```

Thay khối dòng 107-118:

```tsx
  const fbAdsOptions = groupAdAccountOptions(availableFbAdsAccounts, (acc) => acc.account_id);
  const fbAdsValue = fbAdsOptions.filter((opt) =>
    opt.ids.some((id) => editFbAdsAccounts.includes(id)),
  );
  const googleAdsOptions = groupAdAccountOptions(
    availableGoogleAdsAccounts,
    (acc) => acc.customer_id,
  );
  const googleAdsValue = googleAdsOptions.filter((opt) =>
    opt.ids.some((id) => editGoogleAdsAccounts.includes(id)),
  );
```

Không đổi phần `onChange` — vẫn `options.map((opt) => Number(opt.value))`.

- [ ] **Step 6: Dùng trong ChannelCreateDialog**

Cùng cách, thay khối dòng 76-87 của `ChannelCreateDialog.tsx`, dùng `fbAdsAccounts` / `googleAdsAccounts` (tên biến ở dialog này không có tiền tố `edit`):

```tsx
  const fbAdsOptions = groupAdAccountOptions(availableFbAdsAccounts, (acc) => acc.account_id);
  const fbAdsValue = fbAdsOptions.filter((opt) =>
    opt.ids.some((id) => fbAdsAccounts.includes(id)),
  );
  const googleAdsOptions = groupAdAccountOptions(
    availableGoogleAdsAccounts,
    (acc) => acc.customer_id,
  );
  const googleAdsValue = googleAdsOptions.filter((opt) =>
    opt.ids.some((id) => googleAdsAccounts.includes(id)),
  );
```

Nếu TypeScript than về generic của `ReactSelect<ChannelSelectOption, true>`, đổi generic thành `ReactSelect<AdAccountGroupOption, true>` và import type đó từ `@/features/channels/utils`.

- [ ] **Step 7: Chạy test + typecheck**

Run: `npx vitest run src/features/channels`
Expected: PASS (các test ChannelManagement sẵn có phải vẫn xanh)

Run: `npx tsc --noEmit`
Expected: không lỗi

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/channels
git commit -m "feat(channels): dedupe ad account options by external id"
```

---

### Task 9: Kiểm chứng toàn bộ và mở MR

**Files:** không sửa file nguồn; chỉ chạy kiểm chứng và đẩy nhánh.

- [ ] **Step 1: Chạy toàn bộ test backend của 3 app liên quan**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_adsscope_final \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/facebook_ads apps/google_ads apps/orders/tests/test_ads_reports.py apps/channels_core -q --create-db'
```

Expected: PASS toàn bộ. Nếu đỏ ở test không liên quan — chạy lại tuần tự trước khi nghi thay đổi của mình (test dùng chung `desi-test-pg` có thể flaky khi chạy song song).

- [ ] **Step 2: Ruff + import linter**

Run: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`
Expected: `All checks passed!`

Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest sh -c 'pip install -q import-linter && python -m importlinter.cli lint-imports'`
Expected: `Contracts: 1 kept, 0 broken.`

- [ ] **Step 3: Test + typecheck frontend**

Từ `frontend/`:

Run: `npx vitest run src/features/ads src/features/channels src/lib`
Expected: PASS

Run: `npx tsc --noEmit`
Expected: không lỗi

Run: `npm run test:ci`
Expected: chỉ còn đúng 2 file đỏ **sẵn có trên master** (`RequireAuth.test.tsx`, `use-orders-data.realtime-upsert-filters.test.tsx`). Bất kỳ file đỏ nào khác là do thay đổi trong MR này.

- [ ] **Step 4: Cập nhật spec với ghi chú hoàn thành**

Thêm vào cuối `docs/superpowers/specs/2026-08-10-ads-report-scope-duplicate-accounts-design.md`:

```markdown
## 7. Trạng thái

Đã hoàn thành trên nhánh `fix/ads-report-scope-duplicate-accounts`. Sau khi deploy cần
kiểm chứng bằng tài khoản `loiads` trên tab Chiến dịch và tab Tổng quan → Theo page.

Còn nợ (MR riêng): gộp các dòng `FacebookAdAccount` / `GoogleAdsAccount` trùng, thêm
unique trên id ngoài. Mục 3.1 đã làm cho việc trùng vô hại với báo cáo nên đây là dọn
dẹp, không phải sửa lỗi.
```

```bash
git add docs/superpowers/specs/2026-08-10-ads-report-scope-duplicate-accounts-design.md
git commit -m "docs: mark ads scope design as implemented"
```

- [ ] **Step 5: Đẩy nhánh và tạo MR**

Repo không có `glab` / token — tạo MR bằng push options:

```bash
git push -o merge_request.create \
  -o merge_request.title="fix(ads): scope campaign report by real ad account + CTR + search" \
  -o merge_request.remove_source_branch \
  -u origin fix/ads-report-scope-duplicate-accounts
```

Expected: output có URL MR. Gửi link đó cho người dùng.

Nếu báo `Everything up-to-date` mà MR chưa được tạo: xoá remote branch (`git push origin --delete fix/ads-report-scope-duplicate-accounts`) rồi push lại với cùng push options.

---

## Self-Review

**Spec coverage:**

| Mục spec | Task |
|---|---|
| 3.1 Lọc theo TKQC vật lý | Task 1 (port + adapter), Task 2 (sửa tận gốc trong `OrderRepository`) |
| 3.2 Bỏ rỗng câm (`accessible_accounts` + băng cảnh báo) | Task 3, 4 (backend), Task 7 (frontend) |
| 3.3 Cột CTR | Task 5 |
| 3.4 Tìm kiếm theo tên chiến dịch | Task 6 |
| 3.5 Gom trùng dropdown | Task 8 |
| 4.1 Test bắt đúng bug (FB + Google) | Task 1 (adapter), Task 2 (luật phân quyền), Task 3 Step 1, Task 4 Step 1 |
| 4.2 Test cách ly kênh khác | Task 3 Step 1 (`test_does_not_leak_other_channel_spend`) |
| 4.3 Test `accessible_accounts` null/số | Task 3 Step 1 (`CampaignReportAccessibleAccountsTests`) |
| 4.4 Test page report với kịch bản nhân bản | Task 3 Step 5 |
| 4.5 Test frontend CTR + băng cảnh báo | Task 5, Task 7 |
| 4.6 Test frontend tìm kiếm | Task 6 |
| 4.7 Test `groupAdAccountOptions` | Task 8 |
| 2.4 Sửa cả tab Theo page | Task 3 Step 4 |

**Không có mục spec nào thiếu task.**

**Placeholder scan:** không có "TBD"/"TODO"/"tương tự Task N". Mọi bước code đều có khối code thật.

**Type consistency:** `AdAccountScope(ids, count)` dùng nhất quán ở Task 1-4. `computeCtr(clicks, impressions)` khai ở Task 5, dùng đúng thứ tự tham số ở test. `accessible_accounts` (snake_case, payload API) vs `accessibleAccounts` (camelCase, prop React) — khác nhau có chủ đích, đã ghi rõ ở cả hai nơi. `groupAdAccountOptions` / `AdAccountGroupOption` khai ở Task 8, dùng đúng tên ở cả hai dialog.
