"""Trả file từ storage về client mà không nạp trọn file vào RAM — dưới ASGI.

Prod chạy ``gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker``
nên MỌI response đi đường async. Khi ``StreamingHttpResponse``/``FileResponse``
nhận một iterator ĐỒNG BỘ, Django không stream được mà rơi vào nhánh dự phòng
của ``StreamingHttpResponse.__aiter__``::

    warnings.warn("StreamingHttpResponse must consume synchronous iterators ...")
    for part in await sync_to_async(list)(self.streaming_content):
        yield part

``list(...)`` dựng TOÀN BỘ file thành list chunk trong RAM rồi mới gửi byte đầu
tiên: mất sạch lợi ích của stream (RAM đội theo kích thước file, byte đầu chỉ
tới trình duyệt sau khi đã đọc xong 100% file từ MinIO), trong khi mọi test và
mọi lượt tải thủ công vẫn "chạy đúng".

Cảnh báo kia rất dễ trôi: bộ lọc mặc định của ``warnings`` chỉ in MỘT lần cho
mỗi process, lẫn giữa log truy cập. Chốt chặn hồi quy thật sự là test khẳng định
``response.is_async is True`` — client test của Django chạy đường sync nên test
"tải file thành công" không bao giờ bắt được lớp lỗi này.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header

# 64KB: đủ lớn để không tạo hàng nghìn lượt read cho file vài chục MB, đủ nhỏ để
# không dựng lại nguyên file trong RAM — chính là thứ module này sinh ra để tránh.
DEFAULT_CHUNK_SIZE = 64 * 1024


async def aiter_filelike(body, chunk_size: int = DEFAULT_CHUNK_SIZE):
    """Biến file-like ĐỒNG BỘ (``obj["Body"]`` của boto3, ``BytesIO``...) thành
    async iterator để Django stream thật sự.

    ``sync_to_async`` giữ nguyên mặc định ``thread_sensitive=True``: Django bọc
    mỗi request ASGI trong một ``ThreadSensitiveContext`` RIÊNG, nên từng lượt
    ``read()`` quay về đúng cái thread mà view vừa chạy — không sinh thêm thread,
    không mượn pool dùng chung, và không có chuyện các request tải file xếp hàng
    lên nhau (xem ``apps.common.asgi_concurrency`` về mô hình thread hiện tại).
    """
    read = sync_to_async(body.read)
    try:
        while chunk := await read(chunk_size):
            yield chunk
    finally:
        # Đóng ĐỒNG BỘ, không await: khi client ngắt giữa chừng, ASGI handler gọi
        # ``aclose()`` (nó bọc iterator trong ``aclosing()``) và generator đang xử
        # lý GeneratorExit — await ở nhánh đó là chỗ dễ sinh lỗi khó tái hiện.
        # ``.close()`` của boto3 chỉ đóng stream urllib3 bên dưới nên rẻ.
        close = getattr(body, "close", None)
        if close is not None:
            close()


def streaming_file_response(
    body,
    *,
    content_type: str,
    filename: str = "",
    as_attachment: bool = False,
    content_length: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> StreamingHttpResponse:
    """``FileResponse`` cho đường async.

    Không dùng lại được ``FileResponse`` thật: nó chỉ gọi ``set_headers()`` ở
    nhánh ``hasattr(value, "read")``, mà async generator thì không có ``.read``
    — đưa vào là im lặng mất ``Content-Disposition`` (file tải về mất tên) lẫn
    ``Content-Type``. Nên header dựng tay ở đây, riêng tên file vẫn đi qua
    ``content_disposition_header`` của Django để tên tiếng Việt có dấu được mã
    hoá theo RFC 6266 y hệt trước.
    """
    response = StreamingHttpResponse(
        aiter_filelike(body, chunk_size), content_type=content_type
    )
    if disposition := content_disposition_header(as_attachment, filename):
        response.headers["Content-Disposition"] = disposition
    if content_length is not None:
        response.headers["Content-Length"] = str(content_length)
    return response
