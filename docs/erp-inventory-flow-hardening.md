# Inventory Flow Hardening — Rà soát & vá logic toàn luồng kho

- **Ngày:** 2026-07-03
- **Branch:** `fix/inventory-flow-hardening`
- **Phạm vi:** `apps/inventory` + điểm giao thoa `apps/orders` (dispatch/release), RBAC `apps/users`

## 1. Bối cảnh

Audit toàn bộ luồng kho (ledger → phiếu kho → trả hàng → reclass → bán hàng/giao vận
→ partition) phát hiện 3 nhóm vấn đề: **sai tồn thật** (double-credit, re-issue bị nuốt,
items lệch sau xuất), **race condition** (idempotency không lock, sinh mã trùng,
TOCTOU phiếu điều chỉnh), và **lỗ hổng kiểm soát** (disposal/hủy phiếu trả bypass
cổng duyệt kế toán, thiếu validation).

## 2. Thiết kế fix chính: net-outstanding + advisory lock theo đơn

Nguyên tắc mới cho tồn-theo-đơn (`StockSalesService`):

- **Outstanding** = `-(SUM qty_delta của SALE + SALE_REVERSAL)` theo (kho, sản phẩm).
  Mọi quyết định issue/release dựa trên số NET này thay vì `exists()` — nhờ đó chu kỳ
  xuất → hủy → mở lại → xuất lại hoạt động đúng, và release idempotent tự nhiên.
- **Return credit**: 1 đơn vị vật lý quay về kho (ReturnNote posted, mọi return_type)
  chỉ được cộng vào tồn đúng 1 lần. `release_for_order` trừ credit này trước khi tạo
  SALE_REVERSAL → hết cảnh "quét nhập hoàn xong sale hủy đơn → cộng tồn 2 lần".
  Chiều ngược lại, `post_return` chặn khi đơn nguồn đã được hoàn đủ (guard theo
  outstanding − returned).
- **Advisory lock** `pg_advisory_xact_lock(61001, order_id)` serialize mọi thao tác
  đọc-rồi-ghi movement của 1 đơn (issue / release / resync / post-return / cancel-return)
  → 2 request đồng thời không thể trừ/cộng tồn 2 lần.
- **`resync_for_order`** (mới): khi items của đơn ĐÃ xuất kho bị sửa, tồn được đồng bộ
  lại theo items mới (giảm → hoàn phần chênh có tôn trọng return credit; tăng → trừ thêm).
- **Un-dispatch**: đổi `delivery_business_status_code` từ `dispatched` về
  `warehouse_processing`/rỗng → tự hoàn outstanding (sửa quét nhầm không cần hủy đơn).
  Chuyển sang `received`/các trạng thái sau giao KHÔNG hoàn.

## 3. Danh sách thay đổi

| # | Vấn đề | Fix | File |
|---|--------|-----|------|
| 1 | Double-credit: return đã post + hủy đơn auto-release | release trừ return credit; post_return guard chiều ngược | `stock_sales_service.py`, `return_note_service.py` |
| 2 | Re-issue sau release bị nuốt im lặng | guard theo net outstanding thay vì `exists()` | `stock_sales_service.py` |
| 3 | Sửa items sau xuất không đồng bộ tồn; không có un-dispatch | `resync_for_order` + nhánh un-dispatch trong `update_order_inline` | `order_service_commands.py` |
| 4 | Race check-then-insert issue/release | advisory xact lock theo order_id | `stock_sales_service.py` |
| 5 | Sinh mã phiếu bằng `count()` → trùng code 500 | retry-on-IntegrityError (5 lần, bump suffix) | `stock_document_service.py`, `return_note_service.py` |
| 6 | ADJUSTMENT đọc tồn không lock (TOCTOU) | ensure-row + `select_for_update` trước khi tính delta | `stock_document_service.py` |
| 7 | Reclass scrap/send_back vượt tồn âm im lặng | lock row + check đủ tồn, thiếu → ValueError 400 | `stock_reclass_service.py` |
| 8 | Reclass `reference_id=product_id` không truy vết | model `StockReclassLog` (migration 0009), movement trỏ log.id | `models.py`, `stock_reclass_service.py` |
| 9 | Disposal bypass cổng duyệt kế toán | scrap/send_back cần `stock-documents:approve` | `stock_views.py` |
| 10 | Hủy phiếu trả posted không cần duyệt (bất đối xứng với phiếu kho) | gate `returns:approve` + migration cấp parity cho role có `stock-documents:approve` | `return_views.py`, users migration 0023 |
| 11 | Trùng sản phẩm trong lines → IntegrityError 500 | validate duplicate ở serializer → 400 | 2 serializer files |
| 12 | Hủy phiếu kho không truyền state; `rejected_reason` dính lại sau resubmit | pass `state=mv.state`; clear reason khi submit | `stock_document_service.py` |
| 13 | `rebuild_stock_items` chạy song song ghi movement → số dư lệch | `LOCK TABLE stock_movements IN SHARE MODE` | `rebuild_stock_items.py` |

## 4. Quyết định policy (cần CTO/kế toán xác nhận, đã chọn mặc định an toàn)

- **Post phiếu trả** vẫn chỉ cần section `returns` (luồng quét cần nhanh) — KHÔNG đổi.
- **Hủy phiếu trả posted** giờ cần `returns:approve`. Migration 0023 tự cấp quyền này
  cho mọi role đang có `stock-documents:approve` (nhóm kế toán không bị mất quyền).
- **Reclass `to_sellable`** vẫn chỉ cần section `stock` (không xuất hàng khỏi tồn);
  chỉ `scrap`/`send_back` cần quyền duyệt.
- **Cancel return sau khi đơn nguồn đã release**: cho phép (kế toán tự quyết), nhưng
  giữ advisory lock để không race với release.

## 5. Vận hành / lưu ý

- Migration mới: `inventory.0009_stockreclasslog` (bảng mới, không đụng dữ liệu),
  `users.0023_grant_returns_approve_parity` (data migration, reversible).
- Partition self-heal (`ensure_month_partition`) giữ nguyên; lưu ý vận hành: nhánh
  DETACH lấy ACCESS EXCLUSIVE trên `stock_movements` — beat nên chạy giờ thấp điểm.
- `rebuild_stock_items` giờ block ghi movement trong lúc chạy — chỉ chạy lúc thấp điểm.
- Ranh giới partition theo UTC (lệch 7h vs VN) — nhất quán nội bộ, chỉ cần nhớ khi
  query per-partition thủ công.

## 6. Tests

- `apps/inventory/tests/test_stock_sales_cycle.py` — 14 test vòng đời issue/release/
  resync/return-guard (net-outstanding, credit, chặn double).
- `apps/inventory/tests/test_reclass_service.py` — check tồn, log truy vết, RBAC disposal.
- `apps/inventory/tests/test_document_hardening.py` — duplicate lines 400, rejected_reason
  reset, RBAC hủy phiếu trả, hủy phiếu nhập hoàn đúng state.
- `apps/orders/tests/test_dispatch_stock_cycle.py` — integration: un-dispatch, re-dispatch,
  sửa items sau xuất, hủy-sau-hoàn không double credit.
- Toàn bộ suite inventory (104) + orders/users pass; ruff sạch trên các file thay đổi.

## 7. Tiến độ

- [x] Sprint duy nhất: audit → fix 13 hạng mục → tests → docs (2026-07-03, hoàn thành).
- [ ] Theo dõi sau deploy: cảnh báo `Tồn âm` (log warning) và tần suất trùng mã phiếu
  (retry log) tuần đầu.
- [ ] Chưa làm (đề xuất tương lai): movement_type riêng cho bút toán đảo (hiện dùng
  cùng type với delta ngược — net theo type vẫn đúng); UI hiển thị StockReclassLog.
