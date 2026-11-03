"""Bảng key-value cài đặt hệ thống. Khóa hợp lệ do apps.sysconfig.registry quyết định.

Một khóa dùng ĐÚNG MỘT trong hai cột: `value` (plaintext, còn psql xem được) cho
khóa thường, `secret_value` (Fernet, cùng ENCRYPTION_KEY với các bảng *Connection)
cho khóa `secret=True`. Service đảm bảo cột kia luôn rỗng — caller không tự chọn.
"""

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class SystemSetting(models.Model):
    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True, default="")
    secret_value = EncryptedCharField(max_length=4096, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "system_settings"
        verbose_name = "Cài đặt hệ thống"
        verbose_name_plural = "Cài đặt hệ thống"

    def __str__(self) -> str:
        return self.key


class SystemSettingChange(models.Model):
    """Lịch sử đổi giá trị. Secret chỉ ghi "***" (hoặc "" khi chưa đặt/xoá)."""

    key = models.CharField(max_length=64, db_index=True)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "system_setting_changes"
        ordering = ("-changed_at", "-id")

    def __str__(self) -> str:
        return f"{self.key} @ {self.changed_at:%Y-%m-%d %H:%M}"
