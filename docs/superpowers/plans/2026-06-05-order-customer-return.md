# Order `customer_return` Column & Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `customer_return` boolean to orders — shown as an inline-editable (checkbox) column + a yes/no filter — across the Orders, Landing and KOC list pages, plus editable in the order detail form.

**Architecture:** `customer_return` is a plain `BooleanField(default=False)` directly on `Order` (mirrors `is_delivery_invoice_printed`). It is read via `OrderSerializer`, written via `OrderInlineUpdateSerializer` (used by both grid inline-edit and the detail form, both hitting `PATCH /api/orders/<id>`), and filtered in `OrderRepository._apply_list_filters` with `"yes"/"no"` tokens. The 3 list pages share one frontend stack (`OrdersPage`, `OrdersFilterPanel`, `order-grid-columns`, `use-orders-data`), so each change applies to all three.

**Tech Stack:** Django + DRF (backend), React + TypeScript + ag-Grid + Vite/Vitest (frontend). Spec: `docs/superpowers/specs/2026-06-05-order-customer-return-design.md`.

**Reference template:** `is_delivery_invoice_printed` (backend) / `isDeliveryInvoicePrinted` (frontend) is a 1:1 shape-match for this feature. Many frontend steps are "add a `customerReturn` sibling wherever `isDeliveryInvoicePrinted` appears"; the required TypeScript type changes force `tsc` to flag every site, so `npm run build` passing is the completeness gate.

**Commands (run backend steps from `backend/`, frontend steps from `frontend/`):**
- Backend tests: `python manage.py test apps.orders.tests.test_order_list_filters_api -v 2`
- Backend migration check: `python manage.py makemigrations --check --dry-run`
- Frontend typecheck/build: `npm run build`
- Frontend lint: `npm run lint`
- Frontend tests: `npm test`

---

## Task 1: Backend model field + migration

**Files:**
- Modify: `backend/apps/orders/models.py:193`
- Create: `backend/apps/orders/migrations/0075_order_customer_return.py`

- [ ] **Step 1: Add the model field**

In `backend/apps/orders/models.py`, in class `Order`, add the field immediately after the `is_delivery_invoice_printed` line (currently line 193):

```python
    is_delivery_invoice_printed = models.BooleanField(default=False)
    customer_return = models.BooleanField(default=False)
```

- [ ] **Step 2: Create the migration**

Create `backend/apps/orders/migrations/0075_order_customer_return.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0074_order_shipping_code_trgm_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="customer_return",
            field=models.BooleanField(default=False),
        ),
    ]
```

- [ ] **Step 3: Verify Django sees no missing migrations**

Run (from `backend/`): `python manage.py makemigrations --check --dry-run`
Expected: exit 0, "No changes detected" (the hand-written migration already covers the new field).

- [ ] **Step 4: Apply the migration**

Run (from `backend/`): `python manage.py migrate apps_orders` (or `python manage.py migrate orders`)
Expected: `Applying orders.0075_order_customer_return... OK`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0075_order_customer_return.py
git commit -m "feat(orders): add customer_return boolean field + migration"
```

---

## Task 2: Expose `customer_return` in read + write serializers

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py:194`
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py:282`

- [ ] **Step 1: Add to the read serializer (`OrderSerializer`)**

In `order_core_serializers.py`, inside `OrderSerializer.Meta.fields`, add `"customer_return"` right after `"is_delivery_invoice_printed"`:

```python
            "invoice_status",
            "is_delivery_invoice_printed",
            "customer_return",
            "old_invoice_code",
```

- [ ] **Step 2: Add to the write serializer (`OrderInlineUpdateSerializer`)**

In `kiotviet_landing_serializers.py`, inside `OrderInlineUpdateSerializer.Meta.fields`, add `"customer_return"` after `"feedback"`:

```python
            "feedback",
            "customer_return",
            "total_after_discount",
```

(No custom `validate_customer_return` needed — DRF's `ModelSerializer` maps it to a `BooleanField` automatically, and `OrderService.update_order_inline` already does a generic `setattr` + `save(update_fields=[*data.keys(), ...])`.)

- [ ] **Step 3: Commit**

```bash
git add backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py
git commit -m "feat(orders): serialize and accept customer_return"
```

---

## Task 3: Repository filter (`customer_return` = yes/no)

**Files:**
- Modify: `backend/apps/orders/repositories.py:436`

- [ ] **Step 1: Add the filter block**

In `repositories.py`, inside `_apply_list_filters`, immediately after the `is_delivery_invoice_printed` block (which ends at line 436 with `queryset = queryset.filter(is_delivery_invoice_printed=False)`), add:

```python
        is_delivery_invoice_printed = str(filters.get("is_delivery_invoice_printed") or "").strip()
        if is_delivery_invoice_printed == "yes":
            queryset = queryset.filter(is_delivery_invoice_printed=True)
        elif is_delivery_invoice_printed == "no":
            queryset = queryset.filter(is_delivery_invoice_printed=False)
        customer_return = str(filters.get("customer_return") or "").strip()
        if customer_return == "yes":
            queryset = queryset.filter(customer_return=True)
        elif customer_return == "no":
            queryset = queryset.filter(customer_return=False)
```

(Only the `customer_return = ...` lines and below are new — the `is_delivery_invoice_printed` lines above are shown for placement context and must stay unchanged.)

- [ ] **Step 2: Commit**

```bash
git add backend/apps/orders/repositories.py
git commit -m "feat(orders): filter list by customer_return yes/no"
```

---

## Task 4: Wire `customer_return` into all 3 list views

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (3 sites: lines 52, 112, 173)

- [ ] **Step 1: `list_orders`**

In `order_list_view.py`, in the `filters` dict inside `list_orders`, add after the `is_delivery_invoice_printed` line (line 52):

```python
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "customer_return": source.get("customer_return", ""),
```

- [ ] **Step 2: `list_koc_orders`**

In the `filters` dict inside `list_koc_orders`, add after its `is_delivery_invoice_printed` line (line 112):

```python
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "customer_return": source.get("customer_return", ""),
```

- [ ] **Step 3: `list_landing_orders`**

In the `filters` dict inside `list_landing_orders`, add after its `is_delivery_invoice_printed` line (line 173):

```python
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "customer_return": source.get("customer_return", ""),
```

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_list_view.py
git commit -m "feat(orders): accept customer_return filter on orders/landing/koc endpoints"
```

---

## Task 5: Backend tests (filter + serializer + inline update)

**Files:**
- Modify: `backend/apps/orders/tests/test_order_list_filters_api.py`

- [ ] **Step 1: Write the failing tests**

Append these three methods to the `OrderListFiltersApiTests` class in `test_order_list_filters_api.py` (the `setUp` already creates `order_a`, `order_b`, `order_without_items`, all regular `show_orders=True` orders with default `customer_return=False`):

```python
    def test_filter_orders_by_customer_return_yes(self):
        self.client.force_authenticate(user=self.admin)
        Order.objects.filter(id=self.order_a.id).update(customer_return=True)
        response = self.client.post(
            "/api/orders",
            {"customer_return": "yes", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        ids = {item["id"] for item in results}
        self.assertEqual(ids, {self.order_a.id})
        # field is present in serializer output
        self.assertTrue(results[0]["customer_return"])

    def test_filter_orders_by_customer_return_no_and_empty(self):
        self.client.force_authenticate(user=self.admin)
        Order.objects.filter(id=self.order_a.id).update(customer_return=True)
        resp_no = self.client.post(
            "/api/orders",
            {"customer_return": "no", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp_no.status_code, 200)
        ids_no = {item["id"] for item in resp_no.json()["results"]}
        self.assertEqual(ids_no, {self.order_b.id, self.order_without_items.id})

        resp_all = self.client.post(
            "/api/orders",
            {"customer_return": "", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp_all.status_code, 200)
        ids_all = {item["id"] for item in resp_all.json()["results"]}
        self.assertEqual(
            ids_all, {self.order_a.id, self.order_b.id, self.order_without_items.id}
        )

    def test_inline_update_sets_customer_return(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{self.order_a.id}",
            {"customer_return": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["customer_return"])
        self.order_a.refresh_from_db()
        self.assertTrue(self.order_a.customer_return)
```

- [ ] **Step 2: Run the tests to verify they fail (before Tasks 1–4 are present) or pass (after)**

Run (from `backend/`): `python manage.py test apps.orders.tests.test_order_list_filters_api -v 2`
Expected after Tasks 1–4: all tests PASS. If you are running this task before Tasks 1–4, expect failures referencing the missing field/filter — that is the failing-test baseline.

- [ ] **Step 3: Commit**

```bash
git add backend/apps/orders/tests/test_order_list_filters_api.py
git commit -m "test(orders): cover customer_return filter and inline update"
```

---

## Task 6: Frontend type, column option, default visibility, i18n

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts`
- Modify: `frontend/src/i18n/messages/orders.ts`

- [ ] **Step 1: Add the field to `OrderRow`**

In `order-types.ts`, in the `OrderRow` type, add after `is_delivery_invoice_printed?: boolean;` (line 153):

```ts
  is_delivery_invoice_printed?: boolean;
  customer_return?: boolean;
```

- [ ] **Step 2: Add the column key**

In the `OrderColumnKey` union (ends around line 241 with `| "return_slip_code";`), add a new member:

```ts
  | "return_slip_code"
  | "customer_return";
```

- [ ] **Step 3: Register the column option**

In `ORDER_COLUMN_OPTIONS` (the array ending at line 295), add before the recall keys (so it groups with the regular columns):

```ts
  { key: "customer_code", label: "orders.columns.customerCode" },
  { key: "customer_return", label: "orders.columns.customerReturn" },
  // Hành vi đơn hàng (CRM)
```

- [ ] **Step 4: Map the column key to its field**

In `ORDER_COLUMN_FIELD_BY_KEY` (line 314+), add:

```ts
  customer_code: "customer_code",
  customer_return: "customer_return",
```

- [ ] **Step 5: Make the column visible by default**

In `DEFAULT_ORDER_COLUMNS` (line 302), add `"customer_return"` as the last entry:

```ts
  "kiotviet", // Kênh kiotviet
  "customer_return", // Khách trả hàng
];
```

- [ ] **Step 6: Add i18n labels (Vietnamese + English)**

In `frontend/src/i18n/messages/orders.ts`, add to the **Vietnamese** map (near the other `orders.columns.*` / `orders.filters.*` keys, e.g. after `"orders.columns.customerPrepaid"` line 43):

```ts
    "orders.columns.customerReturn": "Khách trả hàng",
    "orders.columns.customerReturnTooltip":
      "Khách trả lại hàng do không còn nhu cầu — không phải đơn hoàn/thu hồi",
    "orders.filters.customerReturn": "Khách trả hàng",
    "orders.filters.customerReturnAll": "Tất cả",
    "orders.filters.customerReturnYes": "Có trả hàng",
    "orders.filters.customerReturnNo": "Không trả hàng",
```

And the matching keys in the **English** map (after `"orders.columns.customerPrepaid"` line 232):

```ts
    "orders.columns.customerReturn": "Customer Returned",
    "orders.columns.customerReturnTooltip":
      "Customer returned goods (no longer needed) — not a return/recall order",
    "orders.filters.customerReturn": "Customer Returned",
    "orders.filters.customerReturnAll": "All",
    "orders.filters.customerReturnYes": "Returned",
    "orders.filters.customerReturnNo": "Not returned",
```

- [ ] **Step 7: Typecheck**

Run (from `frontend/`): `npm run build`
Expected: build succeeds (this only adds types/keys; downstream usage comes in later tasks).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/orders/order-types.ts frontend/src/i18n/messages/orders.ts
git commit -m "feat(orders-fe): add customer_return type, column option, default + i18n"
```

---

## Task 7: Inline-editable checkbox column in the grid

**Files:**
- Modify: `frontend/src/features/orders/order-grid-columns.tsx`

`createOrderColumnDefs(...)` returns one big array literal of `ColDef<OrderRow>` objects; each column controls visibility via `hide: !isVisible("<key>")`. The function already receives `onPatchOrderArg?: (id, payload, silent?) => Promise<void>` and `t`. `memo` and `ICellRendererParams` are already imported and used (e.g. `QuantityCellRenderer`). `CENTER_CELL_STYLE` already exists (used by the quick-detail column).

- [ ] **Step 1: Add the column object to the returned array**

In `order-grid-columns.tsx`, insert this object into the array returned by `createOrderColumnDefs`, immediately before the shipping-status (`field: "status"`) column object's neighbor `created_at` column (the `{ ... cellRenderer: CreatedAtCellRenderer }` object around line 1526). Place it just before that `created_at` object:

```tsx
    {
      colId: "customer_return",
      field: "customer_return",
      headerName: t("orders.columns.customerReturn", "Khách trả hàng"),
      headerTooltip: t(
        "orders.columns.customerReturnTooltip",
        "Khách trả lại hàng do không còn nhu cầu — không phải đơn hoàn/thu hồi",
      ),
      hide: !isVisible("customer_return"),
      width: 120,
      minWidth: 100,
      editable: false,
      sortable: false,
      cellStyle: CENTER_CELL_STYLE,
      cellRenderer: memo(function CustomerReturnCellRenderer(
        params: ICellRendererParams<OrderRow>,
      ) {
        const order = params.data;
        if (!order) return null;
        const checked = !!order.customer_return;
        return (
          <input
            type="checkbox"
            checked={checked}
            aria-label={t("orders.columns.customerReturn", "Khách trả hàng")}
            onChange={(e) => {
              const next = e.target.checked;
              if (!onPatchOrderArg) return;
              // optimistic: flip the cell, revert if the PATCH fails
              params.node.setDataValue("customer_return", next);
              void onPatchOrderArg(order.id, { customer_return: next }, true).catch(() => {
                params.node.setDataValue("customer_return", !next);
              });
            }}
          />
        );
      }),
    },
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/order-grid-columns.tsx
git commit -m "feat(orders-fe): inline-editable customer_return checkbox column"
```

---

## Task 8: Thread the `customerReturn` filter through state, request body, and UI

This filter has the exact shape and lifecycle of `isDeliveryInvoicePrinted` (a `"" | "yes" | "no"` string). The approach: add a `customerReturn` sibling **at every site** where `isDeliveryInvoicePrinted` appears, with the same value tokens. The required type additions force `tsc` to error on any object-literal site you miss, so `npm run build` passing == every site covered.

**Files (all sites — enumerate with the grep in Step 1):**
- `frontend/src/features/orders/use-orders-filters.ts`
- `frontend/src/features/orders/use-orders-data.ts`
- `frontend/src/features/orders/OrdersFilterPanel.tsx`
- `frontend/src/pages/OrdersPage.tsx`
- `frontend/src/app/routed-pages.tsx`
- `frontend/src/app/RootLayout.tsx`
- `frontend/src/App.tsx`

- [ ] **Step 1: Enumerate every site to mirror**

Run (from `frontend/`): `grep -rn "isDeliveryInvoicePrinted" src`
This is your checklist. For each line, add a `customerReturn` sibling of the identical kind (type member, default value, parse line, destructure, dependency-array entry, prop, JSX prop, `setIsDeliveryInvoicePrinted(...)` → `setCustomerReturn(...)`, `!payload.isDeliveryInvoicePrinted` → `&& !payload.customerReturn`, etc.). Steps 2–6 give the non-mechanical (logic-bearing) snippets explicitly; everything else is a same-shape sibling.

- [ ] **Step 2: `use-orders-filters.ts` — state + reset + return**

In `UseOrdersFiltersReturn` add `customerReturn: string;` (after `isDeliveryInvoicePrinted: string;`, line 33) and `setCustomerReturn: React.Dispatch<React.SetStateAction<string>>;` (after `setIsDeliveryInvoicePrinted`, line 49).

Add the state hook after the `isDeliveryInvoicePrinted` `useState` (line 71-73):

```ts
  const [isDeliveryInvoicePrinted, setIsDeliveryInvoicePrinted] = useState<string>(
    storedFilters.isDeliveryInvoicePrinted,
  );
  const [customerReturn, setCustomerReturn] = useState<string>(storedFilters.customerReturn);
```

In `reset()` add after `setIsDeliveryInvoicePrinted("");` (line 90): `setCustomerReturn("");`

In the returned object add `customerReturn,` (after `isDeliveryInvoicePrinted,`, line 109) and `setCustomerReturn,` (after `setIsDeliveryInvoicePrinted,`, line 125).

- [ ] **Step 3: `use-orders-data.ts` — overrides, stored filters, snapshot, body, in-memory filter**

In this file, mirror `isDeliveryInvoicePrinted` at every grep hit. The logic-bearing ones, verbatim:

`LoadOrdersOverrides` (line 95): add `customerReturn?: string;`

`StoredOrderFilters` type (line 354): add `customerReturn: string;`

All `StoredOrderFilters` object literals — the two `isDeliveryInvoicePrinted: ""` defaults (lines 412, 465) get `customerReturn: "",`; the two parse sites (lines 432, 448) get:

```ts
        customerReturn: String(parsed?.customerReturn ?? "").trim(),
```

Destructure from `useOrdersFilters` (line 804): add `customerReturn,`.

`filterSnapshotRef` generic type (line 869): add `customerReturn: string;`. Its initial value object (line 884): add `customerReturn: "",`.

`rowPassesActiveFilters` — after the two `isDeliveryInvoicePrinted` lines (973-974) add:

```ts
    if (f.isDeliveryInvoicePrinted === "yes" && !row.is_delivery_invoice_printed) return false;
    if (f.isDeliveryInvoicePrinted === "no" && row.is_delivery_invoice_printed) return false;
    if (f.customerReturn === "yes" && !row.customer_return) return false;
    if (f.customerReturn === "no" && row.customer_return) return false;
```

`buildBody` — derive the value next to `isDeliveryInvoicePrintedValue` (lines 1302-1304):

```ts
      const isDeliveryInvoicePrintedValue = (
        overrides?.isDeliveryInvoicePrinted ?? isDeliveryInvoicePrinted
      ).trim();
      const customerReturnValue = (overrides?.customerReturn ?? customerReturn).trim();
```

…and set the body key next to `body.is_delivery_invoice_printed` (lines 1338-1339):

```ts
        if (isDeliveryInvoicePrintedValue)
          body.is_delivery_invoice_printed = isDeliveryInvoicePrintedValue;
        if (customerReturnValue) body.customer_return = customerReturnValue;
```

The remaining `use-orders-data.ts` hits are mechanical siblings: the `filterSnapshotRef.current = { ... }` assignment (≈line 2212) and its dependency array (≈2228); the two other snapshot/persist objects and their deps (≈2246/2262, 2277); the `isEmpty` checks `&& !snapshot.isDeliveryInvoicePrinted` (≈2307) and `&& !payload.isDeliveryInvoicePrinted` (≈2390); the `setIsDeliveryInvoicePrinted(target.isDeliveryInvoicePrinted)` reset (≈2348) → add `setCustomerReturn(target.customerReturn);`; the persisted `payload`/return objects (≈2156, 2373, 2413, 2574). Add a `customerReturn` sibling to each. **Important:** also destructure `setCustomerReturn` from `useOrdersFilters` wherever `setIsDeliveryInvoicePrinted` is destructured, and include `customerReturn` / `setCustomerReturn` in this hook's returned object so `OrdersPage` can consume them (mirror `isDeliveryInvoicePrinted` / `setIsDeliveryInvoicePrinted` in the final `return`).

- [ ] **Step 4: `OrdersFilterPanel.tsx` — props + the `<select>` control**

Add props after `onIsDeliveryInvoicePrintedChange` (lines 113-114):

```ts
  customerReturn: string;
  onCustomerReturnChange: (value: string) => void;
```

Destructure them in the component params after `onIsDeliveryInvoicePrintedChange` (line 155):

```ts
  customerReturn,
  onCustomerReturnChange,
```

Add the filter control immediately after the `is_delivery_invoice_printed` `<div className="w-full">…</div>` block (closes at line 390), copying its structure:

```tsx
        <div className="w-full">
          <label
            htmlFor="orders-filter-customer-return"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            {t("orders.filters.customerReturn", "Khách trả hàng")}
          </label>
          <select
            id="orders-filter-customer-return"
            className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm focus:outline-none focus:border-ring focus:ring-3 focus:ring-ring/50"
            value={customerReturn}
            onChange={(e) => onCustomerReturnChange(e.target.value)}
          >
            <option value="">{t("orders.filters.customerReturnAll", "Tất cả")}</option>
            <option value="yes">{t("orders.filters.customerReturnYes", "Có trả hàng")}</option>
            <option value="no">{t("orders.filters.customerReturnNo", "Không trả hàng")}</option>
          </select>
        </div>
```

- [ ] **Step 5: `OrdersPage.tsx` — prop interfaces, handler, active-filter count, pass-through**

Mirror `isDeliveryInvoicePrinted` at each grep hit in this file:
- The two prop type declarations `isDeliveryInvoicePrinted: string;` (lines 147, 184): add `customerReturn: string;`. (Also add the setter prop the page receives from `useOrdersData`, mirroring however `onIsDeliveryInvoicePrintedChange`/`setIsDeliveryInvoicePrinted` is typed/destructured.)
- Destructure `isDeliveryInvoicePrinted` in the component params (line 567): add `customerReturn,`.
- Active-filter counter (lines 704, 718): add `if (customerReturn) n += 1;` and include `customerReturn` in that `useMemo` deps.
- The filter-key memo object + deps (lines 738, 756): add `customerReturn,`.
- Add the change handler next to `handleIsDeliveryInvoicePrintedChange` (lines 1095-1100):

```tsx
  const handleCustomerReturnChange = useCallback(
    (value: string) => {
      onCustomerReturnChange(value);
      onApplyFilters({ customerReturn: value }).catch((err) => {
        console.error("[filter:customerReturn]", err);
        toast.error(t("orders.toast.fetchListFailed"));
      });
    },
    [onCustomerReturnChange, onApplyFilters, t],
  );
```

(Use the same source for `onCustomerReturnChange` as `onIsDeliveryInvoicePrintedChange` — i.e. the `setCustomerReturn`/handler wired from `useOrdersData`/`useOrdersFilters`. Follow the exact wiring of `onIsDeliveryInvoicePrintedChange` in this file.)
- Pass to `OrdersFilterPanel` next to lines 1773-1774:

```tsx
            isDeliveryInvoicePrinted={isDeliveryInvoicePrinted}
            onIsDeliveryInvoicePrintedChange={handleIsDeliveryInvoicePrintedChange}
            customerReturn={customerReturn}
            onCustomerReturnChange={handleCustomerReturnChange}
```

- [ ] **Step 6: `routed-pages.tsx`, `RootLayout.tsx`, `App.tsx` — pass persisted value through**

Each has a `isDeliveryInvoicePrinted: ...` line inside an object passed down (routed-pages line 66; RootLayout line 155; App lines 592, 670). Add a `customerReturn: <same source>.customerReturn,` sibling at each, matching the object it sits in (e.g. `routed-pages.tsx`: `customerReturn: orders.customerReturn,`; the App/RootLayout ones read from `loaded.customerReturn`).

- [ ] **Step 7: Typecheck — the completeness gate**

Run (from `frontend/`): `npm run build`
Expected: build succeeds with **zero** TypeScript errors. Any error like "Property 'customerReturn' is missing in type …" pinpoints a site from Step 1 you still need to mirror — fix and re-run until green.

- [ ] **Step 8: Lint**

Run (from `frontend/`): `npm run lint`
Expected: no new errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/orders/use-orders-filters.ts frontend/src/features/orders/use-orders-data.ts frontend/src/features/orders/OrdersFilterPanel.tsx frontend/src/pages/OrdersPage.tsx frontend/src/app/routed-pages.tsx frontend/src/app/RootLayout.tsx frontend/src/App.tsx
git commit -m "feat(orders-fe): customer_return filter across state, request body and filter panel"
```

---

## Task 9: Editable checkbox in the order detail form

**Files:**
- Modify: `frontend/src/pages/OrderDetailPage.tsx`

`OrderDetailPage` composes the editable `Block*` components and owns a `patch: (p: Partial<OrderRow>) => void` callback (passed to `BlockFinancials`/`BlockRecall`, e.g. lines 728-740) that PATCHes the order. Add a labeled checkbox bound to that same `patch`.

- [ ] **Step 1: Add the checkbox near the financial/recall blocks**

In `OrderDetailPage.tsx`, near where `BlockFinancials` is rendered (around line 728), add a labeled checkbox row using the existing `patch` and `effectiveOrder || order`:

```tsx
              <label className="flex items-center gap-2 px-1 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={!!(effectiveOrder || order).customer_return}
                  onChange={(e) => patch({ customer_return: e.target.checked })}
                />
                <span>{t("orders.columns.customerReturn", "Khách trả hàng")}</span>
              </label>
```

(If `t` is not already in scope in this file, use the literal "Khách trả hàng" string — confirm by checking existing labels in the file. Place the row inside the same container/grid as the surrounding blocks so it lays out consistently.)

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/OrderDetailPage.tsx
git commit -m "feat(orders-fe): edit customer_return in order detail form"
```

---

## Task 10: Full verification + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Backend test suite for orders**

Run (from `backend/`): `python manage.py test apps.orders.tests.test_order_list_filters_api -v 2`
Expected: all PASS, including the three `customer_return` tests from Task 5.

- [ ] **Step 2: Frontend build + lint + unit tests**

Run (from `frontend/`):
- `npm run build` → success, zero TS errors
- `npm run lint` → no new errors
- `npm test` → existing suite passes (notably `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`)

- [ ] **Step 3: Manual smoke test (run the app)**

Start the app and, on each of the **Orders**, **Landing**, and **KOC** pages:
1. Confirm a "Khách trả hàng" column is visible by default with a checkbox per row.
2. Tick a row's checkbox → it persists (reload the page; value stays). Untick → reverts.
3. Open the filter panel → "Khách trả hàng" select with **Tất cả / Có trả hàng / Không trả hàng**. Choosing "Có trả hàng" shows only ticked orders; "Không trả hàng" shows only unticked; "Tất cả" shows all.
4. Open an order's detail form → toggle "Khách trả hàng" there → it persists and matches the grid checkbox.

- [ ] **Step 4: Final integration commit (if any stray changes) and branch wrap-up**

```bash
git status
# If clean, nothing to do. Otherwise stage intended files and:
git commit -m "chore(orders): finalize customer_return feature"
```

Then use the **superpowers:finishing-a-development-branch** skill to decide merge/PR for branch `feat/order-customer-return`.

---

## Self-Review notes (author)

- **Spec coverage:** model+migration (T1), read serializer (T2), write/inline serializer (T2), repository filter (T3), 3 endpoints (T4), tests (T5), FE type/column/default/i18n (T6), inline checkbox column (T7), filter end-to-end + body (T8), detail-form edit (T9), verification (T10). All spec §3–§5 items mapped.
- **Token convention:** filter uses `"" | "yes" | "no"` end-to-end (backend repo, body, `rowPassesActiveFilters`) — consistent with `is_delivery_invoice_printed`.
- **Completeness gate:** required TS type members (`StoredOrderFilters.customerReturn`, snapshot type, `UseOrdersFiltersReturn.customerReturn`, `OrdersFilterPanel` props) force `tsc` to flag any missed object-literal site in Task 8.
- **No new DB index** (matches `is_delivery_invoice_printed`); not linked to `OrderRecall`.
