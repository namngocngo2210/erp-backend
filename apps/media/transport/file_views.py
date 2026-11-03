"""Tải video qua proxy: stream từ ads-crm về trình duyệt, không đệm vào RAM (spec D9).

Prod chạy ASGI (`gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker`);
xem `apps/common/http_streaming.py` — iterator đồng bộ (`upstream.iter_content(...)`)
bị Django gom hết vào RAM trước khi gửi byte đầu tiên, mất sạch lợi ích của stream.
Nên dùng `aiter_filelike(upstream.raw, ...)`: cần bật `decode_content = True` để
`requests` tự giải nén gzip/deflate trên `.raw` (mặc định `.raw.read()` trả bytes
nén thô). `aiter_filelike` tự đóng `upstream.raw` khi xong/hủy — tương đương đóng
kết nối tới ads-crm, không cần đóng thêm.
"""

from __future__ import annotations

from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import status as http
from rest_framework.decorators import authentication_classes

from apps.common.http_streaming import aiter_filelike
from apps.media.application.services.access_policy import authorize
from apps.media.domain.roles import Action
from apps.media.transport.api_views import _auth, _new_proxy, _passthrough
from apps.media.transport.stream_auth import RefreshCookieAuthentication
from apps.users.jwt import VersionedJWTAuthentication

_FORWARD_HEADERS = ("Content-Disposition", "Content-Length")
_STREAM_FORWARD_HEADERS = ("Content-Length", "Content-Range", "Accept-Ranges")
_CHUNK = 64 * 1024


@_auth(["GET"])
def content_download(request, content_id: int):
    """Stream file (ảnh/video) của một content về client, giữ nguyên header gốc."""
    proxy = _new_proxy(request)
    authorize(proxy.cred, Action.READ)
    timeout = float(getattr(settings, "MEDIA_DOWNLOAD_TIMEOUT", 120.0))
    upstream = proxy.client.stream_get(f"/contents/{content_id}/download", timeout=timeout)
    upstream.raw.decode_content = True
    response = StreamingHttpResponse(
        aiter_filelike(upstream.raw, chunk_size=_CHUNK),
        content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
    )
    for name in _FORWARD_HEADERS:
        if not (value := upstream.headers.get(name)):
            continue
        # `decode_content = True` ở trên khiến ta đọc bytes ĐÃ giải nén, nhưng
        # Content-Length upstream trả là kích thước bytes NÉN gốc (trước gzip/
        # deflate) — copy nguyên văn sẽ sai, trình duyệt cắt cụt hoặc treo tải.
        # Chỉ forward khi upstream không nén.
        if name == "Content-Length" and upstream.headers.get("Content-Encoding"):
            continue
        response[name] = value
    return response


@_auth(["GET"])
def content_video_url(request, content_id: int):
    """Trả URL phát video trực tiếp thay vì buộc trình duyệt tải qua proxy Django.

    ads-crm trả 302 + Location (URL R2 ký sẵn, ~15 phút, hỗ trợ Range/seek) →
    forward thẳng URL đó cho frontend gắn vào thẻ <video>. Cấu hình fallback trên
    ads-crm có thể trả 200 và tự stream — khi đó không có Location, báo
    `mode: "proxy"` để frontend quay lại endpoint /download (stream qua Django)."""
    proxy = _new_proxy(request)
    authorize(proxy.cred, Action.READ)
    status_code, location = proxy.client.video_location(f"/contents/{content_id}/video")
    if status_code == 302 and location:
        return _passthrough(http.HTTP_200_OK, {"url": location, "mode": "redirect"})
    return _passthrough(http.HTTP_200_OK, {"url": None, "mode": "proxy"})


@_auth(["GET"])
@authentication_classes([VersionedJWTAuthentication, RefreshCookieAuthentication])
def content_video_stream(request, content_id: int):
    """Stream video qua chính ERP (same-origin) — đường lùi khi trình duyệt của
    người xem không tới được `*.r2.cloudflarestorage.com` (mạng chặn/timeout) dù
    server tới được. Khác `content_video_url`: ở đây CHỦ ĐỘNG theo redirect tới
    R2 rồi bơm bytes về, thay vì trả Location cho <video src> tự nối tới R2.
    Forward Range để trình duyệt tua được — xem `AdsCrmClient.stream_media`.

    Riêng endpoint này CHẤP NHẬN THÊM cookie phiên `refresh_token` (không chỉ
    Bearer) vì thẻ <video src=…> không gắn được header Authorization — xem
    `RefreshCookieAuthentication` để biết vì sao an toàn (SameSite=Lax chặn
    cookie theo request nhúng cross-site)."""
    proxy = _new_proxy(request)
    authorize(proxy.cred, Action.READ)
    timeout = float(getattr(settings, "MEDIA_DOWNLOAD_TIMEOUT", 120.0))
    range_header = request.META.get("HTTP_RANGE")
    upstream = proxy.client.stream_media(
        f"/contents/{content_id}/video", timeout=timeout, range_header=range_header
    )
    upstream.raw.decode_content = True
    response = StreamingHttpResponse(
        aiter_filelike(upstream.raw, chunk_size=_CHUNK),
        content_type=upstream.headers.get("Content-Type", "video/mp4"),
        status=upstream.status_code,
    )
    for name in _STREAM_FORWARD_HEADERS:
        if not (value := upstream.headers.get(name)):
            continue
        # Cùng bẫy như content_download: decode_content=True trả bytes đã giải
        # nén, Content-Length upstream lại là kích thước nén gốc — sai nếu forward.
        if name == "Content-Length" and upstream.headers.get("Content-Encoding"):
            continue
        response[name] = value
    response["Cache-Control"] = "private, max-age=3600"
    return response


@_auth(["POST"])
def content_video_cache(request, content_id: int):
    """Yêu cầu ads-crm dựng cache video; `force=1/true` chỉ dành cho vai trò admin."""
    proxy = _new_proxy(request)
    force = request.query_params.get("force") in ("1", "true")
    params = None
    if force:
        authorize(proxy.cred, Action.VIDEO_CACHE_FORCE)
        params = [("force", "1")]
    return _passthrough(
        *proxy.forward("POST", f"/contents/{content_id}/video/cache", action=Action.READ, params=params)
    )
