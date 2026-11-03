"""Model của tích hợp Foxia — xem docs/foxia-integration-design.md.

Chia làm 3 nhóm:
  1. Kết nối + nhật ký đồng bộ (FoxiaConnection, FoxiaSyncCursor, FoxiaSyncRun)
  2. Danh mục mirror — bản sao nguyên trạng của Foxia, KHÔNG nhét vào bảng nội bộ
     (tên trùng nhau không có nghĩa là cùng một thực thể)
  3. Bảng ánh xạ danh mục Foxia ↔ chiều nội bộ, cấu hình được qua admin

Sprint 1 dừng ở đây. Đơn hàng (FoxiaOrderLink/Line/Shipment) thuộc Sprint 2.
"""

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


# ──────────────────────────────────────────────────────────────────────────────
# 1. Kết nối & nhật ký đồng bộ
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaAuthStatus(models.TextChoices):
    UNKNOWN = "unknown", "Chưa kiểm tra"
    OK = "ok", "Hợp lệ"
    REVOKED = "revoked", "Bị thu hồi"
    ERROR = "error", "Lỗi kết nối"


class FoxiaConnection(models.Model):
    """Bộ thông tin kết nối JSON-RPC tới Foxia (thường chỉ 1 row active).

    Foxia cấp 4 mảnh: endpoint, tên database, username, API key — và tài liệu
    cảnh báo riêng rằng API key KHÔNG thay thế tên database, phải gửi đúng cả
    hai. Vì vậy `database` là field bắt buộc chứ không suy ra từ key.

    `uid` là kết quả `common.authenticate`: định danh user chứ không phải session
    token, nên cache được lâu và chỉ cần lấy lại khi Foxia từ chối.
    """

    name = models.CharField(max_length=255, default="Foxia")
    endpoint_url = models.CharField(
        max_length=255, help_text="VD: https://crm.example.vn/jsonrpc"
    )
    database = models.CharField(
        max_length=128, help_text="Tên database Foxia cấp, VD: foxia_prod"
    )
    username = models.CharField(max_length=255, help_text="VD: integration.marketing")
    api_key = EncryptedCharField(max_length=1024)

    uid = models.IntegerField(null=True, blank=True, help_text="Cache kết quả authenticate")
    uid_checked_at = models.DateTimeField(null=True, blank=True)
    auth_status = models.CharField(
        max_length=16, choices=FoxiaAuthStatus.choices, default=FoxiaAuthStatus.UNKNOWN
    )
    auth_error = models.TextField(blank=True, default="")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_connections"
        verbose_name = "Foxia connection"
        verbose_name_plural = "Foxia connections"

    def __str__(self) -> str:
        return f"{self.name} ({self.database})"

    @classmethod
    def get_active(cls) -> "FoxiaConnection | None":
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()

    def resolve_endpoint_url(self) -> str:
        return (self.endpoint_url or "").strip()

    def resolve_timeout(self) -> float:
        return float(getattr(settings, "FOXIA_HTTP_TIMEOUT", 30.0))


class FoxiaSyncCursor(models.Model):
    """Watermark theo tài nguyên.

    Foxia KHÔNG cấp `write_date` nên không watermark được theo "lần sửa cuối";
    `synced_until` là mốc NGÀY NGHIỆP VỤ đã quét xong, và mỗi lượt vẫn phải lùi
    lại một cửa sổ để bắt bản ghi sửa trong quá khứ (design §6.3).

    Mốc chỉ tiến khi CẢ lượt thành công — lượt lỗi giữa chừng để nguyên mốc cũ,
    lượt sau quét lại. An toàn vì mọi upsert đều idempotent theo id Foxia.
    """

    resource = models.CharField(max_length=64, unique=True)
    synced_until = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "foxia_sync_cursors"

    def __str__(self) -> str:
        return f"{self.resource} @ {self.synced_until:%Y-%m-%d %H:%M}"


class FoxiaSyncStatus(models.TextChoices):
    RUNNING = "running", "Đang chạy"
    SUCCESS = "success", "Thành công"
    PARTIAL = "partial", "Thành công một phần"
    FAILED = "failed", "Thất bại"


class FoxiaSyncRun(models.Model):
    """Nhật ký từng lượt đồng bộ — bằng chứng khi số liệu bị thắc mắc.

    `skipped` đếm bản ghi bỏ qua có chủ đích (thiếu dữ liệu bắt buộc, giá trị lạ)
    để người vận hành thấy được phần bị bỏ, thay vì im lặng — giới hạn bị nuốt
    lặng đọc như "đã phủ hết".
    """

    resource = models.CharField(max_length=64, db_index=True)
    window_from = models.DateTimeField(null=True, blank=True)
    window_to = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=FoxiaSyncStatus.choices, default=FoxiaSyncStatus.RUNNING
    )
    fetched = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "foxia_sync_runs"
        indexes = [
            models.Index(fields=["resource", "-started_at"], name="foxia_run_res_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.resource} {self.status} ({self.fetched} bản ghi)"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Danh mục mirror
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaCatalogBase(models.Model):
    """Khung chung của danh mục mirror: id gốc bên Foxia + tên hiển thị.

    `foxia_id` unique là khoá idempotent — đồng bộ lại bao nhiêu lần cũng không
    sinh bản ghi trùng. `name` là tên hiển thị và KHÔNG ổn định (tài liệu §4
    cảnh báo), nên mọi phép join phải bám vào `foxia_id`.
    """

    foxia_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.name or f"#{self.foxia_id}"


class FoxiaBranch(FoxiaCatalogBase):
    """congtycon — chi nhánh / công ty con."""

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_branches"


class FoxiaMarketingTeam(FoxiaCatalogBase):
    """doinhommarketing — nhóm marketing."""

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_marketing_teams"


class FoxiaSaleTeam(FoxiaCatalogBase):
    """crm.team — nhóm sale."""

    branch = models.ForeignKey(
        FoxiaBranch, on_delete=models.SET_NULL, null=True, blank=True, related_name="sale_teams"
    )

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_sale_teams"


class FoxiaUser(FoxiaCatalogBase):
    """res.users — nhân viên.

    `login` là khoá ổn định để ánh xạ sang User nội bộ; tên hiển thị thì không
    (tên tiếng Việt trùng nhau là chuyện thường ở quy mô 100+ nhân sự).
    """

    login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    is_active_foxia = models.BooleanField(default=True)
    marketing_team = models.ForeignKey(
        FoxiaMarketingTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )
    sale_team = models.ForeignKey(
        FoxiaSaleTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_users"

    def __str__(self) -> str:
        return self.login or self.name or f"#{self.foxia_id}"


class FoxiaChannel(FoxiaCatalogBase):
    """kenhmarketing — kênh marketing."""

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_marketing_channels"


class FoxiaBrand(FoxiaCatalogBase):
    """thuonghieu — nhãn sản phẩm / thương hiệu."""

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_brands"


class FoxiaSource(FoxiaCatalogBase):
    """utm.source — nguồn marketing.

    Bản thân nguồn đã mang sẵn kênh/thương hiệu/nhân viên phụ trách, nên khi đơn
    chỉ biết `nguonkhachhang_id` ta vẫn suy ra được các chiều còn lại.
    """

    channel = models.ForeignKey(
        FoxiaChannel, on_delete=models.SET_NULL, null=True, blank=True, related_name="sources"
    )
    brand = models.ForeignKey(
        FoxiaBrand, on_delete=models.SET_NULL, null=True, blank=True, related_name="sources"
    )
    marketer = models.ForeignKey(
        FoxiaUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_sources"
    )
    marketing_team = models.ForeignKey(
        FoxiaMarketingTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="sources"
    )
    branch = models.ForeignKey(
        FoxiaBranch, on_delete=models.SET_NULL, null=True, blank=True, related_name="sources"
    )

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_sources"


class FoxiaStage(FoxiaCatalogBase):
    """giaidoandonhang — trạng thái (giai đoạn) đơn hàng phía Foxia.

    Chỉ MIRROR, không map sang OrderStatus nội bộ ở giai đoạn này: chưa có bản
    chốt danh sách mã trạng thái từ Foxia nên mọi phép map lúc này là đoán
    (design §5.5).
    """

    phanloai = models.CharField(max_length=64, blank=True, default="")

    class Meta(FoxiaCatalogBase.Meta):
        db_table = "foxia_order_stages"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Ánh xạ sang chiều nội bộ
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaUserMap(models.Model):
    """foxia_users.login ↔ User nội bộ.

    Khớp theo `login` chứ không theo tên hiển thị — xem FoxiaUser. Một User nội
    bộ có thể ứng với nhiều tài khoản Foxia (nhiều chi nhánh), nên chỉ ràng buộc
    duy nhất ở phía Foxia.
    """

    foxia_user = models.OneToOneField(
        FoxiaUser, on_delete=models.CASCADE, related_name="internal_map"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="foxia_user_maps"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "foxia_user_maps"

    def __str__(self) -> str:
        return f"{self.foxia_user_id} ↔ user:{self.user_id}"


class FoxiaChannelMapKind(models.TextChoices):
    SOURCE = "source", "Nguồn marketing (utm.source)"
    CHANNEL = "channel", "Kênh marketing (kenhmarketing)"


class FoxiaChannelMap(models.Model):
    """Nguồn/kênh Foxia ↔ Channel nội bộ — cầu nối chi phí Ads ↔ đơn Foxia.

    Channel nội bộ đã liên kết sẵn với tài khoản quảng cáo qua ChannelAdAccount,
    nên map được tới Channel là ghép được spend. Nguồn chưa map thì đơn vẫn về
    đủ, chỉ không có chi phí — báo cáo hiển thị nhóm "Chưa ánh xạ" kèm số lượng
    thay vì im lặng bỏ qua.

    Không FK cứng sang FoxiaSource/FoxiaChannel vì hai loại dùng chung một bảng
    map; cặp (kind, foxia_id) là khoá.
    """

    kind = models.CharField(max_length=16, choices=FoxiaChannelMapKind.choices)
    foxia_id = models.BigIntegerField()
    channel = models.ForeignKey(
        "channels_core.Channel", on_delete=models.CASCADE, related_name="foxia_maps"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "foxia_channel_maps"
        constraints = [
            models.UniqueConstraint(fields=["kind", "foxia_id"], name="foxia_channel_map_uniq"),
        ]
        indexes = [
            models.Index(fields=["channel"], name="foxia_chmap_channel_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.foxia_id} ↔ channel:{self.channel_id}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Đơn hàng — bảng phụ của orders.Order (Sprint 2)
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaOrderLink(models.Model):
    """Cầu nối 1-1 giữa đơn mirror trong bảng `orders` và đơn gốc bên Foxia.

    Đơn nằm chung bảng `orders` (order_source="foxia") để tái dùng hạ tầng sẵn
    có; mọi field Foxia không có chỗ trong Order nằm ở đây thay vì đẻ thêm cột
    vào một bảng vốn đã rất rộng và rất nóng.

    `foxia_id` unique là KHOÁ IDEMPOTENT của toàn bộ đồng bộ: chạy lại bao nhiêu
    lần cũng không sinh đơn trùng.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="foxia_link"
    )
    foxia_id = models.BigIntegerField(unique=True)
    foxia_name = models.CharField(max_length=100, blank=True, default="")
    # id cơ hội (lead) sinh ra đơn. Đơn KHÔNG mang nguồn/kênh/thương hiệu —
    # chúng chỉ có trên `cohoi`, nên phải enrich qua id này (design §2).
    cohoi_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    # Mốc thời gian gốc, lưu UTC-aware. KHÔNG ghi đè Order.created_at: đó là mốc
    # bản ghi vào hệ thống ta, hai thứ khác nhau và cần cả hai khi truy vết.
    ngaytao = models.DateTimeField(null=True, blank=True)
    ngayxacnhan = models.DateTimeField(null=True, blank=True)
    ngayhoanthanh = models.DateTimeField(null=True, blank=True)

    # Nhân sự & tổ chức
    marketer = models.ForeignKey(
        FoxiaUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="marketed_orders"
    )
    salesperson = models.ForeignKey(
        FoxiaUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="sold_orders"
    )
    sale_team = models.ForeignKey(
        FoxiaSaleTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    branch = models.ForeignKey(
        FoxiaBranch, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )

    # Chiều marketing — enrich từ cohoi, là cầu nối tới chi phí Ads
    source = models.ForeignKey(
        FoxiaSource, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    channel = models.ForeignKey(
        FoxiaChannel, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    brand = models.ForeignKey(
        FoxiaBrand, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )

    # Trạng thái — lưu thô, không map (design §5.5). Bản sao dạng chuỗi nằm ở
    # orders.partner_delivery_status để báo cáo khỏi phải join.
    stage = models.ForeignKey(
        FoxiaStage, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    stage_class = models.CharField(max_length=64, blank=True, default="")
    return_status = models.CharField(max_length=64, blank=True, default="")

    # Tiền. 18 chữ số vì đây là dữ liệu của hệ thống khác — không giả định biên
    # độ giống đơn nội bộ (Order.total_amount chỉ 12).
    amount_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount_after_extra_discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount_collected = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_order_links"
        indexes = [
            models.Index(fields=["ngayxacnhan"], name="foxia_link_confirmed_idx"),
            models.Index(fields=["ngayhoanthanh"], name="foxia_link_done_idx"),
            # Hai chiều lát cắt chính của báo cáo hiệu quả marketing.
            models.Index(fields=["source", "ngayxacnhan"], name="foxia_link_src_conf_idx"),
            models.Index(fields=["marketer", "ngayxacnhan"], name="foxia_link_mkt_conf_idx"),
        ]

    def __str__(self) -> str:
        return self.foxia_name or f"foxia#{self.foxia_id}"


class FoxiaOrderLine(models.Model):
    """Dòng sản phẩm giữ nguyên trạng.

    KHÔNG tạo OrderItem: `OrderItem.product` là FK PROTECT NOT NULL, còn dòng
    Foxia chỉ có product_id + tên, không có SKU để khớp sang Product nội bộ.
    Ép khớp là đoán, mà đoán sai thì doanh thu gán nhầm sản phẩm.
    """

    link = models.ForeignKey(FoxiaOrderLink, on_delete=models.CASCADE, related_name="lines")
    foxia_line_id = models.BigIntegerField(unique=True)
    foxia_product_id = models.BigIntegerField(null=True, blank=True)
    product_name = models.CharField(max_length=255, blank=True, default="")
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_order_lines"
        indexes = [
            models.Index(fields=["link"], name="foxia_line_link_idx"),
        ]


class FoxiaShipment(models.Model):
    """Vận đơn (donvanchuyen). Một đơn có thể có nhiều vận đơn: giao lại, hoàn.

    `raw_status` là mã THÔ của đơn vị vận chuyển — tài liệu Foxia §8.1 ghi rõ nó
    "phản ánh trạng thái từ đơn vị vận chuyển" và bên nhận tự cấu hình ý nghĩa.
    Ta chưa cấu hình gì cả, chỉ lưu.
    """

    link = models.ForeignKey(FoxiaOrderLink, on_delete=models.CASCADE, related_name="shipments")
    foxia_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=128, blank=True, default="")
    raw_status = models.CharField(max_length=64, blank=True, default="")
    # Nguồn của Order.shipping_fee (tổng mọi vận đơn của đơn — design §5.6).
    actual_ship_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_shipments"
        indexes = [
            models.Index(fields=["link", "-foxia_id"], name="foxia_ship_link_idx"),
        ]


# ──────────────────────────────────────────────────────────────────────────────
# 5. Đẩy đơn sang Foxia — chiều GHI (spec 2026-09-09)
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaLinkChannel(models.Model):
    """Một HÀNG của bảng liên kết: một kênh bán nội bộ.

    Tách thành bảng riêng thay vì suy ra từ các ô đã điền: người vận hành phải
    thêm được hàng rồi mới điền dần, và hàng rỗng là trạng thái hợp lệ.
    """

    channel = models.OneToOneField(
        "channels_core.Channel", on_delete=models.CASCADE, related_name="foxia_link_column"
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "foxia_link_channels"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"hàng:{self.channel_id}"


class FoxiaLinkKind(models.TextChoices):
    """Các CỘT cố định của bảng liên kết — loại nguồn khách hàng chủ hệ thống đã
    tạo bên Foxia (quyết định 2026-09-16, thay cho trục SKU cũ).

    Giá trị TRÙNG với `OrderSource` của đơn: lúc đẩy, cột được chọn theo
    `order.order_source`. `resale` chưa có nguồn đơn tương ứng — cột dựng sẵn
    để điền id, chưa đơn nào tra tới. Nguồn đơn không có cột (koc, shopee…)
    thì bỏ qua.
    """

    LANDING = "landing", "Landing"
    MESSENGER = "messenger", "Messenger"
    HOTLINE = "hotline", "Hotline"
    ZALO = "zalo", "Zalo"
    RESALE = "resale", "Resale"


class FoxiaLinkCell(models.Model):
    """Ô (kênh × loại nguồn) → `idnguonkhachhang` của Foxia, điền tay.

    `foxia_source_id` CỐ Ý không FK sang FoxiaSource: người vận hành phải điền
    được id mà mirror danh mục chưa kịp đồng bộ về, và một FK cứng sẽ chặn đúng
    lúc cần nhất. API chỉ validate `> 0`; tên nguồn (nếu tra được) chỉ để đối
    chiếu bằng mắt, không tham gia logic.

    FK sang hàng chứ không sang thẳng Channel: nhờ vậy "bỏ một hàng thì id
    trong hàng đó biến mất" là ON DELETE CASCADE của Postgres.
    """

    link_channel = models.ForeignKey(
        FoxiaLinkChannel, on_delete=models.CASCADE, related_name="cells"
    )
    kind = models.CharField(max_length=16, choices=FoxiaLinkKind.choices)
    foxia_source_id = models.BigIntegerField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_link_cells"
        constraints = [
            models.UniqueConstraint(fields=["link_channel", "kind"], name="foxia_link_cell_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.link_channel_id}×{self.kind} → {self.foxia_source_id}"


class FoxiaPushStatus(models.TextChoices):
    OK = "ok", "Thành công"
    FAILED = "failed", "Thất bại"
    # Không còn phát sinh từ 2026-09-16 (id tra theo kênh, không theo SKU); giữ
    # để đọc được các dòng nhật ký cũ.
    SKIPPED_NO_PRODUCT = "skipped_no_product", "Bỏ qua — đơn không có sản phẩm"
    SKIPPED_NO_MAPPING = "skipped_no_mapping", "Bỏ qua — chưa cấu hình id"


class FoxiaOrderPush(models.Model):
    """Nhật ký MỘT lần đẩy đơn sang Foxia.

    Ghi một dòng cho MỌI nhánh, kể cả bỏ qua: phần bị bỏ phải nhìn thấy được,
    chứ không suy ra từ sự vắng mặt của bản ghi.

    `response_body` cắt 2000 ký tự — Foxia chưa chốt hợp đồng phản hồi và có thể
    trả nguyên một trang HTML lỗi; bảng này ghi mỗi đơn một dòng nên không được
    phép phình theo kích thước phản hồi.
    """

    RESPONSE_LIMIT = 2000

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="foxia_pushes"
    )
    status = models.CharField(max_length=32, choices=FoxiaPushStatus.choices)
    foxia_source_id = models.BigIntegerField(null=True, blank=True)
    url = models.CharField(max_length=500, blank=True, default="")
    request_body = models.JSONField(null=True, blank=True)
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "foxia_order_pushes"
        indexes = [
            models.Index(fields=["order", "-created_at"], name="foxia_push_order_idx"),
            models.Index(fields=["status", "-created_at"], name="foxia_push_status_idx"),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} {self.status}"


class FoxiaLeadLink(models.Model):
    """Cơ hội (`cohoi`) mà Foxia tạo ra từ MỘT lần đẩy đơn landing của ta.

    Chiều ngược của `FoxiaOrderLink`: bảng kia nối đơn MIRROR (`order_source
    ="foxia"`) về đơn gốc Foxia, bảng này nối đơn NỘI BỘ của ta tới cơ hội mà
    lần POST sinh ra. Hai bảng gặp nhau ở `cohoi_id` — đó là cách
    `FoxiaLeadMatchService` điền `foxia_order_id` mà không cần gọi mạng.

    `order` là ForeignKey chứ KHÔNG phải OneToOne: POST đẩy đơn không idempotent
    (xem `FoxiaOrderPushService`), task Celery bị redeliver là sinh cơ hội thứ
    hai cho cùng một đơn. Schema phải chứa được sự thật đó thay vì vỡ constraint;
    `cohoi_id` unique mới là thứ chống trùng thật.
    """

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="foxia_leads"
    )
    # Hai id Foxia trả về ngay trong phản hồi POST (`data.khachhang_id/cohoi_id`).
    khachhang_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    cohoi_id = models.BigIntegerField(unique=True)

    # Điền sau, khi cơ hội đã lên đơn bên Foxia và chiều đồng bộ kéo đơn đó về.
    foxia_order_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    matched_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "foxia_lead_links"
        indexes = [
            models.Index(fields=["order", "-created_at"], name="foxia_lead_order_idx"),
        ]

    def __str__(self) -> str:
        return f"cohoi:{self.cohoi_id} ← order:{self.order_id}"


# ── 6. Cơ hội resale từ đơn giao thành công ──────────────────────────────────


class FoxiaResalePushStatus(models.TextChoices):
    # Đã giành chỗ (dòng ghi TRƯỚC khi POST). Kẹt ở đây = worker chết giữa chừng;
    # cố ý KHÔNG đẩy lại vì POST không idempotent — xem FoxiaResalePushService.
    PENDING = "pending", "Đang đẩy"
    OK = "ok", "Thành công"
    FAILED = "failed", "Thất bại"
    SKIPPED_NO_PHONE = "skipped_no_phone", "Bỏ qua — đơn không có SĐT"
    SKIPPED_DUPLICATE = "skipped_duplicate", "Bỏ qua — SĐT đã có cơ hội resale trong ngày"
    # HTTP 2xx nhưng phản hồi `"cohoi_id": false`: Foxia ghi nhận khách mà KHÔNG tạo
    # cơ hội (gặp khi khách đã có cơ hội đang mở bên Foxia). Không phải thành công.
    NO_OPPORTUNITY = "no_opportunity", "Foxia không tạo cơ hội"


class FoxiaResalePush(models.Model):
    """Cơ hội resale tạo bên Foxia từ MỘT đơn nội bộ giao thành công.

    `order` UNIQUE: mỗi đơn tối đa một cơ hội resale, kể cả khi job chạy lại hay
    hai lượt quét chồng cửa sổ. Dòng được ghi (status=pending) TRƯỚC khi POST —
    đó là cách chống trùng cho một POST không idempotent: lượt sau thấy dòng là
    bỏ, kể cả khi lượt trước chết giữa chừng.

    `cohoi_id`/`khachhang_id` nằm ngay đây chứ không ở `foxia_lead_links`: bảng
    kia là cơ hội ĐƠN MỚI từ landing, trộn resale vào sẽ làm sai số đối chiếu
    lead→đơn của marketing.
    """

    RESPONSE_LIMIT = 2000

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="foxia_resale_push"
    )
    # Ngày giao thành công (giờ VN) — trục lọc của admin và của chống trùng SĐT.
    delivered_on = models.DateField()
    phone = models.CharField(max_length=30, blank=True, default="")
    status = models.CharField(max_length=32, choices=FoxiaResalePushStatus.choices)
    # Sale resale do bộ chia chọn (cột `resale` của ma trận Chia đơn) — KHÔNG phải
    # sale chốt đơn gốc. Null ở các dòng bỏ qua.
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    foxia_source_id = models.BigIntegerField()
    url = models.CharField(max_length=500, blank=True, default="")
    request_body = models.JSONField(null=True, blank=True)
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    # KHÔNG unique: Foxia có thể gộp khách cũ vào cơ hội đang mở và trả lại id cũ —
    # dữ liệu đó phải ghi được, không được làm vỡ cả lượt.
    cohoi_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    khachhang_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_resale_pushes"
        indexes = [
            # Chống trùng SĐT trong ngày + lọc admin theo ngày giao.
            models.Index(fields=["delivered_on", "phone"], name="foxia_resale_day_phone_idx"),
            models.Index(fields=["status", "-created_at"], name="foxia_resale_status_idx"),
            # Thống kê theo ngày trên trang Chia đơn.
            models.Index(fields=["created_at"], name="foxia_resale_created_idx"),
        ]

    def __str__(self) -> str:
        return f"resale order:{self.order_id} {self.status}"
