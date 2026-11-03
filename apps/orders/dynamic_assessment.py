"""Registry trường đánh giá động: đọc/cache field defs + validate giá trị.

Cache plain dataclass (không cache model instance) để pickle gọn và không rò
queryset. TTL ngắn + invalidate chủ động khi save/delete nên admin sửa xong
tối đa 60s là mọi worker thấy — đủ cho reference data đổi vài lần/tháng.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.core.cache import cache

from apps.orders.assessment_fields import (
    ARRAY_MAX_ITEMS,
    TEXT_MAX_LEN,
    AssessmentValidationError,
)

logger = logging.getLogger(__name__)

REGISTRY_CACHE_KEY = "assessment_field_defs:active"
REGISTRY_CACHE_TTL = 60


@dataclass(frozen=True)
class DynamicFieldDef:
    code: str
    label: str
    kind: str
    options: tuple  # tuple[dict[str, str], ...] — ({"code", "label"}, ...)
    ai_enabled: bool
    ai_hint: str
    sort_order: int


def invalidate_registry_cache() -> None:
    try:
        cache.delete(REGISTRY_CACHE_KEY)
    except Exception:
        logger.warning("assessment registry cache delete failed", exc_info=True)


def get_active_defs() -> tuple[DynamicFieldDef, ...]:
    cached = None
    try:
        cached = cache.get(REGISTRY_CACHE_KEY)
    except Exception:
        logger.warning("assessment registry cache get failed, falling back to DB", exc_info=True)
    if cached is not None:
        return cached
    from apps.orders.models import AssessmentFieldDef

    defs = tuple(
        DynamicFieldDef(
            code=row.code,
            label=row.label,
            kind=row.kind,
            options=tuple(row.options or []),
            ai_enabled=row.ai_enabled,
            ai_hint=row.ai_hint,
            sort_order=row.sort_order,
        )
        for row in AssessmentFieldDef.objects.filter(is_active=True).order_by("sort_order", "id")
    )
    try:
        cache.set(REGISTRY_CACHE_KEY, defs, REGISTRY_CACHE_TTL)
    except Exception:
        logger.warning("assessment registry cache set failed", exc_info=True)
    return defs


def get_active_defs_by_code() -> dict[str, DynamicFieldDef]:
    return {spec.code: spec for spec in get_active_defs()}


def get_ai_enabled_defs() -> tuple[DynamicFieldDef, ...]:
    return tuple(spec for spec in get_active_defs() if spec.ai_enabled)


def option_codes(spec: DynamicFieldDef) -> tuple[str, ...]:
    return tuple(item["code"] for item in spec.options)


def coerce_and_validate_dynamic(spec: DynamicFieldDef, value: Any) -> Any:
    """Validate giá trị trường động theo kind. None đi qua (nghĩa là xoá key)."""
    if value is None:
        return None

    if spec.kind == "single_choice":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{spec.code} must be a string code")
        if value and value not in option_codes(spec):
            raise AssessmentValidationError(f"{spec.code}={value!r} is not a valid code")
        return value

    if spec.kind == "multi_choice":
        if not isinstance(value, (list, tuple)):
            raise AssessmentValidationError(f"{spec.code} must be a list of codes")
        if len(value) > ARRAY_MAX_ITEMS:
            raise AssessmentValidationError(f"{spec.code} exceeds max items {ARRAY_MAX_ITEMS}")
        allowed = option_codes(spec)
        seen: list[str] = []
        for code in value:
            if not isinstance(code, str) or code not in allowed:
                raise AssessmentValidationError(f"{spec.code} contains invalid code {code!r}")
            if code not in seen:
                seen.append(code)
        return seen

    if spec.kind == "text":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{spec.code} must be a string")
        if len(value) > TEXT_MAX_LEN:
            raise AssessmentValidationError(f"{spec.code} exceeds max length {TEXT_MAX_LEN}")
        return value

    if spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AssessmentValidationError(f"{spec.code} must be a number")
        return value

    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise AssessmentValidationError(f"{spec.code} must be a boolean")
        return value

    raise AssessmentValidationError(f"{spec.code} has unknown kind {spec.kind!r}")
