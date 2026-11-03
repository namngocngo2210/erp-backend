"""AdsCrmClient: header, params lặp, parse {"error"}, 401 → key_rejected, staging guard."""

from unittest.mock import MagicMock

import pytest
import requests

from apps.media.exceptions import AdsCrmApiError, AdsCrmUnavailable, MediaStagingBlocked
from apps.media.infrastructure.ads_crm_client import AdsCrmClient


def _resp(status=200, body=None, text=""):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.headers = {"Content-Type": "application/json"}
    r.json.return_value = body if body is not None else {}
    return r


def _client(reader=None, writer=None):
    return AdsCrmClient("https://media.example/", "dak_abc", reader=reader or MagicMock(), writer=writer or MagicMock())


def test_get_sends_api_key_request_id_and_repeated_params():
    reader = MagicMock()
    reader.request.return_value = _resp(200, {"items": [], "total": 0})
    status, data = _client(reader=reader).json_request(
        "GET", "/contents", params=[("product_id", "1"), ("product_id", "2")]
    )
    assert (status, data["total"]) == (200, 0)
    kwargs = reader.request.call_args.kwargs
    assert reader.request.call_args.args[:2] == ("GET", "https://media.example/api/v1/contents")
    assert kwargs["headers"]["X-API-Key"] == "dak_abc"
    assert kwargs["headers"]["X-Request-ID"]
    assert kwargs["params"] == [("product_id", "1"), ("product_id", "2")]


def test_mutations_go_through_writer_not_reader():
    reader, writer = MagicMock(), MagicMock()
    writer.request.return_value = _resp(201, {"id": 5})
    status, _ = _client(reader, writer).json_request("POST", "/contents", json={"title": "x"})
    assert status == 201
    reader.request.assert_not_called()
    assert writer.request.call_args.kwargs["json"] == {"title": "x"}


def test_error_body_raises_api_error_with_code_and_status():
    reader = MagicMock()
    reader.request.return_value = _resp(400, {"error": "invalid_url"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).json_request("GET", "/contents/1")
    assert (exc.value.code, exc.value.status) == ("invalid_url", 400)
    assert exc.value.detail == "URL không hợp lệ (cần scheme + host)."


def test_401_invalid_token_becomes_502_key_rejected():
    """Không được trả 401 về frontend — interceptor sẽ logout phiên desi-order."""
    reader = MagicMock()
    reader.request.return_value = _resp(401, {"error": "invalid_token"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).json_request("GET", "/auth/me")
    assert (exc.value.status, exc.value.code) == (502, "media_key_rejected")


def test_network_error_becomes_unavailable():
    reader = MagicMock()
    reader.request.side_effect = requests.ConnectionError("boom")
    with pytest.raises(AdsCrmUnavailable):
        _client(reader).json_request("GET", "/contents")


def test_non_json_error_body_becomes_unavailable():
    reader = MagicMock()
    r = _resp(502, text="<html>bad gateway</html>")
    r.json.side_effect = ValueError("no json")
    reader.request.return_value = r
    with pytest.raises(AdsCrmUnavailable):
        _client(reader).json_request("GET", "/contents")


def test_staging_blocks_before_network(settings):
    settings.IS_STAGING = True
    reader = MagicMock()
    with pytest.raises(MediaStagingBlocked):
        _client(reader).json_request("GET", "/contents")
    reader.request.assert_not_called()


def test_stream_get_passes_stream_and_timeout():
    reader = MagicMock()
    reader.request.return_value = _resp(200)
    _client(reader).stream_get("/contents/1/download", timeout=120)
    kwargs = reader.request.call_args.kwargs
    assert kwargs["stream"] is True and kwargs["timeout"] == 120


def test_stream_media_follows_redirect_and_passes_timeout():
    reader = MagicMock()
    reader.request.return_value = _resp(200)
    _client(reader).stream_media("/contents/1/video", timeout=120)
    kwargs = reader.request.call_args.kwargs
    assert kwargs["stream"] is True and kwargs["timeout"] == 120
    assert kwargs["allow_redirects"] is True
    assert "Range" not in kwargs["headers"]


def test_stream_media_forwards_range_header():
    reader = MagicMock()
    reader.request.return_value = _resp(206)
    _client(reader).stream_media("/contents/1/video", timeout=120, range_header="bytes=1-5")
    kwargs = reader.request.call_args.kwargs
    assert kwargs["headers"]["Range"] == "bytes=1-5"
    assert kwargs["headers"]["X-API-Key"] == "dak_abc"


def test_stream_media_maps_upstream_errors_like_other_calls():
    reader = MagicMock()
    reader.request.return_value = _resp(404, {"error": "content_not_found"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).stream_media("/contents/1/video", timeout=120)
    assert (exc.value.code, exc.value.status) == ("content_not_found", 404)


def test_video_location_returns_302_location_without_following_redirect():
    reader = MagicMock()
    r = _resp(302)
    r.headers = {"Location": "https://r2.example/presigned?sig=abc"}
    reader.request.return_value = r
    status, location = _client(reader).video_location("/contents/1/video")
    assert (status, location) == (302, "https://r2.example/presigned?sig=abc")
    kwargs = reader.request.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert reader.request.call_args.args[:2] == ("GET", "https://media.example/api/v1/contents/1/video")


def test_video_location_200_has_no_location():
    reader = MagicMock()
    r = _resp(200)
    r.headers = {}
    reader.request.return_value = r
    status, location = _client(reader).video_location("/contents/1/video")
    assert (status, location) is not None
    assert (status, location) == (200, None)


def test_video_location_maps_upstream_errors_like_other_calls():
    reader = MagicMock()
    reader.request.return_value = _resp(404, {"error": "content_not_found"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).video_location("/contents/1/video")
    assert (exc.value.code, exc.value.status) == ("content_not_found", 404)
