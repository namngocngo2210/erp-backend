from datetime import datetime
from uuid import uuid4
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from apps.common.events import event_bus
from apps.customers.contracts.customer_events import CustomerSaved


def generate_customer_code(created_at=None):
    """Tạo mã tạm duy nhất trước khi có PK, sẽ được đổi sang KH<id> sau khi lưu."""
    if created_at is None:
        created_at = datetime.now()
    timestamp = created_at.strftime("%y%m%d%H%M%S")
    random_suffix = uuid4().hex[:6].upper()
    return f"TMP{timestamp}{random_suffix}"


class Customer(models.Model):
    full_name = models.TextField(blank=True, default="")
    # Bỏ db_index (audit 2026-07 mục 3.3): composite customer_code_phone_raw_idx
    # (code, phone_raw) leading trên code đã phủ lookup theo code.
    # (customer_code_phone_idx cũ đã drop ở 0035 — 0 scans trên prod, trùng prefix.)
    #
    # KHÔNG unique — CHỦ Ý nhưng CẦN QUYẾT ĐỊNH (audit 2026-09-23 T2/P2-7): unique
    # bỏ ở migration 0011; prod 23/09 có 7.069 mã trùng / 14.143 dòng active (mã
    # KH<id> tự sinh không trùng, trùng đến từ import/webhook gán mã ngoài). Lookup
    # theo code (gateway_kship_customer_webhook) có thể trả nhiều khách — chỉ được
    # thêm lại unique (partial deleted_at IS NULL) sau khi dọn trùng; xem
    # docs/runbooks/2026-09-23-audit-ops.md mục Database.
    code = models.CharField(
        max_length=50,
        blank=True,
        default=generate_customer_code,
        help_text="Mã khách hàng"
    )
    # CharField thay TextField (audit 2026-09-23 T2/P2-8): hai cột đều có B-tree +
    # GIN trgm; trần đo prod 23/09: phone_raw max 52 ký tự (4 dòng > 32),
    # phone_normalized max 11 → 64/20 là trần an toàn, serializer chặn dài hơn ở 400.
    phone_raw = models.CharField(max_length=64, blank=True, default="")
    phone_normalized = models.CharField(max_length=20, blank=True, default="")
    gender = models.SmallIntegerField(
        choices=[(0, "Female"), (1, "Male")],
        null=True,
        blank=True,
        help_text="0: Female, 1: Male"
    )
    # Loại khách (denormalized): True = khách mới (chưa có đơn GIAO THÀNH CÔNG), False =
    # khách cũ (≥1 đơn giao thành công). Đơn tính = chưa xóa mềm, status=3, hóa đơn
    # không bị hủy. Cập nhật qua apps.customers.customer_type (event orders.saved +
    # post_delete đơn + bulk delete + reconcile trong cron rebuild_segment_snapshot).
    is_new_customer = models.BooleanField(
        default=True,
        help_text="True: khách mới (chưa có đơn giao thành công), False: khách cũ (≥1 đơn giao thành công)",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customers"
        indexes = [
            models.Index(fields=["phone_normalized"], name="customer_phone_normalized_idx"),
            models.Index(fields=["code", "phone_raw"], name="customer_code_phone_raw_idx"),
            models.Index(fields=["deleted_at"], name="customer_deleted_idx"),
            models.Index(fields=["created_at"], name="customer_created_at_idx"),
            GinIndex(fields=["phone_raw"], name="cust_phone_raw_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Hai index dưới TỒN TẠI TRÊN PROD từ trước nhưng chưa từng có trong code
            # (drift tạo tay) — migration 0035 codify lại. Chúng phục vụ ô tìm kiếm
            # khách hàng qua `__ilike_contains` (repositories.py).
            GinIndex(fields=["full_name"], name="cust_fullname_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["code"], name="cust_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["phone_normalized"], name="cust_phone_norm_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            # Một số điện thoại chỉ thuộc về một customer đang active (audit 2026-07 mục 3.2).
            # Partial: bỏ qua phone rỗng và bản ghi đã soft-delete.
            models.UniqueConstraint(
                fields=["phone_normalized"],
                condition=~models.Q(phone_normalized="") & models.Q(deleted_at__isnull=True),
                name="cust_phone_norm_active_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.full_name or self.phone_normalized}"

    def save(self, *args, **kwargs):
        is_creating = self._state.adding
        if is_creating and not self.code:
            self.code = generate_customer_code()

        super().save(*args, **kwargs)

        if is_creating and self.code.startswith("TMP") and self.pk:
            prefix = getattr(self, "_code_prefix", "KH") or "KH"
            final_code = f"{prefix}{self.pk}"
            if self.code != final_code:
                Customer.objects.filter(pk=self.pk).update(code=final_code)
                self.code = final_code

        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not update_fields:
            # save(update_fields=[]) là no-op của Django (không ghi, không post_save).
            return
        # Domain event thay cho post_save mà cnv_loyalty từng móc thẳng vào model
        # (audit 2026-09-23 T3/P2-4). Phát sau khi mã KH đã chốt để consumer đọc
        # được bản ghi hoàn chỉnh sau commit.
        event_bus.publish_on_commit(
            CustomerSaved(
                customer_id=self.pk,
                created=is_creating,
                is_deleted=self.deleted_at is not None,
            )
        )


class LoyalCustomerNote(models.Model):
    """Ghi chú sale nhập tay cho trang Khách hàng thân thiết.

    Tách bảng riêng (sparse) thay vì thêm cột vào ``customers`` vì chỉ một phần nhỏ
    khách hàng cần ghi chú, và để không ảnh hưởng các nghiệp vụ khác dùng ``customers``.
    """

    customer = models.OneToOneField(
        Customer,
        on_delete=models.CASCADE,
        related_name="loyal_note",
    )
    sale_action = models.TextField(blank=True, default="", help_text="Hành động sale cần làm ngay")
    special_note = models.TextField(blank=True, default="", help_text="Ghi chú đặc biệt")
    resale_status = models.TextField(blank=True, default="", help_text="Trạng thái resale (sale nhập tay)")
    resale_suggestion = models.TextField(blank=True, default="", help_text="Gợi ý SP resale tiếp theo (sale nhập tay)")
    assigned_sale = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_loyal_customer_notes",
        help_text="Sale phụ trách khách hàng thân thiết",
    )
    last_care_call_at = models.DateField(
        null=True,
        blank=True,
        help_text="Ngày gọi chăm sóc gần nhất",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_loyal_customer_notes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loyal_customer_notes"

    def __str__(self):
        return f"LoyalNote({self.customer_id})"


class CustomerResaleFollowUp(models.Model):
    """Thoi diem sale da xu ly lien he resale theo tung don giao thanh cong."""

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="resale_followup",
    )
    contact_1_at = models.DateTimeField(null=True, blank=True)
    contact_2_at = models.DateTimeField(null=True, blank=True)
    contact_3_at = models.DateTimeField(null=True, blank=True)
    contact_logs = models.JSONField(default=list, blank=True)
    appointment_at = models.DateTimeField(null=True, blank=True)
    appointment_channel = models.CharField(max_length=64, blank=True, default="")
    appointment_purpose = models.CharField(max_length=120, blank=True, default="")
    appointment_note = models.TextField(blank=True, default="")
    resale_orders = models.JSONField(default=list, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_resale_followups",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_resale_followups"
        indexes = [
            models.Index(fields=["contact_1_at"], name="cust_resale_c1_idx"),
            models.Index(fields=["contact_2_at"], name="cust_resale_c2_idx"),
            models.Index(fields=["contact_3_at"], name="cust_resale_c3_idx"),
        ]

    def __str__(self):
        return f"ResaleFollowUp({self.order_id})"


class CustomerSegmentCategory(models.Model):
    """Danh mục phân loại khách hàng (S1–S7).

    Lưu mã, tên, tiêu chí và ưu tiên chăm sóc để dùng làm nhãn hiển thị và tham chiếu.
    Logic phân loại tự động nằm ở ``apps/customers/segmentation.py``.
    """

    code = models.CharField(max_length=8, unique=True, help_text="Mã phân khúc, vd S1")
    name = models.CharField(max_length=100, help_text="Tên phân khúc")
    criteria = models.TextField(blank=True, default="", help_text="Tiêu chí phân loại")
    care_priority = models.TextField(blank=True, default="", help_text="Ưu tiên chăm sóc")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_segment_categories"
        ordering = ["sort_order", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class CustomerSegmentSnapshot(models.Model):
    """Ảnh chụp phân khúc + RFM/khách, cập nhật định kỳ (Celery). Cho phép filter
    segment bằng WHERE-indexed thay vì classify realtime toàn tập."""
    customer = models.OneToOneField(
        "Customer", on_delete=models.CASCADE, primary_key=True, related_name="segment_snapshot",
    )
    segment_code = models.CharField(max_length=20, db_index=True)
    total_orders = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_purchase_at = models.DateTimeField(null=True, blank=True)
    has_problem = models.BooleanField(default=False)
    latest_sales_status_code = models.CharField(max_length=64, null=True, blank=True)
    is_problem_order_customer = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_segment_snapshot"
        indexes = [
            models.Index(
                fields=["last_purchase_at", "-total_spent"],
                name="snap_lastbuy_spent_idx",
            ),
        ]


class ContactActivityKind(models.TextChoices):
    CALL = "call", "Cuộc gọi"
    NOTE = "note", "Ghi chú"
    OPERATION = "operation", "Tác nghiệp"


class ContactActivity(models.Model):
    """Log liên hệ append-only cấp khách hàng (trang Danh sách contact).

    CHỈ lưu call/note. Lịch sử đổi trạng thái sale KHÔNG lưu ở đây —
    đọc từ OrderJourneyEvent (event_type=sales_status_changed) đã có sẵn.
    """

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="contact_activities"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contact_activities",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contact_activities",
    )
    kind = models.CharField(max_length=16, choices=ContactActivityKind.choices)
    note = models.TextField(blank=True, default="")
    call_meta = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contact_activities"
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="contact_act_cust_created_idx"),
            models.Index(fields=["staff", "created_at"], name="contact_act_staff_idx"),
        ]
