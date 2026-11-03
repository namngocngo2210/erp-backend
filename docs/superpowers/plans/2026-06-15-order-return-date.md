# Order Return Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an editable `return_date` (ngày hoàn hàng) field to orders — inline date-picker editing in the list table, plus a week/month filter scoped to that field.

**Architecture:** Additive nullable `DateField` on the `Order` model. Backend exposes it on the list serializer, accepts it on the inline-update serializer, and adds an independent `return_date_from`/`return_date_to` range filter (separate from the existing `created_at` filter). Frontend adds a `return_date` column to `OrdersTable.tsx` (native `<input type="date">` inline editor, clearable, saves on change), forces the column visible once for existing users, and adds a second `DateFilter` ("Ngày hoàn hàng") wired through the existing filter/data hooks.

**Tech Stack:** Django + DRF (Python 3.12), React + TypeScript (Vite). No new dependencies — native `<input type="date">`, existing `DateFilter` component (already has week/month options).

**Reference precedent:** The `vat` column was added the exact same way; mirror it. See `docs/order-vat-column.md` and grep `vat` in the files below.

---

## Conventions for this plan

- Backend tests run via ephemeral docker (prod image lacks tests/ruff). From repo root:
  ```bash
  docker run --rm --network desi-order_default -v "$PWD/backend:/app" -w /app \
    desi-order-web sh -c "pip install -q pytest pytest-django && pytest <path> -v"
  ```
  If that image/network name differs, run `docker compose ps` to confirm, or run pytest in whatever way the repo already documents. Adjust once, reuse.
- Commit after each task. Commit messages in English, short, no Co-Authored-By trailer (per CLAUDE.md).
- After each FE task run `npx tsc --noEmit` from `frontend/`.

---

## Task 1: Backend — model field + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (Order model, after `customer_return` ~line 254)
- Create: `backend/apps/orders/migrations/0084_order_return_date.py`

- [ ] **Step 1: Add the field**

In `backend/apps/orders/models.py`, immediately after the `customer_return` line (`customer_return = models.BooleanField(default=False)`), add:

```python
    # Ngày hoàn hàng (KH trả về). Nhập tay, độc lập với customer_return / OrderRecall.
    return_date = models.DateField(null=True, blank=True, db_index=True)
```

- [ ] **Step 2: Create the migration**

Create `backend/apps/orders/migrations/0084_order_return_date.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0083_lead_assignment_share"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="return_date",
            field=models.DateField(null=True, blank=True, db_index=True),
        ),
    ]
```

- [ ] **Step 3: Verify migration matches model**

Run: `docker exec desi-web python manage.py makemigrations orders --check --dry-run`
Expected: "No changes detected" (the hand-written migration already covers the field). If it reports changes, reconcile the migration with the model.

- [ ] **Step 4: Django check**

Run: `docker exec desi-web python manage.py check`
Expected: "System check identified no issues".

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0084_order_return_date.py
git commit -m "feat(orders): add return_date field + migration"
```

> NOTE: Do NOT run `migrate` against the prod DB here — defer to deploy:
> `docker exec desi-web python manage.py migrate orders`. The test DB (Task 3) migrates itself.

---

## Task 2: Backend — serializers (expose + inline update)

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` (`OrderSerializer.Meta.fields`)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` (`OrderInlineUpdateSerializer.Meta.fields`)

- [ ] **Step 1: Find the exact insertion points**

Run:
```bash
grep -n '"vat"' backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py
grep -n "vat" backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py
```
`return_date` goes right next to `vat` in each `Meta.fields` tuple/list.

- [ ] **Step 2: Add to `OrderSerializer.Meta.fields`**

In `order_core_serializers.py`, in `OrderSerializer.Meta.fields`, add `"return_date"` adjacent to `"vat"`. `DateField` serializes to an ISO date string (`"2026-06-15"`) automatically — no extra SerializerMethodField needed.

- [ ] **Step 3: Add to `OrderInlineUpdateSerializer.Meta.fields`**

In `kiotviet_landing_serializers.py`, in `OrderInlineUpdateSerializer.Meta.fields`, add `"return_date"` adjacent to `"vat"`. Because the model field has `null=True, blank=True`, DRF's `ModelSerializer` accepts both an ISO date string and `null` (clear) on PATCH — no custom validator needed.

- [ ] **Step 4: Django check**

Run: `docker exec desi-web python manage.py check`
Expected: no issues.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/serializers/order_serializers_impl/
git commit -m "feat(orders): expose return_date on list + inline-update serializers"
```

---

## Task 3: Backend — return_date range filter + tests

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py` (`build_orders_filters`)
- Modify: `backend/apps/orders/repositories.py` (`_apply_list_filters`, ~line 492-508)
- Test: `backend/apps/orders/tests/test_return_date.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/orders/tests/test_return_date.py`. First inspect an existing orders test to reuse fixtures/factory helpers:
```bash
ls backend/apps/orders/tests/
grep -rln "def test_" backend/apps/orders/tests/ | head
```
Mirror the setup used by a neighbouring test (e.g. the VAT test added with the vat column, or any `test_order_*` that builds an Order + customer + channel). The tests to write:

```python
import datetime
import pytest

# Reuse the project's existing order-building fixture/helper. Replace `make_order`
# with whatever the neighbouring tests use (factory, fixture, or direct ORM create).

pytestmark = pytest.mark.django_db


def test_return_date_defaults_to_none(make_order):
    order = make_order()
    assert order.return_date is None


def test_inline_update_sets_return_date(make_order):
    from apps.orders.transport.serializers.order_serializers_impl.kiotviet_landing_serializers import (
        OrderInlineUpdateSerializer,
    )
    order = make_order()
    ser = OrderInlineUpdateSerializer(order, data={"return_date": "2026-06-10"}, partial=True)
    assert ser.is_valid(), ser.errors
    ser.save()
    order.refresh_from_db()
    assert order.return_date == datetime.date(2026, 6, 10)


def test_inline_update_clears_return_date(make_order):
    from apps.orders.transport.serializers.order_serializers_impl.kiotviet_landing_serializers import (
        OrderInlineUpdateSerializer,
    )
    order = make_order(return_date=datetime.date(2026, 6, 10))
    ser = OrderInlineUpdateSerializer(order, data={"return_date": None}, partial=True)
    assert ser.is_valid(), ser.errors
    ser.save()
    order.refresh_from_db()
    assert order.return_date is None


def test_return_date_range_filter(make_order):
    from apps.orders.repositories import OrderRepository
    make_order(return_date=datetime.date(2026, 6, 1))
    inside = make_order(return_date=datetime.date(2026, 6, 10))
    make_order(return_date=datetime.date(2026, 7, 1))
    make_order(return_date=None)
    qs = OrderRepository.list_orders(
        {"return_date_from": "2026-06-05", "return_date_to": "2026-06-30"}
    )
    ids = {o.id for o in qs}
    assert inside.id in ids
    assert len(ids) == 1
```

> If `make_order` doesn't exist as a fixture, add a small `conftest.py` fixture in
> `backend/apps/orders/tests/` that builds a valid Order (copy the minimal field set from
> an existing test's setup). Keep it DRY — reuse, don't reinvent.

- [ ] **Step 2: Run tests, verify they fail**

Run the ephemeral-docker pytest command (see Conventions) on `apps/orders/tests/test_return_date.py`.
Expected: failures — `return_date_from` not yet filtered (range test returns >1 id or errors on unknown key handling).

- [ ] **Step 3: Add filter keys to `build_orders_filters`**

In `_view_helpers.py`, in the dict returned by `build_orders_filters`, next to `"date_from"/"date_to"`, add:

```python
        "return_date_from": source.get("return_date_from", ""),
        "return_date_to": source.get("return_date_to", ""),
```

- [ ] **Step 4: Apply the filter in the repository**

In `repositories.py`, inside `_apply_list_filters`, right before `return queryset` at the end of the method (after the `created_at` block ~line 507), add an **unconditional** return-date filter (independent of `raw_q`, since it's an explicit user selection on a different field):

```python
        return_date_from = filters.get("return_date_from")
        if return_date_from:
            try:
                queryset = queryset.filter(return_date__gte=datetime.date.fromisoformat(return_date_from))
            except ValueError:
                pass
        return_date_to = filters.get("return_date_to")
        if return_date_to:
            try:
                queryset = queryset.filter(return_date__lte=datetime.date.fromisoformat(return_date_to))
            except ValueError:
                pass
        return queryset
```

Ensure `import datetime` exists at the top of `repositories.py` (it imports `from datetime import datetime, timedelta` already — check and use `datetime.date` accordingly; if only `datetime`/`timedelta` are imported, add `from datetime import date` and use `date.fromisoformat(...)`).

Run: `grep -n "^import datetime\|^from datetime" backend/apps/orders/repositories.py` and adapt the code above to whatever import form is present (use `date.fromisoformat` if `date` is imported directly).

> `return_date` is a `DateField`, so compare against `date` objects with `__gte`/`__lte`
> (inclusive both ends). This is intentionally simpler than the `created_at` datetime
> boundary logic — no timezone conversion needed for a pure date.

- [ ] **Step 5: Run tests, verify they pass**

Run the pytest command again on `test_return_date.py`.
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/_view_helpers.py backend/apps/orders/repositories.py backend/apps/orders/tests/test_return_date.py
git commit -m "feat(orders): add return_date range filter + tests"
```

---

## Task 4: Frontend — types + column registration

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts`

- [ ] **Step 1: Add `return_date` to `OrderRow` and column registry**

In `order-types.ts`, mirror every place `vat` appears (grep `vat` to find them all):
1. On the `OrderRow` interface (near `vat?: OrderVat | null;`, ~line 158) add:
   ```ts
   return_date?: string | null;
   ```
2. In `OrderColumnKey` union (~line 197) add `| "return_date"`.
3. In `ORDER_COLUMN_OPTIONS` (~line 266) add:
   ```ts
   { key: "return_date", label: "orders.columns.returnDate" },
   ```
4. In `ORDER_COLUMN_FIELD_BY_KEY` (~line 336) add:
   ```ts
   return_date: "return_date",
   ```
5. In `DEFAULT_ORDER_COLUMNS` (~line 322, the export-columns list) add `"return_date",` near `"created_at"`.

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: passes (or only pre-existing unrelated errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/order-types.ts
git commit -m "feat(orders-fe): register return_date column + type"
```

---

## Task 5: Frontend — inline date-picker cell in OrdersTable

**Files:**
- Modify: `frontend/src/components/OrdersTable.tsx` (add a `case "return_date"` in the `renderCell` switch, next to the `created_at` case ~line 987)

- [ ] **Step 1: Add the `return_date` case**

In `OrdersTable.tsx`, add this case to the `renderCell` switch (place it right after the `case "created_at"` block). It mirrors the `customer_gender`/`vat` inline pattern but uses a native date input (clearable, saves on change):

```tsx
    case "return_date": {
      const canEdit = !!row.can_edit && !!onPatch;
      const raw = row.return_date ?? "";
      const display = raw
        ? `${raw.slice(8, 10)}/${raw.slice(5, 7)}/${raw.slice(0, 4)}`
        : "-";
      if (!canEdit) {
        return <span>{display}</span>;
      }
      return (
        <input
          type="date"
          className="h-7 w-full min-w-[120px] cursor-pointer rounded border border-border bg-transparent px-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
          value={raw}
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onChange={(e) => {
            e.stopPropagation();
            const next = e.target.value || null;
            onPatch?.(row.id, { return_date: next } as Partial<OrderRow>);
          }}
        />
      );
    }
```

> `<input type="date">` value is always `yyyy-MM-dd` — exactly what the DRF `DateField`
> accepts. Empty string → `null` → clears the field (matches "cho xóa"). The browser's
> native picker provides the calendar UI with no new dependency.

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: passes.

- [ ] **Step 3: Manual smoke (optional but recommended)**

If the dev server is running, confirm the column renders a date input on an editable row, picking a date persists (network PATCH to `/api/orders/:id`), and clearing it blanks the cell.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/OrdersTable.tsx
git commit -m "feat(orders-fe): inline return_date date-picker cell"
```

---

## Task 6: Frontend — force column visible for existing users

**Files:**
- Modify: `frontend/src/features/orders/order-storage.ts` (`FORCED_COLUMN_MIGRATIONS` ~line 20, `DEFAULT_ORDER_COLUMNS` ~line 43)
- Test: `frontend/src/features/orders/order-storage.test.ts` (existing — extend if it asserts the migration list)

- [ ] **Step 1: Add the forced migration + default column**

In `order-storage.ts`:
1. In `FORCED_COLUMN_MIGRATIONS` add:
   ```ts
   { flag: "desi_order_return_date_injected", column: "return_date" },
   ```
2. In this file's `DEFAULT_ORDER_COLUMNS` (the visible-columns default, distinct from the export one in `order-types.ts`) add `"return_date",` near `"created_at"`.

- [ ] **Step 2: Run the storage tests**

Run: `npx vitest run src/features/orders/order-storage.test.ts`
Expected: pass. If a test enumerates `FORCED_COLUMN_MIGRATIONS` length/contents, update its expectation to include the new entry.

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/orders/order-storage.ts frontend/src/features/orders/order-storage.test.ts
git commit -m "feat(orders-fe): force return_date column visible once for existing users"
```

---

## Task 7: Frontend — "Ngày hoàn hàng" week/month filter wiring

**Files:**
- Modify: `frontend/src/features/orders/order-storage.ts` (`StoredOrderFilters` type + the 3 return branches in `getStoredOrderFilters` + the catch branch)
- Modify: `frontend/src/features/orders/use-orders-filters.ts` (state + return)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (request body + any filter snapshots)
- Modify: `frontend/src/features/orders/OrdersFilterPanel.tsx` (prop + render second `DateFilter`)

> **Key design point:** `returnDateFilter` must default to **no constraint** so it never
> hides the (majority) orders with a null `return_date`. Default it to
> `{ option: "custom", from: "", to: "" }`. The request only sends `return_date_from/to`
> when `from`/`to` are non-empty, so an untouched filter is a no-op. When the user picks
> "Tuần này"/"Tháng này", the existing `getDateRange` fills `from`/`to` and it filters.

- [ ] **Step 1: Extend `StoredOrderFilters` + loader**

In `order-storage.ts`:
1. Add to the `StoredOrderFilters` type:
   ```ts
   returnDateFilter: DateFilterValue;
   ```
2. In `getStoredOrderFilters`, every `return { ... }` branch (the no-raw branch ~line 149, the non-custom branch ~line 170, the else branch ~line 187, and the catch branch ~line 205) must include:
   ```ts
   returnDateFilter:
     (parsed?.returnDateFilter as DateFilterValue | undefined) ?? {
       option: "custom" as const,
       from: "",
       to: "",
   },
   ```
   For the two branches that have no `parsed` (the `!raw` branch and the `catch`), use the literal default:
   ```ts
   returnDateFilter: { option: "custom" as const, from: "", to: "" },
   ```
   Do NOT recompute its range from the option the way `dateFilter` does — leaving `from`/`to` empty when untouched is what keeps it a no-op.

- [ ] **Step 2: Add state to `use-orders-filters.ts`**

Mirror `dateFilter` (lines 19, 36, 57, 99):
1. Interface field: `returnDateFilter: DateFilterValue;`
2. Setter in the return type: `setReturnDateFilter: React.Dispatch<React.SetStateAction<DateFilterValue>>;`
3. State: `const [returnDateFilter, setReturnDateFilter] = useState<DateFilterValue>(storedFilters.returnDateFilter);`
4. Add `returnDateFilter` and `setReturnDateFilter` to the returned object.

- [ ] **Step 3: Send params in `use-orders-data.ts`**

1. Destructure `returnDateFilter` (and `setReturnDateFilter` if the panel is wired from here) from `filters` near where `dateFilter` is destructured (~line 311/345).
2. Near the body build (~line 764-840), after the `date_from`/`date_to` block, add:
   ```ts
   const returnDateFrom = (overrides?.returnDateFilter ?? returnDateFilter)?.from || "";
   const returnDateTo = (overrides?.returnDateFilter ?? returnDateFilter)?.to || "";
   // ...
   if (returnDateFrom) body.return_date_from = returnDateFrom;
   if (returnDateTo) body.return_date_to = returnDateTo;
   ```
   If the `overrides` param type lists `dateFilter`, add `returnDateFilter?: DateFilterValue;` to it too. Include `returnDateFilter` in any filter snapshot object that currently snapshots `dateFilter` (grep `dateFilter` in this file — ~lines 957, 1783, 1800, 1816 — add the sibling where it makes sense; at minimum the dependency arrays/snapshots that drive refetch).

- [ ] **Step 4: Render the second filter in `OrdersFilterPanel.tsx`**

1. Add props (near lines 74-75):
   ```ts
   returnDateFilter: DateFilterValue;
   onReturnDateFilterChange: (value: DateFilterValue) => void;
   ```
2. Destructure them in the component params (near lines 128-129).
3. Render after the existing `<DateFilter ... />` (~line 186), with a label so users can tell them apart:
   ```tsx
   <div className="flex flex-col gap-1">
     <span className="text-xs text-muted-foreground">Ngày hoàn hàng</span>
     <DateFilter value={returnDateFilter} onChange={onReturnDateFilterChange} />
   </div>
   ```
   (If the existing `DateFilter` already sits inside a labeled wrapper, match that structure for both so they align.)
4. At the panel's call site (grep for `onDateFilterChange=` to find where `<OrdersFilterPanel` is rendered), pass:
   ```tsx
   returnDateFilter={returnDateFilter}
   onReturnDateFilterChange={setReturnDateFilter}
   ```
   pulling `returnDateFilter`/`setReturnDateFilter` from the filters hook.

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit`
Expected: passes. Fix any spot where the new `StoredOrderFilters.returnDateFilter` field is required but missing.

- [ ] **Step 6: Manual smoke (recommended)**

With dev server running: select "Tuần này"/"Tháng này" on the Ngày hoàn hàng filter and confirm the list narrows to orders whose `return_date` falls in that range; clearing it (custom + empty) restores the full list.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/orders/order-storage.ts frontend/src/features/orders/use-orders-filters.ts frontend/src/features/orders/use-orders-data.ts frontend/src/features/orders/OrdersFilterPanel.tsx
git commit -m "feat(orders-fe): add return_date week/month filter"
```

---

## Task 8: i18n + docs progress

**Files:**
- Modify: `frontend/src/i18n/messages/orders.ts` (add `orders.columns.returnDate`, vi + en)
- Modify: `docs/order-return-date-column.md` (tick sprint checkboxes)

- [ ] **Step 1: Add i18n key**

In `orders.ts`, find `columns.vat` (grep `vat`) and add a `returnDate` sibling in both the `vi` and `en` message maps:
- vi: `"Ngày hoàn hàng"`
- en: `"Return date"`

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: passes (i18n maps are often typed — both locales must have the key).

- [ ] **Step 3: Update design doc progress**

In `docs/order-return-date-column.md`, tick the `[x]` boxes for the sprint items completed (Sprint 1 except the deferred prod migrate; Sprint 2; Sprint 3).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/messages/orders.ts docs/order-return-date-column.md
git commit -m "feat(orders-fe): i18n for return_date column + update design progress"
```

---

## Final verification

- [ ] Backend: `docker exec desi-web python manage.py check` clean; `test_return_date.py` 4/4 pass.
- [ ] Frontend: `npx tsc --noEmit` clean; `order-storage.test.ts` pass.
- [ ] Grep sanity: `grep -rn "return_date" backend/apps/orders | wc -l` and `grep -rn "return_date\|returnDate" frontend/src | wc -l` both non-trivial.
- [ ] Deferred (deploy step, not in this branch's CI): `docker exec desi-web python manage.py migrate orders`.
- [ ] Push branch + open MR; paste MR link.
