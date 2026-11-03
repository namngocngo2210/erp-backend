# Báo cáo Sale — Nhóm 9: Data chia & doanh thu chốt theo Sale

- Ngày: 2026-07-23
- Nhánh: `feat/sale-report-closed-revenue`
- Trạng thái: đã chốt thiết kế, chờ triển khai

## 1. Bối cảnh

Trang Báo cáo Sale (`/dashboard`) hiện có 8 nhóm chỉ số. Hai nhóm liên quan:

- **Nhóm 1 — Tỷ lệ chốt trên tổng data**: hiển thị `total` (data được chia cho
  từng sale) và `closed` (số đơn chốt), nhưng không có tiền.
- **Nhóm 4 — Doanh thu giao thành công theo Sale**: chỉ tính đơn đã giao thành
  công (`status = 3`), tức doanh thu đã thực nhận.

Thiếu con số ở giữa: **sale chốt được bao nhiêu tiền** tại thời điểm chốt, không
phụ thuộc kết quả giao hàng. Đây là số dùng để đánh giá năng lực chốt đơn và
để so sánh với doanh thu giao thành công (chênh lệch = tiền rơi ở khâu vận
chuyển).

## 2. Yêu cầu

Thêm một khối ở **cuối trang** Báo cáo Sale, gồm 4 cột theo từng nhân viên sale:

1. Số lượng data được chia
2. Số đơn chốt được
3. Tỷ lệ chốt
4. Doanh thu chốt được

## 3. Định nghĩa số liệu

| Cột | Nguồn | Công thức |
|---|---|---|
| Data được chia | `total` (đã có) | `COUNT(order)` group theo `staff` |
| Đơn chốt | `closed_revenue_orders` (**mới**) | `COUNT(order)` với `assessment.sales_status_code ∈ CLOSED_REVENUE_SALES_STATUS_CODES` |
| % chốt | dẫn xuất ở FE | `closed_revenue_orders / total` |
| Doanh thu chốt | `closed_revenue` (**mới**) | `SUM(total_after_discount)` với `assessment.sales_status_code ∈ CLOSED_REVENUE_SALES_STATUS_CODES` (3 trạng thái) — trên đúng tập đơn của cột `closed_revenue_orders` |

`CLOSED_SALES_STATUS_CODES = ("closed", "closed_express", "closed_scheduled_date", "created_order")`
— đã chốt, chốt hoả tốc, chốt hẹn lịch, đã lên đơn. Vẫn dùng nguyên cho Nhóm
1/2/6, doanh thu Sale FB, báo cáo Ads và báo cáo Khách hàng.

`CLOSED_REVENUE_SALES_STATUS_CODES = ("closed", "closed_express", "created_order")`
— riêng cho Nhóm 9, KHÔNG gồm `closed_scheduled_date` ("đã chốt, hẹn ngày").

### Phạm vi đơn

Kế thừa nguyên phạm vi của báo cáo Sale (`_sales_report_orders_queryset`):

- `staff.department_id = SALES`, `staff.deleted_at IS NULL`
- `channel_id ∈ sale_report_channel_ids()` (kênh có gán NV phòng Ads)
- lọc kỳ theo `created_at` (đồng bộ mốc với các nhóm 1–8)

### Quy tắc hoàn / huỷ (user chốt 2026-07-23)

- **Có tính đơn hoàn.** Đơn đã chốt rồi bị hoàn vẫn là tiền sale chốt được;
  phần rơi rụng do vận chuyển đã có Nhóm 5a/5b/Nhóm 4 phản ánh. Không thêm
  điều kiện lọc `status ∉ (4, 5, 12, 13)`.
- **Không tính đơn huỷ.** Đơn huỷ (`status = 6` hoặc `invoice_status = 2`) đã bị
  loại ở `OrderRepository._base_orders_queryset()` cho toàn bộ báo cáo Sale, nên
  yêu cầu này đã đúng sẵn — **không thêm điều kiện nào**.

Hệ quả có chủ đích: cột "Data được chia" của nhóm mới **khớp tuyệt đối** với
cột "Tổng data" của Nhóm 1, vì dùng chung field `total`. Cột "Đơn chốt" và
"Doanh thu chốt" thì **không** khớp Nhóm 1 — xem mục "Thay đổi sau khi triển
khai (2026-07-23)" bên dưới.

## Thay đổi sau khi triển khai (2026-07-23)

Chủ sở hữu nghiệp vụ chốt thêm: bỏ `closed_scheduled_date` ("đã chốt, hẹn
ngày") khỏi Nhóm 9 — đơn hẹn ngày giao chưa đủ chắc chắn để ghi nhận là tiền đã
chốt. **Chỉ Nhóm 9 đổi**; các báo cáo khác đọc `CLOSED_SALES_STATUS_CODES` (Nhóm
1/2/6, doanh thu Sale FB, 4 query Báo cáo Ads, crosstab Báo cáo Khách hàng) giữ
nguyên đủ 4 trạng thái, không bị đụng tới. Hệ quả: Nhóm 9 lệch Nhóm 1 đúng bằng
số đơn "đã chốt, hẹn ngày" trong kỳ — cả ở cột "Đơn chốt" lẫn "Doanh thu chốt".

## 4. Backend

### Thay đổi

`OrderRepository.summary_sale_performance_by_staff()`
(`backend/apps/orders/_repositories_reports.py`) — thêm hai `annotate` vào lượt
group-by đang chạy. Cả hai dùng chung một filter để tiền và số đơn của Nhóm 9
không lệch tiêu chí:

```python
closed_revenue=Sum(
    "total_after_discount",
    filter=Q(assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES),
),
closed_revenue_orders=Count(
    "id",
    filter=Q(assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES),
),
```

Trong vòng lặp hậu xử lý sẵn có, ép về chuỗi giống `revenue` để giữ độ chính xác
Decimal khi đi qua JSON:

```python
for row in rows:
    row["revenue"] = str(row["revenue"] or Decimal("0"))
    row["closed_revenue"] = str(row["closed_revenue"] or Decimal("0"))
```

### Hiệu năng

Aggregate mới nằm trong cùng một câu `GROUP BY` đang chạy — **không thêm
round-trip DB, không thêm join** (`assessment` đã được join sẵn cho các annotate
`closed`/`nurture`/`dirty`...). Không cần index mới.

### API

`GET /api/orders/dashboard/summary` — key `sale_performance_by_staff` có thêm
field `closed_revenue` (string) và `closed_revenue_orders` (int). Thay đổi cộng
thêm, tương thích ngược; response schema là `JSONField` tự do nên không cần sửa
serializer.

## 5. Frontend

### File chạm tới

| File | Thay đổi |
|---|---|
| `frontend/src/features/dashboard/types.ts` | Thêm `closed_revenue: string` + `closed_revenue_orders: number` vào `SalePerformanceRow` |
| `frontend/src/features/dashboard/use-dashboard-summary.ts` | Cộng dồn cả hai field vào `performanceTotals` |
| `frontend/src/features/dashboard/components/ClosedRevenueSection.tsx` | **File mới** — toàn bộ khối Nhóm 9 |
| `frontend/src/pages/DashboardPage.tsx` | Render `ClosedRevenueSection` cuối trang, sau `CarrierDataSection` |
| `frontend/src/i18n/messages/*` | Key i18n cho tiêu đề / chú thích / tên cột |

Section đặt ở **file riêng** thay vì nối vào `DashboardSections.tsx` — file đó đã
1443 dòng và đang là điểm nghẽn dễ đọc nhất của feature dashboard. Không refactor
file cũ trong phạm vi việc này.

### Giao diện

Bảng mở sẵn (không bọc `CollapsibleTable`), đúng tinh thần "bảng tổng kết cuối
trang liếc là thấy":

```
Nhân viên | Data được chia | Đơn chốt | % chốt | Doanh thu chốt
----------|----------------|----------|--------|----------------
Hạnh      |            420 |      310 |  73.8% |  1.240.500.000
Linh      |            388 |      265 |  68.3% |  1.010.200.000
----------|----------------|----------|--------|----------------
Tổng      |            808 |      575 |  71.2% |  2.250.700.000
```

- Sắp xếp: doanh thu chốt giảm dần (`useMemo` trên `performanceRows`; không sửa
  thứ tự trả về từ API vì các nhóm khác đang dùng chung mảng đó).
- Dòng Tổng: cộng từ `performanceTotals`, `% chốt` tính lại từ tổng chứ không
  lấy trung bình các dòng.
- Dùng lại `formatCurrency`, `resolveDisplayName`, `formatPercent`,
  `Card/CardHeader/CardContent`, `EmptyState` sẵn có — không tự vẽ style mới.
- Rỗng (`performanceRows.length === 0`) → `EmptyState` như các nhóm khác.
- Tên nhân viên trống → nhãn "Không xác định" (`unknownStaffLabel`) như các nhóm
  khác.

### Chú thích dưới tiêu đề

> Doanh thu chốt = tổng tiền các đơn đã có đánh giá ở trạng thái Đã chốt / Hoả
> tốc / Đã lên đơn — KHÔNG gồm "Đã chốt, hẹn ngày". Tính cả đơn sau đó bị hoàn;
> không tính đơn đã huỷ. Vì vậy số đơn ở đây nhỏ hơn cột Tổng chốt của Nhóm 1.
> Nhóm 4 dùng tiêu chí khác — chỉ đơn đã giao thành công, không xét đánh giá.

## 6. Test

### Backend (`pytest`)

Test mới cho `summary_sale_performance_by_staff`, dựng các đơn của cùng một sale:

| Đơn | sales_status | order status | Kỳ vọng |
|---|---|---|---|
| A | `closed` | 3 (đã giao) | tính vào `closed_revenue` |
| B | `closed_express` | 5 (đã hoàn) | **tính** vào `closed_revenue` |
| C | `created_order` | 6 (đã huỷ) | **không** tính (bị loại ở base queryset) |
| D | `closed_scheduled_date` | 0 | **không** tính vào `closed_revenue`, nhưng **vẫn** vào `closed` của Nhóm 1 |
| E | `need_more_consulting` | 0 | **không** tính |

Khẳng định `closed_revenue == tiền(A) + tiền(B)`, `closed_revenue` là chuỗi,
`closed_revenue_orders == 3` và `closed == 4` — chênh đúng 1 đơn hẹn ngày.

### Frontend (`vitest`)

Test mới cho `ClosedRevenueSection`:

- Render đúng số dòng + dòng Tổng, giá trị Tổng bằng tổng các dòng.
- Thứ tự dòng theo doanh thu chốt giảm dần.
- `performanceRows` rỗng → hiện empty state.

## 7. Ngoài phạm vi

- Không refactor `DashboardSections.tsx`.
- Không thêm biểu đồ cho nhóm mới (đã có biểu đồ doanh thu ở Nhóm 4).
- Không xuất Excel cho bảng này.
- Không đổi định nghĩa/số liệu của bất kỳ nhóm 1–8 nào.
