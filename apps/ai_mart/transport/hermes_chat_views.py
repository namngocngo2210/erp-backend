"""API máy-tới-máy cấp hội thoại chat (Pancake) cho agent phân tích ngoài (Hermes).

Vì sao mở HTTP thay vì để agent trỏ thẳng DB: role DB chỉ-đọc dùng chung là
một cửa quá rộng (thấy cả schema public chưa che PII), không giới hạn được
hình dạng truy vấn, và thu hồi phải đụng tới Postgres. Cửa HTTP này chỉ đọc hai
bảng mart ``ai.hoi_thoai`` / ``ai.tin_nhan`` — đã che SĐT ở mọi cột trừ nội
dung thô — và thu hồi bằng cách đổi env, y như kênh transcript cuộc gọi.

Vì sao KHÔNG dùng JWT như phần còn lại của CRM: bên gọi là agent chạy nền, không
có phiên đăng nhập để refresh, và quyền của nó không nên nằm chung hệ phân quyền
nhân sự. Đúng pattern ``AllowAny`` + token tĩnh so sánh hằng-thời-gian đang chạy
cho ``/api/callcenter/hermes/calls`` — helper token/ngày/số là bản sao CÓ CHỦ
ĐÍCH, không import ngang module private của app khác.

Phân trang KEYSET, không offset: agent kéo toàn bộ theo cụm, offset sâu vừa
chậm dần vừa nhảy sót/lặp khi ETL 30 phút chèn dòng mới giữa lúc đang kéo.
Danh sách hội thoại keyset theo ``hoi_thoai_id``; tin nhắn keyset theo
``(gui_luc, tin_nhan_id)`` vì id nguồn KHÔNG đảm bảo tăng theo thời gian (crawler
backfill lịch sử) — sắp theo id trần sẽ trả tin lộn thứ tự.

Bảng ``ai.*`` không có Django model (DDL nằm trong migration RunSQL), nên đọc
bằng SQL tham số hoá qua ``connection.cursor()``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import connection
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

from apps.common.throttling import HermesApiThrottle
from apps.sysconfig import sysconfig

# Một hội thoại ~1-3KB metadata (chưa tính noi_dung); 200 dòng là cỡ vài trăm KB.
# Trần cứng để tham số sai bên Hermes không kéo cả bảng ~100k dòng vào RAM web.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
# Tin nhắn nhẹ hơn (1 dòng/tin) nhưng một hội thoại có thể vài trăm tin.
MSG_MAX_LIMIT = 500
MSG_DEFAULT_LIMIT = 200
# Cửa sổ mặc định bắt buộc: gọi không tham số mà quét toàn bảng là tự bắn chân.
DEFAULT_WINDOW_DAYS = 7


class ParamError(ValueError):
    """Tham số query sai — trả 400 kèm nguyên văn lý do cho agent tự sửa."""


# ─── Cửa token (bản sao có chủ đích của callcenter/hermes) ──────────────────


def _extract_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _token_gate(request) -> Response | None:
    """None = qua cửa; Response = chặn (401/503). Fail-closed trên prod."""
    expected = sysconfig.get("HERMES_API_TOKEN")
    if not expected:
        if getattr(settings, "IS_PRODUCTION", False):
            return Response(
                {"detail": "Hermes API chưa được cấu hình (thiếu HERMES_API_TOKEN)."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return None
    provided = _extract_api_token(request)
    # .encode(): compare_digest với str non-ASCII ném TypeError → 500 thay vì 401.
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        return Response({"detail": "Token không hợp lệ."}, status=status.HTTP_401_UNAUTHORIZED)
    return None


def hermes_view(view_func):
    """GET + không auth CRM + AllowAny + throttle scope hermes + loại khỏi schema."""
    return extend_schema(exclude=True)(
        api_view(["GET"])(
            authentication_classes([])(
                permission_classes([AllowAny])(throttle_classes([HermesApiThrottle])(view_func))
            )
        )
    )


# ─── Đọc tham số ────────────────────────────────────────────────────────────


def _parse_bound(raw: str, field: str, *, end_of_day: bool) -> datetime:
    """``YYYY-MM-DD`` hoặc ISO datetime → mốc aware giờ VN. Ngày trần nở tới cuối ngày.

    Phân nhánh theo dấu ngăn giờ chứ KHÔNG thử ``parse_datetime`` trước: từ Django 5
    hàm đó nuốt luôn chuỗi date-only và trả nửa đêm, nhánh nở-cuối-ngày sẽ không
    bao giờ chạy — mất im lặng gần trọn ngày dữ liệu cuối.
    """
    text = raw.strip()
    if "T" in text or " " in text:
        parsed = parse_datetime(text)
    else:
        day = parse_date(text)
        parsed = (
            datetime.combine(day, time.max if end_of_day else time.min) if day is not None else None
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


def _build_conversation_filters(params) -> dict:
    """Dịch query params thành tham số truy vấn — tách khỏi view để test thẳng."""
    now = timezone.now()
    raw_from = params.get("from", "")
    raw_to = params.get("to", "")
    return {
        "ended_from": (
            _parse_bound(raw_from, "from", end_of_day=False)
            if raw_from
            else now - timedelta(days=DEFAULT_WINDOW_DAYS)
        ),
        "ended_to": _parse_bound(raw_to, "to", end_of_day=True) if raw_to else now,
        "platform": (params.get("platform") or "").strip(),
        "channel_type": (params.get("channel_type") or "").strip(),
        "page": (params.get("page") or "").strip(),
        "exclude_bot": _is_truthy(params.get("exclude_bot", ""), default=True),
        "only_replied": _is_truthy(params.get("only_replied", ""), default=False),
        "min_customer_messages": _parse_int(
            params.get("min_customer_messages", ""),
            "min_customer_messages",
            default=0,
            minimum=0,
            maximum=None,
        ),
        "include_content": _is_truthy(params.get("include_content", ""), default=False),
        "cursor": _parse_int(params.get("cursor", ""), "cursor", default=0, minimum=0, maximum=None),
        "limit": _parse_int(
            params.get("limit", ""), "limit", default=DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT
        ),
    }


def _build_message_filters(params) -> dict:
    return {
        "cursor": _parse_int(params.get("cursor", ""), "cursor", default=0, minimum=0, maximum=None),
        "limit": _parse_int(
            params.get("limit", ""),
            "limit",
            default=MSG_DEFAULT_LIMIT,
            minimum=1,
            maximum=MSG_MAX_LIMIT,
        ),
    }


# ─── Truy vấn ───────────────────────────────────────────────────────────────

_CONVERSATION_COLUMNS = (
    "hoi_thoai_id",
    "pancake_hoi_thoai_id",
    "page_ten",
    "nen_tang_text",
    "loai_kenh_text",
    "bat_dau",
    "ket_thuc",
    "so_tin",
    "so_tin_khach",
    "so_tin_page",
    "co_khach_tra_loi",
    "do_bot_xu_ly",
    "nhan_tag",
    "khach_ten",
    "sdt_che",
    "tom_tat_ai",
    "nhu_cau_ai_text",
    "ly_do_khong_mua_text",
    "san_pham_quan_tam",
    "giai_doan_text",
    "muc_do_quan_tam_text",
    "trang_thai_gan_nhan_text",
    "khach_id",
    "don_id",
    "ma_don",
    "dong_bo_luc",
)


def _fetch_dicts(sql: str, params: list) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        names = [col[0] for col in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _query_conversations(f: dict) -> list[dict]:
    """Trả về tối đa limit+1 dòng (dòng thăm dò để biết còn trang sau, không COUNT)."""
    columns = list(_CONVERSATION_COLUMNS)
    if f["include_content"]:
        columns.append("noi_dung")
    where = ["ket_thuc >= %s", "ket_thuc <= %s"]
    params: list = [f["ended_from"], f["ended_to"]]
    if f["platform"]:
        where.append("lower(nen_tang_text) = lower(%s)")
        params.append(f["platform"])
    if f["channel_type"]:
        where.append("lower(loai_kenh_text) = lower(%s)")
        params.append(f["channel_type"])
    if f["page"]:
        where.append("page_ten = %s")
        params.append(f["page"])
    if f["exclude_bot"]:
        # coalesce: hội thoại chưa gắn thẻ nào (NULL) là của người, không phải bot.
        where.append("NOT coalesce(do_bot_xu_ly, false)")
    if f["only_replied"]:
        where.append("coalesce(co_khach_tra_loi, false)")
    if f["min_customer_messages"]:
        where.append("coalesce(so_tin_khach, 0) >= %s")
        params.append(f["min_customer_messages"])
    if f["cursor"]:
        where.append("hoi_thoai_id > %s")
        params.append(f["cursor"])
    params.append(f["limit"] + 1)
    sql = (
        f"SELECT {', '.join(columns)} FROM ai.hoi_thoai"
        f" WHERE {' AND '.join(where)} ORDER BY hoi_thoai_id LIMIT %s"
    )
    return _fetch_dicts(sql, params)


def _conversation_exists(conversation_id: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [conversation_id])
        return cur.fetchone() is not None


def _query_messages(conversation_id: int, f: dict) -> list[dict]:
    where = ["hoi_thoai_id = %s"]
    params: list = [conversation_id]
    if f["cursor"]:
        # Keyset theo (gui_luc, tin_nhan_id): tra mốc của dòng cursor rồi so sánh
        # bộ đôi — Postgres so row-wise nên không cần nhận thêm tham số thời gian.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT gui_luc FROM ai.tin_nhan WHERE tin_nhan_id = %s AND hoi_thoai_id = %s",
                [f["cursor"], conversation_id],
            )
            anchor = cur.fetchone()
        if anchor is None:
            raise ParamError("Tham số `cursor` không trỏ tới tin nào của hội thoại này.")
        where.append("(gui_luc, tin_nhan_id) > (%s, %s)")
        params.extend([anchor[0], f["cursor"]])
    params.append(f["limit"] + 1)
    sql = (
        "SELECT tin_nhan_id, gui_luc, chieu_text, loai_tin_text, noi_dung, co_dinh_kem"
        f" FROM ai.tin_nhan WHERE {' AND '.join(where)}"
        " ORDER BY gui_luc, tin_nhan_id LIMIT %s"
    )
    return _fetch_dicts(sql, params)


# ─── Serialize ──────────────────────────────────────────────────────────────


def _serialize_conversation(row: dict, *, include_content: bool) -> dict:
    item = {
        "id": row["hoi_thoai_id"],
        "pancake_id": row["pancake_hoi_thoai_id"],
        "page": row["page_ten"],
        "platform": row["nen_tang_text"],
        "channel_type": row["loai_kenh_text"],
        "started_at": row["bat_dau"],
        "ended_at": row["ket_thuc"],
        "message_count": row["so_tin"],
        "customer_message_count": row["so_tin_khach"],
        "page_message_count": row["so_tin_page"],
        "customer_replied": bool(row["co_khach_tra_loi"]),
        "bot_handled": bool(row["do_bot_xu_ly"]),
        "tags": row["nhan_tag"] or [],
        "customer_name": row["khach_ten"],
        "customer_phone_masked": row["sdt_che"],
        "ai": {
            "summary": row["tom_tat_ai"],
            "needs": row["nhu_cau_ai_text"],
            "reasons_not_buying": row["ly_do_khong_mua_text"],
            "products_of_interest": row["san_pham_quan_tam"] or [],
            "stage": row["giai_doan_text"],
            "interest_level": row["muc_do_quan_tam_text"],
            "label_status": row["trang_thai_gan_nhan_text"],
        },
        "customer_id": row["khach_id"],
        "order_id": row["don_id"],
        "order_code": row["ma_don"],
        "synced_at": row["dong_bo_luc"],
        "cursor": row["hoi_thoai_id"],
    }
    if include_content:
        item["content"] = row["noi_dung"]
    return item


def _serialize_message(row: dict) -> dict:
    return {
        "id": row["tin_nhan_id"],
        "sent_at": row["gui_luc"],
        "direction": row["chieu_text"],
        "type": row["loai_tin_text"],
        "content": row["noi_dung"],
        "has_attachment": bool(row["co_dinh_kem"]),
        "cursor": row["tin_nhan_id"],
    }


def _page(items: list[dict], limit: int) -> dict:
    """``items`` đã serialize, dài tối đa limit+1 (dòng thăm dò) — cắt và gắn cursor."""
    has_more = len(items) > limit
    items = items[:limit]
    return {
        "items": items,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": items[-1]["cursor"] if items and has_more else None,
    }


# ─── Views ──────────────────────────────────────────────────────────────────


@hermes_view
def hermes_conversations(request):
    blocked = _token_gate(request)
    if blocked is not None:
        return blocked
    try:
        filters = _build_conversation_filters(request.query_params)
    except ParamError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    rows = _query_conversations(filters)
    include_content = filters["include_content"]
    items = [_serialize_conversation(r, include_content=include_content) for r in rows]
    return Response(_page(items, filters["limit"]))


@hermes_view
def hermes_conversation_messages(request, conversation_id: int):
    blocked = _token_gate(request)
    if blocked is not None:
        return blocked
    if not _conversation_exists(conversation_id):
        return Response({"detail": "Không có hội thoại này."}, status=status.HTTP_404_NOT_FOUND)
    try:
        filters = _build_message_filters(request.query_params)
        rows = _query_messages(conversation_id, filters)
    except ParamError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    body = _page([_serialize_message(r) for r in rows], filters["limit"])
    body["conversation_id"] = conversation_id
    return Response(body)
