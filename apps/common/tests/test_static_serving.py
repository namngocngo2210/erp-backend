"""Admin/static phải được chính Django phục vụ khi DEBUG=0.

Bối cảnh 2026-09-03: nginx prod trên HOST proxy mọi path về web:8000 và không
có `location /static/`; Django với DEBUG=0 cũng không tự serve /static/ → trang
Django admin hiện ra không CSS. Cách sửa chọn WhiteNoise (middleware trong app)
để không phụ thuộc cấu hình nginx ngoài repo.

`WHITENOISE_USE_FINDERS` + `AUTOREFRESH` chỉ để test đọc thẳng từ finders mà
không cần `collectstatic` trước; prod đọc từ STATIC_ROOT như bình thường.
"""

import pytest
from django.test import Client, override_settings


@override_settings(
    DEBUG=False,
    WHITENOISE_USE_FINDERS=True,
    WHITENOISE_AUTOREFRESH=True,
)
def test_admin_static_served_without_debug():
    resp = Client().get("/static/admin/css/base.css")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/css")


def test_whitenoise_middleware_sits_right_after_security():
    """Vị trí trong chuỗi middleware là điều WhiteNoise yêu cầu: đứng trước mọi
    middleware khác trừ SecurityMiddleware, để request static không đi qua
    session/csrf/audit."""
    from django.conf import settings

    mw = settings.MIDDLEWARE
    sec = mw.index("django.middleware.security.SecurityMiddleware")
    assert mw[sec + 1] == "whitenoise.middleware.WhiteNoiseMiddleware"
