"""Giữ Customer.is_new_customer đồng bộ với số đơn giao thành công của khách.

Rule đếm theo status (DELIVERED_SUCCESS) nên phải recompute cả khi đơn đổi trạng thái,
không chỉ tạo/xóa. Soft-delete hàng loạt qua queryset.update (bypass mọi hook) được
gọi thẳng ở bulk_delete_orders; các queryset.update đổi status khác do reconcile cron
2h vá.

Hai đường vào (audit 2026-09-23 T3/P2-4):

- ``orders.saved`` (``apps.orders.contracts.order_events.OrderSaved``) qua event bus —
  thay cho ``post_save`` từng móc thẳng vào model Order. Handler chạy SAU COMMIT nên
  đọc được đơn đã ghi thật; rollback thì không recompute (đúng ý).
- ``post_delete`` của Order VẪN là Django signal: ``QuerySet.delete()`` (kể cả cascade
  từ Customer/Channel) bắn ``post_delete`` từng instance nhưng KHÔNG gọi
  ``Order.delete()``, nên một override ``Order.delete()`` không phủ được 100% — giữ
  signal là cách duy nhất cover xoá cứng hàng loạt. Xoá cứng đơn hiếm (dọn staging,
  thao tác tay), không đáng đổi ngữ nghĩa.

``customers`` và ``orders`` CÙNG tầng (.importlinter) nên không import contract của
orders: subscribe theo CHUỖI event_type và chỉ đọc thuộc tính của event. Nếu orders
đổi tên/field event, ``apps.customers.tests.test_customer_type`` đỏ ngay.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.db.models.signals import post_delete

from apps.common.events import event_bus

from .customer_type import refresh_customer_type

# = apps.orders.contracts.order_events.OrderSaved.event_type (không import — xem docstring).
ORDER_SAVED_EVENT = "orders.saved"

# Các field ảnh hưởng phép đếm đơn giao thành công.
_COUNT_FIELDS = {"status", "invoice_status", "deleted_at", "customer", "customer_id"}


def _on_order_saved(event) -> None:
    customer_id = getattr(event, "customer_id", None)
    if not customer_id:
        return
    update_fields = getattr(event, "update_fields", None)
    # created: mọi luồng tạo đơn. update_fields=None: full save (webhook upsert, form
    # PATCH...) — không biết field nào đổi nên recompute cho chắc (1 count query, rẻ).
    # update_fields cụ thể: chỉ recompute khi đụng field ảnh hưởng phép đếm.
    if (
        getattr(event, "created", False)
        or update_fields is None
        or _COUNT_FIELDS & set(update_fields)
    ):
        refresh_customer_type([customer_id])


def _on_order_deleted(sender, instance, **kwargs):
    if instance.customer_id:
        refresh_customer_type([instance.customer_id])


def register() -> None:
    """Idempotent theo trạng thái THẬT của bus (bài học ``event_bus.clear()`` trong
    test — xem apps/orders/subscribers.py); signal idempotent nhờ ``dispatch_uid``."""
    if not event_bus.is_subscribed(ORDER_SAVED_EVENT, _on_order_saved):
        event_bus.subscribe(ORDER_SAVED_EVENT, _on_order_saved)

    order_model = django_apps.get_model("orders", "Order")
    post_delete.connect(
        _on_order_deleted, sender=order_model, dispatch_uid="customer_type_order_deleted"
    )
