# Thiết kế: Trang đơn hàng Landing — 3 Sheet (1/2/3)

- **Ngày:** 2026-06-02
- **Branch:** `orders-landing-3sheet-redesign-20260602`
- **Trạng thái:** Đã duyệt thiết kế, chờ review spec → writing-plans

## 1. Mục tiêu

Tổ chức lại màn đơn hàng Landing để team Sale làm việc theo **nguồn + độ đầy đủ thông tin** thay vì một danh sách phẳng. Thay riêng tab "Đơn hàng sale" bằng 3 sheet; giữ nguyên các tab "Giao thành công", "Đã hủy", "Blocklist".

Cấu trúc tab mới: **Sheet 1 | Sheet 2 | Sheet 3 | Giao thành công | Đã hủy | Blocklist**.

## 2. Logic phân loại 3 Sheet

Đơn được tự động xếp vào sheet dựa trên `order_source` + độ đầy đủ thông tin.

| Điều kiện | Sheet |
|---|---|
| `order_source` ∈ {landing, hotline, zalo} **VÀ** đủ thông tin | **Sheet 1** |
| `order_source` = messenger **VÀ** đủ thông tin | **Sheet 2** |
| Bất kỳ nguồn nào (landing/hotline/zalo/messenger) **VÀ** chưa đủ thông tin | **Sheet 3** |

- **"Đủ thông tin"** = có **Tên** + **SĐT** + **Địa chỉ là text bất kỳ không trống** (không yêu cầu chuẩn hoá tỉnh/quận/phường). Cụ thể: `name` non-empty AND `phone` non-empty AND `raw_address` (hoặc `address`) non-empty sau khi trim.
- **"Chưa đủ thông tin"** = có Tên + SĐT nhưng địa chỉ trống.
- **KOC tách riêng**, không thuộc 3 sheet này (giữ trang KOC hiện tại).
- 3 sheet chỉ chứa đơn thuộc phạm vi "đơn sale" như tab cũ (không gồm đơn đã giao thành công/đã hủy/blocklist — các trạng thái này đã có tab riêng).

## 3. Sheet 1 & Sheet 2 (bảng)

Sheet 2 dùng y hệt Sheet 1, chỉ khác bộ lọc nguồn mặc định = Messenger và bỏ chips Landing/Hotline/Zalo.

### 3.1. Stats row (5 ô, click để lọc nhanh)
1. **Đơn 7 ngày** — tổng đơn tạo trong 7 ngày của sheet.
2. **Mới chưa gọi** — trạng thái sale "Mới"/chưa có log liên hệ. (highlight đỏ ô)
3. **Đã chốt**.
4. **Hẹn hôm nay** — `next_followup_at`/`appointment_at` = hôm nay.
5. **Spam / sai số**.

Click vào 1 ô = áp filter tương ứng lên bảng.

### 3.2. Cột (6 cột mặc định)
| Cột | Nội dung |
|---|---|
| Khách hàng | badge nguồn + Tên (đậm) + SĐT (gộp 1 cell) |
| Địa chỉ | 2 dòng: Tỉnh/Thành — dòng dưới Quận/Phường (xám) |
| SKU & Giá | SKU (đậm) + giá (xám) cùng cell |
| Trạng thái | badge trạng thái sale (Mới / Đã chốt / Hẹn ... / Spam) |
| Tạo lúc | **thời gian tương đối** (8p / 25p / 1h / Hôm qua) thay timestamp tuyệt đối |
| Sale | tên Sale phụ trách |

Các cột mã vận đơn/hoá đơn bỏ khỏi mặc định nhưng vẫn bật lại được qua nút chọn cột (cơ chế `visibleOrderColumns` sẵn có).

### 3.3. Highlight SLA đỏ
Đơn thuộc nhóm nguồn của sheet, **trạng thái "Mới"/chưa gọi** và `created_at` > **30 phút** → tô nền đỏ + viền trái đỏ. Đây là leading indicator của SLA. Ngưỡng 30 phút là hằng số cấu hình được.

## 4. Sheet 3 (card layout — follow-up)

Khác hẳn Sheet 1/2: không sắp theo thời gian mà theo **nhiệt độ + lịch hẹn**. Dùng card để show đủ context cho mỗi quyết định gọi.

### 4.1. Stats row
Tổng cần chăm · 🔥 Nóng · 🌤 Ấm · ❄️ Lạnh · Hẹn hôm nay.

### 4.2. Filter nhanh (nổi bật)
**Tất cả** · **📅 Có hẹn hôm nay (n)** (làm nổi bật — Sale mở app buổi sáng click vào đây đầu tiên) · Cần gọi lại <24h · ⚠ Cảnh báo >7 ngày.

### 4.3. Cấu trúc card (3 cột)
- **Cột trái:** badge nguồn + badge nhiệt độ + badge cảnh báo ngày; Tên; SĐT; "Quan tâm: SKU · giá"; "Sale: X · Đã gọi: n lần"; box lịch hẹn (nếu có).
- **Cột giữa:** dropdown "Lý do chưa chốt"; lịch sử liên hệ (nhiều dòng theo thời gian, mới nhất trên cùng — KHÔNG ghi đè).
- **Cột phải (nút):** 📞 Ghi liên hệ · 📅 Đặt hẹn · ✓ Chốt đơn (khách Nóng/Ấm) **hoặc** ✕ Đóng case (khách Lạnh).

### 4.4. Quy tắc nhiệt độ (tự động, không tick tay)

Suy ra real-time từ số lần gọi + thời gian + trạng thái sale:

- **🔥 Nóng** = số lần gọi ≤ 2 **VÀ** lần gọi gần nhất ≤ 3 ngày **VÀ** trạng thái sale tích cực.
- **🌤 Ấm** = số lần gọi 3–5 **VÀ** có tương tác nhưng chưa quyết (trạng thái tích cực, không thuộc dead).
- **❄️ Lạnh** = số lần gọi > 5 **HOẶC** > 7 ngày không liên hệ.

**Trạng thái sale "tích cực"** (suy từ `sales_status_code` sẵn có): `scheduled`, `call_back_later`, `need_more_consulting`, `closed_scheduled_date`. **Tiêu cực/dead:** `unreachable`, `wrong_number`, `spam`, `duplicate`, `not_buying`, `no_answer_3`.

Thứ tự áp dụng: kiểm tra Lạnh trước (>5 lần hoặc >7 ngày), rồi Nóng, còn lại là Ấm.

### 4.5. Số lần gọi & lần gọi gần nhất (phương án A — real-time)
- Mỗi lần "Ghi liên hệ" tạo **1 `OrderJourneyEvent` mới** với `event_type = "sale_contact"`, `payload_json` chứa `{ outcome, transcript }`, `actor` = Sale, `created_at` = thời điểm.
- **Số lần gọi** = `COUNT` event `sale_contact` của đơn.
- **Lần gọi gần nhất** = `MAX(created_at)` của event `sale_contact`.
- API Sheet 3 dùng `annotate` (Count + Max subquery) để tránh N+1; index trên `OrderJourneyEvent(order_id, event_type, created_at)`.
- Không lưu cứng cột nhiệt độ → đổi quy tắc sau không cần backfill.

### 4.6. Lý do chưa chốt (dropdown chuẩn hoá)
Giữ enum `not_closed_reason_code` cũ + **bổ sung** giá trị mới vào cùng danh sách:
- Giữ: `too_expensive` (Giá cao), `competitor_cheaper` (So sánh đối thủ), `family_disapproved`, `family_already_bought` (Đã mua chỗ khác), `wrong_phone_number`, `china_product`, `wants_bundle_with_chair`, `kids_playing`, `competitor_closed_first`.
- Bổ sung: `watching_demo` (Đang xem demo), `waiting_for_money` (Chờ tiền), `spouse_not_approved` (Vợ/chồng chưa duyệt), `no_need_anymore` (Không có nhu cầu nữa), `other` (Khác).
- Không nhập tự do → đảm bảo dữ liệu dùng được cho dashboard CEO ("top 5 lý do mất khách").

### 4.7. Ghi liên hệ (log)
Mỗi lần ghi tạo 1 log riêng (timestamp + Sale + outcome + transcript ngắn) qua `OrderJourneyEvent`, không ghi đè. Đây là dữ liệu tính conversion theo Sale và phân tích lý do mất khách.

### 4.8. Đóng case & auto-close
- Nút **Đóng case** thủ công ở khách Lạnh.
- **Tự động đóng sau 14 ngày** không có log liên hệ mới → chuyển sang trạng thái archive (win-back).
- Đơn đã đóng rời khỏi Sheet 3, lưu lại để chạy win-back campaign sau.
- Cơ chế: cron Go-native (theo pattern KV cron hiện có) quét đơn Sheet 3 có lần liên hệ gần nhất > 14 ngày.

## 5. Thay đổi backend

- **Model:** không thêm cột nhiệt độ. Bổ sung giá trị enum `not_closed_reason_code` (crm_catalog). Định nghĩa hằng `SALE_CONTACT` cho `event_type`. Trạng thái archive cho đóng case (dùng `sales_status_code` mới `closed_lost`/`archived` hoặc cờ riêng — chốt khi writing-plans).
- **API:**
  - `POST /orders/landing` mở rộng tham số phân sheet: `sheet=1|2|3` (server tự dịch ra điều kiện nguồn + đủ/thiếu địa chỉ), hoặc filter `info_complete=true/false` + `order_source`.
  - Sheet 3 trả thêm: `contact_count`, `last_contact_at`, `temperature` (computed), `next_followup_at`.
  - Endpoint ghi liên hệ: `POST /orders/{id}/contact-log` `{ outcome, transcript, sales_status?, not_closed_reason? }`.
  - Endpoint đóng case: `POST /orders/{id}/close-case` `{ reason }`.
  - Stats counts cho mỗi sheet (mở rộng `salesStatusCounts` sẵn có).
- **Cron:** auto-close 14 ngày.

## 6. Thay đổi frontend

- `LandingTabKey` đổi thành `sheet1 | sheet2 | sheet3 | delivered | cancelled | blocklist`; cập nhật `LANDING_TABS`.
- Sheet 1/2: tái dùng `OrdersTable`, cấu hình lại cột mặc định (gộp cell Khách hàng / Địa chỉ / SKU&Giá), thêm cột "Tạo lúc" dạng thời gian tương đối, logic highlight row SLA đỏ.
- Stats row component (tái dùng cho cả 3 sheet, props khác nhau).
- Sheet 3: component card mới (`Sheet3FollowUpCard`), filter nhanh, dropdown lý do, lịch sử liên hệ, dialog "Ghi liên hệ" / "Đặt hẹn" / "Đóng case".
- Helper `formatRelativeTime()` (8p / 25p / 1h / Hôm qua).
- Helper `deriveTemperature()` phía hiển thị (đồng bộ với backend; nguồn chân lý là backend).

## 7. Kiểm thử

- **Backend:** unit test phân sheet (đủ/thiếu địa chỉ, từng nguồn, KOC bị loại); test annotate contact_count/last_contact_at; test quy tắc nhiệt độ (các biên ≤2/3-5/>5 lần, ≤3/>7 ngày); test ghi liên hệ tạo event không ghi đè; test auto-close 14 ngày; test bổ sung enum lý do.
- **Frontend:** test `formatRelativeTime`, `deriveTemperature`, render đúng badge/highlight SLA, filter "Có hẹn hôm nay".
- Lưu ý: pytest đang hỏng (BOM trong pytest.ini) → chạy bằng `manage.py test` với Postgres/Redis tạm.

## 8. Phân chia công việc (làm trong 1 đợt, theo thứ tự)

1. Backend: enum lý do + event_type `sale_contact` + index.
2. Backend: API phân sheet + annotate contact_count/last_contact/temperature.
3. Backend: endpoint ghi liên hệ + đóng case + cron auto-close.
4. Frontend: đổi cấu trúc tab + Sheet 1/2 (stats row, cột gộp, thời gian tương đối, SLA đỏ).
5. Frontend: Sheet 3 card + filter + dropdown lý do + lịch sử + các dialog.
6. Test + dọn dẹp.

## 9. Câu hỏi mở (đã chốt khi triển khai)
- Cơ chế "archive/đóng case": **thêm `sales_status_code = "archived"`** vào catalog; Sheet 3 loại đơn archived; win-back = query archived.
- Stats "Mới chưa gọi": **= 0 event `sale_contact`** (chưa từng ghi liên hệ).

## 10. Tiến độ triển khai (2026-06-02)

**Đã xong & test:**
- Backend toàn bộ (25 unit test mới pass, 0 regression):
  - Catalog: 5 lý do chưa chốt mới + `archived` status.
  - `sheet_rules.py` (đủ TT, hằng số SLA 30p / stale 7 ngày / auto-close 14 ngày).
  - Filter `info_complete` trong repository.
  - Index composite `OrderJourneyEvent(order, event_type, created_at)` + migration 0068.
  - `followup.py`: `compute_temperature` + `annotate_contact_aggregates`.
  - Endpoint `POST /api/orders/sheet3` (trả temperature/contact_count/last_contact_at + temperature_counts toàn tập).
  - Endpoint `POST /api/orders/{id}/contact-log` (append-only).
  - Endpoint `POST /api/orders/{id}/close-case` + management command `autoclose_followups` (14 ngày).
  - `list_landing_orders` nhận tham số `sheet=1|2`.
- Frontend:
  - Tái cấu trúc tab **chỉ trên trang Landing**: Sheet 1 | Sheet 2 | Sheet 3 | Giao thành công | Đã hủy | Blocklist (Orders thường + KOC giữ nguyên).
  - Routing endpoint theo scope (`landing-sheet1/2` → `/api/orders/landing?sheet`, `landing-sheet3` → `/api/orders/sheet3`).
  - Helper `formatRelativeTime` + `isSlaBreached` (8 test).
  - Sheet 3 card + stats nhiệt độ + lọc nhanh + dialog Ghi liên hệ / Đặt hẹn + Đóng case + đổi lý do. Verify trực quan bằng Chrome headless (light theme).

- Sheet 1/2 (C3):
  - `list_landing_orders` trả thêm `contact_count` mỗi dòng + `sheet_stats` (total/uncalled/appointment_today) khi `sheet=1|2` (3 test mới).
  - Stats row 5 ô click-to-filter (Tổng / Mới chưa gọi / Đã chốt / Hẹn hôm nay / Spam-sai số).
  - Highlight đỏ + viền trái đỏ dòng SLA (mới & chưa gọi >30p), gate theo `contact_count` (chỉ Sheet 1/2).
  - Cột "Tạo lúc" thời gian tương đối; gộp cell Khách hàng (nguồn+Tên+SĐT) / Địa chỉ (2 dòng) / SKU&Giá — gate theo dòng có `contact_count`.

**Cần QA trực quan với app + data thật:** bảng Sheet 1/2 (cell gộp + SLA) mới chỉ kiểm bằng tsc + unit test; component stats/card đã verify bằng Chrome headless.

**Vận hành:** đăng ký cron chạy `python manage.py autoclose_followups` 1 lần/ngày (DevOps).
