"""Trigger sync CNV: domain event trên bus, chỉ enqueue Celery.

Trigger kép là chủ đích (spec §3.1): ``orders.saved``/``customers.saved``/
``catalog.*_saved`` phủ mọi đường ``.save()`` (kể cả cron import KiotViet),
``orders.status_changed`` phủ các đường ``queryset.update()`` có phát event. Trùng
nhau vô hại — task đọc state mới nhất từ DB. Handler nuốt mọi lỗi: sync loyalty là
phụ, không được phá luồng tạo/sửa đơn.

Trước audit 2026-09-23 (T3/P2-4) các trigger ``*_saved`` là Django ``post_save``
móc thẳng vào model của app khác; nay là event phát từ ``Model.save()`` qua
``publish_on_commit`` — handler chạy sau commit, rollback thì không enqueue.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.catalog.contracts.catalog_events import ProductCategorySaved, ProductSaved
from apps.common.events import event_bus
from apps.common.order_scope import MIRROR_ORDER_SOURCES
from apps.customers.contracts.customer_events import CustomerSaved
from apps.orders.contracts.order_events import OrderSaved, OrderStatusChanged
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

# Field của Order ảnh hưởng payload gửi CNV — save với update_fields ngoài bộ
# này thì khỏi enqueue (đỡ spam queue slow với các save phụ như đổi ghi chú CRM).
_ORDER_SYNC_FIELDS = {
    "status",
    "deleted_at",
    "customer",
    "customer_id",
    "total_amount",
    "discount_amount",
    "total_after_discount",
    "order_code",
}


def _enabled() -> bool:
    return bool(sysconfig.get("CNV_SYNC_ENABLED"))


def _enqueue(task, local_id: int) -> None:
    """Enqueue sau commit; lỗi broker chỉ log — cron reconcile sẽ vớt.

    Handler đã chạy sau commit (bus ``publish_on_commit``) nên ``on_commit`` ở đây
    thực thi ngay; giữ lại để đường ``orders.status_changed`` phát bằng ``publish``
    thẳng trong transaction vẫn an toàn.
    """

    def _dispatch() -> None:
        try:
            task.delay(local_id)
        except Exception:  # noqa: BLE001 — broker chết không được phá request
            logger.exception("CNV sync: enqueue %s(%s) thất bại", task.name, local_id)

    transaction.on_commit(_dispatch)


def _on_order_saved(event: OrderSaved) -> None:
    if not _enabled() or event.is_deleted:
        return
    # Đơn mirror từ CRM đối tác KHÔNG được đẩy ra hệ thống loyalty thật — đó là
    # dữ liệu của pháp nhân khác. Đường đồng bộ Foxia ghi bằng bulk_* nên không
    # phát event, guard này là lưới thứ hai cho một `.save()` lọt lưới ở
    # đường code tương lai: loại lỗi không được phép xảy ra dù chỉ một lần, vì
    # nó gửi dữ liệu ra ngoài và không rút lại được.
    if event.order_source in MIRROR_ORDER_SOURCES:
        return
    if (
        event.created
        or event.update_fields is None
        or _ORDER_SYNC_FIELDS & set(event.update_fields)
    ):
        from apps.cnv_loyalty.tasks import sync_order

        _enqueue(sync_order, event.order_id)


def _on_order_status_changed(event: OrderStatusChanged) -> None:
    if not _enabled() or event.old_status == event.new_status:
        return
    from apps.cnv_loyalty.tasks import sync_order

    _enqueue(sync_order, event.order_id)


def _on_customer_saved(event: CustomerSaved) -> None:
    if not _enabled() or event.is_deleted:
        return
    from apps.cnv_loyalty.tasks import sync_customer

    _enqueue(sync_customer, event.customer_id)


def _on_product_saved(event: ProductSaved) -> None:
    if not _enabled() or event.is_deleted:
        return
    from apps.cnv_loyalty.tasks import sync_product

    _enqueue(sync_product, event.product_id)


def _on_category_saved(event: ProductCategorySaved) -> None:
    if not _enabled() or event.is_deleted:
        return
    from apps.cnv_loyalty.tasks import sync_collection

    _enqueue(sync_collection, event.category_id)


_SUBSCRIPTIONS = (
    (OrderStatusChanged.event_type, _on_order_status_changed),
    (OrderSaved.event_type, _on_order_saved),
    (CustomerSaved.event_type, _on_customer_saved),
    (ProductSaved.event_type, _on_product_saved),
    (ProductCategorySaved.event_type, _on_category_saved),
)


def register() -> None:
    """Idempotent theo trạng thái THẬT của bus (bài học ``event_bus.clear()``
    trong test — xem apps/orders/subscribers.py)."""
    for event_type, handler in _SUBSCRIPTIONS:
        if not event_bus.is_subscribed(event_type, handler):
            event_bus.subscribe(event_type, handler)
