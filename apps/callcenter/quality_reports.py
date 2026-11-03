"""Tổng hợp số liệu cho trang Chất lượng cuộc gọi.

Tách khỏi transport: view chỉ nhận tham số và trả JSON, mọi phép đếm nằm ở đây
để test được mà không dựng request.

v1 đánh giá CHUNG cả team — không có grain theo nhân viên (Zenify chưa trả định
danh nhân viên thật, xem docs §6). Mọi hàm đều lọc theo ``rubric_version`` vì
điểm chỉ so sánh được trong cùng phiên bản rubric.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Avg, Count, Q

from apps.sysconfig import sysconfig

from .models import CallQualityScore, CallRecord, TranscribeStatus
from .qa_rubric import (
    BLOCKS,
    CRITERIA,
    CRITERION_ACTIONS,
    CRITERION_BY_CODE,
    RED_FLAGS,
    RUBRIC_VERSION,
    SCORE_MAX,
    STRENGTH_THRESHOLD,
    WEAKNESS_THRESHOLD,
)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def current_pass_score() -> int:
    """Ngưỡng "đạt" dùng cho tỷ lệ đạt trên trang báo cáo."""
    return sysconfig.get("QA_PASS_SCORE")


def default_range(days: int = 30) -> DateRange:
    from django.utils import timezone

    today = timezone.localdate()
    return DateRange(start=today - timedelta(days=days - 1), end=today)


def scores_in_period(period: DateRange, sales_status: str = ""):
    """Tập điểm của kỳ, lọc thêm theo trạng thái sale nếu có.

    Trạng thái đọc từ ``OrderAssessment`` của đơn — tức trạng thái HIỆN TẠI, không
    phải bản chụp ``sale_sales_status_code`` lúc chấm. Đổi lại số luôn khớp với
    trang Đơn hàng; cái giá là báo cáo kỳ cũ sẽ đổi theo khi sale sửa trạng thái.
    Bản chụp vẫn được giữ nguyên và chỉ dùng cho khối "đối chiếu chéo" ở màn chi
    tiết — nơi mục đích chính là ĐO ĐỘ LỆCH giữa AI và ghi nhận của sale.
    """
    queryset = CallQualityScore.objects.filter(
        rubric_version=RUBRIC_VERSION,
        call_date__gte=period.start,
        call_date__lte=period.end,
    )
    if sales_status:
        queryset = queryset.filter(
            call_record__order__assessment__sales_status_code=sales_status
        )
    return queryset


def filter_calls(period: DateRange, filters: dict):
    """Tập cuộc đã chấm sau khi áp bộ lọc của tab "Cuộc gọi".

    MỘT chỗ duy nhất cho cả màn hình danh sách lẫn job xuất Excel. Tách ra vì bài
    học của trang Đơn hàng: bộ lọc bị chép sang nhiều nơi thì sớm muộn file xuất
    ra và bảng đang xem lệch nhau mà không ai biết.

    Khoá của ``filters`` trùng ĐÚNG tên query param của endpoint danh sách, để
    frontend gửi thẳng bộ lọc đang áp mà không phải dịch sang một bộ tên thứ hai.
    """
    queryset = scores_in_period(period, str(filters.get("sales_status") or "").strip())

    min_score = filters.get("min_score")
    if min_score not in (None, ""):
        queryset = queryset.filter(total_score__gte=min_score)
    max_score = filters.get("max_score")
    if max_score not in (None, ""):
        queryset = queryset.filter(total_score__lte=max_score)
    if filters.get("has_red_flag"):
        queryset = queryset.exclude(red_flags=[])

    # Lọc theo sale PHỤ TRÁCH ĐƠN: Zenify không cho biết ai cầm máy nên đây là
    # chiều duy nhất lọc được theo người.
    staff = str(filters.get("staff") or "").strip()
    if staff.isdigit():
        queryset = queryset.filter(call_record__order__staff_id=int(staff))

    sku = str(filters.get("sku") or "").strip()
    if sku:
        # distinct(): đơn có 2 dòng hàng cùng SKU sẽ nhân đôi dòng sau khi join.
        queryset = queryset.filter(call_record__order__items__sku=sku).distinct()

    # "Cuộc bị 0 điểm ở tiêu chí X" — đường từ bảng tiêu chí yếu sang ví dụ thật
    # để team lead mang đi dạy.
    criterion = str(filters.get("criterion") or "").strip()
    if criterion in CRITERION_BY_CODE:
        queryset = queryset.filter(**{f"criteria__{criterion}__score": 0})

    return queryset


# Khoá mô tả KHOẢNG NGÀY, không phải bộ lọc. Job xuất Excel nhét cả hai vào cùng
# một dict `filters` (để params_hash ổn định), nên phải loại ra ở đây — nếu không
# thì `_has_filter` luôn đúng và dòng độ phủ lúc nào cũng kèm ghi chú thừa.
_PERIOD_KEYS = frozenset({"start", "end"})


def _has_filter(filters: dict) -> bool:
    """Có bộ lọc nào đang áp ngoài khoảng ngày không.

    Dùng cho cờ ``ignores_filters`` của dòng độ phủ — dòng đó CỐ Ý đếm cả kỳ, và
    giao diện cần biết khi nào phải nói rõ điều đó ra.
    """
    return any(
        bool(value) for key, value in (filters or {}).items() if key not in _PERIOD_KEYS
    )


def build_summary(period: DateRange, filters: dict | None = None) -> dict:
    """Thẻ số tổng + điểm trung bình từng tiêu chí + dòng đếm vận hành."""
    filters = filters or {}
    scores = filter_calls(period, filters)
    aggregate = scores.aggregate(
        total_calls=Count("id"),
        avg_score=Avg("total_score"),
        avg_wpm=Avg("wpm"),
        avg_talk_ratio=Avg("talk_ratio"),
        passed=Count("id", filter=Q(total_score__gte=current_pass_score())),
    )
    total_calls = aggregate["total_calls"] or 0

    criteria = _criteria_averages(scores)
    # Chỉ xếp loại tiêu chí CÓ MẪU. Tiêu chí chưa cuộc nào chạm tới thì điểm 0.0
    # là do thiếu dữ liệu, đưa vào "cần cải thiện" là kết luận sai.
    scored_criteria = [item for item in criteria if item["call_count"] > 0]
    strengths = [
        item for item in scored_criteria if item["avg_score"] >= STRENGTH_THRESHOLD
    ]
    strengths.sort(key=lambda item: -item["avg_score"])
    weaknesses = [item for item in scored_criteria if item["avg_score"] < WEAKNESS_THRESHOLD]

    return {
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat()},
        "rubric_version": RUBRIC_VERSION,
        "pass_score": current_pass_score(),
        "total_calls": total_calls,
        "avg_score": round(float(aggregate["avg_score"] or 0), 2),
        "pass_rate": round((aggregate["passed"] or 0) / total_calls, 3) if total_calls else 0.0,
        "avg_wpm": int(aggregate["avg_wpm"] or 0),
        "avg_talk_ratio": round(float(aggregate["avg_talk_ratio"] or 0), 3),
        "blocks": _block_averages(scores),
        "criteria": criteria,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "actions": [
            {
                "code": item["code"],
                "label": item["label"],
                "action": CRITERION_ACTIONS.get(item["code"], ""),
            }
            for item in weaknesses[:3]
        ],
        "red_flags": _red_flag_counts(scores),
        # CỐ Ý không truyền `filters` xuống: coverage là dòng đếm HẠ TẦNG (tổng
        # đài có đang mất file ghi âm không), lọc nó theo trạng thái sale thì tỷ lệ
        # "có ghi âm" mất hết ý nghĩa. Cờ dưới để FE nói rõ dòng này ngoài bộ lọc.
        "coverage": {**build_coverage(period), "ignores_filters": _has_filter(filters)},
    }


def _block_averages(scores) -> list[dict]:
    """Điểm TB từng khối. block_scores là JSONB nên cộng trong Python — vài trăm
    dòng mỗi kỳ, rẻ hơn nhiều so với dựng expression index cho một báo cáo."""
    totals: dict[str, float] = {b.code: 0.0 for b in BLOCKS}
    counts: dict[str, int] = {b.code: 0 for b in BLOCKS}
    for row in scores.values_list("block_scores", flat=True):
        for code, value in (row or {}).items():
            if code in totals:
                totals[code] += float(value or 0)
                counts[code] += 1
    return [
        {
            "code": block.code,
            "label": block.label,
            "weight": block.weight,
            "avg_score": round(totals[block.code] / counts[block.code], 2)
            if counts[block.code]
            else 0.0,
            "call_count": counts[block.code],
        }
        for block in BLOCKS
    ]


def _criteria_averages(scores) -> list[dict]:
    """Điểm TB từng tiêu chí trên thang 0-2, SẮP THEO ĐIỂM THẤP DẦN.

    Đây là màn hình chính của trang: dòng đầu bảng chính là nội dung buổi đào
    tạo tuần tới. ``na`` bị loại khỏi mẫu số nên ``call_count`` mỗi tiêu chí có
    thể khác nhau — FE phải hiện con số đó cạnh điểm.
    """
    totals: dict[str, int] = {c.code: 0 for c in CRITERIA}
    counts: dict[str, int] = {c.code: 0 for c in CRITERIA}
    zeros: dict[str, int] = {c.code: 0 for c in CRITERIA}

    for row in scores.values_list("criteria", flat=True):
        for code, item in (row or {}).items():
            if code not in totals or not isinstance(item, dict):
                continue
            if item.get("na") or item.get("score") is None:
                continue
            score = int(item["score"])
            totals[code] += score
            counts[code] += 1
            if score == 0:
                zeros[code] += 1

    from .qa_rubric import BLOCK_OF_CRITERION

    items = [
        {
            "code": criterion.code,
            "label": criterion.label,
            "block": BLOCK_OF_CRITERION[criterion.code],
            "avg_score": round(totals[criterion.code] / counts[criterion.code], 2)
            if counts[criterion.code]
            else 0.0,
            "max_score": SCORE_MAX,
            "call_count": counts[criterion.code],
            "zero_count": zeros[criterion.code],
        }
        for criterion in CRITERIA
    ]
    # Tiêu chí không có mẫu nào xuống cuối — 0.0 vì thiếu dữ liệu không phải là
    # điểm kém, để nó đứng đầu bảng "cần đào tạo" là chỉ sai chỗ.
    items.sort(key=lambda x: (x["call_count"] == 0, x["avg_score"]))
    return items


def _red_flag_counts(scores) -> list[dict]:
    counts: dict[str, int] = dict.fromkeys(RED_FLAGS, 0)
    for row in scores.values_list("red_flags", flat=True):
        for item in row or []:
            code = (item or {}).get("code")
            if code in counts:
                counts[code] += 1
    return [
        {"code": code, "label": label, "count": counts[code]}
        for code, label in RED_FLAGS.items()
    ]


def build_coverage(period: DateRange) -> dict:
    """Dòng đếm vận hành: cuộc gọi → có ghi âm → có transcript → đã chấm.

    KHÔNG dùng để đánh giá sale. Nó trả lời một câu hỏi hạ tầng: tổng đài có
    đang mất file ghi âm không — mà ghi âm mất là không lấy lại được.
    """
    records = CallRecord.objects.filter(
        start_time__date__gte=period.start, start_time__date__lte=period.end
    )
    counts = records.aggregate(
        total=Count("id"),
        with_recording=Count("id", filter=~Q(recording_url="")),
        transcribed=Count(
            "id", filter=Q(transcribe_status=TranscribeStatus.DONE) & ~Q(transcript="")
        ),
    )
    scored = scores_in_period(period).count()
    return {
        "total_calls": counts["total"] or 0,
        "with_recording": counts["with_recording"] or 0,
        "transcribed": counts["transcribed"] or 0,
        "scored": scored,
    }


def build_trend(period: DateRange, filters: dict | None = None) -> list[dict]:
    """Điểm TB theo tuần (tổng + từng khối) để đo hiệu quả đào tạo.

    Gom theo tuần chứ không theo ngày: ~17 cuộc/ngày thì đường ngày nhiễu tới
    mức không đọc được xu hướng.
    """
    buckets: dict[date, dict] = {}
    rows = filter_calls(period, filters or {}).values_list(
        "call_date", "total_score", "block_scores"
    )
    for call_date, total_score, block_scores in rows:
        week_start = call_date - timedelta(days=call_date.weekday())
        bucket = buckets.setdefault(
            week_start, {"total": 0.0, "count": 0, "blocks": {}, "block_counts": {}}
        )
        bucket["total"] += float(total_score or 0)
        bucket["count"] += 1
        for code, value in (block_scores or {}).items():
            bucket["blocks"][code] = bucket["blocks"].get(code, 0.0) + float(value or 0)
            bucket["block_counts"][code] = bucket["block_counts"].get(code, 0) + 1

    result = []
    for week_start in sorted(buckets):
        bucket = buckets[week_start]
        result.append(
            {
                "week_start": week_start.isoformat(),
                "call_count": bucket["count"],
                "avg_score": round(bucket["total"] / bucket["count"], 2)
                if bucket["count"]
                else 0.0,
                "blocks": {
                    code: round(value / bucket["block_counts"][code], 2)
                    for code, value in bucket["blocks"].items()
                    if bucket["block_counts"].get(code)
                },
            }
        )
    return result


def build_status_breakdown(period: DateRange, filters: dict | None = None) -> list[dict]:
    """Số cuộc + điểm TB theo TRẠNG THÁI SALE hiện tại của đơn.

    Đây là câu trả lời cho "cuộc chốt được đơn khác gì cuộc khách suy nghĩ thêm".
    Gom bằng SQL (không phải Python như các hàm trên): chỉ cần đếm + trung bình
    trên cột thường, không đụng tới JSONB nên `values().annotate()` là đường
    ngắn nhất và không kéo cả tập về bộ nhớ.

    Cuộc chưa khớp đơn (hoặc đơn chưa có phiếu đánh giá) rơi vào mã rỗng — GIỮ
    LẠI chứ không loại: nó là một nhóm có thật và người đọc cần thấy nó lớn cỡ nào.
    """
    rows = (
        filter_calls(period, filters or {})
        .values("call_record__order__assessment__sales_status_code")
        .annotate(call_count=Count("id"), avg_score=Avg("total_score"))
        .order_by("-call_count")
    )
    return [
        {
            "code": row["call_record__order__assessment__sales_status_code"] or "",
            "call_count": row["call_count"],
            "avg_score": round(float(row["avg_score"] or 0), 2),
        }
        for row in rows
    ]


def build_agent_breakdown(
    period: DateRange, *, min_calls: int = 1, filters: dict | None = None
) -> list[dict]:
    """Điểm theo từng sale PHỤ TRÁCH ĐƠN, kèm tiêu chí mạnh/yếu nhất của người đó.

    Zenify không trả định danh người cầm máy nên đây là chiều duy nhất gom được
    theo người — bảng phải nói rõ điều đó, đừng để ai đọc thành "ai gọi dở".

    Gom trong Python thay vì SQL: điểm từng tiêu chí nằm trong JSONB, và ở quy mô
    vài trăm dòng mỗi kỳ thì aggregate JSONB bằng SQL chỉ làm truy vấn khó đọc mà
    không nhanh hơn đáng kể.
    """
    rows = (
        filter_calls(period, filters or {})
        .filter(call_record__order__staff__isnull=False)
        .values_list(
            "call_record__order__staff_id",
            "call_record__order__staff__display_name",
            "call_record__order__staff__username",
            "total_score",
            "criteria",
        )
    )

    agents: dict[int, dict] = {}
    for staff_id, display_name, username, total_score, criteria in rows:
        agent = agents.setdefault(
            staff_id,
            {
                "staff_id": staff_id,
                "name": display_name or username,
                "call_count": 0,
                "_total": 0.0,
                "_sum": {},
                "_count": {},
            },
        )
        agent["call_count"] += 1
        agent["_total"] += float(total_score or 0)
        for code, item in (criteria or {}).items():
            if code not in CRITERION_ACTIONS or not isinstance(item, dict):
                continue
            if item.get("na") or item.get("score") is None:
                continue
            agent["_sum"][code] = agent["_sum"].get(code, 0) + int(item["score"])
            agent["_count"][code] = agent["_count"].get(code, 0) + 1

    labels = {c.code: c.label for c in CRITERIA}
    result: list[dict] = []
    for agent in agents.values():
        if agent["call_count"] < min_calls:
            continue
        averages = [
            {
                "code": code,
                "label": labels[code],
                "avg_score": round(agent["_sum"][code] / agent["_count"][code], 2),
                "call_count": agent["_count"][code],
            }
            for code in agent["_sum"]
            if agent["_count"].get(code)
        ]
        averages.sort(key=lambda item: item["avg_score"])
        result.append(
            {
                "staff_id": agent["staff_id"],
                "name": agent["name"],
                "call_count": agent["call_count"],
                "avg_score": round(agent["_total"] / agent["call_count"], 2),
                # Mạnh/yếu nhất CỦA CHÍNH NGƯỜI ĐÓ — so với các tiêu chí khác của
                # bản thân, không phải so với đồng nghiệp. Một người điểm thấp
                # đều vẫn có "điểm làm tốt nhất", và đó mới là thứ dùng để khen.
                "best": averages[-1] if averages else None,
                "worst": averages[0] if averages else None,
                "action": CRITERION_ACTIONS.get(averages[0]["code"], "") if averages else "",
            }
        )
    result.sort(key=lambda item: -item["avg_score"])
    return result
