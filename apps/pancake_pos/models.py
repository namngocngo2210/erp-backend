"""Bảng liên kết CRM ↔ Pancake POS và nhật ký lượt đồng bộ (§3.4).

Chỉ app này ghi các bảng dưới. Liên kết với `products` bằng FK chuỗi
("catalog.Product") — không import model của module khác.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models.functions import Now


class LinkStatus(models.TextChoices):
    PENDING = "pending", "Chờ đồng bộ"
    SYNCED = "synced", "Đã đồng bộ"
    CONFLICT = "conflict", "Xung đột"
    ERROR = "error", "Lỗi"
    SKIPPED = "skipped", "Bỏ qua"


class SyncRunMode(models.TextChoices):
    FULL = "full", "Đầy đủ"
    INCREMENTAL = "incremental", "Tăng dần"
    DRY_RUN = "dry_run", "Chạy thử"
    # Đẩy tồn đầu kỳ (two-way-sync §7 A1) — tách mode chạy thử/ghi để danh sách lượt
    # (không nạp `report`) vẫn phân biệt được và để tra "dry-run gần nhất" bằng index.
    INITIAL_STOCK_DRY_RUN = "initial_stock_dry", "Tồn đầu kỳ (chạy thử)"
    INITIAL_STOCK = "initial_stock", "Tồn đầu kỳ"


class PancakePosProductLink(models.Model):
    """1 dòng / nhóm CRM (hoặc SKU lẻ `sku:<SKU>`) ↔ 1 sản phẩm Pancake."""

    group_key = models.CharField(max_length=100, unique=True)
    pancake_product_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_product_links"

    def __str__(self) -> str:
        return f"{self.group_key} → {self.pancake_product_id or '?'}"


class PancakePosVariationLink(models.Model):
    """1 dòng / SKU CRM ↔ 1 mẫu mã Pancake."""

    # CASCADE: xoá cứng sản phẩm CRM thì liên kết mất nghĩa (sản phẩm có soft-delete
    # nên thực tế hiếm khi xảy ra).
    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="pancake_pos_link"
    )
    product_link = models.ForeignKey(
        PancakePosProductLink, on_delete=models.CASCADE, related_name="variation_links"
    )
    pancake_variation_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=LinkStatus.choices, default=LinkStatus.PENDING)
    last_error = models.TextField(blank=True, default="")
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    synced_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_variation_links"
        indexes = [
            # Hàng đợi việc cần làm/cần người xem — phần lớn dòng là `synced` nên partial.
            models.Index(
                fields=["status"],
                name="ppos_varlink_todo_idx",
                condition=models.Q(status__in=["pending", "error", "conflict"]),
            ),
        ]

    def __str__(self) -> str:
        return f"product {self.product_id} → {self.pancake_variation_id or '?'} ({self.status})"


class PancakePosSyncRun(models.Model):
    """Nhật ký mỗi lượt đồng bộ. `report` chứa báo cáo dry-run (mẫu payload, lý do loại)
    hoặc lỗi chặn cả lượt (`fatal`)."""

    mode = models.CharField(max_length=20, choices=SyncRunMode.choices)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    linked = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    conflicts = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    # Số mẫu mã CRM đã ẩn trên Pancake (xoá mềm / `never` / hết đủ điều kiện — §3.5).
    hidden = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=dict, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "pancake_pos_sync_runs"
        ordering = ("-started_at", "-id")
        indexes = [
            models.Index(fields=["-started_at"], name="ppos_syncrun_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.mode} @ {self.started_at:%Y-%m-%d %H:%M}"


class PancakePosDirtyProduct(models.Model):
    """Outbox nhẹ cho đồng bộ tăng dần (§3.5): sản phẩm CRM vừa đổi, chờ beat đẩy.

    Không FK tới `products`: sản phẩm có thể đã xoá cứng khi beat chạy, và dòng
    dirty chỉ là tín hiệu "xem lại id này". Ghi bằng `INSERT … ON CONFLICT DO
    NOTHING` (giữ `marked_at` cũ → FIFO), lấy ra bằng `DELETE … FOR UPDATE SKIP
    LOCKED RETURNING` (repositories.DirtyQueue).
    """

    product_id = models.BigIntegerField(primary_key=True)
    marked_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "pancake_pos_dirty_products"
        indexes = [models.Index(fields=["marked_at"], name="ppos_dirty_marked_idx")]

    def __str__(self) -> str:
        return f"dirty product {self.product_id}"


class PancakePosWarehouseLink(models.Model):
    """Ghép kho CRM ↔ kho Pancake (two-way-sync §3): 1–1 cả hai phía, ~3 dòng.

    `priority` (nhỏ = ưu tiên trước) và `is_default_for_push` dùng khi chọn kho xuất
    cho đơn CRM đẩy sang Pancake (A2, §6b). Tối đa MỘT kho mặc định (partial unique).
    """

    # CASCADE: kho CRM thực tế chỉ xoá mềm; xoá cứng thì liên kết mất nghĩa.
    warehouse = models.OneToOneField("warehouses.Warehouse", on_delete=models.CASCADE, related_name="+")
    pancake_warehouse_id = models.CharField(max_length=64, unique=True)
    # Tên kho Pancake lúc ghép (cache để hiển thị khi chưa gọi được Pancake).
    pancake_warehouse_name = models.CharField(max_length=255, blank=True, default="")
    is_default_for_push = models.BooleanField(default=False)
    priority = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_warehouse_links"
        ordering = ("priority", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default_for_push"],
                condition=models.Q(is_default_for_push=True),
                name="ppos_whlink_one_default",
            ),
        ]

    def __str__(self) -> str:
        return f"warehouse {self.warehouse_id} → {self.pancake_warehouse_id}"


class PancakePosCategory(models.Model):
    """Cache id danh mục Pancake do CRM quản lý (hiện có `component` = "Linh kiện",
    two-way-sync §6b). Tạo trên Pancake một lần rồi dùng lại id."""

    key = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    pancake_category_id = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_categories"

    def __str__(self) -> str:
        return f"{self.key} → {self.pancake_category_id}"


# ── đẩy đơn CRM → Pancake (two-way-sync §3, §4.1, §6b, §7 A2) ─────────────────
class OrderLinkOrigin(models.TextChoices):
    CRM_PUSH = "crm_push", "CRM đẩy sang"
    PANCAKE_NATIVE = "pancake_native", "Đơn gốc Pancake"


class OrderLinkStatus(models.TextChoices):
    PENDING = "pending", "Chờ đẩy"
    PUSHING = "pushing", "Đang đẩy"
    PUSHED = "pushed", "Đã đẩy"
    UPDATED = "updated", "Đã cập nhật"
    CANCELLED = "cancelled", "Đã huỷ"
    ERROR = "error", "Lỗi"
    NEEDS_ROUTING = "needs_routing", "Cần chọn cách xuất"
    SKIPPED_DUPLICATE = "skipped_duplicate", "Bỏ qua — trùng"
    # Đơn gốc Pancake (sàn/page) đã khớp/tạo đơn CRM qua webhook (B1). Không thuộc
    # luồng đẩy: không nằm trong LIVE_STATUSES → `sync_changes` không PUT ngược.
    LINKED = "linked", "Đã khớp đơn Pancake"


class PancakePosOrderLink(models.Model):
    """Một đơn Pancake (hoặc một dự định đẩy) của một đơn CRM — 1 CRM ↔ N Pancake (§6b).

    Mỗi dòng = một "kiện" (`part_no`); đơn không tách chỉ có part 1. `custom_id` gửi
    sang Pancake = mã tham chiếu CRM (`<order>` / `<order>-<part>`, cũng là mã đơn hiển thị trên Pancake) để tra lại
    đơn khi mất response (POST không retry 5xx/ReadTimeout). UNIQUE (order, part_no)
    chặn hai worker cùng tạo dòng cho một đơn.

    `items` = phần dòng hàng của kiện khi tách (`[{product_id, quantity}]`); NULL =
    cả đơn. `warehouse` + `pancake_warehouse_id` là ảnh chụp lúc chọn kho (danh sách
    ghép kho bị thay toàn bộ khi lưu nên không FK sang `warehouse_links`).
    """

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="+", db_index=False)
    part_no = models.PositiveSmallIntegerField(default=1)
    pancake_order_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    origin = models.CharField(max_length=20, choices=OrderLinkOrigin.choices, default=OrderLinkOrigin.CRM_PUSH)
    status = models.CharField(max_length=20, choices=OrderLinkStatus.choices, default=OrderLinkStatus.PENDING)
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    pancake_warehouse_id = models.CharField(max_length=64, blank=True, default="")
    items = models.JSONField(null=True, blank=True)
    # Trạng thái đơn trên Pancake đọc được gần nhất (GET trước khi PUT; webhook ở MR B).
    pancake_status = models.SmallIntegerField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    # Lần POST gần nhất có thể đã tới Pancake (5xx/ReadTimeout) → lượt sau tra theo
    # `custom_id` trước khi tạo.
    needs_lookup = models.BooleanField(default=False)
    attempted_at = models.DateTimeField(null=True, blank=True)
    pushed_at = models.DateTimeField(null=True, blank=True)
    # Mốc đã đối chiếu thay đổi đơn CRM (sửa/huỷ → PUT). NULL = cần đối chiếu lại.
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_order_links"
        constraints = [
            models.UniqueConstraint(fields=["order", "part_no"], name="ppos_ordlink_order_part_uniq"),
        ]
        indexes = [
            # Hàng đợi việc (đẩy / thử lại) và các danh sách cần người xử lý — phần lớn
            # dòng là pushed/updated/cancelled nên partial.
            models.Index(
                fields=["status", "attempted_at"],
                name="ppos_ordlink_todo_idx",
                condition=models.Q(
                    status__in=["pending", "pushing", "error", "needs_routing", "skipped_duplicate"]
                ),
            ),
            # Đối chiếu sửa/huỷ: đơn đang sống trên Pancake, đẩy trong N ngày gần đây.
            models.Index(
                fields=["pushed_at"],
                name="ppos_ordlink_live_idx",
                condition=models.Q(status__in=["pushed", "updated"]),
            ),
            models.Index(fields=["-updated_at", "-id"], name="ppos_ordlink_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"order {self.order_id}#{self.part_no} → {self.pancake_order_id or '?'} ({self.status})"


class PancakePosStatusMap(models.Model):
    """Trạng thái đơn Pancake ↔ `OrderStatus` CRM (§3). Seed mặc định ở migration,
    sửa được. `locks_edit` = kho Pancake đã xử lý (in/đóng/xuất/giao) → CRM không
    sửa/huỷ đơn đó nữa (§4.1). `pancake_name` = tên tiếng Việt ghi vào
    `orders.partner_delivery_status`."""

    pancake_status = models.SmallIntegerField(unique=True)
    pancake_name = models.CharField(max_length=100)
    crm_status = models.PositiveSmallIntegerField(null=True, blank=True)
    locks_edit = models.BooleanField(default=False)
    # Mã "Trạng thái vận chuyển thực" CRM (`order_assessments.delivery_business_status_code`)
    # mà trạng thái Pancake này đưa tới (MR D). Rỗng = không đổi. `crm_status` KHÔNG được
    # dùng để ghi `orders.status` (quyết định 01/10: trạng thái đơn do KiotViet quyết định).
    crm_delivery_code = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_status_map"
        ordering = ("pancake_status",)

    def __str__(self) -> str:
        return f"{self.pancake_status} {self.pancake_name} → {self.crm_status}"


# ── webhook Pancake → CRM (two-way-sync §5.1, §7 B1) ──────────────────────────
class WebhookEventType(models.TextChoices):
    ORDERS = "orders", "Đơn hàng"
    VARIATIONS_WAREHOUSES = "variations_warehouses", "Tồn kho"
    PRODUCTS = "products", "Sản phẩm"
    CUSTOMERS = "customers", "Khách hàng"
    UNKNOWN = "unknown", "Không rõ"


class WebhookEventStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSED = "processed", "Đã xử lý"
    IGNORED = "ignored", "Bỏ qua"
    ERROR = "error", "Lỗi"


class PancakePosWebhookEvent(models.Model):
    """Payload webhook thô từ Pancake — lưu trước, xử lý sau (mẫu `CnvWebhookEvent`).

    Pancake không ký payload và không gửi header loại sự kiện → `event_type` suy từ
    hình dạng payload (`domain.inbound_order.classify_payload`). Khoá chống trùng
    `(event_type, record_id, payload_hash)`: Pancake gửi lại y hệt → chèn trượt; hai
    lần cập nhật khác nhau → hash khác → vào được, thứ tự do `record_version` quyết
    (worker bỏ bản cũ hơn bản đã áp — `PancakePosInboundOrder.pancake_updated_at`).

    `record_version` = `updated_at` (đơn/sản phẩm) hoặc `inserted_at` (tồn) Pancake,
    đã parse; NULL khi payload không có.
    """

    event_type = models.CharField(max_length=32, choices=WebhookEventType.choices)
    record_id = models.CharField(max_length=128, blank=True, default="")
    record_version = models.DateTimeField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=WebhookEventStatus.choices, default=WebhookEventStatus.PENDING)
    error = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    received_at = models.DateTimeField(db_default=Now())
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pancake_pos_webhook_events"
        constraints = [
            models.UniqueConstraint(
                fields=["event_type", "record_id", "payload_hash"], name="ppos_webhook_event_uniq"
            ),
        ]
        indexes = [
            # Hàng đợi worker (pending + lỗi còn lượt) — phần lớn dòng đã xử lý nên partial.
            models.Index(
                fields=["event_type", "received_at"],
                name="ppos_webhook_todo_idx",
                condition=models.Q(status__in=["pending", "error"]),
            ),
            # Thống kê 24h / dọn bản cũ theo thời điểm nhận.
            models.Index(fields=["received_at"], name="ppos_webhook_received_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}#{self.record_id or '-'} [{self.status}]"


class InboundMatchStatus(models.TextChoices):
    MATCHED = "matched", "Đã khớp"
    WAITING = "waiting", "Đang chờ khớp"
    UNMATCHED = "unmatched", "Chờ khớp tay"
    IGNORED = "ignored", "Bỏ qua"


class InboundMatchMethod(models.TextChoices):
    ORDER_LINK = "order_link", "Liên kết CRM đẩy"
    MARKETPLACE_CODE = "marketplace_code", "Mã đơn sàn"
    TRACKING_CODE = "tracking_code", "Mã vận đơn"
    CREATED = "created", "Tạo đơn CRM"
    MANUAL = "manual", "Khớp tay"


class PancakePosInboundOrder(models.Model):
    """Trạng thái phía CRM của MỘT đơn Pancake nhận qua webhook (two-way-sync §6b).

    - Mốc thứ tự: `pancake_updated_at` = `updated_at` của bản đã áp gần nhất; sự kiện
      cũ hơn bị bỏ qua (webhook có thể tới sai thứ tự). Dòng khoá `FOR UPDATE` khi áp
      nên hai worker cùng đơn chạy nối tiếp.
    - Khớp đơn CRM (`order`): liên kết CRM đẩy → mã đơn sàn → mã vận đơn → tạo đơn
      (cờ). Không khớp quá N giờ / khớp nhiều đơn → `unmatched` (màn "Chờ khớp" + Lark).
    - Mốc gửi/giao/hoàn lấy từ `status_history` Pancake (CRM không có cột riêng).
    - Trạng thái vận chuyển thực (MR D): `status_name` (tên tiếng Việt), `dvvc_status` (ĐVVC
      mới nhất) của RIÊNG đơn Pancake này; đơn CRM hiện kiện chậm nhất (`orders.partner_*`).
      `status_changed_at` = lúc vào trạng thái hiện tại, `last_partner_status_at` = mốc cập
      nhật ĐVVC gần nhất. Kho Pancake (`pancake_warehouse_id`/`warehouse_name`) cho báo cáo
      đơn treo theo kho.
    """

    pancake_order_id = models.CharField(max_length=64, unique=True)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", db_index=False
    )
    match_status = models.CharField(
        max_length=16, choices=InboundMatchStatus.choices, default=InboundMatchStatus.WAITING
    )
    match_method = models.CharField(max_length=20, choices=InboundMatchMethod.choices, blank=True, default="")
    # Đơn CRM ứng viên khi mã khớp nhiều đơn (người điều phối chọn ở màn Chờ khớp).
    candidates = models.JSONField(default=list, blank=True)
    reason = models.TextField(blank=True, default="")
    pancake_status = models.SmallIntegerField(null=True, blank=True)
    pancake_updated_at = models.DateTimeField(null=True, blank=True)
    order_sources = models.CharField(max_length=64, blank=True, default="")
    source_name = models.CharField(max_length=255, blank=True, default="")
    account_name = models.CharField(max_length=255, blank=True, default="")
    tracking_code = models.CharField(max_length=100, blank=True, default="")
    partner_name = models.CharField(max_length=100, blank=True, default="")
    total_price = models.BigIntegerField(null=True, blank=True)
    inserted_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    status_name = models.CharField(max_length=64, blank=True, default="")
    dvvc_status = models.CharField(max_length=64, blank=True, default="")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    last_partner_status_at = models.DateTimeField(null=True, blank=True)
    pancake_warehouse_id = models.CharField(max_length=64, blank=True, default="")
    warehouse_name = models.CharField(max_length=255, blank=True, default="")
    last_event = models.ForeignKey(
        PancakePosWebhookEvent, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", db_index=False
    )
    alerted_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_pos_inbound_orders"
        indexes = [
            # Màn "Chờ khớp" + beat khớp lại: tập nhỏ (đa số đơn khớp ngay) nên partial.
            models.Index(
                fields=["match_status", "first_seen_at"],
                name="ppos_inbound_todo_idx",
                condition=models.Q(match_status__in=["waiting", "unmatched"]),
            ),
            models.Index(fields=["order"], name="ppos_inbound_order_idx", condition=models.Q(order__isnull=False)),
            # Báo cáo đơn treo (MR D): chưa gửi quá N giờ theo mốc tạo đơn Pancake…
            models.Index(
                fields=["inserted_at"],
                name="ppos_inbound_not_sent_idx",
                condition=models.Q(pancake_status__in=[0, 1, 12, 13, 8, 9]),
            ),
            # …và đã gửi quá M ngày chưa giao theo mốc gửi.
            models.Index(
                fields=["shipped_at"], name="ppos_inbound_shipped_idx", condition=models.Q(pancake_status=2)
            ),
            # Báo cáo lệch KiotViet ↔ Pancake: kiện cập nhật trong cửa sổ gần đây.
            models.Index(fields=["updated_at"], name="ppos_inbound_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"pancake {self.pancake_order_id} → order {self.order_id or '?'} ({self.match_status})"
