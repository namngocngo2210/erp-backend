"""Quy tắc phân Sheet 1/2/3 và follow-up cho trang Landing."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import Q

SHEET_SLA_MINUTES = 30          # đơn mới chưa gọi quá hạn → highlight đỏ
FOLLOWUP_STALE_DAYS = 7         # cảnh báo lâu không liên hệ
FOLLOWUP_AUTOCLOSE_DAYS = 14    # tự đóng case sau ngần này ngày không liên hệ

SALE_CONTACT_EVENT = "sale_contact"   # event_type log mỗi lần "Ghi liên hệ"
CASE_CLOSED_EVENT = "case_closed"     # event_type khi đóng case
# Mở lại case đã bị job đóng oan (sự cố khớp lead↔hóa đơn 2026-09-08).
CASE_REOPENED_EVENT = "case_reopened"
STAFF_ASSIGNED_EVENT = "staff_assigned"  # bộ chia tự động gán sale cho đơn đang trống
STAFF_CHANGED_EVENT = "staff_changed"    # đổi sale phụ trách (sửa tay / chuyển hàng loạt)
# Sale được CHỈ ĐỊNH sẵn từ payload ingest (staff_username), khác STAFF_ASSIGNED_EVENT
# ở chỗ không phải bộ chia tự động chọn — bên gửi đã biết trước ai giữ khách; ghi
# riêng loại vì đường ingest ẩn danh (token dùng chung, không actor).
STAFF_INGEST_DESIGNATED_EVENT = "staff_ingest_designated"
ORDER_FIELD_CHANGED_EVENT = "order_field_changed"  # đổi field nghiệp vụ ngoài khối CRM
ORDER_CREATED_EVENT = "order_created"  # tạo đơn (ai tạo, đường nào, dòng hàng ban đầu)
ORDER_ITEMS_CHANGED_EVENT = "order_items_changed"  # thêm/bỏ sản phẩm, đổi số lượng

# Field của Order được ghi journey khi sửa qua PATCH inline (id → nhãn hiển thị).
# Cố tình HẸP: chỉ những field mà câu hỏi "ai đổi, lúc nào" thực sự phát sinh khi
# đối soát. Thêm field ở đây là thêm dòng vào timeline, đừng gom cả bảng vào.
# `staff` không nằm trong map này — có event riêng (STAFF_CHANGED_EVENT) kèm tên sale.
TRACKED_ORDER_FIELDS = {
    "status": "Trạng thái đơn",
    "phone": "Số điện thoại",
    "address": "Địa chỉ",
    "shipping_code": "Mã vận đơn",
    "shipping_provider": "Đơn vị vận chuyển",
    "total_after_discount": "Tổng sau giảm",
    "order_code": "Mã đơn",
    "invoice_code": "Mã hoá đơn",
    "order_source": "Nguồn đơn",
}

SHEET1_SOURCES = ("landing", "hotline", "zalo")
SHEET2_SOURCES = ("messenger",)
SHEET3_SOURCES = ("landing", "hotline", "zalo", "messenger")

# Trạng thái sale "tích cực" / "dead" để suy nhiệt độ.
# "wants_store_visit": khách hẹn tự đến cửa hàng xem hàng — lead còn sống, CỐ Ý
# không nằm trong CLOSED_SALES_STATUSES để đơn ở lại Sheet 3 cho sale theo tiếp.
POSITIVE_SALES_STATUSES = frozenset(
    {
        "scheduled",
        "call_back_later",
        "need_more_consulting",
        "closed_scheduled_date",
        "wants_store_visit",
    }
)
DEAD_SALES_STATUSES = frozenset(
    {
        "unreachable",
        "wrong_number",
        "spam",
        "duplicate",
        "poor_quality_data",
        "not_buying",
        # Mốc gọi CUỐI của chuỗi "không nghe máy" — giữ khớp với
        # UNREACHED_SALES_STATUS_CODES (_repositories_reports.py). Lần 1..5 vẫn
        # còn nhịp gọi nên đơn ở lại Sheet 3 cho sale theo tiếp.
        "no_answer_6",
        "underlying_condition",
    }
)
# Trạng thái coi như đã rời khỏi follow-up (không hiện ở Sheet 3).
CLOSED_SALES_STATUSES = frozenset(
    {"closed", "closed_express", "closed_scheduled_date", "created_order", "archived"}
)

# Trạng thái làm đơn RỜI HẲN board Tư vấn. Hẹp hơn CLOSED_SALES_STATUSES một cách
# CỐ Ý: chủ sản phẩm yêu cầu đơn đã chốt vẫn ở lại board cho sale nhìn thấy
# (2026-08-11), nên 4 trạng thái chốt không còn nằm đây. Chỉ "archived" — kết quả
# của cron autoclose_followups — mới đẩy đơn ra, nếu không thì board chỉ có tăng
# không có giảm.
BOARD_EXIT_SALES_STATUSES = frozenset({"archived"})


def is_info_complete(*, name: str, phone: str, raw_address: str = "", address: str = "") -> bool:
    """Đủ thông tin = có Tên + SĐT + Địa chỉ (text bất kỳ, raw_address hoặc address)."""
    has_name = bool((name or "").strip())
    has_phone = bool((phone or "").strip())
    has_address = bool((raw_address or "").strip()) or bool((address or "").strip())
    return has_name and has_phone and has_address


SHEET_CHOICES = ("sheet1", "sheet2", "sheet3")

# Cột thứ tư của ma trận chia: KHÔNG phải sheet của trang Landing (không có
# `sheet_q`, không vào workload/chuyển đơn). Bộ chia dùng cột này để gán sale
# resale cho cơ hội Foxia sinh từ đơn giao thành công — docs/foxia-resale-delivered.md.
RESALE_SHEET = "resale"
# Mọi cột của ma trận cấu hình (LeadAssignmentShare.sheet).
ASSIGNMENT_SHEETS = (*SHEET_CHOICES, RESALE_SHEET)

SHEET_LABELS = {
    "sheet1": "Landing xác nhận đơn",
    "sheet2": "Messenger xác nhận đơn",
    "sheet3": "Tư vấn",
    RESALE_SHEET: "Resale (đơn giao thành công)",
}


def info_incomplete_q() -> Q:
    """``Q`` lọc đơn THIẾU thông tin — mirror phủ định của ``is_info_complete``.

    NULL và chuỗi rỗng đều tính là trống, khớp cách ingest ghi dữ liệu.
    """
    from django.db.models import Q

    address_blank = (Q(raw_address__isnull=True) | Q(raw_address="")) & (
        Q(address__isnull=True) | Q(address="")
    )
    name_blank = Q(name__isnull=True) | Q(name="")
    phone_blank = Q(phone__isnull=True) | Q(phone="")
    return address_blank | name_blank | phone_blank


def consulting_q() -> Q:
    """``Q`` lọc đơn thuộc luồng Tư vấn (Sheet 3).

    Hai vế:
    - thiếu Tên/SĐT/Địa chỉ — thuộc tính ĐỘNG, hết thiếu là ra khỏi vế này;
    - ``has_address=False`` — cờ BẤT BIẾN ghi lúc ingest, sale điền địa chỉ
      không xoá được. Đây là vế làm tab Tư vấn "dính": đơn đổ về không có địa
      chỉ ở lại Tư vấn kể cả khi đã đủ thông tin, thay vì nhảy sang Sheet 1/2
      và mất dấu với sale đang theo (yêu cầu 2026-08-11).

    Đơn tạo trước migration 0094 có ``has_address`` default True nên chỉ dính
    theo vế thứ nhất — không backfill được, không còn dấu vết địa chỉ lúc đổ đơn.
    """
    from django.db.models import Q

    return info_incomplete_q() | Q(has_address=False)


def classify_sheet(
    *,
    order_source: str | None,
    name: str,
    phone: str,
    raw_address: str = "",
    address: str = "",
    has_address: bool = True,
) -> str | None:
    """Phân loại 1 đơn vào sheet1/sheet2/sheet3, hoặc None nếu không thuộc 3 sheet.

    Dùng chung cho auto-assign (lúc ingest) và workload/transfer. Phải khớp
    ``sheet_q`` — hai hàm là hai cách diễn đạt (Python / SQL) của CÙNG một luật.

    ``has_address`` mặc định True để caller cũ (đơn không qua ingest) giữ nguyên
    hành vi; luồng ingest phải truyền cờ thật của đơn.
    """
    consulting = (not is_info_complete(
        name=name, phone=phone, raw_address=raw_address, address=address
    )) or (not has_address)
    src = (order_source or "").strip().lower()
    # Tư vấn xét TRƯỚC: đơn dính vế has_address vẫn "đủ thông tin" nên nếu để
    # sau sẽ bị sheet1/sheet2 vợt mất.
    if consulting and src in SHEET3_SOURCES:
        return "sheet3"
    if (not consulting) and src in SHEET2_SOURCES:
        return "sheet2"
    if (not consulting) and src in SHEET1_SOURCES:
        return "sheet1"
    return None


def sheet_q(sheet: str) -> Q:
    """Trả về ``django.db.models.Q`` lọc Order thuộc ``sheet`` (mirror ``classify_sheet``).

    Ba tập RỜI NHAU: sheet1/sheet2 loại hẳn đơn thuộc luồng Tư vấn, nên một đơn
    không bao giờ hiện ở hai tab cùng lúc.

    Note: đơn có order_source NULL bị SQL IN loại, khớp ``classify_sheet`` (map
    None → "" nên không match tuple nguồn nào).
    """
    from django.db.models import Q

    consulting = consulting_q()
    if sheet == "sheet1":
        return Q(order_source__in=SHEET1_SOURCES) & ~consulting
    if sheet == "sheet2":
        return Q(order_source__in=SHEET2_SOURCES) & ~consulting
    if sheet == "sheet3":
        return Q(order_source__in=SHEET3_SOURCES) & consulting
    raise ValueError(f"Sheet không hợp lệ: {sheet}")
