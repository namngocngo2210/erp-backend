# Tab Resale — đổi gốc sang kênh Resale, 4 rổ theo Báo cáo tổng

Ngày: 2026-08-26 · Nhánh: `feat/resale-channel-report` · Thay thế thiết kế
`2026-08-25-sale-resale-report-design.md` (đã deploy, MR !1366)

## Vấn đề

Tab Resale bản đầu suy luận "đơn resale" từ lịch sử mua: đơn của khách đã có ít nhất một
đơn giao thành công trước đó, trong phạm vi kênh ad-driven của Báo cáo Sale. Đo trên prod
kỳ 01–25/08/2026 cho thấy hai chỗ hỏng:

| | Đơn | Doanh thu |
|---|---:|---:|
| Tab đang hiển thị | 148 | 258.650.000 |
| Resale thật toàn công ty | 441 | 828.132.999 |

Tab phủ **31%**. Nguyên nhân: phạm vi báo cáo Sale là "kênh CÓ gán nhân viên phòng Ads",
nên mọi kênh khác bị loại — kể cả những kênh sinh nhiều đơn mua lại nhất:

| Kênh bị loại | Tổng đơn | Đơn khách mua lại |
|---|---:|---:|
| 108238 | 977 | 94 |
| **Resale** | 82 | **54** |
| DELAX MASSAGER | 953 | 44 |
| Hàng mẫu KOC | 94 | 31 |

Nghịch lý đắt nhất: **kênh tên đúng là "Resale" không hề xuất hiện trong tab Resale.**

Và ngay cả khi vá phạm vi, cách suy luận vẫn sai bản chất nghiệp vụ. Công ty **không**
định nghĩa resale bằng lịch sử mua — sale tạo đơn resale trên một kênh riêng. Đó mới là
nguồn sự thật; suy luận từ lịch sử chỉ là đoán lại điều đã được ghi rõ.

## Quyết định thiết kế

**Kênh LÀ định nghĩa.** Đơn resale = đơn thuộc kênh Resale. Bỏ hẳn subquery
`Exists(prior_gtc)` — vừa đúng nghiệp vụ hơn, vừa bỏ được một correlated subquery mỗi
hàng. Hệ quả: đơn khách cũ mua qua Facebook không còn tính là resale, và đơn khách MỚI
mua qua kênh Resale thì có. Đây là đảo ngược có chủ ý so với bản !1366.

**Bốn rổ bê nguyên từ Báo cáo tổng**, không tự chế rổ mới. Đây là điều kiện để hai trang
đặt cạnh nhau không chỏi số, và để rổ cộng khít tổng:

| Rổ | Điều kiện | Đo prod 01–25/08 |
|---|---|---:|
| **Tổng doanh thu** | `~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q` | 82 đơn · 142.889.000 |
| Giao thành công | `status = 3` | 70 đơn · 122.760.000 |
| Chờ giao | phần bù: không thuộc 3 / hoàn / rủi ro hoàn | 9 đơn · 16.340.000 |
| Hoàn | `status IN (4,5,12,13)` | 1 đơn · 1.999.000 |
| Rủi ro hoàn | `status = 10` | 2 đơn · 1.790.000 |

70+9+1+2 = 82 ✓ · 122,76+16,34+1,999+1,79 = 142,889tr ✓

**Rổ "Rủi ro hoàn" được thêm dù user chỉ liệt kê 3 rổ.** Thiếu nó thì 4 rổ không cộng
khít tổng nữa và mọi ô % mất mẫu số chung. Báo cáo tổng tách rổ này có chủ đích: đơn đã
quay đầu một lần (mã 10) có nguy cơ thành hoàn cao hơn hẳn đơn đang giao bình thường.

**Rổ "Chờ giao" định nghĩa bằng PHÉP TRỪ, không liệt kê dương.** `Order.status` là
`PositiveSmallIntegerField` không ép choices ở tầng DB, và luồng import KiotViet ghi
thẳng `int(status_val)` từ payload. Mã trạng thái lạ phải rơi vào MỘT rổ, không được rớt
khỏi cả bốn và làm tổng lệch. Đây là lý do Báo cáo tổng làm vậy — bắt chước y nguyên.

**Doanh thu = mọi đơn ĐÃ LÊN HÓA ĐƠN KiotViet, trừ đơn huỷ**, KHÔNG gác bằng 4 mã trạng
thái sale như bản !1366. Cùng ranh giới với Báo cáo tổng. Đo prod: kênh 24 có **0 đơn
lead** trong kỳ nên đổi ranh giới này không làm rơi đơn nào — nhưng vẫn phải viết đúng để
không lệch khi kênh có đơn chưa lên hoá đơn.

**Dùng chung `SALE_FB_CHANNEL_IDS` (user chốt 2026-08-26).** Kênh id 24 mang tên "Resale"
nhưng `code = 'sale-fb'`, và biến env này đang nuôi báo cáo "doanh thu Sale FB".

> ⚠️ **Rủi ro đã ghi nhận và chấp nhận:** ai thêm kênh vào `SALE_FB_CHANNEL_IDS` cho báo
> cáo Sale FB sẽ **âm thầm đổi số tab Resale**. Phải để cảnh báo ngay tại chỗ đọc biến.
> Lối thoát nếu sau này cần tách: thêm `RESALE_CHANNEL_IDS` mặc định bằng chính biến này.

**Đọc hằng số qua helper `resale_channel_ids()` trong `shared.py`, KHÔNG
`from .shared import SALE_FB_CHANNEL_IDS`.** Đây không phải chuyện phong cách:
`backend/conftest.py` monkeypatch `apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS`
thành `(9_999_000_000,)` cho **toàn bộ** suite (để pk `Channel` tự tăng không tình cờ
chạm 24 rồi làm kênh vô can biến mất khỏi báo cáo ads). Import trực tiếp sẽ **bind giá
trị lúc import**, monkeypatch không ăn, và test sẽ XANH GIẢ trong khi prod chạy khác.
Helper đọc biến module tại thời điểm gọi thì monkeypatch ăn đúng.

**Không lọc theo phòng ban nhân viên.** Bản !1366 lọc `staff.department = SALES`. Đo prod:
cả 5 người chạy kênh 24 đều thuộc phòng Sales nên bộ lọc không loại ai — nhưng nó vô
nghĩa ở đây vì phạm vi đã do kênh quyết định, và giữ lại chỉ tạo rủi ro âm thầm mất
người khi CSKH tham gia bán resale. Đơn không có `staff_id` gom vào một hàng "Không xác
định" thay vì biến mất.

**Loại đơn clone khôi phục còn sống**, y như Báo cáo tổng: clone kế thừa `created_at` của
đơn gốc nên rơi vào CÙNG kỳ; không loại thì đơn gốc và clone cùng cộng vào tổng. Dùng
`Exists()` chứ KHÔNG `.exclude(restored_orders__deleted_at__isnull=True)` — lookup
`__isnull=True` xuyên quan hệ to-many dùng LEFT OUTER JOIN nên khớp cả đơn không có bản
khôi phục nào, và `exclude()` khi đó xoá gần sạch bảng.

## Phạm vi

### 1. Backend — `apps/orders/reporting/shared.py`

Thêm helper (đặt cạnh `sale_report_channel_ids`):

```python
def resale_channel_ids() -> tuple[int, ...]:
    """Ids kênh Resale — hiện DÙNG CHUNG với ``SALE_FB_CHANNEL_IDS`` (kênh id 24
    tên "Resale", code 'sale-fb').

    ⚠️ Thêm kênh vào ``SALE_FB_CHANNEL_IDS`` cho báo cáo Sale FB sẽ ĐỔI LUÔN số
    của tab Resale. Cần tách thì thêm env ``RESALE_CHANNEL_IDS`` riêng.

    Đọc biến module TẠI THỜI ĐIỂM GỌI, không bind lúc import: ``conftest.py``
    monkeypatch hằng số này cho cả suite, import trực tiếp sẽ khiến test xanh giả.
    """
    return SALE_FB_CHANNEL_IDS
```

### 2. Backend — viết lại `apps/orders/reporting/resale_reports.py`

`summary_resale_by_staff(date_from, date_to)` — bỏ tham số `sale_channel_ids` (không còn
dùng scope kênh Ads). Trả **dict** thay vì list:

```python
{
  "kpis":     { <10 khoá rổ> },
  "by_staff": [ { staff_id, staff__username, staff__display_name, <10 khoá rổ> } ],
}
```

**10 khoá rổ = 5 cặp (tiền, số đơn)**, tên khớp từng chữ với `summary_overview` để hai
báo cáo đối chiếu được mà không phải dịch tên: `total_revenue` / `total_orders`,
`delivered_revenue` / `delivered_orders`, `awaiting_revenue` / `awaiting_orders`,
`returned_revenue` / `returned_orders`, `return_risk_revenue` / `return_risk_orders`.

Base queryset:

```python
base = cls._apply_created_at_date_filters(
    cls._exclude_hidden_branches(Order.objects.filter(deleted_at__isnull=True)),
    date_from, date_to,
).exclude(branch_id__in=OVERVIEW_EXCLUDED_BRANCH_IDS)
base = base.exclude(Exists(Order.objects.filter(
    restored_from_id=OuterRef("pk"), deleted_at__isnull=True)))
base = base.filter(channel_id__in=resale_channel_ids())
```

Các `Q` giống hệt `summary_overview`, **không sao chép hằng số**:

```python
total_q       = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q
delivered_q   = total_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
awaiting_q    = total_q & ~Q(status__in=(DELIVERED_SUCCESS_ORDER_STATUS,
                                         *RETURN_ORDER_STATUSES,
                                         *RETURN_RISK_ORDER_STATUSES))
returned_q    = total_q & Q(status__in=RETURN_ORDER_STATUSES)
return_risk_q = total_q & Q(status__in=RETURN_RISK_ORDER_STATUSES)
```

`kpis` lấy bằng `base.aggregate(...)`; `by_staff` bằng
`.values("staff_id","staff__username","staff__display_name").annotate(...)`, sắp theo
`-delivered_revenue`. Mọi cột tiền trả về **chuỗi decimal** (`str(v or Decimal("0"))`).

**Chỉ số hiệu năng.** Không cần index mới: `ord_act_ch_st_cr_idx`
— `(channel_id, status, created_at DESC) WHERE deleted_at IS NULL` — phủ đúng
`channel_id = ? AND created_at BETWEEN ?` của query này. Tập đơn cũng nhỏ (~82 đơn/tháng)
so với bản cũ quét toàn bộ kênh sale.

### 3. Backend — endpoint

Giữ nguyên URL `/api/orders/reports/resale-by-staff` và quyền `require_section("dashboard")`.
Đổi payload từ `{"by_staff": [...]}` sang `{"kpis": {...}, "by_staff": [...]}`.
Bỏ `OrderRepository.ads_channel_scope()` khỏi view (không còn dùng).
`extend_schema` khai lại; `schema.yaml` sửa TAY, không regen.

### 4. Frontend

| File | Việc |
|---|---|
| `sale-report/queries.ts` | đổi `ResaleStaffApiRow` sang 10 khoá rổ; thêm `ResaleKpisApi`; `SaleResaleReportPayload` có thêm `kpis` |
| `sale-report/resale-selectors.ts` | viết lại: `ResaleStaffRow` mang 5 cặp rổ + `deliveredPct`; thêm `buildResaleKpis` |
| `sale-report/components/ResaleKpiTiles.tsx` (mới) | 5 ô KPI, bám khuôn `OverviewKpiTiles` (ô Hoàn / Rủi ro hoàn `tone="danger"`) |
| `sale-report/components/ResaleRevenueChart.tsx` | đổi trục sang **doanh thu giao thành công**; giữ nguyên cách key theo `row.key` + `tickFormatter` (đã sửa ở !1366 để 2 sale trùng tên không chồng tick) |
| `sale-report/components/SaleResaleTab.tsx` | KPI tiles → chart → bảng |
| `sale-report/__tests__/*` | cập nhật theo shape mới |

Bảng theo nhân viên: **Sale · Tổng đơn · Tổng DT · Giao TC (đơn) · Giao TC (tiền) · Chờ
giao · Hoàn · Rủi ro hoàn · % giao TC**. Có dòng tổng.

**Bỏ cột "Khách quay lại"** — khi phạm vi đã là một kênh thì "khách khác nhau trong kênh
Resale" không còn trả lời câu hỏi nào mà 4 rổ chưa trả lời, và dòng Tổng của nó vẫn đếm
trùng. Bỏ luôn cả footnote cảnh báo đếm trùng đi kèm.

Giữ nguyên vị trí tab (thứ 2) và việc dùng chung bộ lọc ngày của trang.

### 5. Test

**Backend** — viết lại class `SaleResaleByStaffTests` + `SaleResaleReportApiTests` trong
`apps/orders/tests/test_sale_reports.py`:

| Ca | Kỳ vọng |
|---|---|
| **Monkeypatch `shared.SALE_FB_CHANNEL_IDS` về kênh test** | bắt buộc — conftest đã đặt `(9_999_000_000,)` cho cả suite |
| Đơn ngoài kênh Resale | không vào bất kỳ rổ nào |
| Đơn trong kênh, `status=3` | vào `delivered` |
| `status=2` (và một mã LẠ chưa có trong enum) | vào `awaiting` — chứng minh phép trừ hoạt động |
| `status IN (4,5,12,13)` | vào `returned` |
| `status = 10` | vào `return_risk` |
| Đơn huỷ (`status=6` hoặc `invoice_status=2`) | không vào rổ nào |
| Đơn lead (`invoice_status IS NULL`) | không vào rổ nào |
| **Bất biến** | `delivered + awaiting + returned + return_risk = total`, cả tiền lẫn đơn |
| Đơn có clone khôi phục còn sống | đơn gốc bị loại, không cộng đôi |
| Đơn không có `staff_id` | vẫn vào `kpis`, và có hàng riêng trong `by_staff` |
| `kpis` = tổng các hàng `by_staff` | cộng ngang phải khít |
| Quyền | user không có section `dashboard` → 403 |

**Frontend** — selector: parse decimal, `deliveredPct` null khi mẫu số 0, dòng tổng tính
lại từ tổng. Component: loading / lỗi / rỗng, và một test khẳng định KPI tiles hiện.
Chart KHÔNG test bằng jsdom (`ResponsiveContainer` đo 0×0).

## Ngoài phạm vi (cố ý)

- Không thêm env mới, không sửa docker (dùng lại `SALE_FB_CHANNEL_IDS` đã có ở
  `docker-compose.prod.yml`).
- Không đụng báo cáo "doanh thu Sale FB" đang dùng chung biến đó.
- Không xuất Excel, không biểu đồ diễn biến theo ngày.
- Không thêm quyền RBAC.
- Không đổi 3 tab còn lại của trang.

## Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| **S1** | helper `resale_channel_ids()` + viết lại `resale_reports.py` + test repository | ✅ xong |
| **S2** | endpoint payload mới + `schema.yaml` + test API/quyền | ✅ xong |
| **S3** | `queries.ts` + `resale-selectors.ts` + test selector | ✅ xong |
| **S4** | `ResaleKpiTiles` + chart + tab + test component | ✅ xong |
| **S5** | lint, chạy test 2 phía, ghi nhận, push + MR | ✅ xong |

S1 và S3 độc lập (S3 chỉ cần hợp đồng payload). S2 phụ thuộc S1; S4 phụ thuộc S3.

### Ghi nhận sau khi thực hiện (2026-08-26)

**Đã làm:** S1–S5 trọn vẹn. Thêm helper `resale_channel_ids()` trong
`apps/orders/reporting/shared.py`, đọc `SALE_FB_CHANNEL_IDS` tại thời điểm gọi (ăn
monkeypatch của `conftest.py`). Viết lại `summary_resale_by_staff` trả về
`{kpis, by_staff}` với 5 rổ theo kênh Resale (giống hệt Q-composition của
`summary_overview`), bỏ tham số `sale_channel_ids` không còn dùng. Endpoint
`/api/orders/reports/resale-by-staff` đổi payload theo shape mới, `schema.yaml` sửa tay.
FE có `ResaleKpiTiles` (5 ô KPI), chart doanh thu giao thành công, và bảng theo nhân viên
kèm dòng tổng; bỏ cột "Khách quay lại" theo đúng thiết kế.

Số test thật (đếm trực tiếp trong file, không suy đoán):
- Backend: `SaleResaleByStaffTests` 13 test + `SaleResaleReportApiTests` 4 test = **17 test**.
- Frontend: `resale-selectors.test.ts` 11 test + `SaleResaleTab.test.tsx` 5 test = **16 test**.

**Chưa làm (cố ý):** env `RESALE_CHANNEL_IDS` riêng (đang dùng chung
`SALE_FB_CHANNEL_IDS`), xuất Excel, biểu đồ diễn biến theo ngày.

**Còn nợ sau deploy:**
1. **SỐ SẼ TỤT HƠN MỘT NỬA — phải báo phòng Sale TRƯỚC khi deploy.** Kỳ 01–25/08:
   148 đơn / 258,65tr → **82 đơn / 142,89tr** (giao thành công **122,76tr**). Số nhân
   viên trong bảng từ **7 xuống 5** — ba người biến mất vì không chạy kênh Resale. Không
   báo trước thì đây trông y hệt một sự cố mất số.
2. **Số của kỳ gần đây sẽ còn tăng sau khi deploy.** 9 đơn đang giao, giao xong sẽ nhảy
   sang rổ "Giao thành công" của chính kỳ đã TẠO đơn.
3. **Đối chiếu tay một kỳ** với Báo cáo tổng lọc kênh Resale: 4 rổ phải khớp tuyệt đối.
   Lệch = có bug ở filter, không phải khác định nghĩa.
4. **Nợ kỹ thuật tồn đọng:** id kênh hard-code (`SALE_FB_CHANNEL_IDS`, `KOC_CHANNEL_ID`)
   vẫn là mìn ở môi trường khác — kênh nào nhận đúng id đó sẽ âm thầm biến mất khỏi báo
   cáo. Cần một MR riêng chuyển sang nhận diện theo `code`. Không xử ở đây.

## Rủi ro & việc phải làm sau deploy

1. **SỐ SẼ TỤT HƠN MỘT NỬA — phải báo phòng Sale TRƯỚC khi deploy.** Kỳ 01–25/08:
   148 đơn / 258,65tr → **82 đơn / 142,89tr** (giao thành công **122,76tr**). Số nhân
   viên trong bảng từ **7 xuống 5** — ba người biến mất vì không chạy kênh Resale. Không
   báo trước thì đây trông y hệt một sự cố mất số.
2. **Số của kỳ gần đây sẽ còn tăng sau khi deploy.** 9 đơn đang giao, giao xong sẽ nhảy
   sang rổ "Giao thành công" của chính kỳ đã TẠO đơn. Xem báo cáo tháng 8 hôm nay và đầu
   tháng 9 ra hai số khác nhau — bản chất của việc lọc theo ngày tạo đơn, Báo cáo tổng
   cũng vậy. Cột "Chờ giao" đứng cạnh chính là để giải thích phần chênh.
3. **Đối chiếu tay một kỳ** với Báo cáo tổng lọc kênh Resale: 4 rổ phải khớp tuyệt đối.
   Lệch = có bug ở filter, không phải khác định nghĩa.
4. **Nợ kỹ thuật tồn đọng:** id kênh hard-code (`SALE_FB_CHANNEL_IDS`, `KOC_CHANNEL_ID`)
   vẫn là mìn ở môi trường khác — kênh nào nhận đúng id đó sẽ âm thầm biến mất khỏi báo
   cáo. Cần một MR riêng chuyển sang nhận diện theo `code`. Không xử ở đây.
