"""Field-level enforcement (drop hidden output / strip protected write).

Dùng field_policy (dict[section→dict[field→rule]]) tính 1 lần/request bởi resolver,
bơm vào serializer context. Không tạo SerializerMethodField per-field (tránh N+1).
"""

import logging

logger = logging.getLogger(__name__)

HIDDEN = "hidden"
READONLY = "readonly"

_RULE_RANK = {READONLY: 1, HIDDEN: 2}

# Nhóm field alias: field ở giá trị (vd "raw_address") kế thừa rule của field khoá
# (vd "address") — ẩn "address" thì ẩn luôn dữ liệu thô đi kèm, admin không cần khai
# rule riêng cho từng biến thể. Tổng quát để thêm alias khác sau này (vd phone <->
# phone_raw) mà không phải sửa lại drop_hidden_fields/strip_protected_write_fields.
FIELD_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "address": ("raw_address",),
}


def _expand_field_aliases(section_policy: dict) -> dict:
    """Áp rule của field gốc cho các field alias của nó (xem FIELD_ALIAS_GROUPS).

    Field alias giữ rule hạn chế hơn nếu bản thân nó đã có rule riêng (field rule
    "ít hạn chế nhất thắng" là quy tắc union nhiều role — ở đây ngược lại: alias
    không được LỎNG hơn field gốc, nên lấy rule hạn chế hơn giữa 2 bên).
    """
    if not section_policy:
        return section_policy
    expanded = dict(section_policy)
    for source_field, aliases in FIELD_ALIAS_GROUPS.items():
        source_rule = section_policy.get(source_field)
        if source_rule is None:
            continue
        for alias in aliases:
            current_rule = expanded.get(alias)
            if current_rule is None or _RULE_RANK[source_rule] > _RULE_RANK.get(current_rule, 0):
                expanded[alias] = source_rule
    return expanded


def drop_hidden_fields(data: dict, section_policy: dict) -> dict:
    """Bỏ field rule=hidden khỏi output. readonly vẫn hiển thị (chỉ chặn write)."""
    if not section_policy:
        return data
    section_policy = _expand_field_aliases(section_policy)
    hidden = {field for field, rule in section_policy.items() if rule == HIDDEN}
    if not hidden:
        return data
    return {k: v for k, v in data.items() if k not in hidden}


def strip_protected_write_fields(data: dict, section_policy: dict) -> tuple[dict, list]:
    """Loại field hidden|readonly khỏi payload ghi. Trả (data_sạch, danh_sách_bị_loại)."""
    if not section_policy:
        return data, []
    section_policy = _expand_field_aliases(section_policy)
    protected = {
        field for field, rule in section_policy.items() if rule in (HIDDEN, READONLY)
    }
    stripped = [k for k in data if k in protected]
    if not stripped:
        return data, []
    cleaned = {k: v for k, v in data.items() if k not in protected}
    return cleaned, stripped


class FieldPolicyOutputMixin:
    """Mixin cho ModelSerializer: drop field hidden ở output.

    Serializer phải khai báo `field_section = "<section>"`. View truyền context
    `field_policy={"<section>": {...}}`.
    """

    field_section: str = ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        policy = (self.context.get("field_policy") or {}).get(self.field_section, {})
        return drop_hidden_fields(data, policy)


class FieldPolicyWriteMixin:
    """Mixin cho serializer ghi: strip field protected trước khi validate.

    Mặc định strip + log (không 403). Serializer khai báo `field_section`.
    """

    field_section: str = ""

    def to_internal_value(self, data):
        policy = (self.context.get("field_policy") or {}).get(self.field_section, {})
        cleaned, stripped = strip_protected_write_fields(dict(data), policy)
        if stripped:
            logger.info(
                "field_policy stripped write fields section=%s fields=%s",
                self.field_section,
                stripped,
            )
        return super().to_internal_value(cleaned)
