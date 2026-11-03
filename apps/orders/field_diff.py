"""So sánh giá trị field của đơn: payload có ĐỔI THẬT so với bản ghi đang lưu không.

Tách khỏi invoice_lock.py để hai guard dùng chung (khoá theo hoá đơn, và quyền sửa
theo field). Module thuần: không import DRF/model, không I/O — chỉ đọc thuộc tính
của order đã nạp.

So GIÁ TRỊ chứ không so sự có mặt trong payload: form đơn gửi lại full object, chặn
theo sự có mặt sẽ 403 oan cho mọi cập nhật khác đi kèm.
"""
from __future__ import annotations

from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation
from typing import Any
from collections.abc import Iterable

# Catalog thuần (dataclass, không Django model/DRF) — an toàn để field_diff.py
# vẫn "thuần" theo constraint của module. Dùng để nhận diện field TĨNH khi lọc
# khoá `*_label` echo trong `_is_crm_label_echo_key`.
from apps.orders.assessment_fields import ASSESSMENT_FIELDS_BY_NAME

# Giá trị payload không parse nổi → sentinel khác MỌI giá trị hợp lệ. Tuyệt đối
# không rơi về None: None trùng với đơn đang để trống → tuột qua guard.
UNPARSEABLE = object()

DECIMAL_FIELDS = frozenset({
    "weight", "total_amount", "discount_amount", "booking_voucher_discount",
    "other_price", "customer_prepaid", "total_after_discount", "shipping_fee",
})

INT_FIELDS = frozenset({"quantity"})

# Khoá trong `validated_data` KHÁC tên field khai báo khi serializer có `source`
# (OrderInlineUpdateSerializer). Tra sai khoá = field luôn "không có trong payload"
# = guard không bao giờ bắn = quyền vô hiệu hoàn toàn, mà test hời hợt vẫn xanh.
VALIDATED_KEY_BY_FIELD: dict[str, str] = {
    "channel_code": "channel",
    "kiotviet_channel_id": "kiotviet_channel",
}

# validated_data trả về INSTANCE model cho FK → so .pk, không so str(instance).
FK_FIELDS = frozenset({"channel", "kiotviet_channel", "fulfillment_warehouse"})
DATETIME_FIELDS = frozenset({"created_at"})
DATE_FIELDS = frozenset({"return_date"})
BOOL_FIELDS = frozenset({"is_export_vat", "customer_return"})
NESTED_FIELDS = frozenset({"crm", "recall", "recall_entries"})


def norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def norm_int(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return UNPARSEABLE


def norm_decimal(value: Any) -> Any:
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return UNPARSEABLE


def items_changed(order: Any, payload_items: list[dict[str, Any]]) -> bool:
    """Đổi dòng hàng = thêm/bớt dòng, đổi sản phẩm, đổi số lượng, đổi đơn giá hoặc
    đổi giảm giá dòng.

    Dòng payload khớp dòng DB theo `product_id`, thiếu thì theo `sku`. `unit_price`
    không gửi nghĩa là "để hệ thống tự tính theo bảng giá" → không coi là đổi giá.
    `discount` không gửi nghĩa là 0 (bỏ giảm giá) → so như số 0.
    """
    current_items = list(order.items.all())
    if len(current_items) != len(payload_items):
        return True

    by_product = {item.product_id: item for item in current_items}
    by_sku: dict[str, Any] = {}
    for item in current_items:
        sku = norm_str(item.sku)
        if sku:
            by_sku.setdefault(sku, item)

    matched_pks: set[Any] = set()
    for payload_item in payload_items:
        product_id = norm_int(payload_item.get("product_id"))
        sku = norm_str(payload_item.get("sku"))
        matched = by_product.get(product_id) if product_id is not None else None
        if matched is None and sku:
            matched = by_sku.get(sku)
        # Không khớp được, hoặc hai dòng payload cùng trỏ một dòng DB (đổi sản phẩm
        # thành trùng dòng khác) → đã đổi.
        if matched is None or matched.pk in matched_pks:
            return True
        matched_pks.add(matched.pk)

        if norm_int(payload_item.get("quantity")) != norm_int(matched.quantity):
            return True
        unit_price = payload_item.get("unit_price")
        if unit_price is not None and norm_decimal(unit_price) != norm_decimal(matched.unit_price):
            return True
        if norm_decimal(payload_item.get("discount")) != norm_decimal(matched.discount):
            return True
    return False


def _norm_dt(value: Any) -> Any:
    """Quy về UTC rồi so. Cùng thời điểm khác múi giờ phải bằng nhau — so chuỗi ISO
    có offset thì '+07:00' và 'Z' của cùng một mốc lại khác nhau → 403 oan."""
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return UNPARSEABLE
    return value.astimezone(UTC)


def _datetime_changed(order: Any, field: str, new_value: Any) -> bool:
    normalized_new = _norm_dt(new_value)
    normalized_current = _norm_dt(getattr(order, field, None))
    # Hai vế cùng không parse nổi thì `UNPARSEABLE != UNPARSEABLE` là False → guard
    # coi như "không đổi" và cho ghi. Hệ fail-closed: không hiểu nổi giá trị thì
    # phải chặn, không phải cho qua.
    if normalized_new is UNPARSEABLE or normalized_current is UNPARSEABLE:
        return True
    return normalized_new != normalized_current


def _fk_pk(value: Any) -> Any:
    return getattr(value, "pk", value)


def _parse_slip_codes(value: Any) -> list[str]:
    """Chuẩn hoá `return_slip_code` payload về list, cùng khuôn với
    `OrderServiceHelpers._parse_slip_codes` (không import service để giữ module
    thuần — hai bên cùng chuẩn hoá độc lập, giống cách `items_changed` đã làm với
    `unit_price`/`quantity`). Nhận chuỗi phẩy (inline edit) hoặc list (sync KiotViet)."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    codes: list[str] = []
    for item in raw:
        code = str(item).strip()[:64]
        if code and code not in codes:
            codes.append(code)
    return codes


def _signature(source: Any, keys: Iterable[str]) -> tuple:
    """Chữ ký chuẩn hoá của một object/dict theo danh sách khoá."""

    def read(k: str) -> Any:
        return source.get(k) if isinstance(source, dict) else getattr(source, k, None)

    return tuple(norm_str(read(k)) for k in keys)


# OrderRecallEntry (1-N, related_name="recall_entries") — mã + cặp ngày mỗi đợt.
_RECALL_ENTRY_KEYS = ("recall_code", "recall_created_date", "recall_received_date")

# OrderNeed (1-N, `assessment.needs`) — một "nhu cầu" được định danh DUY NHẤT bằng mã
# loại nhu cầu (`need_type.code`): model có UniqueConstraint(order_assessment,
# need_type) và serializer dedupe theo cùng khoá đó, nên không có hai dòng cùng mã.
# Các khoá còn lại trong payload (`group_code`, `type_name`, `group_name`) đều suy ra
# được từ mã → không thuộc chữ ký.
_NEED_KEYS = ("type_code",)


def _assessment_value(assessment: Any, key: str) -> Any:
    """Đọc giá trị 1 khoá CRM trên Assessment.

    Field TĨNH (khai báo trong `assessment_fields.ASSESSMENT_FIELDS`) là attribute
    trực tiếp trên model. Field ĐỘNG (registry `assessment_field_defs`, thêm qua
    Django admin không cần migration) không có attribute cùng tên — chúng nằm
    trong `OrderAssessment.extra_fields` (JSONField). `getattr(assessment, key)`
    ngây thơ cho field động luôn đọc None (attribute không tồn tại) → so với giá
    trị thật trong payload luôn ra "đã đổi", 403 oan mỗi lần form gửi lại field
    động cũ y nguyên. Hai tập tên field tĩnh/động không giao nhau theo thiết kế
    (metaclass serializer chỉ đẩy khoá KHÔNG thuộc field DRF khai báo vào nhánh
    động), nên tra `extra_fields` trước là an toàn."""
    extra = getattr(assessment, "extra_fields", None) or {}
    if key in extra:
        return extra[key]
    return getattr(assessment, key, None)


# Key `OrderCRMSerializer` (read) TRẢ RA nhưng KHÔNG PHẢI cột ghi được của
# Assessment — không có attribute cùng tên, không nằm trong `extra_fields`. Form
# đơn spread NGUYÊN object `crm` cũ rồi ghi đè field vừa sửa
# (`crm: {...row.crm, [field]: value}` — OrdersTable/cell-renderers.tsx), nên
# payload THẬT luôn kèm các key echo/computed này. So chúng như field thật
# (`_assessment_value` ra None, khác giá trị echo thật trong payload) là 403 oan
# gần như MỌI LẦN lưu CRM — sự cố tương tự 03/08 (bản đó ở tầng service, 400 thay
# vì 403).
#
# `needs` KHÔNG nằm trong tập này: nó là quan hệ 1-N thật (OrderNeed) và tầng service
# CÓ GHI nó (`_sync_assessment_needs`). Coi nó là echo = user 0 grant PATCH
# `{"crm": {"needs": [...]}}` là ghi đè được nhu cầu khách — fail-open lẻn vào giữa
# hệ fail-closed. Nó được so bằng chữ ký cấu trúc trong `_needs_changed`, KHÔNG phải
# bằng norm_str (đọc `assessment.needs` ra RelatedManager).
#
# KHÔNG import từ `OrderServiceHelpers._CRM_SERIALIZER_ECHO_KEYS`
# (order_service_helpers.py) để giữ module thuần — nơi đó kéo theo toàn bộ cây
# import Django models/DB của tầng service. Danh sách dưới đây phải khớp tay với
# bên đó; test_order_field_diff.py có test đối chiếu song song hai bên, trôi là
# đỏ ngay (cùng khuôn với `_parse_slip_codes`).
_CRM_ECHO_KEYS = frozenset({
    "updated_by_name",
    "next_reminder_at",
    "ai_filled_fields",
    "extra_fields",
    "ai_open_insights",
    "events",
    "created_at",
    "updated_at",
})


def _is_crm_label_echo_key(key: str) -> bool:
    """Key `*_label` do serializer TỰ SINH cho một field thật (metaclass inject
    SerializerMethodField `{name.removesuffix('_code')}_label`) — twin hiển thị
    của một cột thật, không ghi được. Cùng luật với
    `OrderServiceHelpers._is_label_echo_key`."""
    if not key.endswith("_label"):
        return False
    base = key.removesuffix("_label")
    return base in ASSESSMENT_FIELDS_BY_NAME or f"{base}_code" in ASSESSMENT_FIELDS_BY_NAME


def _need_signature_source(item: Any) -> dict:
    """Quy 1 phần tử `needs` về dict có `type_code`, cho cả hai phía so sánh.

    Payload đã qua `OrderCRMNeedInputSerializer.validate` mang sẵn `type_code` đã
    chuẩn hoá đúng `need_type.code`; payload dựng tay (service/test) có thể chỉ có
    instance `need_type`. Bên DB truyền vào là mã đọc thẳng từ query.
    """
    if not isinstance(item, dict):
        return {"type_code": getattr(getattr(item, "need_type", None), "code", None)}
    if item.get("type_code"):
        return item
    return {"type_code": getattr(item.get("need_type"), "code", None)}


def _needs_changed(assessment: Any, payload_needs: Any) -> bool:
    """So tập nhu cầu bằng chữ ký cấu trúc — cùng khuôn `recall_entries`.

    Thứ tự không mang nghĩa (model không có cột sắp xếp) nên so hai list đã sort.
    KHÔNG được rút gọn thành "có `needs` trong payload = đã đổi": FE spread nguyên
    `row.crm` nên payload luôn kèm `needs`, làm thế là 403 oan MỌI lần lưu CRM.

    Đọc mã qua `values_list("need_type__code")`: một query, không N+1 kể cả khi order
    chưa prefetch `assessment__needs__need_type`.
    """
    current_codes = (
        []
        if assessment is None
        else list(assessment.needs.values_list("need_type__code", flat=True))
    )
    current = sorted(_signature({"type_code": c}, _NEED_KEYS) for c in current_codes)
    incoming = sorted(
        _signature(_need_signature_source(item), _NEED_KEYS)
        for item in (payload_needs or [])
    )
    return current != incoming


def nested_changed(order: Any, field: str, new_value: Any) -> bool:
    if field == "recall_entries":
        current = [_signature(e, _RECALL_ENTRY_KEYS) for e in order.recall_entries.all()]
        incoming = [_signature(e, _RECALL_ENTRY_KEYS) for e in (new_value or [])]
        return sorted(current) != sorted(incoming)

    if field == "recall":
        # OrderRecall (1-1, related_name="recall") CHỈ còn `return_slip_code` — mã và
        # ngày thu hồi đã tách hẳn sang OrderRecallEntry (1-N) ở trên. Field nào KHÔNG
        # có mặt tường minh trong payload thì coi là "không đụng tới" (cùng quy tắc
        # với `_upsert_order_recall` ở tầng service), không phải "xoá".
        payload = new_value or {}
        if "return_slip_code" not in payload:
            return False
        current_recall = getattr(order, "recall", None)
        current_codes = list(getattr(current_recall, "return_slip_code", None) or [])
        return current_codes != _parse_slip_codes(payload.get("return_slip_code"))

    # crm: so từng khoá gửi lên với giá trị tương ứng trên Assessment. Chỉ duyệt khoá
    # CÓ trong payload — payload crm là partial, khoá vắng mặt không phải "xoá". Bỏ
    # qua khoá echo/computed (_CRM_ECHO_KEYS/_is_crm_label_echo_key) — chúng không
    # phải cột ghi được, so như field thật sẽ luôn ra "đã đổi". `needs` là quan hệ
    # 1-N GHI ĐƯỢC nên tách nhánh riêng, so bằng chữ ký cấu trúc.
    return bool(crm_changed_subfields(order, new_value))


CRM_FIELD_PREFIX = "crm."
CRM_NEEDS_FIELD = "crm.needs"
CRM_EXTRA_FIELD = "crm.__extra__"


def crm_changed_subfields(order: Any, crm_payload: Any) -> set[str]:
    """Tên grant của những trường CSKH mà payload ĐỔI THẬT giá trị.

    Trả tập tên dạng `crm.<trường>`; trường ĐỘNG (khai qua Django admin, nằm trong
    `OrderAssessment.extra_fields`) gom hết về `crm.__extra__` vì catalog tĩnh ở
    `apps.users` không thể biết chúng — `apps.users` bị cấm import `apps.orders`.

    Chỉ duyệt khoá CÓ trong payload: payload crm là partial, khoá vắng mặt không
    phải "xoá". Khoá echo/computed bị bỏ qua — chúng không phải cột ghi được, so
    như field thật sẽ luôn ra "đã đổi" và gây 403 oan mọi lần lưu.
    """
    assessment = getattr(order, "assessment", None)
    changed: set[str] = set()
    for key, value in (crm_payload or {}).items():
        if key == "needs":
            if _needs_changed(assessment, value):
                changed.add(CRM_NEEDS_FIELD)
            continue
        if key in _CRM_ECHO_KEYS or _is_crm_label_echo_key(key):
            continue
        current = _assessment_value(assessment, key) if assessment is not None else None
        if norm_str(value) == norm_str(current):
            continue
        changed.add(
            f"{CRM_FIELD_PREFIX}{key}" if key in ASSESSMENT_FIELDS_BY_NAME else CRM_EXTRA_FIELD
        )
    return changed


def field_changed(order: Any, field: str, new_value: Any) -> bool:
    if field == "items":
        return items_changed(order, new_value or [])
    if field in NESTED_FIELDS:
        return nested_changed(order, field, new_value)

    if field in FK_FIELDS:
        return _fk_pk(new_value) != getattr(order, f"{field}_id", None)

    if field in DATETIME_FIELDS:
        return _datetime_changed(order, field, new_value)

    current = getattr(order, field, None)
    if field in DATE_FIELDS:
        return new_value != current
    if field in BOOL_FIELDS:
        return bool(new_value) != bool(current)
    if field in DECIMAL_FIELDS:
        return norm_decimal(new_value) != norm_decimal(current)
    if field in INT_FIELDS:
        return norm_int(new_value) != norm_int(current)
    return norm_str(new_value) != norm_str(current)


def changed_fields(order: Any, validated_data: dict[str, Any], fields: Iterable[str]) -> list[str]:
    """Các field trong `fields` mà payload ĐỔI THẬT giá trị, theo thứ tự `fields`.

    Trả về tên FIELD (theo catalog/serializer), không phải khoá validated_data —
    người dùng cần thấy "Kênh", không phải "channel".
    """
    requested = list(fields)
    # Hạt `crm.<trường>`: tính MỘT LẦN tập trường con đã đổi rồi lọc theo `fields`,
    # thay vì gọi comparator cho từng mục (24+ mục × mỗi request là lãng phí, và
    # `_needs_changed` có query DB).
    crm_changed: set[str] | None = None
    if any(f.startswith(CRM_FIELD_PREFIX) for f in requested) and "crm" in validated_data:
        crm_changed = crm_changed_subfields(order, validated_data["crm"])

    result: list[str] = []
    for f in requested:
        if f.startswith(CRM_FIELD_PREFIX):
            if crm_changed and f in crm_changed:
                result.append(f)
            continue
        key = VALIDATED_KEY_BY_FIELD.get(f, f)
        if key in validated_data and field_changed(order, key, validated_data[key]):
            result.append(f)
    return result
