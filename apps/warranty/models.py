from dateutil.relativedelta import relativedelta
from django.db import models
from django.utils import timezone

from apps.common.phone_utils import normalize_phone


def normalize_phone_keep_raw(value: str | None) -> str:
    """normalize_phone() nhưng KHÔNG xoá dữ liệu: chuỗi không ra SĐT VN hợp lệ
    (normalize trả "") thì giữ nguyên bản strip. Dùng cho cột SĐT đơn lẻ của
    warranty — khác Customer/Order vốn có cặp phone_raw/phone_normalized."""
    raw = (value or "").strip()
    return normalize_phone(raw) or raw


class WarrantyClaimStatus(models.IntegerChoices):
    """Trạng thái phiếu BH theo flow CSKH — mã 1-11 đánh theo thứ tự flow, NV chọn tay tự do."""

    WAITING_CUSTOMER_CONFIRM = 1, "Đợi khách XN"
    WAITING_ORDER_CREATE = 2, "Chờ tạo đơn"
    WAITING_SHIPMENT = 3, "Chờ vận chuyển"
    REPAIRING = 4, "Đang sửa chữa"
    WAITING_RESOLUTION = 5, "Chờ chốt phương án"
    WAITING_RESEND_ORDER = 6, "Chờ tạo đơn gửi lại"
    RESENDING = 7, "Đang gửi lại khách"
    WAITING_FEEDBACK = 8, "Xin feedback"
    COMPLETED = 9, "Hoàn thành"
    LOST = 10, "Thất lạc"
    CANCELLED = 11, "Hủy"
    # Thêm sau (2026-09): mã 12 để không phải remap dữ liệu cũ; UI xếp giữa 3 và 4.
    IN_TRANSIT = 12, "Đang vận chuyển"


class WarrantyClaim(models.Model):
    """Phiếu bảo hành nội bộ 1-1 với một đơn. Section 1 đọc live qua FK order."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="warranty_claim"
    )
    customer_report_date = models.DateField(null=True, blank=True)  # MỚI
    activation_date = models.DateField(null=True, blank=True)
    warranty_months = models.PositiveSmallIntegerField(default=24)
    status = models.PositiveSmallIntegerField(
        choices=WarrantyClaimStatus.choices,
        default=WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM,
    )
    completed_at = models.DateTimeField(null=True, blank=True)  # MỚI
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "warranty_claims"
        indexes = [
            models.Index(fields=["status"], name="warranty_claim_status_idx"),
            models.Index(fields=["created_at"], name="warranty_claim_created_idx"),
            models.Index(fields=["deleted_at"], name="warranty_claim_deleted_idx"),
        ]

    def __str__(self):
        return f"WarrantyClaim #{self.pk} (order={self.order_id})"

    @property
    def expiry_date(self):
        """Ngày hết hạn = ngày kích hoạt + số tháng bảo hành. None nếu chưa kích hoạt."""
        if not self.activation_date:
            return None
        return self.activation_date + relativedelta(months=self.warranty_months)

    @property
    def warranty_days_left(self):
        """Số ngày bảo hành còn lại tới hôm nay (âm = đã quá hạn). None nếu chưa kích hoạt."""
        expiry = self.expiry_date
        if expiry is None:
            return None
        return (expiry - timezone.localdate()).days


class JourneyStatus(models.TextChoices):
    NEW = "new", "Mới"
    CREATED = "created", "Đã lên đơn"
    PACKED = "packed", "Đã đóng hàng"
    SHIPPING = "shipping", "Đang giao"
    LOST = "lost", "Thất lạc"
    DELIVERED = "delivered", "GTC"
    PENDING_RETURN = "pending_return", "Chờ kho xác nhận"
    RETURNED = "returned", "Đã nhận hàng hoàn"


class JourneyClassification(models.TextChoices):
    SAME_PRODUCT = "same_product", "Cùng SP"
    DIFFERENT_PRODUCT = "different_product", "Khác SP"
    ACCESSORY = "accessory", "Phụ kiện"


class JourneyErrorCategory(models.TextChoices):
    PRODUCT = "product", "Sản phẩm"
    CUSTOMER = "customer", "Khách"
    SHIPPING = "shipping", "Vận chuyển"
    WAREHOUSE = "warehouse", "Kho"


class JourneyShippingForm(models.TextChoices):
    ONE_WAY = "one_way", "Đơn 1 chiều"
    EXCHANGE = "exchange", "Đơn đổi hàng"
    WARRANTY_RECALL = "warranty_recall", "Đơn thu hồi BH"
    RETURN_RECALL = "return_recall", "Đơn thu hồi trả hàng"
    WARRANTY_RETURN = "warranty_return", "Đơn trả BH"


class JourneyCarrier(models.TextChoices):
    GHN = "ghn", "GHN"
    BEST = "best", "BEST"
    VTP = "vtp", "VTP"


class JourneyInspectionResult(models.TextChoices):
    BROKEN_UNREPAIRABLE = "broken_unrepairable", "Hỏng - Ko sửa được"
    BROKEN_REPAIRABLE = "broken_repairable", "Hỏng - sửa được"
    NORMAL = "normal", "Dùng BT"
    WRONG_ITEM = "wrong_item", "Sai hàng"


class JourneyResolution(models.TextChoices):
    NEW_MACHINE = "new_machine", "Gửi máy mới"
    REWARRANTY = "rewarranty", "Gửi lại máy BH"
    EXCHANGE = "exchange", "Đổi SP khác"
    REFUND = "refund", "Hoàn tiền"


class JourneyWarehouseCheck(models.TextChoices):
    """Kho check hàng khi đã nhận hàng hoàn (status = returned)."""

    DAMAGED = "damaged", "Hàng hỏng"
    NORMAL = "normal", "Hàng dùng BT"


class JourneyReturnStatus(models.TextChoices):
    """Trạng thái giao đơn mới về khách (§7) — độc lập với `status` chính."""

    SHIPPING = "shipping", "Đang giao"
    DELIVERED = "delivered", "GTC"


class JourneyFeedbackAgree(models.TextChoices):
    """Khách có đồng ý để lại review công khai không."""

    YES = "yes", "Có"
    NO = "no", "Không"


class JourneyFeedbackRating(models.TextChoices):
    """Đánh giá của khách về trải nghiệm bảo hành."""

    GOOD = "good", "Tốt"
    AVERAGE = "average", "Trung bình"
    UNSATISFIED = "unsatisfied", "Không hài lòng"
    LOST_TRUST = "lost_trust", "Mất niềm tin"


class JourneyCarrierResponse(models.TextChoices):
    """Phản hồi của đơn vị vận chuyển cho khiếu nại thất lạc."""

    PENDING = "pending", "Chưa phản hồi"
    WORKING_WITH_POST = "working_with_post", "Chờ lv với bưu cục"
    AGREED = "agreed", "Chốt đền bù"
    REJECTED = "rejected", "Không được đền bù"


class JourneyCompensationStatus(models.TextChoices):
    UNPAID = "unpaid", "Chưa đền bù"
    PAID = "paid", "Đã đền bù"


class WarrantyJourney(models.Model):
    """Một hành trình của phiếu bảo hành (section 2–7). N hành trình / phiếu."""

    _MONEY = dict(max_digits=12, decimal_places=2, default=0)

    claim = models.ForeignKey(
        WarrantyClaim, on_delete=models.CASCADE, related_name="journeys"
    )
    seq = models.PositiveSmallIntegerField()
    # Tên hành trình do người dùng đặt; rỗng → UI hiển thị "Hành trình #<seq>".
    name = models.CharField(max_length=255, blank=True, default="")

    # §2 Người nhận
    receiver_name = models.CharField(max_length=255, blank=True, default="")
    receiver_phone = models.CharField(max_length=30, blank=True, default="")
    receiver_raw_address = models.TextField(blank=True, default="")

    # §3 SP & lý do
    # NOTE: brief/plan says "products.Product" but there is no `products` app in
    # this codebase — the real Product model lives in apps.catalog.models.Product
    # (matching name/sku fields the plan's serializers expect). Using that.
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    send_reason = models.TextField(blank=True, default="")
    classification = models.CharField(
        max_length=20, choices=JourneyClassification.choices, blank=True, default=""
    )
    error_category = models.CharField(
        max_length=20, choices=JourneyErrorCategory.choices, blank=True, default=""
    )

    # §4 Vận chuyển & chi phí
    shipping_form = models.CharField(
        max_length=20, choices=JourneyShippingForm.choices, blank=True, default=""
    )
    declared_value = models.DecimalField(**_MONEY)
    cod_amount = models.DecimalField(**_MONEY)
    carrier = models.CharField(
        max_length=10, choices=JourneyCarrier.choices, blank=True, default=""
    )
    shipping_fee = models.DecimalField(**_MONEY)
    order_date = models.DateField(null=True, blank=True)
    new_tracking_code = models.CharField(max_length=100, blank=True, default="")
    recall_code = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(blank=True, default="")

    # Trạng thái
    status = models.CharField(
        max_length=20, choices=JourneyStatus.choices, default=JourneyStatus.NEW
    )

    # §5 Kho nhận hàng BH
    warehouse_received_at = models.DateField(null=True, blank=True)
    inspection_result = models.CharField(
        max_length=20, choices=JourneyInspectionResult.choices, blank=True, default=""
    )
    # Kho check hàng — hiển thị ở UI khi status = returned (đã nhận hàng hoàn).
    warehouse_check = models.CharField(
        max_length=10, choices=JourneyWarehouseCheck.choices, blank=True, default=""
    )

    # §6 CSKH chốt phương án
    resolution = models.CharField(
        max_length=20, choices=JourneyResolution.choices, blank=True, default=""
    )
    resolution_declared_value = models.DecimalField(**_MONEY)
    resolution_cod = models.DecimalField(**_MONEY)
    resolution_note = models.TextField(blank=True, default="")

    # §7 Gửi đơn mới về khách
    return_carrier = models.CharField(
        max_length=10, choices=JourneyCarrier.choices, blank=True, default=""
    )
    return_shipping_fee = models.DecimalField(**_MONEY)
    return_order_date = models.DateField(null=True, blank=True)
    return_tracking_code = models.CharField(max_length=100, blank=True, default="")
    # Trạng thái giao đơn mới về khách — chặng riêng, không đụng `status` chính.
    return_status = models.CharField(
        max_length=10, choices=JourneyReturnStatus.choices, blank=True, default=""
    )

    # Khách nhận hàng — cảm nhận. Hiện khi chặng giao tới khách đã GTC
    # (quy tắc hiển thị nằm ở FE, xem journey-visibility.ts).
    feedback_agree_review = models.CharField(
        max_length=10, choices=JourneyFeedbackAgree.choices, blank=True, default=""
    )
    feedback_rating = models.CharField(
        max_length=20, choices=JourneyFeedbackRating.choices, blank=True, default=""
    )
    feedback_note = models.TextField(blank=True, default="")

    # Khiếu nại & đền bù — hiện khi có mã vận đơn rơi vào nhóm thất lạc.
    claim_date = models.DateField(null=True, blank=True)
    claim_note = models.TextField(blank=True, default="")
    carrier_response = models.CharField(
        max_length=20, choices=JourneyCarrierResponse.choices, blank=True, default=""
    )
    compensation_status = models.CharField(
        max_length=15, choices=JourneyCompensationStatus.choices, blank=True, default=""
    )
    compensation_amount = models.DecimalField(**_MONEY)

    # Trạng thái Best thô mới nhất theo TỪNG mã (cron trace 10' cập nhật) — nhóm
    # hiển thị suy từ đây qua apps.warranty.tracking_status (không lưu nhóm).
    new_tracking_best_status = models.CharField(max_length=32, blank=True, default="")
    recall_best_status = models.CharField(max_length=32, blank=True, default="")
    return_tracking_best_status = models.CharField(max_length=32, blank=True, default="")
    # Mốc `modified_at` (epoch ms) của trạng thái đang lưu, theo từng chặng —
    # chặn event Best tới muộn ghi đè trạng thái mới hơn. BigInteger vì epoch ms
    # tràn IntegerField 32-bit.
    new_tracking_best_status_ms = models.BigIntegerField(default=0)
    recall_best_status_ms = models.BigIntegerField(default=0)
    return_tracking_best_status_ms = models.BigIntegerField(default=0)
    tracking_synced_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "warranty_journeys"
        ordering = ["claim", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["claim", "seq"], name="warranty_journey_claim_seq_uniq"),
        ]
        indexes = [
            models.Index(fields=["status"], name="warranty_journey_status_idx"),
            models.Index(fields=["new_tracking_code"], name="warranty_journey_newtc_idx"),
            models.Index(fields=["return_tracking_code"], name="warranty_journey_rettc_idx"),
            # Thiếu index này khiến subscriber realtime (lọc OR qua cả 3 cột mã)
            # seq-scan toàn bảng trên MỖI webhook Best — kể cả đơn bán không
            # khớp gì. Xem review 2026-07-22 finding #3.
            models.Index(fields=["recall_code"], name="warranty_journey_recalltc_idx"),
        ]

    # 3 cột mã VC — subscriber realtime lọc bằng `__in` (exact match, giữ index
    # dùng được, xem review 2026-07-22 finding #3), còn cron trace so bằng
    # Python có `.strip()`. Chuẩn hoá ngay khi lưu để 2 đường không lệch nhau
    # với mã dính khoảng trắng thừa (finding #8) — rẻ hơn nhiều so với TRIM()
    # trong query, thứ sẽ làm mất index đang cần bảo vệ.
    _TRACKING_CODE_FIELDS = (
        "new_tracking_code",
        "recall_code",
        "return_tracking_code",
    )

    def save(self, *args, **kwargs):
        for field in self._TRACKING_CODE_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, value.strip())
        # SĐT người nhận chuẩn hoá về dạng nội bộ 0xxxxxxxxx như Customer/Order
        # (audit 2026-09-23 T2/P2-8) để đối chiếu với khách/đơn không lệch vì
        # "+84", dấu chấm, khoảng trắng. Chuỗi không ra SĐT hợp lệ thì giữ nguyên
        # (chỉ strip) — cột này là cột duy nhất, không có phone_raw để giữ bản gốc.
        self.receiver_phone = normalize_phone_keep_raw(self.receiver_phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"WarrantyJourney #{self.pk} (claim={self.claim_id} seq={self.seq})"


class WarrantyRegistration(models.Model):
    SOURCE_TIKTOK = "tiktok"
    SOURCE_SHOPEE = "shopee"
    SOURCE_FACEBOOK = "facebook"
    SOURCE_CHOICES = [
        (SOURCE_TIKTOK, "TikTok"),
        (SOURCE_SHOPEE, "Shopee"),
        (SOURCE_FACEBOOK, "Facebook"),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    invoice_code = models.CharField(max_length=100)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30)
    email = models.EmailField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "warranty_registrations"
        indexes = [
            models.Index(fields=["created_at"], name="warranty_created_at_idx"),
            models.Index(fields=["phone"], name="warranty_phone_idx"),
            models.Index(fields=["invoice_code"], name="warranty_invoice_idx"),
        ]

    def save(self, *args, **kwargs):
        # Chuẩn hoá SĐT như WarrantyJourney.receiver_phone (audit 2026-09-23 T2).
        self.phone = normalize_phone_keep_raw(self.phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_code} - {self.full_name}"
