"""Guard rails cho truy vấn DB nặng (báo cáo/precompute).

`statement_timeout`: áp giới hạn thời gian mỗi câu lệnh SQL lên session hiện tại rồi
khôi phục. Dùng cho query báo cáo full-history: khi Celery hard-kill task, query
Postgres mồ côi VẪN chạy tiếp (mặc định `client_connection_check_interval=0` nên
Postgres không phát hiện client chết giữa một query dài), gây chồng đống + cạn
connection. Có timeout thì query tự bị hủy ở tầng DB, bất kể Celery.

`single_flight`: khóa Redis chống chạy chồng (một job precompute chưa xong mà chu kỳ
beat sau đã khởi động → nhiều instance cùng thrash work_mem).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from django.db import connection


@contextmanager
def statement_timeout(seconds: int):
    """Set statement_timeout (giây) cho session DB hiện tại; RESET khi thoát.

    Dùng ``set_config(..., is_local=false)`` để giá trị áp cho cả session (sống qua
    các transaction bên trong khối), không phải chỉ transaction hiện tại.

    ``seconds <= 0`` = tắt (no-op) — KHÔNG áp timeout, tránh vô tình đặt 1ms giết mọi query.
    """
    if seconds is None or seconds <= 0:
        yield
        return
    ms = int(seconds * 1000)
    with connection.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        previous = cur.fetchone()[0]
        cur.execute("SELECT set_config('statement_timeout', %s, false)", [str(ms)])
    try:
        yield
    finally:
        # Khôi phục giá trị TRƯỚC khi vào guard (vd. default toàn cục từ DATABASES
        # OPTIONS), không hardcode '0' — session dùng lại qua CONN_MAX_AGE nên set cứng
        # '0' sẽ vô tình tắt timeout toàn cục cho phần đời còn lại của connection.
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, false)", [previous])


@contextmanager
def single_flight(lock_key: str, ttl_seconds: int):
    """Khóa Redis kiểu single-flight. Yield True nếu giành được khóa (được chạy),
    False nếu một tiến trình khác đang giữ (bỏ qua lượt này).

    TTL là "van an toàn": nếu tiến trình giữ khóa chết bất thường, khóa tự hết hạn
    sau ttl_seconds để chu kỳ sau chạy lại. Đặt ttl > time_limit của task.

    Owner token: giá trị lưu trong khoá là 1 token ngẫu nhiên riêng cho lần acquire
    này, không phải "1" cố định. Nếu không có token, tiến trình A chạy lâu hơn TTL
    (vượt "van an toàn") rồi mới tới bước release sẽ xoá NHẦM khoá mà tiến trình B
    đã acquire lại sau khi TTL của A hết hạn — mở cửa cho tiến trình C chạy chồng
    ngay khi B vẫn đang chạy. Chỉ tiến trình đang giữ token khớp mới được xoá.
    """
    from django.core.cache import cache

    token = uuid.uuid4().hex
    acquired = cache.add(lock_key, token, ttl_seconds)
    try:
        yield acquired
    finally:
        if acquired and cache.get(lock_key) == token:
            cache.delete(lock_key)
