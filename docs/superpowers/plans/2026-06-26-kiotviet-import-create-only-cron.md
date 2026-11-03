# KiotViet Import Create-Only Cron — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Celery beat job that every 15 minutes pulls new KiotViet invoices for every `old=False` channel and creates missing Orders only (no updates).

**Architecture:** Extract the default pagination+upsert loop from `import_kiotviet_invoices.Command.handle()` into a reusable mixin method `_handle_import`. A new Celery task borrows a `Command()` instance and calls that method directly (no `call_command`), iterating `old=False` channels with a per-channel Redis lock and a "today" window.

**Tech Stack:** Python 3.12, Django, Celery (beat + `kiotviet` queue), `django.core.cache` (Redis) for the lock.

## Global Constraints

- Inbound PULL only (KiotViet API → Order DB). Do NOT touch new→old mirror, webhook inbound, or Zalo noti.
- Create-only: never update existing Orders. `_upsert_invoice(create_only=True)` returns `"skipped"` for existing orders — idempotent across ticks.
- Do NOT use `call_command`; borrow `Command()` instance and call handler method (mirrors `_update_status_for_target` in `apps/kiotviet/tasks.py`).
- Channel scope: `KiotVietChannel.objects.filter(old=False, deleted_at__isnull=True)`; skip channels with empty `branch_id`.
- Window: today only — `00:00 localtime → now`, format `"%Y-%m-%d %H:%M:%S"`. No lookback.
- Cadence: every 15 minutes (`15 * 60.0`). Queue: `CELERY_KIOTVIET_QUEUE` (`"kiotviet"`).
- Keep CLI behavior of `import_kiotviet_invoices` 100% unchanged.
- Tests run in the test image, not the prod image (prod lacks `tests/`, ruff, pytest). On Windows: container `desi-order-web-test` on network `desi-order_desi-net`. Adapt the `pytest` invocations below to that runner.

---

### Task 1: Extract `_handle_import` from the command's default path

Move the default pagination+upsert loop out of `Command.handle()` into a method on `ImportHandlersMixin`, so both the CLI and the new cron task can call it. Behavior must stay identical (existing command tests are the safety net), plus add one explicit create-only test.

**Files:**
- Modify: `backend/apps/kiotviet/management/commands/_import_invoices_handlers_mixin.py` (add `_handle_import` method)
- Modify: `backend/apps/kiotviet/management/commands/import_kiotviet_invoices.py:335-421` (replace inline loop with a call)
- Test: `backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py` (add create-only test)

**Interfaces:**
- Produces: `ImportHandlersMixin._handle_import(self, *, kv_channel, branch_ids: list[str], dry_run: bool, create_only: bool, list_missing: bool, from_dt: str, to_dt: str, target_sale_channel_value: str) -> dict` — paginates `/invoices`, upserts (or lists missing), prints completion summary, returns the `summary` dict with keys `channel_id, branch_ids, dry_run, read, created, updated, skipped, errors, pages`.

- [ ] **Step 1: Write the failing test**

Add to `backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py` (class `ImportKiotVietInvoicesCommandTests`):

```python
    @patch("apps.kiotviet.management.commands.import_kiotviet_invoices.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.management.commands.import_kiotviet_invoices.parse_legacy_level3",
        return_value={
            "address": "", "ward": "", "district": "", "province": "",
            "ward_code": "", "district_code": "", "province_code": "",
        },
    )
    @patch("apps.kiotviet.management.commands.import_kiotviet_invoices._kiotviet_request")
    def test_create_only_skips_existing_and_creates_new(self, mock_request, *_):
        existing_invoice = {
            "id": 5001, "code": "INV-CO-EXIST", "customerId": 5001,
            "customerCode": "C-5001", "customerName": "Existing CO",
            "invoiceDelivery": {"address": "A", "status": 1},
            "invoiceDetails": [{"productCode": "SKU-CO-1", "productName": "P1", "price": 1000, "quantity": 1}],
        }
        # First run (normal) creates it.
        mock_request.return_value = _MockResponse({"data": [existing_invoice]})
        self._run_command(self.channel.id, "231174", dry_run=False)
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-EXIST").count(), 1)

        # Second run with --create-only: existing skipped, new one created.
        new_invoice = {
            "id": 5002, "code": "INV-CO-NEW", "customerId": 5002,
            "customerCode": "C-5002", "customerName": "New CO",
            "invoiceDelivery": {"address": "B", "status": 1},
            "invoiceDetails": [{"productCode": "SKU-CO-2", "productName": "P2", "price": 2000, "quantity": 1}],
        }
        mock_request.return_value = _MockResponse({"data": [existing_invoice, new_invoice]})
        out = StringIO()
        call_command(
            "import_kiotviet_invoices",
            "--channel-id", str(self.channel.id),
            "--branch-ids", "231174",
            "--create-only",
            stdout=out,
        )
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-EXIST").count(), 1)
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-NEW").count(), 1)
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 1)
```

- [ ] **Step 2: Run test to verify it passes (baseline behavior already works via inline loop)**

Run: `pytest backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py::ImportKiotVietInvoicesCommandTests::test_create_only_skips_existing_and_creates_new -v`
Expected: PASS (create-only already works through the inline loop — this test pins the behavior before refactor).

- [ ] **Step 3: Add `_handle_import` to the handlers mixin**

In `backend/apps/kiotviet/management/commands/_import_invoices_handlers_mixin.py`, add this method to `ImportHandlersMixin` (paste the loop verbatim from `import_kiotviet_invoices.py:335-421`, wrapped to build & return its own summary):

```python
    def _handle_import(
        self,
        *,
        kv_channel: KiotVietChannel,
        branch_ids: list[str],
        dry_run: bool,
        create_only: bool,
        list_missing: bool,
        from_dt: str,
        to_dt: str,
        target_sale_channel_value: str,
    ) -> dict:
        """Phân trang /invoices và upsert (hoặc liệt kê missing). Trả về summary dict.

        Tách từ Command.handle() để dùng chung cho CLI và cron import create-only.
        """
        summary = {
            "channel_id": kv_channel.id,
            "branch_ids": branch_ids,
            "dry_run": dry_run,
            "read": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "pages": 0,
        }
        missing_invoices: list[dict] = []
        page_size = 100
        current_item = 0

        while True:
            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "branchIds": ",".join(branch_ids),
                "includePayment": "true",
                "includeInvoiceDelivery": "true",
                "IncludePayment": "true",
                "IncludeInvoiceDelivery": "true",
            }
            self._apply_date_params(params, from_dt, to_dt)
            response = _kiotviet_request(
                order=None,
                channel=kv_channel,
                method="GET",
                endpoint="/invoices",
                params=params,
            )
            body = (response.text or "").strip()
            if response.status_code != 200:
                raise CommandError(
                    f"KiotViet /invoices failed: status={response.status_code}, body={body}"
                )

            payload = response.json() if response.content else {}
            invoices = _extract_invoice_items(payload)

            summary["pages"] += 1
            self.stdout.write(
                f"Page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
            )

            if not invoices:
                break

            for invoice in invoices:
                summary["read"] += 1
                if target_sale_channel_value and self._extract_sale_channel_id(invoice) != target_sale_channel_value:
                    summary["skipped"] += 1
                    continue
                if list_missing:
                    if self._resolve_invoice_own_status(invoice=invoice) == 2:
                        summary["skipped"] += 1
                        continue
                    try:
                        existing = self._find_existing_order(invoice, kv_channel)
                    except Exception as exc:
                        summary["errors"] += 1
                        self.stderr.write(self.style.WARNING(f"Lookup failed: {exc}"))
                        continue
                    if existing is None:
                        invoice_code = self._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
                        invoice_id = self._to_int(self._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
                        missing_invoices.append({"id": invoice_id, "code": invoice_code})
                        summary["created"] += 1
                        self.stdout.write(f"MISSING id={invoice_id} code={invoice_code}")
                    else:
                        summary["skipped"] += 1
                    continue
                try:
                    result = self._upsert_invoice(
                        invoice=invoice,
                        kv_channel=kv_channel,
                        dry_run=dry_run,
                        create_only=create_only,
                    )
                    summary[result] += 1
                except Exception as exc:
                    summary["errors"] += 1
                    invoice_code = self._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
                    invoice_id = self._to_int(self._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
                    self.stderr.write(
                        self.style.WARNING(
                            f"Invoice failed id={invoice_id} code={invoice_code}: {exc}"
                        )
                    )

            current_item += len(invoices)
            if len(invoices) < page_size:
                break

        self.stdout.write(self.style.SUCCESS("Import KiotViet invoices completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
        if list_missing:
            self.stdout.write(self.style.SUCCESS(f"Missing total: {len(missing_invoices)}"))
            self.stdout.write(json.dumps(missing_invoices, ensure_ascii=False))
        return summary
```

- [ ] **Step 4: Replace the inline loop in `handle()` with a call**

In `backend/apps/kiotviet/management/commands/import_kiotviet_invoices.py`, delete lines `335-421` (the `while True:` block plus the trailing `self.stdout.write(... "completed")` / summary / missing prints) and replace with:

```python
        self._handle_import(
            kv_channel=kv_channel,
            branch_ids=branch_ids,
            dry_run=dry_run,
            create_only=create_only,
            list_missing=list_missing,
            from_dt=from_dt,
            to_dt=to_dt,
            target_sale_channel_value=target_sale_channel_value,
        )
```

Note: the local `summary`, `missing_invoices`, `page_size`, `current_item` variables declared earlier in `handle()` (around lines 138-151, 102) are still used by the `invoice_codes` early-return branch — leave them. Only the default tail path now delegates to `_handle_import`.

- [ ] **Step 5: Run the full command test suite to verify no behavior change**

Run: `pytest backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py -v`
Expected: PASS — all existing tests plus the new `test_create_only_skips_existing_and_creates_new`.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/kiotviet/management/commands/_import_invoices_handlers_mixin.py backend/apps/kiotviet/management/commands/import_kiotviet_invoices.py backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py
git commit -m "refactor(kiotviet): extract _handle_import for reuse by cron"
```

---

### Task 2: Cron task, channel iterator, window helper, per-channel lock

Add the Celery task plus helpers to `apps/kiotviet/tasks.py`, mirroring the existing `_update_status_for_target` / `update_invoice_status_targets_task` pattern.

**Files:**
- Modify: `backend/apps/kiotviet/tasks.py` (add imports, `_today_window`, `iter_import_channels`, `_import_invoices_for_target`, `import_invoices_create_only_task`)
- Test: `backend/apps/kiotviet/tests/test_import_invoices_create_only_task.py` (new)

**Interfaces:**
- Consumes: `ImportHandlersMixin._handle_import(...)` from Task 1; `KiotVietChannel` model; `django.core.cache.cache`.
- Produces:
  - `_today_window() -> tuple[str, str]` — `(start_of_today, now)` localtime, `"%Y-%m-%d %H:%M:%S"`.
  - `iter_import_channels() -> Iterator[dict]` — yields `{"skip": bool, "channel_id": int, "branch_id": str, "label": str, "reason"?: str}` for each `old=False`, non-deleted channel.
  - `_import_invoices_for_target(*, channel_id: int, branch_ids: list[str], create_only: bool, from_dt: str, to_dt: str) -> dict`.
  - Celery task `apps.kiotviet.tasks.import_invoices_create_only` → returns summary `{"window", "done": [...], "skipped": [...], "errors": [...]}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/kiotviet/tests/test_import_invoices_create_only_task.py`:

```python
from datetime import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet import tasks as kv_tasks


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ImportInvoicesCreateOnlyTaskTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.new_channel = KiotVietChannel.objects.create(
            name="KV New", branch_id="231174", branch_ids=["231174"],
            client_id="c", client_secret="s", retailer="r", old=False,
        )
        self.old_channel = KiotVietChannel.objects.create(
            name="KV Old", branch_id="231175", branch_ids=["231175"],
            client_id="c2", client_secret="s2", retailer="r2", old=True,
        )
        self.no_branch = KiotVietChannel.objects.create(
            name="KV NoBranch", branch_id="", branch_ids=[],
            client_id="c3", client_secret="s3", retailer="r3", old=False,
        )

    def test_today_window_bounds(self):
        from_dt, to_dt = kv_tasks._today_window()
        start = datetime.strptime(from_dt, "%Y-%m-%d %H:%M:%S")
        self.assertEqual((start.hour, start.minute, start.second), (0, 0, 0))
        self.assertEqual(start.date(), timezone.localtime().date())
        # to_dt parses and is not before from_dt
        end = datetime.strptime(to_dt, "%Y-%m-%d %H:%M:%S")
        self.assertGreaterEqual(end, start)

    def test_iter_import_channels_filters_old_and_missing_branch(self):
        items = list(kv_tasks.iter_import_channels())
        by_id = {it["channel_id"]: it for it in items}
        # old=True channel excluded entirely
        self.assertNotIn(self.old_channel.id, by_id)
        # new channel with branch present
        self.assertFalse(by_id[self.new_channel.id]["skip"])
        self.assertEqual(by_id[self.new_channel.id]["branch_id"], "231174")
        # new channel without branch_id -> skip with reason
        self.assertTrue(by_id[self.no_branch.id]["skip"])
        self.assertIn("branch_id", by_id[self.no_branch.id]["reason"])

    @patch.object(kv_tasks, "_import_invoices_for_target")
    def test_task_imports_each_new_channel_create_only(self, mock_import):
        mock_import.return_value = {"created": 1, "skipped": 0}
        result = kv_tasks.import_invoices_create_only_task()
        called_ids = {c.kwargs["channel_id"] for c in mock_import.call_args_list}
        self.assertEqual(called_ids, {self.new_channel.id})
        for c in mock_import.call_args_list:
            self.assertTrue(c.kwargs["create_only"])
        self.assertEqual(len(result["done"]), 1)
        self.assertEqual(len(result["skipped"]), 1)  # no_branch channel

    @patch.object(kv_tasks, "_import_invoices_for_target")
    def test_task_skips_channel_when_lock_held(self, mock_import):
        from django.core.cache import cache
        cache.add(f"kv:import_create:lock:{self.new_channel.id}", 1, 660)
        kv_tasks.import_invoices_create_only_task()
        mock_import.assert_not_called()

    @patch.object(kv_tasks, "_import_invoices_for_target")
    def test_task_isolates_per_channel_errors(self, mock_import):
        another = KiotVietChannel.objects.create(
            name="KV New2", branch_id="231180", branch_ids=["231180"],
            client_id="c4", client_secret="s4", retailer="r4", old=False,
        )

        def side_effect(*, channel_id, **kwargs):
            if channel_id == self.new_channel.id:
                raise RuntimeError("boom")
            return {"created": 0, "skipped": 0}

        mock_import.side_effect = side_effect
        result = kv_tasks.import_invoices_create_only_task()
        error_ids = {e["channel_id"] for e in result["errors"]}
        done_ids = {d["channel_id"] for d in result["done"]}
        self.assertIn(self.new_channel.id, error_ids)
        self.assertIn(another.id, done_ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/apps/kiotviet/tests/test_import_invoices_create_only_task.py -v`
Expected: FAIL with `AttributeError: module 'apps.kiotviet.tasks' has no attribute '_today_window'` (and the other new names).

- [ ] **Step 3: Implement the helpers and task**

Add to the top imports of `backend/apps/kiotviet/tasks.py` (alongside existing imports):

```python
from typing import Iterator

from django.core.cache import cache
```

Append to `backend/apps/kiotviet/tasks.py`:

```python
def _today_window() -> tuple[str, str]:
    """(from, to) = 00:00 hôm nay (localtime) → hiện tại. Đơn mới tạo trong ngày là đủ;
    cron import create-only chạy 15' bắt đơn gần realtime."""
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt)


def iter_import_channels() -> Iterator[dict]:
    """Yield các kênh old=False, deleted_at NULL để import create-only.

    Mỗi item: {"skip": bool, "channel_id": int, "branch_id": str, "label": str}.
    skip=True kèm "reason" khi thiếu branch_id."""
    channels = (
        KiotVietChannel.objects
        .filter(old=False, deleted_at__isnull=True)
        .order_by("id")
    )
    for channel in channels:
        branch_id = (channel.branch_id or "").strip()
        label = f"kv{channel.id}"
        if not branch_id:
            yield {
                "skip": True,
                "channel_id": channel.id,
                "branch_id": "",
                "label": label,
                "reason": "thiếu branch_id",
            }
            continue
        yield {
            "skip": False,
            "channel_id": channel.id,
            "branch_id": branch_id,
            "label": label,
        }


def _import_invoices_for_target(
    *,
    channel_id: int,
    branch_ids: list[str],
    create_only: bool,
    from_dt: str,
    to_dt: str,
) -> dict:
    """Chạy THẲNG vòng lặp import chính ``_handle_import`` của command
    ``import_kiotviet_invoices`` (KHÔNG qua ``call_command``). Khởi tạo instance Command
    chỉ để mượn handler + helper sẵn có."""
    from apps.kiotviet.management.commands.import_kiotviet_invoices import Command

    kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
    cmd = Command()
    return cmd._handle_import(
        kv_channel=kv_channel,
        branch_ids=[str(b).strip() for b in branch_ids if str(b).strip()],
        dry_run=False,
        create_only=create_only,
        list_missing=False,
        from_dt=from_dt,
        to_dt=to_dt,
        target_sale_channel_value="",
    )


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.import_invoices_create_only",
    soft_time_limit=600,
    time_limit=660,
)
def import_invoices_create_only_task(
    self,
    from_dt: str | None = None,
    to_dt: str | None = None,
) -> dict:
    """Cron entrypoint: kéo invoice của MỌI kênh old=False và CHỈ tạo Order mới.

    Mỗi kênh bọc lock per-channel (cache.add) chống chồng tick; 1 kênh lỗi không chặn
    kênh khác. Window mặc định = trong ngày hôm nay; override qua from_dt/to_dt."""
    lock_ttl = 660  # = time_limit; lock tự hết hạn nếu worker chết giữa chừng
    if from_dt and to_dt:
        window_from, window_to = from_dt, to_dt
    else:
        window_from, window_to = _today_window()

    summary: dict = {
        "window": {"from": window_from, "to": window_to},
        "done": [],
        "skipped": [],
        "errors": [],
    }

    for item in iter_import_channels():
        channel_id = item["channel_id"]
        if item["skip"]:
            summary["skipped"].append({"channel_id": channel_id, "reason": item["reason"]})
            logger.warning("import_create_only skip %s: %s", item["label"], item["reason"])
            continue

        lock_key = f"kv:import_create:lock:{channel_id}"
        if not cache.add(lock_key, 1, lock_ttl):
            summary["skipped"].append({"channel_id": channel_id, "reason": "đang chạy (lock)"})
            logger.info("import_create_only skip %s: lock held", item["label"])
            continue

        try:
            result = _import_invoices_for_target(
                channel_id=channel_id,
                branch_ids=[item["branch_id"]],
                create_only=True,
                from_dt=window_from,
                to_dt=window_to,
            )
            summary["done"].append({
                "channel_id": channel_id,
                "created": result.get("created", 0),
                "skipped": result.get("skipped", 0),
                "errors": result.get("errors", 0),
            })
        except Exception as exc:  # noqa: BLE001 — 1 kênh lỗi không chặn kênh còn lại
            logger.exception("import_create_only failed channel_id=%s", channel_id)
            summary["errors"].append({"channel_id": channel_id, "error": str(exc)})
        finally:
            cache.delete(lock_key)

    logger.info(
        "import_invoices_create_only done: ok=%s, skipped=%s, errors=%s, window=%s..%s",
        len(summary["done"]), len(summary["skipped"]), len(summary["errors"]), window_from, window_to,
    )
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/apps/kiotviet/tests/test_import_invoices_create_only_task.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/tasks.py backend/apps/kiotviet/tests/test_import_invoices_create_only_task.py
git commit -m "feat(kiotviet): create-only import task per old=false channel with lock"
```

---

### Task 3: Wire Celery route + beat schedule

Register the task on the `kiotviet` queue and schedule it every 15 minutes.

**Files:**
- Modify: `backend/config/settings_parts/base.py` (`CELERY_TASK_ROUTES` ~line 158, `CELERY_BEAT_SCHEDULE` ~line 232)

**Interfaces:**
- Consumes: task name `apps.kiotviet.tasks.import_invoices_create_only` from Task 2.

- [ ] **Step 1: Add the task route**

In `backend/config/settings_parts/base.py`, inside `CELERY_TASK_ROUTES`, after the existing `"apps.kiotviet.tasks.update_invoice_status_targets"` line, add:

```python
    "apps.kiotviet.tasks.import_invoices_create_only": {"queue": CELERY_KIOTVIET_QUEUE},
```

- [ ] **Step 2: Add the beat schedule entry**

In `backend/config/settings_parts/base.py`, inside `CELERY_BEAT_SCHEDULE`, after the `"kv-update-invoice-status"` entry, add:

```python
    # Kéo invoice mới của MỌI kênh old=False và CHỈ tạo Order mới (create-only), mỗi 15'.
    # Gọi thẳng _handle_import của command (không call_command); window = trong ngày hôm nay.
    "kv-import-create-only": {
        "task": "apps.kiotviet.tasks.import_invoices_create_only",
        "schedule": 15 * 60.0,
    },
```

- [ ] **Step 3: Verify settings import cleanly**

Run: `python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from django.conf import settings; assert 'kv-import-create-only' in settings.CELERY_BEAT_SCHEDULE; assert settings.CELERY_TASK_ROUTES['apps.kiotviet.tasks.import_invoices_create_only']['queue']; print('ok')"`
Expected: prints `ok` (adapt `DJANGO_SETTINGS_MODULE` to the project's settings module; run inside the test/web container).

- [ ] **Step 4: Commit**

```bash
git add backend/config/settings_parts/base.py
git commit -m "feat(kiotviet): schedule import-create-only cron every 15m on kiotviet queue"
```

---

## Self-Review Notes

- **Spec coverage:** §3.1 → Task 1; §3.2 (task, `iter_import_channels`, `_import_invoices_for_target`, `_today_window`, lock) → Task 2; §3.3 (route + beat) → Task 3; §4 error handling → Task 2 task body + tests c/d; §5 tests a/b/c/d → Task 1 (a) + Task 2 (b/c/d).
- **Idempotency:** relies on `_upsert_invoice(create_only=True)` returning `"skipped"` for existing orders (`_import_invoices_upsert_mixin.py:216`).
- **No new settings:** channel scope is derived from `old=False` query, per the final design decision.
- **Lock TTL** equals `time_limit` (660s) so a dead worker self-releases.
