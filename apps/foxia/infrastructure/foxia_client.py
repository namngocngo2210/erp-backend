"""JSON-RPC client cho Foxia.

Giao thức là Odoo JSON-RPC: `common.authenticate` lấy uid, rồi mọi lệnh đọc đi
qua `object.execute_kw`. Ba điểm bắt buộc phải đúng, sai là hỏng im lặng:

1. **HTTP 200 vẫn có thể là lỗi.** JSON-RPC báo lỗi bằng key `error` trong body
   với status 200. Không kiểm tra là caller đọc `result` rỗng rồi tưởng "không
   có dữ liệu". Đúng lớp lỗi đã cắn ở CNV Loyalty (apis trả 200 + `errors`).
2. **Phải gửi đủ `db` + `uid` + `api_key`.** Tài liệu Foxia cảnh báo riêng rằng
   API key không thay thế tên database — sai `db` không trả 401 mà trả lỗi khác.
3. **Chỉ được đọc.** Tài khoản tích hợp chỉ có quyền đọc, nhưng ta tự chặn ở
   phía mình chứ không tin quyền phía Foxia: allowlist method ở `_ALLOWED_METHODS`.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from apps.common.http_client import HttpClient
from apps.foxia.exceptions import FoxiaApiError, FoxiaAuthError, FoxiaConfigError

logger = logging.getLogger(__name__)

# Kill switch read-only. Mọi method ngoài danh sách này bị chặn TRƯỚC khi ra
# mạng — kể cả khi Foxia lỡ cấp nhầm quyền ghi cho tài khoản tích hợp.
_ALLOWED_METHODS = frozenset({"search_read", "search_count", "fields_get", "read"})

# Trần limit của Foxia (tài liệu §5). Gửi lớn hơn thì Foxia tự cắt, nhưng cắt im
# lặng nghĩa là ta phân trang sai — chặn ở đây để lỗi lộ ra ngay.
MAX_LIMIT = 500


class FoxiaClient:
    """Client cho MỘT FoxiaConnection. Không thread-safe (giữ uid trong bộ nhớ)."""

    def __init__(self, connection, http: HttpClient | None = None):
        self.connection = connection
        self._validate_connection()
        self._http = http or HttpClient(
            timeout=connection.resolve_timeout(),
            default_headers={"Content-Type": "application/json"},
        )
        self._request_id = 0

    def _validate_connection(self) -> None:
        missing = [
            name
            for name in ("endpoint_url", "database", "username", "api_key")
            if not str(getattr(self.connection, name, "") or "").strip()
        ]
        if missing:
            raise FoxiaConfigError(f"FoxiaConnection thiếu: {', '.join(missing)}")

    # ── tầng vận chuyển ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _call(self, service: str, method: str, args: list) -> object:
        # Staging chạy snapshot dữ liệu prod. Foxia là hệ thống của đối tác —
        # không gọi sang bằng dữ liệu/khoá của môi trường thử nghiệm.
        if getattr(settings, "IS_STAGING", False):
            raise FoxiaApiError("Foxia outbound bị chặn trên staging (APP_ENV=staging).")

        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": self._next_id(),
        }
        url = self.connection.resolve_endpoint_url()
        try:
            response = self._http.post(url, json=payload)
        except requests.exceptions.RequestException as exc:
            raise FoxiaApiError(f"Foxia không phản hồi: {exc}") from exc

        if response.status_code >= 400:
            raise FoxiaApiError(
                f"Foxia {service}.{method} HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise FoxiaApiError(
                f"Foxia {service}.{method}: phản hồi không phải JSON: {response.text[:200]}"
            ) from exc

        # Điểm 1 ở docstring module: lỗi nghiệp vụ nấp dưới HTTP 200.
        if isinstance(body, dict) and body.get("error"):
            error = body["error"]
            message = ""
            if isinstance(error, dict):
                data = error.get("data") or {}
                message = data.get("message") or error.get("message") or ""
            raise FoxiaApiError(
                f"Foxia {service}.{method}: {str(message or error)[:300]}",
                status_code=response.status_code,
                payload=body,
            )
        return body.get("result") if isinstance(body, dict) else None

    # ── xác thực ─────────────────────────────────────────────────────────────

    def authenticate(self) -> int:
        """Trả uid. Raise FoxiaAuthError khi Foxia trả false (key bị thu hồi).

        Foxia trả `false` chứ không phải lỗi HTTP, nên phải kiểm tra kiểu trả về:
        `False` là bool, mà `bool` lại là subclass của `int` trong Python — kiểm
        tra `isinstance(result, int)` trước sẽ nuốt mất trường hợp này.
        """
        result = self._call(
            "common",
            "authenticate",
            [self.connection.database, self.connection.username, self.connection.api_key, {}],
        )
        if result is False or result is None or result == 0:
            raise FoxiaAuthError(
                "Foxia từ chối xác thực (API key bị thu hồi hoặc tài khoản ngừng hoạt động)."
            )
        if isinstance(result, bool) or not isinstance(result, int):
            raise FoxiaApiError(f"Foxia authenticate trả giá trị lạ: {result!r}")
        return result

    def _uid(self) -> int:
        """uid đã cache trên connection, chưa có thì authenticate và lưu lại."""
        from apps.foxia.infrastructure.uid_provider import ensure_uid

        return ensure_uid(self.connection, client=self)

    # ── đọc dữ liệu ──────────────────────────────────────────────────────────

    def execute_kw(self, resource: str, method: str, args: list, kwargs: dict | None = None):
        if method not in _ALLOWED_METHODS:
            raise FoxiaApiError(
                f"Method '{method}' không nằm trong danh sách chỉ-đọc {sorted(_ALLOWED_METHODS)}"
            )
        return self._call(
            "object",
            "execute_kw",
            [
                self.connection.database,
                self._uid(),
                self.connection.api_key,
                resource,
                method,
                args,
                kwargs or {},
            ],
        )

    def search_count(self, resource: str, conditions: list | None = None) -> int:
        result = self.execute_kw(resource, "search_count", [conditions or []])
        return int(result or 0)

    def search_read(
        self,
        resource: str,
        conditions: list | None = None,
        fields: list[str] | None = None,
        offset: int = 0,
        limit: int = MAX_LIMIT,
        order: str = "",
    ) -> list[dict]:
        if limit > MAX_LIMIT:
            raise FoxiaApiError(f"limit={limit} vượt trần {MAX_LIMIT} của Foxia")
        options: dict = {"offset": offset, "limit": limit}
        if fields:
            options["fields"] = list(fields)
        if order:
            options["order"] = order
        result = self.execute_kw(resource, "search_read", [conditions or []], options)
        return list(result or [])

    def iter_all(
        self,
        resource: str,
        conditions: list | None = None,
        fields: list[str] | None = None,
        order: str = "id asc",
        page_size: int = MAX_LIMIT,
    ):
        """Duyệt hết bản ghi khớp điều kiện, từng trang một.

        Hai lưới chặn vòng lặp vô hạn: trang rỗng thì dừng, và tổng số trang bị
        chặn trần theo `search_count`. Dữ liệu thay đổi giữa các trang là chuyện
        bình thường với hệ thống đang chạy — nó không được phép biến thành vòng
        lặp bất tận.
        """
        total = self.search_count(resource, conditions)
        if total <= 0:
            return
        max_pages = (total // page_size) + 2
        offset = 0
        pages = 0
        while pages < max_pages:
            rows = self.search_read(
                resource,
                conditions=conditions,
                fields=fields,
                offset=offset,
                limit=page_size,
                order=order,
            )
            if not rows:
                return
            yield rows
            offset += len(rows)
            pages += 1
            if len(rows) < page_size:
                return
        logger.warning(
            "Foxia iter_all(%s): chạm trần %s trang (total=%s) — dừng sớm để tránh lặp vô hạn",
            resource,
            max_pages,
            total,
        )

    def fields_get(self, resource: str, attributes: list[str] | None = None) -> dict:
        result = self.execute_kw(
            resource, "fields_get", [], {"attributes": attributes or ["string", "type", "selection"]}
        )
        return dict(result or {})

    def close(self) -> None:
        self._http.close()
