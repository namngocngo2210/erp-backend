from django.db import models
from django.db.models import Q

from apps.common.fields import EncryptedCharField
from apps.sysconfig import sysconfig


class CnvConnection(models.Model):
    """Cấu hình + token OAuth của một kết nối CNV Loyalty (thường chỉ 1 row active).

    client_secret và cặp token là secret → mã hóa như mọi secret khác trong DB
    (pattern BestExpressAccount). Token lấy qua luồng SSO OAuth ở
    ``transport/api_views.py``; refresh tự động ở ``infrastructure/token_provider.py``.
    """

    name = models.CharField(max_length=255, default="CNV Loyalty")
    # account_id do CNV cấp kèm bộ client (2026-08-05). API/tài liệu hiện KHÔNG dùng
    # nó trong request nào — lưu để đối soát với CNV và sẵn chỗ nếu API thật yêu cầu.
    account_id = models.CharField(max_length=255, blank=True, default="")
    client_id = models.CharField(max_length=255)
    client_secret = EncryptedCharField(max_length=512)
    access_token = EncryptedCharField(max_length=4096, blank=True, default="")
    refresh_token = EncryptedCharField(max_length=4096, blank=True, default="")
    token_type = models.CharField(max_length=32, blank=True, default="Bearer")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scope = models.CharField(max_length=512, blank=True, default="")
    sso_server_uri = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Override SSO server; trống = CNV_SSO_SERVER_URI trong cài đặt hệ thống",
    )
    api_base_url = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Override API base; trống = CNV_API_BASE_URL trong cài đặt hệ thống",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cnv_connections"

    def __str__(self) -> str:
        return f"{self.name} ({self.client_id})"

    def resolve_sso_server_uri(self) -> str:
        return (self.sso_server_uri or sysconfig.get("CNV_SSO_SERVER_URI")).rstrip("/")

    def resolve_api_base_url(self) -> str:
        return (self.api_base_url or sysconfig.get("CNV_API_BASE_URL")).rstrip("/")

    @classmethod
    def get_active(cls) -> "CnvConnection | None":
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()


class CnvEntityType(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    PRODUCT = "product", "Product"
    ORDER = "order", "Order"
    CUSTOM_COLLECTION = "custom_collection", "Custom collection"


class CnvSyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SYNCED = "synced", "Synced"
    FAILED = "failed", "Failed"


class CnvSyncRecord(models.Model):
    """Outbox + bảng mapping ID nội bộ ↔ CNV cho từng entity.

    Mọi lần đẩy đều đi qua record này nên sync idempotent và retry được:
    cron ``retry_failed_records`` quét status≠synced theo backoff; ``attempts``
    chặn retry vô hạn. ``extra`` giữ dữ liệu phụ theo entity (variant_id của
    product, danh mục đã collect...) — không đủ phổ quát để thành cột riêng.
    """

    entity_type = models.CharField(max_length=32, choices=CnvEntityType.choices)
    local_id = models.BigIntegerField()
    cnv_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=CnvSyncStatus.choices, default=CnvSyncStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cnv_sync_records"
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "local_id"], name="cnv_sync_entity_local_uniq"
            ),
        ]
        indexes = [
            # Cron retry chỉ quét record chưa synced — partial index giữ nó nhỏ
            # dù bảng phình theo tổng số entity đã sync.
            models.Index(
                fields=["status", "updated_at"],
                name="cnv_sync_unsynced_idx",
                condition=~Q(status="synced"),
            ),
            models.Index(fields=["entity_type", "cnv_id"], name="cnv_sync_cnv_id_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}#{self.local_id} -> {self.cnv_id or '-'} [{self.status}]"


# ─────────────────────────────────────────────────────────────────────────────
# Chiều NGƯỢC: CNV → desi-order (spec 2026-08-19)
# ─────────────────────────────────────────────────────────────────────────────


class CnvOrderLink(models.Model):
    """Cầu nối 1-1 giữa đơn mirror trong bảng `orders` và đơn gốc bên CNV.

    Đơn nằm chung bảng `orders` (order_source="cnv") để tái dùng hạ tầng sẵn có;
    mọi field CNV không có chỗ trong Order nằm ở đây thay vì đẻ thêm cột vào một
    bảng vốn đã rất rộng và rất nóng — cùng lý do `FoxiaOrderLink`.

    ``cnv_order_id`` unique là KHOÁ IDEMPOTENT của toàn bộ đường ingest: webhook
    retry, poll chồng cửa sổ, backfill chạy lại — bao nhiêu lần cũng không sinh
    đơn thứ hai.

    ``unique_id`` giữ nguyên văn external id CNV trả về. Đơn do CHÍNH MÌNH đẩy
    lên mang dạng ``desi-<order_id>`` (xem ``domain/mappers.build_unique_id``) và
    bị loại ngay ở tầng lọc đầu tiên, nên giá trị ở đây gần như luôn là external
    id của bên thứ ba hoặc rỗng — lưu lại để truy vết khi đối soát với CNV.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="cnv_link"
    )
    cnv_order_id = models.BigIntegerField(unique=True)
    cnv_name = models.CharField(max_length=100, blank=True, default="")
    unique_id = models.CharField(max_length=128, blank=True, default="")
    cnv_customer_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    # Bộ 3 trạng thái của CNV, lưu NGUYÊN VĂN. Không map sang OrderStatus: chưa
    # có bản chốt danh sách mã từ CNV nên map lúc này là đoán, mà đoán sai thì
    # đơn hiện sai trạng thái và không ai biết vì dữ liệu gốc đã bị nuốt.
    financial_status = models.CharField(max_length=32, blank=True, default="")
    fulfillment_status = models.CharField(max_length=32, blank=True, default="")
    cancelled_status = models.CharField(max_length=32, blank=True, default="")

    location_id = models.BigIntegerField(null=True, blank=True)
    subtotal_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_discounts = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    shipment_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Mốc thời gian gốc phía CNV. KHÔNG ghi đè Order.created_at: đó là mốc bản
    # ghi vào hệ thống ta, hai thứ khác nhau và cần cả hai khi truy vết.
    cnv_created_at = models.DateTimeField(null=True, blank=True)
    cnv_updated_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cnv_order_links"
        indexes = [
            models.Index(fields=["cnv_updated_at"], name="cnv_link_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"cnv#{self.cnv_order_id} -> order#{self.order_id}"


class CnvOrderLine(models.Model):
    """Dòng sản phẩm của đơn CNV, giữ nguyên trạng.

    KHÔNG tạo ``OrderItem``: ``OrderItem.product`` là FK PROTECT NOT NULL, mà đơn
    CNV có thể chứa sản phẩm không nằm trong danh mục nội bộ (khách đặt hàng bán
    riêng trên app loyalty). Ép khớp là đoán, đoán sai thì doanh thu gán nhầm
    sản phẩm.

    Khác Foxia ở một điểm quan trọng: CNV CÓ trả ``sku``. Lưu lại thì ngày nào
    chốt promote đơn CNV thành đơn thật, dữ kiện khớp Product đã nằm sẵn trong
    DB, không phải kéo lại từ CNV.

    ``line_index`` chứ không phải id dòng: payload CNV không đảm bảo mỗi line
    item có id ổn định, nên vị trí trong mảng là thứ duy nhất dùng được làm khoá
    idempotent khi cùng một đơn được đẩy về nhiều lần.
    """

    link = models.ForeignKey(CnvOrderLink, on_delete=models.CASCADE, related_name="lines")
    line_index = models.PositiveIntegerField()
    cnv_product_id = models.BigIntegerField(null=True, blank=True)
    cnv_variant_id = models.BigIntegerField(null=True, blank=True)
    sku = models.CharField(max_length=100, blank=True, default="")
    product_name = models.CharField(max_length=255, blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cnv_order_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["link", "line_index"], name="cnv_order_line_idx_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["sku"], name="cnv_order_line_sku_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.link_id}#{self.line_index} {self.sku or self.product_name}"


class CnvInboundCursor(models.Model):
    """Mốc quét tăng dần của đường POLL (lưới vá cho webhook).

    Một dòng cho mỗi resource. Poll KHÔNG phải đường chính — webhook mới là — nên
    cursor được phép lùi lại ``CNV_INBOUND_LOOKBACK_MINUTES`` mỗi lần chạy: quét
    chồng lặp vô hại vì ``cnv_order_id`` unique, còn bỏ sót đơn thì không ai biết.
    """

    resource = models.CharField(max_length=64, unique=True)
    synced_until = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "cnv_inbound_cursors"

    def __str__(self) -> str:
        return f"{self.resource} @ {self.synced_until:%Y-%m-%d %H:%M}"


class CnvWebhookEvent(models.Model):
    """Payload webhook thô từ CNV — lưu trước, xử lý sau (pattern BestWebhookEvent).

    Vì sao tách hai bước: CNV chờ HTTP response, mà ingest một đơn có thể chạm
    nhiều bảng. Lưu rồi trả 200 ngay thì CNV không retry vì mình chậm; lỗi áp
    event nằm lại ở ``processed=False`` cho cron vớt.

    Khoá chống trùng là ``(topic, cnv_object_id, payload_hash)``: CNV retry cùng
    một event thì payload y hệt → hash trùng → chèn trượt. Hai lần cập nhật thật
    sự khác nhau thì hash khác nhau nên vẫn vào được.
    """

    topic = models.CharField(max_length=64, blank=True, default="")
    cnv_object_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    payload_hash = models.CharField(max_length=64)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cnv_webhook_events"
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "cnv_object_id", "payload_hash"],
                name="cnv_webhook_event_uniq",
            ),
        ]
        indexes = [
            # Cron chỉ quét event chưa xử lý — partial index giữ nó nhỏ dù bảng
            # phình theo tổng số webhook đã nhận.
            models.Index(
                fields=["received_at"],
                name="cnv_webhook_unprocessed_idx",
                condition=Q(processed=False),
            ),
            models.Index(fields=["received_at"], name="cnv_webhook_received_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.topic or '?'}#{self.cnv_object_id or '-'} [{'ok' if self.processed else 'pending'}]"
