"""Dựng nội dung file Excel trang Chất lượng cuộc gọi.

Tách khỏi ``exports_definitions``: ở đây chỉ có "một bản ghi điểm → những dòng
nào", không biết gì về job/quyền/tiến độ, nên test được bằng dữ liệu thuần.

File có 3 sheet vì ba người đọc khác nhau:
1. **Cuộc gọi** — quản lý lọc, sắp, dò một cuộc cụ thể.
2. **Điểm tiêu chí** — team lead lấy trích dẫn thật mang đi dạy.
3. **Tổng hợp** — bản chụp của tab Tổng quan để dán vào báo cáo tuần.
"""

from __future__ import annotations

from apps.orders.crm_catalog import SALES_STATUS_LABELS

from .qa_rubric import BLOCK_OF_CRITERION, BLOCKS, CRITERION_BY_CODE, RED_FLAGS, SCORE_MAX

# Trần dòng mỗi lần xuất, đếm theo SỐ CUỘC (sheet 1). Sheet 2 bung theo tiêu chí
# nên gấp ~15 lần con số này — 20.000 cuộc ≈ 300.000 dòng vẫn nằm trong sức của
# writer streaming, nhưng file mở bằng Excel đã bắt đầu nặng.
QUALITY_EXPORT_MAX_ROWS = 20_000

_BLOCK_HEADERS = [f"Điểm {block.label}" for block in BLOCKS]

CALL_SHEET_HEADERS = [
    "Ngày gọi",
    "Sale phụ trách đơn",
    "Khách hàng",
    "Số điện thoại",
    "SKU",
    "Mã đơn",
    "Trạng thái sale",
    "Thời lượng (giây)",
    "Điểm tổng",
    "Đạt",
    *_BLOCK_HEADERS,
    "Cờ đỏ",
    "Từ/phút",
    "Tỷ lệ sale nói",
    "Nhận xét",
]

CRITERION_SHEET_HEADERS = [
    "Mã cuộc",
    "Ngày gọi",
    "Sale phụ trách đơn",
    "Mã đơn",
    "Khối",
    "Mã tiêu chí",
    "Tiêu chí",
    "Điểm",
    "Thang điểm",
    "Không áp dụng",
    "Trích dẫn",
]

_BLOCK_LABELS = {block.code: block.label for block in BLOCKS}


def _sale_name(score) -> str:
    """Giống hệt cột trên màn hình (xem quality_views_impl._sale_name).

    Zenify không trả định danh người cầm máy nên đây là sale PHỤ TRÁCH ĐƠN —
    tiêu đề cột phải nói rõ, đừng để ai đọc thành "ai gọi dở".
    """
    if score.agent_name:
        return score.agent_name
    order = getattr(score.call_record, "order", None)
    staff = getattr(order, "staff", None) if order else None
    if staff is None:
        return ""
    return staff.display_name or staff.username


def _sales_status_label(score) -> str:
    """Trạng thái HIỆN TẠI của đơn — cùng nguồn với bộ lọc, để file khớp màn hình."""
    order = getattr(score.call_record, "order", None)
    assessment = getattr(order, "assessment", None) if order else None
    code = (assessment.sales_status_code or "") if assessment else ""
    return SALES_STATUS_LABELS.get(code, code)


def _skus(score) -> str:
    order = getattr(score.call_record, "order", None)
    if order is None:
        return ""
    seen: list[str] = []
    for item in order.items.all():
        if item.sku and item.sku not in seen:
            seen.append(item.sku)
    return ", ".join(seen)


def call_row_values(score, *, pass_score: int) -> list:
    """Một dòng sheet "Cuộc gọi"."""
    order = getattr(score.call_record, "order", None)
    block_scores = score.block_scores or {}
    return [
        score.call_date.isoformat(),
        _sale_name(score),
        order.name if order else "",
        score.call_record.caller or (order.phone if order else ""),
        _skus(score),
        score.order_id_snapshot or "",
        _sales_status_label(score),
        score.duration_seconds,
        float(score.total_score),
        "Đạt" if float(score.total_score) >= pass_score else "Chưa đạt",
        *[float(block_scores.get(block.code) or 0) for block in BLOCKS],
        # Ghi NHÃN cờ đỏ chứ không phải mã: file này đi tới quản lý, không tới
        # lập trình viên.
        ", ".join(
            RED_FLAGS.get(item.get("code"), item.get("code") or "")
            for item in score.red_flags or []
        ),
        score.wpm,
        float(score.talk_ratio),
        score.summary,
    ]


def criterion_row_values(score) -> list[list]:
    """Các dòng sheet "Điểm tiêu chí" của MỘT cuộc — mỗi tiêu chí một dòng.

    Đi theo thứ tự rubric chứ không theo thứ tự khoá trong JSONB: hai cuộc phải
    có cùng thứ tự dòng thì mới so sánh được bằng mắt.

    Tiêu chí model không chấm (thiếu khoá) bị bỏ qua thay vì ghi dòng rỗng — dòng
    rỗng chỉ làm loãng file mà không mang thông tin nào.
    """
    criteria = score.criteria or {}
    sale = _sale_name(score)
    rows: list[list] = []
    for code, criterion in CRITERION_BY_CODE.items():
        item = criteria.get(code)
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                score.id,
                score.call_date.isoformat(),
                sale,
                score.order_id_snapshot or "",
                _BLOCK_LABELS.get(BLOCK_OF_CRITERION[code], ""),
                code,
                criterion.label,
                # `na` thì để TRỐNG ô điểm, không ghi 0: 0 là "làm nhưng sai",
                # còn na là "không có cơ hội làm" — gộp lại là bóp méo số liệu.
                "" if item.get("na") or item.get("score") is None else int(item["score"]),
                SCORE_MAX,
                "x" if item.get("na") else "",
                item.get("evidence") or "",
            ]
        )
    return rows


def summary_sheet_rows(summary: dict, status_breakdown: list[dict]) -> list[list]:
    """Toàn bộ sheet "Tổng hợp" — bản chụp phẳng của tab Tổng quan.

    Trả về danh sách dòng (kể cả dòng tiêu đề của từng khối và dòng trống ngăn
    cách) thay vì nhiều bảng rời: sheet này để ĐỌC, không để lọc, nên xếp dọc
    một cột là đủ và tránh phải bịa ra ba writer khác nhau.
    """
    period = summary.get("period") or {}
    rows: list[list] = [
        ["TỔNG QUAN"],
        ["Từ ngày", period.get("start", "")],
        ["Đến ngày", period.get("end", "")],
        ["Phiên bản rubric", summary.get("rubric_version", "")],
        ["Số cuộc đã chấm", summary.get("total_calls", 0)],
        ["Điểm trung bình", summary.get("avg_score", 0)],
        ["Ngưỡng đạt", summary.get("pass_score", 0)],
        ["Tỷ lệ đạt", summary.get("pass_rate", 0)],
        ["Từ/phút trung bình", summary.get("avg_wpm", 0)],
        ["Tỷ lệ sale nói trung bình", summary.get("avg_talk_ratio", 0)],
        [],
        ["ĐIỂM THEO TIÊU CHÍ"],
        ["Mã", "Tiêu chí", "Khối", "Điểm TB", "Thang điểm", "Số cuộc chấm", "Số cuộc 0 điểm"],
    ]
    for item in summary.get("criteria") or []:
        rows.append(
            [
                item["code"],
                item["label"],
                _BLOCK_LABELS.get(item["block"], item["block"]),
                item["avg_score"],
                item["max_score"],
                item["call_count"],
                item["zero_count"],
            ]
        )

    rows += [[], ["CỜ ĐỎ"], ["Mã", "Cờ đỏ", "Số cuộc"]]
    for item in summary.get("red_flags") or []:
        rows.append([item["code"], item["label"], item["count"]])

    rows += [[], ["THEO TRẠNG THÁI SALE"], ["Mã", "Trạng thái", "Số cuộc", "Điểm TB"]]
    for item in status_breakdown:
        code = item["code"]
        rows.append(
            [
                code,
                # Mã rỗng = cuộc chưa khớp đơn hoặc đơn chưa có phiếu đánh giá.
                # Ghi rõ ra chứ không để trống: một nhóm lớn không tên là thứ làm
                # người đọc mất niềm tin vào cả bảng.
                SALES_STATUS_LABELS.get(code, code) if code else "(chưa khớp đơn)",
                item["call_count"],
                item["avg_score"],
            ]
        )
    return rows
