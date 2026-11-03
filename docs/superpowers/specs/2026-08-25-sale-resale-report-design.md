# Tab "Resale" trên trang Báo cáo Sale

Ngày: 2026-08-25 · Nhánh: `feat/sale-resale-report`

## Vấn đề

Trang Báo cáo Sale hiện có 3 tab — "Tổng quan", "Báo cáo theo sale", "Báo cáo chi tiết
theo sale" — và cả ba đều chỉ đo **một lát cắt duy nhất**: sale nhận data về rồi chốt
được bao nhiêu. Không tab nào trả lời được câu hỏi ngược lại: *doanh thu đến từ khách
đã mua rồi quay lại là bao nhiêu, và ai đang làm tốt việc đó.*

Đây là lỗ hổng có giá. Chi phí có được một data mới nằm ở báo cáo Ads; doanh thu từ
khách cũ gần như miễn phí đầu vào. Không tách được hai nguồn này thì:

- Không biết tỉ trọng doanh thu thật sự phụ thuộc vào tiền quảng cáo.
- Không thưởng/phạt đúng người: sale chỉ giỏi cày data mới và sale giữ được khách
  đang trông giống hệt nhau trên mọi bảng hiện có.

Trong codebase **đã có** domain resale (trang "Theo dõi resale", bảng
`customer_resale_followups`) nhưng nó là công cụ **tác nghiệp**, không phải báo cáo:
`resale_orders` là JSONField sale gõ tay (`product`, `quantity`, `order_value`), không
có cột nhân viên, không đối soát được với KiotViet. Dùng nó làm nguồn số cho báo cáo
KPI là xây báo cáo trên dữ liệu tự khai.

## Quyết định thiết kế

**Nguồn số là đơn thật trong bảng `orders`, không phải `resale_orders` JSON.** Số tự
động, đối chiếu được với KiotViet, không chết khi sale lười nhập. Đánh đổi: rổ resale
gộp cả khách tự quay lại mua chứ không riêng công sale chăm sóc — chấp nhận, vì báo
cáo này đo *doanh thu từ khách cũ*, còn hiệu quả chăm sóc đã có trang Theo dõi resale.

**Định nghĩa đơn resale.** Đơn `O` là đơn resale ⟺ khách của `O` đã có **ít nhất một
đơn giao thành công (GTC) được tạo trước `O.created_at`**. Mệnh đề GTC lấy đúng chuẩn
đang dùng toàn hệ thống (`apps/analytics/sql/gtc.py`): `status=3`,
`invoice_status IS DISTINCT FROM 2`, loại chi nhánh ẩn, loại đơn mirror.

**Mốc so sánh là `created_at`, KHÔNG phải ngày giao hàng.** Hệ quả cố ý: khách đặt đơn
1 ngày 01/08, đặt đơn 2 ngày 02/08, đơn 1 giao thành công ngày 05/08 → đơn 2 **vẫn**
tính resale dù lúc đặt khách chưa cầm hàng. Lý do: hệ thống không có cột "ngày giao
thành công" đáng tin — trang Theo dõi resale đang xài tạm `updated_at`, mà cột đó trôi
mỗi lần bất kỳ ai sửa đơn. Chọn `created_at` cho ra luật ổn định và giải thích được;
đổi lại rổ resale rộng hơn nghĩa chặt một chút.

**Gán doanh thu cho `Order.staff` của chính đơn resale**, không phải sale của đơn đầu
tiên. Nhất quán với cả 3 tab hiện có (đều gán theo staff của đơn), nên số cộng ngang
không lệch và KPI đối soát được. Câu hỏi "ai nuôi khách tốt" là báo cáo khác, không
nhét vào đây.

**Đơn ngành hàng cũ vẫn tính là lần mua trước.** Mart `dwh.customer_360` tách `era`
(legacy / current) vì 354k khách chỉ mua ngành cũ trước 2025. Ở đây **không** lọc theo
`era`: chỉ 485 khách mua cả hai ngành (0,14%), thêm điều kiện là thêm một khái niệm
người đọc báo cáo phải học để đổi lấy 0,1% sai số. Ghi lại ở đây để sau này ai thấy
lệch thì biết vì sao.

**Phạm vi đơn và tập trạng thái dùng lại y nguyên tab "Báo cáo theo sale":**
`_sales_report_orders_queryset` (kênh có gán NV phòng Ads + `staff.department=SALES` +
staff chưa xoá) và `CLOSED_SALES_STATUS_CODES` (4 mã chốt). Đây là điều kiện để
`resale_revenue ≤ total_revenue` luôn đúng và hai tab đặt cạnh nhau không chỏi số.

**Module riêng `reporting/resale_reports.py`, không nhồi vào `sale_reports.py`.**
File đó đã 515 dòng / 7 method; folder `reporting/` vốn tách theo miền
(`ads_reports`, `overview`, `delivery_quality`, `product_sales_rank`). Thêm mixin thứ 5
đúng khuôn có sẵn.

**Trả cả `total_*` lẫn `resale_*` trong một payload.** FE cần `% doanh thu resale trên
tổng` — tính ở backend trong cùng một lần quét thay vì bắt FE gọi thêm endpoint thứ hai
rồi ghép hai bộ số có thể lệch scope.

**Payload gộp sẵn theo nhân viên (không có chiều ngày).** Tab "Báo cáo theo sale" trả
hàng phẳng `(ngày, nhân viên)` vì nó nuôi 2 tab. Tab này chỉ có 1 bảng xếp hạng, thêm
chiều ngày là nhân payload lên ~30 lần cho một tính năng chưa ai yêu cầu. Cần biểu đồ
diễn biến thì mở rộng sau.

## Phạm vi

### 1. Backend — `apps/orders/reporting/resale_reports.py` (mới)

```python
class ResaleReportsMixin:
    @classmethod
    def summary_resale_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ) -> list[dict]:
```

Điều kiện resale (subquery tương quan):

```python
prior_gtc = cls._base_orders_queryset().filter(
    customer_id=OuterRef("customer_id"),
    created_at__lt=OuterRef("created_at"),
    status=DELIVERED_SUCCESS_ORDER_STATUS,   # 3
).values("pk")
is_resale = Exists(prior_gtc)
```

**Dùng lại `_base_orders_queryset()` chứ không tự dựng 5 mệnh đề GTC.** Helper đó đã
gói sẵn `deleted_at IS NULL` + loại `invoice_status=2` + `_exclude_hidden_branches()`,
và nó chạy trên `Order.objects` — manager **đã lọc đơn mirror** (`foxia`/`cnv`, xem
`apps/common/order_scope.py`). Cộng thêm `status=3` là ra đúng mệnh đề GTC chuẩn.

Đây **không** phải chuyện tiện tay: `CANCELLED_INVOICE_STATUS` sống ở
`apps/customers/order_status_constants.py`, mà `.importlinter` xếp `customers | orders`
**cùng tầng, độc lập** — module production của `apps.orders` import sang `apps.customers`
là vi phạm hợp đồng tầng (chỉ `apps.orders.tests.*` được miễn). Đi qua
`_base_orders_queryset()` vừa tránh vi phạm vừa khỏi chép hằng số.

`Order.customer` là FK non-null (`on_delete=PROTECT`) nên không có nhánh NULL.

Aggregate, group theo `staff`:

| Field | Công thức |
|---|---|
| `total_orders` | `Count(id, filter=closed_q)` |
| `total_revenue` | `Sum(total_after_discount, filter=closed_q)` |
| `resale_orders` | `Count(id, filter=closed_q & resale_q)` |
| `resale_revenue` | `Sum(total_after_discount, filter=closed_q & resale_q)` |
| `resale_customers` | `Count(customer_id, distinct=True, filter=closed_q & resale_q)` |

`closed_q = Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)`.
Cột tiền trả về dạng **chuỗi decimal** (`str(value or Decimal("0"))`) — đúng khuôn
`summary_sale_daily_by_staff`, tránh mất chính xác khi qua JSON.

Hàng gồm `staff_id`, `staff__username`, `staff__display_name`; sắp theo
`-resale_revenue` để FE khỏi phải sort lại (vẫn sort phòng thân ở selector).

**Chỉ số hiệu năng.** Index `ord_gtc_customer_idx` — `(customer, -created_at)
WHERE deleted_at IS NULL AND status = 3` — phủ **đúng** subquery trên, nên mỗi đơn
trong tập lọc tốn một index scan có bound, không phải seq scan. Không cần migration
mới. Cần kiểm chứng bằng `EXPLAIN (ANALYZE, BUFFERS)` trên prod sau khi deploy.

### 2. Backend — ghép mixin

`reporting/__init__.py`: import `ResaleReportsMixin`, thêm vào `OrderReportsMixin`
(mixin thứ 5, không trùng tên method nên thứ tự vô nghĩa), cập nhật docstring liệt kê
module.

### 3. Backend — endpoint

`apps/orders/transport/views/order_views_impl/dashboard_report_views.py`:

```
GET /api/orders/reports/resale-by-staff?date_from=&date_to=
→ { "by_staff": [ ... ] }
```

- `@permission_classes([IsAuthenticated, require_section("dashboard")])` — cùng quyền
  với `sale_staff_report_summary`, **không** thêm quyền mới (ai xem được Báo cáo Sale
  thì xem được tab này).
- Scope kênh lấy qua `OrderRepository.ads_channel_scope()` một lần cho cả request,
  đúng khuôn view anh em.
- `extend_schema` khai `by_staff` để `schema.yaml` sinh ra type FE dùng được.

`apps/orders/urls.py`: `path("reports/resale-by-staff", ..., name="orders-resale-report")`
đặt ngay sau `reports/sale-by-staff`.

### 4. Frontend

| File | Việc |
|---|---|
| `features/sale-report/queries.ts` | thêm `useSaleResaleReport` — cùng chính sách cache/refetch (`SALE_REPORT_REFRESH_MS`, `keepPreviousData`) với `useSaleStaffReport` |
| `features/sale-report/resale-selectors.ts` (mới) | hàm thuần: parse chuỗi decimal → number, tính `aov`, `sharePct`, dựng dòng tổng, sort giảm dần |
| `features/sale-report/components/SaleResaleTab.tsx` (mới) | khung tab: loading (`LogoSpinner`), lỗi (card `border-destructive/50`), rỗng — theo đúng khuôn `SaleStaffReportTab.tsx` |
| `features/sale-report/components/ResaleRevenueChart.tsx` (mới) | bar ngang xếp hạng |
| `pages/DashboardPage.tsx` | chèn tab **vị trí thứ 2**, ngay sau "Tổng quan"; dùng chung `dateFilter` của trang |

**Chart.** `BarChart layout="vertical"` (recharts v3): `XAxis type="number"` +
`YAxis type="category" dataKey="key"` (dataKey là `key` — định danh duy nhất của hàng,
KHÔNG phải `label` hiển thị, để hai nhân viên trùng `display_name` không gộp thành một
hạng mục trên trục; nhãn người-đọc lấy lại qua `tickFormatter`/lookup key→label), chiều
cao `Math.max(260, rows * 40 + 72)` — cùng tinh thần công thức `CloseRateChart` (số cụ
thể lệch nhẹ để vừa margin của chart này). Màu/lưới lấy từ token sẵn có (`var(--border)`,
`CHART_COLORS`), tiền format bằng `formatRevenueShort` từ `@/lib/metrics`. Tooltip dùng
`ChartTooltip` chung của `DashboardPrimitives`, label cũng dịch qua lookup key→label
trước khi hiện.

**Bảng dưới chart:** Sale · Đơn resale · Khách quay lại · Doanh thu resale · AOV resale
· % trên tổng doanh thu. Có dòng tổng.

**i18n:** dùng khuôn `t("saleReport.<key>", "<fallback tiếng Việt>")` — namespace
`saleReport` hiện **không** có entry trong `i18n/messages/`, cả 3 tab đang sống bằng
fallback inline. Giữ nguyên cách đó, không phát sinh file dịch.

### 5. `schema.yaml`

Sửa tay theo công thức đã đúc kết (ChoiceField hoist thành `$ref`, `required` sort theo
alphabet, không param nào có `explode`/`style`). **Không** regen toàn file — bản trên
master đã cũ sẵn ~444 dòng, regen sẽ kéo nhiễu của feature khác vào MR này.

### 6. Test

**Backend** — thêm **một** class vào `apps/orders/tests/test_sale_reports.py` (theo
CLAUDE.md: không rải test lẻ). Phủ:

| Ca | Kỳ vọng |
|---|---|
| Khách chỉ có 1 đơn (đơn đầu) | không vào rổ resale |
| Khách có đơn GTC trước → đơn sau | vào rổ resale |
| Đơn trước **huỷ** (`invoice_status=2`) | đơn sau không phải resale |
| Đơn trước **chưa giao** (`status≠3`) | đơn sau không phải resale |
| Đơn trước ở **chi nhánh ẩn** | đơn sau không phải resale |
| Đơn trước là **đơn mirror** (`order_source="foxia"`) | đơn sau không phải resale (rào chắn ngầm của `Order.objects`, không phải mệnh đề tường minh — test riêng để bắt được nếu ai đó đổi `_base_orders_queryset` sang `Order.all_objects`) |
| Đơn trước bị **xoá mềm** (`deleted_at` có giá trị) | đơn sau không phải resale |
| Đơn resale nhưng **trạng thái sale ngoài 4 mã chốt** | không vào doanh thu |
| Staff ngoài phòng Sales | bị loại khỏi cả `total` lẫn `resale` — ĐÃ viết |
| Đơn ngoài kênh sale (`channel_id` không thuộc `sale_channel_ids`) | **CHƯA có test riêng.** Cả hai vế do cùng một lời gọi `_sales_report_orders_queryset()` áp, nên test "staff ngoài phòng Sales" ở trên đã chứng minh hàm này thực sự đi qua queryset đó; vế kênh nếu hỏng sẽ hỏng đồng thời ở cả 4 tab báo cáo chứ không riêng Resale. Lưu ý: trong `test_sale_reports.py` hiện KHÔNG có test nào dựng 2 kênh để soi vế kênh — chỗ gần nhất là `DataByCarrierReportTests`. Đây là nợ test của helper dùng chung, không phải của riêng tab này. |
| 2 đơn resale cùng 1 khách | `resale_orders=2`, `resale_customers=1` |
| Bất biến | `resale_revenue ≤ total_revenue` |
| Quyền | user không có section `dashboard` → 403 |

**Frontend** — `features/sale-report/__tests__/resale-selectors.test.ts`: parse decimal,
chia 0 (`total_revenue=0` → `sharePct` null chứ không `Infinity`/`NaN`), sort, dòng
tổng. Test component tối thiểu cho `SaleResaleTab` (loading / lỗi / rỗng).

⚠️ Chart **không** test bằng jsdom — recharts cần đo kích thước thật, `ResponsiveContainer`
ra 0×0. Test dữ liệu ở tầng selector, không ở tầng render chart.

## Ngoài phạm vi (cố ý)

- Không đụng trang "Theo dõi resale" và `customer_resale_followups`.
- Không thêm chiều ngày / biểu đồ diễn biến.
- Không xuất Excel cho tab này.
- Không thêm quyền RBAC mới.
- Không đổi 3 tab hiện có.

## Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| **S1 — Backend core** | `resale_reports.py` + ghép mixin + test repository | ✅ xong |
| **S2 — Endpoint** | view + url + `extend_schema` + `schema.yaml` + test API/quyền | ✅ xong |
| **S3 — FE data** | `queries.ts` + `resale-selectors.ts` + test selector | ✅ xong |
| **S4 — FE UI** | `SaleResaleTab` + `ResaleRevenueChart` + chèn tab + test component | ✅ xong |
| **S5 — Chốt** | lint (ruff / eslint / tsc), chạy test 2 phía, ghi chú đã-làm/chưa-làm, push + MR | ✅ xong |

S1 và S3 độc lập nhau (S3 chỉ cần *hợp đồng* payload, không cần backend chạy) nên chạy
song song được. S2 phụ thuộc S1; S4 phụ thuộc S3.

### Ghi nhận sau khi thực hiện (2026-08-25)

**Đã làm:** S1–S5 đầy đủ.

- Backend: `ResaleReportsMixin.summary_resale_by_staff` trong
  `apps/orders/reporting/resale_reports.py`, ghép vào `OrderReportsMixin`; endpoint
  `GET /api/orders/reports/resale-by-staff` (view + `urls.py` + `extend_schema` +
  `schema.yaml` sửa tay).
- Frontend: `useSaleResaleReport` (`queries.ts`), `resale-selectors.ts`, tab "Resale"
  (`SaleResaleTab.tsx`) chèn ở vị trí thứ 2 trên `DashboardPage.tsx` — ngay sau
  "Tổng quan" — với `ResaleRevenueChart.tsx` (bar ngang xếp hạng) và bảng 6 cột
  (Sale · Đơn resale · Khách quay lại · Doanh thu resale · AOV resale · % trên tổng
  doanh thu), có dòng Tổng.
- Test: backend 17 test trong `apps/orders/tests/test_sale_reports.py`
  (`SaleResaleByStaffTests`: 12; `SaleResaleReportApiTests`: 5 — đã hiệu chỉnh sau vòng
  fix cuối để bổ sung 2 ca thiếu, xem "Chưa làm" ở review gốc). Frontend: 10 test
  `resale-selectors.test.ts` + 5 test `SaleResaleTab.test.tsx` + 1 test bổ sung trong
  `DashboardPage.tabs.test.tsx` ("chỉ gọi API resale khi mở tab Resale").
- Lint: ruff (`apps/orders/`), eslint (`features/sale-report`), `tsc --noEmit` sạch.

**Chưa làm (cố ý):** xuất Excel cho tab này, biểu đồ diễn biến theo ngày (payload gộp
sẵn theo nhân viên, không có chiều ngày), lọc theo `era` ngành hàng (legacy/current) —
xem mục "Ngoài phạm vi" ở trên để biết lý do từng mục.

**Còn nợ sau deploy:**

1. **EXPLAIN trên prod.** Xác nhận subquery `Exists` bám `ord_gtc_customer_idx`, không
   rơi về seq scan trên bảng `orders` (~455k đơn GTC). Nếu p95 vượt ~2s thì cân nhắc
   cache Redis TTL ngắn như trang resale-tracking đang làm. Lưu ý thêm từ review cuối:
   annotation `Exists` được dùng lại trong CẢ BA `filter=resale_q` (`resale_orders`,
   `resale_revenue`, `resale_customers`), nên Postgres không gói một `Exists` rồi tái
   dùng — nó nới thành BA `FILTER (WHERE ...)` riêng, mỗi cái mang một correlated
   SubPlan độc lập trong plan tổng. Việc `EXPLAIN (ANALYZE, BUFFERS)` phải soi có đúng
   3 SubPlan cùng bám index, không phải giả định 1 SubPlan dùng chung.
2. **Đối chiếu tay một kỳ.** Lấy 1 tháng, so `total_revenue` của tab này với
   `total_revenue` tab "Báo cáo theo sale" — **phải khớp tuyệt đối** (cùng queryset,
   cùng tập trạng thái). Lệch = có bug ở filter, không phải "khác định nghĩa".
3. **Giải thích trước cho phòng Sale** rằng % resale sẽ khác con số họ tự đếm trên trang
   Theo dõi resale, vì hai nơi đo hai thứ khác nhau (đơn thật vs. đơn sale tự ghi).

## Rủi ro & việc phải làm sau deploy

1. **EXPLAIN trên prod.** Xác nhận subquery `Exists` bám `ord_gtc_customer_idx`, không
   rơi về seq scan trên bảng `orders` (~455k đơn GTC). Nếu p95 vượt ~2s thì cân nhắc
   cache Redis TTL ngắn như trang resale-tracking đang làm.
2. **Đối chiếu tay một kỳ.** Lấy 1 tháng, so `total_revenue` của tab này với
   `total_revenue` tab "Báo cáo theo sale" — **phải khớp tuyệt đối** (cùng queryset,
   cùng tập trạng thái). Lệch = có bug ở filter, không phải "khác định nghĩa".
3. **Giải thích trước cho phòng Sale** rằng % resale sẽ khác con số họ tự đếm trên trang
   Theo dõi resale, vì hai nơi đo hai thứ khác nhau (đơn thật vs. đơn sale tự ghi).
