# Phiếu kho: ngày chứng từ = ngày nghiệp vụ (2026-08-15)

## Triệu chứng

Phiếu chuyển kho `CK20260815-0001` (Đại Mỗ 1 → Cửa hàng, 5 × DT11 + 12 × MSC06 + 6 × DC30)
đã ở trạng thái **Đã ghi sổ**, tồn kho đã bị trừ, nhưng **không hiện ở tab Xuất** của trang
Lịch sử nhập xuất khi lọc đến hết ngày 14/08 — đúng ngày ghi trên phiếu.

## Root cause

| Tầng | Thực tế trước fix |
|---|---|
| Form phiếu kho | Người dùng chọn "Ngày tạo phiếu" → FE gửi `created_at` |
| `create_document` | Chỉ ghi vào cột `created_at`; `occurred_at` **luôn NULL** |
| `approve_document` | `record_movement(occurred_at=doc.occurred_at)` → `None` → lấy `now()` |
| Trang Lịch sử nhập xuất | Lọc theo `occurred_at` (`movement_window.filter_occurred_between`) |

Phiếu ghi ngày 14/08 nhưng duyệt ngày 15/08 ⇒ bút toán mang ngày **15/08** ⇒ khung lọc
14/08 không thấy gì. Cột `StockDocument.occurred_at` đã tồn tại từ migration 0029 nhưng
chưa có đường ghi nào từ API — chỉ luồng phiếu tự sinh (mã quét) truyền thẳng vào service.

Sai lệch này ảnh hưởng **mọi loại phiếu kho** (nhập / xuất / điều chỉnh / chuyển kho), không
riêng chuyển kho; chuyển kho chỉ là ca người dùng phát hiện ra.

## Thay đổi

1. **`create_document`**: `occurred_at = occurred_at or created_at`. Caller truyền thẳng
   `occurred_at` (phiếu sinh từ mã quét) vẫn được ưu tiên — mốc quét sát nghiệp vụ hơn.
2. **`update_document`**: sửa ngày trên phiếu nháp kéo theo `occurred_at`; chặn ngày tương lai.
3. **`POST /api/inventory/documents`**: bắt `ValueError` → **400** thay vì 500 (ngày tương lai
   bị chặn ngay lúc tạo, tránh phiếu tạo xong rồi kẹt vì lần duyệt nào cũng nổ).
4. **FE**: nhãn đổi thành "Ngày chứng từ" + chú thích, `max` = hiện tại (không chọn được tương lai).
5. **Migration `0034` + service `stock_document_date_backfill`**: nắn dữ liệu cũ.

## Backfill — ba lằn ranh

- Chỉ bút toán **gốc** (`note = ''`); bút toán đảo lúc huỷ phiếu dùng chung
  `movement_type`/`reference_id`, là sự kiện của **ngày huỷ** nên không nắn.
- Chỉ phiếu **`posted`**; phiếu `cancelled` đang có cặp gốc/đảo cùng ngày (net 0 ở mọi khung),
  nắn nửa cặp sẽ làm phiếu đã huỷ "sống lại" ở ngày chứng từ.
- Chỉ nắn **lùi** (`d.created_at < m.occurred_at` và `<= m.created_at`): giữ bất biến
  `occurred_at <= created_at` (nền của partition pruning) và khiến thao tác idempotent.

Chia lô 50k id trên `stock_movements`, cùng khuôn `occurred_at_backfill` (0027).
`occurred_at` không phải partition key nên UPDATE không làm dòng nhảy partition.

## Ảnh hưởng báo cáo sau khi chạy migration

Số liệu **chuyển chỗ, không sinh thêm**: phiếu kho từng bị tính vào ngày duyệt nay về ngày
chứng từ. Ngày duyệt sẽ giảm đúng bằng phần ngày chứng từ tăng. Chỉ các phiếu có
`created_at` lệch ngày duyệt mới đổi; phiếu lập và duyệt trong ngày không đổi gì.

## Test

| File | Phủ |
|---|---|
| `test_occurred_at.py` | ngày lùi → bút toán mang ngày chứng từ; `occurred_at` tường minh vẫn thắng; sửa nháp kéo theo ngày; phiếu chuyển ghi lùi hiện ở tab Xuất đúng ngày |
| `test_stock_document_api.py` | ngày tương lai → 400 |
| `test_stock_document_date_backfill.py` | nắn cả 2 nhánh chuyển kho; lấp `occurred_at` phiếu chưa duyệt; không đụng bút toán đảo; bỏ qua phiếu đã huỷ; idempotent; giữ bất biến |
| `StockDocumentsPage.lines.test.tsx` | ô ngày chứng từ chặn tương lai |

## Runbook deploy

1. Deploy code (migration `0034` chạy cùng nhịp — an toàn với code cũ vì chỉ ghi `occurred_at`).
2. Migration là RunPython `atomic = False`, chia lô — chạy được lúc có tải, nhưng nên chọn giờ thấp điểm.
3. Kiểm chứng: mở Lịch sử nhập xuất → tab Xuất, lọc đúng ngày trên phiếu `CK20260815-0001`
   (14/08) → phải thấy dòng "Phiếu chuyển kho CK20260815-0001" và footer có phần "chuyển kho".
