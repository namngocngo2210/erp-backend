"""Backfill comment Facebook cũ (trước khi poll bắt đầu lưu comment, 24/09/2026).

Poll chỉ lưu comment của hội thoại vừa cập nhật, nên comment cũ không bao giờ về.
Lệnh này quét danh sách hội thoại của page theo từng cửa sổ thời gian (lùi từ
`until` về `since`), gọi chi tiết cho mọi hội thoại `type=COMMENT` rồi lưu qua
`comment_store` — cùng đường ghi với poll, idempotent theo `comment_id`.

- Cửa sổ `WINDOW` lọc theo `updated_at` (client gửi `order_by=updated_at`), mỗi
  hội thoại có đúng một `updated_at` nên các cửa sổ phủ kín, không trùng đáng kể.
- Hội thoại đã có comment trong kho thì bỏ qua (chạy lại sau khi đứt giữa chừng
  không tốn request); `refresh=True` để lấy lại cả những hội thoại đó.
- KHÔNG đụng lead/đơn/cursor poll: chỉ ghi `pancake_posts` / `pancake_comments`.
- Page chưa có token thì sinh token (cần quyền Admin bên Pancake) nhưng KHÔNG bật
  poll — `is_enabled` giữ nguyên.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.pancake.application.services import comment_store
from apps.pancake.infrastructure.pancake_client import (
    PancakeClient,
    PancakeError,
    PancakePageTokenRejected,
)
from apps.pancake.models import PancakePage

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    page_id: int
    conversations: int = 0
    comment_conversations: int = 0
    skipped_stored: int = 0
    comments_saved: int = 0
    errors: int = 0
    error: str = ""


class PancakeCommentBackfillService:
    WINDOW = timedelta(days=30)
    PAGE_SIZE = 200

    def __init__(self, client: PancakeClient, sleep: float = 0.0, sleeper: Callable[[float], None] = time.sleep):
        self.client = client
        self.sleep = sleep
        self._sleeper = sleeper

    def backfill_page(
        self, page: PancakePage, since: datetime, until: datetime | None = None, refresh: bool = False
    ) -> BackfillResult:
        result = BackfillResult(page_id=page.id)
        until = until or timezone.now()
        try:
            token = page.page_access_token or self._new_token(page)
            win_until = until
            while win_until > since:
                win_since = max(since, win_until - self.WINDOW)
                try:
                    self._scan_window(page, token, win_since, win_until, refresh, result)
                except PancakePageTokenRejected:
                    token = self._new_token(page)
                    self._scan_window(page, token, win_since, win_until, refresh, result)
                win_until = win_since
        except PancakeError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("Pancake backfill comment: page=%s dừng (%s)", page.id, result.error)
        return result

    def _new_token(self, page: PancakePage) -> str:
        token = self.client.generate_page_token(page.pancake_page_id)
        page.page_access_token = token
        page.token_error = ""
        page.save(update_fields=["page_access_token", "token_error"])
        return token

    def _scan_window(
        self, page: PancakePage, token: str, since: datetime, until: datetime, refresh: bool, result: BackfillResult
    ) -> None:
        page_number = 1
        while True:
            conv_page = self.client.list_conversations(page.pancake_page_id, token, since, until, page_number)
            for conv in conv_page.items:
                result.conversations += 1
                if conv.type != comment_store.COMMENT_TYPE:
                    continue
                result.comment_conversations += 1
                if not refresh and comment_store.has_stored_comments(page, conv.id):
                    result.skipped_stored += 1
                    continue
                self._save_conversation(page, token, conv, result)
            if not conv_page.items or page_number * self.PAGE_SIZE >= conv_page.total:
                return
            page_number += 1

    def _save_conversation(self, page: PancakePage, token: str, conv, result: BackfillResult) -> None:
        if self.sleep:
            self._sleeper(self.sleep)
        try:
            detail = self.client.get_conversation_detail(page.pancake_page_id, token, conv.id)
        except PancakePageTokenRejected:
            raise
        except PancakeError as exc:
            # Một hội thoại lỗi không chặn cả page — đếm lại, chạy lại lệnh sẽ thử lần nữa.
            result.errors += 1
            logger.warning("Pancake backfill comment: page=%s hội thoại=%s lỗi (%s)", page.id, conv.id, exc)
            return
        result.comments_saved += comment_store.save_comments(page, conv, detail)
