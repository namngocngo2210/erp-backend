# Báo cáo tổng — đổi gốc doanh thu sang trạng thái sale

**Ngày:** 2026-08-11 · **Trạng thái:** chờ user duyệt

## Vấn đề

Báo cáo tổng đang chia đơn theo *hóa đơn KiotViet*: doanh thu = đơn đã có hóa
đơn, không hủy, không hoàn. User muốn nhìn theo *trạng thái sale* — tiền mà đội
sale đã chốt trong kỳ — rồi soi tiền đó rơi vào đâu: giao được, đang giao, hoàn,
hay đang có nguy cơ hoàn.

Kèm theo là một bất biến user yêu cầu: **các ô kết quả cộng lại phải đúng bằng ô
Tổng doanh thu**. Hàng KPI hiện tại không có tính chất đó (chi phí Ads đứng lẫn
trong dãy, đơn đang giao không có ô nào).

## Định nghĩa số liệu

Tập đơn cơ sở **giữ nguyên**: `deleted_at IS NULL`, loại chi nhánh nội bộ
(`EXCLUDED_BRANCH_IDS`) và kho Đại Mỗ (`OVERVIEW_EXCLUDED_BRANCH_IDS`), loại
clone khôi phục còn sống, lọc theo `created_at` (+07), lọc nhóm kênh bán và lọc
sản phẩm không đổi.

### Phạm vi đơn (thay cho "đã có hóa đơn KiotViet")

```
CLOSED_SCOPE = trạng thái sale ∈ {closed, closed_express, closed_scheduled_date, created_order}
             ∪ (đã có hóa đơn KiotViet  ∧  chưa gắn trạng thái sale)

TỔNG DOANH THU = CLOSED_SCOPE  \  đơn hủy (status = 6 ∨ invoice_status = 2)
```

Vế thứ hai giữ lại doanh thu sàn: đơn Shopee / TikTok Shop và hóa đơn tạo thẳng
trên KiotViet không đi qua luồng CRM nên không có bản đánh giá, không mang trạng
thái sale — bỏ vế này thì khối donut *Nội sàn* thành rỗng. "Chưa gắn trạng thái
sale" gồm cả ba dạng dữ liệu thật: không có `OrderAssessment`, có nhưng
`sales_status_code` NULL, và có nhưng chuỗi rỗng.

Đơn hủy bị loại khỏi Tổng doanh thu để 4 rổ bên dưới phân hoạch kín. Dùng lại
`CANCELLED_ORDER_Q` (đơn hủy web KiotViet chỉ set `invoice_status=2`, đơn hủy POS
set `status=6`) — không định nghĩa lại quy tắc hủy ở đây.

### Bốn rổ kết quả

Sau khi loại đơn hủy còn đúng 13 mã trạng thái giao hàng, chia thành 4 rổ rời
nhau, không sót mã nào:

| Ô KPI | Trạng thái | Hằng số |
|---|---|---|
| Doanh thu giao thành công | 3 | `DELIVERED_SUCCESS_ORDER_STATUS` |
| Doanh thu chờ giao | 0, 1, 2, 7, 8, 9, 11 | `AWAITING_DELIVERY_ORDER_STATUSES` *(mới)* |
| Hoàn | 4, 5, 12, 13 | `RETURN_ORDER_STATUSES` *(có sẵn)* |
| Rủi ro hoàn | 10 (Chờ giao lại) | `RETURN_RISK_ORDER_STATUSES` *(mới)* |

**Bất biến:** `giao thành công + chờ giao + hoàn + rủi ro hoàn = tổng doanh thu`,
đúng cả về tiền lẫn số đơn, với mọi tổ hợp bộ lọc. Có test chốt.

`AWAITING_DELIVERY_ORDER_STATUSES` cố ý khai báo mới thay vì tái dùng
`IN_DELIVERY_ORDER_STATUSES` (0,1,2,7,8,9,10,11) của nhóm 5a: bộ đó gộp cả mã 10,
còn ở đây mã 10 phải tách ra thành ô Rủi ro hoàn. Định nghĩa rổ mới bằng phép trừ
trong Python trên `OrderStatus` để thêm trạng thái mới về sau không lọt rổ:
`AWAITING = mọi status - {3} - RETURN - {10} - {6}`.

### Phần trăm

Mẫu số của cả 4 ô đổi từ `gross_revenue` sang **Tổng doanh thu** → bốn ô cộng lại
đúng 100%. `% chi phí Ads` cũng tính trên Tổng doanh thu (trước là trên gross).

Chi phí Ads giữ nguyên công thức: FB × `ADS_DAILY_COST_MULTIPLIER` (1.1) + Google
× `GOOGLE_ADS_DAILY_COST_MULTIPLIER` (1.08), toàn bộ ad account theo `spend.date`.
Đây là tiền chi ra, **không** tham gia phép cộng của 4 rổ.

### Bỏ khỏi báo cáo

- Ô **Hủy** và ô **Đơn chờ chốt** (lead chưa lên hóa đơn) rời khỏi hàng KPI.
- `gross_revenue`, `pending_*`, `cancelled_*` rời khỏi response.

Bucket lead (`LEAD_ORDER_Q`) không còn được tính riêng, nhưng cơ chế chống phồng
số 2026-07-20 vẫn còn hiệu lực theo cách khác: lead chỉ vào Tổng doanh thu khi
sale đã đặt trạng thái chốt, chứ không phải mọi lead đang mở như bug cũ.

## Rủi ro đã biết

**Đơn đã có hóa đơn nhưng trạng thái sale ngoài 4 mã chốt sẽ rớt khỏi báo cáo.**
User chốt 2026-08-11: không tính. Nếu sau này đội sale đổi trạng thái sang *Khách
bom hàng* / *Huỷ đơn* sau khi đơn bị hoàn thì chính những đơn hoàn đó biến mất
khỏi cả Tổng doanh thu lẫn ô Hoàn, làm ô Hoàn tụt thấp giả tạo. Dấu hiệu nhận
biết: ô Hoàn gần 0 trong khi tab Hoàn ở màn đơn hàng vẫn nhiều đơn. Cách sửa khi
đó: nới vế hai thành "mọi đơn đã có hóa đơn KiotViet" — sửa đúng một hằng số
`CLOSED_SCOPE_Q`.

Chưa đo được tỷ trọng này vì DB local chỉ có dữ liệu seed; nên đối chiếu trên
prod ngay sau khi deploy.

## Backend

`OrderRepository.summary_overview` (`apps/orders/_repositories_reports.py`) —
cấu trúc query không đổi: 1 aggregate tổng + 1 group-by nhóm kênh bán + 1
group-by địa lý + 2 query spend. Chỉ đổi các `Q` lọc.

Phạm vi mới join sang `order_assessments`; cột `sales_status_code` đã có index
(`ord_assessment_sales_idx`) và đây là OneToOne nên không phát sinh N+1.

Response mới:

```json
{
  "total_revenue": "0",     "total_orders": 0,
  "delivered_revenue": "0", "delivered_orders": 0,
  "awaiting_revenue": "0",  "awaiting_orders": 0,
  "returned_revenue": "0",  "returned_orders": 0,
  "return_risk_revenue": "0", "return_risk_orders": 0,
  "ads_cost": "0",
  "revenue_by_sale_channel":     [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0,
                                    "delivered_revenue": "0", "delivered_orders": 0 }],
  "returned_by_sale_channel":    [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0 }],
  "return_risk_by_sale_channel": [{ "sale_channel": "marketplace", "revenue": "0", "orders": 0 }],
  "revenue_by_region":   [{ "region": "red_river_delta", "revenue": "0", "orders": 0,
                            "delivered_revenue": "0", "delivered_orders": 0 }],
  "revenue_by_province": [{ "province": "Hà Nội", "revenue": "0", "orders": 0,
                            "delivered_revenue": "0", "delivered_orders": 0 }]
}
```

Đổi tên `net_*` → `total_*` thay vì giữ tên cũ: "net" nghĩa là sau hoàn hủy, còn
số mới thì gồm cả hoàn — giữ tên cũ sẽ đánh lừa người đọc code sau này. Endpoint
chỉ có trang này gọi nên không có consumer nào khác phải sửa.

RBAC, URL, quyền `overview-report` không đổi.

## Frontend

- `OverviewKpiTiles.tsx` — 6 ô: Tổng doanh thu · Giao thành công · Chờ giao ·
  Hoàn · Rủi ro hoàn · Chi phí FB + Google Ads. Bốn ô giữa tô theo nghĩa (hoàn /
  rủi ro hoàn đỏ), ô chi phí đỏ và tách khỏi nhóm bằng ghi chú "ngoài phép cộng".
- `OverviewReportPage.tsx` — mặc định `DateFilter` đổi `today` → `this_month`;
  khối *Hủy theo kênh bán* thay bằng *Rủi ro hoàn theo kênh bán*; tiêu đề khối
  doanh thu bỏ chữ "sau hoàn hủy"; sửa mô tả trang.
- `overview-selectors.ts` — `OverviewKpis` theo response mới, mọi `%` chia cho
  `totalRevenue`.
- `lib/api/overview-report.ts` + `api/schema.d.ts` (chạy `gen:api`), i18n key mới
  cho 2 ô KPI và tiêu đề khối.

## Test

Backend `apps/orders/tests/test_overview_report.py`:

- **Bất biến cộng dồn**: dựng đủ 14 trạng thái + đơn hủy + đơn không có trạng
  thái sale, assert 4 rổ cộng lại = tổng, cả tiền lẫn số đơn.
- **Phạm vi**: đơn có trạng thái sale ngoài 4 mã chốt bị loại kể cả khi đã có hóa
  đơn; đơn đã có hóa đơn mà chưa gắn trạng thái sale được tính (3 dạng: không có
  assessment / NULL / chuỗi rỗng); lead có trạng thái chốt được tính.
- **Đơn hủy** không xuất hiện ở rổ nào.
- Cập nhật các test cũ đang assert `net_revenue` / `cancelled_*` / `pending_*`.
- Bất biến "tổng khối donut = ô KPI" giữ nguyên, chỉ đổi cơ sở sang tổng mới.

Frontend: `overview-selectors` test cho phép chia % mới; test hàng KPI hiển thị
đủ 6 ô.

## Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 | BE: hằng số rổ mới + `summary_overview` + response + test | Chưa làm |
| 2 | FE: hàng KPI, khối donut, mặc định Tháng này, i18n, test | Chưa làm |
| 3 | Cập nhật `docs/overview-report.md` + đối chiếu số trên prod | Chưa làm |
