"""Shared HTTP client with sane timeout + retry defaults.

External integrations (Lark, KiotViet, Facebook) each used to build their
own ``requests`` calls with inconsistent timeouts and ad-hoc retry logic. This
module centralises that plumbing so every outbound call gets:

- a default connect/read timeout (never block forever),
- automatic retry with exponential backoff on transient errors (429, 5xx),
- a reusable connection-pooled session.

Usage::

    client = HttpClient(base_url="https://open.larksuite.com", timeout=10)
    resp = client.post("/open-apis/bot/v2/hook/xxx", json=payload)
    resp.raise_for_status()

Or as a context manager to release the pool::

    with HttpClient(timeout=5) as client:
        client.get("https://example.com/api")

Retry safety: retry is configured PER CLIENT, not per call. Automatic retry is
only safe for read requests — a POST mutation that times out may have been
applied upstream (KShip create-order: timeout nhưng vận đơn VẪN tạo thật, retry
tự động = vận đơn thứ hai). For mutations, build a client with ``retries=0``:
it keeps the timeout default + connection pooling but never re-sends, and
preserves ``requests``' native exception types (ReadTimeout stays ReadTimeout
instead of being wrapped into ConnectionError by urllib3's Retry machinery).
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.5
RETRY_STATUSES = (429, 500, 502, 503, 504)


class HttpClient:
    """Thin wrapper around ``requests.Session`` with timeout + retry baked in."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF,
        default_headers: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        if default_headers:
            self._session.headers.update(default_headers)
        if retries > 0:
            retry: Retry | int = Retry(
                total=retries,
                connect=retries,
                read=retries,
                status=retries,
                backoff_factor=backoff_factor,
                status_forcelist=RETRY_STATUSES,
                allowed_methods=frozenset(
                    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                ),
                raise_on_status=False,
            )
        else:
            # retries=0: no retry at all, keep requests' default exception
            # semantics (Retry(0, read=False) — read timeouts re-raise as
            # ReadTimeout, not MaxRetryError→ConnectionError). Required for
            # non-idempotent mutations where callers classify Timeout vs
            # ConnectionError to decide reconciliation.
            retry = 0
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not self.base_url:
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        return self._session.request(method, self._url(path), **kwargs)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs) -> requests.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
