"""Client Pancake — hợp đồng đã kiểm chứng ở repo desi-crawl-conversations
(docs/pancake-api-verified.md, 2026-06-26). Ba bẫy được chốt ở đây:
1. Sinh token page phải POST kèm Content-Type JSON + body {} (thiếu → 500).
2. `recent_phone_numbers` có 2 dạng phần tử: chuỗi hoặc object {phone_number}.
3. Timestamp Pancake không có múi giờ → UTC.
"""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.pancake.infrastructure.pancake_client import (
    PancakeClient,
    PancakeError,
    PancakePageTokenRejected,
    PancakeTokenError,
    PancakeUnavailable,
    extract_phones,
    parse_pancake_time,
)

MODULE = "apps.pancake.infrastructure.pancake_client.HttpClient"


def _resp(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def test_parse_time_naive_la_utc():
    assert parse_pancake_time("2026-06-14T02:00:56.667139") == datetime(
        2026, 6, 14, 2, 0, 56, 667139, tzinfo=UTC
    )
    assert parse_pancake_time("2026-06-14T02:00:56") == datetime(2026, 6, 14, 2, 0, 56, tzinfo=UTC)
    assert parse_pancake_time("") is None
    assert parse_pancake_time(None) is None
    assert parse_pancake_time("rác") is None


def test_extract_phones_hai_dang():
    assert extract_phones(["0901234567", {"phone_number": "0912345678"}, {"number": "0923456789"}, None, 5]) == [
        "0901234567", "0912345678", "0923456789"
    ]
    assert extract_phones(None) == []
    assert extract_phones("0901234567") == ["0901234567"]


def test_list_pages_chi_lay_activated():
    payload = {
        "success": True,
        "categorized": {
            "activated": [
                {"id": "945997011940587", "name": "DELAX", "platform": "facebook", "is_activated": True,
                 "has_active_subscription": True, "role_in_page": "ADMINISTER"},
            ],
            "hidden": [{"id": "x", "name": "Ẩn", "platform": "facebook"}],
        },
    }
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        pages = PancakeClient("tok").list_pages()

    assert [p.id for p in pages] == ["945997011940587"]
    assert pages[0].role_in_page == "ADMINISTER"
    assert get.call_args.args[0] == "https://pages.fm/api/v1/pages"
    assert get.call_args.kwargs["params"] == {"access_token": "tok"}


def test_list_pages_loi_mang_khong_lo_token():
    """`requests.RequestException` nhét nguyên URL (kèm access_token) vào message —
    PancakeError bọc lại KHÔNG được mang theo `str(exc)` gốc."""
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.side_effect = requests.ConnectionError(
            "https://pages.fm/api/v1/pages?access_token=SECRET"
        )
        with pytest.raises(PancakeError) as excinfo:
            PancakeClient("tok").list_pages()
    assert "SECRET" not in str(excinfo.value)


def test_list_pages_success_false_raise():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp({"success": False, "message": "Invalid token"})
        with pytest.raises(PancakeTokenError, match="Invalid token"):
            PancakeClient("tok").list_pages()


def test_generate_page_token_post_json_body_rong():
    with patch(MODULE) as client_cls:
        post = client_cls.return_value.__enter__.return_value.post
        post.return_value = _resp({"success": True, "page_access_token": "ptok", "message": "Tạo mới token thành công"})
        assert PancakeClient("tok").generate_page_token("945997011940587") == "ptok"

    assert post.call_args.args[0] == "https://pages.fm/api/v1/pages/945997011940587/generate_page_access_token"
    assert post.call_args.kwargs["params"] == {"access_token": "tok"}
    assert post.call_args.kwargs["json"] == {}


def test_generate_page_token_thieu_quyen_raise_message_pancake():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _resp(
            {"success": False, "message": "Thiếu quyền Admin", "error_code": None}
        )
        with pytest.raises(PancakeTokenError, match="Thiếu quyền Admin"):
            PancakeClient("tok").generate_page_token("1")


def test_generate_page_token_http_5xx_la_loi_tam_thoi_khong_phai_token_hong():
    """Sự cố 03/10/2026: 500 bị coi là token hỏng → page bị tắt vĩnh viễn."""
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _resp({"success": False}, status_code=500)
        with pytest.raises(PancakeUnavailable) as exc_info:
            PancakeClient("tok").generate_page_token("1")
    assert not isinstance(exc_info.value, PancakeTokenError)


def test_list_conversations_map_dto():
    payload = {
        "success": True,
        "total": 1,
        "conversations": [
            {
                "id": "945997011940587_27427851286867310",
                "type": "INBOX",
                "inserted_at": "2026-06-14T02:00:56.667139",
                "updated_at": "2026-06-14T02:03:04.381000",
                "has_phone": True,
                "recent_phone_numbers": [{"phone_number": "0901234567"}],
                "tags": [{"id": 46, "text": "BOT-BNC02"}],
                "customers": [{"id": "uuid-1", "name": "Tuan Anh Nguyen"}],
                "customer_id": "uuid-1",
                "current_assign_users": [{"id": "u1", "name": "Nhung HN"}],
            }
        ],
    }
    since = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 15, tzinfo=UTC)
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        page = PancakeClient("tok").list_conversations("945997011940587", "ptok", since, until, 2)

    assert page.total == 1
    conv = page.items[0]
    assert conv.id == "945997011940587_27427851286867310"
    assert conv.has_phone is True
    assert conv.phones == ["0901234567"]
    assert conv.tags == ["BOT-BNC02"]
    assert conv.customer_name == "Tuan Anh Nguyen"
    assert conv.assignee_name == "Nhung HN"
    assert conv.updated_at == datetime(2026, 6, 14, 2, 3, 4, 381000, tzinfo=UTC)
    assert get.call_args.args[0] == "https://pages.fm/api/public_api/v1/pages/945997011940587/conversations"
    assert get.call_args.kwargs["params"] == {
        "page_access_token": "ptok",
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "page_number": 2,
        # `order_by` (KHÔNG phải `sort_by` — Pancake lặng lẽ bỏ qua tham số lạ và trả
        # DESC theo inserted_at, làm poll bỏ sót hội thoại cũ vừa cho số — sự cố 21/09/2026).
        "order_by": "updated_at",
    }


def test_list_conversations_token_bi_tu_choi():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp({"success": False, "message": "Invalid page_access_token"}, 401)
        with pytest.raises(PancakePageTokenRejected):
            PancakeClient("tok").list_conversations("1", "bad", datetime.now(UTC), datetime.now(UTC), 1)


def test_get_conversation_detail_lay_phone_va_tin_nhan_co_sdt_moi_nhat():
    payload = {
        "success": True,
        "customers": [{"id": "uuid-1", "name": "Tuan Anh", "recent_phone_numbers": ["0901234567", "0912345678"]}],
        "messages": [
            {"id": "m1", "inserted_at": "2026-06-14T02:00:49.000000", "has_phone": False},
            {"id": "m2", "inserted_at": "2026-06-14T02:01:10.000000", "has_phone": True},
            {"id": "m3", "inserted_at": "2026-06-14T02:02:00.000000", "has_phone": True},
            {"id": "m4", "inserted_at": "2026-06-14T02:03:00.000000", "has_phone": False},
        ],
    }
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")

    assert detail.phones == ["0901234567", "0912345678"]
    assert detail.customer_name == "Tuan Anh"
    assert detail.phone_message_at == datetime(2026, 6, 14, 2, 2, 0, tzinfo=UTC)
    assert get.call_args.args[0] == "https://pages.fm/api/public_api/v1/pages/1/conversations/1_2/messages"
    assert get.call_args.kwargs["params"] == {"page_access_token": "ptok", "current_count": 0}


def test_get_conversation_detail_khong_tin_nao_co_sdt():
    payload = {"success": True, "customers": [], "messages": [{"id": "m1", "inserted_at": "2026-06-14T02:00:49", "has_phone": False}]}
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")
    assert detail.phones == []
    assert detail.phone_message_at is None


def test_get_conversation_detail_gop_sdt_tu_ho_so_khach_don_pos_va_conv_phone_numbers():
    """Số nhân viên gắn qua đơn Pancake POS nằm ở hồ sơ khách dạng {order_id, phone_number}
    (không bật has_phone, không có tin nhắn nào chứa số); ngoài ra endpoint còn trả
    `recent_phone_numbers`/`conv_phone_numbers` ở cấp trên cùng. Gộp cả ba, khử trùng,
    giữ thứ tự (đo bằng token thật 17/09/2026)."""
    payload = {
        "success": True,
        "customers": [{"id": "uuid-1", "name": "Đoàn Cường",
                       "recent_phone_numbers": [{"order_id": 108000001, "phone_number": "0978000001"}]}],
        "recent_phone_numbers": ["0978000001", "0912000002"],
        "conv_phone_numbers": ["0912000002", {"phone_number": "0903000003"}],
        "messages": [{"id": "m1", "inserted_at": "2026-09-17T02:00:00", "has_phone": False}],
    }
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")
    assert detail.phones == ["0978000001", "0912000002", "0903000003"]
    assert detail.phone_message_at is None
