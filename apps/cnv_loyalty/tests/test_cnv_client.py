"""REST client CNV: header Token, 401 force-refresh 1 lần, kill switch staging."""

from unittest.mock import MagicMock, patch

import pytest

from apps.cnv_loyalty.exceptions import CnvApiError
from apps.cnv_loyalty.infrastructure import cnv_client as cnv_client_module
from apps.cnv_loyalty.infrastructure.cnv_client import CnvLoyaltyClient
from apps.cnv_loyalty.models import CnvConnection


def _connection():
    return CnvConnection(client_id="c", client_secret="s")


def _response(status_code=200, json_body=None, text="", content=b"{}"):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.content = content
    response.json.return_value = json_body if json_body is not None else {}
    return response


@pytest.fixture
def token():
    with patch.object(
        cnv_client_module, "ensure_access_token", return_value=("Bearer", "tok-1")
    ) as mocked:
        yield mocked


def test_sends_token_scheme_header(token):
    http = MagicMock()
    http.request.return_value = _response(json_body={"customers": []})
    client = CnvLoyaltyClient(_connection(), http=http)
    client.list_customers()
    headers = http.request.call_args.kwargs["headers"]
    # Scheme là "Token" theo tài liệu CNV, không phải "Bearer".
    assert headers["Authorization"] == "Token tok-1"


def test_401_forces_refresh_once_then_succeeds(token):
    http = MagicMock()
    http.request.side_effect = [
        _response(status_code=401, text="unauthorized"),
        _response(json_body={"order": {"id": 1}}),
    ]
    client = CnvLoyaltyClient(_connection(), http=http)
    data = client.create_order({"order": {}})
    assert data == {"order": {"id": 1}}
    assert http.request.call_count == 2
    force_calls = [
        call for call in token.call_args_list if call.kwargs.get("force_refresh")
    ]
    assert len(force_calls) == 1


def test_persistent_401_raises(token):
    http = MagicMock()
    http.request.return_value = _response(status_code=401, text="unauthorized")
    client = CnvLoyaltyClient(_connection(), http=http)
    with pytest.raises(CnvApiError, match="401"):
        client.list_orders()


def test_http_error_includes_body(token):
    http = MagicMock()
    http.request.return_value = _response(status_code=422, text="phone required")
    with pytest.raises(CnvApiError, match="phone required"):
        CnvLoyaltyClient(_connection(), http=http).create_customer({})


def test_200_with_errors_body_raises(token):
    """CNV trả lỗi nghiệp vụ dạng HTTP 200 + {"errors": ...} (prod 2026-08-06)."""
    http = MagicMock()
    http.request.return_value = _response(
        json_body={"errors": "Contact email or phone is existed"}
    )
    with pytest.raises(CnvApiError, match="existed"):
        CnvLoyaltyClient(_connection(), http=http).create_customer({})


def test_staging_kill_switch(token, settings):
    settings.IS_STAGING = True
    client = CnvLoyaltyClient(_connection(), http=MagicMock())
    with pytest.raises(RuntimeError, match="staging"):
        client.create_order({})


def test_resource_paths(token):
    http = MagicMock()
    http.request.return_value = _response(json_body={})
    client = CnvLoyaltyClient(_connection(), http=http)
    client.get_customer(5)
    client.update_product(7, {})
    client.get_custom_collection(9)
    client.create_collect(3, 4)
    paths = [call[0][1] for call in http.request.call_args_list]
    assert paths == [
        "/customers/5.json",
        "/products/7.json",
        "/custom_collections/9.json",
        "/collects.json",
    ]
