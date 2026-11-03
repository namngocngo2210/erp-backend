"""Hợp đồng dữ liệu giữa Gemini và OrderAssessment.

`orders` giữ quyền sở hữu luật: field nào AI được điền, code nào hợp lệ, chuẩn
hoá ra sao. `callcenter` chỉ dựng câu prompt và gọi API — không import catalog,
không tự quyết field nào được ghi.

Enum trong response_schema sinh THẲNG từ crm_catalog: thêm option mới trong
catalog là prompt tự cập nhật, không có chỗ nào lệch nhau.
"""

from __future__ import annotations

from apps.orders.age_group_backfill import AGE_GROUP_NOT_DISCLOSED
from apps.orders.assessment_fields import TEXT_MAX_LEN
from apps.orders.crm_catalog import (
    AGE_GROUP_LABELS,
    DELAX_SOURCE_LABELS,
    LIFESTYLE_HABIT_LABELS,
    NEED_TYPE_SEED,
    OCCUPATION_LABELS,
    PAIN_DURATION_LABELS,
    PAIN_LEVEL_LABELS,
    RELAX_TIME_LABELS,
)

# "not_disclosed" = ĐÃ HỎI nhưng khách không nói — dữ kiện chỉ sale có. LLM
# không phân biệt được với "cuộc gọi không nhắc tới tuổi", mà điền vào là cột
# hết trống: sale mất chỉ báo cần hỏi lại, và luật "chỉ ghi field trống" khoá
# luôn lần extract sau. Không đủ căn cứ thì để null, đó là việc của cột rỗng.
AI_AGE_GROUP_LABELS: dict[str, str] = {
    code: label for code, label in AGE_GROUP_LABELS.items() if code != AGE_GROUP_NOT_DISCLOSED
}

# occupation_code khai là kind="text" không whitelist trong assessment_fields
# (sale gõ tự do được). Đường AI thì ÉP whitelist — để LLM tự chế chuỗi sẽ làm
# bẩn report mà không ai phát hiện.
_SCALAR_WHITELIST: dict[str, dict[str, str]] = {
    "age_text": AI_AGE_GROUP_LABELS,
    "occupation_code": OCCUPATION_LABELS,
    "pain_level_code": PAIN_LEVEL_LABELS,
    "pain_duration_code": PAIN_DURATION_LABELS,
}

# Tên cột age_text là di sản (nay chứa MÃ nhóm tuổi). Không nói rõ thì model
# trả "45 tuổi" theo đúng nghĩa đen của tên field, rồi bị normalize loại sạch —
# mất trắng thông tin dù transcript có nói.
_FIELD_HINTS: dict[str, str] = {
    "age_text": "nhóm tuổi; khách nói tuổi cụ thể thì quy về nhóm chứa tuổi đó",
}
_ARRAY_WHITELIST: dict[str, dict[str, str]] = {
    "lifestyle_habit_codes": LIFESTYLE_HABIT_LABELS,
    "relax_time_codes": RELAX_TIME_LABELS,
    "delax_source_codes": DELAX_SOURCE_LABELS,
}
NEED_TYPE_LABELS: dict[str, str] = {item["code"]: item["name_vi"] for item in NEED_TYPE_SEED}

AI_SCALAR_FIELDS: tuple[str, ...] = (
    "age_text",
    "occupation_code",
    "pain_level_code",
    "pain_duration_code",
    "used_massage_before",
)
AI_ARRAY_FIELDS: tuple[str, ...] = tuple(_ARRAY_WHITELIST)
# "needs" không phải cột trên assessment (bảng OrderNeed riêng) nhưng vẫn cần 1
# khoá evidence để áp cùng luật "không trích dẫn thì không ghi".
AI_EVIDENCE_KEYS: tuple[str, ...] = AI_SCALAR_FIELDS + AI_ARRAY_FIELDS + ("needs",)

OPEN_INSIGHTS_KEY = "open_insights"
OPEN_INSIGHT_TOPIC_MAX = 100


def _dynamic_ai_defs():
    """Import trong hàm: tránh vòng crm_catalog ← assessment_fields ← dynamic_assessment."""
    from apps.orders.dynamic_assessment import get_ai_enabled_defs

    return get_ai_enabled_defs()


def ai_evidence_keys() -> tuple[str, ...]:
    return AI_EVIDENCE_KEYS + tuple(spec.code for spec in _dynamic_ai_defs())


def build_extract_response_schema() -> dict:
    """Schema OpenAPI-subset cho `response_schema` của google-genai."""
    properties: dict[str, dict] = {
        "used_massage_before": {"type": "boolean", "nullable": True},
    }
    for name, labels in _SCALAR_WHITELIST.items():
        properties[name] = {"type": "string", "enum": sorted(labels), "nullable": True}
    for name, labels in _ARRAY_WHITELIST.items():
        properties[name] = {"type": "array", "items": {"type": "string", "enum": sorted(labels)}}
    properties["need_type_codes"] = {
        "type": "array",
        "items": {"type": "string", "enum": sorted(NEED_TYPE_LABELS)},
    }

    from apps.orders.dynamic_assessment import option_codes

    for spec in _dynamic_ai_defs():
        if spec.kind == "single_choice":
            properties[spec.code] = {"type": "string", "enum": sorted(option_codes(spec)), "nullable": True}
        elif spec.kind == "multi_choice":
            properties[spec.code] = {
                "type": "array",
                "items": {"type": "string", "enum": sorted(option_codes(spec))},
            }
        elif spec.kind == "number":
            properties[spec.code] = {"type": "number", "nullable": True}
        elif spec.kind == "bool":
            properties[spec.code] = {"type": "boolean", "nullable": True}
        else:  # text
            properties[spec.code] = {"type": "string", "nullable": True}

    properties[OPEN_INSIGHTS_KEY] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "content": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["topic", "content", "quote"],
        },
    }

    # Mảng thay vì object khoá động: Gemini không giữ ổn định object có key tự do.
    properties["evidence"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": sorted(ai_evidence_keys())},
                "quote": {"type": "string"},
            },
            "required": ["field", "quote"],
        },
    }
    return {"type": "object", "properties": properties}


def build_prompt_catalog_text() -> str:
    """Liệt kê code kèm nhãn tiếng Việt để LLM map đúng ngữ nghĩa, không đoán mò."""
    blocks: list[str] = []
    for name, labels in {**_SCALAR_WHITELIST, **_ARRAY_WHITELIST}.items():
        options = "; ".join(f"{code} = {label}" for code, label in labels.items())
        hint = _FIELD_HINTS.get(name)
        header = f"{name} ({hint})" if hint else name
        blocks.append(f"- {header}: {options}")
    needs = "; ".join(f"{code} = {label}" for code, label in NEED_TYPE_LABELS.items())
    blocks.append(f"- need_type_codes: {needs}")

    for spec in _dynamic_ai_defs():
        if spec.kind in ("single_choice", "multi_choice"):
            options = "; ".join(f"{item['code']} = {item['label']}" for item in spec.options)
            header = f"{spec.code} ({spec.ai_hint})" if spec.ai_hint else spec.code
            blocks.append(f"- {header}: {options}")
        else:
            hint = spec.ai_hint or spec.label
            blocks.append(f"- {spec.code}: text tự do — {hint}")
    blocks.append(
        f"- {OPEN_INSIGHTS_KEY}: MỌI thông tin đáng chú ý khác về khách/thương vụ chưa thuộc "
        "field nào ở trên; mỗi item gồm topic (chủ đề ngắn), content (nội dung), "
        "quote (trích dẫn NGUYÊN VĂN câu của khách — thiếu quote thì item bị loại)."
    )
    return "\n".join(blocks)


def _clean_evidence(raw_evidence, allowed_keys) -> dict[str, str]:
    if not isinstance(raw_evidence, list):
        return {}
    result: dict[str, str] = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        quote = item.get("quote")
        if field in allowed_keys and isinstance(quote, str) and quote.strip():
            result.setdefault(field, quote.strip())
    return result


def _clean_open_insights(raw_items) -> list[dict]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic")
        content = item.get("content")
        quote = item.get("quote")
        if not all(isinstance(v, str) and v.strip() for v in (topic, content, quote)):
            continue
        items.append(
            {
                "topic": topic.strip()[:OPEN_INSIGHT_TOPIC_MAX],
                "content": content.strip()[:TEXT_MAX_LEN],
                "quote": quote.strip()[:TEXT_MAX_LEN],
            }
        )
    return items


def normalize_extracted(raw: dict) -> tuple[dict, dict]:
    """Lọc output LLM về đúng hợp đồng. Trả ``(clean, rejected)``.

    KHÔNG tin enum của LLM: schema chỉ là gợi ý, model vẫn trả code lạ được.
    Phần bị loại giữ lại trong ``rejected`` để soi chất lượng prompt về sau.
    """
    clean: dict = {}
    rejected: dict = {}

    used = raw.get("used_massage_before")
    if isinstance(used, bool):
        clean["used_massage_before"] = used
    elif used is not None:
        rejected["used_massage_before"] = used

    for name, labels in _SCALAR_WHITELIST.items():
        value = raw.get(name)
        if value is None:
            continue
        if isinstance(value, str) and value in labels:
            clean[name] = value
        else:
            rejected[name] = value

    for name, labels in {**_ARRAY_WHITELIST, "need_type_codes": NEED_TYPE_LABELS}.items():
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, list):
            rejected[name] = value
            continue
        kept: list[str] = []
        dropped: list = []
        for code in value:
            if isinstance(code, str) and code in labels and code not in kept:
                kept.append(code)
            elif code not in kept:
                dropped.append(code)
        if kept:
            clean[name] = kept
        if dropped:
            rejected[name] = dropped

    from apps.orders.dynamic_assessment import option_codes

    for spec in _dynamic_ai_defs():
        value = raw.get(spec.code)
        if value is None:
            continue
        if spec.kind == "single_choice":
            if isinstance(value, str) and value in option_codes(spec):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        elif spec.kind == "multi_choice":
            if not isinstance(value, list):
                rejected[spec.code] = value
                continue
            allowed = option_codes(spec)
            kept = [c for i, c in enumerate(value) if isinstance(c, str) and c in allowed and c not in value[:i]]
            dropped = [c for c in value if not (isinstance(c, str) and c in allowed)]
            if kept:
                clean[spec.code] = kept
            if dropped:
                rejected[spec.code] = dropped
        elif spec.kind == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        elif spec.kind == "bool":
            if isinstance(value, bool):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        else:  # text
            if isinstance(value, str) and value.strip():
                clean[spec.code] = value.strip()[:TEXT_MAX_LEN]
            else:
                rejected[spec.code] = value

    clean[OPEN_INSIGHTS_KEY] = _clean_open_insights(raw.get(OPEN_INSIGHTS_KEY))
    clean["evidence"] = _clean_evidence(raw.get("evidence"), ai_evidence_keys())
    return clean, rejected
