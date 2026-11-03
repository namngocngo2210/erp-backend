"""Adapter phía kiotviet cho các port mà app orders khai ở ``orders/contracts``.

Chiều import kiotviet → orders là hợp lệ theo contract phân tầng, nên adapter
sống ở đây và được đăng ký tại ``KiotVietConfig.ready()`` (xem ``apps.py``).

Các method import service LƯỜI (tại thời điểm gọi) để:
- ``ready()`` không kéo cả cây service của kiotviet vào lúc khởi động app;
- test hiện có patch thẳng ``apps.kiotviet.infrastructure.services.*`` vẫn
  ăn vào call qua port (bind attribute lúc gọi, không đóng băng lúc import).
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterator


class KiotVietInvoiceAdapter:
    """Hiện thực ``apps.orders.contracts.kiotviet_invoice_port.KiotVietInvoicePort``."""

    def get_invoice_by_code(self, *, invoice_code: str, order: Any, channel: Any) -> dict:
        from apps.kiotviet.infrastructure.services import invoice_sync_queries

        return invoice_sync_queries.get_invoice_by_code(
            invoice_code=invoice_code, order=order, channel=channel
        )

    def is_koc_non_cod_order(self, order: Any) -> bool:
        from apps.kiotviet.infrastructure.services import invoice_mutations_push

        return invoice_mutations_push.is_koc_non_cod_order(order)

    def web_request(
        self,
        *,
        order: Any,
        channel: Any,
        method: str,
        endpoint: str,
        params: dict | None = None,
    ):
        from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook

        return gateway_kship_customer_webhook._kiotviet_web_request(  # noqa: SLF001 — adapter nội bộ, giữ nguyên hành vi call cũ
            order=order,
            channel=channel,
            method=method,
            endpoint=endpoint,
            params=params,
        )


class KiotVietOrderFlowAdapter:
    """Hiện thực ``apps.orders.contracts.kiotviet_order_flow_port.KiotVietOrderFlowPort``."""

    _CHANNEL_OPTION_FIELDS = ("id", "name", "branch_id", "branch_ids", "branch_names")

    def list_channel_options(self, *, offset: int, limit: int) -> list[dict]:
        from apps.kiotviet.models import KiotVietChannel

        return list(
            KiotVietChannel.objects.filter(deleted_at__isnull=True)
            .values(*self._CHANNEL_OPTION_FIELDS)
            .order_by("created_at", "id")[offset : offset + limit]
        )

    def iter_active_channels(self) -> Iterator[Any]:
        from apps.kiotviet.models import KiotVietChannel

        queryset = KiotVietChannel.objects.filter(deleted_at__isnull=True).order_by("id")
        return queryset.iterator(chunk_size=100)

    def get_kship_options(self, channel: Any) -> dict:
        from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook

        return gateway_kship_customer_webhook.get_kship_providers_and_services(channel)

    def execute_order_action(
        self,
        order: Any,
        action: str,
        *,
        actor_id: Any = None,
        provider_code: str | None = None,
        service_code: str | None = None,
        kship_note: str = "",
        shipper_note: str = "",
        sender_address_id: Any = None,
        service_extra: dict | None = None,
    ) -> dict:
        from apps.kiotviet.infrastructure.services import service_actions

        return service_actions.KiotVietService.execute_action(
            order,
            action,
            actor_id=actor_id,
            provider_code=provider_code,
            service_code=service_code,
            kship_note=kship_note,
            shipper_note=shipper_note,
            sender_address_id=sender_address_id,
            service_extra=service_extra,
        )


class KiotVietCancellationAdapter:
    """Hiện thực ``apps.orders.contracts.kiotviet_cancellation_port.KiotVietCancellationPort``.

    Dịch cả HAI chiều ở biên: enum ``CancelCheck`` → ``CancelVerdict``, và
    exception ``VerificationUnavailable`` → ``CancellationVerificationUnavailable``.
    Nhờ vậy orders không cần biết kiểu nào của kiotviet, còn test đang patch
    ``cancellation_verifier.InvoiceCancellationVerifier.verify_not_cancelled`` và
    ném ``VerificationUnavailable`` vẫn chạy nguyên.
    """

    def verify_not_cancelled(self, order: Any):
        from apps.orders.contracts.kiotviet_cancellation_port import (
            CancellationVerificationUnavailable,
            CancelVerdict,
        )

        from apps.kiotviet.application.services import cancellation_verifier

        try:
            check = cancellation_verifier.InvoiceCancellationVerifier.verify_not_cancelled(order)
        except cancellation_verifier.VerificationUnavailable as exc:
            raise CancellationVerificationUnavailable(str(exc)) from exc

        # `CancelCheck` là str enum nên `.value` khớp 1-1 với `CancelVerdict`.
        # Giá trị lạ (mock trả bừa) → coi như không xác minh được: fail-closed,
        # không im lặng cho qua.
        try:
            return CancelVerdict(getattr(check, "value", check))
        except ValueError:
            return CancelVerdict.UNVERIFIABLE


class KiotVietDeliveryAdapter:
    """Hiện thực ``apps.inventory.contracts.kiotviet_delivery_port.KiotVietDeliveryPort``."""

    def update_invoice_delivery_status(
        self,
        *,
        invoice_id: Any,
        status: Any,
        order: Any,
        channel: Any,
    ):
        from apps.kiotviet.infrastructure.services import invoice_mutations_push

        return invoice_mutations_push.update_invoice_delivery_status_by_id(
            invoice_id=invoice_id,
            status=status,
            order=order,
            channel=channel,
        )


def register() -> None:
    """Đăng ký toàn bộ adapter của kiotviet vào registry các app tầng dưới."""
    from apps.inventory.contracts.kiotviet_delivery_port import register_kiotviet_delivery_port
    from apps.orders.contracts.kiotviet_cancellation_port import register_kiotviet_cancellation_port
    from apps.orders.contracts.kiotviet_invoice_port import register_kiotviet_invoice_port
    from apps.orders.contracts.kiotviet_order_flow_port import register_kiotviet_order_flow_port

    register_kiotviet_invoice_port(KiotVietInvoiceAdapter())
    register_kiotviet_order_flow_port(KiotVietOrderFlowAdapter())
    register_kiotviet_cancellation_port(KiotVietCancellationAdapter())
    register_kiotviet_delivery_port(KiotVietDeliveryAdapter())
