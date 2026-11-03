"""Client Pancake (pages.fm) — hợp đồng kiểm chứng 2026-06-26 ở repo
desi-crawl-conversations (`docs/pancake-api-verified.md`).

Hai namespace, xác thực qua QUERY STRING (không header):
- Account API `https://pages.fm/api/v1`: list page, sinh token page (`access_token`).
- Page API `https://pages.fm/api/public_api/v1`: hội thoại, tin nhắn (`page_access_token`).

Bẫy đã biết:
- Sinh token page PHẢI là POST với `Content-Type: application/json` + body `{}`;
  thiếu header thì Pancake trả HTTP 500.
- Tham số sắp xếp là `order_by=updated_at` (KHÔNG phải `sort_by` — Pancake lặng
  lẽ bỏ qua tham số lạ và trả DESC theo `inserted_at`; poll dùng `sort_by` từ
  17/09 đến 21/09/2026 nên bỏ sót mọi hội thoại cũ vừa cho số). Với
  `order_by=updated_at`: kết quả DESC theo `updated_at` VÀ `since/until` lọc
  theo `updated_at` (hội thoại tạo từ lâu vừa có tin mới vẫn về); không có
  `order_by` thì `since/until` lọc theo `inserted_at`. Đo bằng token thật 21/09/2026.
- `recent_phone_numbers` phần tử là chuỗi HOẶC object `{phone_number}` tuỳ page.
- Timestamp không có múi giờ → UTC (đo bằng token thật ở crawler).
- Comment Facebook là hội thoại `type=COMMENT`: một hội thoại cho mỗi (post,
  người comment), có `post_id` ở danh sách. Endpoint chi tiết trả từng comment
  trong `messages` (`type=COMMENT`, cả trả lời của page; `parent_id`, `is_removed`,
  `is_hidden` — comment đã xoá vẫn còn nội dung) và toàn văn bài ở `post`.
  `message` bị HTML-escape và bọc `<div>`, `original_message` là chữ thuần.
  Đo bằng token thật 24/09/2026.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC

import requests

from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig


class PancakeError(Exception):
    """Lỗi chung khi gọi Pancake."""


class PancakeTokenError(PancakeError):
    """Token tài khoản không dùng được / không sinh được token page (mang message của Pancake)."""


class PancakePageTokenRejected(PancakeError):
    """Token page bị từ chối — bên gọi sinh lại token một lần rồi mới tắt page."""


class PancakeUnavailable(PancakeError):
    """Pancake lỗi phía máy chủ (HTTP 5xx) — lỗi tạm thời, KHÔNG phải token hỏng.

    Bên gọi giữ nguyên page/token, lượt sau thử lại. Sự cố 03/10/2026: sinh token
    trả HTTP 500 trong vài phút, bị coi là `PancakeTokenError` → 7 page bị tắt
    vĩnh viễn ~2,5 ngày, mất lead.
    """


@dataclass(frozen=True)
class PageDTO:
    id: str
    name: str
    platform: str
    is_activated: bool
    has_active_subscription: bool
    role_in_page: str


@dataclass(frozen=True)
class ConversationDTO:
    id: str
    type: str
    inserted_at: datetime | None
    updated_at: datetime | None
    has_phone: bool
    phones: list[str] = field(default_factory=list)
    customer_name: str = ""
    customer_id: str = ""
    tags: list[str] = field(default_factory=list)
    assignee_name: str = ""
    post_id: str = ""


@dataclass(frozen=True)
class ConversationPage:
    total: int
    items: list[ConversationDTO]


@dataclass(frozen=True)
class ConversationDetail:
    phones: list[str]
    customer_name: str
    customer_id: str
    # `inserted_at` của tin nhắn MỚI NHẤT có `has_phone` trong 30 tin trả về — là
    # "Ngày cho số điện thoại". Không có tin nào → None, bên gọi rơi về updated_at.
    phone_message_at: datetime | None
    # Chỉ có với hội thoại COMMENT; hội thoại INBOX → None / rỗng.
    post: PostDTO | None = None
    comments: list[CommentDTO] = field(default_factory=list)


@dataclass(frozen=True)
class PostDTO:
    id: str
    message: str
    raw: dict


@dataclass(frozen=True)
class CommentDTO:
    id: str
    parent_id: str
    from_id: str
    from_name: str
    message: str
    inserted_at: datetime | None
    has_phone: bool
    phone_info: list | dict | None
    attachments: list
    like_count: int
    is_hidden: bool
    is_removed: bool


_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


def parse_pancake_time(value) -> datetime | None:
    """Chuỗi ISO không múi giờ của Pancake → datetime UTC-aware; rác → None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def extract_phones(value) -> list[str]:
    """`recent_phone_numbers` → list chuỗi THÔ (chưa chuẩn hoá), giữ thứ tự, bỏ rác."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    phones: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("phone_number") or item.get("number") or "").strip()
        else:
            continue
        if text:
            phones.append(text)
    return phones


def _first_customer(raw: dict) -> dict:
    customers = raw.get("customers") or []
    return customers[0] if customers and isinstance(customers[0], dict) else {}


def _assignee_name(raw: dict) -> str:
    """Tên nhân viên đang được gán hội thoại. Hình dạng `current_assign_users` chưa
    kiểm chứng bằng token thật — đọc `name` nếu có, rỗng nếu không (chỉ hiển thị)."""
    users = raw.get("current_assign_users") or []
    for user in users:
        if isinstance(user, dict) and user.get("name"):
            return str(user["name"]).strip()
    return ""


def _conversation_dto(raw: dict) -> ConversationDTO:
    customer = _first_customer(raw)
    return ConversationDTO(
        id=str(raw.get("id") or ""),
        type=str(raw.get("type") or ""),
        inserted_at=parse_pancake_time(raw.get("inserted_at")),
        updated_at=parse_pancake_time(raw.get("updated_at")),
        has_phone=bool(raw.get("has_phone")),
        phones=extract_phones(raw.get("recent_phone_numbers")),
        customer_name=str(customer.get("name") or "").strip(),
        customer_id=str(raw.get("customer_id") or customer.get("id") or ""),
        tags=[str(t.get("text")).strip() for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("text")],
        assignee_name=_assignee_name(raw),
        post_id=str(raw.get("post_id") or ""),
    )


_TAG_RE = re.compile(r"<[^>]+>")


def _comment_text(raw: dict) -> str:
    text = raw.get("original_message")
    if isinstance(text, str):
        return text.strip()
    return _TAG_RE.sub("", html.unescape(str(raw.get("message") or ""))).strip()


def _comment_dto(raw: dict) -> CommentDTO:
    sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
    try:
        like_count = int(raw.get("like_count") or 0)
    except (TypeError, ValueError):
        like_count = 0
    return CommentDTO(
        id=str(raw.get("id") or ""),
        parent_id=str(raw.get("parent_id") or ""),
        from_id=str(sender.get("id") or ""),
        from_name=str(sender.get("name") or "").strip(),
        message=_comment_text(raw),
        inserted_at=parse_pancake_time(raw.get("inserted_at")),
        has_phone=bool(raw.get("has_phone")),
        phone_info=raw.get("phone_info") if isinstance(raw.get("phone_info"), (list, dict)) else None,
        attachments=raw.get("attachments") if isinstance(raw.get("attachments"), list) else [],
        like_count=like_count,
        is_hidden=bool(raw.get("is_hidden")),
        is_removed=bool(raw.get("is_removed")),
    )


def _post_dto(raw) -> PostDTO | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return PostDTO(id=str(raw["id"]), message=str(raw.get("message") or ""), raw=raw)


class PancakeClient:
    ACCOUNT_BASE = "https://pages.fm/api/v1"
    PAGE_BASE = "https://pages.fm/api/public_api/v1"

    def __init__(self, access_token: str, timeout: float | None = None):
        self._access_token = access_token
        self._timeout = float(timeout or sysconfig.get("PANCAKE_HTTP_TIMEOUT"))

    def _client(self) -> HttpClient:
        # GET idempotent, POST sinh token cũng idempotent (chỉ tạo token mới) → retry được.
        return HttpClient(timeout=self._timeout, retries=2)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Gọi HTTP qua một cửa duy nhất — bọc lỗi mạng (`requests.RequestException`,
        vd `ConnectionError`/`ReadTimeout`) thành `PancakeError`.

        Message KHÔNG được mang `str(exc)`: `requests` nhét nguyên URL đã gọi (kèm
        `access_token`/`page_access_token` dạng query string) vào message của
        exception đó — để traceback/`str(exc)` tự nhiên lọt ra log hay response là
        lộ token đối tác.
        """
        with self._client() as client:
            try:
                return getattr(client, method)(url, **kwargs)
            except requests.RequestException as exc:
                raise PancakeError(f"Không gọi được Pancake ({type(exc).__name__})") from exc

    @staticmethod
    def _json(response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise PancakeError(f"Pancake trả không phải JSON (HTTP {response.status_code})") from exc
        return data if isinstance(data, dict) else {}

    # ── Account API ──────────────────────────────────────────────────────────

    def list_pages(self) -> list[PageDTO]:
        response = self._request("get", f"{self.ACCOUNT_BASE}/pages", params={"access_token": self._access_token})
        data = self._json(response)
        if not data.get("success"):
            raise PancakeTokenError(str(data.get("message") or f"HTTP {response.status_code}"))
        pages = []
        for raw in (data.get("categorized") or {}).get("activated") or []:
            pages.append(
                PageDTO(
                    id=str(raw.get("id") or ""),
                    name=str(raw.get("name") or ""),
                    platform=str(raw.get("platform") or ""),
                    is_activated=bool(raw.get("is_activated", True)),
                    has_active_subscription=bool(raw.get("has_active_subscription")),
                    role_in_page=str(raw.get("role_in_page") or ""),
                )
            )
        return pages

    def generate_page_token(self, page_id: str) -> str:
        response = self._request(
            "post",
            f"{self.ACCOUNT_BASE}/pages/{page_id}/generate_page_access_token",
            params={"access_token": self._access_token},
            json={},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code >= 500:
            raise PancakeUnavailable(f"HTTP {response.status_code}")
        data = self._json(response)
        token = str(data.get("page_access_token") or "")
        if not data.get("success") or not token:
            raise PancakeTokenError(str(data.get("message") or f"HTTP {response.status_code}"))
        return token

    # ── Page API ─────────────────────────────────────────────────────────────

    def _page_get(self, url: str, params: dict) -> dict:
        response = self._request("get", url, params=params)
        if response.status_code in (401, 403):
            raise PancakePageTokenRejected(f"HTTP {response.status_code}")
        data = self._json(response)
        if not data.get("success"):
            message = str(data.get("message") or f"HTTP {response.status_code}")
            if "token" in message.lower():
                raise PancakePageTokenRejected(message)
            raise PancakeError(message)
        return data

    def list_conversations(
        self, page_id: str, page_token: str, since: datetime, until: datetime, page_number: int
    ) -> ConversationPage:
        data = self._page_get(
            f"{self.PAGE_BASE}/pages/{page_id}/conversations",
            {
                "page_access_token": page_token,
                "since": int(since.timestamp()),
                "until": int(until.timestamp()),
                "page_number": page_number,
                "order_by": "updated_at",
            },
        )
        items = [_conversation_dto(raw) for raw in data.get("conversations") or [] if isinstance(raw, dict)]
        return ConversationPage(total=int(data.get("total") or 0), items=items)

    def get_conversation_detail(self, page_id: str, page_token: str, conversation_id: str) -> ConversationDetail:
        data = self._page_get(
            f"{self.PAGE_BASE}/pages/{page_id}/conversations/{conversation_id}/messages",
            {"page_access_token": page_token, "current_count": 0},
        )
        customer = _first_customer(data)
        phone_message_at = None
        for message in data.get("messages") or []:
            if isinstance(message, dict) and message.get("has_phone"):
                when = parse_pancake_time(message.get("inserted_at"))
                if when and (phone_message_at is None or when > phone_message_at):
                    phone_message_at = when
        # Ba chỗ có thể chứa số (đo bằng token thật 17/09/2026): hồ sơ khách (kể cả số
        # nhân viên gắn qua ĐƠN Pancake POS — dạng {order_id, phone_number}, KHÔNG bật
        # has_phone và không nằm trong tin nhắn nào), rồi `recent_phone_numbers` và
        # `conv_phone_numbers` ở cấp trên cùng. Gộp, khử trùng, giữ thứ tự.
        phones: list[str] = []
        for source in (
            customer.get("recent_phone_numbers"),
            data.get("recent_phone_numbers"),
            data.get("conv_phone_numbers"),
        ):
            for raw in extract_phones(source):
                if raw not in phones:
                    phones.append(raw)
        return ConversationDetail(
            phones=phones,
            customer_name=str(customer.get("name") or "").strip(),
            customer_id=str(customer.get("customer_id") or customer.get("id") or ""),
            phone_message_at=phone_message_at,
            post=_post_dto(data.get("post")),
            comments=[
                _comment_dto(m)
                for m in data.get("messages") or []
                if isinstance(m, dict) and m.get("type") == "COMMENT" and m.get("id")
            ],
        )
