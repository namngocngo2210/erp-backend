"""Model của module Media — xem docs/superpowers/specs/2026-09-04-media-module-design.md §4.2.

Không có bảng nghiệp vụ nào của ads-crm được mirror về đây. Ba bảng chỉ phục vụ
việc NÓI CHUYỆN với ads-crm: kết nối (key chung) và hai bảng owner để mô phỏng
kiểm tra chủ sở hữu khi mọi request đi bằng một tài khoản service.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class MediaConnection(models.Model):
    """Kết nối tới ads-crm (thường chỉ 1 dòng active). Key chung mã hoá at-rest."""

    base_url = models.CharField(max_length=255, help_text="VD: https://media.example.vn (không kèm /api/v1)")
    shared_api_key = EncryptedCharField(max_length=1024, blank=True, default="")
    is_active = models.BooleanField(default=True)

    last_check_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(null=True, blank=True)
    last_check_error = models.TextField(blank=True, default="")
    shared_key_role = models.CharField(max_length=32, blank=True, default="")
    shared_key_username = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "media_connections"

    def __str__(self) -> str:
        return f"MediaConnection({self.base_url})"

    @classmethod
    def get_active(cls) -> MediaConnection | None:
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()

    def resolve_base_url(self) -> str:
        return (self.base_url or "").strip().rstrip("/")


class MediaContentOwner(models.Model):
    """Nội dung ads-crm `remote_id` do user desi-order nào tạo QUA KEY CHUNG.

    Chỉ ghi ở chế độ key chung; key riêng thì ads-crm đã biết chủ sở hữu thật.
    """

    remote_id = models.BigIntegerField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "media_content_owners"


class MediaProposalOwner(models.Model):
    remote_id = models.BigIntegerField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "media_proposal_owners"
