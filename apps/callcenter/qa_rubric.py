"""Hợp đồng dữ liệu giữa Gemini và điểm chất lượng cuộc gọi.

Rubric mã hoá "Khung kịch bản mẫu" của team sale (4 bước × tiêu chí đánh giá).
File này giữ TOÀN BỘ luật: tiêu chí nào, thang điểm ra sao, cờ đỏ nào hợp lệ,
chuẩn hoá output của model thế nào. ``scoring.py`` chỉ biết cách nói chuyện với
Gemini — cùng cách ``apps/orders/ai_extract_contract.py`` tách khỏi
``callcenter/extract.py``.

Prompt và ``response_schema`` đều SINH RA từ các hằng ở đây: sửa tiêu chí là cả
hai tự cập nhật, không có chỗ nào lệch nhau.

Xem docs/superpowers/specs/2026-07-30-call-quality-scoring-design.md
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Bump khi sửa tiêu chí/trọng số/thang điểm. Điểm chỉ so sánh được trong cùng
# version — báo cáo luôn lọc theo version, không trộn.
RUBRIC_VERSION = "v1"

# Điểm mỗi tiêu chí. "na" = cuộc gọi không đi tới bước đó (khách cúp máy ở phút
# 1 thì khối Chốt đơn là na) — bị loại khỏi mẫu số thay vì cho 0, nếu không thì
# cuộc gọi xui bị chấm như sale kém.
SCORE_MIN = 0
SCORE_MAX = 2
SCORE_NA = "na"


@dataclass(frozen=True)
class Criterion:
    code: str
    label: str
    guide: str
    # True = tiêu chí phụ thuộc âm thanh (tông giọng, âm lượng), transcript
    # không mang thông tin đó. Model được yêu cầu trả "na"; nếu nó cố chấm thì
    # normalize ép về "na" — xem docs §4.4.
    audio_only: bool = False


@dataclass(frozen=True)
class Block:
    code: str
    label: str
    weight: int
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)


BLOCKS: tuple[Block, ...] = (
    Block(
        code="A",
        label="Mở đầu & xin phép",
        weight=15,
        criteria=(
            Criterion(
                "a1_greeting",
                "Chào hỏi, xưng danh, xin phép",
                "Có chào hỏi đúng chuẩn, xưng danh công ty DELAX và xin phép tư vấn không?",
            ),
            Criterion(
                "a2_purpose",
                "Nói rõ lý do gọi",
                "Có nói rõ vì sao gọi (khách để lại thông tin tìm hiểu sản phẩm) không?",
            ),
            Criterion(
                "a3_attitude",
                "Thái độ tích cực, tôn trọng",
                "Cách xưng hô, dùng từ có tôn trọng và tích cực không? Chỉ đánh giá "
                "phần thể hiện qua LỜI NÓI, không suy đoán giọng điệu.",
            ),
        ),
    ),
    Block(
        code="B",
        label="Khai thác nhu cầu",
        weight=30,
        criteria=(
            Criterion(
                "b1_discovery",
                "Hỏi đủ 4 câu khai thác",
                "Có hỏi đủ: mua cho ai / đau vùng nào / đau lâu chưa / độ tuổi? "
                "Hỏi được càng nhiều càng cao điểm; hỏi 0-1 câu là 0 điểm.",
            ),
            Criterion(
                "b2_listening",
                "Lắng nghe chủ động",
                "Có ngắt lời khách không? Có tóm tắt/xác nhận lại ý khách trước khi "
                "tư vấn tiếp không?",
            ),
            Criterion(
                "b3_empathy",
                "Thấu cảm",
                "Có thể hiện đồng cảm khi khách than phiền về tình trạng hiện tại không?",
            ),
            Criterion(
                "b4_contraindication",
                "Nhận diện chống chỉ định",
                "Khách có nhắc tới bệnh thuộc nhóm KHÔNG dùng được (tim có thiết bị y "
                "tế cấy, loãng xương, ung thư, tiểu đường giai đoạn nặng, mới phẫu "
                "thuật)? Nếu có thì sale có nhận ra và xử lý đúng không? Khách không "
                "nhắc gì tới bệnh nền thì để na.",
            ),
        ),
    ),
    Block(
        code="C",
        label="Tư vấn & xử lý từ chối",
        weight=35,
        criteria=(
            Criterion(
                "c1_structure",
                "Đúng chuỗi tư vấn",
                "Có đi đúng chuỗi: khách nói đau ở đâu → giải thích nguyên nhân bằng "
                "ngôn ngữ đời thường → liên hệ cơ chế sản phẩm → nói lợi ích khách "
                "nhận được?",
            ),
            Criterion(
                "c2_value_prop",
                "Nói lợi ích, không kể tính năng",
                "Có tập trung vào LỢI ÍCH khách nhận được, hay chỉ thao thao bất tuyệt "
                "liệt kê tính năng sản phẩm?",
            ),
            Criterion(
                "c3_knowledge",
                "Nắm chính sách, giá, bảo hành",
                "Tư vấn về giá, ưu đãi, chương trình khuyến mãi và chế độ bảo hành có "
                "rõ ràng, tự tin, nhất quán không?",
            ),
            Criterion(
                "c4_objection",
                "Xử lý từ chối",
                "Khi khách chê đắt / so sánh đối thủ / nói 'để suy nghĩ thêm', sale có "
                "bình tĩnh xử lý hay bị cuốn theo và bỏ cuộc sớm? Khách không từ chối "
                "gì thì để na.",
            ),
            Criterion(
                "c5_real_reason",
                "Làm rõ lý do từ chối thật",
                "Sale có đào sâu để tìm lý do từ chối THẬT SỰ đằng sau câu từ chối bề "
                "mặt không? Khách không từ chối gì thì để na.",
            ),
        ),
    ),
    Block(
        code="D",
        label="Chốt đơn",
        weight=20,
        criteria=(
            Criterion(
                "d1_proactive_close",
                "Chủ động đề nghị chốt",
                "Sale có chủ động đưa ra lời đề nghị chốt đơn không, hay chỉ tư vấn rồi "
                "để khách tự quyết?",
            ),
            Criterion(
                "d2_info_complete",
                "Xác nhận đủ thông tin",
                "Khi chốt có xác nhận đủ: tên, SĐT, địa chỉ, giá tiền + quà tặng, thời "
                "gian giao hàng, khung giờ khách tiện nhận? Cuộc gọi không đi tới bước "
                "chốt thì để na.",
            ),
            Criterion(
                "d3_reassure",
                "Trấn an khi nhận hàng",
                "Có nói khách được bóc sản phẩm kiểm tra rồi mới thanh toán, và giao "
                "trong giờ hành chính? Không đi tới bước chốt thì để na.",
            ),
        ),
    ),
)

CRITERIA: tuple[Criterion, ...] = tuple(c for b in BLOCKS for c in b.criteria)
CRITERION_BY_CODE: dict[str, Criterion] = {c.code: c for c in CRITERIA}
BLOCK_BY_CODE: dict[str, Block] = {b.code: b for b in BLOCKS}
BLOCK_OF_CRITERION: dict[str, str] = {c.code: b.code for b in BLOCKS for c in b.criteria}

# Cờ đỏ KHÔNG trừ điểm — đây là chuyện tuân thủ, không phải kỹ năng. Báo riêng
# để quản lý xử lý ngay, và để một cuộc gọi vi phạm không bị pha loãng trong
# điểm trung bình.
RED_FLAGS: dict[str, str] = {
    "rf_medical_claim": "Cam kết chữa khỏi bệnh / nói quá công dụng y tế",
    "rf_ignored_contraindication": "Khách khai bệnh chống chỉ định mà vẫn chốt, không cảnh báo",
    "rf_false_policy": "Hứa sai giá / bảo hành / thời gian giao hàng",
    "rf_disrespect": "Thái độ tiêu cực, cãi hoặc gắt với khách",
}

# Việc CẦN LÀM khi một tiêu chí yếu. Viết sẵn ở đây thay vì hỏi AI mỗi lần: cùng
# một điểm yếu phải ra cùng một khuyến nghị, và nội dung đào tạo là thứ team sale
# phải duyệt được chứ không để model tự chế mỗi lần một kiểu.
CRITERION_ACTIONS: dict[str, str] = {
    "a1_greeting": "Chuẩn hoá câu mở đầu: chào — xưng tên + DELAX — xin phép tư vấn, đọc thuộc trong 15 giây.",
    "a2_purpose": "Nói ngay lý do gọi ở câu thứ hai: khách vừa để lại thông tin tìm hiểu sản phẩm nào.",
    "a3_attitude": "Rà lại cách xưng hô theo độ tuổi khách; bỏ từ cộc lốc, thêm 'dạ/ạ' ở đầu và cuối lượt nói.",
    "b1_discovery": "Bắt buộc hỏi đủ 4 câu trước khi tư vấn: mua cho ai / đau vùng nào / đau lâu chưa / bao nhiêu tuổi.",
    "b2_listening": "Tập tóm tắt lại ý khách một câu trước khi tư vấn tiếp; không cắt lời khi khách đang kể.",
    "b3_empathy": "Thêm một câu ghi nhận cảm xúc khi khách than phiền, trước khi chuyển sang giải pháp.",
    "b4_contraindication": "Hỏi bệnh nền thành thói quen; thuộc danh sách chống chỉ định và biết cách từ chối khéo.",
    "c1_structure": "Đi đúng chuỗi: khách đau ở đâu → nguyên nhân đời thường → cơ chế sản phẩm → lợi ích.",
    "c2_value_prop": "Mỗi tính năng nói ra phải kèm 'nên anh/chị sẽ...' — không liệt kê thông số trần trụi.",
    "c3_knowledge": "Ôn lại bảng giá, CTKM và chế độ bảo hành; không đoán khi khách hỏi chính sách.",
    "c4_objection": "Luyện kịch bản cho 3 câu từ chối phổ biến: đắt, so sánh đối thủ, để suy nghĩ thêm.",
    "c5_real_reason": "Khi khách từ chối, hỏi thêm một câu đào sâu trước khi kết thúc — đừng dừng ở lý do bề mặt.",
    "d1_proactive_close": "Chủ động đề nghị chốt sau khi tư vấn xong, đừng chờ khách tự quyết.",
    "d2_info_complete": "Đọc lại checklist khi chốt: tên, SĐT, địa chỉ, giá + quà, thời gian giao, khung giờ nhận.",
    "d3_reassure": "Luôn nói khách được bóc kiểm tra rồi mới thanh toán và giao trong giờ hành chính.",
}

# Ngưỡng phân loại trên thang 0-2 (đo trên điểm TRUNG BÌNH của tiêu chí):
# >= 1.5 là đang làm tốt, < 1.0 là cần cải thiện. Khoảng giữa cố ý không xếp vào
# đâu — nêu ra thì loãng, mà nội dung đào tạo mỗi lần chỉ nên có vài mục.
STRENGTH_THRESHOLD = 1.5
WEAKNESS_THRESHOLD = 1.0

_PROMPT_HEADER = (
    "Đây là transcript một cuộc gọi giữa nhân viên tư vấn (Nhân viên) và khách "
    "hàng (Khách), bằng tiếng Việt, về sản phẩm ghế/máy massage của DELAX. Hãy "
    "chấm chất lượng tư vấn của NHÂN VIÊN theo khung bên dưới.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- Thang điểm mỗi tiêu chí: 0 = không làm, 1 = có làm nhưng thiếu/sai, "
    "2 = làm tốt.\n"
    "- Cuộc gọi KHÔNG đi tới bước đó thì trả \"na\", TUYỆT ĐỐI không cho 0.\n"
    "- Mỗi tiêu chí chấm từ 1 điểm trở lên PHẢI kèm `evidence` là câu nói NGUYÊN "
    "VĂN của nhân viên trong transcript làm căn cứ. Không trích dẫn được thì cho "
    "0 điểm. Tuyệt đối không bịa hoặc diễn giải lại lời thoại.\n"
    "- Chỉ dựa vào NỘI DUNG LỜI NÓI. Không suy đoán giọng điệu, âm lượng, tốc độ "
    "nói — transcript không chứa thông tin đó.\n"
    "- `summary`: 2-3 câu nhận xét ngắn. `coaching`: đúng MỘT điểm cần cải thiện "
    "nhất, nói cụ thể việc phải làm khác đi.\n"
    "- `not_closed_reason`: nếu khách KHÔNG chốt, nêu lý do thật sự nghe được từ "
    "transcript; không rõ thì để chuỗi rỗng.\n"
    "- `contraindications`: liệt kê bệnh nền khách tự khai thuộc nhóm chống chỉ "
    "định; khách không nhắc gì thì để mảng rỗng.\n\n"
)


def build_prompt_rubric_text() -> str:
    """Phần mô tả rubric nhét vào prompt — sinh từ BLOCKS, không viết tay."""
    lines: list[str] = []
    for block in BLOCKS:
        lines.append(f"\nKHỐI {block.code} — {block.label}:")
        for criterion in block.criteria:
            note = " [để na: transcript không đánh giá được]" if criterion.audio_only else ""
            lines.append(f"- {criterion.code} ({criterion.label}): {criterion.guide}{note}")
    lines.append("\nCỜ ĐỎ (chỉ nêu khi có bằng chứng rõ, kèm trích dẫn):")
    for code, label in RED_FLAGS.items():
        lines.append(f"- {code}: {label}")
    return "\n".join(lines)


def build_prompt(transcript: str) -> str:
    return f"{_PROMPT_HEADER}{build_prompt_rubric_text()}\n\nTRANSCRIPT:\n{transcript}"


def build_score_response_schema() -> dict:
    """JSON Schema cho structured output của Gemini.

    ``score`` khai là string vì phải chứa được cả "0"/"1"/"2" lẫn "na" — enum
    chuỗi cho model tín hiệu rõ hơn là số + cờ boolean riêng.
    """
    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "properties": {
                    criterion.code: {
                        "type": "object",
                        "properties": {
                            "score": {"type": "string", "enum": ["0", "1", "2", SCORE_NA]},
                            "evidence": {"type": "string"},
                        },
                        "required": ["score"],
                    }
                    for criterion in CRITERIA
                },
                "required": [c.code for c in CRITERIA],
            },
            "red_flags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": list(RED_FLAGS)},
                        "evidence": {"type": "string"},
                    },
                    "required": ["code", "evidence"],
                },
            },
            "contraindications": {"type": "array", "items": {"type": "string"}},
            "not_closed_reason": {"type": "string"},
            "summary": {"type": "string"},
            "coaching": {"type": "string"},
        },
        "required": ["criteria", "summary"],
    }


# ── chuẩn hoá output ─────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def _canon(text: str) -> str:
    """Chuẩn hoá để so khớp trích dẫn: bỏ dấu câu, gộp khoảng trắng, hạ chữ thường.

    Không bỏ dấu tiếng Việt — model trích sai dấu là trích sai, không phải khác
    định dạng. NFC để "ì" gõ 2 kiểu unicode vẫn khớp nhau.
    """
    text = unicodedata.normalize("NFC", text or "").lower()
    return _SPACES.sub(" ", _PUNCT.sub(" ", text)).strip()


# Trích dẫn ngắn hơn ngưỡng này thì không kiểm chứng được (model trả "dạ vâng"
# là khớp mọi transcript) — coi như không có bằng chứng.
MIN_EVIDENCE_CHARS = 12


def normalize_scored(raw: dict, transcript: str) -> tuple[dict, dict]:
    """Trả ``(clean, rejected)``.

    ``clean`` = phần tin được: điểm hợp lệ + evidence có THẬT trong transcript.
    ``rejected`` = phần bị loại, giữ nguyên để soi chất lượng prompt về sau.

    Luật loại (docs §3.4): điểm ≥ 1 mà không có trích dẫn khớp transcript thì
    hạ về 0. LLM sẽ chấm hào phóng nếu không bị chặn — đây là chốt chặn duy nhất
    ngăn "điểm ảo" vào báo cáo.
    """
    haystack = _canon(transcript)
    clean_criteria: dict[str, dict] = {}
    rejected: dict[str, str] = {}

    raw_criteria = raw.get("criteria") if isinstance(raw.get("criteria"), dict) else {}
    for criterion in CRITERIA:
        item = raw_criteria.get(criterion.code)
        if not isinstance(item, dict):
            rejected[criterion.code] = "thiếu trong output"
            clean_criteria[criterion.code] = {"score": None, "na": True, "evidence": ""}
            continue

        score_raw = str(item.get("score", "")).strip().lower()
        evidence = str(item.get("evidence") or "").strip()

        if criterion.audio_only or score_raw == SCORE_NA:
            clean_criteria[criterion.code] = {"score": None, "na": True, "evidence": ""}
            continue

        if score_raw not in {"0", "1", "2"}:
            rejected[criterion.code] = f"điểm không hợp lệ: {score_raw!r}"
            clean_criteria[criterion.code] = {"score": None, "na": True, "evidence": ""}
            continue

        score = int(score_raw)
        if score >= 1:
            canon_evidence = _canon(evidence)
            if len(canon_evidence) < MIN_EVIDENCE_CHARS:
                rejected[criterion.code] = "trích dẫn quá ngắn để kiểm chứng"
                score, evidence = 0, ""
            elif canon_evidence not in haystack:
                rejected[criterion.code] = "trích dẫn không có trong transcript"
                score, evidence = 0, ""

        clean_criteria[criterion.code] = {
            "score": score,
            "na": False,
            "evidence": evidence if score >= 1 else "",
        }

    clean_flags: list[dict] = []
    for item in raw.get("red_flags") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if code not in RED_FLAGS:
            continue
        canon_evidence = _canon(evidence)
        # Cờ đỏ gọi người ra xử lý — bịa một cờ đỏ tốn nhiều lòng tin hơn bỏ sót.
        if len(canon_evidence) < MIN_EVIDENCE_CHARS or canon_evidence not in haystack:
            rejected[f"red_flag:{code}"] = "trích dẫn không có trong transcript"
            continue
        clean_flags.append({"code": code, "evidence": evidence})

    clean = {
        "criteria": clean_criteria,
        "red_flags": clean_flags,
        "contraindications": [
            str(x).strip() for x in (raw.get("contraindications") or []) if str(x).strip()
        ],
        "not_closed_reason": str(raw.get("not_closed_reason") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "coaching": str(raw.get("coaching") or "").strip(),
    }
    return clean, rejected


def compute_scores(clean_criteria: dict) -> tuple[float, dict]:
    """Trả ``(total_score, block_scores)`` trên thang 100.

    Tiêu chí ``na`` bị loại khỏi mẫu số của khối. Khối na TOÀN BỘ thì trọng số
    của nó chia lại cho các khối còn lại — nếu không, cuộc gọi ngắn tự động mất
    20 điểm khối Chốt đơn dù sale không làm gì sai.
    """
    ratios: dict[str, float] = {}
    active_weight = 0
    for block in BLOCKS:
        earned = 0
        possible = 0
        for criterion in block.criteria:
            item = clean_criteria.get(criterion.code) or {}
            if item.get("na") or item.get("score") is None:
                continue
            earned += int(item["score"])
            possible += SCORE_MAX
        if possible == 0:
            continue
        ratios[block.code] = earned / possible
        active_weight += block.weight

    if active_weight == 0:
        return 0.0, {}

    block_scores: dict[str, float] = {}
    total = 0.0
    for code, ratio in ratios.items():
        # Chia lại theo tỉ trọng các khối còn sống, giữ tổng luôn trên thang 100.
        weight = BLOCK_BY_CODE[code].weight / active_weight * 100
        value = round(ratio * weight, 2)
        block_scores[code] = value
        total += value
    return round(total, 2), block_scores
