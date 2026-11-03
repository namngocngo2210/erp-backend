# Spec — Inventory: Quét mã chưa sync → bảng chờ + cron tự khớp

> Sửa flow **Quét vận đơn** (xuất giao / nhập hoàn): khi mã vận đơn chưa có trong hệ thống
> (đơn chưa được cron KiotViet 5' kéo về), **vẫn ghi nhận** vào bảng chờ `PendingShipmentScan`.
> Cron mỗi 60s tự khớp: có đơn → xuất kho / tạo phiếu trả như thao tác tay; chưa có →
> hiển thị ở phần kho danh sách "Chờ khớp đơn".
>
> - Ngày tạo: 2026-07-06
> - Nền: `docs/erp-inventory-scan-shipment.md` (flow quét gốc), `docs/erp-inventory-returns.md` (ReturnNote), `docs/architecture-standard.md`.
> - Module liên quan: `inventory` (model + cron + UI kho), `orders` (lookup + dispatch-batch), `warehouses`.

> ⚠️ **Cập nhật 2026-07-11.** "Tạo `ReturnNote`" trong doc này nay là **tạo `ReturnEntry`** trong **phiếu
> ngày** của **ngày quét gốc** (`scan.created_at` giờ VN; phiếu ngày đó đã chốt → tạo phiếu mới cùng ngày).
> `result_ref` trỏ tới phiếu ngày; guard chống trùng theo `shipping_code` chuyển sang tầng entry.
> → **`docs/erp-return-daily-note.md`**.

---

## 1. Quyết định kiến trúc

| # | Quyết định |
|---|---|
| Q1 | **1 bảng chờ chung `PendingShipmentScan`** cho cả xuất (`dispatch`) và hoàn (`return`). Phía hoàn **bỏ cơ chế tạo phiếu nháp 0 dòng** cho mã không tìm thấy — phiếu trả chỉ được tạo khi đã khớp đơn (đủ dòng). Flow khớp thủ công PATCH cho các phiếu nháp 0-dòng cũ giữ nguyên. |
| Q2 | **Khi cron khớp được đơn → tự hoàn tất** như thao tác tay: `dispatch` → set `delivery_business_status_code="dispatched"` qua đúng đường dispatch-batch hiện có (tự `issue_for_order` trừ tồn, idempotent); `return` → tạo ReturnNote **nháp đầy đủ dòng** (auto-fill từ OrderItems như PATCH khớp tay), vẫn chờ kế toán duyệt. NV đã thể hiện ý định lúc quét; chỉ thiếu dữ liệu. |
| Q3 | **Check trùng lúc quét query cả bảng chờ**: endpoint lookup trả thêm trạng thái mã đang chờ → FE báo trùng như quét trùng đơn thường. |
| Q4 | **Partial unique `(code, kind) WHERE status='pending'`** — chống ghi trùng mã đang chờ (kể cả 2 phiên quét song song). |
| Q5 | Cron beat **60s** (độ trễ tối đa ~1' sau khi sync 5' kéo đơn về), task riêng của app `inventory`, **không móc vào app `kiotviet`** (tránh coupling — audit 2026-07 mục 6). Single-flight lock qua Redis cache. |
| Q6 | Khớp được đơn nhưng thao tác lỗi (đơn đã huỷ, thiếu tồn config, đã xuất trước đó bằng tay…) → `status="failed"` + `error`, hiển thị ở kho để xử lý tay (Thử lại / Xoá). **Không retry tự động dòng failed** — tránh lặp lỗi vô hạn. |
| Q7 | Cron khớp theo `shipping_code` **hoặc** `invoice_code` (mã hoá đơn — thêm sau, chỉ `kind=dispatch`); nếu 1 mã trùng cả hai loại ở 2 đơn khác nhau thì đơn khớp `shipping_code` **thắng tuyệt đối**, bất kể tuổi bản ghi. Nhiều đơn cùng **một loại** mã → cron lấy **đơn mới nhất** trong nhóm đó (nhất quán với dialog quét hiện lấy đơn đầu danh sách), ghi chú vào `error` dạng cảnh báo. |
| Q8 | **Ngoài phạm vi**: backfill các phiếu nháp 0-dòng đang tồn tại (vẫn khớp tay); trigger khớp tức thì sau task import KiotViet (có thể thêm 1 dòng dispatch sau nếu cần). |

---

## 2. Model — `PendingShipmentScan` (app `inventory`)

Bảng `pending_shipment_scans`:

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `code` | varchar(64), index | Mã vận đơn đã chuẩn hoá (trim, cùng normalize với lookup) |
| `kind` | choices `dispatch` / `return` | |
| `warehouse` | FK `warehouses.Warehouse` (PROTECT) | Kho xuất / kho nhận chọn lúc quét |
| `return_type` | varchar, null | Chỉ dùng cho `kind=return` (mặc định `defect` như flow quét hoàn) |
| `scanned_by` | FK User (SET_NULL) | |
| `status` | choices `pending` / `matched` / `failed` | |
| `matched_order` | FK Order (SET_NULL), null | |
| `result_ref` | varchar, null | Mã ReturnNote tạo ra (kind=return) |
| `error` | text, default "" | Lý do failed / cảnh báo multiple-match |
| `attempts` | int, default 0 | |
| `last_attempt_at` | datetime, null | |
| `created_at` / `updated_at` | | |

Constraint & index:
- `UniqueConstraint(fields=["code", "kind"], condition=Q(status="pending"), name="uniq_pending_scan_code_kind")`
- Index `(status, created_at)` cho query cron + danh sách kho.

Migration mới trong `apps/inventory/migrations/` (không đụng app `orders` — tránh va số 0098 đang treo ở session khác).

---

## 3. Backend

### 3.1 Endpoint lookup — bổ sung check bảng chờ
`GET /api/orders/lookup-by-shipping-code?code=` (hiện có): thêm field khi mã đang nằm bảng chờ:

```json
404 → {"detail": "not_found", "pending_scan": {"kind": "dispatch", "created_at": "..."}}
```

- Nếu mã 404 **và** có bản ghi `pending` cùng code → trả kèm `pending_scan` để FE báo "đã quét rồi, đang chờ khớp" (beep đỏ, không thêm dòng).
- Mã tìm thấy đơn → giữ nguyên response hiện tại (check trùng đơn đã có sẵn ở FE/BE dispatch).

### 3.2 API bảng chờ — `apps/inventory/transport` (RBAC section `shipments` — cùng section với lookup + dispatch-batch, mọi người quét đều đã có)
- `POST /api/inventory/pending-scans/batch` — body `{"kind": "dispatch|return", "warehouse_id": 1, "return_type": "defect", "codes": ["ABC123", ...]}`. Tạo từng bản ghi `pending`; mã vi phạm partial-unique → trả `{code, ok: false, reason: "duplicate"}` (không fail cả lô). Trả `{results: [{code, ok, reason?}]}`.
- `GET /api/inventory/pending-scans?status=pending|failed&kind=&limit=&offset=` — danh sách cho UI kho (kèm count).
- `POST /api/inventory/pending-scans/<id>/retry` — chỉ cho dòng `failed`: reset về `pending` (+ xoá `error`) để cron thử lại.
- `DELETE /api/inventory/pending-scans/<id>` — xoá dòng quét nhầm (chỉ `pending`/`failed`).

### 3.3 Service khớp — `apps/inventory/application/services/pending_scan_service.py`
`match_pending_scans(limit=200) -> summary`:

1. Lấy batch `status="pending"` order by `created_at` (`select_for_update(skip_locked=True)` per-row trong transaction ngắn — an toàn khi lock Redis hụt).
2. Với mỗi bản ghi: tìm `Order` theo `shipping_code` **hoặc** (chỉ `kind=dispatch`) `invoice_code` exact (loại chuỗi rỗng) — đơn khớp `shipping_code` LUÔN thắng nếu trùng cả hai loại mã ở 2 đơn khác nhau, dù đơn đó cũ hơn. Không thấy → `attempts += 1`, `last_attempt_at`, giữ `pending`.
3. Thấy ≥1 đơn (nhiều đơn cùng MỘT loại mã → lấy mới nhất trong nhóm đó; note lý do vào `error` dạng warning):
   - `kind=dispatch`: gọi đúng service dispatch-batch hiện có (gán `fulfillment_warehouse` + set `dispatched` qua `update_order_inline`) → tự trừ tồn idempotent + phát event. Actor = `scanned_by`.
   - `kind=return`: tạo ReturnNote nháp `source_order=order`, `warehouse`, `return_type`, lines auto-fill từ OrderItems (tái dùng logic auto-fill của PATCH khớp tay), `shipping_code=code`.
   - Thành công → `status="matched"`, `matched_order`, `result_ref`.
   - Exception nghiệp vụ (ValidationError…) → `status="failed"`, `error=str(e)`. Mỗi bản ghi 1 transaction riêng — 1 dòng lỗi không chặn cả lô.

### 3.4 Cron — `apps/inventory/tasks.py` + beat
- Task `match_pending_shipment_scans` (`@shared_task`, `time_limit=120, soft_time_limit=100` — override guard 30s global), single-flight lock Redis (`cache.add` TTL, pattern như import create-only).
- `CELERY_BEAT_SCHEDULE["inventory-match-pending-scans"] = {"task": ..., "schedule": 60.0}` (queue default — như task inventory hiện có).

---

## 4. Frontend

### 4.1 `ScanDispatchDialog` — ghi nhận mã chưa khớp (đổi lớn nhất)
- Mã 404: **không mất nữa** — thêm dòng đỏ badge "Chưa khớp đơn" (mirror UX `ScanReturnDialog`); 404 kèm `pending_scan` → toast "đã quét rồi, đang chờ khớp" + beep đỏ, không thêm dòng.
- State `scanned` chuyển sang dạng row union matched/unmatched (như `scan-return-rows.ts`).
- **Xác nhận xuất kho**: đơn khớp → `dispatch-batch` như hiện tại; mã chưa khớp → `POST /api/inventory/pending-scans/batch` (`kind=dispatch`, kho xuất đã chọn). Kết quả từng dòng hiển thị ✓/✗.

### 4.2 `ScanReturnDialog` — đổi đích ghi của dòng unmatched
- Dòng `unmatched` khi Xác nhận: **không** tạo ReturnNote 0 dòng nữa → `POST pending-scans/batch` (`kind=return`, kho nhận + `return_type` mặc định `defect`). Dòng matched giữ nguyên flow tạo phiếu nháp đủ dòng.
- 404 kèm `pending_scan` → báo trùng, không thêm dòng (đáp ứng Q3).

### 4.3 Trang Phiếu kho (`StockDocumentsPage`) — khối "Chờ khớp đơn"
- Nút/badge đếm `pending + failed` (query React Query, `queries.ts` per-feature theo convention). Mở dialog/panel danh sách: mã vận đơn, loại (Xuất/Hoàn), kho, người quét, thời gian quét, trạng thái (`Chờ khớp` / `Lỗi` + tooltip `error`), hành động **Thử lại** (failed) / **Xoá**.
- API module mới `src/lib/api/pending-scans.ts` typed từ `schema.d.ts` (openapi-typescript). Components dùng `components/ui`, không react-select.

---

## 5. Test

**BE (`apps/inventory/tests/test_pending_scan_*.py`):**
- Service khớp: dispatch thành công (trạng thái + StockMovement trừ tồn); return tạo phiếu nháp đủ dòng đúng `return_type`; chưa có đơn → attempts tăng, giữ pending; đơn đã dispatched trước → matched idempotent (không trừ đôi); đơn huỷ / lỗi nghiệp vụ → failed + error; nhiều đơn cùng mã → lấy mới nhất; chạy lại cron không xử lý lại dòng matched/failed.
- API: batch create + duplicate partial-unique; retry chỉ cho failed; RBAC section inventory; lookup 404 trả `pending_scan`.

**FE (vitest):**
- `ScanDispatchDialog`: 404 thêm dòng đỏ; confirm tách 2 đường matched/unmatched; 404+pending báo trùng.
- `ScanReturnDialog`: unmatched đi qua pending-scans (không gọi create return-note 0 dòng).

---

## 6. Sprint & tiến độ

| Sprint | Phạm vi | Trạng thái |
|---|---|---|
| S1 | BE: model + migration + service khớp + API + cron task + tests | ✅ Xong |
| S2 | FE: 2 dialog quét + khối "Chờ khớp đơn" ở Phiếu kho + regen schema.d.ts + tests | ✅ Xong |

### Ghi chú sau sprint

**S1 (BE):** Model `PendingShipmentScan` + migration `0017_pendingshipmentscan` (app `inventory`, không đụng migration `orders` — tránh va số với 0098 đang treo ở session khác). Service `pending_scan_service.py`: `create_batch` (cách ly lỗi trùng mã qua `IntegrityError` bắt theo tên constraint), `match_pending_scans` (per-row `select_for_update(skip_locked=True)`, mỗi dòng 1 transaction riêng). Endpoint lookup thêm `pending_scan` khi 404. API `pending-scans` (batch/list/retry/delete). Cron `match_pending_shipment_scans` beat 60s + single-flight lock Redis. Đầy đủ test cho các luồng: matched, chưa có đơn (giữ pending), đơn huỷ/lỗi nghiệp vụ (failed), nhiều đơn cùng mã (lấy mới nhất + warning), không xử lý lại matched/failed.

**S2 (FE):** `ScanDispatchDialog` thêm dòng đỏ "Chưa khớp đơn" cho mã 404 (mirror `ScanReturnDialog`); xác nhận xuất kho tách 2 đường matched (dispatch-batch) / unmatched (`POST pending-scans/batch`). `ScanReturnDialog`: dòng unmatched không còn tạo ReturnNote 0 dòng, chuyển sang `pending-scans/batch`. Khối "Chờ khớp đơn" ở `StockDocumentsPage` (badge đếm, danh sách, Thử lại/Xoá) dùng React Query + API module typed `src/lib/api/pending-scans.ts`. `schema.d.ts` đã regen theo endpoint mới.

**Lệch so với spec ban đầu (chủ đích, không phải thiếu sót):**
- **Semantics `matched` khi đơn đã `already_dispatched`:** service không phân biệt "vừa xuất" và "xuất trước đó rồi (idempotent no-op)" ở field `status` — cả hai đều thành `matched`. Tín hiệu no-op nằm ở `OrderService.dispatch_orders_batch` (`order_service_commands.py`) trả `warnings=["already_dispatched"]` dù `ok=True`; `pending_scan_service.py` gom warning này vào field `error` (dùng lại đúng như Q7 dùng `error` cho cảnh báo non-fail, không chỉ lỗi thật). Kho xem cột "Lỗi" sẽ thấy dòng `matched` kèm ghi chú `already_dispatched` — cần lưu ý khi đọc UI, không phải bug.
- **RBAC section:** endpoint `pending-scans` (`pending_scan_views.py`) dùng `require_section("shipments")` — cùng section với lookup/dispatch-batch (đúng theo Q của spec §3.2), **không phải** `inventory` như câu chữ ghi nhầm ở §5 (Test) của bản spec gốc. Đã thống nhất theo code thực tế: `shipments`.
- **`return_type` validation:** field model là `CharField(choices=ReturnType.choices, blank=True, default="")` — `choices` ở model **không tự enforce** trừ khi gọi `full_clean()` (service không gọi). Validation thật sự nằm ở serializer: `CreatePendingScansBatchSerializer.return_type` là `ChoiceField` bắt buộc qua API. Code nào tạo `PendingShipmentScan` trực tiếp (ví dụ test) sẽ bỏ qua check này — chấp nhận được vì API là cổng ghi duy nhất từ FE.
- **`fetchJson` thêm `error.body`:** bổ sung ngoài phạm vi spec §4 ban đầu — `frontend/src/services/http.ts` giờ parse JSON response lỗi (`response.clone().json()`, nuốt lỗi parse nếu không phải JSON) và gắn vào `error.body` cạnh `error.status`. Mục đích: cho phép FE đọc kết quả từng mã trong response lỗi của `POST /api/inventory/pending-scans/batch` thay vì chỉ có message string phẳng.

**Follow-up còn treo (ghi nhận từ review, chưa fix trong sprint này):**
- Cách ly lỗi theo dòng trong `match_pending_scans` mới bọc "lỗi nghiệp vụ" dạng đã biết (per-row transaction + bắt exception) — nhưng `create_batch` khi gặp `IntegrityError` không phải do trùng mã (`uniq_pending_scan_code_kind`) sẽ **raise lại và fail cả lô**, chưa cô lập triệt để mọi loại lỗi ở tầng tạo batch.
- Chưa có test cho luồng `scanned_by=None` (actor rỗng) khi cron tự khớp dispatch — tất cả test hiện tại đều gán `scanned_by=staff_user`.
- Chưa có test riêng cho path "matched nhưng có warning `already_dispatched`" (idempotent no-op) mô tả ở trên — chỉ mới test path `ok=False` (business error → failed).

**Fix sau final review toàn branch (đã vào branch, 152cde1f + fe8a4ff8):**
- Chống tạo TRÙNG phiếu trả: `_complete_return` short-circuit khi đã có ReturnNote (khác cancelled) cùng `shipping_code` — scan thành `matched` trỏ `result_ref` về phiếu sẵn có, không tạo thêm (chặn double-credit khi NV quét tay ngay sau khi đơn sync, cron chạy sau).
- Check trùng lúc quét scope theo `kind`: mã đang pending `dispatch` không còn block quét `return` (và ngược lại) — dialog so `pending_scan.kind` với loại của chính nó.
- `usePendingScans` thêm `{enabled}`: trang gate theo `perm("shipments:view")`, dialog gate theo `open` — user không có section shipments không còn bắn query 403.
- `SoftTimeLimitExceeded` được re-raise trong `_process_one` — timeout của task không còn bị gắn nhầm thành scan `failed`.

**Follow-up sau final review (không chặn merge):**
- `PendingScansDialog` chưa phân trang — badge hiện `total` nhưng list cắt ở limit 50.
- Chưa có retention/purge cho dòng `matched` (bảng lớn dần; theo pattern `audit-purge-old-logs`).
- Dedup phiếu trả hiện là check-then-create (cửa sổ race còn vài ms) — fix bền là mở rộng partial unique constraint `returnnote_shipcode_draft_uniq` (đang chỉ cover `source_order IS NULL`).
- OpenAPI của GET list thiếu khai báo `limit`/`offset` (BE parse tay) — nit schema-fidelity.
