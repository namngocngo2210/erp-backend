# Báo cáo Sale — Nhóm 10: Đơn chốt, đơn hoàn & doanh thu theo Sale × Nguồn đơn

- Ngày: 2026-08-04
- Nhánh: `feat/sale-source-closed-revenue`
- Trạng thái: đã chốt thiết kế, chờ triển khai

## 1. Bối cảnh

Tab "Tổng quan" của trang Báo cáo Sale (`/dashboard`) hiện có 9 nhóm. Nhóm 9
("Data chia & doanh thu chốt theo Sale") trả lời "sale chốt được bao nhiêu tiền",
nhưng chỉ ở mức **một con số cho cả sale** và **chỉ ở thời điểm chốt** — không
biết tiền đó đến từ nguồn lead nào, cũng không biết bao nhiêu rơi lại vì đơn bị
hoàn.

Hai câu hỏi điều hành chưa trả lời được:

1. Nguồn lead nào (landing / zalo / messenger / hotline) mang lại tiền
   thật cho từng sale?
2. Nguồn nào chốt được nhiều nhưng hoàn nhiều — tức tiền chốt là ảo?

## 2. Yêu cầu

Thêm một khối ở **cuối tab Tổng quan** (sau Nhóm 9), bảng hai chiều:

- **Hàng** = từng nhân viên sale.
- **Cột** = từng `order_source`, mỗi nguồn 4 chỉ số.
- Nguồn trong phạm vi: `landing`, `zalo`, `messenger`, `hotline`.
  **Loại** `tiktok`, `shopee`, `koc`, `kol` và cả đơn không có nguồn.
  (`kol` là tên cũ của `koc` — form không cho chọn nữa, chỉ còn ở đơn cũ; giữ
  lại sẽ mâu thuẫn với việc đã loại `koc`. User xác nhận 2026-08-04.)

4 chỉ số cho mỗi ô (sale, nguồn) — user chốt lại 2026-08-04:

1. **Số đơn chốt** — đơn ở trạng thái sale đã chốt + đã lên đơn
2. **Tổng doanh thu** — tiền của chính các đơn ở cột 1
3. **Số đơn hoàn** — trong số đơn ở cột 1, đơn rơi vào trạng thái vận chuyển hoàn
4. **Doanh thu giao thành công** — cột 2 trừ tiền của các đơn hoàn ở cột 3

Bản đầu tiên làm cột 3/4 là "phần còn lại sau khi trừ hoàn"; user đổi cột 3
thành chính phần bị hoàn để nhìn được độ rơi rụng, cột 4 giữ nguyên giá trị.

## 3. Định nghĩa số liệu

### Phạm vi đơn

Kế thừa nguyên phạm vi báo cáo Sale (`OrderRepository._sales_report_orders_queryset`,
`backend/apps/orders/repositories.py:197`):

- `channel_id ∈ sale_report_channel_ids()` — kênh có gán NV phòng Ads.
- `staff.department_id = SALES`, `staff.deleted_at IS NULL`.
- Đơn huỷ (`status = 6` hoặc `invoice_status = 2`) đã bị `_base_orders_queryset()`
  loại sẵn — không thêm điều kiện.
- Lọc kỳ theo `created_at`, cùng mốc với nhóm 1–9.

Thêm **đúng một** điều kiện của riêng Nhóm 10:

```python
order_source__in=SALE_SOURCE_REPORT_ORDER_SOURCES  # 4 nguồn kể trên
```

Điều kiện này tự loại luôn `order_source` NULL/rỗng — user chốt 2026-08-04:
đơn không có nguồn không xếp được vào cột nào nên không hiện trong bảng.

### Công thức 4 chỉ số

| Cột | Field | Công thức |
|---|---|---|
| Số đơn chốt | `closed_orders` | `Count(id)` filter `assessment.sales_status_code ∈ CLOSED_REVENUE_SALES_STATUS_CODES` |
| Tổng doanh thu | `closed_revenue` | `Sum(total_after_discount)` cùng filter trên |
| Số đơn hoàn | `returned_orders` | như trên **và** `status ∈ RETURN_ORDER_STATUSES` |
| DThu giao thành công | `net_revenue` | như cột 2 **và** `status ∉ RETURN_ORDER_STATUSES` |

Cột 4 được mô tả nghiệp vụ là "tổng doanh thu − doanh thu hoàn"; cài đặt dùng
filter phủ định ngay trong SQL — **cùng một giá trị** (hai rổ hoàn/không-hoàn
rời nhau và phủ kín tập đơn chốt), đỡ một vòng cộng ở Python.

- `CLOSED_REVENUE_SALES_STATUS_CODES = ("closed", "closed_express", "created_order")`
  — đã chốt / hoả tốc / đã lên đơn. **Không** gồm `closed_scheduled_date`
  ("đã chốt, hẹn ngày"), khớp đúng tiêu chí Nhóm 9 để hai nhóm nói cùng một
  ngôn ngữ về "đơn chốt".
- `RETURN_ORDER_STATUSES = (4, 5, 12, 13)` — đang chuyển hoàn, đã chuyển hoàn,
  chờ lấy hàng hoàn, chờ xác nhận hoàn. User chốt 2026-08-04: trừ **cả nhóm**,
  không chỉ status 5, để số đơn hoàn ổn định sớm thay vì tăng dần khi đơn
  4/12/13 chuyển thành 5.

### Quan hệ với các nhóm khác (có chủ đích, không phải sai số)

- Tổng cột "Tổng doanh thu" của Nhóm 10 **nhỏ hơn** Nhóm 9, đúng bằng phần đơn chốt
  có nguồn sàn / koc / kol / không có nguồn. Chú thích dưới tiêu đề phải nói rõ điều
  này.
- Nhóm 4 ("Doanh thu giao thành công") dùng tiêu chí khác hẳn: chỉ `status = 3`,
  không xét đánh giá. Cột "DThu giao thành công" của Nhóm 10 **rộng hơn** Nhóm 4 vì vẫn tính
  đơn đang trên đường giao (status 0/1/2/7–11).

## 4. Backend

### Hằng số (`backend/apps/orders/_repositories_reports.py`)

```python
# Nguồn lead thuộc luồng sale nội bộ — bảng Nhóm 10 chỉ nhìn các nguồn này.
SALE_SOURCE_REPORT_ORDER_SOURCES = (
    OrderSource.LANDING, OrderSource.ZALO,
    OrderSource.MESSENGER, OrderSource.HOTLINE,
)
```

Đổi tên `OVERVIEW_RETURN_ORDER_STATUSES` → `RETURN_ORDER_STATUSES`: định nghĩa
"đơn hoàn" giờ dùng chung cho Báo cáo tổng và Nhóm 10, tên cũ mang tiền tố
`OVERVIEW_` gây hiểu nhầm là riêng của một báo cáo. Hằng số này chỉ được dùng ở
3 chỗ trong đúng file đó (dòng 136 định nghĩa, 1370 docstring, 1439–1440) —
đổi tên là thay đổi cục bộ, không có test nào tham chiếu.

### Method mới

`OrderRepository.summary_closed_revenue_by_staff_source()` — **một** query
group-by `(staff_id, order_source)`:

```python
queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
queryset = queryset.filter(order_source__in=SALE_SOURCE_REPORT_ORDER_SOURCES)

closed_q = Q(assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES)
returned_q = closed_q & Q(status__in=RETURN_ORDER_STATUSES)
net_q = closed_q & ~Q(status__in=RETURN_ORDER_STATUSES)

rows = (
    queryset.values("staff_id", "staff__username", "staff__display_name", "order_source")
    .annotate(
        closed_orders=Count("id", filter=closed_q),
        closed_revenue=Sum("total_after_discount", filter=closed_q),
        returned_orders=Count("id", filter=returned_q),
        net_revenue=Sum("total_after_discount", filter=net_q),
    )
    .order_by("staff__username", "staff_id", "order_source")
)
```

Hai field tiền ép về chuỗi (`str(value or Decimal("0"))`) như mọi summary khác,
giữ độ chính xác Decimal khi đi qua JSON. Hai field đếm ép về `int` với mặc
định 0.

Trả **hàng phẳng** thay vì lồng theo sale: FE tự dựng ma trận, backend không
phải biết layout bảng. Quy mô ~25 sale × 4 nguồn = tối đa 100 hàng.

### API

`GET /api/orders/dashboard/summary` — thêm key `closed_revenue_by_staff_source`.
Thay đổi cộng thêm, tương thích ngược. Response là `JSONField` tự do nên không
cần sửa serializer, chỉ thêm field vào `inline_serializer` để schema OpenAPI
không drift.

### Hiệu năng

+1 query trên tổng ~10 query sẵn có của `dashboard_summary`. Không join mới
(`assessment` đã join sẵn qua các annotate hiện hữu), lọc theo `created_at` +
`channel_id` + `staff_id` đều đã có index. Không cần index mới — đo trên dữ
liệu thật trước khi thêm.

Không gộp vào `summary_sale_performance_by_staff` (Nhóm 9) vì grain khác nhau:
gộp sẽ làm Nhóm 9 nở thành nhiều hàng/sale và phá số của mọi nhóm đang dùng
chung mảng `sale_performance_by_staff`.

## 5. Frontend

### File chạm tới

| File | Thay đổi |
|---|---|
| `frontend/src/features/dashboard/types.ts` | Thêm type `SaleSourceRevenueRow` + field vào `DashboardSummary` |
| `frontend/src/features/dashboard/sale-source-revenue.ts` | **Mới** — hàm pure `buildSaleSourceMatrix` |
| `frontend/src/features/dashboard/components/SaleSourceRevenueSection.tsx` | **Mới** — toàn bộ khối Nhóm 10 |
| `frontend/src/pages/DashboardPage.tsx` | Render sau `ClosedRevenueSection` |

Section đặt ở file riêng, không nối vào `DashboardSections.tsx` (đã 1443 dòng) —
cùng lý do và cùng cách làm với `ClosedRevenueSection` của Nhóm 9. Không refactor
file cũ trong phạm vi việc này.

### Selector

`buildSaleSourceMatrix(rows)` là hàm thuần, không phụ thuộc React, để unit test
không cần render:

- Gom hàng phẳng theo `staff_id` → mỗi sale một object có map `bySource`.
- **Luôn** trải đủ 4 nguồn theo thứ tự cố định `landing, zalo, messenger,
  hotline`, kể cả nguồn không phát sinh trong kỳ (ô 0) — bảng không nhảy cột khi
  đổi kỳ.
- Mỗi sale có thêm ô `total` = cộng ngang 4 nguồn.
- Trả kèm hàng `totals` = cộng dọc toàn bộ sale, gồm cả ô `total` của hàng tổng.
- Sắp xếp sale theo tổng doanh thu toàn nguồn giảm dần; hàng bằng
  nhau thì theo tên để thứ tự ổn định.

### Giao diện

Header 2 tầng, cột "Nhân viên" sticky khi cuộn ngang (1 cột tên + 4 nguồn × 4
chỉ số + nhóm Tổng 4 chỉ số = 21 cột):

```
             |            landing            |             zalo              | ... |             Tổng
Nhân viên    | Đơn  | DThu  | Hoàn  | DT.TC  | Đơn  | DThu  | Hoàn  | DT.TC  | ... | Đơn  | DThu  | Hoàn  | DT.TC 
-------------|------|-------|-------|--------|------|-------|-------|--------|-----|------|-------|-------|-------
Hạnh         |  240 |  980M |   210 |   861M |   48 |  190M |    40 |   158M | ... |  310 | 1240M |   268 |  1070M
-------------|------|-------|-------|--------|------|-------|-------|--------|-----|------|-------|-------|-------
Tổng         |  445 | 1790M |   390 |  1566M |   83 |  330M |    70 |   276M | ... |  575 | 2250M |   499 |  1950M
```

Nhóm cột "Tổng" ở cuối là **cộng ngang**, không phải chỉ số mới — bảng 16 cột
số mà không có tổng theo sale thì không dùng được. User đã chốt: không thêm cột
"% thất thoát do hoàn".

- Dùng lại `ORDER_SOURCE_LABELS` (`features/orders/order-source.ts:29`) cho nhãn
  nguồn, `formatCurrency` / `resolveDisplayName` (`dashboard-utils`) cho tiền và
  tên NV, `Card/CardHeader/CardContent` + `EmptyState` như các nhóm khác. Không
  tự vẽ style mới.
- Bảng mở sẵn, không bọc `CollapsibleTable` — cùng tinh thần bảng tổng kết cuối
  trang của Nhóm 9.
- Payload rỗng → `EmptyState`. Tên NV trống → `unknownStaffLabel`.

### Chú thích dưới tiêu đề

> Chỉ tính đơn có nguồn Landing / Zalo / Messenger / Hotline — không gồm
> đơn sàn (TikTok, Shopee), đơn KOC và đơn chưa gắn nguồn, nên tổng ở đây nhỏ
> hơn Nhóm 9. "Trước hoàn" = mọi đơn đã chốt (Đã chốt / Hoả tốc / Đã lên đơn,
> KHÔNG gồm "Đã chốt, hẹn ngày"). "Sau hoàn" = trừ tiếp các đơn đang hoàn hoặc
> đã hoàn. Đơn đã huỷ không tính ở cả hai cột.

## 6. Test

### Backend (`pytest`)

Test mới cho `summary_closed_revenue_by_staff_source`, dựng đơn của cùng một sale:

| Đơn | order_source | sales_status | order status | Kỳ vọng |
|---|---|---|---|---|
| A | landing | `closed` | 3 (đã giao) | vào đơn chốt + DThu giao thành công, KHÔNG vào đơn hoàn |
| B | landing | `closed_express` | 5 (đã hoàn) | vào đơn chốt **và** đơn hoàn |
| C | landing | `created_order` | 12 (chờ lấy hàng hoàn) | vào đơn chốt **và** đơn hoàn |
| D | zalo | `closed` | 0 | vào cả hai, ở hàng nguồn `zalo` |
| E | landing | `closed_scheduled_date` | 0 | **không** tính (hẹn ngày) |
| F | landing | `need_more_consulting` | 0 | **không** tính |
| G | landing | `closed` | 6 (đã huỷ) | **không** tính (loại ở base queryset) |
| H | tiktok | `closed` | 3 | **không** có hàng nào |
| I | koc | `closed` | 3 | **không** có hàng nào |
| J | NULL | `closed` | 3 | **không** có hàng nào |

Khẳng định thêm: status 4 và 13 cũng tính là đơn hoàn (cùng rổ với 5/12);
hai field tiền trả về dạng chuỗi; RBAC — user không có section `dashboard` nhận
403 khi gọi `/api/orders/dashboard/summary`.

### Frontend (`vitest`)

- Selector: dựng đúng ma trận sale × 4 nguồn; nguồn không có dữ liệu ra ô 0;
  ô `total` bằng tổng 4 nguồn; hàng `totals` bằng tổng các sale; thứ tự sale
  theo tổng doanh thu giảm dần.
- Component: render đủ 21 cột + hàng Tổng; payload rỗng → empty state; tên NV
  trống → "Không xác định".

## 7. Sprint

### Sprint 1 — Backend ✅
- [x] Hằng `SALE_SOURCE_REPORT_ORDER_SOURCES`; đổi tên `OVERVIEW_RETURN_ORDER_STATUSES` → `RETURN_ORDER_STATUSES`
- [x] `summary_closed_revenue_by_staff_source()` + key mới trong `dashboard_summary`
- [x] Unit test: lọc nguồn, đếm đơn hoàn, trừ đúng DThu giao thành công, loại hẹn ngày / đơn huỷ, RBAC

### Sprint 2 — Frontend ✅
- [x] Type + selector `buildSaleSourceMatrix`
- [x] `SaleSourceRevenueSection` + wiring vào `DashboardPage`
- [x] Unit test selector + component
- [x] Đồng bộ `backend/schema.yaml` + `frontend/src/api/schema.d.ts` (job drift của CI)

## 8. Ghi chú sau khi thực hiện

**Đã làm**

- Backend: 1 query group-by duy nhất; `returned_*` và `net_*` chỉ khác `closed_*`
  ở một điều kiện status nên "DThu giao thành công ≤ Tổng doanh thu" và "Số đơn
  hoàn ≤ Số đơn chốt" đúng theo cấu trúc, không phải nhờ kiểm tra runtime.
- Backend: hàng vẫn xuất hiện cho cặp (sale, nguồn) có data nhưng chốt 0 đơn —
  giữ nguyên tinh thần Nhóm 9 (sale nào cũng có mặt), không lọc bớt.
- FE: `buildSaleSourceMatrix` cộng nguồn **ngoài** danh sách vào ô Tổng thay vì
  bỏ đi — nếu sau này backend mở thêm nguồn mà FE chưa cập nhật, tổng bảng vẫn
  khớp API thay vì âm thầm hụt.
- Đã chạy: pytest 52/52 (10 test mới + hồi quy dashboard summary / overview
  report do đổi tên hằng), ruff sạch, `tsc --noEmit` sạch, `lint:strict` sạch,
  `format:check` sạch, `test:ci` 823 passed / 7 failed — 7 test đỏ này
  (RequireAuth ×2, use-orders-data.realtime-upsert-filters ×5) đã đỏ sẵn trên
  master.

**Chưa làm / cố ý bỏ qua**

- Chưa đánh index mới: query đi qua `created_at` + `channel_id` + `staff_id` +
  `order_source` — 3 cột đầu đã có index, đo trên dữ liệu thật trước khi thêm
  index cho `order_source`.

## 9. Ngoài phạm vi

- Không export Excel cho bảng này.
- Không thêm biểu đồ.
- Không thêm cột dẫn xuất "% thất thoát do hoàn".
- Không đổi định nghĩa/số liệu của bất kỳ nhóm 1–9 nào.
- Không refactor `DashboardSections.tsx`.
- Không đụng 2 tab "Báo cáo theo sale" / "Chi tiết theo sale".
