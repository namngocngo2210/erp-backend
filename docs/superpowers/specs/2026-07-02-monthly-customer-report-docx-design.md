# Báo cáo khách hàng hàng tháng (Word .docx) — Thiết kế

- **Ngày:** 2026-07-02
- **Trạng thái:** Draft, chờ review
- **Nguồn dữ liệu:** `order_assessment` (+ `orders`, `order_needs`, `customers`)
- **Deliverable:** 1 Django management command sinh file `.docx`. Không có FE, không endpoint HTTP.

## 1. Mục tiêu

Sinh báo cáo Word hàng tháng tổng hợp từ dữ liệu chân dung khách hàng (`order_assessment`),
**trọng tâm là so sánh tháng này với tháng trước**, phục vụ ban lãnh đạo lập kế hoạch phát triển.

Ba câu hỏi báo cáo phải trả lời:

1. **Tỉ lệ note chân dung** tháng này so với tháng trước ra sao? (bao nhiêu đơn Đầy đủ / Cơ bản / Chưa note)
2. **Xu hướng khách hàng** (phân loại + nhu cầu) tháng này lệch tháng trước thế nào?
3. **Trước mua & sau mua** thay đổi ra sao so với tháng trước?

Line chart 6–12 tháng chỉ là bối cảnh cho tỉ lệ note + vài chỉ số headline, không áp cho mọi phân phối.

## 2. Bối cảnh & ràng buộc dữ liệu (QUAN TRỌNG)

- Trước đây `order_assessment` chỉ ~2.2% đơn được điền, các trường **sau mua gần như trống**.
  → Chính con số "tỉ lệ note" là thông điệp chính (KPI thúc đội sale note đủ), đồng thời giới hạn độ tin cậy
  của các phân tích nhu cầu/sau mua. Báo cáo phải **tự cảnh báo "mẫu nhỏ"** khi số đơn có dữ liệu < ngưỡng (mặc định 30).
- Bài học `customer-report-runaway-query`: KHÔNG gọi `summary()` 12 lần cho trend.
  Trend dài dùng **1 query GROUP BY tháng**. So sánh MoM chỉ gọi `summary()` **2 lần** (tháng này + tháng trước).

## 3. Phạm vi

**Trong phạm vi:**
- Management command sinh `.docx` cho 1 tháng, so sánh với tháng liền trước, kèm trend 6–12 tháng cho chỉ số headline.
- Phân tầng đơn theo mức độ note (3 tầng).
- Biểu đồ nhúng (matplotlib → PNG).

**Ngoài phạm vi (YAGNI):**
- FE / endpoint / nút tải.
- Tự động chạy theo lịch (cron) & gửi email/Lark — có thể thêm sau bằng cách gọi command trong scheduler.
- Xuất PDF/PPTX.
- Lưu file lên MinIO — chỉ ghi ra đường dẫn local (`--out`).

## 4. Giao diện command

```
python manage.py generate_monthly_customer_report --month 2026-06 [--out <path>] [--months-trend 12]
```

| Arg | Bắt buộc | Mặc định | Ý nghĩa |
|-----|----------|----------|---------|
| `--month` | ✅ | — | Tháng báo cáo `YYYY-MM` |
| `--out` | ❌ | `./reports/bao-cao-khach-hang-<YYYY-MM>.docx` | Đường dẫn file xuất |
| `--months-trend` | ❌ | `12` | Số tháng cho line chart xu hướng (gồm tháng báo cáo) |

Kết thúc: in ra stdout đường dẫn file + tóm tắt (tổng đơn, %/số đơn Đầy đủ/Cơ bản/Chưa note, delta so tháng trước).
Lỗi rõ ràng nếu `--month` sai định dạng hoặc không có đơn nào trong tháng.

## 5. Kiến trúc (clean architecture, mỗi file < 300 dòng)

```
backend/apps/customers/
  management/commands/generate_monthly_customer_report.py   # lớp ngoài mỏng: parse args → service → ghi file + stdout
  report_doc/
    __init__.py
    tiers.py         # quy tắc 3 tầng + đếm theo tháng (business rule thuần, không I/O)
    trend.py         # aggregate tỉ lệ note + headline theo tháng bằng 1 query GROUP BY month
    charts.py        # matplotlib → PNG bytes (bar so sánh 2 tháng, pie, line trend). Không side-effect toàn cục.
    docx_builder.py  # python-docx: bìa, heading, bảng so sánh, chèn ảnh, nhận xét
    service.py       # MonthlyReportDocService.build(month, months_trend) -> (bytes, summary_dict)
```

Luồng phụ thuộc: command → service → {tiers, trend, charts, docx_builder} + tái dùng `CustomerReportService.summary()`.
Không tầng nào phụ thuộc HTTP. `service` không import matplotlib trực tiếp — chỉ qua `charts`.

## 6. Luồng dữ liệu

1. Command parse `--month` → tính `(month_start, month_end)` và `(prev_start, prev_end)`.
2. `service.build()`:
   a. `cur = CustomerReportService.summary(date_from=month_start, date_to=month_end)`
   b. `prev = CustomerReportService.summary(date_from=prev_start, date_to=prev_end)`
   c. `tiers_cur, tiers_prev = tiers.count_tiers(month)`, `tiers.count_tiers(prev_month)`
      — query riêng trên **toàn bộ đơn trong tháng** (xem §7).
   d. `trend_series = trend.monthly_series(end_month=month, n=months_trend)` — 1 query.
   e. `charts.*` dựng các PNG từ (cur, prev, tiers, trend_series).
   f. `docx_builder.build(...)` ráp file → `bytes`.
   g. trả `(bytes, summary_dict)` để command ghi file + in stdout.

## 7. Phân tầng note — mẫu số & quy tắc

**Mẫu số:** TẤT CẢ đơn không xóa trong tháng, cùng scope với báo cáo hiện tại
(`deleted_at__isnull=True`, loại `EXCLUDED_BRANCH_IDS`), **left-join** `assessment`.
Khác với `CustomerReportService.summary()` (vốn dùng `assessment__isnull=False`) — vì phải đếm được cả đơn "Chưa note".

**Định nghĩa "filled" theo kiểu trường:** text = chuỗi khác rỗng; code = không null/không rỗng;
array = list khác rỗng; bool = không null; int = không null.

**Nhóm trường:**
- `CORE` = `customer_classification_code` ∧ `sales_status_code` ∧ (≥1 trong {`pain_level_code`, `delax_source_codes`, `occupation_code`, `age_text`})
- `PRE_FULL` (8): `pain_level_code`, `pain_duration_code`, `lifestyle_habit_codes`, `relax_time_codes`, `delax_source_codes`, `purchase_barrier_codes`, `comparing_brand_text`, `used_massage_before`
- `POST` = có `post_purchase_feedback_text` ∨ `post_purchase_rating_code`

**3 tầng (loại trừ lẫn nhau, xét từ cao xuống thấp):**
- **Đầy đủ** = CORE ∧ (≥5/8 PRE_FULL) ∧ (≥1 POST)
- **Cơ bản** = đủ CORE (nhưng chưa đạt Đầy đủ)
- **Chưa note** = còn lại (0 trường chân dung, hoặc chưa đủ CORE)

> Ngưỡng (5/8, danh sách CORE) là tham số hằng trong `tiers.py`, dễ chỉnh. Chốt lại khi review.

## 8. Cấu trúc file Word

1. **Bìa:** tiêu đề, tháng báo cáo, phạm vi, ngày sinh, tổng số đơn (tháng này / tháng trước).
2. **Tỉ lệ note chân dung:** bảng 3 tầng (số đơn + %) tháng này | tháng trước | delta điểm%; pie tháng này; line trend tỉ lệ Đầy đủ+Cơ bản 6–12 tháng. Nhận xét tự sinh.
3. **Phân loại khách hàng:** bảng classification + segment + potential (tháng này | tháng trước | delta); bar so sánh nhóm chính.
4. **Nhu cầu:** need_type + pain_level + purchase_barrier (tháng này | tháng trước | delta); bar.
5. **Trước mua:** nguồn biết đến (delax_source), lifestyle, so sánh brand top — so tháng trước.
6. **Sau mua:** rating, tỉ lệ đơn có feedback, vài feedback mẫu; **cảnh báo mẫu nhỏ** nếu < ngưỡng.
7. **Nhận xét & khuyến nghị:** tổng hợp delta MoM theo ngưỡng (tái dùng ý tưởng `insights`); nêu rõ điểm cần cải thiện (vd tỉ lệ note thấp, sau mua trống).

Nhận xét tự sinh dựa trên quy tắc ngưỡng đơn giản (tăng/giảm ≥ Xđ% → câu tương ứng), không dùng LLM.

## 9. Xử lý lỗi & biên

- `--month` sai định dạng → `CommandError` với hướng dẫn.
- Không có đơn trong tháng → vẫn xuất file với thông báo "không có dữ liệu", không crash.
- Tháng trước không có dữ liệu → cột delta hiển thị "—" (không chia cho 0).
- Thư mục `--out` chưa tồn tại → tạo (`makedirs`).
- Số đơn có assessment < ngưỡng `SMALL_SAMPLE = 30` → in nhãn cảnh báo "mẫu nhỏ" ở các section liên quan.

## 10. Phụ thuộc mới

- `python-docx`, `matplotlib` → thêm vào requirements backend. `matplotlib` dùng backend `Agg` (không cần GUI).
- Rebuild backend Docker image (multi-stage giữ nguyên). Command chạy trong image backend.

## 11. Test

- `tiers.py`: unit test các ca biên phân tầng (đúng đủ CORE → Cơ bản; đủ 5/8 + POST → Đầy đủ; rỗng → Chưa note; ranh giới 4/8 vs 5/8).
- `trend.py`: test gom nhóm theo tháng đúng số liệu trên dữ liệu dựng sẵn; đúng số tháng, đúng thứ tự.
- `charts.py`: smoke — hàm trả bytes PNG không rỗng.
- Command: smoke test end-to-end — chạy trên dữ liệu seed nhỏ, khẳng định file `.docx` hợp lệ mở được bằng `python-docx` và có đủ heading các section.

## 12. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| Dữ liệu quá thưa → biểu đồ vô nghĩa | Nhãn "mẫu nhỏ"; tỉ lệ note là KPI chính dù mẫu nhỏ |
| Query trend nặng | 1 query GROUP BY month, có index `created_at`; không gọi summary ×N |
| matplotlib chậm/nặng trong container | backend Agg, render tuần tự vài chart/lần, tần suất thấp |
| Quy tắc 3 tầng chưa khớp kỳ vọng | Hằng số tập trung trong `tiers.py`, chốt khi review |
