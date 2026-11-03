"""Báo cáo Resale — đơn của KÊNH Resale, chia rổ theo kết quả giao hàng.

Nuôi tab "Resale" của trang Báo cáo Sale. Thiết kế:
docs/superpowers/specs/2026-08-26-resale-channel-report-design.md

Bản trước (MR !1366) SUY LUẬN đơn resale từ lịch sử mua (khách đã có đơn giao
thành công trước đó) trong phạm vi kênh ad-driven. Đo prod cho thấy cách đó chỉ
phủ 31% doanh thu resale thật, và mỉa mai nhất là loại luôn chính kênh tên
"Resale". Công ty KHÔNG định nghĩa resale bằng lịch sử mua — sale tạo đơn resale
trên một kênh riêng, nên kênh mới là nguồn sự thật.

Năm rổ dùng lại NGUYÊN VĂN định nghĩa của ``summary_overview`` (cùng hằng số,
cùng phép trừ) để hai trang không thể lệch định nghĩa trong im lặng.
"""
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum

from apps.orders.models import Order

from .shared import (
    CANCELLED_ORDER_Q,
    DELIVERED_SUCCESS_ORDER_STATUS,
    LEAD_ORDER_Q,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    resale_channel_ids,
)

# 5 cặp (tiền, số đơn). Tên khớp từng chữ với summary_overview để hai báo cáo
# đối chiếu được mà không phải dịch tên cột.
_MONEY_FIELDS = (
    "total_revenue",
    "delivered_revenue",
    "awaiting_revenue",
    "returned_revenue",
    "return_risk_revenue",
)


def _bucket_aggregates():
    """Năm cặp aggregate dùng chung cho cả `kpis` lẫn từng hàng `by_staff`.

    Một chỗ định nghĩa duy nhất: hai lượt gộp không thể lệch mệnh đề.
    """
    total_q = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q
    delivered_q = total_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
    # PHỦ ĐỊNH, không liệt kê dương: Order.status là PositiveSmallIntegerField
    # không ép choices ở tầng DB, và luồng import KiotViet ghi thẳng int từ
    # payload API. Mã trạng thái lạ phải rơi vào MỘT rổ, không được rớt khỏi cả
    # bốn rổ và làm tổng lệch.
    awaiting_q = total_q & ~Q(
        status__in=(
            DELIVERED_SUCCESS_ORDER_STATUS,
            *RETURN_ORDER_STATUSES,
            *RETURN_RISK_ORDER_STATUSES,
        )
    )
    returned_q = total_q & Q(status__in=RETURN_ORDER_STATUSES)
    return_risk_q = total_q & Q(status__in=RETURN_RISK_ORDER_STATUSES)
    return {
        "total_revenue": Sum("total_after_discount", filter=total_q),
        "total_orders": Count("id", filter=total_q),
        "delivered_revenue": Sum("total_after_discount", filter=delivered_q),
        "delivered_orders": Count("id", filter=delivered_q),
        "awaiting_revenue": Sum("total_after_discount", filter=awaiting_q),
        "awaiting_orders": Count("id", filter=awaiting_q),
        "returned_revenue": Sum("total_after_discount", filter=returned_q),
        "returned_orders": Count("id", filter=returned_q),
        "return_risk_revenue": Sum("total_after_discount", filter=return_risk_q),
        "return_risk_orders": Count("id", filter=return_risk_q),
    }


def _stringify_money(row: dict) -> dict:
    for field in _MONEY_FIELDS:
        row[field] = str(row[field] or Decimal("0"))
    return row


class ResaleReportsMixin:
    """summary_resale_* — ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @classmethod
    def summary_resale_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        """Đơn của kênh Resale trong kỳ, chia 4 rổ, kèm bảng theo nhân viên.

        ĐƠN RESALE = đơn thuộc kênh Resale (``resale_channel_ids()``). KHÔNG suy
        luận từ lịch sử mua nữa — xem docstring đầu module.

        DOANH THU = mọi đơn ĐÃ LÊN HÓA ĐƠN KiotViet trừ đơn huỷ
        (``~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q``), giống hệt Báo cáo tổng. Tổng đó
        chia đúng 4 rổ RỜI NHAU nên ``delivered + awaiting + returned +
        return_risk = total``, cả tiền lẫn số đơn — có test khoá bất biến này.

        KHÔNG lọc theo phòng ban nhân viên: phạm vi đã do kênh quyết định, thêm
        điều kiện phòng ban chỉ tạo rủi ro âm thầm mất người khi CSKH tham gia
        bán resale. Đơn chưa gán sale rơi vào hàng ``staff_id = None``.
        """
        base = cls._apply_created_at_date_filters(
            cls._exclude_hidden_branches(Order.objects.filter(deleted_at__isnull=True)),
            date_from,
            date_to,
        ).exclude(branch_id__in=OVERVIEW_EXCLUDED_BRANCH_IDS)
        # Clone khôi phục kế thừa created_at của đơn gốc nên rơi vào CÙNG kỳ; không
        # loại đơn gốc thì cả hai cùng cộng vào tổng.
        #
        # DÙNG Exists(), KHÔNG `.exclude(restored_orders__deleted_at__isnull=True)`:
        # lookup `__isnull=True` xuyên quan hệ to-many dùng LEFT OUTER JOIN nên nó
        # khớp CẢ đơn không có bản khôi phục nào — exclude() khi đó xoá gần sạch bảng.
        base = base.exclude(
            Exists(Order.objects.filter(restored_from_id=OuterRef("pk"), deleted_at__isnull=True))
        )
        # Đọc qua helper, KHÔNG import hằng số: conftest patch nó cho cả suite.
        # Index ord_act_ch_st_cr_idx — (channel_id, status, created_at DESC)
        # WHERE deleted_at IS NULL — phủ đúng lát cắt này.
        base = base.filter(channel_id__in=resale_channel_ids())

        aggregates = _bucket_aggregates()
        kpis = _stringify_money(base.aggregate(**aggregates))
        for field, value in kpis.items():
            if field.endswith("_orders"):
                kpis[field] = value or 0

        rows = [
            _stringify_money(row)
            for row in base.values("staff_id", "staff__username", "staff__display_name").annotate(
                **aggregates
            )
        ]
        for row in rows:
            for field, value in row.items():
                if field.endswith("_orders"):
                    row[field] = value or 0
        # Sắp ở Python: order_by trên alias của Sum có filter buộc Postgres gói
        # thêm một lớp subquery, mà số hàng ở đây chỉ cỡ số nhân viên.
        rows.sort(key=lambda row: Decimal(row["delivered_revenue"]), reverse=True)
        return {"kpis": kpis, "by_staff": rows}
