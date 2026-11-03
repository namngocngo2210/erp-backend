from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class StockState(models.TextChoices):
    SELLABLE = "sellable", "Bán được"
    WARRANTY = "warranty", "Bảo hành"
    DEFECT = "defect", "Lỗi"


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Nhập kho"
    ISSUE = "issue", "Xuất kho"
    ADJUSTMENT = "adjustment", "Điều chỉnh"
    TRANSFER_IN = "transfer_in", "Chuyển đến"
    TRANSFER_OUT = "transfer_out", "Chuyển đi"
    SALE = "sale", "Bán hàng"
    SALE_REVERSAL = "sale_reversal", "Hoàn bán"
    RETURN_IN = "return_in", "Nhập trả hàng"
    RECLASS_IN = "reclass_in", "Chuyển trạng thái (vào)"
    RECLASS_OUT = "reclass_out", "Chuyển trạng thái (ra)"
    DISPOSAL = "disposal", "Xuất huỷ/gửi lại"


class MovementReferenceType(models.TextChoices):
    """Loại chứng từ nguồn của một movement (audit 2026-07 mục 3.7).

    Python-level choices để chặn typo khi code gán — KHÔNG có check constraint DB
    (migration no-op) nên data lịch sử không bị ảnh hưởng.
    """

    ORDER = "order", "Đơn hàng"
    DOCUMENT = "document", "Phiếu kho"
    # "return" là giá trị LỊCH SỬ (trước khi tách phiếu ngày). Migration
    # 0022_return_entry trỏ lại toàn bộ movement cũ ("return", note_id) →
    # ("return_entry", entry_id), nên KHÔNG code path nào còn đọc/ghi giá trị này.
    # Giữ member lại chỉ vì trên prod data cũ vẫn tồn tại tới lúc 0022 chạy xong —
    # đừng dùng cho movement mới.
    RETURN = "return", "Phiếu trả hàng"
    RETURN_ENTRY = "return_entry", "Nhóm đơn hoàn"
    RECLASS = "reclass", "Chuyển trạng thái tồn"


class StockMovement(models.Model):
    """Immutable stock ledger — the source of truth. Append-only: never updated
    or deleted; corrections are recorded as reversing movements.

    Ngoại lệ DUY NHẤT: migration một lần có thể trỏ lại con trỏ chứng từ
    (reference_type/reference_id) khi tái cấu trúc model nguồn — ví dụ 0022_return_entry đổi
    ("return", note_id) → ("return_entry", entry_id). Số liệu kho (qty_delta,
    balance_after) thì tuyệt đối không được sửa.
    """

    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="stock_movements"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_movements"
    )
    qty_delta = models.IntegerField()
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    state = models.CharField(
        max_length=20, choices=StockState.choices, default=StockState.SELLABLE
    )
    balance_after = models.IntegerField()
    reference_type = models.CharField(
        max_length=20, null=True, blank=True, choices=MovementReferenceType.choices
    )
    reference_id = models.BigIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Ngày NGHIỆP VỤ: lúc hàng thực sự rời/về kho. Lệch `created_at` khi mã quét
    # ngày D chỉ khớp được đơn ở ngày D+n (đơn KiotViet về muộn / quét sát nửa
    # đêm). MỌI báo cáo theo ngày đọc cột này; `created_at` giữ hai vai trò
    # KHÁC: dấu vết audit "ghi sổ lúc nào" và PARTITION KEY của bảng — đừng đụng.
    # Bất biến: occurred_at <= created_at (ép ở StockCommandService).
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "stock_movements"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["warehouse", "product", "created_at"],
                name="stockmove_wh_prod_idx",
            ),
            models.Index(
                fields=["reference_type", "reference_id"], name="stockmove_ref_idx"
            ),
            models.Index(
                fields=["reference_type", "movement_type", "created_at"],
                name="stockmove_ref_type_created_idx",
            ),
            models.Index(
                fields=["reference_type", "movement_type", "occurred_at"],
                name="stockmove_reftype_occurred_idx",
            ),
        ]

    def __str__(self):
        return f"{self.movement_type} {self.qty_delta:+d} w{self.warehouse_id} p{self.product_id}"


class StockItem(models.Model):
    """Current on-hand balance per (warehouse, product). A projection of the
    ledger, updated atomically with each movement. May go negative (overselling
    is allowed with a warning — see spec §4.2)."""

    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="stock_items"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_items"
    )
    state = models.CharField(
        max_length=20, choices=StockState.choices, default=StockState.SELLABLE
    )
    quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stock_items"
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "product", "state"],
                name="uniq_stock_item_wh_prod_state",
            )
        ]
        indexes = [
        ]

    def __str__(self):
        return f"w{self.warehouse_id} p{self.product_id} = {self.quantity}"


class DocType(models.TextChoices):
    RECEIPT = "receipt", "Nhập kho"
    ISSUE = "issue", "Xuất kho"
    ADJUSTMENT = "adjustment", "Điều chỉnh"
    TRANSFER = "transfer", "Chuyển kho"


class DocStatus(models.TextChoices):
    DRAFT = "draft", "Nháp"
    PENDING_APPROVAL = "pending_approval", "Chờ duyệt"
    POSTED = "posted", "Đã ghi sổ"
    CANCELLED = "cancelled", "Đã huỷ"


class StockDocument(models.Model):
    code = models.CharField(max_length=32, unique=True)
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    source_warehouse = models.ForeignKey(
        "warehouses.Warehouse",
        on_delete=models.PROTECT,
        related_name="source_documents",
        null=True,
        blank=True,
    )
    dest_warehouse = models.ForeignKey(
        "warehouses.Warehouse",
        on_delete=models.PROTECT,
        related_name="dest_documents",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20, choices=DocStatus.choices, default=DocStatus.DRAFT
    )
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)
    # Mốc nghiệp vụ = lúc hàng thực rời/về kho, tách khỏi `created_at` (lúc lập phiếu)
    # và `posted_at` (lúc duyệt). None → bút toán lấy now() lúc duyệt: giữ nguyên hành
    # vi của mọi phiếu tay. Phiếu sinh tự động (hàng gửi bù) set = ngày kho QUÉT để sản
    # lượng rơi đúng ngày làm việc dù phiếu được duyệt muộn.
    occurred_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_stock_documents",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_stock_documents",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "stock_documents"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["doc_type", "status"], name="stockdoc_type_status_idx"
            ),
            models.Index(fields=["created_at"], name="stockdoc_created_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.doc_type}/{self.status})"


class StockDocumentAttachment(models.Model):
    document = models.ForeignKey(
        StockDocument, on_delete=models.CASCADE, related_name="attachments"
    )
    file_key = models.CharField(max_length=300)
    content_type = models.CharField(max_length=100)
    original_name = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_stock_doc_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_document_attachments"
        ordering = ["uploaded_at", "id"]
        indexes = [
            models.Index(
                fields=["document", "uploaded_at"], name="stockdoc_attach_doc_idx"
            ),
        ]

    def __str__(self):
        return f"doc{self.document_id} {self.original_name or self.file_key}"


class StockDocumentLine(models.Model):
    document = models.ForeignKey(
        StockDocument, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_doc_lines"
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        db_table = "stock_document_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "product"], name="uniq_stock_doc_line"
            ),
            # Serializer đã ép min_value=1 nhưng caller nội bộ (pending scan tạo
            # phiếu bù) đi thẳng service — dòng qty=0 sẽ nổ khó hiểu lúc DUYỆT
            # ("qty_delta must be non-zero"). Chốt ở DB, đồng bộ `ReturnNoteLine`.
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="stockdocline_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"doc{self.document_id} p{self.product_id} x{self.quantity}"


class ReturnType(models.TextChoices):
    DELIVERY_RETURNED = "delivery_returned", "Hàng hoàn"
    EXTERNAL_RETURN = "external_return", "Trả hàng ngoài"
    WARRANTY = "warranty", "Bảo hành"
    DEFECT = "defect", "Đổi lỗi"


class ReturnStatus(models.TextChoices):
    DRAFT = "draft", "Nháp"
    POSTED = "posted", "Đã ghi sổ"
    CANCELLED = "cancelled", "Đã huỷ"


class ReturnNoteStatus(models.TextChoices):
    OPEN = "open", "Đang mở"
    CLOSED = "closed", "Đã chốt"


class ReturnNote(models.Model):
    """Phiếu trả hàng NGÀY: 1 phiếu / ngày / kho. Chứng từ gom; mọi ngữ nghĩa
    theo đơn (tồn, trạng thái đơn) nằm ở ReturnEntry bên dưới."""

    code = models.CharField(max_length=32, unique=True)
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="return_notes"
    )
    # Ngày chứng từ theo GIỜ VN — không suy ra từ created_at (UTC) khi đọc.
    doc_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=ReturnNoteStatus.choices, default=ReturnNoteStatus.OPEN
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_return_notes",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_return_notes"
        ordering = ["-doc_date", "-id"]
        indexes = [
            models.Index(fields=["doc_date"], name="returnnote_docdate_idx"),
            models.Index(
                fields=["warehouse", "doc_date"], name="returnnote_wh_date_idx"
            ),
        ]
        constraints = [
            # Mỗi (ngày, kho) tối đa 1 phiếu ĐANG MỞ — chặn ở DB để race lúc quét
            # đồng loạt không đẻ 2 phiếu. Phiếu đã chốt không tính.
            models.UniqueConstraint(
                fields=["doc_date", "warehouse"],
                condition=Q(status="open"),
                name="returnnote_open_day_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.status})"


class ReturnEntry(models.Model):
    """Nhóm đơn hoàn trong phiếu ngày — đơn vị ghi sổ/huỷ thật sự.

    Giữ nguyên ngữ nghĩa của ReturnNote cũ (1 đơn ↔ 1 lần hoàn), chỉ không còn
    mã phiếu riêng.
    """

    return_note = models.ForeignKey(
        ReturnNote, on_delete=models.CASCADE, related_name="entries"
    )
    source_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_entries",
    )
    shipping_code = models.CharField(max_length=64, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_phone = models.CharField(max_length=32, blank=True, default="")
    source_note = models.TextField(blank=True, default="")
    note = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.DRAFT
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    # Trạng thái đơn nguồn ngay TRƯỚC khi ghi sổ đặt đơn = RETURNED.
    order_status_before = models.IntegerField(null=True, blank=True)
    scan_batch_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_return_entries"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["shipping_code"], name="returnentry_shipcode_idx"),
            models.Index(fields=["status"], name="returnentry_status_idx"),
            models.Index(fields=["scan_batch_id"], name="returnentry_batch_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["shipping_code"],
                condition=Q(shipping_code__gt="")
                & Q(source_order__isnull=True)
                & Q(status="draft"),
                name="returnentry_shipcode_draft_uniq",
            ),
        ]

    def __str__(self):
        return f"entry#{self.id} note{self.return_note_id} ({self.status})"


class ReclassAction(models.TextChoices):
    TO_SELLABLE = "to_sellable", "Chuyển về bán được"
    SEND_BACK = "send_back", "Gửi lại khách"
    SCRAP = "scrap", "Huỷ hàng lỗi"


class StockReclassLog(models.Model):
    """Nhật ký thao tác xử lý tồn warranty/defect — reference đích cho các
    movement reclass/disposal (trước đây reference_id trỏ product_id, không
    truy vết được thao tác nào)."""

    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="reclass_logs"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="reclass_logs"
    )
    from_state = models.CharField(max_length=20, choices=StockState.choices)
    action = models.CharField(max_length=20, choices=ReclassAction.choices)
    quantity = models.PositiveIntegerField()
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_reclass_logs"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["warehouse", "product", "created_at"],
                name="reclasslog_wh_prod_idx",
            ),
        ]

    def __str__(self):
        return f"{self.action} {self.quantity} w{self.warehouse_id} p{self.product_id}"


class ReturnNoteLine(models.Model):
    entry = models.ForeignKey(
        ReturnEntry, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.PositiveIntegerField()
    # Loại hoàn BẮT BUỘC ở dòng — phiếu ngày không còn loại để kế thừa.
    return_type = models.CharField(max_length=20, choices=ReturnType.choices)
    condition_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "stock_return_note_lines"
        constraints = [
            models.UniqueConstraint(fields=["entry", "product"], name="uniq_return_line"),
            models.CheckConstraint(
                condition=Q(return_type__in=ReturnType.values),
                name="returnline_return_type_valid",
            ),
            # quantity=0 dòng vô nghĩa (không hoàn gì) — đường nhập nào cũng đã
            # chặn ở serializer/service (min_value=1); constraint là chốt cuối.
            # Audit followup 2026-07 mục 4.3.
            models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name="returnline_quantity_positive",
            ),
        ]


class PendingScanKind(models.TextChoices):
    DISPATCH = "dispatch", "Xuất giao"
    RETURN = "return", "Nhập hoàn"


class PendingScanStatus(models.TextChoices):
    PENDING = "pending", "Chờ khớp"
    MATCHED = "matched", "Đã khớp"
    FAILED = "failed", "Lỗi"
    INVALID = "invalid", "Mã không đúng"
    # Đơn về ở trạng thái HỦY → cron không được tự xuất kho; giữ mã lại cho người xử lý.
    CANCELLED = "cancelled", "Đơn đã hủy"


class PendingShipmentScan(models.Model):
    """Mã vận đơn đã quét nhưng chưa có đơn trong hệ thống (chờ cron KiotViet sync).

    Cron ``match_pending_shipment_scans`` quét bảng này mỗi 60s: có đơn →
    tự hoàn tất xuất kho / tạo phiếu trả nháp như thao tác tay lúc quét.
    """

    code = models.CharField(max_length=64)
    kind = models.CharField(max_length=10, choices=PendingScanKind.choices)
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="pending_scans"
    )
    # Chỉ dùng cho kind=return; trống = mặc định delivery_returned khi tạo phiếu.
    return_type = models.CharField(
        max_length=20, choices=ReturnType.choices, blank=True, default=""
    )
    # Đợt quét gốc — phiếu trả tạo ra khi khớp sẽ kế thừa để về đúng đợt.
    scan_batch_id = models.UUIDField(null=True, blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(
        max_length=10, choices=PendingScanStatus.choices, default=PendingScanStatus.PENDING
    )
    matched_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pending_scans",
    )
    # Mã phiếu trả tạo ra khi khớp (kind=return); rỗng với kind=dispatch.
    result_ref = models.CharField(max_length=32, blank=True, default="")
    error = models.TextField(blank=True, default="")
    # Ghi chú nhập tay trên dòng chờ khớp; kế thừa sang phiếu trả khi khớp (kind=return).
    note = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    # "Vé xử lý" cho nhánh dispatch: set trong transaction #1 TRƯỚC khi nhả lock
    # đi gọi KiotViet; đường kia (cron ↔ manual match) thấy claim còn hạn thì từ
    # chối thay vì dispatch một đơn khác từ cùng 1 lần quét. Finalize giải phóng;
    # worker chết giữa chừng thì claim tự hết hạn (TTL trong service).
    claimed_at = models.DateTimeField(null=True, blank=True)
    # THU HỒI dòng quét: đặt khi lần nhập hoàn tương ứng bị huỷ. Dòng đã thu hồi
    # biến mất khỏi màn hình + file Excel và KHÔNG còn giữ mã (quét lại được) —
    # nhưng vẫn nằm trong DB để tra ai đã bắn nhầm, bắn lúc nào.
    #
    # Cố ý KHÔNG tái dùng `PendingScanStatus.CANCELLED`: trạng thái đó mang nghĩa
    # "đơn về ở trạng thái huỷ" và kho PHẢI thấy để đối chiếu (nằm trong
    # `_LIST_STATUSES`). Nhồi hai nghĩa vào một cột là bẫy cho lần sửa sau.
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_pending_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pending_shipment_scans"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="pendingscan_status_created"),
            models.Index(fields=["code"], name="pendingscan_code_idx"),
            # Phép kiểm "mã này còn lần bắn nào sống không" chạy trên HOT PATH quét
            # kho (mỗi lần bắn 1 lượt). Partial index chỉ phủ dòng còn sống nên nhỏ
            # và không phình theo lịch sử đã thu hồi.
            models.Index(
                fields=["kind", "code"],
                condition=Q(voided_at__isnull=True),
                name="pendingscan_live_kind_code",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "kind"],
                condition=Q(status="pending"),
                name="uniq_pending_scan_code_kind",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.kind}/{self.status})"


class WatchStatus(models.TextChoices):
    WAITING = "waiting", "Chờ hàng về"
    RETURNED = "returned", "Hàng đã về"
    CLOSED = "closed", "Đã đóng"


class CloseReason(models.TextChoices):
    CUSTOMER_KEPT = "customer_kept", "Khách đã nhận và giữ hàng"
    LOST_IN_TRANSIT = "lost_in_transit", "Thất lạc, đơn vị vận chuyển đền"
    DISPOSED_OUTSIDE = "disposed_outside", "Huỷ tại chỗ, không trả về"
    OTHER = "other", "Khác"
    # Lý do HỆ THỐNG tự ghi, không nằm trong dropdown đóng tay: sổ xuất đã chuyển
    # sang đơn xuất lại `<mã>.NN` nên đơn cũ không còn nợ kho. KHÔNG dùng
    # WatchStatus.RETURNED cho ca này — hàng không hề quay về kho, đếm vào "hàng
    # đã về" là nói dối báo cáo. Xem `AwaitingReturnService.MANUAL_CLOSE_REASONS`.
    REISSUED = "reissued", "Sổ xuất đã chuyển sang đơn xuất lại"


class CancelledDispatchWatch(models.Model):
    """Hàng đợi công việc: đơn đã huỷ nhưng hàng còn ngoài kho.

    KHÔNG phải nguồn số. Số lượng còn nợ luôn đọc từ sổ (`outstanding_by_key`);
    bảng này chỉ trả lời "đơn nào cần theo dõi" và giữ dấu vết quyết định đóng.
    Có bảng riêng vì dựng danh sách thuần từ ledger phải gộp `stock_movements`
    trên toàn bộ đơn mỗi lần mở tab.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="cancelled_dispatch_watch"
    )
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    cancelled_at = models.DateTimeField()
    status = models.CharField(
        max_length=10, choices=WatchStatus.choices, default=WatchStatus.WAITING
    )
    closed_reason = models.CharField(
        max_length=20, choices=CloseReason.choices, blank=True, default=""
    )
    closed_note = models.TextField(blank=True, default="")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cancelled_dispatch_watches"
        ordering = ["cancelled_at", "id"]
        indexes = [
            models.Index(
                fields=["status", "cancelled_at"], name="cancelwatch_status_idx"
            ),
        ]

    def __str__(self):
        return f"watch order#{self.order_id} ({self.status})"


class StockFlowNoteTarget(models.TextChoices):
    ORDER_DISPATCH = "order_dispatch", "Dòng đơn xuất"
    AWAITING_RETURN = "awaiting_return", "Dòng chờ hàng hoàn về"


class StockFlowNote(models.Model):
    """Ghi chú tự do cho dòng trang Lịch sử nhập xuất KHÔNG có chỗ lưu sẵn.

    Ba loại dòng khác của trang (mã quét chờ khớp / nhóm đơn hoàn / phiếu kho)
    đã có field `note` riêng và KHÔNG đi qua bảng này — điều hướng nằm ở
    `FlowNoteService.update`.

    Cùng một `order_id` có thể có HAI ghi chú độc lập: một cho dòng ở tab Xuất
    (`order_dispatch`), một cho dòng ở tab Chờ hàng hoàn về (`awaiting_return`)
    — hai tình huống nghiệp vụ khác nhau (xem docs/flow-history-notes.md §2).

    CỐ Ý không FK sang `orders`: đây là sổ ghi chú của module kho, gắn FK sẽ kéo
    phụ thuộc ngược tầng và chặn xoá đơn. Dòng mồ côi vô hại.
    """

    target_type = models.CharField(max_length=32, choices=StockFlowNoteTarget.choices)
    target_id = models.BigIntegerField()
    note = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stock_flow_notes"
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_id"], name="stock_flow_note_target_uniq"
            )
        ]

    def __str__(self):
        return f"note {self.target_type}#{self.target_id}"
