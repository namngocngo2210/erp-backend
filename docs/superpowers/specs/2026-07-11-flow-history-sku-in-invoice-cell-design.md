# Lịch sử nhập xuất — hiện SKU + số lượng trong cột mã hóa đơn

Ngày: 2026-07-11 · Branch: `feat/flow-history-sku-in-invoice-cell`

## Vấn đề

Trang **Lịch sử nhập xuất** (`/inventory/dispatch-history`) ở kiểu xem "Theo đơn" chỉ hiện mã hóa đơn,
số SKU và tổng số lượng. Muốn biết đơn gồm SKU nào, mỗi SKU bao nhiêu, phải bấm vào từng dòng để mở
drill-down. Người dùng cần thấy ngay danh sách SKU × SL trong cell mã hóa đơn.

## Phạm vi

| Vị trí | Áp dụng |
| --- | --- |
| Tab **Xuất hàng ra** — xem theo đơn, cột "Mã hóa đơn" | Có |
| Tab **Nhập hàng hoàn về** — xem theo đơn, cột "Mã hóa đơn nguồn" | Có |
| Kiểu xem "Theo SKU" (cả 2 tab) | Không |
| Dòng "chờ khớp" (PendingShipmentScan) | Không — chưa khớp đơn nên không có SKU |
| Excel export | Không |

Drill-down giữ nguyên (vẫn có tên sản phẩm và số liệu dispatched/reversed chi tiết).

## Hiển thị

Mã hóa đơn ở dòng trên (`font-medium`), bên dưới liệt kê **đầy đủ** SKU × SL, chữ nhỏ, màu
`text-muted-foreground`:

```
Mã hóa đơn
──────────────────
HD00123
  DESI-A01 × 2
  DESI-B07 × 1
  DESI-C12 × 3
```

Không giới hạn số SKU hiển thị (đơn nhiều SKU thì dòng cao lên — chấp nhận). Cell bỏ
`whitespace-nowrap` để xuống dòng được.

## Backend

Nguyên tắc: **trả kèm `lines` trong payload danh sách**, không để FE gọi thêm endpoint (sẽ thành N+1
request). Cả hai service list đã sẵn pattern "lấy id của trang → 1 query gộp" (`wh_names`, `qty_map`)
nên chi phí thêm tối đa 1 query/trang.

### `DispatchHistoryService.orders_page`

Sau khi có `order_ids` của trang, thêm 1 query gộp trên chính `base_queryset(**filters)`:

```
.filter(reference_id__in=order_ids)
.values("reference_id", "product_id", "product__sku")
.annotate(sale_sum=..., rev_sum=...)
```

Dựng `lines_map[order_id] = [{product_id, sku, qty}]` với `qty = dispatched − reversed` (**net**, đúng
nguyên tắc net của tab Xuất), sắp xếp SL giảm dần. Mỗi item của `items` thêm khóa `lines`.

### `ReturnHistoryService.list_page`

Nâng cấp query `qty_map` hiện có thành gộp theo `(return_note_id, product_id, product__sku)` —
từ cùng một result set dựng cả `qty_total` (như cũ) lẫn `lines: [{product_id, sku, qty}]`. Không tốn
thêm query.

### Serializer (`flow_history_views.py`)

Thêm `_FLOW_LINE_BRIEF = {"product_id": Integer, "sku": Char(allow_null), "qty": Integer}`, nhúng
`many=True` vào `_ISSUE_ORDER_ITEM` và item của return list → FE có type qua `npm run gen:api`.

### Bộ lọc

Hai tab khác nhau, đều đồng nhất với drill-down của chính tab đó:

- Tab **Xuất**: bộ lọc sản phẩm lọc ở mức *movement* (`base_queryset`), nên cũng lọc luôn `lines`
  — gõ 1 SKU thì cell chỉ hiện SKU đó, giống drill-down `order_lines`.
- Tab **Nhập hoàn**: bộ lọc sản phẩm lọc ở mức *phiếu* (`Exists` trong `_base` — phiếu có ít nhất
  1 dòng khớp SKU thì cả phiếu được giữ), nên `lines` vẫn hiện **đầy đủ** mọi SKU của phiếu, không
  bị narrow theo SKU đang lọc — giống hệt drill-down `ReturnHistoryService.lines()`, vốn cũng trả
  toàn bộ dòng của phiếu bất kể bộ lọc. Đây là chủ đích, không phải bug.

## Frontend

- Component dùng chung `SkuLines` trong `features/inventory/components/flow-shared.tsx`:
  nhận `code: string | null` và `lines: FlowLineBrief[]`, render mã + danh sách `SKU × SL`.
- `IssueTab.tsx` (nhánh view "order"): cell mã hóa đơn → `<SkuLines code={invoice_code ?? order_code} lines={r.lines} />`.
- `ReturnTab.tsx` (nhánh view "order"): cell mã hóa đơn nguồn → tương tự.
- Type lấy từ `src/api/schema.d.ts` (chạy `gen:api`), không khai báo tay.

## Test

- BE `test_dispatch_history_service.py`: đơn 2 SKU + 1 reversal → `lines` có SKU đúng, `qty` là **net**.
- BE `test_return_history_service.py`: phiếu trả 2 dòng → `lines` đúng SKU/SL, `qty_total` không đổi.
- FE: render test `SkuLines` (có/không có lines).

## Không làm (YAGNI)

- Không cap/"+N SKU khác", không tooltip.
- Không đổi Excel export, không đổi kiểu xem "Theo SKU", không rename route/component.
