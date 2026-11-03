# Bút toán kho mang ngày nghiệp vụ, không mang ngày khớp đơn

Ngày: 2026-07-22
Nhánh: `fix/scan-match-business-date`

## 1. Vấn đề

Kho quét mã vận đơn vào bảng chờ (`PendingShipmentScan`). Cron 60s dò đơn đã sync
về rồi ghi sổ kho. Khoảng cách giữa **lúc quét** và **lúc khớp** có thể là vài
phút (quét sát nửa đêm) hoặc vài ngày (đơn KiotViet về muộn).

Nhánh **hoàn** đã xử lý đúng: phiếu ngày lấy `doc_date = ngày quét`
(`pending_scan_service._complete_return`), và lịch sử Nhập hoàn lọc theo
`return_note__doc_date`.

Nhánh **xuất** thì sai. `_complete_dispatch` → `dispatch_orders_batch` →
`issue_for_order` → `StockMovement` với `created_at = auto_now_add`, tức thời
điểm cron chạy. Tab Xuất (`DispatchHistoryService`) lọc và sắp xếp đúng theo
`created_at`. Hệ quả: mã quét ngày 20, đơn về ngày 22 → toàn bộ sản lượng xuất
nhảy sang ngày 22. Số liệu ngày không dùng được để đối chiếu với kho.

Bút toán của nhánh hoàn tuy nằm đúng phiếu ngày nhưng bản thân `StockMovement`
vẫn mang giờ cron — mọi báo cáo đọc thẳng ledger (vd. tốc độ bán 3d/7d) đều lệch
theo. Vì vậy vấn đề được xử lý ở đúng một chỗ: bản thân bút toán kho.

Mã quét không khớp đơn nào còn được thử khớp với hành trình bảo hành, và ở đó
`warehouse_received_at` cũng đang lấy ngày cron chạy — cùng lớp lỗi, xử lý luôn
(§8).

## 2. Quyết định

Tách **ngày nghiệp vụ** khỏi **ngày ghi sổ** bằng một cột mới trên
`StockMovement`.

| Cột | Nghĩa | Dùng cho |
|---|---|---|
| `created_at` | Lúc hệ thống ghi sổ | Audit, **partition key** |
| `occurred_at` | Lúc hàng thực sự rời/về kho | Mọi báo cáo theo ngày |

Hai phương án bị loại:

- **Ghi đè thẳng `created_at`**: rẻ nhất nhưng `created_at` là partition key của
  `stock_movements` (RANGE theo tháng — xem `PartitionService`), ghi lùi ngày sẽ
  đẩy dòng sang partition khác so với thứ tự ghi thật; đồng thời mất dấu vết
  "ghi sổ lúc nào" và làm `balance_after` không còn đơn điệu theo thứ tự thời
  gian khi đọc thẻ kho.
- **Sửa ở tầng đọc** (join sang `PendingShipmentScan` để lấy ngày quét): phải
  lặp subquery ở mọi điểm đọc (lọc, sort, tổng, drill-down, export), không dùng
  được index, và không phủ được các luồng ghi lùi ngày khác trong tương lai.

## 3. Bất biến

```
occurred_at <= created_at
```

Không luồng nào được ghi ngày nghiệp vụ ở tương lai. Bất biến này là cơ sở cho
tối ưu partition pruning ở §6 — mọi thay đổi sau này phải giữ nó.

## 4. Đường ghi

`StockCommandService.record_movement` nhận thêm `occurred_at: datetime | None`,
mặc định `timezone.now()`. Không truyền ⇒ hành vi y hệt hiện tại.

Tham số được luồn xuyên suốt hai nhánh khớp mã:

```
PendingScanService._complete_dispatch(occurred_at=scan.created_at)
  → OrderService.dispatch_orders_batch(occurred_at=...)
    → OrderServiceCommands.update_order_inline(occurred_at=...)
      → StockSalesService.issue_for_order(occurred_at=...)
        → StockCommandService.record_movement(occurred_at=...)

PendingScanService._complete_return(occurred_at=scan.created_at)
  → ReturnEntryService.create_and_post_entry(occurred_at=...)
    → StockCommandService.record_movement(occurred_at=...)
```

Áp cho **cả cron tự khớp lẫn khớp tay**: `manual_match` đi chung
`_dispatch_and_finalize` / `_complete_return` nên không cần đường riêng.

Mọi caller khác của `record_movement` (phiếu kho tay, chuyển kho, điều chỉnh,
reclass, sửa nhóm đơn hoàn) không truyền tham số ⇒ `occurred_at = created_at`.

## 5. Đường đọc

Đổi `created_at` → `occurred_at`:

- `DispatchHistoryService.base_queryset` (lọc ngày) và `_dispatch_last_at()`
  (cột "ngày xuất" + thứ tự sắp xếp). Tab Xuất, tile tổng hợp
  (`FlowSummaryService`), drill-down theo SKU và export đều dùng chung
  `base_queryset` nên đúng theo mà không cần sửa riêng.
- `IssueDocumentHistoryService`, `TransferDocumentHistoryService`: hai luồng này
  luôn có `occurred_at == created_at`, đổi để trong codebase chỉ còn **một luật
  duy nhất** về "ngày của bút toán".
- `StockQueryService` (tốc độ bán 3d/7d): tính theo ngày hàng rời kho.

Giữ nguyên:

- Lịch sử Nhập hoàn (`ReturnHistoryService`) vẫn lọc theo
  `return_note__doc_date` — đã đúng sẵn, và phiếu ngày mới là chứng từ của
  nghiệp vụ hoàn.
- Danh sách mã chờ khớp (`PendingScanService.list_unmatched`) vẫn lọc theo
  `PendingShipmentScan.created_at` — đó chính là ngày quét.
- `ledger_views.ledger_documents` lọc trên `StockDocument`, không liên quan.

## 6. Partition pruning

`stock_movements` là bảng partition RANGE theo tháng trên `created_at`. Lọc
thuần theo `occurred_at` sẽ quét mọi partition.

Nhờ bất biến `occurred_at <= created_at`, một query lọc `occurred_at >=
date_from` vẫn cho **đúng kết quả** khi kèm thêm `created_at >= date_from` —
mọi dòng bị loại thêm đều có `created_at < date_from`, kéo theo `occurred_at <
date_from`, tức đằng nào cũng bị loại. Điều kiện phụ này cắt sạch các partition
cũ hơn kỳ lọc.

Luật được gói trong **một** helper dùng chung (`filter_occurred_between`) để
không bị chép rời rạc rồi lệch nhau. Cận trên không thêm được điều kiện tương
ứng (`created_at` có thể lớn hơn `date_to`), nhưng các partition mới hơn kỳ lọc
chỉ là vài tháng gần nhất nên chi phí không đáng kể.

Index mới trên bảng cha (Postgres tự lan xuống mọi partition hiện có và tương
lai): `(reference_type, movement_type, occurred_at)`.

## 7. Migration

Ba bước, để không khoá bảng lâu:

1. `AddField occurred_at DateTimeField(null=True)` — thao tác metadata, không
   rewrite bảng.
2. Data migration backfill theo lô (mặc định `occurred_at = created_at`), lặp
   theo khoảng id để mỗi statement ngắn; chạy được lại từ đầu nếu đứt.
3. `ALTER ... SET DEFAULT now()` + `SET NOT NULL` + tạo index.

**Nắn lại dữ liệu lịch sử** (đã chốt: nắn toàn bộ, không giới hạn kỳ). Sau bước
2, với mọi `PendingShipmentScan` ở trạng thái `matched`:

- `kind = dispatch` → các bút toán `sale` có `reference_type='order'` và
  `reference_id = scan.matched_order_id`: `occurred_at = scan.created_at`.
- `kind = return` → các bút toán của `ReturnEntry` khớp `shipping_code =
  scan.code`: `occurred_at = scan.created_at`.

Chỉ nắn khi `scan.created_at < movement.created_at` để giữ bất biến §3. Bút toán
`sale_reversal` **không** nắn — đảo sổ là nghiệp vụ xảy ra tại thời điểm đảo,
không phải tại thời điểm quét.

Hệ quả cần báo trước cho vận hành: báo cáo của các ngày đã qua sẽ đổi số so với
ảnh chụp trước đó. Đó chính là mục tiêu của thay đổi này.

## 8. Hành trình bảo hành

Cùng lớp vấn đề, nằm trong phạm vi. Mã quét không khớp `Order` nào sẽ được thử
khớp với `WarrantyJourney` (`PendingScanService._match_warranty_journey*`).
Nhánh HOÀN gọi `WarrantyJourneyService.mark_returned_by_tracking`, trong đó
`warehouse_received_at = timezone.localdate()` — tức ngày cron chạy, không phải
ngày kho nhận hàng.

Chỉ trường này bị đóng dấu ngày: `mark_packed_by_tracking` chỉ nâng trạng thái
lên `packed`, không ghi ngày nào, nên không cần đụng.

Sửa: `mark_returned_by_tracking(code, received_at=None)` — mặc định
`timezone.localdate()` (giữ nguyên cho mọi caller khác), luồng quét truyền
`received_at = timezone.localtime(scan.created_at).date()`. Giữ nguyên guard
hiện có "chỉ set khi đang trống" để không đè giá trị người dùng đã tự nhập.

**Backfill.** `warehouse_received_at` là trường người dùng sửa được
(`JOURNEY_EDITABLE_FIELDS`) nên không được nắn mù. Chỉ nắn khi giá trị hiện tại
vẫn đúng bằng thứ cron đã ghi, tức với mọi `PendingShipmentScan` có
`kind=return`, `status=matched`, `result_ref` dạng `warranty-journey:<id>`:

```
nắn khi  journey.warehouse_received_at == localdate(scan.updated_at)
     và  journey.warehouse_received_at != localdate(scan.created_at)
→ journey.warehouse_received_at = localdate(scan.created_at)
```

`scan.updated_at` là thời điểm bản ghi chuyển sang `matched` (bút toán không bị
đụng sau đó — xem `list_unmatched` đang dùng chính giả định này cho
`matched_at`). Giá trị đã bị người dùng sửa tay sẽ không khớp vế đầu ⇒ bỏ qua.

## 9. Kiểm thử

Viết test trước (TDD). Các ca chính:

1. **Cron xuất lệch ngày**: tạo scan `dispatch` ở ngày D, đơn về, chạy
   `match_pending_scans` ở ngày D+2 → `movement.occurred_at.date() == D` và
   `movement.created_at.date() == D+2`.
2. **Cron hoàn lệch ngày**: tương tự cho `kind=return`; kèm khẳng định phiếu
   ngày vẫn là `doc_date == D` (không hồi quy hành vi đang đúng).
3. **Khớp tay**: `manual_match` ở D+2 cho scan ngày D → cùng kết quả ca 1.
4. **Tab Xuất**: đơn ở ca 1 xuất hiện khi lọc `date_from=date_to=D`, không xuất
   hiện khi lọc D+2; `last_at` trả ngày D.
5. **Luồng không phải khớp mã**: xuất kho tay / phiếu kho → `occurred_at` xấp xỉ
   `created_at` (hành vi không đổi).
6. **Migration backfill**: dựng bút toán lệch ngày bằng dữ liệu thô rồi chạy hàm
   backfill, khẳng định nắn đúng và `sale_reversal` không bị đụng.
7. **Bất biến**: không sinh ra dòng nào có `occurred_at > created_at`.
8. **Bảo hành**: scan hoàn ngày D khớp journey ở ngày D+2 →
   `warehouse_received_at == D`; journey đã có sẵn ngày thì không bị đè.
9. **Backfill bảo hành**: journey có ngày đúng bằng ngày cron ghi → được nắn;
   journey có ngày do người dùng tự nhập → giữ nguyên.

## 10. Ngoài phạm vi

- Frontend không đổi: BE giữ nguyên tên trường trả về (`last_at`).
- Không thêm trường "ngày đóng hàng" cho `mark_packed_by_tracking` — hiện không
  có trường nào như vậy và không ai đang cần.
