"""drf-spectacular extensions cho app users (D7).

Đăng ký ``VersionedJWTAuthentication`` với OpenAPI để mọi endpoint khai báo đúng
security scheme Bearer/JWT — nếu không, spectacular phát 1 warning
"could not resolve authenticator" cho TỪNG endpoint.

Module này được import (có guard) trong ``UsersConfig.ready`` để extension được
spectacular phát hiện trước khi sinh schema. Import phải nhẹ và không vỡ khi
drf-spectacular chưa cài (core auth không được phụ thuộc cứng vào nó).
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class VersionedJWTScheme(OpenApiAuthenticationExtension):
    """Map auth class nội bộ sang security scheme HTTP Bearer (JWT)."""

    target_class = "apps.users.jwt.VersionedJWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
