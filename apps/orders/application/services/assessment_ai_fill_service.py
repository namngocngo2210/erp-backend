"""Ghi kết quả AI extract vào OrderAssessment — CHỈ điền field đang trống.

Đi qua đúng cửa mà sale đang đi (`_upsert_order_assessment`) để không tồn tại
nhánh logic thứ hai lệch với luồng nhập tay: validate, sync needs, auto-block
spam, journey event đều dùng chung.

Ba luật cứng (docs/superpowers/specs/2026-07-24-...-design.md §7):
1. Chỉ ghi field đang trống — không bao giờ đè giá trị sale đã nhập.
2. Array chỉ ghi khi rỗng, KHÔNG union: union thì sale xoá xong lần sau AI điền lại.
3. Không có trích dẫn transcript thì không ghi — chốt chặn chống hallucination.

Trường động (extra_fields) đi qua đúng 3 luật trên. Giỏ mở (ai_open_insights)
là NGOẠI LỆ có chủ đích: append bất kể assessment đang có gì — nó không thuộc
field nào để mà "đã điền", chỉ dedupe theo (topic, content) và cap
`OPEN_INSIGHT_MAX_ITEMS` để không phình vô hạn.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.orders.ai_extract_contract import AI_ARRAY_FIELDS, AI_SCALAR_FIELDS, OPEN_INSIGHTS_KEY
from apps.orders.models import Order, OrderAssessment, OrderNeed

OPEN_INSIGHT_MAX_ITEMS = 100


def _is_empty(value) -> bool:
    """False/0 là giá trị THẬT (used_massage_before=False), không phải trống."""
    return value is None or value == "" or value == []


@transaction.atomic
def apply_ai_insights(*, order: Order, clean: dict, call_record_id: int) -> list[str]:
    """Điền field trống (kể cả trường động) + append giỏ mở từ payload đã normalize.

    Trả tên field đã điền, kèm ``"open_insights"`` nếu có item mới được append.
    """
    from apps.orders.application.services.order_service_helpers import OrderServiceHelpers

    # select_for_update: sale có thể đang bấm Lưu form đúng lúc tick chạy.
    assessment = OrderAssessment.objects.select_for_update().filter(order=order).first()
    evidence = clean.get("evidence") or {}
    now_iso = timezone.now().isoformat()

    crm_payload: dict = {}
    filled: dict[str, dict] = {}

    for name in AI_SCALAR_FIELDS + AI_ARRAY_FIELDS:
        value = clean.get(name)
        if _is_empty(value) or name not in evidence:
            continue
        if assessment is not None and not _is_empty(getattr(assessment, name)):
            continue
        crm_payload[name] = value
        filled[name] = {"call_record_id": call_record_id, "quote": evidence[name], "at": now_iso}

    need_codes = clean.get("need_type_codes") or []
    if need_codes and "needs" in evidence:
        already_has_needs = assessment is not None and OrderNeed.objects.filter(
            order_assessment=assessment
        ).exists()
        if not already_has_needs:
            crm_payload["needs"] = [{"type_code": code} for code in need_codes]
            filled["needs"] = {
                "call_record_id": call_record_id,
                "quote": evidence["needs"],
                "at": now_iso,
            }

    from apps.orders.assessment_fields import AssessmentValidationError
    from apps.orders.dynamic_assessment import coerce_and_validate_dynamic, get_ai_enabled_defs

    extra_existing = (assessment.extra_fields or {}) if assessment is not None else {}
    for spec in get_ai_enabled_defs():
        value = clean.get(spec.code)
        if _is_empty(value) or spec.code not in evidence:
            continue
        if not _is_empty(extra_existing.get(spec.code)):
            continue
        try:
            coerce_and_validate_dynamic(spec, value)
        except AssessmentValidationError:
            continue
        crm_payload[spec.code] = value
        filled[spec.code] = {"call_record_id": call_record_id, "quote": evidence[spec.code], "at": now_iso}

    open_items = clean.get(OPEN_INSIGHTS_KEY) or []
    if not crm_payload and not open_items:
        return []

    # actor = người sửa cuối hiện tại, KHÔNG phải None: _save_assessment gán
    # updated_by = actor vô điều kiện, truyền None sẽ xoá mất dấu vết sale nào
    # chạm assessment lần cuối. AI không phải người dùng — dấu vết AI nằm ở
    # journey event bên dưới (actor=None) và ở ai_filled_fields.
    actor = assessment.updated_by if assessment is not None else None
    # payload có thể rỗng ({}) khi chỉ có open_insights: helper vẫn get_or_create
    # assessment (cần để append ai_open_insights) mà không đổi field nào khác.
    assessment, _changed = OrderServiceHelpers._upsert_order_assessment(order, crm_payload, actor)

    if filled:
        # Set SAU khi gọi helper: helper vừa xoá key của mọi field changed (luật
        # "sale sửa đè thì hết là AI điền"), giờ mới đánh dấu lại đúng phần AI ghi.
        assessment.ai_filled_fields = {**(assessment.ai_filled_fields or {}), **filled}
        assessment.save(update_fields=["ai_filled_fields", "updated_at"])

    appended = 0
    if open_items:
        existing = list(assessment.ai_open_insights or [])
        seen = {(item.get("topic"), item.get("content")) for item in existing}
        for item in open_items:
            if len(existing) >= OPEN_INSIGHT_MAX_ITEMS:
                break
            key = (item["topic"], item["content"])
            if key in seen:
                continue
            existing.append({**item, "call_record_id": call_record_id, "at": now_iso})
            seen.add(key)
            appended += 1
        if appended:
            assessment.ai_open_insights = existing
            assessment.save(update_fields=["ai_open_insights", "updated_at"])

    if filled or appended:
        OrderServiceHelpers._create_journey_event(
            order=order,
            event_type="assessment_ai_filled",
            actor=None,
            payload_json={
                "call_record_id": call_record_id,
                "fields": {
                    name: {"value": crm_payload[name], "quote": meta["quote"]}
                    for name, meta in filled.items()
                },
                "open_insights_appended": appended,
            },
        )
    return sorted(filled) + (["open_insights"] if appended else [])
