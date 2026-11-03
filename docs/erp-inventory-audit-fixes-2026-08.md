# Inventory Audit Fixes — Vá các phát hiện audit toàn phần kho 2026-08-03

- **Ngày:** 2026-08-03
- **Branch:** `fix/inventory-audit-hardening`
- **Nguồn:** audit 4 nhóm (ledger lõi, trả hàng, dispatch/quét, lịch sử/báo cáo).
  Chi tiết từng phát hiện nằm trong báo cáo audit phiên 2026-08-03.

## Phạm vi & thiết kế fix

### P0 — có thể sai tồn thật

| # | Vấn đề | Fix |
|---|--------|-----|
| P0.1 | Race 2 request `create_and_post_entry` cùng `shipping_code`, KHÔNG có đơn nguồn → `RETURN_IN` ghi 2 lần (guard thoát sớm khi `source_order_id=None`; constraint `returnentry_shipcode_draft_uniq` chỉ phủ `status=draft`) | Advisory xact lock theo `hashtext(shipping_code)` (class 61002) lấy TRƯỚC bước check trùng trong `create_entry` — serialize mọi đường tạo entry cùng mã; request sau thấy row posted của request trước → 400 như luồng tuần tự |
| P0.2 | Sibling `.NN` về TRỄ sau khi xử lý huỷ: không gì chuyển ledger sang sibling (transfer chỉ chạy 1 lần tại thời điểm huỷ); sweep `find_leaks` khoá theo `CANCEL_RELEASE_NOTE` — note chết từ 2026-07-29 → sweep mù; quét hoàn khớp sibling fail-open → double credit | (a) Sweep thêm dạng leak mới: đơn HUỶ còn outstanding + sibling sống chưa có SALE ledger → `transfer_issue_to_sibling`; (b) `_guard_double_credit` đóng fail-open cho sibling: đơn không có SALE nhưng là sibling của đơn huỷ còn outstanding → chặn với thông báo chạy repair |
| P0.3 | Manual match đua cron trong cửa sổ HTTP verify → 2 đơn khác nhau cùng bị trừ kho từ 1 lần quét (dispatch commit trước finalize re-check) | Re-claim scan bằng cột `claimed_at` set trong txn #1 (điều kiện `claimed_at IS NULL` hoặc quá hạn 10'); đường kia thấy claim còn hạn → bỏ qua/400. Finalize giải phóng claim khi thất bại |

### P1 — vận hành/an toàn

| # | Vấn đề | Fix |
|---|--------|-----|
| P1.1 | Detector `find_unreissued` thiếu `AUTO_RELEASE_RECLAIM_NOTE` → cảnh báo giả vĩnh viễn với đơn legacy đã reclaim | Thêm SALE `AUTO_RELEASE_RECLAIM_NOTE` vào công thức net — đồng nhất với `pending_cancel_release_by_key` |
| P1.2 | Duyệt 2 phiếu transfer ngược chiều đồng thời → deadlock (khoá src→dst per-line) | `approve_document` gom mọi op `(warehouse, product, delta)` của phiếu rồi ghi theo thứ tự toàn cục `(warehouse_id, product_id)` — cùng luật với `cancel_document` |
| P1.3 | Watch "chờ hàng về" kẹt: `open_for_order` không reset RETURNED→WAITING khi cancel entry; `adjust_entry` không settle/reopen | `open_for_order` reset RETURNED→WAITING; thêm `refresh_for_order` (WAITING↔RETURNED theo owed hiện tại) gọi từ `adjust_entry` |

### P2 — báo cáo & hạ tầng

| # | Vấn đề | Fix |
|---|--------|-----|
| P2.1 | `MergedIssueHistory.orders_page/skus_page` đếm total gồm đơn/SKU chỉ-có-reversal → total ≠ số dòng | Áp `_HAS_REAL_SALE` vào count như đã fix ở `DispatchHistoryService.orders_page` |
| P2.2 | Tile "ĐƠN ĐÃ XUẤT" ≠ bảng khi un-dispatch cùng kỳ | Tile đếm cùng predicate với bảng (nhóm có SALE thật trong kỳ) |
| P2.3 | Excel export cắt lặng lẽ 5000 dòng, footer aggregate toàn kỳ | Khi chạm trần: chèn dòng cảnh báo rõ số dòng bị cắt |
| P2.4 | `stock_movements` partitioned mất toàn bộ FK tầng DB (migration 0008) | Migration mới `ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID` + `VALIDATE CONSTRAINT` (không khoá dài); index `created_by_id` |

### Robustness nhỏ

- Dòng trùng `product_id` trong payload phiếu kho / phiếu trả → 400 (hiện IntegrityError 500).
- `StockDocumentLine`: CheckConstraint `quantity >= 1` (đồng bộ với `ReturnNoteLine`).
- `create_document` chặn `occurred_at` tương lai ngay lúc tạo (hiện kẹt lúc duyệt).
- Cảnh báo tồn âm chỉ bắn khi CHUYỂN sang âm (old ≥ 0 → new < 0), hết spam Lark.
- `purge_matched_pending_scans` giữ lại scan còn cờ `needs_replacement_issue`.
- `manual_match` chấp nhận scan CANCELLED (hết ngõ cụt khi cron auto-pick nhầm đơn huỷ).

### Ghi nhận KHÔNG sửa đợt này (có chủ đích)

- Hiển thị gross cho đơn đã chuyển sổ sibling (by design, đã document).
- Reversal note rỗng đời cũ không phân biệt được huỷ/un-dispatch — hạn chế dữ liệu lịch sử.
- Run-rate làm tròn 2 lần BE→FE (cosmetic, chờ chốt hợp đồng hiển thị).
- Sinh mã phiếu `count()` + retry 5 lần (đã có trần an toàn).
- `rebuild_stock_items` giữ SHARE lock — vận hành giờ thấp điểm (đã document).

## Tiến độ

- [x] P0.1 race trả ngoài — advisory lock 61002 theo `hashtext(shipping_code)`; test concurrency 2 thread.
- [x] P0.2 sweep sibling + fail-open — `find_outstanding_leaks` + guard tự `transfer_issue_to_sibling` khi quét trúng sibling chưa có sổ.
- [x] P0.3 claim scan — cột `claimed_at` (migration 0031), TTL 10'; cron/manual từ chối khi vé còn hạn; finalize giải phóng.
- [x] P1.1 detector/fixer — thêm `AUTO_RELEASE_RECLAIM_NOTE` vào công thức net.
- [x] P1.2 lock ordering transfer — approve trải phẳng cặp bút toán, sort toàn cục `(warehouse_id, product_id)`.
- [x] P1.3 watch chờ hàng về — `refresh_for_order` (WAITING↔RETURNED theo sổ); gọi từ cancel/adjust; `open_for_order` reset nhãn cũ.
- [x] P2.1–P2.3 báo cáo — total merged đếm trên tập đã lọc; loại dòng un-dispatch-trong-kỳ (`issued=0` và không có SALE hành chính); Excel chèn dòng cảnh báo khi cắt 5000.
- [x] P2.4 FK migration — 0032 (ADD thẳng, PG không hỗ trợ NOT VALID trên bảng partitioned — CHẠY GIỜ THẤP ĐIỂM) + index `created_by_id`.
- [x] Robustness nhỏ — dup lines 400 (return build_lines), CheckConstraint qty>=1 (0033), chặn `occurred_at` tương lai lúc tạo phiếu, alert Lark theo `crossed_negative`, purge giữ scan còn cờ phiếu bù, manual match nhận scan CANCELLED.
- [x] Full suite inventory + warehouses + orders/kiotviet liên quan: 604 passed (2026-08-03); ruff sạch

## Lưu ý deploy

- Migration 0032 scan toàn bộ `stock_movements` khi thêm FK — chạy migrate giờ thấp điểm.
- Nếu 0032 fail ở bước ADD CONSTRAINT: dữ liệu đang có movement mồ côi, xử lý dữ liệu trước rồi migrate lại.
