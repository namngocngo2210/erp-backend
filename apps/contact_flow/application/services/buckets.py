"""Phân loại ``due_at`` tác nghiệp thành bucket (spec §4) — NGUỒN SỰ THẬT duy nhất.

Tách khỏi ``transport/serializers/runtime_serializers.py`` (audit 2026-09-23
T3/P2-2): application của orders (``contact_list_operation``) cần ``bucket_of``
mà không được import lên transport. Transport (runtime_serializers, board/list)
import lại từ đây — đúng chiều transport → application. KHÔNG viết lại logic
phân loại ở nơi khác.
"""

from __future__ import annotations

from django.utils import timezone

BUCKETS = ("overdue", "today", "upcoming", "ended", "none")


def bucket_of(due_at, now=None) -> str:
    now = now or timezone.now()
    if due_at is None:
        return "ended"
    if due_at < now:
        return "overdue"
    if timezone.localtime(due_at).date() == timezone.localtime(now).date():
        return "today"
    return "upcoming"
