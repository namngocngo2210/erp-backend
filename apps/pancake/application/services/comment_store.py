"""Lưu comment Facebook từ hội thoại Pancake `type=COMMENT` vào `pancake_posts` /
`pancake_comments`.

Không tốn request riêng: poll lead (`poll_service`) vốn đã gọi endpoint chi tiết
cho mọi hội thoại vừa cập nhật, và response đó có sẵn `post` + từng comment. Vì
vậy comment chỉ về cho page đang bật poll, và chỉ từ khi page nối Pancake.

Endpoint chi tiết (`current_count=0`) trả ~30 tin mới nhất của hội thoại — một
người comment hơn 30 lần dưới cùng một bài thì phần cũ không về (hiếm).
"""

from __future__ import annotations

from django.db import transaction

from apps.pancake.infrastructure.pancake_client import ConversationDetail, ConversationDTO
from apps.pancake.models import PancakeComment, PancakePage, PancakePost

COMMENT_TYPE = "COMMENT"


def has_stored_comments(page: PancakePage, conversation_id: str) -> bool:
    return PancakeComment.objects.filter(page=page, conversation_id=conversation_id).exists()


def save_comments(page: PancakePage, conv: ConversationDTO, detail: ConversationDetail) -> int:
    """Upsert bài + comment của một hội thoại COMMENT. Trả số comment đã ghi."""
    if conv.type != COMMENT_TYPE or not detail.comments:
        return 0

    with transaction.atomic():
        post = _upsert_post(page, conv, detail)
        for comment in detail.comments:
            PancakeComment.objects.update_or_create(
                comment_id=comment.id[:128],
                defaults={
                    "page": page,
                    "post": post,
                    "conversation_id": conv.id[:128],
                    "parent_id": comment.parent_id[:128],
                    "pancake_customer_id": conv.customer_id[:64],
                    "from_id": comment.from_id[:64],
                    "from_name": comment.from_name[:255],
                    # Trả lời của page mang `from.id` = id page Facebook.
                    "is_page_reply": bool(comment.from_id) and comment.from_id == page.pancake_page_id,
                    "message": comment.message,
                    "attachments": comment.attachments,
                    "has_phone": comment.has_phone,
                    "phone_info": comment.phone_info,
                    "like_count": comment.like_count,
                    "is_hidden": comment.is_hidden,
                    "is_removed": comment.is_removed,
                    "commented_at": comment.inserted_at,
                },
            )
    return len(detail.comments)


def _upsert_post(page: PancakePage, conv: ConversationDTO, detail: ConversationDetail) -> PancakePost | None:
    post_id = (detail.post.id if detail.post else "") or conv.post_id
    if not post_id:
        return None
    post, created = PancakePost.objects.get_or_create(
        post_id=post_id[:128],
        defaults={
            "page": page,
            "message": detail.post.message if detail.post else "",
            "raw": detail.post.raw if detail.post else {},
        },
    )
    # Bài có thể được sửa nội dung sau khi đăng — giữ bản mới nhất Pancake trả.
    if not created and detail.post and detail.post.message and detail.post.message != post.message:
        post.message = detail.post.message
        post.raw = detail.post.raw
        post.save(update_fields=["message", "raw", "updated_at"])
    return post
