"""Comparator CSKH phải chỉ ra ĐÚNG trường con nào đổi, không phải cả khối.

Trước đây `crm` là một hạt quyền duy nhất nên comparator chỉ cần trả bool. Từ khi
quyền xuống tới từng trường đánh giá, guard cần biết chính xác trường nào đổi —
nếu không, sale bị cấm sửa "Trạng thái sale" sẽ bị chặn cả khi chỉ sửa ghi chú.

False positive ở đây = 403 oan mọi lần lưu CSKH (đã xảy ra một lần với khoá echo),
false negative = lọt quyền. Cả hai đều nghiêm trọng.
"""

import pytest

from apps.orders.field_diff import changed_fields, crm_changed_subfields


@pytest.mark.django_db
class TestCrmChangedSubfields:
    def test_resending_everything_unchanged_reports_nothing(self, order_with_assessment):
        order, payload = order_with_assessment
        assert crm_changed_subfields(order, payload) == set()

    def test_only_the_edited_static_field_is_reported(self, order_with_assessment):
        order, payload = order_with_assessment
        assert crm_changed_subfields(order, {**payload, "internal_note": "ghi chu MOI"}) == {
            "crm.internal_note"
        }

    def test_two_edits_report_both(self, order_with_assessment):
        order, payload = order_with_assessment
        changed = crm_changed_subfields(
            order, {**payload, "internal_note": "khac", "sales_status_code": "no_answer_1"}
        )
        assert changed == {"crm.internal_note", "crm.sales_status_code"}

    def test_dynamic_field_falls_into_the_extra_bucket(self, order_with_assessment):
        """Trường động khai qua admin không có trong catalog tĩnh → gom về crm.__extra__."""
        order, payload = order_with_assessment
        assert crm_changed_subfields(order, {**payload, "truong_dong_x": "gia tri moi"}) == {
            "crm.__extra__"
        }

    def test_echo_keys_never_count_as_a_change(self, order_with_assessment):
        """Khoá do serializer TÍNH RA (nhãn, next_reminder_at) không phải cột ghi được."""
        order, payload = order_with_assessment
        noisy = {
            **payload,
            "sales_status_label": "Nhãn bất kỳ",
            "customer_classification_label": "Nhãn khác",
            "next_reminder_at": "2026-09-01T00:00:00Z",
        }
        assert crm_changed_subfields(order, noisy) == set()


@pytest.mark.django_db
class TestChangedFieldsGranularity:
    def test_editing_a_granted_crm_field_is_not_blocked(self, order_with_assessment):
        """Đây là mục đích của cả tính năng: cấm sửa Trạng thái sale KHÔNG được chặn
        người ta sửa Ghi chú nội bộ."""
        order, payload = order_with_assessment
        ungranted = ["crm.sales_status_code"]
        validated = {"crm": {**payload, "internal_note": "ghi chu MOI"}}

        assert changed_fields(order, validated, ungranted) == []

    def test_editing_an_ungranted_crm_field_is_blocked(self, order_with_assessment):
        order, payload = order_with_assessment
        ungranted = ["crm.sales_status_code"]
        validated = {"crm": {**payload, "sales_status_code": "no_answer_1"}}

        assert changed_fields(order, validated, ungranted) == ["crm.sales_status_code"]

    def test_crm_absent_from_payload_blocks_nothing(self, order_with_assessment):
        order, _payload = order_with_assessment
        assert changed_fields(order, {"feedback": "abc"}, ["crm.sales_status_code"]) == []
