"""Kiểm chứng lớp stream file dưới ASGI — xem apps/common/http_streaming.py.

Điểm cần chốt không phải "tải được file", mà là "KHÔNG nạp trọn file vào RAM":
lỗi cũ vẫn trả về đúng byte, chỉ khác là trả sau khi đã đọc hết 100% file.
"""

import io

import pytest
from asgiref.sync import async_to_sync

from apps.common.http_streaming import aiter_filelike, streaming_file_response


class _CountingBody:
    """File-like đếm số lượt `.read()` — để đo đúng hành vi lazy của stream."""

    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.reads = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        self.reads += 1
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


def _drain(agen) -> bytes:
    async def _collect() -> bytes:
        return b"".join([chunk async for chunk in agen])

    return async_to_sync(_collect)()


def _next_chunk(agen) -> bytes:
    async def _one() -> bytes:
        return await anext(agen)

    return async_to_sync(_one)()


def _aclose(agen) -> None:
    async def _close() -> None:
        await agen.aclose()

    async_to_sync(_close)()


def test_aiter_filelike_yields_chunks_and_closes_body():
    body = _CountingBody(b"abcdefg")

    assert _drain(aiter_filelike(body, chunk_size=3)) == b"abcdefg"
    # 3 lượt có dữ liệu + 1 lượt trả b"" báo hết.
    assert body.reads == 4
    assert body.closed is True


def test_aiter_filelike_does_not_read_whole_file_upfront():
    """Chốt chặn cốt lõi: byte đầu tiên phải tới client sau ĐÚNG MỘT lượt read.

    Nhánh dự phòng của Django (`sync_to_async(list)`) đọc hết file rồi mới gửi —
    test này là thứ phân biệt được hai hành vi đó, còn test "nội dung file đúng"
    thì không.
    """
    body = _CountingBody(b"x" * 1000)
    agen = aiter_filelike(body, chunk_size=100)

    first = _next_chunk(agen)

    assert first == b"x" * 100
    assert body.reads == 1
    _aclose(agen)
    assert body.closed is True


def test_aiter_filelike_closes_body_when_client_disconnects():
    """Client ngắt giữa chừng → ASGI handler gọi aclose() → phải nhả stream MinIO.

    Rò ở đây không làm hỏng response nào cả, nó chỉ giữ connection urllib3 tới
    MinIO cho đến khi GC dọn — kiểu rò chỉ lộ ra lúc tải nhiều file lớn.
    """
    body = _CountingBody(b"x" * 1000)
    agen = aiter_filelike(body, chunk_size=100)
    _next_chunk(agen)

    _aclose(agen)

    assert body.closed is True


def test_streaming_file_response_is_async():
    response = streaming_file_response(io.BytesIO(b"ab"), content_type="text/csv")

    assert response.is_async is True
    assert response["Content-Type"] == "text/csv"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.xlsx", 'attachment; filename="report.xlsx"'),
        # Tên file export sinh ra từ nghiệp vụ nên có dấu tiếng Việt là chuyện
        # thường; RFC 6266 bắt buộc mã hoá phần này, để nguyên là trình duyệt
        # nhận tên rác.
        ("đơn hàng.xlsx", "attachment; filename*=utf-8''%C4%91%C6%A1n%20h%C3%A0ng.xlsx"),
    ],
)
def test_streaming_file_response_content_disposition(filename, expected):
    response = streaming_file_response(
        io.BytesIO(b"ab"), content_type="text/csv", filename=filename, as_attachment=True
    )

    assert response["Content-Disposition"] == expected


def test_streaming_file_response_omits_content_length_when_unknown():
    """`file_size` của job cũ có thể NULL — thiếu Content-Length vẫn tải được,
    còn đặt bừa một con số sai thì trình duyệt cắt file giữa chừng."""
    with_length = streaming_file_response(
        io.BytesIO(b"ab"), content_type="text/csv", content_length=2
    )
    without_length = streaming_file_response(
        io.BytesIO(b"ab"), content_type="text/csv", content_length=None
    )

    assert with_length["Content-Length"] == "2"
    assert "Content-Length" not in without_length
