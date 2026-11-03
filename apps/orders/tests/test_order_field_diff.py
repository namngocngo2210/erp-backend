"""Comparator field đơn: gửi lại giá trị CŨ không được tính là đổi.

Đây là điều kiện sống của guard quyền field: form đơn gửi full object mỗi lần lưu,
một false positive ở đây là 403 oan cho người dùng hợp lệ.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.orders.field_diff import _parse_slip_codes, changed_fields, field_changed


@pytest.mark.django_db
def test_resending_current_values_is_not_a_change(order_with_items):
    order = order_with_items
    validated = {
        "phone": order.phone,
        "total_amount": Decimal(str(order.total_amount)),
        "status": order.status,
        "channel": order.channel,
        "created_at": order.created_at,
        "is_export_vat": order.is_export_vat,
    }

    assert changed_fields(order, validated, list(validated)) == []


@pytest.mark.django_db
def test_foreign_key_compares_by_pk(order_with_items, another_channel):
    order = order_with_items

    assert not field_changed(order, "channel", order.channel)
    assert field_changed(order, "channel", another_channel)


@pytest.mark.django_db
def test_nullable_fk_resending_none_is_not_a_change(order_with_items):
    """`kiotviet_channel`/`fulfillment_warehouse` là FK NULLABLE (đơn chưa gán
    kênh KiotViet / kho) — trường hợp thực tế nhất là form resend `None` cho ô
    chưa từng điền, không phải trường hợp FK bắt buộc như `channel` ở test trên.
    `_fk_pk(None) != getattr(order, f"{field}_id", None)` phải ra `None != None`
    = False, không phải chặn oan mọi lần lưu đơn chưa gán kho/kênh KV."""
    order = order_with_items
    assert order.kiotviet_channel_id is None
    assert order.fulfillment_warehouse_id is None

    assert not field_changed(order, "kiotviet_channel", None)
    assert not field_changed(order, "fulfillment_warehouse", None)

    # Đổi từ "chưa gán" sang "có gán" vẫn phải bắt được — không phải None luôn
    # luôn "không đổi". Không cần model thật: `_fk_pk` chỉ đọc `.pk`.
    other = SimpleNamespace(pk=999999)
    assert field_changed(order, "kiotviet_channel", other)
    assert field_changed(order, "fulfillment_warehouse", other)


@pytest.mark.django_db
def test_datetime_compares_across_timezones(order_with_items):
    """Cùng một thời điểm, khác biểu diễn múi giờ → KHÔNG phải đổi."""
    order = order_with_items
    same_instant_other_tz = order.created_at.astimezone(
        dt_timezone(timedelta(hours=7))
    )

    assert not field_changed(order, "created_at", same_instant_other_tz)
    assert field_changed(order, "created_at", order.created_at + timedelta(minutes=1))


@pytest.mark.django_db
def test_datetime_unparseable_value_is_fail_closed(order_with_items):
    """Không parse nổi thành datetime có tz → coi như đã đổi, không cho ghi lọt."""
    order = order_with_items

    assert field_changed(order, "created_at", "not-a-datetime")
    assert field_changed(order, "created_at", None)
    naive = datetime(2026, 8, 17, 12, 0, 0)
    assert field_changed(order, "created_at", naive)


@pytest.mark.django_db
def test_date_field_has_no_timezone_trap(order_with_items):
    order = order_with_items
    order.return_date = date(2026, 8, 17)
    order.save(update_fields=["return_date"])

    assert not field_changed(order, "return_date", date(2026, 8, 17))
    assert field_changed(order, "return_date", date(2026, 8, 18))


@pytest.mark.django_db
def test_branch_id_is_a_string_not_a_foreign_key(order_with_items):
    """branch_id là CharField dù tên kết thúc bằng _id — suy kiểu theo tên là chặn oan."""
    order = order_with_items
    order.branch_id = "CN01"
    order.save(update_fields=["branch_id"])

    assert not field_changed(order, "branch_id", "CN01")
    assert field_changed(order, "branch_id", "CN02")


@pytest.mark.django_db
def test_boolean_field(order_with_items):
    order = order_with_items
    order.customer_return = False
    order.save(update_fields=["customer_return"])

    assert not field_changed(order, "customer_return", False)
    assert field_changed(order, "customer_return", True)


@pytest.mark.django_db
def test_items_unchanged_when_resent_identically(order_with_items):
    order = order_with_items
    payload = [
        {"sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price}
        for i in order.items.all()
    ]

    assert not field_changed(order, "items", payload)


@pytest.mark.django_db
def test_items_changed_on_quantity_bump(order_with_items):
    order = order_with_items
    payload = [
        {"sku": i.sku, "quantity": i.quantity + 1, "unit_price": i.unit_price}
        for i in order.items.all()
    ]

    assert field_changed(order, "items", payload)


# ---------------------------------------------------------------------------
# validated_data dùng khoá theo `source` của serializer, khác tên field khai báo.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_changed_fields_translates_channel_code_to_channel_key(order_with_items, another_channel):
    """`channel_code` khai báo trên serializer nhưng khoá thật trong validated_data
    là `channel` (source=). Tra sai khoá = guard không bao giờ bắn."""
    order = order_with_items

    assert changed_fields(order, {"channel": another_channel}, ["channel_code"]) == [
        "channel_code"
    ]
    assert changed_fields(order, {"channel": order.channel}, ["channel_code"]) == []


@pytest.mark.django_db
def test_changed_fields_returns_declared_field_name_not_validated_key(order_with_items):
    """Người dùng phải thấy nhãn theo field khai báo (vd 'sku'), không phải khoá nội bộ."""
    order = order_with_items

    result = changed_fields(order, {"sku": "SKU-KHAC-HAN"}, ["sku"])

    assert result == ["sku"]


# ---------------------------------------------------------------------------
# nested_changed: crm (Assessment), recall (OrderRecall), recall_entries (1-N)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_crm_static_field_resent_unchanged_is_not_a_change(order_with_items):
    from apps.orders.models import OrderAssessment

    order = order_with_items
    OrderAssessment.objects.create(order=order, internal_note="Ghi chu cu")
    order = type(order).objects.get(pk=order.pk)

    assert not field_changed(order, "crm", {"internal_note": "Ghi chu cu"})
    assert field_changed(order, "crm", {"internal_note": "Ghi chu moi"})


@pytest.mark.django_db
def test_crm_dynamic_field_lives_in_extra_fields_not_as_attribute(order_with_items):
    """Field động (registry assessment_field_defs) nằm trong `extra_fields` JSON,
    KHÔNG phải attribute trực tiếp trên OrderAssessment. So bằng getattr() ngây thơ
    sẽ luôn đọc None → false positive mỗi lần form gửi lại field động cũ."""
    from apps.orders.models import OrderAssessment

    order = order_with_items
    OrderAssessment.objects.create(order=order, extra_fields={"custom_field_x": "gia-tri-cu"})
    order = type(order).objects.get(pk=order.pk)

    assert not field_changed(order, "crm", {"custom_field_x": "gia-tri-cu"})
    assert field_changed(order, "crm", {"custom_field_x": "gia-tri-moi"})


@pytest.mark.django_db
def test_crm_missing_assessment_only_changed_when_payload_has_value(order_with_items):
    order = order_with_items

    assert not field_changed(order, "crm", {})
    assert field_changed(order, "crm", {"internal_note": "Ghi chu"})


def _add_need(assessment, type_code: str, group_code: str = "suc-khoe"):
    """Gắn một OrderNeed thật vào assessment (dựng NeedGroup/NeedType nếu chưa có)."""
    from apps.orders.models import NeedGroup, NeedType, OrderNeed

    group, _ = NeedGroup.objects.get_or_create(
        code=group_code, defaults={"name_vi": group_code}
    )
    need_type, _ = NeedType.objects.get_or_create(
        code=type_code, defaults={"group": group, "name_vi": type_code}
    )
    return OrderNeed.objects.create(order_assessment=assessment, need_type=need_type)


@pytest.mark.django_db
def test_crm_full_shaped_echo_payload_resent_unchanged_is_not_a_change(order_with_items):
    """Frontend spread NGUYÊN object `crm` cũ rồi ghi đè field vừa sửa
    (`crm: {...row.crm, [field]: value}` — OrdersTable/cell-renderers.tsx), nên
    payload THẬT luôn kèm các key `OrderCRMSerializer` (read) TỰ SINH:
    `next_reminder_at` (SerializerMethodField), `*_label` (twin hiển thị của field
    code), `needs` (quan hệ 1-N, echo lại y nguyên), và các key meta khác
    (updated_by_name/created_at/updated_at/ai_*/events/extra_fields). Trước fix, so
    các key này như field thật → luôn "đã đổi" → 403 oan GẦN NHƯ MỌI LẦN người dùng
    lưu panel CRM."""
    from apps.orders.models import OrderAssessment

    order = order_with_items
    assessment = OrderAssessment.objects.create(
        order=order, internal_note="Ghi chu that", sales_status_code="interested"
    )
    _add_need(assessment, "massage-vai-gay")
    order = type(order).objects.get(pk=order.pk)

    payload = {
        # Field thật, gửi lại y nguyên — phải so đúng, không được ăn theo echo.
        "internal_note": "Ghi chu that",
        "sales_status_code": "interested",
        # Twin hiển thị (*_label) — giá trị KHÔNG khớp gì với DB, phải bị bỏ qua
        # hoàn toàn, không so như field thật.
        "sales_status_label": "Nhãn hiển thị bất kỳ",
        "customer_classification_label": "Nhãn khác không liên quan",
        # `needs` echo lại đúng nhu cầu đang lưu (kèm cả khoá chỉ-đọc do
        # OrderNeedSerializer trả ra) → KHÔNG được tính là đổi.
        "needs": [
            {
                "type_code": "massage-vai-gay",
                "group_code": "suc-khoe",
                "type_name": "Massage vai gáy",
                "group_name": "Sức khoẻ",
            }
        ],
        # SerializerMethodField tính từ sale_reminder_service.
        "next_reminder_at": "2026-08-20T10:00:00+07:00",
        # Key meta khác trong OrderCRMSerializer output.
        "updated_by_name": "Nguyen Van A",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-10T00:00:00Z",
        "ai_filled_fields": {"internal_note": "ai"},
        "ai_open_insights": [{"title": "x"}],
        "events": [{"type": "note"}],
        "extra_fields": {"legacy_key": "legacy_value"},
    }

    assert not field_changed(order, "crm", payload)


@pytest.mark.django_db
def test_crm_real_field_change_still_detected_amid_echo_keys(order_with_items):
    """Bỏ qua khoá echo không được nuốt luôn thay đổi THẬT đi kèm trong cùng payload."""
    from apps.orders.models import OrderAssessment

    order = order_with_items
    OrderAssessment.objects.create(order=order, internal_note="Ghi chu cu")
    order = type(order).objects.get(pk=order.pk)

    payload = {
        "internal_note": "Ghi chu MOI",
        "sales_status_label": "Nhãn bất kỳ",
        "needs": [],
        "next_reminder_at": None,
    }

    assert field_changed(order, "crm", payload)


# ---------------------------------------------------------------------------
# `needs` — quan hệ 1-N GHI ĐƯỢC (`_sync_assessment_needs` ở tầng service). Bỏ qua
# nó trong diff = user 0 grant vẫn ghi đè được nhu cầu khách hàng (fail-open).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_needs_resent_identically_is_not_a_change(order_with_items):
    """Thứ tự không mang nghĩa: gửi lại đúng tập nhu cầu (đảo thứ tự) = không đổi."""
    from apps.orders.models import OrderAssessment

    order = order_with_items
    assessment = OrderAssessment.objects.create(order=order)
    _add_need(assessment, "massage-vai-gay")
    _add_need(assessment, "giam-can")
    order = type(order).objects.get(pk=order.pk)

    payload = {"needs": [{"type_code": "giam-can"}, {"type_code": "massage-vai-gay"}]}

    assert not field_changed(order, "crm", payload)


@pytest.mark.django_db
def test_needs_added_removed_or_replaced_is_a_change(order_with_items):
    from apps.orders.models import OrderAssessment

    order = order_with_items
    assessment = OrderAssessment.objects.create(order=order)
    _add_need(assessment, "massage-vai-gay")
    order = type(order).objects.get(pk=order.pk)

    added = {"needs": [{"type_code": "massage-vai-gay"}, {"type_code": "giam-can"}]}
    removed = {"needs": []}
    replaced = {"needs": [{"type_code": "giam-can"}]}

    assert field_changed(order, "crm", added)
    assert field_changed(order, "crm", removed)
    assert field_changed(order, "crm", replaced)


@pytest.mark.django_db
def test_needs_added_to_order_without_assessment_is_a_change(order_with_items):
    """Đơn chưa có assessment: gửi `needs` rỗng không phải đổi, gửi nhu cầu là đổi."""
    order = order_with_items

    assert not field_changed(order, "crm", {"needs": []})
    assert field_changed(order, "crm", {"needs": [{"type_code": "giam-can"}]})


@pytest.mark.django_db
def test_needs_signature_reads_need_type_instance(order_with_items):
    """Payload dựng tay ở tầng service mang instance `need_type` thay vì `type_code`
    (`_sync_assessment_needs` chấp nhận cả hai) — chữ ký phải đọc được cả hai dạng."""
    from apps.orders.models import NeedType, OrderAssessment

    order = order_with_items
    assessment = OrderAssessment.objects.create(order=order)
    _add_need(assessment, "massage-vai-gay")
    order = type(order).objects.get(pk=order.pk)
    need_type = NeedType.objects.get(code="massage-vai-gay")

    assert not field_changed(order, "crm", {"needs": [{"need_type": need_type}]})


@pytest.mark.django_db
def test_recall_compares_return_slip_code(order_with_items):
    """OrderRecall CHỈ còn `return_slip_code` (mảng) — mã/ngày thu hồi đã tách sang
    OrderRecallEntry. Payload gửi chuỗi phẩy, model lưu list đã chuẩn hoá."""
    from apps.orders.models import OrderRecall

    order = order_with_items
    OrderRecall.objects.create(order=order, return_slip_code=["TH-001", "TH-002"])
    order = type(order).objects.get(pk=order.pk)

    assert not field_changed(order, "recall", {"return_slip_code": "TH-001,TH-002"})
    assert field_changed(order, "recall", {"return_slip_code": "TH-001,TH-003"})


@pytest.mark.django_db
def test_recall_not_sent_is_not_a_change(order_with_items):
    order = order_with_items

    assert not field_changed(order, "recall", None)
    assert not field_changed(order, "recall", {})


@pytest.mark.django_db
def test_recall_entries_unchanged_when_resent_identically(order_with_items):
    from apps.orders.models import OrderRecallEntry

    order = order_with_items
    OrderRecallEntry.objects.create(
        order=order,
        recall_code="TH-001",
        recall_created_date=date(2026, 8, 1),
        recall_received_date=date(2026, 8, 5),
    )
    order = type(order).objects.get(pk=order.pk)

    payload = [
        {
            "recall_code": e.recall_code,
            "recall_created_date": e.recall_created_date,
            "recall_received_date": e.recall_received_date,
        }
        for e in order.recall_entries.all()
    ]

    assert not field_changed(order, "recall_entries", payload)


@pytest.mark.django_db
def test_recall_entries_changed_on_new_entry(order_with_items):
    from apps.orders.models import OrderRecallEntry

    order = order_with_items
    OrderRecallEntry.objects.create(order=order, recall_code="TH-001")
    order = type(order).objects.get(pk=order.pk)

    assert field_changed(
        order,
        "recall_entries",
        [{"recall_code": "TH-001"}, {"recall_code": "TH-002"}],
    )


# ---------------------------------------------------------------------------
# Đối chiếu song song với chuẩn hoá phía service — trôi logic phải đỏ ngay, không
# lặng lẽ lệch hành vi giữa `field_diff.py` (thuần) và tầng service (đã dùng thật).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "TH-001",
        "TH-001,TH-002",
        " TH-001 , TH-002 ,, TH-001 ",
        ["TH-001", "TH-002"],
        ["  TH-003  ", "TH-003"],
        "a" * 100,
    ],
)
def test_parse_slip_codes_matches_service_normalization(raw):
    """`field_diff._parse_slip_codes` là bản sao thuần của
    `OrderServiceHelpers._parse_slip_codes` (không import service để giữ module
    thuần). Không gì buộc hai bên đồng bộ khi service đổi rule — test này đối
    chiếu trực tiếp, trôi logic là đỏ ngay thay vì lặng lẽ lệch hành vi ở production."""
    from apps.orders.application.services.order_service_helpers import (
        _parse_slip_codes as service_parse_slip_codes,
    )

    assert _parse_slip_codes(raw) == service_parse_slip_codes(raw)


def test_crm_echo_keys_match_service_definition():
    """`field_diff._CRM_ECHO_KEYS`/`_is_crm_label_echo_key` là bản sao thuần của
    `OrderServiceHelpers._CRM_SERIALIZER_ECHO_KEYS`/`_is_label_echo_key`. Service
    thêm/bớt key echo mà quên cập nhật field_diff phải đỏ ở CI, không phải bị phát
    hiện bằng 403 oan hàng loạt ở production."""
    from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
    from apps.orders.assessment_fields import ASSESSMENT_FIELDS_BY_NAME
    from apps.orders.field_diff import _CRM_ECHO_KEYS, _is_crm_label_echo_key

    assert OrderServiceHelpers._CRM_SERIALIZER_ECHO_KEYS == _CRM_ECHO_KEYS
    # "needs" là field GHI ĐƯỢC (`_sync_assessment_needs`), không phải echo — nó phải
    # nằm ngoài cả hai tập, nếu không guard lại fail-open như trước fix.
    assert "needs" not in _CRM_ECHO_KEYS

    sample_keys = [f"{name}_label" for name in ASSESSMENT_FIELDS_BY_NAME] + [
        "made_up_label",
        "totally_unknown_field",
        "internal_note",
    ]
    for key in sample_keys:
        assert _is_crm_label_echo_key(key) == OrderServiceHelpers._is_label_echo_key(key)
