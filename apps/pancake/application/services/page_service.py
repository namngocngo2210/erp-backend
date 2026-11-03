"""Đồng bộ danh sách page từ Pancake + bật/tắt poll trên một page.

Bật page = sinh token page (đòi quyền Admin bên Pancake) + đặt cursor bằng
"bây giờ" (không backfill — quyết định D5). Tắt page (ở view) chỉ hạ cờ, giữ
nguyên token và cursor; bật lại thì cursor đặt lại bằng now.

Ngoại lệ: `resume_from` — khôi phục page bị TỰ tắt (token lỗi) mà không mất lead
của khoảng vừa rồi (lệnh `pancake_resume_pages`, sau sự cố 03/10/2026).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.pancake.infrastructure.pancake_client import PancakeClient, PancakeTokenError
from apps.pancake.models import PancakeAuthStatus, PancakeConnection, PancakePage


class PageNotEligible(Exception):
    """Page chưa đủ điều kiện bật — message hiển thị thẳng lên UI."""


def check_eligible(page: PancakePage) -> None:
    """Kiểm tra điều kiện LOCAL để bật page — không cần gọi Pancake.

    Tách khỏi `PancakePageService.enable_page` để transport (view PATCH page) gọi
    được TRƯỚC khi đòi access token: thiếu kênh là lỗi nhập liệu, không phải lỗi
    kết nối Pancake — người dùng phải thấy "chưa gán kênh" thay vì "chưa có
    token" dù chưa từng nhập token bao giờ. Một nguồn message duy nhất, không lặp
    chuỗi giữa view và service.
    """
    if page.channel_id is None:
        raise PageNotEligible("Chọn kênh bán trước khi bật page.")
    if page.foxia_kind is None:
        raise PageNotEligible(f"Nền tảng {page.platform!r} chưa có cột nguồn Foxia tương ứng.")


@dataclass(frozen=True)
class SyncPagesResult:
    fetched: int
    created: int
    updated: int
    deactivated: int


def get_client(connection: PancakeConnection | None = None) -> PancakeClient | None:
    connection = connection or PancakeConnection.get_active()
    if connection is None or not connection.access_token:
        return None
    return PancakeClient(connection.access_token)


class PancakePageService:
    def __init__(self, client: PancakeClient):
        self.client = client

    def sync_pages(self, connection: PancakeConnection) -> SyncPagesResult:
        try:
            pages = self.client.list_pages()
        except PancakeTokenError as exc:
            connection.auth_status = PancakeAuthStatus.ERROR
            connection.auth_error = str(exc)
            connection.save(update_fields=["auth_status", "auth_error", "updated_at"])
            raise

        created = updated = 0
        seen: list[str] = []
        for dto in pages:
            if not dto.id:
                continue
            seen.append(dto.id)
            _, is_new = PancakePage.objects.update_or_create(
                pancake_page_id=dto.id,
                defaults={
                    "name": dto.name,
                    "platform": dto.platform,
                    "is_activated": True,
                    "has_active_subscription": dto.has_active_subscription,
                    "role_in_page": dto.role_in_page,
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1
        # Page biến mất khỏi danh sách: chỉ đánh dấu, KHÔNG xoá — lead đang PROTECT
        # theo page, và page có thể quay lại (mất quyền tạm thời).
        deactivated = (
            PancakePage.objects.filter(is_activated=True).exclude(pancake_page_id__in=seen).update(is_activated=False)
        )
        connection.auth_status = PancakeAuthStatus.OK
        connection.auth_error = ""
        connection.last_pages_synced_at = timezone.now()
        connection.save(update_fields=["auth_status", "auth_error", "last_pages_synced_at", "updated_at"])
        return SyncPagesResult(fetched=len(pages), created=created, updated=updated, deactivated=deactivated)

    def enable_page(self, page: PancakePage, resume_from: datetime | None = None) -> PancakePage:
        """Bật poll. `resume_from=None` → cursor = now (D5). Có `resume_from` →
        cursor = max(cursor cũ, resume_from): quét bù từ đó, không lùi quá chỗ đã quét."""
        check_eligible(page)
        try:
            token = self.client.generate_page_token(page.pancake_page_id)
        except PancakeTokenError as exc:
            page.is_enabled = False
            page.page_access_token = ""
            page.token_error = str(exc)
            page.save(update_fields=["is_enabled", "page_access_token", "token_error"])
            raise
        page.page_access_token = token
        page.token_error = ""
        page.is_enabled = True
        if resume_from is None:
            page.polled_until = timezone.now()
        else:
            page.polled_until = max(page.polled_until or resume_from, resume_from)
        page.last_error = ""
        page.save(update_fields=["page_access_token", "token_error", "is_enabled", "polled_until", "last_error"])
        return page
