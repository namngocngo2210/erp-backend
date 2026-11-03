# Duyệt phiếu kho (Accounting approval) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phiếu kho (StockDocument) phải qua kế toán DUYỆT mới ghi sổ (mới đổi tồn); tách người tạo và người duyệt.

**Architecture:** Thêm state `pending_approval` + state machine `DRAFT → (kho: submit) PENDING_APPROVAL → (kế toán: approve = ghi sổ) POSTED`. Tồn kho chỉ đổi ở bước approve (tái dùng `_apply_line` sẵn có). Tách quyền bằng RBAC: submit gate `:view`, approve/reject/cancel gate `:approve`; chặn tự duyệt ở service. Phiếu trả (ReturnNote) không đụng.

**Tech Stack:** Django + DRF, pytest; React + TS, react-query, OrdersTable-độc-lập StockDocumentsPage.

**Spec:** `docs/superpowers/specs/2026-06-17-stock-document-approval-design.md`

## Global Constraints

- Phạm vi: chỉ `StockDocument` (receipt/issue/adjustment/transfer). KHÔNG đụng `ReturnNote` / `returns/*`.
- Tồn kho chỉ đổi khi APPROVE. Submit/reject KHÔNG sinh movement. Không sửa core kho (`StockCommandService`, `_apply_line` logic).
- Tách quyền: approve/reject/cancel yêu cầu permission `stock-documents:approve`; submit yêu cầu `stock-documents:view`. Người `submitted_by` KHÔNG được approve phiếu của chính mình (raise ở service).
- DocStatus values: `draft`, `pending_approval`, `posted`, `cancelled`.
- Backend test + makemigrations chạy bằng ephemeral docker (prod image thiếu tests):
  ```
  wc=$(docker ps -qf name=web | head -1)
  docker run --rm --user root --network desi-order_default \
    --env-file <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$wc" | grep -E '^(DATABASE|POSTGRES|DJANGO|REDIS|SECRET|DB_|CELERY|MINIO|ALLOWED|CACHE)') \
    -v "$(pwd)/backend:/app" -w /app desi-order-web python -m pytest <paths> -q --reuse-db
  ```
  (đổi `python -m pytest ...` thành `python manage.py makemigrations inventory` để tạo migration; file ghi vào source đã mount.)
- FE quyền: `usePermission()` (`hooks/usePermission.ts`) → `can("stock-documents:approve")`. Không cần backend đổi để FE biết quyền.

---

## File Structure

- Modify: `backend/apps/inventory/models.py` — `DocStatus` + fields mới.
- Create: `backend/apps/inventory/migrations/00XX_stock_document_approval.py` (sinh tự động).
- Modify: `backend/apps/inventory/application/services/stock_document_service.py` — submit/approve/reject; bỏ `post_document`.
- Modify: `backend/apps/inventory/transport/views/document_views.py` — view submit/approve/reject; gate cancel; bỏ post.
- Modify: `backend/apps/inventory/urls.py` — routes.
- Modify: `backend/apps/inventory/transport/serializers/document_serializers.py` — field mới.
- Test: `backend/apps/inventory/tests/test_stock_document_approval.py` (mới).
- Modify FE: `frontend/src/features/inventory/documents-types.ts`, `documents-queries.ts`, `pages/StockDocumentsPage.tsx`.

---

## SPRINT 1 — Backend: model + state

### Task 1: Model + migration

**Files:**
- Modify: `backend/apps/inventory/models.py` (`DocStatus` ~101-104, `StockDocument` ~107-135)
- Create: migration (sinh tự động)
- Test: `backend/apps/inventory/tests/test_stock_document_approval.py`

**Interfaces:**
- Produces: `DocStatus.PENDING_APPROVAL = "pending_approval"`; `StockDocument.submitted_by/submitted_at/approved_by/approved_at/rejected_reason`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_stock_document_approval.py` với test model tối thiểu (bám fixture của `apps/inventory/tests/test_stock_document_service.py` nếu có — đọc để dùng đúng cách tạo Warehouse/Product/StockDocument):
```python
import pytest
from apps.inventory.models import StockDocument, DocStatus, DocType


@pytest.mark.django_db
def test_doc_has_approval_fields_and_pending_state(warehouse, product):
    assert DocStatus.PENDING_APPROVAL == "pending_approval"
    doc = StockDocument.objects.create(code="NK-T1", doc_type=DocType.RECEIPT, dest_warehouse=warehouse)
    # các field mới tồn tại + mặc định None/blank
    assert doc.submitted_by_id is None and doc.submitted_at is None
    assert doc.approved_by_id is None and doc.approved_at is None
    assert doc.rejected_reason == ""
```
(Nếu chưa có fixture `warehouse`/`product`, tạo trong file/conftest theo `test_stock_sales_service.py`.)

- [ ] **Step 2: Chạy test — FAIL** (AttributeError `PENDING_APPROVAL` / field thiếu).

- [ ] **Step 3: Sửa model**

Trong `DocStatus` (TextChoices) thêm:
```python
    PENDING_APPROVAL = "pending_approval", "Chờ duyệt"
```
(đặt giữa DRAFT và POSTED.)

Trong `StockDocument`, thêm các field (cạnh `posted_at`):
```python
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="submitted_stock_documents",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_stock_documents",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True, default="")
```
Kiểm tra `from django.conf import settings` đã import ở đầu file (model `created_by` đã dùng `settings.AUTH_USER_MODEL` → đã có; nếu chưa, thêm).

- [ ] **Step 4: Sinh migration**

Chạy (ephemeral docker, đổi command thành):
`python manage.py makemigrations inventory`
File migration mới xuất hiện trong `backend/apps/inventory/migrations/`. Mở xem chỉ gồm AddField + AlterField(status choices) — không có thao tác lạ.

- [ ] **Step 5: Chạy test — PASS** (sau khi migration áp; pytest `--reuse-db` có thể cần `--create-db` lần đầu để áp migration mới — nếu lỗi schema, chạy lại với `--create-db`).

- [ ] **Step 6: Commit**
```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/ backend/apps/inventory/tests/test_stock_document_approval.py
git commit -m "feat(inventory): add pending_approval state + audit fields to stock document"
```

---

## SPRINT 2 — Backend: service

### Task 2: submit / approve / reject

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_document_service.py`
- Test: `backend/apps/inventory/tests/test_stock_document_approval.py`

**Interfaces:**
- Consumes: `_apply_line` (giữ nguyên), `DocStatus.PENDING_APPROVAL` (Task 1).
- Produces: `StockDocumentService.submit_for_approval(document_id, user_id)`, `approve_document(document_id, user_id)`, `reject_document(document_id, user_id, reason)`. Bỏ `post_document`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `test_stock_document_approval.py` (bám cách `test_stock_document_service.py` tạo phiếu DRAFT + seed tồn + assert `StockItem`/`StockMovement`):
```python
# submit: draft -> pending, không movement
def test_submit_moves_to_pending_no_movement(...): ...
# approve: pending -> posted, CÓ movement, set approved_by
def test_approve_posts_and_applies_movement(...): ...
# approve sai state -> ValueError
def test_approve_requires_pending(...): ...
# tự duyệt -> ValueError
def test_submitter_cannot_approve_own_document(...):
    # submit bởi user A, approve bởi user A -> raise
# reject: pending -> draft, lưu reason, clear submitted_by
def test_reject_returns_to_draft_with_reason(...): ...
```
Dùng `with pytest.raises(ValueError)` cho 2 case lỗi. Tạo 2 user (A submit, B approve) cho happy-path + dùng A cho case tự-duyệt.

- [ ] **Step 2: Chạy test — FAIL.**

- [ ] **Step 3: Sửa service**

Trong `stock_document_service.py`, THAY method `post_document` bằng 3 method sau (giữ `_apply_line`, `cancel_document`, `create_document`, `_generate_code` nguyên):
```python
    @staticmethod
    @transaction.atomic
    def submit_for_approval(*, document_id: int, user_id: int | None = None) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới gửi duyệt được (đang {doc.status}).")
        doc.status = DocStatus.PENDING_APPROVAL
        doc.submitted_by_id = user_id
        doc.submitted_at = timezone.now()
        doc.save(update_fields=["status", "submitted_by", "submitted_at"])
        return doc

    @staticmethod
    @transaction.atomic
    def approve_document(*, document_id: int, user_id: int | None = None) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.PENDING_APPROVAL:
            raise ValueError(f"Chỉ phiếu chờ duyệt mới ghi sổ được (đang {doc.status}).")
        if user_id is not None and doc.submitted_by_id == user_id:
            raise ValueError("Người gửi duyệt không thể tự duyệt phiếu của mình.")
        for line in doc.lines.select_related("product").all():
            StockDocumentService._apply_line(doc, line, user_id)
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.approved_by_id = user_id
        doc.approved_at = timezone.now()
        doc.save(update_fields=["status", "posted_at", "approved_by", "approved_at"])
        return doc

    @staticmethod
    @transaction.atomic
    def reject_document(*, document_id: int, user_id: int | None = None, reason: str = "") -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.PENDING_APPROVAL:
            raise ValueError(f"Chỉ phiếu chờ duyệt mới từ chối được (đang {doc.status}).")
        doc.status = DocStatus.DRAFT
        doc.rejected_reason = reason or ""
        doc.submitted_by = None
        doc.submitted_at = None
        doc.save(update_fields=["status", "rejected_reason", "submitted_by", "submitted_at"])
        return doc
```

- [ ] **Step 4: Chạy test — PASS** (5 test service).

- [ ] **Step 5: Commit**
```bash
git add backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_document_approval.py
git commit -m "feat(inventory): submit/approve/reject stock document service"
```

---

## SPRINT 3 — Backend: views + RBAC + serializer

### Task 3: Endpoints + quyền + serializer

**Files:**
- Modify: `backend/apps/inventory/transport/views/document_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Modify: `backend/apps/inventory/transport/serializers/document_serializers.py`
- Test: `backend/apps/inventory/tests/test_stock_document_approval.py`

**Interfaces:**
- Consumes: service methods (Task 2). `require_permission` từ `apps.users.permissions`.

- [ ] **Step 1: Viết test API thất bại**

Thêm API test (bám `apps/orders/tests/test_shipment_orders_api.py` cho `make_user_with_sections` + APIClient; với permission theo action, đọc `apps/users/test_helpers` xem có helper cấp permission `stock-documents:approve` không — nếu chỉ có `make_user_with_sections` cấp `:view`, tạo user có role/permission `approve` theo cơ chế RBAC thật):
```python
# user chỉ :view -> submit 200, approve 403
# user :approve -> approve 200 (phiếu pending do user khác submit) + tồn đổi
# tự duyệt -> 400
# cancel cần :approve (user :view -> 403)
```
Dùng `reverse("inventory-document-submit"/"...-approve"/"...-reject"/"...-cancel")`.

- [ ] **Step 2: Chạy test — FAIL** (NoReverseMatch / 403 sai).

- [ ] **Step 3: Serializer — thêm field**

Trong `document_serializers.py` `StockDocumentSerializer`:
```python
    submitted_by_name = serializers.CharField(source="submitted_by.username", read_only=True, default=None)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True, default=None)
```
và thêm vào `Meta.fields`: `"submitted_by", "submitted_by_name", "submitted_at", "approved_by", "approved_by_name", "approved_at", "rejected_reason"`.

- [ ] **Step 4: Views**

Trong `document_views.py`:
- Thêm import: `from apps.users.permissions import require_permission` (giữ `require_section`).
- THAY `post_document_view` bằng `submit_document_view`:
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("stock-documents")])
@never_cache
def submit_document_view(request, document_id):
    try:
        doc = StockDocumentService.submit_for_approval(document_id=document_id, user_id=request.user.id)
    except StockDocument.DoesNotExist:
        return Response({"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = StockDocument.objects.prefetch_related("lines__product").select_related(
        "source_warehouse", "dest_warehouse").get(pk=doc.pk)
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)
```
- Thêm `approve_document_view` + `reject_document_view` (gate `require_permission("stock-documents:approve")`):
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:approve")])
@never_cache
def approve_document_view(request, document_id):
    try:
        doc = StockDocumentService.approve_document(document_id=document_id, user_id=request.user.id)
    except StockDocument.DoesNotExist:
        return Response({"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = StockDocument.objects.prefetch_related("lines__product").select_related(
        "source_warehouse", "dest_warehouse").get(pk=doc.pk)
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:approve")])
@never_cache
def reject_document_view(request, document_id):
    reason = str(request.data.get("reason", "") or "")
    try:
        doc = StockDocumentService.reject_document(document_id=document_id, user_id=request.user.id, reason=reason)
    except StockDocument.DoesNotExist:
        return Response({"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = StockDocument.objects.prefetch_related("lines__product").select_related(
        "source_warehouse", "dest_warehouse").get(pk=doc.pk)
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)
```
- `cancel_document_view`: đổi decorator gate thành `require_permission("stock-documents:approve")`.

- [ ] **Step 5: URLs**

Trong `urls.py`: bỏ import + route `post_document_view`; thêm import `submit_document_view, approve_document_view, reject_document_view`. Routes:
```python
    path("documents/<int:document_id>/submit", submit_document_view, name="inventory-document-submit"),
    path("documents/<int:document_id>/approve", approve_document_view, name="inventory-document-approve"),
    path("documents/<int:document_id>/reject", reject_document_view, name="inventory-document-reject"),
    path("documents/<int:document_id>/cancel", cancel_document_view, name="inventory-document-cancel"),
```

- [ ] **Step 6: Chạy test — PASS.**

- [ ] **Step 7: Commit**
```bash
git add backend/apps/inventory/
git commit -m "feat(inventory): submit/approve/reject endpoints with accounting RBAC gate"
```

---

## SPRINT 4 — Frontend

### Task 4: Types + queries

**Files:**
- Modify: `frontend/src/features/inventory/documents-types.ts`
- Modify: `frontend/src/features/inventory/documents-queries.ts`

**Interfaces:**
- Produces: `useSubmitStockDocument`, `useApproveStockDocument`, `useRejectStockDocument`; `StockDocumentRow.status` thêm `"pending_approval"` + field mới.

- [ ] **Step 1: Types**

Trong `documents-types.ts`: type `StockDocumentRow.status` thêm `"pending_approval"`; thêm field optional `submitted_by_name?: string | null`, `approved_by_name?: string | null`, `submitted_at?: string | null`, `approved_at?: string | null`, `rejected_reason?: string` (đọc file để khớp tên/format hiện có).

- [ ] **Step 2: Queries**

Trong `documents-queries.ts`: THÊM `useSubmitStockDocument` (URL `/${id}/submit`, body none) — **giữ tạm `usePostStockDocument`** để build không đỏ giữa chừng (Task 5 sẽ xoá sau khi page chuyển xong). Thêm tiếp `useApprove`/`useReject`:
```typescript
export function useApproveStockDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      fetchJson(`${DOCS_BASE}/${id}/approve`, { method: "POST" }) as Promise<StockDocumentRow>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "documents"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "stock"] });
    },
  });
}

export function useRejectStockDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      fetchJson(`${DOCS_BASE}/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }) as Promise<StockDocumentRow>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "documents"] });
    },
  });
}
```
Giữ `useCancelStockDocument`.

- [ ] **Step 3: Verify**

`cd frontend && npx tsc --noEmit -p tsconfig.json` — PASS (page vẫn dùng `usePostStockDocument` cũ chưa bị xoá). Commit.
```bash
git add frontend/src/features/inventory/documents-types.ts frontend/src/features/inventory/documents-queries.ts
git commit -m "feat(inventory): FE submit/approve/reject document mutations"
```

### Task 5: StockDocumentsPage UI

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`

**Interfaces:**
- Consumes: Task 4 mutations; `usePermission` from `@/hooks/usePermission`.

- [ ] **Step 1: Quyền + nhãn**

Trong `StockDocumentsPage.tsx`:
- Import `usePermission` (`@/hooks/usePermission`); `const canApprove = usePermission()("stock-documents:approve");`
- Đổi `postMutation = usePostStockDocument()` → `submitMutation = useSubmitStockDocument()`; thêm `approveMutation = useApproveStockDocument()`, `rejectMutation = useRejectStockDocument()`. Sau khi page không còn dùng `usePostStockDocument`, **xoá nó khỏi `documents-queries.ts`** (dọn dẹp) — verify tsc/build vẫn xanh.
- `STATUS_LABELS`: thêm `pending_approval: "Chờ duyệt"`. `STATUS_CLASSES`: thêm `pending_approval: "bg-amber-100 text-amber-700"`.
- Filter trạng thái (dropdown lọc status — tìm chỗ render option): thêm option `{ value: "pending_approval", label: "Chờ duyệt" }`.

- [ ] **Step 2: Nút theo trạng thái**

THAY khối nút (~463-487) bằng:
```tsx
<div className="flex gap-2">
  {r.status === "draft" && (
    <Button
      size="sm" variant="outline"
      className="h-7 px-2 text-xs text-blue-700 border-blue-300 hover:bg-blue-50"
      disabled={submitMutation.isPending}
      onClick={() => handleSubmit(r.id)}
    >
      Gửi duyệt
    </Button>
  )}
  {r.status === "pending_approval" && canApprove && (
    <>
      <Button
        size="sm" variant="outline"
        className="h-7 px-2 text-xs text-green-700 border-green-300 hover:bg-green-50"
        disabled={approveMutation.isPending}
        onClick={() => handleApprove(r.id)}
      >
        Duyệt
      </Button>
      <Button
        size="sm" variant="outline"
        className="h-7 px-2 text-xs text-destructive border-destructive/30 hover:bg-destructive/10"
        disabled={rejectMutation.isPending}
        onClick={() => handleReject(r.id)}
      >
        Từ chối
      </Button>
    </>
  )}
  {r.status === "posted" && canApprove && (
    <Button
      size="sm" variant="outline"
      className="h-7 px-2 text-xs text-destructive border-destructive/30 hover:bg-destructive/10"
      disabled={cancelMutation.isPending}
      onClick={() => handleCancel(r.id)}
    >
      Huỷ
    </Button>
  )}
</div>
```
Thêm handlers (cạnh handlePost cũ — đổi tên):
```tsx
const handleSubmit = (id: number) => submitMutation.mutate(id, { onError: showErr });
const handleApprove = (id: number) => approveMutation.mutate(id, { onError: showErr });
const handleReject = (id: number) => {
  const reason = window.prompt("Lý do từ chối:") ?? "";
  rejectMutation.mutate({ id, reason }, { onError: showErr });
};
```
(`showErr` = cách hiển thị lỗi hiện có trong file — đọc `handlePost`/`handleCancel` cũ để dùng đúng toast/onError pattern; nếu chưa có, dùng `toast.error` từ sonner với message từ lỗi.)

- [ ] **Step 3: Verify**

`cd frontend && npx tsc --noEmit -p tsconfig.json && npm run build` — PASS. Kiểm tra: phiếu draft hiện "Gửi duyệt"; pending hiện badge "Chờ duyệt" + (nếu có quyền) Duyệt/Từ chối; posted hiện Huỷ (nếu có quyền). Filter "Chờ duyệt" hoạt động.

- [ ] **Step 4: Commit**
```bash
git add frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(inventory): approval buttons + pending filter on stock documents page"
```

---

## SPRINT 5 — Verify + docs

### Task 6: E2E + docs

- [ ] **Step 1:** Chạy toàn bộ test inventory (đặc biệt `test_stock_document_approval.py` + test stock document/service cũ) bằng ephemeral docker — PASS. Nếu test cũ có gọi `post_document`/endpoint `/post` → cập nhật theo API mới.
- [ ] **Step 2:** `npm run build` FE — PASS.
- [ ] **Step 3:** Kịch bản tay: kho tạo phiếu → "Gửi duyệt" → kế toán "Duyệt" → tồn đổi; "Từ chối" → về nháp; user kho không thấy nút Duyệt; tự duyệt → lỗi.
- [ ] **Step 4:** Cập nhật spec: đánh dấu "Đã triển khai" + nhắc vận hành (cấp `stock-documents:approve` cho Kế toán, gỡ khỏi Kho).
- [ ] **Step 5: Commit**
```bash
git add docs/superpowers/specs/2026-06-17-stock-document-approval-design.md
git commit -m "docs(inventory): mark stock document approval implemented"
```

---

## Rủi ro / lưu ý

- **Test cũ** của stock document/service có thể gọi `post_document` (đã bỏ) → Task 6 Step 1 cập nhật chúng sang submit+approve.
- **Vận hành**: admin phải cấp `stock-documents:approve` cho role Kế toán và đảm bảo role Kho KHÔNG có (nếu không, separation vô nghĩa). Kiểm tra role hiện tại.
- `--reuse-db`: migration mới (Task 1) cần áp — nếu lỗi schema chạy `--create-db` một lần.
- Không sửa lines khi pending/posted: nếu có endpoint sửa lines, đảm bảo nó vốn chỉ cho DRAFT (kiểm tra; ngoài phạm vi nếu chưa có).
- Không đụng ReturnNote / returns endpoints.
