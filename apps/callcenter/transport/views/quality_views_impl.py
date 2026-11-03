"""API trang Chất lượng cuộc gọi.

Gate bằng section RBAC ``call-quality`` — một mức ``view`` cho quản lý. v1 không
hiển thị theo nhân viên nên không có chuyện sale xem điểm của nhau; đổi lại
transcript và trích dẫn (PII: SĐT, thông tin bệnh của khách) nằm trọn sau quyền
này, không mở cho toàn công ty.
"""


from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.dates import parse_iso_date
from apps.users.permissions import require_permission, require_section

from apps.callcenter import quality_reports
from apps.callcenter.models import CallQualityReview, CallQualityScore
# Nhãn tiếng Việt lấy thẳng từ catalog của `orders` — MỘT nguồn sự thật. Chép
# tay sang FE là bảo đảm lệch khi ai đó thêm trạng thái mới (mã `underlying_condition`
# thêm 2026-07-29 là ví dụ gần nhất).
from apps.orders.crm_catalog import NOT_CLOSED_REASON_LABELS, SALES_STATUS_LABELS
from apps.callcenter.qa_rubric import (
    BLOCK_OF_CRITERION,
    CRITERION_BY_CODE,
    RED_FLAGS,
    RUBRIC_VERSION,
)

SECTION = "call-quality"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _resolve_period(request) -> quality_reports.DateRange:
    """Khoảng ngày từ query param, mặc định 30 ngày gần nhất.

    Tham số hỏng thì rơi về mặc định thay vì 400: đây là báo cáo chỉ đọc, trả
    dữ liệu vẫn hữu ích hơn chặn người dùng vì gõ sai một ký tự.
    """
    period = quality_reports.default_range()
    start = parse_iso_date(request.query_params.get("start")) or period.start
    end = parse_iso_date(request.query_params.get("end")) or period.end
    if start > end:
        start, end = end, start
    return quality_reports.DateRange(start=start, end=end)


def _resolve_sales_status(request) -> str:
    """Mã trạng thái sale để lọc, rỗng = không lọc.

    Không validate theo danh mục: mã lạ chỉ đơn giản không khớp dòng nào, còn
    trả 400 thì một bộ lọc đọc-only chặn cả trang chỉ vì mã cũ còn trong URL cũ.
    """
    return str(request.query_params.get("sales_status", "")).strip()


def _scope_filters(request) -> dict:
    """Bộ lọc chi phối CẢ TRANG (khác `_calls_filters` của riêng bảng danh sách).

    Hiện chỉ có trạng thái sale. Trả về dict thay vì chuỗi để thêm trục lọc toàn
    trang sau này không phải đổi chữ ký của 3 hàm báo cáo.
    """
    return {"sales_status": _resolve_sales_status(request)}


_SALES_STATUS_PARAM = OpenApiParameter(
    "sales_status", str, description="Mã trạng thái sale hiện tại của đơn"
)

_PERIOD_PARAMS = [
    OpenApiParameter("start", str, description="Ngày bắt đầu YYYY-MM-DD (mặc định 30 ngày trước)"),
    OpenApiParameter("end", str, description="Ngày kết thúc YYYY-MM-DD (mặc định hôm nay)"),
    _SALES_STATUS_PARAM,
]


@extend_schema(
    operation_id="call_quality_summary",
    parameters=_PERIOD_PARAMS,
    responses={200: OpenApiResponse(description="Thẻ số tổng, điểm từng khối/tiêu chí, độ phủ")},
    description="Tổng quan chất lượng cuộc gọi toàn team theo khoảng ngày.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_summary(request):
    return Response(
        quality_reports.build_summary(_resolve_period(request), _scope_filters(request))
    )


@extend_schema(
    operation_id="call_quality_trend",
    parameters=_PERIOD_PARAMS,
    responses={200: OpenApiResponse(description="Điểm trung bình theo tuần")},
    description="Xu hướng điểm theo tuần (tổng + từng khối) để đo hiệu quả đào tạo.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_trend(request):
    return Response(
        {
            "items": quality_reports.build_trend(
                _resolve_period(request), _scope_filters(request)
            )
        }
    )


def _sale_name(score: CallQualityScore) -> str:
    """Tên sale PHỤ TRÁCH ĐƠN, không phải người cầm máy.

    Zenify không trả định danh nhân viên (`agent_name` rỗng, 4 `agent_id` dùng
    chung cho cả nghìn cuộc) nên đây là đường duy nhất có tên. Đơn đổi tay giữa
    các sale thì tên hiện sẽ là người đang giữ đơn — vì vậy nhãn trên trang phải
    nói rõ "sale phụ trách đơn", đừng để ai hiểu nhầm là người thực hiện cuộc gọi.
    """
    if score.agent_name:
        return score.agent_name
    order = getattr(score.call_record, "order", None)
    staff = getattr(order, "staff", None) if order else None
    if staff is None:
        return ""
    return staff.display_name or staff.username


def _customer_phone(score: CallQualityScore) -> str:
    """SĐT khách của chính cuộc gọi. `caller` là số đã dùng để khớp đơn."""
    record = score.call_record
    if record.caller:
        return record.caller
    order = getattr(record, "order", None)
    return order.phone if order else ""


def _order_skus(score: CallQualityScore) -> list[str]:
    """SKU các sản phẩm trên đơn khớp với cuộc gọi.

    Đọc `OrderItem.sku` (đã denormalize sẵn trên dòng hàng) chứ không join sang
    Product: một truy vấn ít hơn, và SKU tại thời điểm bán mới là thứ đúng khi
    sản phẩm được đổi mã về sau.
    """
    order = getattr(score.call_record, "order", None)
    if order is None:
        return []
    seen: list[str] = []
    for item in order.items.all():
        if item.sku and item.sku not in seen:
            seen.append(item.sku)
    return seen


def _live_sales_status(score: CallQualityScore) -> str:
    """Trạng thái sale HIỆN TẠI của đơn, không phải bản chụp lúc chấm.

    Bản chụp ``sale_sales_status_code`` vẫn nằm nguyên trên bản ghi và vẫn là thứ
    khối "đối chiếu chéo" dùng — nó tồn tại để ĐO ĐỘ LỆCH giữa AI và ghi nhận của
    sale, thay bằng giá trị live thì báo cáo đó tự lành mỗi lần có người sửa lại
    trạng thái cho đẹp. Còn bộ lọc và cột trên bảng thì phải khớp trang Đơn hàng,
    nên đọc live.

    ``getattr(order, "assessment", None)`` an toàn với OneToOne ngược chưa có
    dòng: Django dựng ``RelatedObjectDoesNotExist`` kế thừa cả ``AttributeError``.
    """
    order = getattr(score.call_record, "order", None)
    assessment = getattr(order, "assessment", None) if order else None
    return (assessment.sales_status_code or "") if assessment else ""


def _score_row(score: CallQualityScore) -> dict:
    order = getattr(score.call_record, "order", None)
    sales_status_code = _live_sales_status(score)
    return {
        "skus": _order_skus(score),
        "id": score.id,
        "call_record_id": score.call_record_id,
        "call_date": score.call_date.isoformat(),
        "sale_name": _sale_name(score),
        "customer_name": order.name if order else "",
        "customer_phone": _customer_phone(score),
        "order_id": score.order_id_snapshot,
        "duration_seconds": score.duration_seconds,
        "total_score": float(score.total_score),
        "block_scores": score.block_scores,
        "red_flags": [item.get("code") for item in score.red_flags or []],
        "sales_status_code": sales_status_code,
        "sales_status_label": SALES_STATUS_LABELS.get(sales_status_code, sales_status_code),
        "wpm": score.wpm,
        "talk_ratio": float(score.talk_ratio),
        "summary": score.summary,
    }


def _calls_filters(request) -> dict:
    """Bộ lọc của tab "Cuộc gọi" đọc từ query param.

    Khoá trùng đúng tên param để `quality_reports.filter_calls` nhận được y
    nguyên dict này, và để frontend gửi lại đúng bộ đó khi bấm Xuất Excel.
    """
    return {
        "sales_status": _resolve_sales_status(request),
        "min_score": request.query_params.get("min_score"),
        "max_score": request.query_params.get("max_score"),
        "has_red_flag": str(request.query_params.get("has_red_flag", "")).strip()
        in {"1", "true"},
        "staff": request.query_params.get("staff"),
        "sku": request.query_params.get("sku"),
        "criterion": request.query_params.get("criterion"),
    }


@extend_schema(
    operation_id="call_quality_calls",
    parameters=[
        *_PERIOD_PARAMS,
        OpenApiParameter("min_score", int, description="Điểm tổng tối thiểu"),
        OpenApiParameter("max_score", int, description="Điểm tổng tối đa"),
        OpenApiParameter("has_red_flag", str, description="1 = chỉ cuộc có cờ đỏ"),
        OpenApiParameter("criterion", str, description="Mã tiêu chí bị 0 điểm"),
        OpenApiParameter("staff", int, description="ID sale phụ trách đơn"),
        OpenApiParameter("sku", str, description="SKU sản phẩm trên đơn"),
        OpenApiParameter("page", int),
        OpenApiParameter("page_size", int),
    ],
    responses={200: OpenApiResponse(description="Danh sách cuộc gọi đã chấm")},
    description="Danh sách cuộc gọi đã chấm, lọc được theo điểm/cờ đỏ/tiêu chí yếu.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_calls(request):
    period = _resolve_period(request)
    # Bộ lọc dựng ở quality_reports.filter_calls — DÙNG CHUNG với job xuất Excel,
    # để file tải về luôn khớp đúng bảng đang xem.
    queryset = quality_reports.filter_calls(period, _calls_filters(request))

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    total = queryset.count()
    offset = (page - 1) * page_size
    # select_related tới staff: thiếu nó là N+1 (mỗi dòng 3 query cho
    # call_record → order → staff) trên trang 100 dòng.
    rows = (
        queryset.select_related(
            "call_record",
            "call_record__order",
            "call_record__order__staff",
            # Trạng thái sale nay đọc live từ phiếu đánh giá — thiếu dòng này là
            # thêm 1 query mỗi dòng trên trang 100 dòng.
            "call_record__order__assessment",
        )
        .prefetch_related("call_record__order__items")
        .order_by("-call_date", "-id")[offset : offset + page_size]
    )
    return Response(
        {
            "items": [_score_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@extend_schema(
    operation_id="call_quality_call_detail",
    responses={
        200: OpenApiResponse(description="Chi tiết điểm 1 cuộc gọi kèm trích dẫn + transcript"),
        404: OpenApiResponse(description="Không tìm thấy bản chấm"),
    },
    description="Chi tiết 1 cuộc: điểm từng tiêu chí kèm trích dẫn, transcript, đối chiếu chéo.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_call_detail(request, score_id: int):
    score = (
        CallQualityScore.objects.filter(id=score_id)
        .select_related(
            "call_record",
            "call_record__order",
            "call_record__order__staff",
            "call_record__order__assessment",
        )
        .prefetch_related("reviews", "call_record__order__items")
        .first()
    )
    if score is None:
        return Response({"detail": "Không tìm thấy bản chấm."}, status=status.HTTP_404_NOT_FOUND)

    criteria = []
    for code, item in (score.criteria or {}).items():
        criterion = CRITERION_BY_CODE.get(code)
        if criterion is None:
            continue
        criteria.append(
            {
                "code": code,
                "label": criterion.label,
                "guide": criterion.guide,
                "block": BLOCK_OF_CRITERION[code],
                "score": item.get("score"),
                "na": bool(item.get("na")),
                "evidence": item.get("evidence") or "",
            }
        )

    return Response(
        {
            **_score_row(score),
            "rubric_version": score.rubric_version,
            "model_name": score.model_name,
            "coaching": score.coaching,
            "criteria": criteria,
            "red_flags": [
                {
                    "code": item.get("code"),
                    "label": RED_FLAGS.get(item.get("code"), ""),
                    "evidence": item.get("evidence") or "",
                }
                for item in score.red_flags or []
            ],
            "rejected": score.rejected,
            "cross_check": {
                "ai_not_closed_reason": score.ai_not_closed_reason,
                "sale_not_closed_reason_code": score.sale_not_closed_reason_code,
                "sale_not_closed_reason_label": NOT_CLOSED_REASON_LABELS.get(
                    score.sale_not_closed_reason_code, score.sale_not_closed_reason_code
                ),
                # BẢN CHỤP lúc chấm — cố ý giữ nguyên, đây là vế "sale ghi nhận
                # gì" của phép đối chiếu. Trạng thái hiện tại nằm ở
                # `sales_status_code` của dòng (xem _live_sales_status).
                "sale_sales_status_code": score.sale_sales_status_code,
                "sale_sales_status_label": SALES_STATUS_LABELS.get(
                    score.sale_sales_status_code, score.sale_sales_status_code
                ),
                # Sale đã sửa trạng thái sau khi chấm hay chưa. Không có cờ này
                # thì người đọc thấy hai mã khác nhau trên cùng màn hình mà không
                # hiểu vì sao.
                "sales_status_changed_since": bool(
                    score.sale_sales_status_code
                    and score.sale_sales_status_code != _live_sales_status(score)
                ),
                "contraindications": score.contraindications,
            },
            "transcript": score.call_record.transcript,
            "recording_url": score.call_record.recording_url,
            "reviews": [
                {
                    "id": review.id,
                    "criterion": review.criterion,
                    "ai_score": review.ai_score,
                    "human_score": review.human_score,
                    "note": review.note,
                    "created_at": review.created_at,
                }
                for review in score.reviews.all()
            ],
        }
    )


@extend_schema(
    operation_id="call_quality_review",
    request=inline_serializer(
        name="CallQualityReviewRequest",
        fields={
            "criterion": drf_serializers.CharField(
                required=False, allow_blank=True, help_text="Mã tiêu chí; rỗng = nhận xét cả cuộc"
            ),
            "human_score": drf_serializers.IntegerField(
                required=False, allow_null=True, min_value=0, max_value=2
            ),
            "note": drf_serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        201: OpenApiResponse(description="Đã ghi nhận phản hồi"),
        400: OpenApiResponse(description="Tham số không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy bản chấm"),
    },
    description="Ghi nhận 'AI chấm sai' + điểm đúng theo người. KHÔNG ghi đè điểm AI.",
)
# Ghi review "AI chấm sai" là hành vi GHI -> call-quality:edit (action thêm vào
# PERMISSION_CATALOG cùng MR; grandfather 0039 cấp :edit cho role có :view).
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(f"{SECTION}:edit")])
def quality_review(request, score_id: int):
    score = CallQualityScore.objects.filter(id=score_id).first()
    if score is None:
        return Response({"detail": "Không tìm thấy bản chấm."}, status=status.HTTP_404_NOT_FOUND)

    criterion = str(request.data.get("criterion") or "").strip()
    if criterion and criterion not in CRITERION_BY_CODE:
        return Response(
            {"detail": f"Tiêu chí không hợp lệ: {criterion}"}, status=status.HTTP_400_BAD_REQUEST
        )

    human_score = request.data.get("human_score")
    if human_score not in (None, ""):
        try:
            human_score = int(human_score)
        except (TypeError, ValueError):
            return Response(
                {"detail": "human_score phải là số nguyên 0-2."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if human_score < 0 or human_score > 2:
            return Response(
                {"detail": "human_score phải nằm trong khoảng 0-2."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        human_score = None

    ai_score = None
    if criterion:
        item = (score.criteria or {}).get(criterion) or {}
        ai_score = item.get("score")

    review = CallQualityReview.objects.create(
        score=score,
        reviewer=request.user,
        criterion=criterion,
        ai_score=ai_score,
        human_score=human_score,
        note=str(request.data.get("note") or "").strip(),
    )
    return Response({"id": review.id}, status=status.HTTP_201_CREATED)


@extend_schema(
    operation_id="call_quality_agents",
    parameters=[
        *_PERIOD_PARAMS,
        OpenApiParameter("min_calls", int, description="Số cuộc tối thiểu để vào bảng"),
    ],
    responses={200: OpenApiResponse(description="Điểm theo từng sale phụ trách đơn")},
    description="Điểm TB, điểm làm tốt nhất và cần cải thiện nhất của từng sale.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_agents(request):
    try:
        min_calls = max(1, int(request.query_params.get("min_calls", 1)))
    except (TypeError, ValueError):
        min_calls = 1
    return Response(
        {
            "items": quality_reports.build_agent_breakdown(
                _resolve_period(request),
                min_calls=min_calls,
                filters=_scope_filters(request),
            )
        }
    )


@extend_schema(
    operation_id="call_quality_filter_options",
    parameters=_PERIOD_PARAMS,
    responses={200: OpenApiResponse(description="Danh sách sale và SKU có trong dữ liệu đã chấm")},
    description="Giá trị lọc CÓ THẬT trong kỳ — không đổ toàn bộ danh mục sản phẩm/nhân sự về FE.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_filter_options(request):
    """Chỉ trả những sale/SKU thực sự xuất hiện trong tập đã chấm của kỳ.

    Đổ nguyên danh mục 10k sản phẩm về cho một dropdown là lãng phí, và tệ hơn:
    người dùng chọn một SKU không có cuộc gọi nào rồi tưởng hệ thống hỏng.
    """
    period = _resolve_period(request)
    sales_status = _resolve_sales_status(request)
    scores = quality_reports.scores_in_period(period)

    # Ô sale và ô SKU thu hẹp theo trạng thái đang chọn — chọn một trạng thái rồi
    # thấy tên sale không có cuộc nào trong đó là bộ lọc nói dối. Riêng danh sách
    # TRẠNG THÁI thì luôn tính trên toàn kỳ: thu hẹp theo chính nó sẽ khoá người
    # dùng lại ở lựa chọn hiện tại, không đổi sang trạng thái khác được.
    narrowed = quality_reports.scores_in_period(period, sales_status)

    sales: dict[int, str] = {}
    for staff_id, display_name, username in narrowed.filter(
        call_record__order__staff__isnull=False
    ).values_list(
        "call_record__order__staff_id",
        "call_record__order__staff__display_name",
        "call_record__order__staff__username",
    ).distinct():
        sales[staff_id] = display_name or username

    skus = sorted(
        value
        for value in narrowed.values_list("call_record__order__items__sku", flat=True)
        .distinct()
        if value
    )

    # Trạng thái sale CÓ THẬT trong kỳ, kèm nhãn tiếng Việt từ crm_catalog. Đọc
    # từ phiếu đánh giá của đơn (trạng thái hiện tại) chứ không phải bản chụp lúc
    # chấm — nếu không, ô lọc còn liệt kê trạng thái mà không dòng nào còn mang.
    # Mã rỗng (cuộc chưa khớp đơn) bị loại: chọn nó không lọc ra gì có nghĩa.
    statuses = sorted(
        {
            code
            for code in scores.values_list(
                "call_record__order__assessment__sales_status_code", flat=True
            ).distinct()
            if code
        }
    )

    return Response(
        {
            "sales": [
                {"id": staff_id, "name": name}
                for staff_id, name in sorted(sales.items(), key=lambda pair: pair[1])
            ],
            "skus": skus,
            "sales_statuses": [
                {"code": code, "label": SALES_STATUS_LABELS.get(code, code)} for code in statuses
            ],
        }
    )


@extend_schema(
    operation_id="call_quality_rubric",
    responses={200: OpenApiResponse(description="Rubric hiện hành")},
    description="Rubric đang dùng — FE hiển thị nhãn tiêu chí mà không hard-code.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def quality_rubric(request):
    from apps.callcenter.qa_rubric import BLOCKS

    return Response(
        {
            "version": RUBRIC_VERSION,
            "pass_score": quality_reports.current_pass_score(),
            "blocks": [
                {
                    "code": block.code,
                    "label": block.label,
                    "weight": block.weight,
                    "criteria": [
                        {"code": c.code, "label": c.label, "guide": c.guide}
                        for c in block.criteria
                    ],
                }
                for block in BLOCKS
            ],
            "red_flags": [{"code": code, "label": label} for code, label in RED_FLAGS.items()],
        }
    )
