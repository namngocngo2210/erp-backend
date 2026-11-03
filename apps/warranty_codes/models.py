from django.db import models


class WarrantyCodeBatchStatus(models.TextChoices):
    RESERVED = "reserved", "Chưa gắn sản phẩm"
    ASSIGNED = "assigned", "Đã gắn sản phẩm"


class WarrantyCodeCounter(models.Model):
    """Bảng 1 dòng cấp phát dải seq toàn cục (lock bằng select_for_update)."""

    last_seq = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "warranty_code_counter"


class WarrantyCodeBatch(models.Model):
    batch_code = models.CharField(max_length=32, unique=True)
    start_seq = models.BigIntegerField()
    end_seq = models.BigIntegerField()
    quantity = models.PositiveIntegerField()
    # Seq lớn nhất ĐÃ có dòng trong warranty_codes. Lô lớn sinh mã nền theo chunk
    # (task generate_batch_codes), mỗi chunk chốt cột này cùng transaction → worker
    # chết giữa chừng thì chạy lại nối tiếp từ generated_seq + 1, không trùng dải.
    # generated_seq < end_seq = lô đang sinh (khoá gán SP / tách / xuất).
    generated_seq = models.BigIntegerField()
    status = models.CharField(
        max_length=16, choices=WarrantyCodeBatchStatus.choices, default=WarrantyCodeBatchStatus.RESERVED
    )
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    created_by = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_by = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "warranty_code_batches"
        indexes = [
            models.Index(fields=["status", "created_at"], name="wc_batch_status_created_idx"),
            models.Index(fields=["product", "created_at"], name="wc_batch_product_idx"),
        ]

    @property
    def is_generating(self) -> bool:
        return self.generated_seq < self.end_seq

    @property
    def generated_count(self) -> int:
        return max(0, self.generated_seq - self.start_seq + 1)


class WarrantyCode(models.Model):
    seq = models.BigIntegerField(unique=True)
    serial = models.CharField(max_length=20, unique=True)
    code = models.CharField(max_length=10, unique=True)
    batch = models.ForeignKey(WarrantyCodeBatch, on_delete=models.PROTECT, related_name="codes")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Số PHIÊN quét trang sản phẩm (§3.3 design). Không đếm từ warranty_code_scan_logs
    # vì task retention xoá log > 180 ngày — số sẽ tụt dần và khách thấy sai. Counter
    # ở đây là dữ liệu vĩnh viễn, và rẻ hơn một COUNT(*) mỗi lượt xem trang.
    scan_count = models.PositiveIntegerField(default=0)
    first_scanned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "warranty_codes"
        # wc_code_batch_idx (trùng FK auto batch_id) và wc_code_like_idx /
        # wc_serial_like_idx (unique=True trên varchar đã tự sinh index
        # `_like` varchar_pattern_ops) đã bỏ — audit 2026-09-23 T2, 37 cặp trùng.
        indexes = [
            models.Index(fields=["product", "created_at"], name="wc_code_product_idx"),
        ]


class PortalChannel(models.TextChoices):
    WEB = "web", "Website"
    ZALO_OA = "zalo_oa", "Zalo OA"


class ScanResult(models.TextChoices):
    ACTIVATED = "activated", "Kích hoạt thành công"
    ALREADY_ACTIVATED = "already_activated", "Mã đã được kích hoạt"
    NOT_FOUND = "not_found", "Không tìm thấy mã"
    VIEWED = "viewed", "Xem lại"
    PAGE_VIEWED = "page_viewed", "Xem trang sản phẩm"


class WarrantyActivation(models.Model):
    """Một mã bảo hành chỉ kích hoạt được đúng một lần (unique code)."""

    code = models.OneToOneField(
        WarrantyCode, on_delete=models.PROTECT, related_name="activation"
    )
    # Từ 08/08: SĐT quay lại BẮT BUỘC khi kích hoạt, nhưng chỉ là thông tin đăng
    # ký bảo hành điện tử cho CSKH — KHÔNG dùng để xác thực quyền sở hữu (mã dưới
    # lớp cào đã đủ). SĐT của lần kích hoạt ĐẦU TIÊN là chuẩn, không bị ghi đè
    # bởi các lần "xem lại" sau. blank=True giữ để đọc được bản ghi cũ (giai đoạn
    # 07/08 khi cột này còn rỗng). Xem docs/warranty-portal-design.md §4.1.
    phone = models.CharField(max_length=20, blank=True, default="")
    channel = models.CharField(max_length=16, choices=PortalChannel.choices, default=PortalChannel.WEB)
    activated_at = models.DateTimeField()
    expires_at = models.DateField()
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "warranty_activations"
        indexes = [
            models.Index(fields=["phone", "activated_at"], name="wc_activation_phone_idx"),
            models.Index(fields=["activated_at"], name="wc_activation_time_idx"),
        ]


class WarrantyCodeScanLog(models.Model):
    """Nhật ký mọi lần khách nhập/quét mã — kể cả mã sai (phát hiện tem giả)."""

    keyword = models.CharField(max_length=64)
    code = models.ForeignKey(
        WarrantyCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="scan_logs"
    )
    product_sku = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    result = models.CharField(max_length=24, choices=ScanResult.choices)
    channel = models.CharField(max_length=16, choices=PortalChannel.choices, default=PortalChannel.WEB)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "warranty_code_scan_logs"
        indexes = [
            models.Index(fields=["created_at"], name="wc_scanlog_created_idx"),
            models.Index(fields=["result", "created_at"], name="wc_scanlog_result_idx"),
            models.Index(fields=["ip_address", "created_at"], name="wc_scanlog_ip_idx"),
            models.Index(fields=["keyword"], name="wc_scanlog_kw_like_idx", opclasses=["varchar_pattern_ops"]),
        ]
