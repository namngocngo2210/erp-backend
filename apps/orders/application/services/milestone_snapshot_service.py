"""Chụp snapshot mốc vòng đời đơn (spec 2026-08-04-order-milestone-snapshots).

Write-once theo (order, milestone) nhờ get_or_create + unique constraint —
webhook bắn lại / cron chạy lại / hóa đơn reissue không tạo dòng mới, không ghi
đè. Đây là dữ liệu ĐỐI SOÁT: lỗi ghi chỉ log error, tuyệt đối không raise làm
vỡ luồng tạo đơn / gán hóa đơn đang chạy.
"""
import logging

from apps.orders.models import OrderMilestone, OrderMilestoneSnapshot

logger = logging.getLogger(__name__)


def _capture(order, milestone: str, amount_source: str) -> None:
    try:
        OrderMilestoneSnapshot.objects.get_or_create(
            order=order,
            milestone=milestone,
            defaults={
                "total_amount": order.total_amount,
                "total_after_discount": order.total_after_discount,
                "quantity": order.quantity,
                "sku": order.sku,
                "amount_source": amount_source,
            },
        )
    except Exception:
        logger.exception(
            "Failed to capture milestone snapshot (order_id=%s, milestone=%s)",
            getattr(order, "id", None),
            milestone,
        )


def capture_created_snapshot(order, *, amount_source: str) -> None:
    """Chụp mốc "đơn đổ về" — gọi ngay sau khi ingest tạo Order."""
    _capture(order, OrderMilestone.CREATED, amount_source)


def capture_invoice_snapshot(order) -> None:
    """Chụp mốc "đã tạo hóa đơn" — gọi khi invoice_code được gán lần đầu."""
    _capture(order, OrderMilestone.INVOICED, "invoice")


def copy_created_snapshot(source_order, target_order) -> None:
    """Đơn nhân đôi/khôi phục kế thừa mốc created của đơn gốc (số của DÒNG GỐC).

    Đơn gốc chưa có snapshot (tạo tay, tạo trước khi có bảng) -> không tạo gì.
    """
    try:
        source = source_order.milestone_snapshots.filter(
            milestone=OrderMilestone.CREATED
        ).first()
        if source is None:
            return
        OrderMilestoneSnapshot.objects.get_or_create(
            order=target_order,
            milestone=OrderMilestone.CREATED,
            defaults={
                "total_amount": source.total_amount,
                "total_after_discount": source.total_after_discount,
                "quantity": source.quantity,
                "sku": source.sku,
                "amount_source": "cloned",
            },
        )
    except Exception:
        logger.exception(
            "Failed to copy created snapshot (source_id=%s, target_id=%s)",
            getattr(source_order, "id", None),
            getattr(target_order, "id", None),
        )
