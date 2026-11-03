# Merge Returns into Stock-Documents Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One unified "Phiếu kho" table listing both StockDocument and ReturnNote rows (with a "Loại phiếu" column + filters), backed by a single RBAC-aware union endpoint; the standalone Returns page is removed from the menu and redirected.

**Architecture:** A new backend endpoint `GET /api/inventory/ledger-documents` normalizes both models into common columns via a DB-level `union(all=True)` (filter/sort/paginate at DB, labels resolved per-page), gated so each source only appears if the caller holds that section. The Stock-Documents page swaps its list source to this endpoint, branches row actions by `kind`, hosts a "Tạo ▼" dropdown (reusing both existing create dialogs) and the "Quét hàng hoàn" button moved from the Returns page. `/inventory/returns` redirects to `/inventory/stock-documents`; the Returns nav tab is removed.

**Tech Stack:** Django 6 + DRF, PostgreSQL, pytest; React + Vite + TS, shadcn/ui, TanStack Query.

## Global Constraints

- Branch `feat/merge-returns-into-stock-documents` (already checked out; scan-shipment feature already merged to master and present here).
- Commit messages English, short, **no** `Co-Authored-By`.
- Clean architecture: business/query logic in `application/services` or the view's query layer; thin transport. FE: feature-slice under `features/inventory`.
- RBAC (spec M3): include `stock` rows only if caller has `stock-documents:view`; include `return` rows only if `returns:view`; page reachable with EITHER. `require_permission(*codes)` passes if the user has ANY listed code; `require_section(a, b)` = `require_permission("a:view","b:view")` = ANY. Check individual sources with `resolve_effective_permissions(user).can("<section>:view")`.
- Run backend tests via ephemeral docker (prod image lacks tests):
  ```bash
  docker run --rm --user "$(id -u):$(id -g)" --network desi-order_default \
    --env-file .env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
    -e POSTGRES_DB=desi_mergetest -e DJANGO_SETTINGS_MODULE=config.settings \
    -v "$PWD/backend:/app" -w /app desi-order-web:latest \
    python -m pytest --create-db <paths> -q
  ```
- Run FE checks from `frontend/`: `npx tsc --noEmit` (exit 0), `npx eslint <files> --max-warnings=9999` (no new errors), `npx vitest run <path>`.
- Type/status vocab: stock `doc_type` ∈ receipt|issue|adjustment|transfer; stock `status` ∈ draft|pending_approval|posted|cancelled. return `return_type` ∈ delivery_returned|external_return|warranty|defect; return `status` ∈ draft|posted|cancelled.

---

## File Structure

- `backend/apps/inventory/transport/views/ledger_views.py` — create: `ledger_documents` GET view (union + RBAC + label resolution).
- `backend/apps/inventory/urls.py` — modify: register `ledger-documents`.
- `backend/apps/inventory/tests/test_ledger_documents.py` — create.
- `frontend/src/features/inventory/ledger-types.ts` — create: `LedgerRow`, `LedgerResponse`.
- `frontend/src/features/inventory/ledger-queries.ts` — create: `useLedgerDocuments(filters)`.
- `frontend/src/features/inventory/pages/StockDocumentsPage.tsx` — modify: unified table (Loại filter, Tạo dropdown, per-kind actions, Quét hàng hoàn button), list source → ledger.
- `frontend/src/app/routes.tsx` — modify: redirect `inventory/returns` → stock-documents; allow merged page with either section (GuardAny).
- `frontend/src/app/modules.ts` — modify: remove the `returns` tab from the inventory module.

---

### Task 1: Backend union endpoint `ledger-documents`

**Files:**
- Create: `backend/apps/inventory/transport/views/ledger_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_ledger_documents.py`

**Interfaces:**
- Produces: `GET /api/inventory/ledger-documents?type=&status=&q=&date_from=&date_to=&limit=&offset=` (type & status repeatable). Response:
  ```json
  {"items":[{"kind","id","code","type","return_type","type_label","status","status_label","warehouse_label","has_attachments","source_label","created_at"}],"total":N,"limit":L,"offset":O}
  ```
  `type` filter values: receipt|issue|adjustment|transfer|return. RBAC: `stock` rows require `stock-documents:view`; `return` rows require `returns:view`; 403 if neither.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/inventory/tests/test_ledger_documents.py`:

```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.application.services.stock_document_service import StockDocumentService
from apps.orders.models import Order, Product
from apps.users.test_helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse


class LedgerDocumentsTests(TestCase):
    def setUp(self):
        self.admin = make_admin("ledger-admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.wh = Warehouse.objects.create(code="WH-L", name="Kho L")
        self.wh2 = Warehouse.objects.create(code="WH-L2", name="Kho L2")
        self.p = Product.objects.create(name="SP L", sku="SKU-L-1")
        self.channel = Channel.objects.create(code="lp-l", name="Ledger ch")

    def _stock_receipt(self):
        return StockDocumentService.create_document(
            doc_type="receipt", dest_warehouse_id=self.wh.id,
            lines=[{"product_id": self.p.id, "quantity": 5}],
        )

    def _return(self):
        return ReturnNoteService.create_return(
            return_type="defect", warehouse_id=self.wh.id,
            lines=[{"product_id": self.p.id, "quantity": 2}],
            customer_name="Khach L",
        )

    def test_union_lists_both_kinds(self):
        self._stock_receipt()
        self._return()
        resp = self.client.get("/api/inventory/ledger-documents?limit=50&offset=0")
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        kinds = {r["kind"] for r in body["items"]}
        assert kinds == {"stock", "return"}
        assert body["total"] == 2

    def test_filter_type_return_only(self):
        self._stock_receipt()
        self._return()
        resp = self.client.get("/api/inventory/ledger-documents?type=return&limit=50&offset=0")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "return"
        assert body["items"][0]["type_label"].startswith("Trả hàng")

    def test_filter_type_receipt_only(self):
        self._stock_receipt()
        self._return()
        resp = self.client.get("/api/inventory/ledger-documents?type=receipt&limit=50&offset=0")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "stock"
        assert body["items"][0]["type"] == "receipt"

    def test_rbac_stock_only_user_sees_no_returns(self):
        self._stock_receipt()
        self._return()
        user = make_user_with_permissions("stock-only", ["stock-documents:view"])
        c = APIClient(); c.force_authenticate(user)
        resp = c.get("/api/inventory/ledger-documents?limit=50&offset=0")
        body = resp.json()
        assert {r["kind"] for r in body["items"]} == {"stock"}
        assert body["total"] == 1

    def test_rbac_returns_only_user_sees_no_stock(self):
        self._stock_receipt()
        self._return()
        user = make_user_with_permissions("ret-only", ["returns:view"])
        c = APIClient(); c.force_authenticate(user)
        resp = c.get("/api/inventory/ledger-documents?limit=50&offset=0")
        body = resp.json()
        assert {r["kind"] for r in body["items"]} == {"return"}
        assert body["total"] == 1

    def test_rbac_neither_section_403(self):
        user = make_user_with_permissions("nope", ["shipments:view"])
        c = APIClient(); c.force_authenticate(user)
        resp = c.get("/api/inventory/ledger-documents?limit=50&offset=0")
        assert resp.status_code == 403

    def test_labels_and_warehouse(self):
        self._stock_receipt()
        resp = self.client.get("/api/inventory/ledger-documents?type=receipt")
        row = resp.json()["items"][0]
        assert row["type_label"] == "Nhập kho"
        assert row["status_label"] == "Nháp"
        assert row["warehouse_label"] == "Kho L"
        assert row["has_attachments"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest --create-db apps/inventory/tests/test_ledger_documents.py -q`
Expected: FAIL (404 — route not registered).

- [ ] **Step 3: Create the view**

Create `backend/apps/inventory/transport/views/ledger_views.py`:

```python
from __future__ import annotations

from django.db.models import BooleanField, CharField, Exists, F, IntegerField, OuterRef, Value
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView  # noqa: F401  (kept for parity; not required)

from apps.inventory.models import (
    DocStatus,
    DocType,
    ReturnNote,
    ReturnType,
    StockDocument,
    StockDocumentAttachment,
)
from apps.orders.models import Order
from apps.users.permissions import require_section
from apps.users.rbac import resolve_effective_permissions
from apps.warehouses.models import Warehouse

_STOCK_TYPES = {DocType.RECEIPT, DocType.ISSUE, DocType.ADJUSTMENT, DocType.TRANSFER}
_DOC_TYPE_LABELS = dict(DocType.choices)
_DOC_STATUS_LABELS = dict(DocStatus.choices)
_RETURN_TYPE_LABELS = dict(ReturnType.choices)
_RETURN_STATUS_LABELS = {  # ReturnStatus choices
    "draft": "Nháp", "posted": "Đã ghi sổ", "cancelled": "Đã huỷ",
}

_COLS = ["id", "code", "kind", "type_code", "status", "src_wh", "dest_wh", "has_att", "created_at"]


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock-documents", "returns")])
def ledger_documents(request):
    eff = resolve_effective_permissions(request.user)
    can_stock = eff.can("stock-documents:view")
    can_return = eff.can("returns:view")

    types = request.query_params.getlist("type")
    statuses = request.query_params.getlist("status")
    q = (request.query_params.get("q") or "").strip()
    date_from = request.query_params.get("date_from") or ""
    date_to = request.query_params.get("date_to") or ""
    limit = max(1, min(_int(request.query_params.get("limit"), 50), 200))
    offset = max(0, _int(request.query_params.get("offset"), 0))

    want_return = (not types) or ("return" in types)
    stock_types = [t for t in types if t in {c.value for c in _STOCK_TYPES}] if types else None
    want_stock = (not types) or bool(stock_types)

    parts = []

    if can_stock and want_stock:
        s = StockDocument.objects.all()
        if stock_types:
            s = s.filter(doc_type__in=stock_types)
        if statuses:
            s = s.filter(status__in=statuses)
        if q:
            s = s.filter(code__icontains=q)
        if date_from:
            s = s.filter(created_at__date__gte=date_from)
        if date_to:
            s = s.filter(created_at__date__lte=date_to)
        s = s.annotate(
            kind=Value("stock", output_field=CharField()),
            type_code=F("doc_type"),
            src_wh=F("source_warehouse_id"),
            dest_wh=F("dest_warehouse_id"),
            has_att=Exists(StockDocumentAttachment.objects.filter(document_id=OuterRef("pk"))),
        ).values(*_COLS)
        parts.append(s)

    if can_return and want_return:
        r = ReturnNote.objects.all()
        if statuses:
            r = r.filter(status__in=statuses)
        if q:
            r = r.filter(code__icontains=q)
        if date_from:
            r = r.filter(created_at__date__gte=date_from)
        if date_to:
            r = r.filter(created_at__date__lte=date_to)
        r = r.annotate(
            kind=Value("return", output_field=CharField()),
            type_code=F("return_type"),
            src_wh=F("warehouse_id"),
            dest_wh=Value(None, output_field=IntegerField()),
            has_att=Value(False, output_field=BooleanField()),
        ).values(*_COLS)
        parts.append(r)

    if not parts:
        return Response({"items": [], "total": 0, "limit": limit, "offset": offset})

    union_qs = parts[0] if len(parts) == 1 else parts[0].union(parts[1], all=True)
    union_qs = union_qs.order_by("-created_at", "-id")
    total = union_qs.count()
    page = list(union_qs[offset : offset + limit])

    # Resolve labels for the page (batched, no N+1).
    wh_ids = {row["src_wh"] for row in page if row["src_wh"]} | {
        row["dest_wh"] for row in page if row["dest_wh"]
    }
    wh_names = dict(Warehouse.objects.filter(id__in=wh_ids).values_list("id", "name"))
    return_ids = [row["id"] for row in page if row["kind"] == "return"]
    source_map = {}
    if return_ids:
        for rid, ocode, cname in ReturnNote.objects.filter(id__in=return_ids).values_list(
            "id", "source_order__order_code", "customer_name"
        ):
            label = " · ".join([x for x in [ocode, cname] if x])
            source_map[rid] = label

    items = []
    for row in page:
        if row["kind"] == "stock":
            if row["type_code"] == DocType.TRANSFER:
                wl = f"{wh_names.get(row['src_wh'], '?')} → {wh_names.get(row['dest_wh'], '?')}"
            else:
                wl = wh_names.get(row["src_wh"]) or wh_names.get(row["dest_wh"]) or "—"
            type_label = _DOC_TYPE_LABELS.get(row["type_code"], row["type_code"])
            status_label = _DOC_STATUS_LABELS.get(row["status"], row["status"])
            return_type = None
        else:
            wl = wh_names.get(row["src_wh"], "—")
            type_label = f"Trả hàng: {_RETURN_TYPE_LABELS.get(row['type_code'], row['type_code'])}"
            status_label = _RETURN_STATUS_LABELS.get(row["status"], row["status"])
            return_type = row["type_code"]
        items.append({
            "kind": row["kind"],
            "id": row["id"],
            "code": row["code"],
            "type": row["type_code"] if row["kind"] == "stock" else "return",
            "return_type": return_type,
            "type_label": type_label,
            "status": row["status"],
            "status_label": status_label,
            "warehouse_label": wl,
            "has_attachments": row["has_att"],
            "source_label": source_map.get(row["id"], "") if row["kind"] == "return" else "",
            "created_at": row["created_at"],
        })

    return Response({"items": items, "total": total, "limit": limit, "offset": offset})
```

- [ ] **Step 4: Register the route**

In `backend/apps/inventory/urls.py` add the import and a path:
```python
from apps.inventory.transport.views.ledger_views import ledger_documents
```
```python
    path("ledger-documents", ledger_documents, name="inventory-ledger-documents"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest --create-db apps/inventory/tests/test_ledger_documents.py -q`
Expected: PASS (7 tests). If `.union()` raises about mismatched columns, confirm both `.values(*_COLS)` use the identical `_COLS` order.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/transport/views/ledger_views.py backend/apps/inventory/urls.py backend/apps/inventory/tests/test_ledger_documents.py
git commit -m "feat(inventory): unified ledger-documents endpoint (stock + returns, RBAC-aware)"
```

---

### Task 2: Frontend data layer for ledger

**Files:**
- Create: `frontend/src/features/inventory/ledger-types.ts`
- Create: `frontend/src/features/inventory/ledger-queries.ts`

**Interfaces:**
- Produces: `LedgerRow`, `LedgerResponse` types; `useLedgerDocuments(filters: Record<string,string|string[]>)` → TanStack query returning `LedgerResponse`. Query key prefix `["inventory","ledger"]`.

- [ ] **Step 1: Create types**

Create `frontend/src/features/inventory/ledger-types.ts`:

```ts
export type LedgerKind = "stock" | "return";

export type LedgerRow = {
  kind: LedgerKind;
  id: number;
  code: string;
  type: string; // receipt|issue|adjustment|transfer|return
  return_type: string | null;
  type_label: string;
  status: string;
  status_label: string;
  warehouse_label: string;
  has_attachments: boolean;
  source_label: string;
  created_at: string;
};

export type LedgerResponse = {
  items: LedgerRow[];
  total: number;
  limit: number;
  offset: number;
};
```

- [ ] **Step 2: Create the query hook**

Create `frontend/src/features/inventory/ledger-queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";

import type { LedgerResponse } from "./ledger-types";

const LEDGER_BASE = "/api/inventory/ledger-documents";

export const ledgerKeys = {
  list: (filters: Record<string, string | string[]>) =>
    ["inventory", "ledger", filters] as const,
};

export function useLedgerDocuments(filters: Record<string, string | string[]>) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (Array.isArray(v)) v.forEach((x) => x && params.append(k, x));
    else if (v) params.set(k, v);
  }
  return useQuery({
    queryKey: ledgerKeys.list(filters),
    queryFn: () => fetchJson(`${LEDGER_BASE}?${params.toString()}`) as Promise<LedgerResponse>,
    staleTime: 10_000,
  });
}
```

- [ ] **Step 3: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/ledger-types.ts src/features/inventory/ledger-queries.ts --max-warnings=9999` → no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/ledger-types.ts frontend/src/features/inventory/ledger-queries.ts
git commit -m "feat(inventory): ledger data layer hook"
```

---

### Task 3: Unified table on Stock-Documents page

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`

**Interfaces:**
- Consumes: `useLedgerDocuments` + `LedgerRow` (Task 2); existing `CreateDocumentDialog` (in-file), `CreateReturnDialog`, `StockDocumentDetailDialog`, `useStockDocument` (fetch full doc for edit), `useSubmitStockDocument`/`useApproveStockDocument`/`useRejectStockDocument`/`useCancelStockDocument` (id-only), `usePostReturn`/`useCancelReturn` (id-only), `ScanReturnDialog`, shadcn `DropdownMenu`.

- [ ] **Step 1: Switch the list source + columns to ledger**

In `StockDocumentsPage.tsx`, replace the list query (`useStockDocuments(...)`) feeding the table with `useLedgerDocuments(filters)` where `filters` includes `type` (array), `status`, `q`, `limit`, `offset`. Render rows as `LedgerRow`. Update the table header to: `Mã · Loại phiếu · Trạng thái · Kho · Chứng từ · Ngày tạo · Thao tác` and the body to read `r.code`, `r.type_label`, `r.status_label`, `r.warehouse_label`, attachments cell only when `r.kind === "stock"` (the existing `DocAttachmentsCell` needs `attachments`; the ledger row only has `has_attachments` — render a paperclip indicator if `r.has_attachments` that opens detail via `setDetailId(r.id)`, else "—"; do NOT call `DocAttachmentsCell` here since ledger has no attachment list), `new Date(r.created_at).toLocaleDateString("vi-VN")`.

Concretely, the attachments cell becomes:
```tsx
<td className="px-3 py-2">
  {r.kind === "stock" && r.has_attachments ? (
    <button type="button" onClick={() => setDetailId(r.id)} className="text-primary" title="Xem chứng từ">
      <Paperclip className="size-4" />
    </button>
  ) : (
    <span className="text-muted-foreground">—</span>
  )}
</td>
```
Add `Paperclip` to the lucide-react import. Keep `colSpan` equal to the column count (7).

- [ ] **Step 2: Add "Loại phiếu" + status filters**

Above the table, add a "Loại phiếu" Select that sets a `typeFilter` state (values: "" all, `receipt`, `issue`, `adjustment`, `transfer`, `return`) and a status Select (`""`, `draft`, `pending_approval`, `posted`, `cancelled`). Feed them into `filters` as `type: typeFilter ? [typeFilter] : []` and `status: statusFilter`. Reset `offset` to 0 on filter change. Hide the `return` option if the user lacks `returns:view` and hide all stock options if lacking `stock-documents:view` — use `usePermission()` (`const perm = usePermission(); perm("returns:view")`).

- [ ] **Step 3: "Tạo ▼" dropdown + Quét hàng hoàn button**

Replace the single create button with a shadcn `DropdownMenu`:
```tsx
<DropdownMenu>
  <DropdownMenuTrigger asChild>
    <Button><Plus className="mr-1 size-4" />Tạo</Button>
  </DropdownMenuTrigger>
  <DropdownMenuContent align="end">
    <DropdownMenuItem onClick={() => { setEditId(null); setCreateOpen(true); }}>Phiếu kho</DropdownMenuItem>
    <DropdownMenuItem onClick={() => setReturnOpen(true)}>Phiếu trả hàng</DropdownMenuItem>
  </DropdownMenuContent>
</DropdownMenu>
<Button variant="outline" onClick={() => setScanReturnOpen(true)}>Quét hàng hoàn</Button>
```
Add state `const [returnOpen, setReturnOpen] = useState(false);` and `const [scanReturnOpen, setScanReturnOpen] = useState(false);`. Import `CreateReturnDialog` from `@/features/inventory/pages/CreateReturnDialog`, `ScanReturnDialog` from `@/features/inventory/scan/ScanReturnDialog`, and `DropdownMenu*` from `@/components/ui/dropdown-menu`. Mount near the existing CreateDocumentDialog mount:
```tsx
<CreateReturnDialog open={returnOpen} onOpenChange={setReturnOpen} />
<ScanReturnDialog open={scanReturnOpen} onOpenChange={setScanReturnOpen} />
```

- [ ] **Step 4: Per-kind row actions**

In the row "Thao tác" cell, branch on `r.kind`:
- `stock`: keep current actions but source the full doc for edit. Replace the edit handler: add `const [editId, setEditId] = useState<number | null>(null); const { data: editDocFull } = useStockDocument(editId);` and an effect that opens the dialog when the full doc arrives:
  ```tsx
  useEffect(() => {
    if (editId != null && editDocFull) {
      setEditDoc(editDocFull);
      setCreateOpen(true);
    }
  }, [editId, editDocFull]);
  ```
  "Sửa" button (shown when `r.status === "draft"`) → `onClick={() => setEditId(r.id)}`. "Xem" → `setDetailId(r.id)`. Submit/Approve/Reject/Cancel buttons keep calling the id-only mutations with `r.id`, gated by `r.status` exactly as today. When the create dialog closes, reset `setEditId(null)`.
- `return`: render "Ghi sổ" when `r.status === "draft"` → `usePostReturn().mutateAsync(r.id)`; "Huỷ" when `r.status === "posted"` → `useCancelReturn().mutateAsync(r.id)` (import these from `@/features/inventory/returns-queries`). Both with try/catch + toast (mirror ReturnsPage handlers).

After any mutation success, invalidate `["inventory","ledger"]` so the unified list refreshes (the existing stock mutations invalidate `["inventory","documents"]`; add a `queryClient.invalidateQueries({ queryKey: ["inventory","ledger"] })` in the page's success handlers, or call `refetch` from `useLedgerDocuments`). Simplest: capture `const { data, refetch } = useLedgerDocuments(filters)` and call `refetch()` in each action's `onSuccess`/after `mutateAsync`.

- [ ] **Step 5: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/pages/StockDocumentsPage.tsx --max-warnings=9999` → no new errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(inventory): unified stock+return ledger table with type filter and create dropdown"
```

---

### Task 4: Routing + menu cleanup

**Files:**
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/app/modules.ts`

**Interfaces:**
- Consumes: existing `RedirectTo` helper and `Guard` in routes.tsx; `usePermission` for the any-of guard.

- [ ] **Step 1: Allow the merged page with either section + redirect returns**

In `frontend/src/app/routes.tsx`, add an any-of guard near `Guard`:
```tsx
function GuardAny({ sections, children }: { sections: AppSection[]; children: ReactNode }) {
  const perm = usePermission();
  const ok = sections.some((s) => perm(`${s}:view`));
  if (ok) return <>{children}</>;
  // fall back to the standard single-section gate (handles unauthorized UX)
  return <Guard section={sections[0]}>{children}</Guard>;
}
```
Add `import { usePermission } from "@/hooks/usePermission";` if not present. Change the `inventory/stock-documents` route element to:
```tsx
<GuardAny sections={["stock-documents", "returns"]}>
  <StockDocumentsRoute />
</GuardAny>
```
Change the `inventory/returns` route element to a redirect:
```tsx
element: <RedirectTo to="/inventory/stock-documents" />,
```
(Remove its `<Guard section="returns">` wrapper and the `ReturnsRoute` usage. If `ReturnsRoute`/`ReturnsPage` import becomes unused, remove the import to keep eslint clean.)

- [ ] **Step 2: Remove the Returns tab from the inventory module**

In `frontend/src/app/modules.ts`, delete the line:
```tsx
      { key: "returns", labelKey: "topnav.tabs.returns", icon: Undo2 },
```
If `Undo2` import becomes unused, remove it from the lucide import.

- [ ] **Step 3: Typecheck + lint + build**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/app/routes.tsx src/app/modules.ts --max-warnings=9999` → no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/routes.tsx frontend/src/app/modules.ts
git commit -m "feat(inventory): redirect returns route and remove returns tab"
```

---

### Task 5: Verification

**Files:** none.

- [ ] **Step 1: Backend + FE checks**

Run:
- Backend: `python -m pytest --create-db apps/inventory -q` (ledger tests + no inventory regressions).
- FE (from `frontend/`): `npx tsc --noEmit` → 0; `npx eslint src/features/inventory src/app/routes.tsx src/app/modules.ts --max-warnings=9999` → no errors; `npx vitest run src/features/inventory` → pass.

- [ ] **Step 2: Push**

```bash
git push origin feat/merge-returns-into-stock-documents
```

---

## Self-Review

**Spec coverage:** M1 unified table → Task 3. M2 backend union endpoint → Task 1. M3 RBAC (per-source + either-section page) → Task 1 (source filter + 403) and Task 4 (GuardAny). M4 Tạo dropdown reusing both dialogs → Task 3 Step 3. M5 per-kind actions → Task 3 Step 4. M6 remove tab + redirect + move Quét hàng hoàn → Task 4 + Task 3 Step 3. M7 returns no attachments/detail → Task 3 Step 1 (paperclip only for stock, "—" for return; no new return detail).

**Placeholder scan:** Task 3 references reading the existing page to place edits — concrete (exact column list, exact handlers, exact imports given). No TBD/TODO.

**Type consistency:** `LedgerRow` fields (Task 2) match the endpoint response keys (Task 1) exactly: kind, id, code, type, return_type, type_label, status, status_label, warehouse_label, has_attachments, source_label, created_at. `useLedgerDocuments`/`ledgerKeys` names consistent between Task 2 and Task 3. Query-invalidation key `["inventory","ledger"]` consistent (Task 2 prefix, Task 3 refetch).

**Known limitation (acceptable):** the unified list's stock "Sửa" path does an extra fetch (`useStockDocument`) to get line items — necessary because the ledger row is normalized without lines.
