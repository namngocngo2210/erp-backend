# Thiết kế: Duyệt phiếu kho (Accounting approval cho StockDocument)

- **Ngày:** 2026-06-17
- **Trạng thái:** ✅ Đã triển khai (branch `feature/stock-document-approval`)
- **Liên quan:** `docs/erp-inventory.md`, [[shipments-page-feature]]

## 1. Mục tiêu

Nhân viên kho tạo phiếu kho nhưng **phải có kế toán duyệt** thì phiếu mới ghi sổ (mới tác động
tồn kho). Tách bạch người tạo và người duyệt (separation of duties / 4-eyes).

## 2. Quyết định (đã chốt với product owner)

| # | Quyết định | Giá trị |
|---|-----------|---------|
| 1 | Phạm vi | **Tất cả `StockDocument`** (receipt/issue/adjustment/transfer). **TRỪ phiếu trả** (`ReturnNote` — model riêng, giữ nguyên luồng cũ). |
| 2 | Số cấp duyệt | **1 cấp** (kế toán). |
| 3 | Thông báo | **Chỉ hiển thị "chờ duyệt"** trong app (badge/filter). KHÔNG push Lark/notification. |
| 4 | Tách quyền | Người tạo/gửi duyệt ≠ người duyệt; **chặn tự duyệt phiếu của chính mình**. |
| 5 | Điểm ghi sổ | Tồn kho **chỉ đổi khi kế toán DUYỆT** (không phải lúc tạo). |

## 3. Hiện trạng (đã khảo sát)

- `StockDocument.status`: `draft / posted / cancelled` (`apps/inventory/models.py:101-104`). KHÔNG có
  state "chờ duyệt", KHÔNG có `submitted_by/approved_by/...`.
- `StockDocumentService.post_document` (`stock_document_service.py:65-77`): **post = vừa duyệt vừa
  ghi sổ 1 bước**; sinh `StockMovement` qua `_apply_line`. Endpoint `POST /api/inventory/documents/<id>/post`
  (`document_views.py:91-107`) chỉ gate `require_section("stock-documents")` = `:view` → **ai có view là
  ghi sổ được**.
- RBAC: catalog đã có action `approve` cho `stock-documents` (`rbac_catalog.py:38`) nhưng **chưa
  enforce**. Cơ chế `require_permission("stock-documents:approve")` **đã có sẵn**
  (`apps/users/permissions.py:7-26`); `require_section` chỉ là shim `:view`.
- `ReturnNote` là model + endpoint riêng (`returns/...`, `return_views.py`) — KHÔNG đụng.
- FE `StockDocumentsPage.tsx`: nút "Ghi sổ"/"Huỷ" theo status, chưa kiểm tra quyền (dựa 403 backend).
  Cần xác minh FE có truy cập danh sách permission theo action (để hiện nút Duyệt đúng người) hay chỉ
  có `allowed_sections` (sẽ xử lý trong plan).

## 4. State machine mới

```
        (kho tạo)            (kho: Gửi duyệt)        (kế toán: Duyệt → SINH MOVEMENT)
  ─────▶ DRAFT ───────────────▶ PENDING_APPROVAL ───────────────▶ POSTED ──(kế toán: Huỷ)──▶ CANCELLED
            ▲                          │
            └──(kế toán: Từ chối)──────┘
```

- **DRAFT**: tạo/sửa lines. (giữ như cũ — `create_document` vẫn ra DRAFT)
- **PENDING_APPROVAL**: chờ kế toán; không sửa lines.
- **POSTED**: đã ghi sổ (tồn đã đổi).
- **CANCELLED**: đã huỷ (đảo movement) — chỉ từ POSTED.

## 5. Backend — chi tiết

### 5.1 Model (`apps/inventory/models.py`)
- `DocStatus`: thêm `PENDING_APPROVAL = "pending_approval", "Chờ duyệt"`.
- `StockDocument`: thêm field
  `submitted_by` (FK User null), `submitted_at` (dt null),
  `approved_by` (FK User null), `approved_at` (dt null),
  `rejected_reason` (Text blank).
- Migration: thêm cột (nullable). Dữ liệu cũ DRAFT/POSTED/CANCELLED giữ nguyên (không backfill).

### 5.2 Service (`stock_document_service.py`)
Tách `post_document` cũ thành 3 hành động (giữ `_apply_line`/`cancel_document` không đổi logic ghi sổ):
- `submit_for_approval(document_id, user_id)`: yêu cầu `status == DRAFT` → set `PENDING_APPROVAL`,
  `submitted_by/at`. (KHÔNG sinh movement.)
- `approve_document(document_id, user_id)`: yêu cầu `status == PENDING_APPROVAL`; **chặn tự duyệt**
  (`submitted_by_id == user_id` → `ValueError("Người gửi duyệt không thể tự duyệt phiếu")`); chạy
  `_apply_line` cho từng line (**sinh movement, đổi tồn**); set `POSTED`, `posted_at`, `approved_by/at`.
- `reject_document(document_id, user_id, reason)`: yêu cầu `PENDING_APPROVAL` → set `DRAFT`,
  lưu `rejected_reason`, clear `submitted_by/at`. (KHÔNG sinh movement.)
- Giữ `cancel_document` (POSTED→CANCELLED, đảo movement).
- Bỏ `post_document` (thay bằng submit+approve) — hoặc giữ làm private helper nếu approve dùng lại.

### 5.3 View + RBAC (`document_views.py`, `urls.py`)
- `POST documents/<id>/submit` → `submit_for_approval`. Gate: `require_section("stock-documents")` (`:view` —
  nhân viên kho). 
- `POST documents/<id>/approve` → `approve_document`. Gate: **`require_permission("stock-documents:approve")`** (kế toán).
- `POST documents/<id>/reject` (body `{reason}`) → `reject_document`. Gate: `require_permission("stock-documents:approve")`.
- `POST documents/<id>/cancel` → giữ, nhưng **đổi gate sang `require_permission("stock-documents:approve")`**
  (huỷ đảo tồn = hành động kế toán).
- **Gỡ** endpoint `/post` cũ (thay bằng submit+approve).
- `StockDocumentSerializer`: thêm `status` label + các field mới (`submitted_by`, `approved_by`,
  `submitted_at`, `approved_at`, `rejected_reason`) cho FE hiển thị.
- Lỗi tự-duyệt / sai state → `ValueError` → view trả 400 (đã có pattern bắt `ValueError`).

### 5.4 Audit
Không thêm domain event (YAGNI — chỉ hiển thị chờ duyệt). Thông tin who/when nằm ở field
`submitted_by/approved_by/...` (đủ cho audit). Generic AuditLog middleware vẫn log request.

## 6. Frontend (`StockDocumentsPage.tsx` + `documents-queries.ts`)

- **Quyền**: xác định FE có biết user có `stock-documents:approve` không. Nếu user object expose danh
  sách permission/action → dùng để hiện nút Duyệt/Từ chối. Nếu CHỈ có `allowed_sections` (không có
  action), bổ sung expose cờ `can_approve` cho section này từ backend (kiểm tra `resolve_effective_permissions`
  đã được serialize ra FE chưa — xử lý trong plan). 
- **Filter trạng thái**: thêm "Chờ duyệt" (`pending_approval`) vào dropdown lọc.
- **Nút theo trạng thái + quyền**:
  - `draft`: "Gửi duyệt" (→ `/submit`) + giữ sửa/xoá hiện có.
  - `pending_approval`: nếu `canApprove` → "Duyệt" (`/approve`) + "Từ chối" (`/reject`, hỏi lý do);
    nếu không → badge "Chờ duyệt" (chỉ xem).
  - `posted`: "Huỷ" (`/cancel`) — chỉ hiện nếu `canApprove`.
- **queries**: thêm `useSubmit/useApprove/useReject` mutation; bỏ/đổi `usePostStockDocument` (post → submit).
  Invalidate `["inventory","documents"]` + `["inventory","stock"]` như cũ.
- Badge/cột hiển thị người gửi & người duyệt nếu có chỗ.

## 7. Edge cases & rủi ro

- Phiếu DRAFT cũ (trước khi có flow) vẫn submit được bình thường.
- Phiếu POSTED cũ giữ nguyên; cancel vẫn hoạt động (gate đổi sang `:approve`).
- **Vận hành (bắt buộc)**: admin cấp quyền `stock-documents:approve` cho role **Kế toán**, và đảm bảo
  role **Kho KHÔNG có** `:approve` (nếu kho có sẵn `:approve` từ catalog mặc định → phải gỡ, nếu không
  separation of duties vô nghĩa). Kiểm tra role hiện tại khi triển khai.
- Chặn tự duyệt enforce ở service (kể cả khi 1 user có cả 2 quyền).
- Không sửa lines khi `pending_approval`/`posted` (chỉ DRAFT mới sửa) — kiểm tra endpoint sửa lines
  (nếu có) chặn theo state.

## 8. Kiểm thử

- **Service**: submit (draft→pending, không movement); approve (pending→posted, **có movement**, set
  approved_by); approve sai state → lỗi; **tự duyệt → lỗi**; reject (pending→draft, lưu reason); cancel
  (posted→cancelled, đảo movement).
- **View/RBAC**: user chỉ `:view` submit được nhưng **approve → 403**; user có `:approve` approve được;
  tự duyệt → 400; cancel cần `:approve`.
- **FE**: nút Duyệt/Từ chối chỉ hiện với người có quyền; luồng submit→approve cập nhật tồn; filter "Chờ duyệt".

## 9. Ngoài phạm vi

- Phiếu trả (`ReturnNote`) — không đổi.
- Nhiều cấp duyệt, thông báo push (Lark), đính kèm chứng từ — để sau.
- Không đụng core kho (`StockCommandService`, movements, projection).

## 10. Ghi chú triển khai

**State machine thực tế:**
```
DRAFT ──(submit)──▶ PENDING_APPROVAL ──(approve)──▶ POSTED ──(cancel)──▶ CANCELLED
                           │
                     (reject) ──▶ DRAFT
```

**Các điểm then chốt:**
- `approve_document` = ghi sổ (sinh `StockMovement` + cập nhật `StockItem`) + chặn tự duyệt
  (`submitted_by_id == user_id` → `ValueError` → HTTP 400). Nếu `user_id=None` (system), tự duyệt được phép.
- RBAC gates: `submit` yêu cầu `stock-documents:view`; `approve/reject/cancel` yêu cầu `stock-documents:approve`.
- FE nút "Duyệt" / "Từ chối" / "Huỷ" bị gắn sau `usePermission("stock-documents:approve")` — chỉ render
  cho user có quyền đó.
- `ReturnNote` không bị đụng — endpoint `/api/inventory/returns/<id>/post` giữ nguyên.

**Vận hành (bắt buộc khi deploy):**
1. Cấp permission `stock-documents:approve` cho role **Kế toán** trong trang Quản lý vai trò.
2. Đảm bảo role **Kho KHÔNG có** `stock-documents:approve` — nếu có thì gỡ bỏ, vì nếu Kho tự duyệt được thì separation of duties vô nghĩa.
3. Chạy migration `0003_stockdocument_approval_fields.py` (đã có trong branch) trước khi deploy.
