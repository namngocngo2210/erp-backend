"""`push_customer` — hàm thuần POST một khách vào `{base}/{source_id}`, dùng chung
cho đẩy đơn landing và đẩy lead Pancake. Không raise, không retry, đọc được
`cohoi_id` khi Foxia trả JSON đúng dạng, và cắt phản hồi về RESPONSE_LIMIT."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from apps.foxia.application.services.order_push_service import PushResult, push_customer

PUSH_ON = override_settings(
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang", FOXIA_PUSH_TIMEOUT=10.0
)
MODULE = "apps.foxia.application.services.order_push_service.HttpClient"


def _response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@PUSH_ON
def test_thanh_cong_doc_duoc_cohoi_id():
    with patch(MODULE) as client_cls:
        post = client_cls.return_value.__enter__.return_value.post
        post.return_value = _response(text='{"success": true, "data": {"khachhang_id": 7, "cohoi_id": 99}}')
        result = push_customer(1234, {"name": "A", "phone": "0901234567", "address": ""})

    assert isinstance(result, PushResult)
    assert result.ok is True
    assert (result.url, result.http_status) == ("https://foxia.test/duongdankhachhang/1234", 200)
    assert (result.cohoi_id, result.khachhang_id) == (99, 7)
    assert post.call_args.kwargs["json"] == {"name": "A", "phone": "0901234567", "address": ""}
    # Không retry: POST không idempotent.
    assert client_cls.call_args.kwargs["retries"] == 0


@PUSH_ON
def test_phan_hoi_khong_phai_json_van_ok_nhung_khong_co_cohoi():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(text="<html>ok</html>")
        result = push_customer(1, {})
    assert result.ok is True
    assert result.cohoi_id is None


@PUSH_ON
def test_http_500_la_failed_va_cat_body():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(500, "x" * 5000)
        result = push_customer(1, {})
    assert result.ok is False
    assert result.http_status == 500
    assert len(result.response_body) == 2000


@PUSH_ON
def test_loi_mang_khong_raise():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = requests.ConnectionError("boom")
        result = push_customer(1, {})
    assert result.ok is False
    assert result.http_status is None
    assert result.error.startswith("ConnectionError")


@PUSH_ON
@pytest.mark.parametrize("raw", ["false", "0", "null"])
def test_cohoi_id_false_la_khong_co_co_hoi(raw):
    """07/10/2026: Foxia trả `"cohoi_id": false` khi KHÔNG tạo cơ hội; `int(False) == 0`
    từng lọt thành cohoi_id 0 trông như thành công."""
    body = '{"success": true, "data": {"khachhang_id": 11934, "cohoi_id": ' + raw + "}}"
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(text=body)
        result = push_customer(115, {"name": "A", "phone": "0901234567", "address": ""})

    assert result.ok is True
    assert result.cohoi_id is None
    assert result.khachhang_id == 11934
