"""Module Tác nghiệp CSKH — máy trạng thái cấu hình được (docs/contact-flow-design.md)."""
from django.db import models


class OperationStage(models.Model):
    """Bước tác nghiệp (sheet `tacnghiepcan`). name là khóa upsert import."""

    name = models.CharField(max_length=120, unique=True)
    color = models.CharField(max_length=7, blank=True, default="")
    is_start = models.BooleanField(default=False)
    allow_edit_due = models.BooleanField(default=False)
    # Cờ "Kho số" + "Nhãn khách hàng": v1 lưu thô, chưa có hành vi (spec §2.6).
    pool_enabled = models.BooleanField(default=False)
    pool_minutes = models.IntegerField(default=0)
    customer_label = models.CharField(max_length=120, blank=True, default="")
    is_active = models.BooleanField(default=True)
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operation_stages"
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class OperationResult(models.Model):
    """Kết quả tác nghiệp (sheet `ketquatacnghiep`). Độc lập với sales_status_code."""

    name = models.CharField(max_length=120, unique=True)
    color = models.CharField(max_length=7, blank=True, default="")
    is_close = models.BooleanField(default=False)
    requires_schedule = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operation_results"
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class OperationTransition(models.Model):
    """Rule: (from_stage, result) → to_stage sau delay_minutes.

    to_stage null = kết thúc nhánh (due_at về null). Validate ở service:
    to_stage khác null ⇔ delay_minutes ≥ 1.
    """

    from_stage = models.ForeignKey(
        OperationStage, on_delete=models.PROTECT, related_name="transitions_out"
    )
    result = models.ForeignKey(
        OperationResult, on_delete=models.PROTECT, related_name="transitions"
    )
    to_stage = models.ForeignKey(
        OperationStage, on_delete=models.PROTECT, null=True, blank=True,
        related_name="transitions_in",
    )
    delay_minutes = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operation_transitions"
        constraints = [
            models.UniqueConstraint(
                fields=["from_stage", "result"], name="op_transition_from_result_uniq"
            )
        ]


class CustomerOperationState(models.Model):
    """Trạng thái runtime, 1 dòng/khách. due_at null = flow kết thúc nhánh."""

    customer = models.OneToOneField(
        "customers.Customer", on_delete=models.CASCADE, related_name="operation_state"
    )
    current_stage = models.ForeignKey(OperationStage, on_delete=models.PROTECT)
    due_at = models.DateTimeField(null=True, blank=True)
    entered_at = models.DateTimeField()
    last_result = models.ForeignKey(
        OperationResult, on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_operation_states"
        indexes = [
            models.Index(fields=["due_at"], name="op_state_due_idx"),
            models.Index(fields=["current_stage", "due_at"], name="op_state_stage_due_idx"),
        ]
