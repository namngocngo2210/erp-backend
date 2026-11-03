"""Tạo/hủy/cập nhật vận đơn Best Express + ghi BestShipment.

Giới hạn nghiệp vụ phía Best (hủy trước 202, sửa địa chỉ trước 303, số lần
sửa, hạ COD…) KHÔNG re-implement ở đây — gọi thẳng và passthrough lỗi Best.
"""

from decimal import Decimal

from django.db import IntegrityError

from apps.best_express.models import BestShipmentStatus
from apps.best_express.exceptions import BestExpressError
from apps.best_express.infrastructure.best_client import BestExpressClient
from apps.best_express.models import BestExpressAccount, BestShipment

# *District không bắt buộc (địa chỉ 2 cấp mới từ 01/07/2025 để trống District).
REQUIRED_FIELDS = (
    "Code",
    "ProductName",
    "JourneyType",
    "ServiceId",
    "Weight",
    "SourceCity",
    "SourceWard",
    "SourceAddress",
    "SourceName",
    "SourcePhoneNumber",
    "DestCity",
    "DestWard",
    "DestAddress",
    "DestName",
    "DestPhoneNumber",
    "ReturnCity",
    "ReturnWard",
    "ReturnAddress",
    "ReturnName",
    "ReturnPhoneNumber",
)
_DISTRICT_FIELDS = ("SourceDistrict", "DestDistrict", "ReturnDistrict")


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def create_shipment(
    account: BestExpressAccount, payload: dict, *, order=None, created_by_id: int | None = None
) -> BestShipment:
    missing = [
        field for field in REQUIRED_FIELDS if payload.get(field) in (None, "")
    ]
    if missing:
        raise BestExpressError(f"Thiếu trường bắt buộc: {', '.join(missing)}")
    payload = dict(payload)
    for field in _DISTRICT_FIELDS:
        payload.setdefault(field, "")

    merchant_order_code = str(payload["Code"]).strip()
    if BestShipment.objects.filter(merchant_order_code=merchant_order_code).exists():
        raise BestExpressError(
            f"Mã đơn {merchant_order_code} đã có vận đơn Best — không tạo trùng."
        )

    data = BestExpressClient(account).create_order(payload)
    try:
        return BestShipment.objects.create(
            account=account,
            order=order,
            created_by_id=created_by_id,
            merchant_order_code=merchant_order_code,
            code_best=str(data.get("Code") or "").strip() or None,
            route_code=str(data.get("RouteCode") or "").strip(),
            status=BestShipmentStatus.CREATED,
            cod=_to_decimal(payload.get("CollectAmount")),
            product_price=_to_decimal(payload.get("ProductPrice")),
            weight=_to_int(payload.get("Weight")),
            length=_to_int(payload.get("Length")),
            width=_to_int(payload.get("Width")),
            height=_to_int(payload.get("Height")),
            journey_type=_to_int(payload.get("JourneyType")) or 1,
            service_id=_to_int(payload.get("ServiceId")) or 12491,
            pickup_type=_to_int(payload.get("PickupType")) or 2,
            request_payload=payload,
        )
    except IntegrityError as exc:
        raise BestExpressError(
            f"Mã đơn {merchant_order_code} bị tạo trùng đồng thời — vận đơn Best "
            f"{data.get('Code') or '?'} đã tạo phía Best, cần đối soát/hủy tay."
        ) from exc


def _best_code(shipment: BestShipment) -> str:
    return shipment.code_best or shipment.merchant_order_code


def cancel_shipment(shipment: BestShipment) -> BestShipment:
    BestExpressClient(shipment.account).cancel_order(_best_code(shipment))
    # Không đụng last_event_at: webhook in-flight muộn có thể tạm ghi đè CANCELLED —
    # chấp nhận eventual consistency, event "customer Cancel" của Best sẽ chốt lại.
    shipment.status = BestShipmentStatus.CANCELLED
    shipment.save(update_fields=["status", "updated_at"])
    return shipment


def _without_code(changes: dict) -> dict:
    """Loại mọi biến thể khoá `Code` do client gửi lên.

    Mã vận đơn LUÔN do server suy ra từ `shipment_id` đã qua kiểm quyền. Nếu để
    client đặt `Code` trong payload passthrough thì gate theo shipment_id bị vô hiệu:
    người dùng thao tác được trên vận đơn CỦA NGƯỜI KHÁC (hạ COD, đổi địa chỉ nhận →
    chuyển hướng hàng) mà bản ghi nạn nhân không hề đổi (audit 2026-07-14 vòng 2).
    So khớp case-insensitive vì payload Best là JSON tự do.
    """
    return {key: value for key, value in changes.items() if str(key).strip().lower() != "code"}


def update_shipment(shipment: BestShipment, changes: dict) -> BestShipment:
    changes = _without_code(changes)
    # `Code` đặt SAU cùng: không cho key nào của client ghi đè mã server đã resolve.
    payload = {**changes, "Code": _best_code(shipment)}
    BestExpressClient(shipment.account).update_order(payload)
    if "CollectAmount" in changes:
        shipment.cod = _to_decimal(changes["CollectAmount"])
    if "ProductPrice" in changes:
        shipment.product_price = _to_decimal(changes["ProductPrice"])
    for best_field, model_field in (
        ("Weight", "weight"),
        ("Length", "length"),
        ("Width", "width"),
        ("Height", "height"),
        ("ServiceId", "service_id"),
    ):
        if best_field in changes:
            setattr(shipment, model_field, _to_int(changes[best_field]))
    shipment.request_payload = {**(shipment.request_payload or {}), **changes}
    shipment.save()
    return shipment


def update_shipment_address(shipment: BestShipment, address: dict) -> dict:
    address = _without_code(address)
    payload = {**address, "Code": _best_code(shipment)}
    data = BestExpressClient(shipment.account).update_address(payload)
    shipment.request_payload = {**(shipment.request_payload or {}), "UpdateAddress": address}
    shipment.save(update_fields=["request_payload", "updated_at"])
    return data
