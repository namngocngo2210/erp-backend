from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models import F, Q, TextField
from django.db.models.functions import Cast, Upper
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.common.order_scope import MIRROR_ORDER_SOURCES
from apps.common.phone_utils import normalize_phone
from apps.orders.sheet_rules import ASSIGNMENT_SHEETS, SHEET_LABELS
from apps.customers.models import Customer

# ── DEPRECATED re-export ────────────────────────────────────────────────────
# Product Catalog đã move sang apps.catalog (docs/catalog-app-split.md). Code
# mới import từ apps.catalog.models; re-export này chỉ đỡ các nhánh đang mở,
# sẽ gỡ ở MR dọn sau.
from apps.catalog.models import (  # noqa: F401
    PROFILE_SECTIONS,
    PriceBook,
    Product,
    ProductCategory,
    ProductComponent,
    ProductGroup,
    ProductPackage,
    ProductPackagePrice,
    ProductProfile,
    ProductProfileImage,
)


class OrderStatus(models.IntegerChoices):
    NEW = 0, "New"
    PENDING_PROCESSING = 1, "Pending processing"
    IN_DELIVERY = 2, "In delivery"
    DELIVERED_SUCCESS = 3, "Delivered success"
    IN_RETURN = 4, "In return"
    RETURNED = 5, "Returned"
    CANCELLED = 6, "Cancelled"
    PICKING_UP = 7, "Picking up"
    AWAITING_REPICKUP = 8, "Awaiting re-pickup"
    PICKED_UP = 9, "Picked up"
    AWAITING_REDELIVERY = 10, "Awaiting re-delivery"
    AWAITING_SHIPMENT = 11, "Awaiting shipment"
    AWAITING_RETURN_SHIPMENT = 12, "Awaiting return shipment"
    AWAITING_RETURN_CONFIRMATION = 13, "Awaiting return confirmation"


class OrderType(models.TextChoices):
    NORMAL = "normal", "Normal"
    NO_RETURN = "no_return", "No Return"
    HAS_RETURN = "has_return", "Has Return"


class KocOrderType(models.TextChoices):
    """Phân loại đơn KOC. Rỗng = đơn KOC thường (không phải một lựa chọn)."""

    SAMPLE_GIFT = "sample_gift", "Tặng mẫu"
    RENEWAL = "renewal", "Gia hạn"
    RECALL = "recall", "Thu hồi"


class OrderSource(models.TextChoices):
    LANDING = "landing", "Landing"
    KOL = "kol", "KOL"
    ZALO = "zalo", "Zalo"
    MESSENGER = "messenger", "Messenger"
    HOTLINE = "hotline", "Hotline"
    KOC = "koc", "KOC"
    SHOPEE = "shopee", "Shopee"
    TIKTOK = "tiktok", "TikTok"
    # Đơn MIRROR từ hệ thống đối tác — không phải đơn của công ty. Bị loại khỏi
    # mọi báo cáo/danh sách nội bộ qua `apps.common.order_scope`.
    FOXIA = "foxia", "Foxia"
    # Đơn khách tự đặt trên app loyalty CNV. Mirror như Foxia theo quyết định
    # 19/08 (chưa tính vào doanh thu công ty) — xem
    # docs/superpowers/specs/2026-08-19-cnv-inbound-orders-design.md §2.
    CNV = "cnv", "CNV Loyalty"

    @classmethod
    def from_invoice_code(cls, code):
        """Suy ra nguồn đơn sàn từ prefix mã hoá đơn (startswith, không phân biệt
        hoa thường). Trả về ``None`` nếu mã không thuộc sàn nào — điểm chân lý
        duy nhất cho cả auto-detect ở ``Order.save()`` lẫn command backfill."""
        text = str(code or "").strip().upper()
        for prefix, source in INVOICE_CODE_SOURCE_PREFIXES.items():
            if text.startswith(prefix):
                return source
        return None

    @classmethod
    def normalize(cls, value, *, default=None):
        """Chuẩn hoá order_source về một giá trị enum hợp lệ.

        Điểm normalize duy nhất cho mọi luồng ingest (landing/Lark/Excel) và
        cập nhật đơn. Trim + lowercase; trả về ``default`` khi rỗng hoặc không
        thuộc enum (lenient — không raise, tránh chặn ingest vì dữ liệu lạ).
        """
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in cls.values:
            return text
        return default


# Mã hoá đơn KiotViet của đơn sàn mang prefix nhận diện.
# Khớp startswith, case-insensitive — key phải viết HOA.
INVOICE_CODE_SOURCE_PREFIXES = {
    "HDTTS_": OrderSource.TIKTOK,
    "HDSPE_": OrderSource.SHOPEE,
}

# Nguồn đơn sàn TMĐT — không thuộc luồng sale Landing, các trang Landing/Sheet
# loại các nguồn này (cùng cách loại đơn koc).
MARKETPLACE_ORDER_SOURCES = sorted(set(INVOICE_CODE_SOURCE_PREFIXES.values()))


class OrderVat(models.TextChoices):
    """Hình thức xuất hoá đơn VAT cho đơn hàng."""

    INDIVIDUAL = "individual", "Individual"  # Xuất cho cá nhân
    COMPANY = "company", "Company"  # Xuất cho công ty
    NO = "no", "No"  # Không xuất


class ShippingType(models.TextChoices):
    """Hình thức xuất hàng của đơn.

    NULL (chưa xác định) là trạng thái thứ ba, hợp lệ: đơn tạo trước tính năng này và
    đơn chưa qua thao tác nào của người dùng đều mang NULL. Không dùng chuỗi rỗng —
    chuỗi rỗng lẫn với dữ liệu nhập thiếu.
    """

    SHIPMENT = "shipment", "Shipment"  # Gửi vận chuyển
    DIRECT = "direct", "Direct"  # Bán trực tiếp tại quầy


class InternalOrderManager(models.Manager):
    """Manager MẶC ĐỊNH của Order: chỉ đơn nội bộ, loại đơn mirror từ đối tác.

    Đặt hàng rào ở manager thay vì sửa ~170 điểm query: mọi báo cáo, danh sách,
    export, luồng kho hiện có tự động an toàn, và code viết sau này cũng vậy.
    Đường đồng bộ đối tác phải chọn tham gia tường minh qua `Order.all_objects`
    — mở cửa có chủ đích thì thấy được ở review, quên đóng cửa thì không.

    Không lọc `deleted_at`: đó là ngữ nghĩa khác và nhiều chỗ đang tự lọc theo
    cách riêng (đơn xoá vẫn cần tra ở lịch sử). Manager này chỉ làm một việc.
    """

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                Q(order_source__isnull=True) | ~Q(order_source__in=MIRROR_ORDER_SOURCES)
            )
        )


class Order(models.Model):
    # Thứ tự khai báo quyết định `_default_manager` → `objects` (đã lọc) là mặc
    # định. `Meta.base_manager_name = "all_objects"` để Django dùng manager KHÔNG
    # lọc khi nạp quan hệ: base manager mà lọc thì `order_item.order` của một đơn
    # mirror sẽ raise DoesNotExist — Django cảnh báo đúng điều này trong tài liệu.
    objects = InternalOrderManager()
    all_objects = models.Manager()

    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="orders")
    kiotviet_channel = models.ForeignKey(
        "kiotviet.KiotVietChannel",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
    )
    fulfillment_warehouse = models.ForeignKey(
        "warehouses.Warehouse",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    # Cờ do HỆ THỐNG gắn theo thao tác thật (xem ingest_job_tasks), không cho sửa tay:
    # nó quyết định thời điểm trừ tồn và luật hoàn tồn khi huỷ, sửa tay là sửa luật kho
    # của một đơn đã ghi sổ.
    shipping_type = models.CharField(
        max_length=16, choices=ShippingType.choices, null=True, blank=True, default=None
    )
    # "Đơn bán trực tiếp KHÔNG trừ tồn tại thời điểm lên hoá đơn." Từ 28/08/2026 mọi đơn
    # quick-sale đều mang cờ True. Cờ KHÔNG trả lời "đã xuất chưa" — câu đó do sổ tồn và
    # `delivery_business_status_code` trả lời. Nó phân biệt hai lớp đơn direct có hai luật
    # huỷ khác nhau; xem specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
    direct_dispatch_deferred = models.BooleanField(default=False)
    name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    # Canonical VN form of `phone`, kept in sync by save(). Lets phone search hit a
    # plain B-tree index with an exact match instead of a cross-table OR / trigram scan.
    phone_normalized = models.CharField(max_length=20, blank=True, default="", db_default="")
    raw_address = models.TextField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    # Cờ bất biến ghi tại thời điểm API ingest (landing/lark): đơn đổ về không có
    # địa chỉ → False. Sale điền địa chỉ sau KHÔNG flip lại — trạng thái hiện tại
    # của địa chỉ vẫn check qua raw_address/address. Đơn tạo tay/Excel giữ True.
    has_address = models.BooleanField(default=True, db_default=True)
    ward = models.TextField(blank=True, default="")
    district = models.TextField(blank=True, default="")
    province = models.TextField(blank=True, default="")
    ward_code = models.CharField(max_length=20, blank=True, default="")
    district_code = models.CharField(max_length=20, blank=True, default="")
    province_code = models.CharField(max_length=20, blank=True, default="")
    kiotviet_location = models.CharField(max_length=255, blank=True, default="")
    kiotviet_ward = models.CharField(max_length=255, blank=True, default="")
    # Địa chỉ 2 cấp (34 tỉnh, sau 01/07/2025) — quy đổi từ raw_address, chỉ đọc trên
    # UI. 3 cấp ở trên vẫn giữ nguyên; xem apps/orders/address_level2.py.
    address_level2 = models.TextField(blank=True, default="", db_default="")
    ward_level2 = models.TextField(blank=True, default="", db_default="")
    province_level2 = models.TextField(blank=True, default="", db_default="")
    ward_code_level2 = models.CharField(max_length=20, blank=True, default="", db_default="")
    province_code_level2 = models.CharField(max_length=20, blank=True, default="", db_default="")
    product = models.TextField(blank=True, default="")
    product_raw = models.TextField(blank=True, default="")
    # Chuỗi tổng hợp SKU của MỌI dòng (join). Đơn sàn nhiều dòng dễ vượt 100 ký tự
    # → TextField (khác OrderItem.sku/Product.sku là SKU đơn lẻ, giữ CharField(100)).
    sku = models.TextField(blank=True, default="")
    quantity = models.PositiveIntegerField(default=0)
    channel_owner = models.CharField(max_length=255, blank=True, default="")
    url_page = models.URLField(max_length=1024, blank=True, default="")
    # ID quảng cáo Facebook sinh ra đơn này, lấy từ query param `ad_id` trên URL
    # landing. Rỗng = không xác định được (đơn trước go-live, đơn messenger/hotline,
    # hoặc landing chưa gắn tag). Join thẳng với facebook_ads.fb_ad_id.
    fb_ad_id = models.CharField(max_length=64, blank=True, default="", db_default="")
    # ID chiến dịch Facebook — cùng nguồn với fb_ad_id nhưng đọc từ `utm_id` (Meta
    # điền {{campaign.id}}); ad_id đọc từ `utm_content`. Xem meta_attribution.py.
    # Nối với facebook_ad_campaigns.fb_campaign_id để quy đơn/doanh thu về campaign.
    fb_campaign_id = models.CharField(max_length=64, blank=True, default="", db_default="")
    # KHÔNG unique theo (channel, external_order_id) — CẦN QUYẾT ĐỊNH nghiệp vụ
    # (audit 2026-09-23 T2/P2-7): prod 23/09 có 2.655 cặp trùng active (đơn sàn
    # tách/ghi lại). Chưa rõ trùng nào là hợp lệ nên chưa siết; xem
    # docs/runbooks/2026-09-23-audit-ops.md mục Database.
    external_order_id = models.CharField(max_length=100, blank=True, default="")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_orders",
        null=True,
        blank=True,
    )
    status = models.PositiveSmallIntegerField(choices=OrderStatus.choices, default=OrderStatus.NEW)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    booking_voucher_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_after_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Tiền khách TỰ ĐĂNG KÝ trên landing lúc đơn đổ về (trước khi sale tư vấn).
    # Ghi một lần duy nhất ở create_order_from_landing, không bao giờ sửa lại
    # (cùng họ bất biến với has_address). Đơn không qua landing giữ 0.
    landing_confirmed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, db_default=0
    )
    other_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=100, blank=True, default="")
    shipping_code = models.CharField(max_length=100, blank=True, default="")
    shipping_provider = models.CharField(max_length=50, blank=True, default="", db_default="")
    shipping_service = models.CharField(max_length=50, blank=True, default="", db_default="")
    weight = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    dimensions = models.CharField(max_length=255, blank=True, default="")
    branch_id = models.CharField(max_length=100, blank=True, default="")
    order_type = models.CharField(max_length=30, choices=OrderType.choices, default=OrderType.NORMAL)
    # Nhãn nghiệp vụ do nhân viên gán tay, CHỈ dùng cho đơn KOC. Trước đây viết tay
    # vào ô ghi chú nên không lọc được; tách ra field riêng để ghi chú quay về đúng
    # vai trò ghi chú vấn đề phát sinh.
    koc_order_type = models.CharField(
        max_length=32, choices=KocOrderType.choices, blank=True, default=""
    )
    order_source = models.CharField(max_length=30, choices=OrderSource.choices, null=True, blank=True)
    # ── Trạng thái THÔ của đơn mirror (đơn nội bộ luôn để rỗng) ───────────────
    # Hai cột vì hai nguồn khác nhau và thường xuyên lệch nhau: đối tác cập nhật
    # trạng thái của họ theo nhịp riêng, chậm/nhanh hơn đơn vị vận chuyển. Gộp
    # một cột là mất đúng độ lệch cần nhìn khi số liệu hai bên không khớp.
    #
    # Cố ý KHÔNG map sang `status`: chưa có bản chốt danh sách mã trạng thái từ
    # đối tác nên map lúc này là đoán, mà đoán sai thì đơn hiện sai trạng thái và
    # không ai biết vì dữ liệu gốc đã bị nuốt. Lưu thô thì map lại lúc nào cũng
    # được, không phải kéo lại dữ liệu từ đối tác.
    #
    # Tên `partner_*` chứ không phải `foxia_*`: chỗ chứa chung cho mọi hệ thống
    # đối tác đổ đơn vào, Foxia chỉ là cái đầu tiên.
    partner_delivery_status = models.CharField(
        max_length=64, blank=True, default="", db_default="",
        help_text="Trạng thái đơn theo hệ thống đối tác (nguyên văn)",
    )
    partner_dvvc_delivery_status = models.CharField(
        max_length=64, blank=True, default="", db_default="",
        help_text="Trạng thái theo đơn vị vận chuyển mà đối tác kết nối (nguyên văn)",
    )
    vat = models.CharField(max_length=20, choices=OrderVat.choices, default=OrderVat.NO)
    # Đã thực sự xuất hoá đơn VAT cho đơn hay chưa (trạng thái), tách biệt với
    # `vat` (hình thức xuất). Mặc định False.
    is_export_vat = models.BooleanField(default=False, db_default=False)
    # Link tới hoá đơn VAT đã xuất (file/URL tra cứu). CharField để không bắt buộc
    # đúng định dạng URL chặt như URLField (link nội bộ/đường dẫn có thể không chuẩn).
    vat_link = models.CharField(max_length=500, blank=True, default="", db_default="")
    # Mốc đã gửi thông báo Lark "đơn cần xuất VAT" sau khi kship thành công.
    # Null = chưa gửi; dùng để chống gửi trùng khi job kship chạy lại.
    vat_invoice_notified_at = models.DateTimeField(null=True, blank=True)
    show_orders = models.BooleanField(default=True)
    customer_prepaid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    result = models.TextField(blank=True, default="")
    # order_code KHÔNG unique — CHỦ Ý (audit 2026-09-23 T2/P2-7): một hoá đơn
    # KiotViet có thể tách thành nhiều đơn nội bộ cùng mã. Không thêm unique.
    order_code = models.CharField(max_length=100, blank=True, default="")
    invoice_code = models.CharField(max_length=100, blank=True, default="")
    invoice_id = models.BigIntegerField(null=True, blank=True)
    invoice_status = models.PositiveSmallIntegerField(null=True, blank=True)
    kiotviet_purchase_date = models.DateTimeField(null=True, blank=True, db_index=True)
    # Mốc sale chốt đơn: thời điểm `OrderAssessment.sales_status_code` chuyển từ
    # NGOÀI nhóm chốt (CLOSED_SALES_STATUS_CODES) vào TRONG nhóm. Đi trong nội bộ
    # nhóm (vd "Đã chốt" -> "Đã lên đơn") KHÔNG đổi mốc; chốt lại sau khi đã rơi ra
    # ngoài thì GHI ĐÈ mốc mới (đúng nghĩa "vừa chốt xong"); rơi ra ngoài nhóm thì
    # giữ nguyên chứ không xoá.
    #
    # Vì sao cột nằm ở `orders` chứ không ở `order_assessments` (nơi giữ trạng
    # thái): trang đơn là endpoint nóng nhất, ORDER BY trên bảng join không dùng
    # được index — EXPLAIN prod cho cost 117.179 (seq scan + hash join + sort) so
    # với 62 khi cột nằm trên `orders`. Xem docs/orders-sort-by-closed-date.md §3.
    #
    # NULL = chưa từng chốt, hoặc đơn không đi qua luồng CRM (đơn sàn / sync
    # KiotViet / import) — 62% số đơn. Mọi sắp xếp theo cột này dùng NULLS LAST.
    sales_closed_at = models.DateTimeField(null=True, blank=True)
    is_delivery_invoice_printed = models.BooleanField(default=False)
    customer_return = models.BooleanField(default=False)
    # Ngày hoàn hàng (KH trả về). Nhập tay, độc lập với customer_return / OrderRecall.
    return_date = models.DateField(null=True, blank=True, db_index=True)
    old_invoice_code = models.CharField(max_length=100, blank=True, default="")
    old_id = models.BigIntegerField(null=True, blank=True)
    # Các mã hoá đơn TỪNG thuộc đơn này rồi bị KiotViet huỷ-xuất lại (`X` → `X.01`).
    # Webhook reattach repoint invoice_code sang mã mới; giữ mã cũ ở đây để sự kiện
    # huỷ đến muộn / cron import vẫn nhận ra chủ nhân của mã cũ và KHÔNG tạo một dòng
    # đơn huỷ trùng (audit 2026-07-14).
    reattached_invoice_codes = ArrayField(
        models.CharField(max_length=100),
        default=list,
        blank=True,
    )
    # Đơn hủy mà đơn này được clone ra khi bấm "Khôi phục". Null với đơn tạo
    # bình thường. Dùng để chặn khôi phục cùng một đơn hủy hai lần và để truy
    # vết đơn mới ↔ đơn hủy gốc. SET_NULL: xóa đơn hủy cũ không được kéo theo
    # đơn đang bán. db_index=False vì index đã khai báo tường minh ở Meta.
    restored_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="restored_orders",
        null=True,
        blank=True,
        db_index=False,
    )
    feedback = models.TextField(blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        # Django dùng base manager để nạp quan hệ (FK forward, cascade). Nó KHÔNG
        # được lọc, nếu không thì đọc `order_item.order` của một đơn mirror sẽ
        # raise DoesNotExist — tài liệu Django cảnh báo đúng điều này.
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["created_at"], name="order_created_at_idx"),
            # Hai index cho cùng một cột vì NULLS LAST không đối xứng khi quét
            # ngược: index DESC NULLS LAST quét ngược ra ASC NULLS FIRST, tức 62%
            # đơn chưa chốt sẽ dồn lên đầu. Cần đủ cả hai chiều để "Ngày chốt: Mới
            # → Cũ" lẫn "Cũ → Mới" đều đi bằng index. Partial theo deleted_at để
            # khớp `_base_orders_queryset` và không phình index của bảng nóng.
            models.Index(
                F("sales_closed_at").desc(nulls_last=True),
                F("id").desc(),
                condition=Q(deleted_at__isnull=True),
                name="ord_sales_closed_desc_idx",
            ),
            models.Index(
                F("sales_closed_at").asc(nulls_last=True),
                F("id").asc(),
                condition=Q(deleted_at__isnull=True),
                name="ord_sales_closed_asc_idx",
            ),
            # Sắp xếp "Tổng sau giảm giá" hai chiều. Cột NOT NULL nên MỘT index
            # quét ngược là đủ cho chiều còn lại (khác sales_closed_at ở trên).
            models.Index(
                fields=["total_after_discount", "id"],
                condition=Q(deleted_at__isnull=True),
                name="ord_total_after_disc_idx",
            ),
            # Range ngày trong MỘT kênh (apps.conversion_feed) — xem migration 0148.
            models.Index(
                fields=["channel", "created_at"], condition=Q(deleted_at__isnull=True), name="ord_channel_created_idx"
            ),
            models.Index(fields=["status"], name="status_idx"),
            # Đơn mirror là tập nhỏ so với toàn bảng: partial index phục vụ trang
            # báo cáo Foxia mà không phình index chung của bảng nóng.
            models.Index(
                fields=["order_source", "-created_at"],
                condition=Q(deleted_at__isnull=True),
                name="ord_src_created_idx",
            ),
            models.Index(fields=["channel", "status", "created_at"], name="order_ch_st_cr_idx"),
            models.Index(fields=["customer", "created_at"], name="order_customer_created_idx"),
            models.Index(fields=["phone"], name="order_phone_idx"),
            models.Index(fields=["phone_normalized"], name="order_phone_norm_idx"),
            models.Index(fields=["deleted_at"], name="order_deleted_idx"),
            models.Index(fields=["-created_at"], condition=Q(deleted_at__isnull=True), name="ord_act_cr_desc_idx"),
            models.Index(fields=["shipping_type", "-created_at"], name="order_shiptype_created_idx"),
            # Partial: gần như toàn bộ orders có koc_order_type rỗng (chỉ đơn KOC được
            # gán nhãn mới có giá trị) — index đầy đủ chỉ tốn write overhead. Ghép
            # created_at DESC vì bộ lọc luôn đi kèm khoảng thời gian và list sắp xếp theo nó.
            models.Index(
                fields=["koc_order_type", "-created_at"],
                condition=~Q(koc_order_type=""),
                name="order_koc_type_created_idx",
            ),
            models.Index(
                fields=["channel", "status", "-created_at"],
                condition=Q(deleted_at__isnull=True),
                name="ord_act_ch_st_cr_idx",
            ),
            # Resale-tracking listing sorts delivered+active orders by -updated_at.
            # 90% of orders are status=3/active, so status_idx is useless here; this
            # partial index lets the LIMIT-20 page walk in updated_at order without
            # seq-scanning + sorting the whole table (270ms -> ~1ms).
            models.Index(
                fields=["-updated_at", "-id"],
                condition=Q(deleted_at__isnull=True, status=3),
                name="ord_resale_delivered_upd_idx",
            ),
            # GTC aggregation (buyers CTE + _metrics_map) lọc status=3 & active rồi GROUP BY customer.
            models.Index(
                fields=["customer", "-created_at"],
                condition=Q(deleted_at__isnull=True, status=3),
                name="ord_gtc_customer_idx",
            ),
            # Bộ lọc "Không có địa chỉ" (đơn ingest về thiếu địa chỉ) — tập rất nhỏ so
            # với toàn bảng, partial index theo created_at phục vụ cả lọc lẫn sort ngày.
            models.Index(
                fields=["created_at"],
                condition=Q(has_address=False),
                name="ord_no_address_created_idx",
            ),
            models.Index(
                fields=["order_code"],
                condition=Q(order_code__gt=""),
                name="order_order_code_idx",
            ),
            models.Index(
                fields=["invoice_code"],
                condition=Q(invoice_code__gt=""),
                name="order_invoice_code_idx",
            ),
            # `phone` (cột THÔ) giữ trigram GIN: search đơn dùng `phone__contains` trên
            # CẢ `phone_normalized` lẫn `phone`, vì đơn có SĐT không chuẩn hóa được
            # (ô nhập thiếu số) sẽ có `phone_normalized` rỗng và biến mất khỏi tìm kiếm
            # nếu chỉ dựa vào cột chuẩn hóa (repositories.py, customer_resale_service.py).
            GinIndex(fields=["phone"], name="order_phone_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Substring search (`q`) ILIKE %...% on these code columns needs trigram GIN;
            # the plain/partial B-tree indexes above only serve exact/prefix matches.
            GinIndex(fields=["invoice_code"], name="order_invoice_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["old_invoice_code"], name="order_old_inv_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            # `list_kiotviet_jobs` tra đơn sàn bằng `external_order_id__contains=q`
            # (`LIKE '%q%'`) — trước đây cột này KHÔNG có index nào nên mỗi lần gõ ô tìm
            # kiếm là một seq-scan toàn bảng `orders`. Trigram GIN vì lookup là substring
            # ở giữa chuỗi, B-tree chỉ phục vụ được exact/prefix (audit 2026-07-26 C2).
            GinIndex(fields=["external_order_id"], name="order_ext_order_id_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Lookup `reattached_invoice_codes__contains=[code]` (webhook huỷ muộn / cron
            # import) — GIN mảng, không phải trigram.
            GinIndex(fields=["reattached_invoice_codes"], name="ord_reattached_codes_gin"),
            # `q` search always runs `shipping_code LIKE '%code%'`; needs trigram GIN.
            # (order_ship_trgm_idx was dropped in 0032 and never re-added → seq scan.)
            GinIndex(fields=["shipping_code"], name="order_ship_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Substring (partial) phone search runs `phone_normalized LIKE '%digits%'`;
            # the plain order_phone_norm_idx (B-tree) only serves exact/prefix, so a
            # trigram GIN is needed for the contains form.
            GinIndex(fields=["phone_normalized"], name="order_phone_norm_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Search theo TÊN khách (`q`) dùng `name__icontains` → Postgres sinh
            # `UPPER((name)::text) LIKE UPPER(%s)` (name là varchar nên có cast ::text).
            # Index phải khớp CHÍNH XÁC biểu thức đó (Upper(Cast(name AS text))) thì mới
            # được dùng; nếu chỉ Upper(name) sẽ lệch cast → planner bỏ index, seq-scan.
            GinIndex(
                OpClass(Upper(Cast("name", output_field=TextField())), name="gin_trgm_ops"),
                name="order_name_upper_trgm_idx",
            ),
            # `order_code` substring search (`q`) chạy `order_code LIKE '%code%'`; cần
            # trigram GIN (order_order_code_idx là B-tree partial chỉ phục vụ exact/prefix).
            GinIndex(fields=["order_code"], name="order_order_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Bộ lọc "Sản phẩm" theo text khớp cả `orders.product` (chuỗi sản phẩm denormalized
            # trên đơn). `product__icontains` sinh `UPPER(product) LIKE UPPER(%s)` (product là
            # TEXT nên KHÔNG có cast ::text) → functional trigram trên Upper(product) để không
            # seq-scan toàn bảng orders.
            GinIndex(OpClass(Upper("product"), name="gin_trgm_ops"), name="order_product_upper_trgm_idx"),
            # returns_sync_service._find_order lọc theo (kiotviet_channel, invoice_id)
            # mỗi lần đồng bộ trạng thái trả hàng — partial vì invoice_id chỉ có ở đơn
            # KiotViet còn sống (audit followup 2026-07 mục 4.1, phải land trước backfill).
            models.Index(
                fields=["kiotviet_channel", "invoice_id"],
                condition=Q(invoice_id__isnull=False, deleted_at__isnull=True),
                name="ord_kv_channel_invoice_idx",
            ),
            # status_counts_for đếm chip "Đã hủy" qua invoice_status=2 OR status=6; tập
            # invoice_status=2 rất nhỏ so với toàn bảng (audit followup 2026-07 mục 4.2).
            models.Index(
                fields=["invoice_status"],
                condition=Q(invoice_status=2),
                name="ord_inv_status_cancel_idx",
            ),
            # Tập đơn có ad_id nhỏ hơn nhiều toàn bảng → partial index.
            models.Index(
                fields=["fb_ad_id"],
                condition=Q(fb_ad_id__gt=""),
                name="ord_fb_ad_id_idx",
            ),
            models.Index(
                fields=["fb_campaign_id"],
                condition=Q(fb_campaign_id__gt=""),
                name="ord_fb_campaign_id_idx",
            ),
            # Task khôi phục hỏi "đơn hủy này đã có bản clone còn sống chưa" qua
            # `restored_orders`. Tập đơn có restored_from IS NOT NULL rất nhỏ so
            # với toàn bảng → partial index.
            models.Index(
                fields=["restored_from"],
                condition=Q(restored_from__isnull=False),
                name="ord_restored_from_idx",
            ),
            models.Index(
                fields=["-created_at"],
                name="order_direct_deferred_idx",
                condition=models.Q(direct_dispatch_deferred=True),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["kiotviet_channel", "invoice_code"],
                condition=Q(kiotviet_channel__isnull=False, invoice_code__gt="", deleted_at__isnull=True),
                name="uniq_order_kv_inv_code_active",
            ),
            models.UniqueConstraint(
                fields=["kiotviet_channel", "old_invoice_code"],
                condition=Q(kiotviet_channel__isnull=False, old_invoice_code__gt="", deleted_at__isnull=True),
                name="uniq_order_kv_old_inv_code_active",
            ),
            # CHECK tiền ≥ 0 (audit 2026-09-23 T2/P2-9). Chỉ áp lên cột prod đã đo
            # 0 dòng vi phạm (23/09/2026): discount_amount, shipping_fee. KHÔNG áp
            # total_amount / total_after_discount — prod có 8 đơn âm (điều chỉnh
            # hoá đơn KiotViet) cần quyết định nghiệp vụ trước khi siết.
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0), name="ord_discount_nonneg"
            ),
            models.CheckConstraint(
                condition=Q(shipping_fee__gte=0), name="ord_shipping_fee_nonneg"
            ),
            # KHÔNG CHECK status theo OrderStatus dù prod 23/09 mọi giá trị đều hợp lệ:
            # sync KiotViet ghi thẳng int(status_val) không qua whitelist
            # (_import_service_upsert_mixin.py; test_overview_report/test_sale_reports
            # ghim mã lạ 14/99 rơi vào rổ "chờ giao"). Siết ở DB = sync chết ngay khi
            # KiotViet thêm mã mới.
        ]

    def save(self, *args, **kwargs):
        # Derive phone_normalized from phone on every write so search never has to
        # JOIN customers.
        self.phone_normalized = normalize_phone(self.phone) if self.phone else ""
        # Đơn sàn: mã hoá đơn mang prefix nhận diện (HDTTS_/HDSPE_) là bằng chứng
        # chắc chắn nguồn đơn — enforce tại save() để phủ mọi luồng ghi (form,
        # cron KiotViet, import), kể cả khi nguồn đã bị gắn tay khác đi.
        marketplace_source = OrderSource.from_invoice_code(self.invoice_code)
        source_changed = marketplace_source is not None and self.order_source != marketplace_source
        if source_changed:
            self.order_source = marketplace_source
        # Callers that pass update_fields=["phone", ...] (inline edit, KiotViet
        # --update-phone) would otherwise drop the recomputed value — pull it in.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if "phone" in update_fields:
                update_fields.add("phone_normalized")
            if source_changed:
                update_fields.add("order_source")
            kwargs["update_fields"] = update_fields
        created = self._state.adding
        super().save(*args, **kwargs)
        if update_fields is not None and not update_fields:
            # save(update_fields=[]) là no-op của Django (không ghi, không post_save).
            return
        # Domain event thay cho post_save mà customers/cnv_loyalty từng móc thẳng vào
        # model (audit 2026-09-23 T3/P2-4). Phát ở đây vì Order được .save() từ rất
        # nhiều luồng (form, webhook, cron import, update_or_create) — không có nơi
        # ghi tập trung. Import trong hàm: order_events import OrderStatus từ module
        # này, nạp ở top-level là vòng.
        from apps.common.events import event_bus
        from apps.orders.contracts.order_events import OrderSaved

        event_bus.publish_on_commit(
            OrderSaved(
                order_id=self.pk,
                customer_id=self.customer_id,
                order_source=self.order_source or "",
                created=created,
                is_deleted=self.deleted_at is not None,
                update_fields=None if update_fields is None else tuple(sorted(update_fields)),
            )
        )


class NeedGroup(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name_vi = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "need_groups"
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="need_group_active_sort_idx"),
        ]
        ordering = ["sort_order", "id"]


class NeedType(models.Model):
    code = models.CharField(max_length=64, unique=True)
    group = models.ForeignKey(NeedGroup, on_delete=models.PROTECT, related_name="need_types")
    name_vi = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "need_types"
        indexes = [
            models.Index(fields=["group", "is_active", "sort_order"], name="need_type_grp_act_sort_idx"),
        ]
        ordering = ["group__sort_order", "sort_order", "id"]


class OrderAssessment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="assessment")
    # 5 field dưới bỏ db_index=True (audit 2026-07 mục 3.3): Meta.indexes đã có
    # index cùng cột (ord_assessment_*_idx) — db_index tạo index thứ hai trùng lặp.
    customer_classification_code = models.CharField(max_length=64, null=True, blank=True)
    customer_attitude_raw = models.CharField(max_length=255, blank=True, default="")
    sales_status_code = models.CharField(max_length=64, null=True, blank=True)
    # not_closed_reason_code/occupation_code/pain_level_code bỏ db_index=True (audit 2026-07
    # follow-up task 10): chỉ dùng trong aggregation/values() để dựng report, không có
    # equality/__in filter nào — single-column index low-selectivity không mang lại lợi ích,
    # chỉ tốn write overhead. GIỮ post_purchase_rating_code vì có __in filter ở 3 service customers.
    not_closed_reason_code = models.CharField(max_length=64, null=True, blank=True)
    delivery_business_status_code = models.CharField(max_length=64, null=True, blank=True)
    bomb_reason_group_code = models.CharField(max_length=64, null=True, blank=True)
    bomb_reason_type_code = models.CharField(max_length=64, null=True, blank=True)
    bomb_reason_text = models.TextField(blank=True, default="")
    internal_note = models.TextField(blank=True, default="")
    # Mã nhóm tuổi (crm_catalog.AGE_GROUP_OPTIONS), KHÔNG phải text tự do.
    # Tên cột giữ nguyên vì là column key trong cấu hình bảng lưu ở client.
    age_text = models.CharField(max_length=32, blank=True, default="")
    occupation_code = models.CharField(max_length=64, null=True, blank=True)
    delax_source_codes = ArrayField(models.CharField(max_length=64), blank=True, default=list)
    pain_level_code = models.CharField(max_length=64, null=True, blank=True)
    pain_duration_code = models.CharField(max_length=64, null=True, blank=True)
    lifestyle_habit_codes = ArrayField(models.CharField(max_length=64), blank=True, default=list)
    used_massage_before = models.BooleanField(null=True, blank=True)
    relax_time_codes = ArrayField(models.CharField(max_length=64), blank=True, default=list)
    purchase_barrier_codes = ArrayField(models.CharField(max_length=64), blank=True, default=list)
    comparing_brand_text = models.TextField(blank=True, default="")
    call_duration_code = models.CharField(max_length=64, null=True, blank=True)
    post_purchase_feedback_text = models.TextField(blank=True, default="")
    post_purchase_rating_code = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    potential_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    next_followup_at = models.DateTimeField(null=True, blank=True)
    # Giá trị các trường động định nghĩa trong AssessmentFieldDef.
    # Shape: {"<field_code>": str | list[str] | int | float | bool}
    extra_fields = models.JSONField(default=dict, blank=True)
    # Giỏ mở: AI ghi nhận thông tin đáng chú ý chưa thuộc trường nào. Append-only,
    # dedupe theo (topic, content), tối đa 100 item (xem assessment_ai_fill_service).
    # Shape: [{"topic": str, "content": str, "quote": str, "call_record_id": int, "at": iso8601}]
    ai_open_insights = models.JSONField(default=list, blank=True)
    # Field nào do AI điền từ transcript + trích dẫn làm căn cứ. Một JSONB thay vì
    # 25 cột `*_source`: thêm field CRM mới không phải đụng schema.
    # Shape: {"<field>": {"call_record_id": int, "quote": str, "at": iso8601}}
    # Sale sửa đè field nào thì key đó bị xoá (xem _upsert_order_assessment).
    ai_filled_fields = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_order_assessments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_assessments"
        indexes = [
            models.Index(fields=["customer_classification_code"], name="ord_assessment_cls_idx"),
            models.Index(fields=["sales_status_code"], name="ord_assessment_sales_idx"),
            models.Index(fields=["delivery_business_status_code"], name="ord_assessment_delivery_idx"),
            models.Index(fields=["bomb_reason_group_code"], name="ord_assessment_bomb_group_idx"),
            models.Index(fields=["bomb_reason_type_code"], name="ord_assessment_bomb_type_idx"),
            models.Index(fields=["created_at"], name="ord_assessment_created_idx"),
            models.Index(fields=["updated_at"], name="ord_assessment_updated_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(potential_level__gte=1, potential_level__lte=5) | Q(potential_level__isnull=True),
                name="ord_assessment_potential_1_5",
            ),
        ]


class OrderNeed(models.Model):
    order_assessment = models.ForeignKey(OrderAssessment, on_delete=models.CASCADE, related_name="needs")
    need_type = models.ForeignKey(NeedType, on_delete=models.PROTECT, related_name="order_needs")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_needs"
        indexes = [
        ]
        constraints = [
            models.UniqueConstraint(fields=["order_assessment", "need_type"], name="uniq_order_need_assessment_type"),
        ]


class FieldKind(models.TextChoices):
    SINGLE_CHOICE = "single_choice", "Chọn một"
    MULTI_CHOICE = "multi_choice", "Chọn nhiều"
    TEXT = "text", "Text tự do"
    NUMBER = "number", "Số"
    BOOL = "bool", "Có/Không"


# Code trường động không được trùng cột thật / key hệ thống của payload CRM.
RESERVED_FIELD_CODES = frozenset(
    {
        "needs",
        "extra_fields",
        "ai_filled_fields",
        "ai_open_insights",
        "events",
        # Key hợp đồng AI extract tĩnh (schema/normalize) — không cho field động đè.
        "need_type_codes",
        "evidence",
        "open_insights",
    }
)


class AssessmentFieldDef(models.Model):
    """Registry trường đánh giá động — thêm trường CRM mới là thao tác dữ liệu
    trong Django admin, không cần migration. Giá trị lưu ở OrderAssessment.extra_fields."""

    code = models.SlugField(max_length=64, unique=True)
    label = models.CharField(max_length=255)
    kind = models.CharField(max_length=32, choices=FieldKind.choices)
    # [{"code": str, "label": str}] — bắt buộc với single/multi_choice, rỗng với kind khác.
    options = models.JSONField(default=list, blank=True)
    ai_enabled = models.BooleanField(default=False)
    ai_hint = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_field_defs"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.code} ({self.get_kind_display()})"

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        from apps.orders.assessment_fields import ASSESSMENT_FIELD_NAMES

        if self.code in RESERVED_FIELD_CODES or self.code in ASSESSMENT_FIELD_NAMES:
            raise ValidationError({"code": f"'{self.code}' trùng cột có sẵn hoặc key hệ thống."})

        is_choice = self.kind in (FieldKind.SINGLE_CHOICE, FieldKind.MULTI_CHOICE)
        options = self.options or []
        if is_choice:
            if not options:
                raise ValidationError({"options": "Kind choice bắt buộc có options."})
            codes = []
            for item in options:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("code"), str)
                    or not item.get("code")
                    or not isinstance(item.get("label"), str)
                    or not item.get("label")
                ):
                    raise ValidationError({"options": 'Mỗi option phải là {"code": str, "label": str}.'})
                codes.append(item["code"])
            if len(codes) != len(set(codes)):
                raise ValidationError({"options": "Option code bị trùng."})
        elif options:
            raise ValidationError({"options": f"Kind '{self.kind}' không dùng options."})

        # Đổi kind khi đã có dữ liệu = đọc sai dữ liệu cũ. Muốn đổi thì tạo field mới.
        if self.pk:
            current = AssessmentFieldDef.objects.filter(pk=self.pk).values_list("kind", flat=True).first()
            if current and current != self.kind:
                if OrderAssessment.objects.filter(extra_fields__has_key=self.code).exists():
                    raise ValidationError({"kind": "Đã có dữ liệu — không đổi kind, hãy tạo field mới."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from apps.orders.dynamic_assessment import invalidate_registry_cache

        invalidate_registry_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from apps.orders.dynamic_assessment import invalidate_registry_cache

        invalidate_registry_cache()
        return result


class OrderRecall(models.Model):
    """Phiếu trả hàng của đơn (1-1 với Order). Chỉ còn `return_slip_code` —
    job returns_sync_service (KiotViet) ghi UNION vào đây theo invoice_id.
    Mã/ngày thu hồi đã tách sang OrderRecallEntry (1-N)."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="recall")
    return_slip_code = ArrayField(                                                 # Phiếu trả hàng (nhiều mã)
        models.CharField(max_length=64),
        default=list,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_order_recalls",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_recalls"
        indexes = [
            models.Index(fields=["created_at"], name="order_recall_created_idx"),
        ]


class OrderRecallEntry(models.Model):
    """Một đợt thu hồi của đơn KOC. Một đơn có N đợt, mỗi đợt một mã và cặp ngày
    của riêng nó — trước đây N mã dùng chung một cặp ngày nên nhìn vào không biết
    mã nào ứng với ngày nào."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="recall_entries")
    recall_code = models.CharField(max_length=64, blank=True, default="")          # Mã đơn thu hồi
    recall_created_date = models.DateField(null=True, blank=True)                  # Ngày tạo đơn thu hồi
    recall_received_date = models.DateField(null=True, blank=True)                 # Ngày nhận hàng thu hồi
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_order_recall_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_recall_entries"
        # Thứ tự nhóm = thứ tự tạo. Không có cột sort_order: không có nhu cầu sắp
        # xếp lại, mà một cột thứ tự không ai đổi là cột chết.
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "id"], name="recall_entry_order_idx"),
            # btree, KHÔNG phải GIN: mỗi dòng một mã nên equality trên btree vừa
            # nhanh hơn vừa nhỏ hơn (đảo ngược có chủ đích lựa chọn GIN của MR !1228,
            # vốn chỉ cần khi cột còn là mảng).
            models.Index(fields=["recall_code"], name="recall_entry_code_idx"),
        ]


class OrderJourneyEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="journey_events")
    # Bỏ db_index (audit 2026-07 mục 3.3): composite ord_journey_type_created_idx
    # (event_type, created_at) đã phủ mọi query lọc theo event_type.
    event_type = models.CharField(max_length=64)
    from_code = models.CharField(max_length=64, null=True, blank=True)
    to_code = models.CharField(max_length=64, null=True, blank=True)
    note = models.TextField(blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    payload_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "order_journey_events"
        indexes = [
            models.Index(fields=["order", "created_at"], name="ord_journey_order_created_idx"),
            models.Index(fields=["event_type", "created_at"], name="ord_journey_type_created_idx"),
            models.Index(
                fields=["order", "event_type", "created_at"],
                name="ord_journey_ord_type_cr_idx",
            ),
        ]


class OrderMilestone(models.TextChoices):
    CREATED = "created", "Đơn đổ về"
    INVOICED = "invoiced", "Đã tạo hóa đơn"


class OrderMilestoneSnapshot(models.Model):
    """Ảnh chụp bất biến giá trị đơn tại một mốc vòng đời (spec 2026-08-04).

    Ghi-một-lần: mọi điểm ghi dùng get_or_create theo (order, milestone) — webhook
    bắn lại / cron chạy lại / hóa đơn reissue không tạo dòng mới, không ghi đè.
    Là dữ liệu ĐỐI SOÁT, không phải dữ liệu giao dịch: lỗi ghi chỉ log, không
    được làm vỡ luồng tạo đơn/gán hóa đơn.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="milestone_snapshots")
    milestone = models.CharField(max_length=20, choices=OrderMilestone.choices)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_after_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=0)
    sku = models.TextField(blank=True, default="")
    # Số tiền đến từ đâu: payload | text | price_book | none (mốc created);
    # invoice (mốc invoiced); cloned (copy từ đơn gốc); seeded (suy ngược một lần
    # bởi seed_order_milestone_snapshots cho đơn có trước bảng này).
    amount_source = models.CharField(max_length=20, blank=True, default="")
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_milestone_snapshots"
        constraints = [
            models.UniqueConstraint(fields=["order", "milestone"], name="uniq_order_milestone"),
        ]


class OrderKiotVietJobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class OrderKiotVietJob(models.Model):
    ACTION_PUSH = "push"
    ACTION_CREATE_INVOICE = "create_invoice"
    ACTION_SYNC_SHIPPING = "sync_shipping"
    ACTION_SYNC_STATUS = "sync_status"
    ACTION_CREATE_KSHIP_DELIVERY = "create_kship_delivery"
    ACTION_CANCEL_INVOICE = "cancel_invoice"
    ACTION_CHOICES = (
        (ACTION_PUSH, "Push order"),
        (ACTION_CREATE_INVOICE, "Create invoice"),
        (ACTION_SYNC_SHIPPING, "Sync shipping code"),
        (ACTION_SYNC_STATUS, "Sync status"),
        (ACTION_CREATE_KSHIP_DELIVERY, "Create KShip delivery"),
        (ACTION_CANCEL_INVOICE, "Cancel invoice"),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="kiotviet_jobs")
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    status = models.CharField(max_length=20, choices=OrderKiotVietJobStatus.choices, default=OrderKiotVietJobStatus.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # KShip delivery specific fields
    kship_provider_code = models.CharField(max_length=50, blank=True, default="", null=True)
    kship_service_code = models.CharField(max_length=50, blank=True, default="", null=True)
    kship_note = models.TextField(blank=True, default="", null=True)

    # Quy định xem hàng (SERVICE_EXTRA.ShipperNote). Rỗng = job tạo trước khi có tính
    # năng chọn, hoặc client không gửi — payload builder tự rơi về mặc định.
    kship_shipper_note = models.CharField(max_length=32, blank=True, db_default="", default="")

    # Lựa chọn cho các SERVICE_EXTRA hãng bắt buộc, dạng {"<code>": "<giá trị>"}
    # (hiện chỉ SPX "PickShift" — ca lấy hàng). Rỗng = job cũ hoặc hãng không cần;
    # gateway tự lấy giá trị đầu tiên còn khả dụng.
    kship_service_extra = models.JSONField(default=dict, blank=True, null=True)

    # ID bản ghi KshipSenderAddress được chọn (không FK cứng: bản ghi bị xóa
    # không được chặn queue — resolver tự fallback rule mặc định).
    kship_sender_address_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "order_kiotviet_jobs"
        indexes = [
            models.Index(fields=["status", "created_at"], name="ord_kv_job_st_cr_idx"),
            models.Index(fields=["order", "action", "created_at"], name="ord_kv_job_or_ac_cr_idx"),
            models.Index(fields=["order", "-created_at", "-id"], name="ord_kv_job_or_cr_id_idx"),
            models.Index(fields=["order", "status", "-created_at"], name="ord_kv_job_or_st_cr_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "action"],
                condition=Q(status__in=[OrderKiotVietJobStatus.PENDING, OrderKiotVietJobStatus.RUNNING]),
                name="uniq_ord_kv_job_act_ac",
            )
        ]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    product_name = models.CharField(max_length=255, blank=True, default="")
    sku = models.CharField(max_length=100, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Tiền giảm cho MỖI sản phẩm (cùng nghĩa với cột "Giảm giá" trên từng dòng hoá
    # đơn KiotViet): line_total = (unit_price − discount) × quantity.
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Chỉ có giá trị khi sale nhập giảm giá theo % — để form mở lại hiển thị đúng
    # chế độ %, còn tiền giảm thật luôn nằm ở `discount`.
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_items"
        indexes = [
            models.Index(fields=["order", "created_at"], name="order_item_order_cr_idx"),
            GinIndex(fields=["sku"], name="ord_item_sku_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["product_name"], name="ord_item_name_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="uniq_order_item_order_product"),
            # CHECK ≥ 0 cho unit_price/discount (prod 23/09: 0 vi phạm). KHÔNG áp
            # line_total — prod có 10 dòng âm (điều chỉnh) cần quyết định trước.
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="ord_item_unit_price_nonneg"
            ),
            models.CheckConstraint(condition=Q(discount__gte=0), name="ord_item_discount_nonneg"),
        ]


class LandingIngestTraceStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    VALIDATION_FAILED = "validation_failed", "Validation failed"
    ENQUEUE_FAILED = "enqueue_failed", "Enqueue failed"
    DRY_RUN_SKIPPED = "dry_run_skipped", "Dry-run skipped"
    CREATED = "created", "Order created"
    TASK_FAILED = "task_failed", "Task failed"
    REJECTED_BLOCKED = "rejected_blocked", "Rejected - blocklist"
    REJECTED_DUPLICATE = "rejected_duplicate", "Rejected - duplicate"
    REJECTED_CLAIMED = "rejected_claimed", "Rejected - claimed by other channel"


class IngestSource(models.TextChoices):
    LANDING = "landing", "Landing page"
    LARK = "lark", "Lark"
    KIOTVIET = "kiotviet", "KiotViet webhook"
    EXCEL = "excel", "Excel import"
    TEXT = "text", "Text import"
    MANUAL = "manual", "Manual create"


class LandingIngestTrace(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=32,
        choices=LandingIngestTraceStatus.choices,
        default=LandingIngestTraceStatus.RECEIVED,
    )
    source = models.CharField(
        max_length=20,
        choices=IngestSource.choices,
        default=IngestSource.LANDING,
    )
    is_dry_run = models.BooleanField(default=False)

    phone = models.CharField(max_length=30, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    raw_address = models.TextField(blank=True, default="")
    product_code = models.CharField(max_length=100, blank=True, default="")
    url_page = models.CharField(max_length=1024, blank=True, default="")

    payload = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(null=True, blank=True)

    task_id = models.CharField(max_length=64, blank=True, default="")
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landing_ingest_traces",
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "landing_ingest_traces"
        indexes = [
            models.Index(fields=["-created_at"], name="land_ing_trace_cr_idx"),
            models.Index(fields=["status", "-created_at"], name="land_ing_trace_st_cr_idx"),
            models.Index(fields=["phone"], name="land_ing_trace_phone_idx"),
            models.Index(fields=["is_dry_run", "-created_at"], name="land_ing_trace_dry_cr_idx"),
            models.Index(fields=["source", "-created_at"], name="land_ing_trace_src_cr_idx"),
        ]


class PhoneBlocklistReason(models.TextChoices):
    SPAM = "spam", "Spam"
    BOMB = "bomb", "Bomb (multiple failed delivery)"
    BLACKLIST = "blacklist", "Blacklist (manual)"
    DUPLICATE_ABUSE = "duplicate_abuse", "Duplicate abuse"
    OTHER = "other", "Other"


class PhoneBlocklist(models.Model):
    """Phone numbers rejected at ingest time (spam, bomb, blacklist)."""

    phone_normalized = models.CharField(max_length=20, unique=True)
    reason = models.CharField(
        max_length=32,
        choices=PhoneBlocklistReason.choices,
        default=PhoneBlocklistReason.BLACKLIST,
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_phone_blocklist_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "phone_blocklists"
        indexes = [
            models.Index(fields=["-created_at"], name="phone_blk_cr_idx"),
            models.Index(fields=["reason", "-created_at"], name="phone_blk_reason_cr_idx"),
        ]


class PhoneClaim(models.Model):
    """First-touch channel ownership for a phone number.

    The first channel to successfully ingest a given phone keeps ownership
    permanently. Subsequent attempts from other channels are rejected to
    prevent unhealthy competition between sales channels.
    """

    phone_normalized = models.CharField(max_length=20, unique=True)
    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        related_name="phone_claims",
    )
    first_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="phone_claim_origin",
    )
    claimed_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    hit_count = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "phone_claims"
        indexes = [
            models.Index(fields=["channel", "-claimed_at"], name="phone_claim_ch_cr_idx"),
            models.Index(fields=["-last_seen_at"], name="phone_claim_last_seen_idx"),
        ]


class KshipNotePreset(models.Model):
    """Preset note text users can quickly insert into KShip delivery notes."""

    text = models.CharField(max_length=500)
    sort_order = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_kship_note_presets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "kship_note_presets"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["sort_order", "id"], name="kship_note_sort_idx"),
        ]


class KshipSenderAddress(models.Model):
    """Địa chỉ điểm gửi hàng cho luồng tạo vận đơn KShip.

    location_id/ward_id là ID nội bộ KiotViet (KShip) — nhập tay khi thêm mới.
    `code` chỉ dành cho 2 bản ghi seed để rule mặc định theo kênh/branch trỏ vào;
    bản ghi thường để NULL (không lưu chuỗi rỗng để không vướng unique).
    """

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, null=True, blank=True, default=None)
    location_id = models.IntegerField()
    ward_id = models.IntegerField()
    address = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20)
    sort_order = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_kship_sender_addresses",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "kship_sender_addresses"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["sort_order", "id"], name="kship_sender_sort_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = None
        super().save(*args, **kwargs)


class SaleReminderKind(models.TextChoices):
    NEED_MORE_CONSULTING_D2 = "need_more_consulting_d2", "Tư vấn thêm — D+2"
    NEED_MORE_CONSULTING_D4 = "need_more_consulting_d4", "Tư vấn thêm — D+4"
    NEED_MORE_CONSULTING_D7 = "need_more_consulting_d7", "Tư vấn thêm — D+7"
    SCHEDULED = "scheduled", "Hẹn lịch"
    CALL_BACK_LATER = "call_back_later", "Gọi lại sau"
    CLOSED_EXPRESS = "closed_express", "Đã chốt/Hỏa tốc"
    CLOSED_SCHEDULED_DATE = "closed_scheduled_date", "Đã chốt, hẹn ngày"
    RESALE_APPOINTMENT = "resale_appointment", "Hẹn resale"


class SaleReminderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class SaleReminder(models.Model):
    """Per-order Lark reminder scheduled when an assessment sales_status_code changes.

    Aggregate KNM reminders (no_answer_*) are NOT stored here; they fire from
    a stateless cron task that throttles via User.last_no_answer_reminder_at.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="sale_reminders")
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_reminders",
    )
    kind = models.CharField(max_length=40, choices=SaleReminderKind.choices)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=16,
        choices=SaleReminderStatus.choices,
        default=SaleReminderStatus.PENDING,
    )
    sales_status_code_snapshot = models.CharField(max_length=64, blank=True, default="")
    note_snapshot = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=120, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sale_reminders"
        indexes = [
            models.Index(fields=["status", "due_at"], name="sale_reminder_status_due_idx"),
            models.Index(fields=["order", "status"], name="sale_reminder_order_status_idx"),
        ]


class AdsDailyChannelLarkRecord(models.Model):
    """Map (ngày|channel_id) -> Lark Bitable record id cho bảng Ads_Daily_Channel.
    Cho phép upsert (PUT vs POST) khi đẩy lại cùng key trong cửa sổ 7 ngày."""

    record_key = models.CharField(max_length=64, unique=True, verbose_name="Record Key")  # 'YYYY-MM-DD|channel_id'
    lark_record_id = models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ads_daily_channel_lark_records"
        verbose_name = "Ads Daily Channel Lark Record"

    def __str__(self):
        return f"{self.record_key} -> {self.lark_record_id}"


class AdsDailyProductLarkRecord(models.Model):
    """Map (ngày|channel_id|product_id) -> Lark Bitable record id cho bảng Ads_Daily_Product."""

    record_key = models.CharField(max_length=96, unique=True, verbose_name="Record Key")  # 'YYYY-MM-DD|channel_id|product_id'
    lark_record_id = models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ads_daily_product_lark_records"
        verbose_name = "Ads Daily Product Lark Record"

    def __str__(self):
        return f"{self.record_key} -> {self.lark_record_id}"


class LeadAssignmentShare(models.Model):
    """Cấu hình tỉ lệ chia đơn (lead) cho từng nhân viên theo (NGÀY, CA, SHEET).

    Mỗi dòng = (ngày, ca, sheet, nhân viên, trọng số). Không có dòng nào cho một
    (ngày, ca, sheet) ⇒ luồng đó KHÔNG auto-chia ca đó. ``assigned_count`` là bộ
    đếm số đơn đã chia trong (ngày, ca, sheet), dùng cho largest-remainder.
    """

    # 3 sheet Landing + cột `resale` (chia sale resale cho cơ hội Foxia từ đơn GTC).
    SHEET_CHOICES = [(key, SHEET_LABELS[key]) for key in ASSIGNMENT_SHEETS]
    SHIFT_CHOICES = [
        ("morning", "Sáng"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]

    date = models.DateField()
    shift = models.CharField(max_length=16, choices=SHIFT_CHOICES, default="morning")
    sheet = models.CharField(max_length=16, choices=SHEET_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lead_assignment_shares",
    )
    weight = models.PositiveSmallIntegerField()
    assigned_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lead_assignment_shares"
        unique_together = ("date", "shift", "sheet", "user")
        indexes = [
            models.Index(fields=["date", "shift", "sheet"], name="lead_assign_d_sh_sheet_idx")
        ]

    def __str__(self):
        return f"{self.date} {self.shift} {self.sheet} u={self.user_id} w={self.weight} n={self.assigned_count}"


class ShiftWindow(models.Model):
    """Khung giờ 3 ca làm việc (global, không theo ngày).

    Mỗi ca có thể "tắt" (không cấu hình giờ) — khi đó không lưu dòng tương ứng. Không
    bắt buộc phủ kín 24h: đơn vào ngoài mọi khung giờ sẽ không được chia tự động.
    """

    SHIFT_CHOICES = [
        ("morning", "Sáng"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]

    shift = models.CharField(max_length=16, choices=SHIFT_CHOICES, unique=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shift_windows"
        verbose_name = "Shift Window"

    def __str__(self):
        return f"{self.shift} {self.start_time}-{self.end_time}"


class FloatingPoolConfig(models.Model):
    """Cấu hình Kho số thả nổi — bảng 1 dòng (pk=1). Xem docs/sale-floating-pool.md §6."""

    enabled = models.BooleanField(default=False)
    stale_days = models.PositiveSmallIntegerField(default=3)
    saved_status_codes = ArrayField(models.CharField(max_length=64), blank=True, default=list)
    max_float_count = models.PositiveSmallIntegerField(default=10)
    daily_claim_limit = models.PositiveSmallIntegerField(default=10)
    max_order_age_days = models.PositiveSmallIntegerField(default=0)
    hide_phone = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_floating_pool_configs",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "floating_pool_config"

    @classmethod
    def get_solo(cls) -> "FloatingPoolConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class FloatingPoolEntry(models.Model):
    """Một vòng thả nổi của một đơn. Giữ vĩnh viễn làm lịch sử + đếm số vòng."""

    class Status(models.TextChoices):
        FLOATING = "floating", "Đang nổi"
        PICKED = "picked", "Đã nhặt"
        REMOVED = "removed", "Đã gỡ"

    class RemovedReason(models.TextChoices):
        SAVED = "saved", "Được chăm lại"
        CANCELLED = "cancelled", "Đơn hủy/xóa"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="floating_entries")
    previous_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="floating_pool_released",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.FLOATING)
    float_count = models.PositiveSmallIntegerField(default=1)
    entered_at = models.DateTimeField(default=timezone.now)
    picked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="floating_pool_picked",
    )
    picked_at = models.DateTimeField(null=True, blank=True)
    removed_reason = models.CharField(max_length=16, choices=RemovedReason.choices, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "floating_pool_entries"
        constraints = [
            models.UniqueConstraint(
                fields=["order"], condition=Q(status="floating"),
                name="floating_entry_one_active_per_order",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "entered_at"], name="floating_status_entered_idx"),
            models.Index(fields=["picked_by", "picked_at"], name="floating_picked_by_at_idx"),
        ]
