# Thiết kế: Báo cáo khách hàng Sprint 3 — Hành động được (tái kích hoạt, xu hướng, chất lượng kênh)

**Ngày:** 2026-06-30
**Branch:** `feat/customer-report-redesign`
**Trạng thái:** Spec — chuyển thẳng sang plan + thực thi
**Tiền thân:** Sprint 1/2 (báo cáo + bức tranh toàn cảnh) + perf snapshot. Sprint 3 thêm 3 khối *hành động được*.

## 1. Bối cảnh

Báo cáo hiện trả lời "ai / bao nhiêu / có quay lại không" nhưng yếu ở **tiền theo thời gian, chất lượng kênh, và việc cần làm**. 3 khối mới — tất cả tính từ `orders`/RFM (đáng tin, không phụ thuộc assessment 2,2%) và **precompute trong cron 2h** (nối hạ tầng `customer_segment_snapshot` đã có) nên không làm chậm trang.

## 2. Feature A — Tái kích hoạt (ưu tiên #1)

**Mục tiêu:** biến 92,6% khách ngủ đông thành cơ hội có thể giao việc.

**Backend** `reactivation_service.py`:
- `value_at_risk()` → từ `customer_segment_snapshot`: tổng `total_spent` + đếm khách của nhóm `at_risk` (180–365 ngày) và `dormant` (>365 ngày). Tính lifecycle từ `last_purchase_at` vs now (giống behavior report).
- `worklist(lifecycle, limit, offset)` → khách thuộc nhóm chọn, **xếp `total_spent` giảm dần** (từ snapshot, indexed), join `Customer` (tên/SĐT/mã) + đơn mới nhất (SP gần nhất để gợi ý chào lại). Phân trang.
- Endpoint: `GET /api/customers/report/reactivation?lifecycle=dormant|at_risk&limit=&offset=` → `{ value_at_risk: {...}, results: [...], total }`.

**Nối module resale có sẵn:** báo cáo *chỉ ra ai cần gọi*; mỗi dòng worklist link sang hồ sơ khách / trang resale (`/api/customers/resale`) để sale theo dõi liên hệ. KHÔNG dựng tracking mới.

**Frontend:** tab **"Tái kích hoạt"** trong report:
- Thẻ "Giá trị đang nguy cơ" (tổng tiền + số khách at_risk/dormant).
- Bảng worklist: tên, SĐT, tổng chi tiêu, ngày mua cuối, số ngày ngủ đông, SP gần nhất; nút "→ Hồ sơ / Resale". Bộ lọc lifecycle + phân trang.

## 3. Feature C — Xu hướng theo kỳ (ưu tiên #2)

**Mục tiêu:** biết đang tốt lên hay xấu đi.

**Backend** `report_trends.py` (precompute → bảng `ReportTrendSnapshot(scope pk, payload jsonb, updated_at)`):
- Chuỗi 12 tháng: mỗi tháng → khách mới (đơn GTC đầu tiên trong tháng), số người mua, số đơn, doanh thu, AOV, số khách mua-lại-trong-tháng.
- KPI so kỳ: tháng hiện tại vs tháng trước (Δ%).
- Cron 2h gọi `report_trends.rebuild_snapshot()`; endpoint đọc payload (fallback live).
- Gắn vào report summary key `trends` (unfiltered) hoặc endpoint riêng `GET /api/customers/report/trends`.

**Frontend:** tab/section **"Xu hướng"**: biểu đồ đường (recharts) doanh thu + người mua + tỷ lệ mua lại theo tháng; hàng KPI có mũi tên ▲▼ so kỳ trước.

## 4. Feature B — Chất lượng kênh (ưu tiên #3)

**Mục tiêu:** kênh nào mang khách *dính* vs *mua-một-lần* → phân bổ ngân sách.

**Backend** `channel_quality.py` (precompute → `ReportChannelQualitySnapshot` hoặc dùng chung bảng trend-style):
- Theo **kênh acquire** (kênh của đơn GTC đầu tiên mỗi khách): số khách acquire, **% trở thành mua-lại (≥2 GTC)**, đơn TB/khách, doanh thu/khách, % ngủ đông.
- Cron 2h precompute; endpoint `GET /api/customers/report/channel-quality`.

**Frontend:** tab/section **"Chất lượng kênh"**: bảng theo kênh, sort được theo % mua-lại / doanh thu-trên-khách; highlight kênh tốt/xấu.

## 5. Phạm vi & không làm
- Tất cả từ `orders`/snapshot — KHÔNG dùng assessment cho 3 khối này.
- Precompute vào cron 2h hiện có (`rebuild_segment_snapshot` task gọi thêm rebuild của trends + channel).
- Không dựng module resale mới (tái dùng).
- Query có filter kênh/ngày của 3 khối này: ưu tiên precompute scope mặc định; filter nâng cao là follow-up.

## 6. Sprint/thứ tự
1. **A — Tái kích hoạt** (rẻ nhất, dữ liệu sẵn trong snapshot, chạm nỗi đau #1).
2. **C — Xu hướng** (precompute đơn giản).
3. **B — Chất lượng kênh** (nặng nhất, giá trị chiến lược).

## 7. Rủi ro
- Snapshot trễ ≤2h (chấp nhận, ghi "cập nhật lúc").
- worklist join per-page (như `list_profiles`) → tránh kéo toàn bộ.
- Channel-acquire cần "đơn GTC đầu tiên mỗi khách" — precompute, không tính realtime.
