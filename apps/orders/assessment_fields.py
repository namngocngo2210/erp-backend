"""Single source of truth for OrderAssessment CRM-editable fields.

Adding a new assessment CRM field? Add ONE entry here and the upsert/duplicate
helpers + DRF serializer pick it up automatically.
"""
from dataclasses import dataclass
from typing import Any

from apps.orders.crm_catalog import (
    AGE_GROUP_LABELS,
    CALL_DURATION_LABELS,
    CUSTOMER_CLASSIFICATION_LABELS,
    DELAX_SOURCE_LABELS,
    DELIVERY_BUSINESS_STATUS_LABELS,
    LIFESTYLE_HABIT_LABELS,
    NOT_CLOSED_REASON_LABELS,
    PAIN_DURATION_LABELS,
    PAIN_LEVEL_LABELS,
    POST_PURCHASE_RATING_LABELS,
    PURCHASE_BARRIER_LABELS,
    RELAX_TIME_LABELS,
    SALES_STATUS_LABELS,
    BOMB_REASON_GROUP_LABELS,
    BOMB_REASON_TYPE_LABELS,
)

TEXT_MAX_LEN = 4000  # for free-form text/textarea fields
ARRAY_MAX_ITEMS = 32


@dataclass(frozen=True)
class AssessmentField:
    name: str
    kind: str  # "code" | "code_array" | "text" | "bool" | "int" | "datetime"
    null_default: Any = None
    whitelist: dict | None = None  # for kind in {"code", "code_array"}
    max_length: int | None = None  # for kind == "text"


ASSESSMENT_FIELDS: tuple[AssessmentField, ...] = (
    AssessmentField("customer_classification_code", "code", whitelist=CUSTOMER_CLASSIFICATION_LABELS),
    AssessmentField("customer_attitude_raw", "text", null_default="", max_length=TEXT_MAX_LEN),
    AssessmentField("sales_status_code", "code", whitelist=SALES_STATUS_LABELS),
    AssessmentField("not_closed_reason_code", "code", whitelist=NOT_CLOSED_REASON_LABELS),
    AssessmentField("delivery_business_status_code", "code", whitelist=DELIVERY_BUSINESS_STATUS_LABELS),
    AssessmentField("bomb_reason_group_code", "code", whitelist=BOMB_REASON_GROUP_LABELS),
    AssessmentField("bomb_reason_type_code", "code", whitelist=BOMB_REASON_TYPE_LABELS),
    AssessmentField("bomb_reason_text", "text", null_default="", max_length=TEXT_MAX_LEN),
    AssessmentField("internal_note", "text", null_default="", max_length=TEXT_MAX_LEN),
    AssessmentField("potential_level", "int"),
    # Cột tên là age_text vì lý do lịch sử nhưng chứa MÃ nhóm tuổi, không phải
    # text tự do. Giữ tên để không reset cấu hình cột đã lưu phía client.
    AssessmentField("age_text", "code", null_default="", whitelist=AGE_GROUP_LABELS),
    AssessmentField("occupation_code", "text", null_default="", max_length=64),
    AssessmentField("delax_source_codes", "code_array", null_default=[], whitelist=DELAX_SOURCE_LABELS),
    AssessmentField("pain_level_code", "code", whitelist=PAIN_LEVEL_LABELS),
    AssessmentField("pain_duration_code", "code", whitelist=PAIN_DURATION_LABELS),
    AssessmentField("lifestyle_habit_codes", "code_array", null_default=[], whitelist=LIFESTYLE_HABIT_LABELS),
    AssessmentField("used_massage_before", "bool"),
    AssessmentField("relax_time_codes", "code_array", null_default=[], whitelist=RELAX_TIME_LABELS),
    AssessmentField("purchase_barrier_codes", "code_array", null_default=[], whitelist=PURCHASE_BARRIER_LABELS),
    AssessmentField("comparing_brand_text", "text", null_default="", max_length=TEXT_MAX_LEN),
    AssessmentField("call_duration_code", "code", whitelist=CALL_DURATION_LABELS),
    AssessmentField("post_purchase_feedback_text", "text", null_default="", max_length=TEXT_MAX_LEN),
    AssessmentField("post_purchase_rating_code", "code", whitelist=POST_PURCHASE_RATING_LABELS),
    AssessmentField("next_followup_at", "datetime"),
)

# Trường KHÔNG được copy khi nhân bản đơn (`duplicate_order`, reset_progress=False).
# `delivery_business_status_code` là marker "đơn GỐC đã rời kho": guard chống xuất trùng
# (`_has_dispatched_delivery_state`, `_is_dispatched` ở lookup quét kho) đọc thẳng field
# này kể cả khi sổ tồn trống, nên bản sao kế thừa `dispatched` sẽ bị kho quét mã vận đơn
# MỚI báo "đã xuất kho trước đó" dù chưa hề trừ tồn — cùng lớp lỗi với is_export_vat/
# vat_link ở create_kwargs. Bản sao để trống, đúng luồng đơn chưa xuất.
NON_CLONED_ASSESSMENT_FIELDS: frozenset[str] = frozenset({"delivery_business_status_code"})

ASSESSMENT_FIELDS_BY_NAME: dict[str, AssessmentField] = {f.name: f for f in ASSESSMENT_FIELDS}
ASSESSMENT_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in ASSESSMENT_FIELDS)


class AssessmentValidationError(ValueError):
    """Raised when CRM payload contains invalid values for OrderAssessment."""


def coerce_and_validate(field_name: str, value: Any) -> Any:
    """Apply null-defaulting + type/whitelist/length validation for a single field.

    Returns the coerced value. Raises AssessmentValidationError on invalid input.
    """
    spec = ASSESSMENT_FIELDS_BY_NAME.get(field_name)
    if spec is None:
        return value

    if value is None and spec.null_default is not None:
        # text "" / array [] defaults; codes/bools/ints stay None
        return [] if isinstance(spec.null_default, list) else spec.null_default

    if value is None:
        return None

    if spec.kind == "text":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{field_name} must be a string")
        if spec.max_length is not None and len(value) > spec.max_length:
            raise AssessmentValidationError(
                f"{field_name} exceeds max length {spec.max_length} (got {len(value)})"
            )
        return value

    if spec.kind == "code":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{field_name} must be a string code")
        if spec.whitelist is not None and value and value not in spec.whitelist:
            raise AssessmentValidationError(f"{field_name}={value!r} is not a valid code")
        return value

    if spec.kind == "code_array":
        if not isinstance(value, (list, tuple)):
            raise AssessmentValidationError(f"{field_name} must be a list of codes")
        if len(value) > ARRAY_MAX_ITEMS:
            raise AssessmentValidationError(
                f"{field_name} exceeds max items {ARRAY_MAX_ITEMS} (got {len(value)})"
            )
        seen: list[str] = []
        for code in value:
            if not isinstance(code, str):
                raise AssessmentValidationError(f"{field_name} items must be strings")
            if spec.whitelist is not None and code not in spec.whitelist:
                raise AssessmentValidationError(f"{field_name} contains invalid code {code!r}")
            if code not in seen:
                seen.append(code)
        return seen

    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise AssessmentValidationError(f"{field_name} must be a boolean")
        return value

    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise AssessmentValidationError(f"{field_name} must be an integer")
        return value

    if spec.kind == "datetime":
        # Accept ISO-8601 strings (with or without 'Z') and datetime instances.
        from datetime import datetime

        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is None:
                raise AssessmentValidationError(
                    f"{field_name} must be an ISO-8601 datetime string"
                )
            dt = parsed
        else:
            raise AssessmentValidationError(f"{field_name} must be a datetime")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    return value
