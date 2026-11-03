import uuid

from django.db import models
from django.utils import timezone


class ExportJobStatus(models.TextChoices):
    PENDING = "pending", "Đang chờ"
    RUNNING = "running", "Đang xử lý"
    SUCCESS = "success", "Hoàn tất"
    FAILED = "failed", "Thất bại"
    CANCELLED = "cancelled", "Đã huỷ"
    EXPIRED = "expired", "Hết hạn"


# Trạng thái mà một job vẫn còn "sống" — dùng cho dedupe và cho panel FE (chỉ poll
# khi còn job sống). Gom thành hằng để hai nơi không lệch nhau khi thêm trạng thái.
ACTIVE_STATUSES = (ExportJobStatus.PENDING, ExportJobStatus.RUNNING)


class ExportJob(models.Model):
    """Một lượt xuất file chạy nền.

    Khóa chính là UUID chứ không phải số tuần tự: id này lộ ra URL cho FE, và số
    tuần tự cho phép đoán/đếm số lượt xuất của cả công ty.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="export_jobs")
    kind = models.CharField(max_length=50)
    params = models.JSONField(default=dict)
    # sha256 của (kind + params đã chuẩn hoá) — nền tảng của dedupe chống double-click.
    params_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=ExportJobStatus.choices, default=ExportJobStatus.PENDING
    )
    total_rows = models.IntegerField(null=True, blank=True)
    progress_rows = models.IntegerField(default=0)
    file_key = models.CharField(max_length=255, blank=True, default="")
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    celery_task_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "export_jobs"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="export_user_created_idx"),
            models.Index(fields=["status", "expires_at"], name="export_status_expires_idx"),
            models.Index(
                fields=["user", "kind", "params_hash", "status"], name="export_dedupe_idx"
            ),
        ]
        constraints = [
            # Dedupe phải do DB bảo đảm, không phải do "đọc rồi ghi" ở tầng ứng dụng:
            # hai request double-click chạy song song đều đọc thấy "chưa có job nào"
            # trước khi bên kia kịp commit. Điều kiện theo trạng thái ACTIVE để job cũ
            # đã xong không chặn lượt xuất mới cùng bộ lọc.
            #
            # `condition` dùng giá trị chuỗi ["pending", "running"] chứ không phải hằng
            # `ACTIVE_STATUSES` — migration serialize điều kiện, dùng hằng import sẽ
            # sinh migration phụ thuộc tên module. PHẢI khớp thủ công với ACTIVE_STATUSES
            # ở trên nếu sau này thêm/bớt trạng thái active.
            models.UniqueConstraint(
                fields=["user", "kind", "params_hash"],
                condition=models.Q(status__in=["pending", "running"]),
                name="export_dedupe_active_uniq",
            ),
        ]

    def is_downloadable(self) -> bool:
        return (
            self.status == ExportJobStatus.SUCCESS
            and bool(self.file_key)
            and self.expires_at > timezone.now()
        )
