# Thiết kế: Nhắc lịch hẹn resale qua Lark

- **Ngày:** 2026-05-26
- **Branch:** `resale-appointment-lark-reminder-20260526`
- **Phạm vi:** Backend (Django app `orders` + `customers`)

## 1. Bối cảnh & vấn đề

Trang theo dõi resale ([ResaleTrackingPage.tsx](../../../frontend/src/pages/ResaleTrackingPage.tsx))
cho phép sale đặt lịch hẹn với khách cũ (`CustomerResaleFollowUp.appointment_at`).
Khác với luồng sale-flow (`OrderAssessment.next_followup_at` → có bot Lark nhắc),
lịch hẹn resale **chỉ lưu DB, không có thông báo nào**. Sale dễ quên hẹn.

Các phần đã tích hợp Lark để so sánh: đơn mới, sale reminder (D+2/4/7, hẹn lịch,
gọi lại sau, chốt), nhắc gom "không liên lạc" (KNM), sync doanh thu KiotViet & FB Ads.

## 2. Mục tiêu

Khi sale đặt/sửa lịch hẹn resale, hệ thống tự lên lịch nhắc qua Lark **đúng giờ hẹn**,
@mention **sale của đơn** (`order.staff`) — nhất quán với mọi reminder hiện có.

Phi mục tiêu (YAGNI): không nhắc khi tick các mốc liên hệ (`contact_n`), không nhắc
khi tạo resale order note, không tạo bot/cron/webhook mới.

## 3. Nguyên tắc thiết kế

Tái dùng toàn bộ hạ tầng `SaleReminder` sẵn có:

- Bảng `sale_reminders` + cron `process_sale_reminders` (chạy mỗi 60s) đã xử lý
  **mọi `kind`** một cách generic: pull row PENDING có `due_at <= now`, render text,
  gửi qua `send_sale_reminder_text`, đánh dấu SENT/giữ PENDING để retry.
- Rào `OrderSource.LANDING` **chỉ** nằm ở tầng scheduler `reschedule_for_assessment`
  (luồng sale-flow), **không** nằm ở cron. Vì vậy chỉ cần tạo `SaleReminder` đúng
  cách là cron tự gửi — không phải sửa cron.

Logic lên lịch thuộc về app `orders` (sở hữu `SaleReminder`); app `customers` chỉ gọi vào.

## 4. Thay đổi chi tiết

### 4.1 Model — `apps/orders/models.py`
Thêm 1 giá trị vào `SaleReminderKind`:
```python
RESALE_APPOINTMENT = "resale_appointment", "Hẹn resale"
```
Chỉ đổi `choices` (không đổi schema cột `kind`), Django sinh migration `AlterField`.

### 4.2 Scheduler — `apps/orders/application/services/sale_reminder_service.py`
Thêm hàm public:
```python
@transaction.atomic
def schedule_resale_appointment_reminder(*, order, appointment_at, actor, note="") -> SaleReminder | None
```
Hành vi:
1. Cancel mọi `SaleReminder` PENDING `kind=RESALE_APPOINTMENT` của order
   (reason=`resale_appointment_changed`). Chỉ đụng đúng kind này → không ảnh hưởng
   reminder sale-flow của cùng order.
2. Nếu `appointment_at` là None hoặc `<= now` → chỉ cancel, log, return `None`
   (không nhắc cho lịch quá khứ).
3. Ngược lại tạo `SaleReminder(order, staff=order.staff, kind=RESALE_APPOINTMENT,
   due_at=appointment_at, status=PENDING, note_snapshot=note)` và return row.

Không đi qua `reschedule_for_assessment` → không bị chặn bởi rào LANDING.

### 4.3 Template — `apps/orders/infrastructure/sale_reminder_templates.py`
Thêm `build_resale_appointment_message(reminder)` và đăng ký vào `_BUILDERS`.
Nội dung: @mention `order.staff`, tên/SĐT khách, giờ hẹn (`due_at`), và `note_snapshot`
(mục đích · kênh). Văn phong chăm khách cũ (không tư vấn lại từ đầu).

### 4.4 Tích hợp — `apps/customers/services.py`
Cuối `update_resale_appointment()`, sau `save()`, gọi:
```python
schedule_resale_appointment_reminder(
    order=order, appointment_at=appointment_at, actor=updated_by,
    note=" · ".join(p for p in [purpose, channel] if p),
)
```

## 5. Hành vi cập nhật lại
- Sửa giờ hẹn → cancel reminder cũ, tạo mới.
- Xoá giờ hẹn (None) hoặc đặt vào quá khứ → cancel, không nhắc.

## 6. Edge cases
- `order.staff` null → builder fallback `[Sale]` (đã có sẵn trong `_mention`).
- Lark gửi lỗi → cron giữ PENDING, retry tick sau (cơ chế sẵn có).
- Nhiều lần cập nhật liên tiếp → luôn chỉ tồn tại tối đa 1 reminder PENDING/đơn.

## 7. Test
- Scheduler: tạo mới; cancel-then-create khi gọi lại; skip khi None; skip khi quá khứ;
  không đụng reminder sale-flow của cùng order.
- Builder: render đúng tên khách + giờ + note; fallback khi staff null.

## 8. Không đụng tới
Luồng sale-flow reminders, no-answer KNM, KiotViet/FB Ads bitable sync, frontend
(API response của `update_resale_appointment` giữ nguyên).
