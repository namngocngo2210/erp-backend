from django.db import models


class Warehouse(models.Model):
    # code/name chỉ unique trong tập kho CHƯA xoá mềm (audit 2026-09-23 C8):
    # xoá mềm một kho rồi tạo lại cùng mã/tên phải được. Xem Meta.constraints.
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "warehouses"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["deleted_at"], name="warehouse_deleted_idx"),
            models.Index(fields=["is_active"], name="warehouse_active_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="warehouse_code_active_uniq",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(deleted_at__isnull=True),
                name="warehouse_name_active_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"
