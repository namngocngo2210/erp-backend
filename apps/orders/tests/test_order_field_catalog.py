"""Catalog field đơn phải khớp đúng serializer ghi đơn.

Thiếu test này thì thêm field vào serializer mà quên khai nhóm sẽ trôi im lặng:
field đó không nằm trong catalog nên guard bỏ qua = ai cũng sửa được, đúng thứ
allow-list sinh ra để chặn.
"""
from apps.orders.transport.serializers.order_serializers_impl.kiotviet_landing_serializers import (
    OrderInlineUpdateSerializer,
)
from apps.users.rbac_catalog import (
    ALL_ORDER_FIELDS,
    CRM_FIELD_PREFIX,
    ORDER_FIELD_GROUPS,
    ORDER_FIELD_LABELS,
)


def test_catalog_covers_every_writable_order_field():
    """Catalog phủ đúng tập field ghi được của serializer.

    Ngoại lệ duy nhất: `crm` không còn là một hạt quyền mà nở thành nhiều mục
    `crm.<trường>` (xem test_crm_field_catalog.py). Gộp chúng lại thành `crm` để
    so, nhờ vậy test vẫn bắt được field serializer mới mà quên khai catalog.
    """
    serializer_fields = set(OrderInlineUpdateSerializer.Meta.fields) - {"staff_id"}
    crm_grants = {f for f in ALL_ORDER_FIELDS if f.startswith(CRM_FIELD_PREFIX)}
    collapsed = (ALL_ORDER_FIELDS - crm_grants) | {"crm"}
    assert serializer_fields == collapsed
    assert crm_grants, "crm phải nở thành các mục con, không được rỗng"


def test_every_catalog_field_has_a_vietnamese_label():
    assert set(ORDER_FIELD_LABELS) == ALL_ORDER_FIELDS


def test_no_field_belongs_to_two_groups():
    seen: set[str] = set()
    for _code, _label, fields in ORDER_FIELD_GROUPS:
        overlap = seen & set(fields)
        assert not overlap, f"field nằm ở 2 nhóm: {overlap}"
        seen |= set(fields)


def test_group_codes_are_unique():
    codes = [code for code, _label, _fields in ORDER_FIELD_GROUPS]
    assert len(codes) == len(set(codes))
