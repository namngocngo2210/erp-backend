# Bung dòng theo sản phẩm ở 2 tab digital (Báo cáo ads)

Ngày: 2026-08-24 · Nhánh: `feat/ads-digital-product-breakdown`

## 1. Bài toán

Trang Báo cáo ads có 2 tab:

* **Báo cáo theo digital** — mỗi hàng là một kênh bán (digital), cột: doanh thu,
  chi phí, ME/RE, số data, CPL, số đơn, AOV, tỉ lệ chốt.
* **Báo cáo chi tiết theo digital** — chọn 1 kênh + 1 tháng, mỗi hàng là một ngày.

Yêu cầu: **bấm vào một hàng thì bung ra bên dưới các dòng con theo SẢN PHẨM**, với
đúng bộ chỉ số đó. Tập sản phẩm lấy từ các **chiến dịch nằm trong những TKQC được
gắn vào kênh** đang xét.

## 2. Nguồn dữ liệu đã có sẵn

| Việc | Đã có ở đâu |
|---|---|
| Chi tiêu theo (ngày, TKQC, sản phẩm-của-chiến-dịch) | `AdsSpendPort.spend_rows_by_account_product` — FB đọc grain *ad*, Google grain *campaign*, đều đã khử bản sao TKQC |
| Bản đồ TKQC → kênh (và nhóm bản sao) | `ChannelAdAccount` + `provider.account_groups()`; chi phí chia đều 1/N khi 1 TKQC map nhiều kênh |
| Phạm vi kênh/đơn của báo cáo ads | `_ads_report_channel_qs`, `_ads_landing_orders_queryset` |
| Doanh thu riêng của SP trong đơn | `OrderItem.line_total` (đường lọc `product_ids` của `summary_ads_daily_breakdown`) |

Nên **không phát sinh bảng mới, không migration**.

## 3. Chốt nghiệp vụ

1. **Tập sản phẩm hiện ra = chỉ SP được gán cho chiến dịch có chi tiêu trong kỳ,
   thuộc TKQC gắn vào chính kênh đó.** SP bán được ở kênh nhưng không chạy ads thì
   KHÔNG hiện → tổng doanh thu các dòng con **nhỏ hơn** dòng cha. UI ghi chú rõ.
2. **Tab chi tiết bung theo đúng ngày của hàng** (hàng 12/08 → các SP phát sinh
   ngày 12/08 của kênh đang chọn).
3. **Dòng cuối "Chưa gán chiến dịch"** = `chi phí dòng cha − tổng chi phí các dòng
   SP`. Làm bằng phép TRỪ ở frontend (không cộng riêng `product_id IS NULL`) — cùng
   lý do với `summary_ads_assigned_cost`: chi tiêu grain ad/campaign không khớp
   tuyệt đối grain account, phần chênh grain đúng là "chưa quy được về SP nào".
   Nhờ vậy tổng chi phí dòng con **luôn** khớp dòng cha.

### Định nghĩa từng cột ở dòng con

| Cột | Công thức | Ghi chú |
|---|---|---|
| Doanh thu | `SUM(OrderItem.line_total)` của SP, trong đơn ĐÃ CHỐT của kênh | tiền của riêng SP, không phải giá trị cả đơn |
| Chi phí | chi tiêu chiến dịch gán SP đó, ×hệ số FB / ×1.08 (Google), chia đều 1/N kênh | không phân bổ theo tỉ trọng doanh thu |
| Số data | số ĐƠN (mọi trạng thái chốt) có chứa SP | đơn 2 SP đếm ở cả 2 dòng → **tổng dòng con > dòng cha** |
| Số đơn | số đơn ĐÃ CHỐT có chứa SP | như trên |
| ME/RE, CPL, AOV, tỉ lệ chốt | dẫn xuất, dùng lại `toRow()` của `digital-report-selectors.ts` | mẫu số 0 → `—` |

**Bất biến kiểm chứng:** lọc 1 SP ở ô lọc sản phẩm sẵn có → dòng kênh phải ra đúng
bằng dòng con của SP đó khi không lọc. Đây là test chốt của backend.

## 4. Thiết kế

### Backend

`OrderRepository.summary_ads_product_breakdown(...)` trong `apps/orders/reporting/ads_reports.py`:

```
params: date_from, date_to, user, channel_ids, sale_channel_ids, excluded_ids,
        product_ids (lọc thêm), group_by_date: bool
return: [{date|None, channel_id, product_id, product__sku, product__name,
          revenue, cost, leads, closed_orders}]
```

* Trích helper `_ads_cost_channel_maps()` từ `summary_ads_daily_breakdown` (đang
  dựng `channel_labels` + `pk_to_group` + `group_channels`) để **hai đường dùng
  chung một cách gộp bản sao TKQC** — lệch chỗ này là số hai bảng đá nhau.
* Chi phí: duyệt `spend_rows_by_account_product`, bỏ `product_id IS NULL`, chia
  đều theo kênh của nhóm → `cost[(date?, channel, product)]`.
* Doanh thu/đơn/data: 1 query `OrderItem` trên tập đơn trong scope, giới hạn
  `product_id__in` = tập SP thu được từ bước chi phí.
* Ghép: chỉ giữ cặp `(kênh, SP)` **có mặt ở bản đồ chi phí**.

Endpoint mới `GET /api/orders/reports/ads/products` (cùng `require_section("ads-report")`):
`date_from`, `date_to`, `channel_ids`, `product_ids`, `group=channel|day`.
Tách endpoint riêng thay vì nhồi vào `daily_breakdown` để **tải lười**: chỉ bắn khi
người dùng bung dòng đầu tiên, và payload không bị nhân theo (ngày × kênh × SP).

### Frontend

* `queries.ts`: `useAdsDigitalProducts({dateFrom, dateTo, channelIds, productIds, group, enabled})`.
* `digital-report-selectors.ts`: `buildDigitalProductRows(costRows, parentRow)` — dựng
  `DigitalMetricRow[]` con + chèn dòng "Chưa gán chiến dịch" cuối cùng.
* `DigitalMetricTable`: thêm prop `expandable` — hàng có nút chevron, bung ra
  `<tr>` con thụt lề, dùng lại đúng hàm `cells()` nên không lệch cột.
* `DigitalReportTab` bung theo `channelKey`; `DigitalDetailTab` bung theo `date`.

## 5. Sprint

| # | Việc | Trạng thái |
|---|---|---|
| 1 | Backend: helper dùng chung `_ads_cost_channel_maps` + `summary_ads_product_breakdown` | ✅ |
| 2 | Backend: endpoint `reports/ads/products` + schema + 7 test | ✅ |
| 3 | FE: hook `useAdsDigitalProducts` + selector `buildDigitalProductRows` + 7 test | ✅ |
| 4 | FE: bung hàng ở `DigitalMetricTable` + 2 tab + 6 test | ✅ |
| 5 | Lint/typecheck/test/regen schema | ✅ |

## 6. Đã làm / chưa làm

### Đã làm

* `_ads_cost_channel_maps` tách khỏi `summary_ads_daily_breakdown` — hai đường chi
  phí (theo ngày/kênh và theo sản phẩm) nay gộp bản sao TKQC bằng **cùng một** đoạn
  code, không còn khả năng lệch.
* `summary_ads_product_breakdown` + `GET /api/orders/reports/ads/products`
  (`group=channel|day`, có `product_ids`), gác `require_section("ads-report")`.
* Bảng `DigitalMetricTable` bung hàng: dòng con render bằng đúng hàm `cells()` của
  dòng cha; dòng cuối "Chưa gán chiến dịch"; ghi chú phần doanh thu của SP không có
  chiến dịch.
* Tải lười: query chỉ bật khi đã bung ≥1 hàng, **một** request cho cả bảng.
* Test: backend 38 xanh (7 mới), frontend 1.415 xanh (13 mới). `ruff`,
  `lint-imports`, `eslint --max-warnings=0`, `tsc --noEmit` sạch.

### Đo trên prod (2026-08-24, khoảng 18–23/08)

| Kênh | Chi phí | Số SP sẽ hiện | % chưa gán |
|---|---|---|---|
| Facebook Lợi | 50,7tr | 6 | 0,0% |
| Facebook Cường | 45,1tr | 4 | 0,0% |
| Facebook | 43,4tr | 5 | 0,0% |
| Facebook Đức | 36,6tr | 5 | 0,0% |
| Facebook Thành | 34,2tr | 7 | 0,0% |
| Facebook Khanh | 10,7tr | 4 | 0,6% |

Chi tiêu grain ad phủ **100%** grain account sau khi trải bản sao TKQC (nếu đo mà
KHÔNG trải bản sao thì ra 0–24% — bẫy đo, không phải lỗ hổng dữ liệu). Payload
mỗi lần bung chỉ vài chục dòng.

### Chưa làm / cần theo dõi sau deploy

* Chưa có ô "xuất Excel" cho phần bung — nếu phòng Ads cần, làm ở MR sau.
* Chỉ mới xét nguồn Facebook trên prod; Google mới có 2 link TKQC vào 1 kênh, cần
  soát lại số sau khi deploy.
* Tổng "số data" các dòng con LỚN HƠN dòng cha (đơn nhiều SP đếm ở mọi dòng SP) —
  cần nói trước với phòng Ads để không bị coi là sai số.
