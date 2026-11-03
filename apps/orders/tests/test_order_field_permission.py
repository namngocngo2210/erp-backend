"""Quyền sửa theo field trên PATCH /api/orders/{id}.

Ma trận: (có grant / không grant) × (đổi thật / gửi lại giá trị cũ) × các kiểu field.
"""
from decimal import Decimal

import pytest

from apps.users.models import RoleFieldGrant

pytestmark = pytest.mark.django_db


def grant(role, *fields):
    for f in fields:
        RoleFieldGrant.objects.create(role=role, section="orders", field=f)


def test_change_without_grant_is_rejected(sale_client, sale_role, order_with_items):
    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "999000"},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["total_amount"]
    assert "Tổng tiền" in res.json()["detail"]
    order_with_items.refresh_from_db()
    assert order_with_items.total_amount != Decimal("999000")


def test_change_with_grant_is_allowed(sale_client, sale_role, order_with_items):
    grant(sale_role, "total_amount")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "999000"},
        content_type="application/json",
    )

    assert res.status_code == 200
    order_with_items.refresh_from_db()
    assert order_with_items.total_amount == Decimal("999000")


def test_resending_unchanged_value_without_grant_is_allowed(
    sale_client, sale_role, order_with_items
):
    """Form gửi full object: field không được cấp nhưng giá trị y nguyên → không chặn.
    Đây là case làm hỏng cả tính năng nếu làm sai — mọi lần lưu sẽ 403."""
    grant(sale_role, "feedback")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={
            "feedback": "ghi chú mới",
            "total_amount": str(order_with_items.total_amount),
            "phone": order_with_items.phone,
        },
        content_type="application/json",
    )

    assert res.status_code == 200
    order_with_items.refresh_from_db()
    assert order_with_items.feedback == "ghi chú mới"


def test_blocked_fields_lists_every_violation(sale_client, sale_role, order_with_items):
    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "1", "phone": "0900000009"},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert set(res.json()["blocked_fields"]) == {"total_amount", "phone"}


def test_channel_grant_uses_declared_field_name(
    sale_client, sale_role, order_with_items, another_channel
):
    """channel_code là tên field; validated_data lại dùng khoá 'channel'. Cấp
    'channel_code' phải có tác dụng thật."""
    grant(sale_role, "channel_code")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"channel_code": another_channel.code},
        content_type="application/json",
    )

    assert res.status_code == 200


def test_superuser_bypasses_field_grants(admin_client, order_with_items):
    res = admin_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "123000"},
        content_type="application/json",
    )

    assert res.status_code == 200


def test_crm_needs_change_without_grant_is_rejected(
    sale_client, sale_role, order_with_items
):
    """`needs` là quan hệ 1-N GHI ĐƯỢC (`_sync_assessment_needs` ở tầng service). Coi
    nó là khoá echo = user 0 grant PATCH `{"crm": {"needs": [...]}}` ghi đè được nhu
    cầu khách hàng — fail-open lẻn vào giữa hệ fail-closed."""
    from apps.orders.models import NeedGroup, NeedType

    group = NeedGroup.objects.create(code="suc-khoe", name_vi="Sức khoẻ")
    NeedType.objects.create(code="giam-can", group=group, name_vi="Giảm cân")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"crm": {"needs": [{"type_code": "giam-can"}]}},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["crm.needs"]


def test_crm_needs_resent_unchanged_is_allowed(sale_client, sale_role, order_with_items):
    """FE spread nguyên `row.crm` nên payload LUÔN kèm `needs`. Chặn theo sự CÓ MẶT là
    403 oan mọi lần lưu CRM — lỗi đã xảy ra một vòng, không được lặp lại."""
    from apps.orders.models import NeedGroup, NeedType, OrderAssessment, OrderNeed

    # Cấp đúng hạt mà test này sửa;  CỐ Ý không cấp để chứng minh gửi lại
    # y nguyên không bị tính là đổi.
    grant(sale_role, "crm.internal_note")
    group = NeedGroup.objects.create(code="suc-khoe", name_vi="Sức khoẻ")
    need_type = NeedType.objects.create(code="giam-can", group=group, name_vi="Giảm cân")
    assessment = OrderAssessment.objects.create(
        order=order_with_items, internal_note="Ghi chu cu"
    )
    OrderNeed.objects.create(order_assessment=assessment, need_type=need_type)

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={
            "crm": {
                "internal_note": "Ghi chu moi",
                "needs": [{"type_code": "giam-can", "group_code": "suc-khoe"}],
            }
        },
        content_type="application/json",
    )

    assert res.status_code == 200, res.json()
    assessment.refresh_from_db()
    assert assessment.internal_note == "Ghi chu moi"


def test_crm_needs_resent_unchanged_without_grant_is_allowed(
    sale_client, sale_role, order_with_items
):
    """Chiều NGƯỢC của lỗ `needs`: role KHÔNG có grant `crm`, PATCH gửi lại
    `crm.needs` y nguyên giá trị đang lưu → phải 200, không phải 403 oan.

    `test_crm_needs_resent_unchanged_is_allowed` ở trên cấp sẵn `crm` nên guard loại
    field khỏi `ungranted` trước khi chạm `_needs_changed` — không khoá được gì. Ở
    đây `crm` rơi đúng vào `ungranted` (có trong payload, không có grant), nên nếu
    `_needs_changed` so sai (báo "đã đổi" dù giống hệt) thì mọi lần lưu CRM của role
    thiếu quyền `crm` sẽ bị chặn oan."""
    from apps.orders.models import NeedGroup, NeedType, OrderAssessment, OrderNeed

    grant(sale_role, "feedback")
    group = NeedGroup.objects.create(code="suc-khoe", name_vi="Sức khoẻ")
    need_type = NeedType.objects.create(code="giam-can", group=group, name_vi="Giảm cân")
    assessment = OrderAssessment.objects.create(
        order=order_with_items, internal_note="Ghi chu cu"
    )
    OrderNeed.objects.create(order_assessment=assessment, need_type=need_type)

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={
            "feedback": "ghi chu moi",
            "crm": {"needs": [{"type_code": "giam-can", "group_code": "suc-khoe"}]},
        },
        content_type="application/json",
    )

    assert res.status_code == 200, res.json()
    order_with_items.refresh_from_db()
    assert order_with_items.feedback == "ghi chu moi"


def test_serializer_derived_fields_are_not_blocked(
    sale_client, sale_role, order_with_items
):
    """`OrderInlineUpdateSerializer.validate()` TỰ SINH province/district/ward từ
    *_code vào `validated_data`. FE strip được key USER gửi, không strip nổi key
    SERIALIZER thêm vào → role có `province_code` mà không có `province` sẽ ăn 403
    "không có quyền sửa Tỉnh/TP" mỗi lần lưu dù không hề đụng địa chỉ (rất phổ biến:
    đơn KiotViet/landing/import có tên tỉnh khác tên chuẩn hoá từ mã)."""
    grant(sale_role, "province_code", "district_code", "ward_code")
    # Tên tỉnh đang lưu KHÁC tên chuẩn hoá từ mã — đúng hiện trạng đơn nhập tay/import.
    order_with_items.province = "TP HCM (nhap tay)"
    order_with_items.province_code = ""
    order_with_items.save(update_fields=["province", "province_code"])

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"province_code": "79"},
        content_type="application/json",
    )

    assert res.status_code == 200, res.json()
    order_with_items.refresh_from_db()
    assert order_with_items.province_code == "79"
    # Field dẫn xuất vẫn được ghi bình thường — chỉ guard bỏ qua, service không bỏ.
    assert order_with_items.province != "TP HCM (nhap tay)"


def test_directly_sent_derived_field_is_still_blocked(
    sale_client, sale_role, order_with_items
):
    """Chỉ field DẪN XUẤT mới được miễn. User tự gửi thẳng `province` mà không có
    grant vẫn phải bị chặn như cũ — nếu không, I2 biến thành lỗ fail-open."""
    grant(sale_role, "province_code")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"province": "Tinh Khac Han"},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["province"]


def test_items_change_without_grant_is_rejected(sale_client, sale_role, order_with_items):
    payload = [
        {"sku": i.sku, "quantity": i.quantity + 1} for i in order_with_items.items.all()
    ]

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"items": payload},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["items"]


def test_crm_granularity_separates_note_from_sales_status(
    sale_client, sale_role, order_with_items
):
    """Mục đích của cả tính năng: cấm sửa Trạng thái sale KHÔNG được cấm sửa Ghi chú.

    Trước khi tách hạt, cả khối CSKH chung một quyền nên hai thao tác này không thể
    phân biệt được.
    """
    from apps.orders.models import OrderAssessment

    grant(sale_role, "crm.internal_note")
    assessment = OrderAssessment.objects.create(
        order=order_with_items, internal_note="Ghi chu cu", sales_status_code="closed"
    )

    allowed = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"crm": {"internal_note": "Ghi chu moi", "sales_status_code": "closed"}},
        content_type="application/json",
    )
    assert allowed.status_code == 200, allowed.json()
    assessment.refresh_from_db()
    assert assessment.internal_note == "Ghi chu moi"

    blocked = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"crm": {"internal_note": "Ghi chu moi", "sales_status_code": "no_answer_1"}},
        content_type="application/json",
    )
    assert blocked.status_code == 403
    assert blocked.json()["blocked_fields"] == ["crm.sales_status_code"]
    assert "Trạng thái sale" in blocked.json()["detail"]
