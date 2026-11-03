"""JSON-RPC client Foxia: envelope, lỗi nấp dưới HTTP 200, kill switch chỉ-đọc."""

from unittest.mock import MagicMock

import pytest

from apps.foxia.exceptions import FoxiaApiError, FoxiaAuthError, FoxiaConfigError
from apps.foxia.infrastructure.foxia_client import MAX_LIMIT, FoxiaClient
from apps.foxia.models import FoxiaAuthStatus, FoxiaConnection


def _connection(**overrides):
    values = {
        "endpoint_url": "https://crm.example.vn/jsonrpc",
        "database": "foxia_prod",
        "username": "integration.marketing",
        "api_key": "secret-key",
        # uid + auth_status OK để execute_kw không phải authenticate lại trong test.
        "uid": 42,
        "auth_status": FoxiaAuthStatus.OK,
    }
    values.update(overrides)
    return FoxiaConnection(**values)


def _response(status_code=200, json_body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = json_body if json_body is not None else {}
    return response


def _client(http):
    return FoxiaClient(_connection(), http=http)


# ── envelope ─────────────────────────────────────────────────────────────────


def test_authenticate_sends_db_user_key_envelope():
    http = MagicMock()
    http.post.return_value = _response(json_body={"result": 42})

    uid = _client(http).authenticate()

    assert uid == 42
    payload = http.post.call_args.kwargs["json"]
    assert payload["jsonrpc"] == "2.0"
    assert payload["params"]["service"] == "common"
    assert payload["params"]["method"] == "authenticate"
    # Tài liệu Foxia cảnh báo riêng: API key KHÔNG thay thế tên database.
    assert payload["params"]["args"][:3] == ["foxia_prod", "integration.marketing", "secret-key"]


def test_execute_kw_sends_db_uid_key_and_resource():
    http = MagicMock()
    http.post.return_value = _response(json_body={"result": []})

    _client(http).search_read("sale.order", fields=["id"], limit=10, order="id asc")

    args = http.post.call_args.kwargs["json"]["params"]["args"]
    assert args[0] == "foxia_prod"
    assert args[1] == 42  # uid đã cache, không authenticate lại
    assert args[2] == "secret-key"
    assert args[3] == "sale.order"
    assert args[4] == "search_read"
    assert args[6] == {"offset": 0, "limit": 10, "fields": ["id"], "order": "id asc"}


# ── lỗi ──────────────────────────────────────────────────────────────────────


def test_http_200_with_error_key_raises():
    """JSON-RPC báo lỗi bằng key `error` kèm status 200.

    Không bắt là caller đọc `result` rỗng rồi tưởng 'không có dữ liệu' — đúng lớp
    lỗi đã cắn ở CNV Loyalty.
    """
    http = MagicMock()
    http.post.return_value = _response(
        json_body={"error": {"data": {"message": "Access Denied"}}}
    )

    with pytest.raises(FoxiaApiError, match="Access Denied"):
        _client(http).search_count("sale.order")


def test_http_error_status_raises():
    http = MagicMock()
    http.post.return_value = _response(status_code=500, text="boom")

    with pytest.raises(FoxiaApiError, match="HTTP 500"):
        _client(http).search_count("sale.order")


def test_non_json_body_raises():
    http = MagicMock()
    response = _response(text="<html>502</html>")
    response.json.side_effect = ValueError("not json")
    http.post.return_value = response

    with pytest.raises(FoxiaApiError, match="không phải JSON"):
        _client(http).search_count("sale.order")


def test_authenticate_false_raises_auth_error():
    """Key bị thu hồi → Foxia trả `false`, không phải lỗi HTTP.

    `False` là bool mà bool là subclass của int, nên thứ tự kiểm tra trong client
    phải bắt `is False` TRƯỚC `isinstance(int)`.
    """
    http = MagicMock()
    http.post.return_value = _response(json_body={"result": False})

    with pytest.raises(FoxiaAuthError):
        _client(http).authenticate()


def test_missing_connection_fields_raises_config_error():
    with pytest.raises(FoxiaConfigError, match="database"):
        FoxiaClient(_connection(database=""), http=MagicMock())


# ── kill switch chỉ-đọc ──────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["create", "write", "unlink", "execute"])
def test_write_methods_are_blocked_before_hitting_network(method):
    http = MagicMock()

    with pytest.raises(FoxiaApiError, match="chỉ-đọc"):
        _client(http).execute_kw("sale.order", method, [[]])

    http.post.assert_not_called()


def test_staging_blocks_outbound(settings):
    settings.IS_STAGING = True
    http = MagicMock()

    with pytest.raises(FoxiaApiError, match="staging"):
        _client(http).authenticate()

    http.post.assert_not_called()


# ── phân trang ───────────────────────────────────────────────────────────────


def test_search_read_rejects_limit_above_cap():
    with pytest.raises(FoxiaApiError, match="vượt trần"):
        _client(MagicMock()).search_read("sale.order", limit=MAX_LIMIT + 1)


def test_iter_all_walks_pages_until_short_page():
    http = MagicMock()
    page1 = [{"id": i} for i in range(1, 4)]
    page2 = [{"id": 4}]
    http.post.side_effect = [
        _response(json_body={"result": 4}),  # search_count
        _response(json_body={"result": page1}),
        _response(json_body={"result": page2}),
    ]

    pages = list(_client(http).iter_all("congtycon", fields=["id", "name"], page_size=3))

    assert pages == [page1, page2]
    offsets = [
        call.kwargs["json"]["params"]["args"][6]["offset"]
        for call in http.post.call_args_list[1:]
    ]
    assert offsets == [0, 3]


def test_iter_all_stops_when_count_is_zero():
    http = MagicMock()
    http.post.return_value = _response(json_body={"result": 0})

    assert list(_client(http).iter_all("congtycon")) == []
    # Chỉ gọi search_count, không gọi search_read nào.
    assert http.post.call_count == 1


def test_iter_all_stops_at_page_cap_when_server_keeps_returning_full_pages():
    """Server trả trang đầy mãi (dữ liệu đang đổi) không được thành vòng lặp vô tận."""
    http = MagicMock()
    full_page = [{"id": i} for i in range(2)]
    http.post.side_effect = [_response(json_body={"result": 2})] + [
        _response(json_body={"result": full_page}) for _ in range(20)
    ]

    pages = list(_client(http).iter_all("congtycon", page_size=2))

    # total=2, page_size=2 → max_pages = 2//2 + 2 = 3
    assert len(pages) == 3
