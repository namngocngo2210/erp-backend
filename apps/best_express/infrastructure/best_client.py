"""HTTP client cho Best Express API V3.

Token: hạn 30 ngày (lưu 29 ngày cho an toàn), re-login tối đa 1 lần khi 401.
Best giới hạn 5 lần login/phút — chặn bằng counter trên Redis cache.
Lỗi Best (Result=2) raise BestExpressError với Message NGUYÊN VĂN (passthrough).
"""

import logging
from datetime import timedelta

import requests
from django.core.cache import cache
from django.utils import timezone

from apps.best_express.exceptions import BestExpressError
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)

API_PREFIX = "/VietNamV3/v3/api/process/sears"
TRACE_MAX_CODES = 10
PRINT_MAX_CODES = 50
LOGIN_RATE_LIMIT = 5  # theo tài liệu Best: 1 phút không gọi token quá 5 lần
TOKEN_LIFETIME_DAYS = 29
_TIMEOUT = 30


def _http_post(url: str, **kwargs):
    # Timeout mặc định _TIMEOUT: quên truyền cũng không bao giờ block vô hạn.
    kwargs.setdefault("timeout", _TIMEOUT)
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise BestExpressError(f"Best Express không phản hồi: {exc}") from exc


def _login_url(account: BestExpressAccount) -> str:
    return f"{account.resolve_base_url().rstrip('/')}{API_PREFIX}/User/Login"


def ensure_token(account: BestExpressAccount, force_refresh: bool = False) -> str:
    if (
        not force_refresh
        and account.token
        and account.token_expires_at
        and account.token_expires_at > timezone.now()
    ):
        return account.token
    return _login(account)


def _login(account: BestExpressAccount) -> str:
    # Worker khác có thể vừa refresh token — đọc lại từ DB trước khi tốn lượt login.
    stale_token = account.token
    account.refresh_from_db(fields=["token", "token_expires_at"])
    if (
        account.token
        and account.token != stale_token
        and account.token_expires_at
        and account.token_expires_at > timezone.now()
    ):
        return account.token
    rate_key = f"best_express:login_count:{account.pk}:{account.username}"
    cache.add(rate_key, 0, timeout=60)
    if cache.incr(rate_key) > LOGIN_RATE_LIMIT:
        raise BestExpressError(
            "Best Express: vượt giới hạn 5 lần login/phút — thử lại sau."
        )
    logger.info("Best Express login: account=%s", account.pk)
    response = _http_post(
        _login_url(account),
        json={"Username": account.username, "Password": account.password},
        headers={"Content-Type": "application/json"},
    )
    if response.status_code >= 400:
        raise BestExpressError(
            f"Best Express login HTTP {response.status_code}: {response.text[:500]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise BestExpressError(
            f"Best Express login: phản hồi không phải JSON: {response.text[:200]}"
        ) from exc
    if isinstance(data, dict) and data.get("Result") == 2:
        raise BestExpressError(
            str(data.get("Message") or "Best Express login failed"), payload=data
        )
    token = str(data.get("token") or "").strip()
    if not token:
        raise BestExpressError(f"Best Express login không trả token: {data}")
    account.token = token
    account.token_expires_at = timezone.now() + timedelta(days=TOKEN_LIFETIME_DAYS)
    account.save(update_fields=["token", "token_expires_at", "updated_at"])
    return token


class BestExpressClient:
    def __init__(self, account: BestExpressAccount):
        self.account = account

    # -- core ---------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.account.resolve_base_url().rstrip('/')}{API_PREFIX}/{path}"

    def _headers(self, token: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if self.account.ems_key:
            headers["ems-key"] = self.account.ems_key
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        token = ensure_token(self.account)
        response = _http_post(self._url(path), json=payload, headers=self._headers(token))
        if response.status_code == 401:
            logger.warning("Best Express %s: 401 — re-login and retry", path)
            token = ensure_token(self.account, force_refresh=True)
            response = _http_post(
                self._url(path),
                json=payload,
                headers=self._headers(token),
            )
        if response.status_code >= 400:
            raise BestExpressError(
                f"Best Express HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise BestExpressError(
                f"Best Express {path}: phản hồi không phải JSON: {response.text[:200]}"
            ) from exc
        logger.info("Best Express %s -> HTTP %s", path, response.status_code)
        if isinstance(data, dict) and data.get("Result") == 2:
            raise BestExpressError(
                str(data.get("Message") or "Best Express error"), payload=data
            )
        return data

    # -- endpoints ------------------------------------------------------------

    def create_order(self, payload: dict) -> dict:
        return self._post("Order/Add", payload)

    def update_order(self, payload: dict) -> dict:
        return self._post("Order/Update", payload)

    def update_address(self, payload: dict) -> dict:
        return self._post("Order/UpdateAddress", payload)

    def cancel_order(self, code: str) -> dict:
        return self._post("Order/Cancel", {"Code": code})

    def trace_query(self, codes: list[str], lang: str = "en-US") -> dict:
        if len(codes) > TRACE_MAX_CODES:
            raise BestExpressError(
                f"TraceQuery tối đa {TRACE_MAX_CODES} mã/lần (nhận {len(codes)})."
            )
        return self._post("Order/Query", {"LangType": lang, "Codes": codes})

    def estimate_fee(self, payload: dict) -> dict:
        return self._post("Service/EstimateFee", payload)

    def print_waybill(self, codes: list[str]) -> dict:
        if len(codes) > PRINT_MAX_CODES:
            raise BestExpressError(
                f"PrintWayBill tối đa {PRINT_MAX_CODES} đơn/lần (nhận {len(codes)})."
            )
        return self._post("Print/PrintWayBill", {"codes": codes})
