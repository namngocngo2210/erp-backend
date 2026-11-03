"""Model của tích hợp Pancake — xem docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md.

Sáu bảng:
  1. PancakeConnection — token tài khoản (mã hoá), thường 1 row.
  2. PancakePage — mirror danh sách page + gán kênh bán + cursor poll theo page.
  3. PancakeLead — mỗi số điện thoại khách cho trong hội thoại là một dòng.
     Từ 2026-09-17: lead mới tạo ĐƠN trong `orders` (đơn tự đẩy Foxia theo
     đường đơn thường, xem `apps.orders.application.services.order_service_impl.OrderService.create_order_from_landing`)
     — các cột `push_*`/`cohoi_id` chỉ còn phục vụ đọc lịch sử lead cũ (trước
     17/09, đẩy thẳng sang Foxia, không qua `orders`).
  4. PancakeStaffMap — ánh xạ tên nhân viên Pancake (như poll hội thoại thấy)
     sang user hệ thống, dùng để gán sale cho đơn tạo từ lead.
  5. PancakePost — bài viết Facebook có comment (lưu từ hội thoại COMMENT).
  6. PancakeComment — từng comment dưới bài (cả trả lời của page), lưu kèm lượt
     poll lead — xem `apps.pancake.application.services.comment_store`.
"""

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class PancakeAuthStatus(models.TextChoices):
    UNKNOWN = "unknown", "Chưa kiểm tra"
    OK = "ok", "Hợp lệ"
    ERROR = "error", "Lỗi"


class PancakeConnection(models.Model):
    """Token của MỘT tài khoản Pancake có quyền Admin trên các page.

    Token nằm trong DB mã hoá (cùng cách FoxiaConnection.api_key), không ở env:
    đây là secret của đối tác và người vận hành xoay nó qua UI, không qua deploy.
    """

    name = models.CharField(max_length=255, default="Pancake")
    access_token = EncryptedCharField(max_length=1024)
    is_active = models.BooleanField(default=True)
    auth_status = models.CharField(
        max_length=16, choices=PancakeAuthStatus.choices, default=PancakeAuthStatus.UNKNOWN
    )
    auth_error = models.TextField(blank=True, default="")
    last_pages_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_connections"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get_active(cls) -> "PancakeConnection | None":
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()


# Platform Pancake → cột của bảng liên kết Foxia (`FoxiaLinkKind`). Page nền tảng
# khác (tiktok_shop, shopee_official…) không có cột → không gán kênh được (D4).
PLATFORM_TO_FOXIA_KIND: dict[str, str] = {
    "facebook": "messenger",
    "zalo": "zalo",
    "personal_zalo": "zalo",
}


class PancakePage(models.Model):
    """Mirror một page Pancake + cấu hình vận hành của ta trên page đó.

    `page_access_token` sinh khi BẬT page (cần quyền Admin bên Pancake), xoá khi
    Pancake từ chối. `polled_until` là cursor theo `updated_at` hội thoại — chỉ
    tiến khi lượt poll page đó không lỗi.
    """

    pancake_page_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    platform = models.CharField(max_length=32, blank=True, default="")
    is_activated = models.BooleanField(default=True)
    has_active_subscription = models.BooleanField(default=False)
    role_in_page = models.CharField(max_length=32, blank=True, default="")
    synced_at = models.DateTimeField(auto_now=True)

    page_access_token = EncryptedCharField(max_length=2048, blank=True, default="")
    token_error = models.TextField(blank=True, default="")

    channel = models.ForeignKey(
        "channels_core.Channel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pancake_pages",
    )
    is_enabled = models.BooleanField(default=False)
    polled_until = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "pancake_pages"
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return f"{self.name or self.pancake_page_id} ({self.platform})"

    @property
    def foxia_kind(self) -> str | None:
        return PLATFORM_TO_FOXIA_KIND.get(self.platform)


class PancakeStaffMap(models.Model):
    """Ánh xạ tên nhân viên Pancake (như poll hội thoại thấy trong `assignee_name`)
    sang user hệ thống. Poll tự thêm dòng khi gặp tên mới (user=None) — người vận
    hành gán user qua UI/admin. `last_seen_at` phục vụ dọn tên không còn dùng.
    """

    pancake_name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pancake_staff_maps",
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_staff_maps"
        ordering = ["pancake_name"]

    def __str__(self) -> str:
        return self.pancake_name


class PancakePushStatus(models.TextChoices):
    PENDING = "pending", "Chờ đẩy"
    OK = "ok", "Thành công"
    FAILED = "failed", "Thất bại"
    SKIPPED_NO_MAPPING = "skipped_no_mapping", "Bỏ qua — chưa cấu hình id"
    ORDER_CREATED = "order_created", "Đã tạo đơn"
    ORDER_FAILED = "order_failed", "Tạo đơn lỗi"
    ORDER_EXISTS = "order_exists", "Đã có đơn"


class PancakeLead(models.Model):
    """Một số điện thoại khách cho trong một hội thoại Pancake.

    `channel`/`foxia_kind` là SNAPSHOT lúc bắt số: đổi kênh của page sau này
    không đổi nghĩa lead cũ. `UNIQUE (channel, phone)` là khoá chống trùng D3 —
    ở DB, không ở code.

    Từ 2026-09-17: lead mới tạo ĐƠN trong orders — các cột push_*/cohoi_id chỉ
    còn phục vụ đọc lịch sử lead cũ.
    """

    RESPONSE_LIMIT = 2000

    page = models.ForeignKey(PancakePage, on_delete=models.PROTECT, related_name="leads")
    channel = models.ForeignKey(
        "channels_core.Channel", on_delete=models.PROTECT, related_name="pancake_leads"
    )
    conversation_id = models.CharField(max_length=128, db_index=True)
    conversation_inserted_at = models.DateTimeField(null=True, blank=True)
    pancake_customer_id = models.CharField(max_length=64, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    phone_raw = models.CharField(max_length=32)
    phone = models.CharField(max_length=16)
    tags = models.JSONField(default=list, blank=True)
    phone_given_at = models.DateTimeField(null=True, blank=True)
    assignee_name = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    foxia_kind = models.CharField(max_length=16)
    foxia_source_id = models.BigIntegerField(null=True, blank=True)
    push_status = models.CharField(
        max_length=32, choices=PancakePushStatus.choices, default=PancakePushStatus.PENDING
    )
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    pushed_at = models.DateTimeField(null=True, blank=True)
    cohoi_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    khachhang_id = models.BigIntegerField(null=True, blank=True)

    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pancake_leads",
    )

    class Meta:
        db_table = "pancake_leads"
        constraints = [
            models.UniqueConstraint(fields=["channel", "phone"], name="pancake_lead_channel_phone_uniq"),
        ]
        indexes = [
            models.Index(fields=["page", "-created_at"], name="pancake_lead_page_created_idx"),
            models.Index(fields=["push_status", "-created_at"], name="pancake_lead_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.phone} @ {self.page_id} ({self.push_status})"


class PancakePost(models.Model):
    """Bài viết của page — chỉ những bài đã có comment đi qua poll. `raw` giữ
    nguyên object `post` Pancake trả (hình dạng chưa kiểm chứng hết)."""

    page = models.ForeignKey(PancakePage, on_delete=models.PROTECT, related_name="posts")
    post_id = models.CharField(max_length=128, unique=True)
    message = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_posts"
        indexes = [models.Index(fields=["page", "-first_seen_at"], name="pancake_post_page_seen_idx")]

    def __str__(self) -> str:
        return self.post_id


class PancakeComment(models.Model):
    """Một comment Facebook (hoặc trả lời của page) lấy qua Pancake.

    Upsert theo `comment_id`: lượt sau cập nhật `is_removed`/`is_hidden`/
    `like_count` — comment bị xoá vẫn giữ nội dung. `conversation_id` là hội
    thoại Pancake (post × người comment), `parent_id` bằng chính `comment_id`
    với comment gốc.
    """

    page = models.ForeignKey(PancakePage, on_delete=models.PROTECT, related_name="comments")
    post = models.ForeignKey(
        PancakePost, on_delete=models.PROTECT, null=True, blank=True, related_name="comments"
    )
    comment_id = models.CharField(max_length=128, unique=True)
    conversation_id = models.CharField(max_length=128, db_index=True)
    parent_id = models.CharField(max_length=128, blank=True, default="")
    pancake_customer_id = models.CharField(max_length=64, blank=True, default="")
    from_id = models.CharField(max_length=64, blank=True, default="")
    from_name = models.CharField(max_length=255, blank=True, default="")
    is_page_reply = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    attachments = models.JSONField(default=list, blank=True)
    has_phone = models.BooleanField(default=False)
    phone_info = models.JSONField(null=True, blank=True)
    like_count = models.IntegerField(default=0)
    is_hidden = models.BooleanField(default=False)
    is_removed = models.BooleanField(default=False)
    commented_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_comments"
        indexes = [
            models.Index(fields=["page", "-commented_at"], name="pancake_cmt_page_at_idx"),
            models.Index(fields=["post", "commented_at"], name="pancake_cmt_post_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.from_name}: {self.message[:40]}"
