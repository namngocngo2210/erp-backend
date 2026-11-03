from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.fields import EncryptedCharField

from apps.best_express.domain import status_map


class BestShipmentStatus(models.TextChoices):
    """Trạng thái vận đơn Best — giá trị/nhãn lấy từ domain ``status_map`` (pure).

    Enum Django đặt ở đây (chứ không ở domain) vì ``models.TextChoices`` là chi tiết
    Django; domain chỉ giữ chuỗi + bảng map (audit 2026-09-23 T3/P2-2).
    """

    CREATED = status_map.CREATED, status_map.STATUS_LABELS[status_map.CREATED]
    PICKING_UP = status_map.PICKING_UP, status_map.STATUS_LABELS[status_map.PICKING_UP]
    PICKUP_FAILED = status_map.PICKUP_FAILED, status_map.STATUS_LABELS[status_map.PICKUP_FAILED]
    PICKED_UP = status_map.PICKED_UP, status_map.STATUS_LABELS[status_map.PICKED_UP]
    IN_TRANSIT = status_map.IN_TRANSIT, status_map.STATUS_LABELS[status_map.IN_TRANSIT]
    OUT_FOR_DELIVERY = (
        status_map.OUT_FOR_DELIVERY,
        status_map.STATUS_LABELS[status_map.OUT_FOR_DELIVERY],
    )
    DELIVERY_FAILED = (
        status_map.DELIVERY_FAILED,
        status_map.STATUS_LABELS[status_map.DELIVERY_FAILED],
    )
    DELIVERED = status_map.DELIVERED, status_map.STATUS_LABELS[status_map.DELIVERED]
    RETURNING = status_map.RETURNING, status_map.STATUS_LABELS[status_map.RETURNING]
    RETURN_FAILED = status_map.RETURN_FAILED, status_map.STATUS_LABELS[status_map.RETURN_FAILED]
    RETURNED = status_map.RETURNED, status_map.STATUS_LABELS[status_map.RETURNED]
    LOST = status_map.LOST, status_map.STATUS_LABELS[status_map.LOST]
    DAMAGED = status_map.DAMAGED, status_map.STATUS_LABELS[status_map.DAMAGED]
    CANCELLED = status_map.CANCELLED, status_map.STATUS_LABELS[status_map.CANCELLED]


class BestExpressEnvironment(models.TextChoices):
    TEST = "test", "Test"
    PRODUCTION = "production", "Production"


class BestExpressAccount(models.Model):
    name = models.CharField(max_length=255, verbose_name="Tên tài khoản")
    environment = models.CharField(
        max_length=20,
        choices=BestExpressEnvironment.choices,
        default=BestExpressEnvironment.TEST,
    )
    base_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Override base URL; trống = default theo environment",
    )
    username = models.CharField(max_length=255)
    password = EncryptedCharField(max_length=512)
    token = EncryptedCharField(max_length=4096, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    # ems_key là secret xác thực (gửi làm header `ems-key` mỗi call) → mã hoá như các
    # secret khác, không để plaintext trong DB/backup (audit 2026-07-14 vòng 2).
    ems_key = EncryptedCharField(max_length=512, blank=True, default="")
    webhook_token = EncryptedCharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Bearer token tĩnh mình sinh, gửi sale Best cấu hình webhook",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "best_express_accounts"
        constraints = [
            # Credential được quản lý trực tiếp trong bảng này (admin/UI) theo cặp
            # username+environment; không có constraint thì 2 lần tạo đồng thời
            # sinh bản trùng.
            models.UniqueConstraint(
                fields=["username", "environment"], name="best_account_username_env_uniq"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.environment})"

    def resolve_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.environment == BestExpressEnvironment.PRODUCTION:
            return settings.BEST_EXPRESS_BASE_URL_PROD
        return settings.BEST_EXPRESS_BASE_URL_TEST


_DECIMAL_KW = {"max_digits": 14, "decimal_places": 2, "default": 0}


class BestShipment(models.Model):
    account = models.ForeignKey(
        BestExpressAccount, on_delete=models.PROTECT, related_name="shipments"
    )
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="best_shipments",
    )
    # Vận đơn chưa gắn Order (Best chưa wire vào luồng đơn) không có gì để quy chiếu chủ
    # sở hữu → scope hẹp không thấy lại chính vận đơn mình vừa tạo. Neo theo người tạo
    # (audit 2026-07-14 vòng 3).
    created_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="best_shipments_created",
    )
    merchant_order_code = models.CharField(max_length=100, unique=True)
    code_best = models.CharField(max_length=100, unique=True, null=True, blank=True)
    route_code = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=BestShipmentStatus.choices,
        default=BestShipmentStatus.CREATED,
    )
    best_status_code = models.CharField(max_length=32, blank=True, default="")
    status_content = models.TextField(blank=True, default="")
    cod = models.DecimalField(**_DECIMAL_KW)
    product_price = models.DecimalField(**_DECIMAL_KW)
    freight_fee = models.DecimalField(**_DECIMAL_KW)
    cod_fee = models.DecimalField(**_DECIMAL_KW)
    insurance_fee = models.DecimalField(**_DECIMAL_KW)
    vat_fee = models.DecimalField(**_DECIMAL_KW)
    return_fee = models.DecimalField(**_DECIMAL_KW)
    discount = models.DecimalField(**_DECIMAL_KW)
    total_fee = models.DecimalField(**_DECIMAL_KW)
    weight = models.IntegerField(default=0, help_text="gram")
    length = models.IntegerField(default=0, help_text="mm")
    width = models.IntegerField(default=0, help_text="mm")
    height = models.IntegerField(default=0, help_text="mm")
    journey_type = models.IntegerField(default=1)
    service_id = models.IntegerField(default=12491)
    pickup_type = models.IntegerField(default=2)
    request_payload = models.JSONField(default=dict, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "best_shipments"
        indexes = [
            models.Index(fields=["status"], name="best_shipment_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.merchant_order_code} ({self.code_best or '-'})"


class BestWebhookEvent(models.Model):
    account = models.ForeignKey(
        BestExpressAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_events",
    )
    code_best = models.CharField(max_length=100, blank=True, default="")
    merchant_order_code = models.CharField(max_length=100, blank=True, default="", db_index=True)
    best_status_code = models.CharField(max_length=32, blank=True, default="")
    payload = models.JSONField(default=dict)
    modified_at_ms = models.BigIntegerField(default=0)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "best_webhook_events"
        indexes = [
            # Purge theo tuổi (task retention) + tra cứu event lỗi/chưa khớp để xử lý lại.
            models.Index(fields=["received_at"], name="best_event_received_idx"),
            models.Index(
                fields=["processed", "received_at"],
                name="best_event_unprocessed_idx",
                condition=Q(processed=False),
            ),
        ]
        constraints = [
            # KHÔNG kèm `modified_at_ms > 0`: event thiếu/parse lỗi `modified_at` được lưu
            # với ts=0, nếu loại chúng khỏi constraint thì mỗi lần Best retry và mỗi lần
            # `trace()` re-store traceLogs lại nhân bản một dòng y hệt — đúng thứ constraint
            # sinh ra để chặn (audit 2026-07-14 vòng 2). Hai event cùng (mã, trạng thái) mà
            # đều không có mốc thời gian là không phân biệt được, gộp làm một là đúng.
            models.UniqueConstraint(
                fields=["code_best", "best_status_code", "modified_at_ms"],
                name="best_webhook_event_uniq",
                condition=Q(code_best__gt=""),
            ),
            # Giai đoạn Best chưa cấp `code_best`, event chỉ có mã đơn của mình.
            models.UniqueConstraint(
                fields=["merchant_order_code", "best_status_code", "modified_at_ms"],
                name="best_webhook_event_merchant_uniq",
                condition=Q(code_best="") & Q(merchant_order_code__gt=""),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code_best or self.merchant_order_code} [{self.best_status_code}]"
