# Xuất Excel theo bộ lọc — khắc phục lệch giữa báo cáo sale và file Excel

## Bối cảnh / vấn đề

Báo cáo sale (`dashboard_summary` → `summary_landing_delivered_by_staff`, ...) hiển thị
**số tổng hợp** đếm mọi đơn khớp tiêu chí. Còn file Excel xuất ra từ trang Đơn hàng lại
**không** chạy lại truy vấn báo cáo — endpoint `bulk-export-excel` chỉ nhận danh sách
`ids` do frontend gửi lên (`filter(id__in=ids)`).

Mà `ids` = `selectedIds` (đơn user tick), và checkbox **"Chọn tất cả"**
(`toggleSelectAllRows`) chỉ gom id của **trang đang hiển thị** (`rows`). Danh sách phân
trang server-side (mặc định 50/trang), nên khi 1 nhân viên có nhiều đơn hơn 1 trang →
Excel chỉ ra số đơn của 1 trang, lệch hẳn so với con số trên báo cáo.

→ Hai bên là hai truy vấn độc lập; file Excel không bao giờ phản ánh đủ tập đơn khớp bộ lọc.

## Giải pháp (phương án B)

Cho phép export **server-side theo bộ lọc**: khi không truyền `ids`, endpoint dựng lại
queryset bằng đúng filter của danh sách đơn (`_apply_list_filters`) và xuất **toàn bộ** đơn
khớp, không phụ thuộc phân trang.

### Backend
- `repositories.py`
  - `EXPORT_MAX_ROWS = 50000`: trần số dòng cho 1 lần xuất theo bộ lọc (openpyxl dựng
    workbook trong RAM → tránh OOM).
  - `OrderRepository.export_orders_queryset(filters)`: queryset đầy đủ (không phân trang)
    + `select_related`/`Prefetch` như export theo ids (tránh N+1).
- `_view_helpers.build_orders_filters(request)`: tách logic dựng filter dict của trang Đơn
  hàng (bỏ phân trang) để **dùng chung** giữa `list_orders` và export → không lệch logic.
  `list_orders` đã refactor để gọi helper này.
- `order_export_view.bulk_export_excel`:
  - Có `ids` → giữ nguyên hành vi cũ (xuất đúng các đơn đã chọn).
  - Không `ids` → dựng filter qua `build_orders_filters` + `_apply_user_channel_scope`,
    `export_orders_queryset`, kiểm tra trần `EXPORT_MAX_ROWS` (vượt → 400 yêu cầu thu hẹp
    bộ lọc), rồi xuất.

### Frontend
- `use-orders-data.ts`: lưu snapshot bộ lọc danh sách hiện tại (bỏ `limit/offset/sort`) vào
  `ordersExportFiltersRef` mỗi lần `loadOrders`; expose `getOrdersExportFilters()`.
  Chỉ áp dụng cho **trang Đơn hàng chính** — Landing/KOC có scope server-side riêng nên trả
  `null` (giữ luồng xuất theo lựa chọn).
- Thread `getExportFilters` qua `routed-pages` → `OrdersPage` → `OrdersTable`.
- `OrdersTable`: có đơn đã chọn → xuất đúng các đơn đó; không chọn → xuất toàn bộ theo bộ
  lọc. `ExportColumnsDialog` hiển thị `modeLabel` cho biết phạm vi sẽ xuất.

## Mở rộng 2026-08-06 — bật export-theo-bộ-lọc cho KOC / Gửi ngoài / Landing

Yêu cầu: ở MỌI trang đơn, không chọn row mà bấm "Xuất Excel" → xuất toàn bộ theo bộ lọc
hiện tại. Trước đây chỉ trang `/orders` làm được; 3 trang còn lại bị chặn vì list của
chúng dùng endpoint riêng với **filter cứng theo mode** mà `build_orders_filters` không
tái tạo (KOC khóa `order_source="koc"`, Gửi ngoài khóa `outside=true`, Landing khóa
`order_source_present` + loại koc/sàn TMĐT + sheet 1/2) — mở FE mà không sửa backend sẽ
xuất sai tập đơn.

### Backend
- `order_export_view._section_filter_overrides(request, section)`: mirror forced-filters
  của list view từng section, server tự quyết theo `section` (client không nới được);
  nhánh filter của `bulk_export_excel` merge overrides này sau `build_orders_filters`.
- Chi tiết dễ sót đã mirror đúng: KOC/Landing **không** default `show_orders="true"`
  như trang Đơn hàng (đơn ẩn vẫn ra file KOC — khớp list); Landing bỏ
  `kiotviet_channel_null`; sheet 1/2 áp `info_complete` + ràng buộc nguồn như
  `list_landing_orders`.
- Quyền `{section}:export`, record-scope, trần `EXPORT_MAX_ROWS` giữ nguyên.

### Frontend
- `use-orders-table.ts`: snapshot bộ lọc cho **mọi mode**; ngoại lệ duy nhất là Landing
  **Sheet 3** (endpoint riêng, ràng buộc ngoài bộ lọc chung — thiếu thông tin + loại
  trạng thái đã đóng) → snapshot `null`, vẫn bắt chọn row. Tab "Tổng hợp" dùng landing
  list không sheet nên xuất được bình thường.
- `getExportFilters` vốn đã thread cho cả 4 route qua `useOrdersPageProps` — không đổi.

### Test
- `test_order_export.py::BulkExportExcelSectionFilterTests`: 6 ca parity theo section
  (koc / outside / landing / sheet1 / sheet2 / orders-default-show_orders). Đồng thời trả
  món nợ docs cũ: mục tiến độ trước tick `test_export_orders_by_filter.py` nhưng file đó
  chưa từng tồn tại — nhánh export-theo-filter nay đã có test thật.
- FE `use-orders-data.export-filters.test.tsx`: snapshot có ở orders/koc/outside/sheet1
  (kèm `sheet=1`), null ở sheet3, không kẹt null khi rời sheet3.

## Sự cố 2026-08-06 — nút Xuất Excel luôn đòi chọn row (React Compiler đóng băng snapshot)

**Triệu chứng:** trên prod, trang Landing tab "Tổng hợp", lọc xong bấm "Xuất Excel" luôn
nhận toast "Vui lòng chọn ít nhất một đơn hàng…", dù snapshot bộ lọc đã có.

**Root cause (không phải deploy, không phải backend):** `useOrdersTableData` tính

```ts
const exportAllFilters = selectedIds.length === 0 ? (getExportFilters?.() ?? null) : null;
```

**trong thân render**. `getExportFilters` đọc `ordersExportFiltersRef.current` — một
`useRef`. Ref mutation không gây re-render và không phải dependency hợp lệ, nên React
Compiler (bật ở `vite.config.ts`, `target: "19"`) memo hóa biểu thức theo
`[getExportFilters, selectedIds.length]`. Cả hai đứng yên (`getExportFilters` là
`useCallback` deps rỗng; `selectedIds.length` = 0), nên cache giữ **nguyên `null` của lần
render đầu** — lúc `loadOrders` chưa xong — và không bao giờ tính lại. Bằng chứng trong
bundle prod:

```js
e[43]!==y||e[44]!==c.length ? (pe = c.length===0 ? y?.()??null : null, e[45]=pe) : pe=e[45];
const ie=pe, je=ie!==null;   // je = canExportAllFiltered, kẹt false vĩnh viễn
```

Vì phụ thuộc thời điểm mount, lỗi chỉ lộ khi vào thẳng URL/F5 (render đầu xảy ra trước
khi list load xong) — nên nó đã âm thầm tồn tại từ lần bật export-theo-filter cho trang
`/orders`, không phải do đợt mở rộng KOC/Landing sinh ra.

**Fix:** đọc snapshot **tại thời điểm bấm nút** (trong handler), chốt vào state
`pendingExportFilters` để dialog và lúc xác nhận dùng chung một phạm vi — kể cả khi list
tự reload trong lúc user đang chọn cột.

**Lỗ hổng đã để lọt:** `vitest.config.ts` dùng `react()` **không** kèm
`babel-plugin-react-compiler`, tức suite test chạy với ngữ nghĩa memo khác bản build —
cả lớp lỗi do compiler không thể bị test bắt. Đã bật compiler trong `vitest.config.ts`
cho khớp `vite.config.ts`. Test chống regression:
`useOrdersTableData.export-gate.test.tsx` khóa bất biến "handler phải tự đọc lại
snapshot khi được gọi" (đúng bản chất, không phụ thuộc compiler có bật hay không).

## Phạm vi & giới hạn (đã biết)
- Sửa này làm **Excel = toàn bộ danh sách theo bộ lọc đang áp dụng** (mọi trang). Đây là
  nguyên nhân lệch dễ thấy nhất.
- Để Excel **khớp tuyệt đối** với con số báo cáo sale còn cần cùng *scope* với báo cáo
  (`channel_id ∈ (4,5,6,7,9,28)`, `staff.role=STAFF`, `department=SALES`, gồm cả đơn
  `show_orders=False` có nguồn). Báo cáo gộp cả đơn Orders + Landing nên một export từ một
  trang danh sách vẫn chưa tái tạo 100% — nếu cần đối soát từng dòng theo báo cáo thì làm
  thêm "Xuất chi tiết" ngay trên trang báo cáo (phương án C, chưa làm).
- Landing Sheet 3: vẫn xuất theo lựa chọn (muốn mở phải nhân bản ràng buộc board tư vấn
  vào endpoint export — làm khi có nhu cầu thật).

## Tiến độ
- [x] Backend: queryset + helper + endpoint 2 chế độ + trần dòng
- [x] Frontend: snapshot filter + thread prop + 2 chế độ xuất + nhãn chế độ; tsc/eslint sạch
- [x] 2026-08-06: bật export-theo-bộ-lọc cho KOC / Gửi ngoài / Landing (trừ Sheet 3) +
  test backend `BulkExportExcelSectionFilterTests` + test FE snapshot
- [ ] (Tùy chọn) Phương án C: export chi tiết ngay trên báo cáo sale để đối soát từng dòng
- [ ] (Tùy chọn) Bật export-theo-bộ-lọc cho Landing Sheet 3
