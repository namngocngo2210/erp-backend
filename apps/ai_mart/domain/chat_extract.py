"""Hợp đồng dữ liệu giữa Gemini và mart hội thoại.

KHÔNG TIN enum của LLM: response_schema chỉ là gợi ý, model vẫn trả mã lạ.
Mọi giá trị đi qua normalize_chat_extracted; phần bị loại giữ ở `rejected` để
biết từ điển đang thiếu khái niệm nào — đó là tín hiệu nghiệp vụ, không phải
rác (spec §6, §9).

Bộ mã lấy TỪ BẢNG need_types và từ crm_catalog, không chép tay: nhãn chat phải
so được với nhãn sale nhập trên phiếu, chép tay là hai kho số lệch nhau (C4).
"""

from __future__ import annotations

from apps.ai_mart.domain.chat_dictionary import GIAI_DOAN_CHAT, MUC_DO_QUAN_TAM
from apps.common.phone_utils import normalize_phone
from apps.orders.crm_catalog import NOT_CLOSED_REASON_OPTIONS

TEXT_MAX_LEN = 200
MAX_PRODUCTS = 10
MAX_EVIDENCE = 20

# Một nguồn duy nhất cho field nào được phép có evidence — schema và normaliser
# đọc CHUNG hằng số này, không khai hai chỗ để không lệch nhau.
EVIDENCE_FIELDS = (
    "nhu_cau", "ly_do_khong_mua", "giai_doan", "muc_do_quan_tam", "san_pham_quan_tam",
)

GIAI_DOAN_LABELS = dict(GIAI_DOAN_CHAT)
MUC_DO_LABELS = dict(MUC_DO_QUAN_TAM)


def load_need_labels() -> dict[str, str]:
    """Mã nhu cầu CÒN HIỆU LỰC. Mã đã tắt vẫn có nhãn trong từ điển (để đọc dữ
    liệu cũ) nhưng KHÔNG được đưa vào prompt — không mời model gán nhãn chết."""
    from apps.orders.models import NeedType

    return dict(
        NeedType.objects.filter(is_active=True)
        .order_by("sort_order", "code")
        .values_list("code", "name_vi")
    )


def load_reason_labels() -> dict[str, str]:
    return {o.code: o.label for o in NOT_CLOSED_REASON_OPTIONS}


_PROMPT_HEADER = (
    "Đây là một hội thoại chat giữa page bán hàng và khách hàng, bằng tiếng Việt, "
    "về sản phẩm ghế/máy massage. Mỗi dòng có tiền tố vai trò: 'Khách:' là lời "
    "khách, 'Page:' là lời nhân viên hoặc chatbot.\n\n"
    "Hãy trích xuất NHU CẦU của khách và LÝ DO khách chưa/không mua.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- CHỈ căn cứ vào dòng bắt đầu bằng 'Khách:'. Lời của Page là câu hỏi gợi ý, "
    "KHÔNG phải nhu cầu của khách.\n"
    "- CHỈ điền khi khách nói rõ. TUYỆT ĐỐI không suy đoán từ ngữ cảnh.\n"
    "- Không chắc chắn thì để null (với mảng thì để mảng rỗng).\n"
    "- Mỗi field có điền giá trị PHẢI kèm một mục trong `evidence` với trích dẫn "
    "NGUYÊN VĂN câu nói của khách làm căn cứ.\n"
    "- Chỉ dùng đúng các mã dưới đây, không tự chế mã mới.\n"
    "- sdt_khach: chỉ điền khi khách tự gõ số điện thoại trong chat.\n\n"
    "DANH MỤC MÃ HỢP LỆ (mã = ý nghĩa):\n"
)


def _catalog_block(name: str, labels: dict[str, str], note: str = "") -> str:
    options = "; ".join(f"{code} = {label}" for code, label in labels.items())
    header = f"{name} ({note})" if note else name
    return f"- {header}: {options}"


def build_prompt_catalog_text(
    need_labels: dict[str, str] | None = None,
    reason_labels: dict[str, str] | None = None,
) -> str:
    # need_labels/reason_labels: truyền sẵn để gọi hàng loạt (Task 7) không phải
    # mỗi hội thoại lại truy vấn lại need_types/crm_catalog — mặc định None thì
    # tự nạp, giữ nguyên hành vi gọi đơn lẻ của các test cũ.
    need_labels = load_need_labels() if need_labels is None else need_labels
    reason_labels = load_reason_labels() if reason_labels is None else reason_labels
    return "\n".join([
        _catalog_block("nhu_cau", need_labels, "nhu cầu khách nói ra, chọn nhiều được"),
        _catalog_block("ly_do_khong_mua", reason_labels,
                       "chỉ điền khi khách NÊU lý do chưa mua"),
        _catalog_block("giai_doan", GIAI_DOAN_LABELS, "chọn đúng MỘT"),
        _catalog_block("muc_do_quan_tam", MUC_DO_LABELS, "chọn đúng MỘT"),
        "- tom_tat: 1-3 câu tiếng Việt tóm tắt hội thoại theo góc nhìn khách cần gì.",
        "- san_pham_quan_tam: tên sản phẩm khách nhắc tới, nguyên văn, tối đa 10 mục.",
        "- sdt_khach: số điện thoại khách tự gõ trong chat, dạng số, không kèm chữ.",
    ])


def build_chat_prompt(
    noi_dung: str,
    need_labels: dict[str, str] | None = None,
    reason_labels: dict[str, str] | None = None,
) -> str:
    catalog = build_prompt_catalog_text(need_labels, reason_labels)
    return f"{_PROMPT_HEADER}{catalog}\n\nHỘI THOẠI:\n{noi_dung}"


def build_chat_response_schema(
    need_labels: dict[str, str] | None = None,
    reason_labels: dict[str, str] | None = None,
) -> dict:
    need_labels = load_need_labels() if need_labels is None else need_labels
    reason_labels = load_reason_labels() if reason_labels is None else reason_labels
    return {
        "type": "object",
        "properties": {
            "tom_tat": {"type": "string", "nullable": True},
            "nhu_cau": {"type": "array",
                        "items": {"type": "string", "enum": sorted(need_labels)}},
            "ly_do_khong_mua": {"type": "array",
                                "items": {"type": "string", "enum": sorted(reason_labels)}},
            "san_pham_quan_tam": {"type": "array", "items": {"type": "string"}},
            "giai_doan": {"type": "string", "enum": sorted(GIAI_DOAN_LABELS), "nullable": True},
            "muc_do_quan_tam": {"type": "string", "enum": sorted(MUC_DO_LABELS),
                                "nullable": True},
            "sdt_khach": {"type": "string", "nullable": True},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": list(EVIDENCE_FIELDS)},
                        "quote": {"type": "string"},
                    },
                    "required": ["field", "quote"],
                },
            },
        },
    }


def _clean_code_array(value, labels, clean, rejected, key) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        rejected[key] = value
        return
    kept: list[str] = []
    dropped: list = []
    for code in value:
        if isinstance(code, str) and code in labels and code not in kept:
            kept.append(code)
        elif code not in kept and code not in dropped:
            dropped.append(code)
    if kept:
        clean[key] = kept
    if dropped:
        rejected[key] = dropped


def _clean_scalar(value, labels, clean, rejected, key) -> None:
    if value is None:
        return
    if isinstance(value, str) and value in labels:
        clean[key] = value
    else:
        rejected[key] = value


def normalize_chat_extracted(
    raw: dict,
    need_labels: dict[str, str] | None = None,
    reason_labels: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    """Lọc output LLM về đúng hợp đồng khoá của ai.hoi_thoai. Trả ``(clean, rejected)``."""
    need_labels = load_need_labels() if need_labels is None else need_labels
    reason_labels = load_reason_labels() if reason_labels is None else reason_labels
    clean: dict = {}
    rejected: dict = {}

    tom_tat = raw.get("tom_tat")
    if isinstance(tom_tat, str) and tom_tat.strip():
        clean["tom_tat"] = tom_tat.strip()[:2000]
    elif tom_tat is not None:
        rejected["tom_tat"] = tom_tat

    _clean_code_array(raw.get("nhu_cau"), need_labels, clean, rejected, "nhu_cau")
    _clean_code_array(raw.get("ly_do_khong_mua"), reason_labels, clean, rejected,
                      "ly_do_khong_mua")
    _clean_scalar(raw.get("giai_doan"), GIAI_DOAN_LABELS, clean, rejected, "giai_doan")
    _clean_scalar(raw.get("muc_do_quan_tam"), MUC_DO_LABELS, clean, rejected, "muc_do_quan_tam")

    products = raw.get("san_pham_quan_tam")
    if isinstance(products, list):
        kept: list[str] = []
        for item in products:
            if not isinstance(item, str) or not item.strip():
                continue
            # Khử trùng lặp trên chuỗi ĐÃ TRIM: giá trị này đi thẳng vào cột
            # TEXT[] của mart (jsonb_array_elements_text trong chat_hoi_thoai.py)
            # — hai chuỗi chỉ khác khoảng trắng đầu/cuối vẫn là CÙNG một sản phẩm
            # trên mart, để trùng lặp lọt qua là rác hiển thị cho người đọc báo cáo.
            text = item.strip()[:TEXT_MAX_LEN]
            if text not in kept:
                kept.append(text)
        if kept:
            clean["san_pham_quan_tam"] = kept[:MAX_PRODUCTS]
    elif products is not None:
        rejected["san_pham_quan_tam"] = products

    # Chuẩn hoá NGAY tại đây: cột don_id join thẳng customers.phone_normalized,
    # để nguyên "+84 912..." là join trượt 100% mà không ai thấy lỗi.
    phone = raw.get("sdt_khach")
    if isinstance(phone, str) and phone.strip():
        normalized = normalize_phone(phone)
        if normalized:
            clean["sdt_khach"] = normalized
        else:
            rejected["sdt_khach"] = phone
    elif phone is not None:
        rejected["sdt_khach"] = phone

    evidence = raw.get("evidence")
    if isinstance(evidence, list):
        clean["evidence"] = [
            {"field": item["field"], "quote": item["quote"].strip()[:TEXT_MAX_LEN]}
            for item in evidence
            if isinstance(item, dict)
            and item.get("field") in EVIDENCE_FIELDS
            and isinstance(item.get("quote"), str)
            and item["quote"].strip()
        ][:MAX_EVIDENCE]
    return clean, rejected
