"""API máy-tới-máy cấp transcript cuộc gọi cho agent phân tích ngoài (Hermes).

Vì sao KHÔNG dùng JWT như phần còn lại của CRM: bên gọi là một agent chạy nền,
không phải người — nó không có phiên đăng nhập để refresh, và quyền của nó không
nên nằm chung hệ phân quyền nhân sự (thu hồi bằng cách đổi env, không phải đi sửa
role). Đây đúng pattern đang chạy cho landing ingest / webhook đối tác:
``AllowAny`` + token tĩnh so sánh hằng-thời-gian, loại khỏi schema CRM nội bộ.

Phân trang là KEYSET theo ``id`` chứ không phải offset: agent kéo toàn bộ danh
sách theo cụm, mà offset sâu vừa chậm dần vừa nhảy sót/lặp bản ghi khi có cuộc
gọi mới chèn vào giữa lúc đang kéo.
"""

from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.common.masking import mask_keep_last
from apps.common.throttling import HermesApiThrottle
from apps.sysconfig import sysconfig

from apps.callcenter.models import CallRecord, TranscribeStatus

# Transcript nặng ~2-5KB/cuộc: 200 dòng đã là payload cỡ 1MB. Trần cứng để một
# tham số sai bên Hermes không kéo cả bảng vào RAM của web worker.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
# Cửa sổ mặc định — bắt buộc phải có, nếu không lỡ tay gọi không tham số là quét
# toàn bảng.
DEFAULT_WINDOW_DAYS = 7
# Zenify: callType "1" = cuộc gọi ra (sale gọi khách), còn lại là gọi vào.
OUTBOUND_CALL_TYPE = "1"


class ParamError(ValueError):
    """Tham số query sai — trả 400 kèm nguyên văn lý do cho agent tự sửa."""


def _extract_api_token(request) -> str:
    """Đọc token từ ``X-Api-Token`` hoặc ``Authorization: Bearer``.

    Bản sao có chủ đích của helper cùng tên bên orders ingest: giữ callcenter độc
    lập, không kéo import ngang sang module private của app khác.
    """
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _parse_bound(raw: str, field: str, *, end_of_day: bool) -> datetime:
    """Nhận cả ``YYYY-MM-DD`` lẫn ISO datetime, trả về mốc aware theo giờ VN.

    Ngày trần (`to`) nở tới cuối ngày: agent gửi ``to=2026-08-20`` mong có cả cuộc
    gọi lúc 23h hôm đó, cắt ở 00:00 là mất im lặng gần trọn một ngày dữ liệu.

    Phân nhánh theo dấu ngăn giờ chứ KHÔNG thử ``parse_datetime`` trước: từ Django
    5 hàm đó nuốt luôn chuỗi date-only và trả về nửa đêm, nên nhánh nở-cuối-ngày
    sẽ không bao giờ chạy — lỗi im lặng, mất gần trọn ngày dữ liệu cuối cùng.
    """
    text = raw.strip()
    has_time_part = "T" in text or " " in text
    if has_time_part:
        parsed = parse_datetime(text)
    else:
        day = parse_date(text)
        parsed = (
            datetime.combine(day, time.max if end_of_day else time.min)
            if day is not None
            else None
        )
    if parsed is None:
        raise ParamError(f"Tham số `{field}` không phải ngày hợp lệ (YYYY-MM-DD hoặc ISO).")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_int(raw: str, field: str, *, default: int, minimum: int, maximum: int | None) -> int:
    text = str(raw).strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise ParamError(f"Tham số `{field}` phải là số nguyên.") from exc
    if value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _is_truthy(raw: str, *, default: bool) -> bool:
    text = str(raw).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes"}


def _build_filters(params) -> dict:
    """Dịch query params thành tham số truy vấn — tách khỏi view để test thẳng."""
    now = timezone.now()
    raw_from = params.get("from", "")
    raw_to = params.get("to", "")
    started_from = (
        _parse_bound(raw_from, "from", end_of_day=False)
        if raw_from
        else now - timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    started_to = _parse_bound(raw_to, "to", end_of_day=True) if raw_to else now
    return {
        "started_from": started_from,
        "started_to": started_to,
        "cursor": _parse_int(
            params.get("cursor", ""), "cursor", default=0, minimum=0, maximum=None
        ),
        "limit": _parse_int(
            params.get("limit", ""),
            "limit",
            default=DEFAULT_LIMIT,
            minimum=1,
            maximum=MAX_LIMIT,
        ),
        "min_duration": _parse_int(
            params.get("min_duration", ""), "min_duration", default=0, minimum=0, maximum=None
        ),
        "only_transcribed": _is_truthy(params.get("only_transcribed", ""), default=True),
        "include_phone": _is_truthy(params.get("include_phone", ""), default=False),
    }


def _customer_phone(row: dict) -> str:
    """SĐT khách: cuộc gọi ra thì khách ở ``called``, gọi vào thì ở ``caller``."""
    if row["call_type"] == OUTBOUND_CALL_TYPE:
        return row["called"] or row["caller"]
    return row["caller"] or row["called"]


def _serialize(row: dict, *, include_phone: bool) -> dict:
    phone = _customer_phone(row)
    return {
        "call_id": row["call_id"],
        "direction": "outbound" if row["call_type"] == OUTBOUND_CALL_TYPE else "inbound",
        "agent_id": row["agent_id"],
        "agent_name": row["agent_name"],
        "customer_phone": phone if include_phone else mask_keep_last(phone),
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_seconds": row["acd_time"],
        "transcribe_status": row["transcribe_status"],
        "transcript": row["transcript"],
        "order_id": row["order_id"],
        "cursor": row["id"],
    }


@extend_schema(exclude=True)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([HermesApiThrottle])
def hermes_calls(request):
    expected_token = sysconfig.get("HERMES_API_TOKEN")
    if not expected_token:
        # Fail-closed trên prod: thiếu env nghĩa là ai cũng đọc được transcript
        # khách hàng, nên đóng hẳn thay vì bỏ qua bước xác thực.
        if getattr(settings, "IS_PRODUCTION", False):
            return Response(
                {"detail": "Hermes API chưa được cấu hình (thiếu HERMES_API_TOKEN)."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    else:
        provided = _extract_api_token(request)
        # So bytes với bytes: `compare_digest` trên str đòi ASCII thuần, một
        # header có ký tự ngoài ASCII sẽ TypeError → 500 thay vì 401.
        if not provided or not secrets.compare_digest(
            provided.encode("utf-8"), str(expected_token).encode("utf-8")
        ):
            return Response(
                {"detail": "Token không hợp lệ."}, status=status.HTTP_401_UNAUTHORIZED
            )

    try:
        filters = _build_filters(request.query_params)
    except ParamError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    queryset = CallRecord.objects.filter(
        start_time__gte=filters["started_from"],
        start_time__lte=filters["started_to"],
        acd_time__gte=filters["min_duration"],
    )
    if filters["only_transcribed"]:
        queryset = queryset.filter(transcribe_status=TranscribeStatus.DONE).exclude(transcript="")
    if filters["cursor"]:
        queryset = queryset.filter(id__gt=filters["cursor"])

    limit = filters["limit"]
    # +1 dòng thăm dò: biết còn trang sau mà không phải COUNT cả bảng.
    rows = list(
        queryset.order_by("id").values(
            "id",
            "call_id",
            "call_type",
            "agent_id",
            "agent_name",
            "caller",
            "called",
            "start_time",
            "end_time",
            "acd_time",
            "transcribe_status",
            "transcript",
            "order_id",
        )[: limit + 1]
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    return Response(
        {
            "items": [_serialize(row, include_phone=filters["include_phone"]) for row in rows],
            "limit": limit,
            "has_more": has_more,
            "next_cursor": rows[-1]["id"] if rows and has_more else None,
        }
    )
