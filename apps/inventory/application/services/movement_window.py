"""Lọc bút toán kho theo NGÀY NGHIỆP VỤ mà vẫn cắt được partition.

`stock_movements` được RANGE-partition theo THÁNG trên `created_at` (xem
`PartitionService`), nên lọc thuần `occurred_at` sẽ quét mọi partition.

Nhờ bất biến `occurred_at <= created_at` (ép ở `StockCommandService`), thêm điều
kiện `created_at >= date_from` KHÔNG đổi kết quả: mọi dòng bị nó loại thêm đều có
`created_at < date_from`, kéo theo `occurred_at < date_from`, tức đằng nào cũng bị
loại — nhưng nó cắt sạch các partition cũ hơn kỳ lọc.

Cận trên không có điều kiện đối xứng (`created_at` được phép lớn hơn `date_to`),
nên các partition mới hơn kỳ lọc vẫn bị quét; đó chỉ là vài tháng gần nhất. KHÔNG
được thêm điều kiện `created_at <= date_to`: đó chính là ca "quét khớp trễ" (mã
quét ngày D nhưng ghi sổ ngày D+n) mà nhánh này sinh ra để phục vụ — thêm vào sẽ
âm thầm loại đúng các dòng đó.

So sánh phải dùng CỘT TRẦN (`__gte`/`__lt` trên datetime), TUYỆT ĐỐI không cast
`__date` lên cột: Postgres chỉ cắt được partition khi vế trái của điều kiện LÀ
chính cột khoá partition — một biểu thức như `((col) AT TIME ZONE 'Asia/Bangkok')
::date` (cách Django dịch `__date`) không bao giờ khớp, và cùng lý do đó làm
index `stockmove_reftype_occurred_idx` (tạo riêng cho query này) vô dụng. Nửa-mở
[local_midnight(d), local_midnight(d+1)) cho kết quả y hệt `__date__gte`/
`__date__lte` nhưng giữ nguyên cột trần — cùng bài học đã áp dụng ở
`contact_list_service.py` (EXPLAIN 2026-07-16).

Đây là NƠI DUY NHẤT giữ luật này — đừng chép rời ra từng service, hai bản sao sẽ
lệch nhau lúc nào không hay.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.inventory.models import StockMovement


def _as_date(value: datetime | date | str) -> date:
    """Chấp nhận `datetime`, `date` có sẵn, hoặc chuỗi `YYYY-MM-DD`.

    `datetime` phải được bắt TRƯỚC nhánh `date`: `datetime` là subclass của
    `date`, nên `isinstance(value, date)` vẫn đúng với nó — nếu để lọt xuống,
    `_local_midnight` sẽ combine lấy phần ngày theo ĐÚNG tzinfo của datetime đó
    (vd 18:00 UTC ngày 04/07 → lấy nhầm ngày UTC 04/07 thay vì ngày Bangkok
    05/07). Quy hết về giờ Bangkok bằng `timezone.localtime` trước khi lấy
    `.date()` để tránh lệch ngày âm thầm.

    Chuỗi phải parse bằng `strptime("%Y-%m-%d")`, KHÔNG dùng
    `date.fromisoformat`: `_valid_date` ở view (`flow_history_shared.py`) validate
    bằng `strptime`, vốn chấp nhận thành phần không đệm số 0 (vd "2026-7-4"),
    còn `fromisoformat` đòi zero-pad và raise `ValueError` — dùng parser chặt
    hơn ở đây biến một request đã qua validate thành lỗi 500.
    """
    if isinstance(value, datetime):
        return timezone.localtime(value).date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _local_midnight(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def local_day_bounds(
    date_from: datetime | date | str = "",
    date_to: datetime | date | str = "",
) -> tuple[datetime | None, datetime | None]:
    """Cận nửa-mở [00:00 ngày from, 00:00 ngày to+1) theo giờ địa phương.

    Dùng cho MỌI bảng cần lọc "theo ngày" trên cột datetime (không riêng
    stock_movements): so trên cột trần bằng ``__gte``/``__lt`` để index trên cột
    còn dùng được, thay vì cast ``__date`` — xem docstring module. ``None`` =
    không có cận tương ứng.
    """
    start = _local_midnight(_as_date(date_from)) if date_from else None
    end = _local_midnight(_as_date(date_to) + timedelta(days=1)) if date_to else None
    return start, end


def filter_occurred_between(
    qs: QuerySet[StockMovement],
    date_from: datetime | date | str = "",
    date_to: datetime | date | str = "",
) -> QuerySet[StockMovement]:
    start, end = local_day_bounds(date_from, date_to)
    if start:
        qs = qs.filter(occurred_at__gte=start, created_at__gte=start)
    if end:
        qs = qs.filter(occurred_at__lt=end)
    return qs
