"""Alias-group field policy (task 2.5): ẩn `address` phải kéo theo ẩn `raw_address`."""
from __future__ import annotations

from apps.users.field_policy import (
    HIDDEN,
    READONLY,
    drop_hidden_fields,
    strip_protected_write_fields,
)


class TestFieldAliasGroups:
    def test_hidden_address_also_hides_raw_address(self):
        data = {"id": 1, "address": "123 Le Loi", "raw_address": "123 le loi, q1", "name": "K"}
        policy = {"address": HIDDEN}

        result = drop_hidden_fields(data, policy)

        assert "address" not in result
        assert "raw_address" not in result
        assert result == {"id": 1, "name": "K"}

    def test_no_rule_on_address_leaves_raw_address_untouched(self):
        data = {"id": 1, "address": "123 Le Loi", "raw_address": "123 le loi, q1"}
        result = drop_hidden_fields(data, {})
        assert result == data

    def test_readonly_address_does_not_hide_raw_address(self):
        # readonly chỉ chặn ghi, không ẩn output — alias cũng chỉ nên kế thừa readonly.
        data = {"address": "123 Le Loi", "raw_address": "123 le loi, q1"}
        result = drop_hidden_fields(data, {"address": READONLY})
        assert result == data

    def test_raw_address_explicit_hidden_rule_still_wins_when_stricter(self):
        # raw_address tự có rule riêng nghiêm hơn address (readonly) -> vẫn ẩn theo alias.
        data = {"address": "123 Le Loi", "raw_address": "123 le loi, q1"}
        policy = {"address": HIDDEN, "raw_address": READONLY}
        result = drop_hidden_fields(data, policy)
        assert "raw_address" not in result
        assert "address" not in result

    def test_strip_protected_write_fields_also_expands_alias(self):
        data = {"address": "New addr", "raw_address": "new addr raw", "name": "K"}
        cleaned, stripped = strip_protected_write_fields(data, {"address": HIDDEN})
        assert "address" not in cleaned
        assert "raw_address" not in cleaned
        assert set(stripped) == {"address", "raw_address"}
