"""Product Catalog — 9 model move từ apps/orders/models.py (state-only, không đổi
schema DB). Xem docs/catalog-app-split.md. `db_table` của mọi model GIỮ NGUYÊN
tên bảng từ thời còn ở orders — đổi tên bảng là việc của một đợt khác (nếu có).
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from apps.catalog.contracts.catalog_events import ProductCategorySaved, ProductGroupSaved, ProductSaved
from apps.common.events import event_bus


class PancakeSyncMode(models.TextChoices):
    """Ghi đè luật chọn SKU đẩy lên Pancake POS (docs/pancake-pos-product-sync.md §3.1)."""

    AUTO = "auto", "Theo luật"
    ALWAYS = "always", "Luôn đồng bộ"
    NEVER = "never", "Không đồng bộ"


class Product(models.Model):
    # Tên hoá đơn/khai báo — import hoá đơn KiotViet GHI ĐÈ trường này.
    name = models.CharField(max_length=255)
    # Tên ngắn bán hàng (Pancake POS...) do người dùng quản lý; import KiotViet KHÔNG
    # đụng tới (docs/product-short-name.md). Rỗng = dùng `name`.
    short_name = models.CharField(max_length=120, blank=True, default="", db_default="")
    # KHÔNG unique=True tuyệt đối: model có soft-delete (deleted_at) nên unique
    # phải là partial trên bản ghi còn sống — xem constraints (audit 2026-09-23 C8).
    sku = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    weight = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    length = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    width = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    height = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    categories = models.ManyToManyField("ProductCategory", related_name="products", blank=True)
    group = models.ForeignKey(
        "ProductGroup",
        on_delete=models.SET_NULL,
        related_name="products",
        null=True,
        blank=True,
    )
    # db_default: ETL/INSERT thô vào `products` (không qua ORM) vẫn có giá trị hợp lệ;
    # PG ≥ 11 thêm cột DEFAULT hằng không rewrite bảng.
    pancake_sync_mode = models.CharField(
        max_length=10,
        choices=PancakeSyncMode.choices,
        default=PancakeSyncMode.AUTO,
        db_default=PancakeSyncMode.AUTO,
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        indexes = [
            models.Index(fields=["is_active"], name="product_is_active_idx"),
            models.Index(fields=["deleted_at"], name="product_deleted_idx"),
            models.Index(fields=["created_at"], name="product_created_at_idx"),
            # group: index FK tự sinh (products_group_id_*) đã phủ — bỏ product_group_idx trùng (audit 2026-09-23).
            GinIndex(fields=["name"], name="product_name_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["sku"], name="product_sku_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            # Một SKU chỉ thuộc về một sản phẩm ĐANG SỐNG; xoá mềm rồi tạo lại cùng
            # SKU phải được (audit 2026-09-23 C8 — trước đây unique tuyệt đối làm
            # IntegrityError). Mẫu: PriceBook.pricebook_code_active_uniq.
            models.UniqueConstraint(
                fields=["sku"],
                condition=models.Q(deleted_at__isnull=True),
                name="product_sku_active_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not update_fields:
            return  # save(update_fields=[]) là no-op của Django
        # Domain event thay cho post_save mà cnv_loyalty từng móc thẳng vào model
        # (audit 2026-09-23 T3/P2-4).
        event_bus.publish_on_commit(
            ProductSaved(
                product_id=self.pk, created=created, is_deleted=self.deleted_at is not None
            )
        )


class ProductGroup(models.Model):
    # Partial unique trên bản ghi chưa xoá mềm — xem constraints.
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    image_url = models.URLField(max_length=1024, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_groups"
        indexes = [
            models.Index(fields=["is_active"], name="prodgroup_active_idx"),
            models.Index(fields=["deleted_at"], name="prodgroup_deleted_idx"),
            GinIndex(fields=["code"], name="prodgroup_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["name"], name="prodgroup_name_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="prodgroup_code_active_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not update_fields:
            return  # save(update_fields=[]) là no-op của Django
        # Xem Product.save().
        event_bus.publish_on_commit(
            ProductGroupSaved(group_id=self.pk, created=created, is_deleted=self.deleted_at is not None)
        )


class ProductCategory(models.Model):
    # Partial unique trên bản ghi chưa xoá mềm — xem constraints.
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    # Danh mục mặc định cho bộ lọc trang Khách hàng thân thiết. Tối đa một danh mục
    # (chưa xóa) được đánh dấu — bảo đảm bằng partial unique constraint bên dưới.
    is_loyal_default = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_categories"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["is_active"], name="prodcat_is_active_idx"),
            models.Index(fields=["deleted_at"], name="prodcat_deleted_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_loyal_default"],
                condition=models.Q(is_loyal_default=True, deleted_at__isnull=True),
                name="prodcat_one_loyal_default",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="prodcat_code_active_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not update_fields:
            return  # save(update_fields=[]) là no-op của Django
        # Xem Product.save().
        event_bus.publish_on_commit(
            ProductCategorySaved(
                category_id=self.pk, created=created, is_deleted=self.deleted_at is not None
            )
        )


class ProductPackage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="packages")
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    weight = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    length = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    width = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    height = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_packages"
        constraints = [
            models.UniqueConstraint(fields=["product", "quantity"], name="uniq_product_package_qty"),
        ]
        # product_package_pq_idx đã bỏ: trùng tuyệt đối với unique ở trên (audit 2026-09-23).
        ordering = ["quantity"]


class ProductComponent(models.Model):
    """Thành phần của một SKU combo — CHỈ phục vụ báo cáo, KHÔNG tham gia ghi sổ tồn kho.

    Tồn kho vẫn trừ theo chính SKU combo như trước; bảng này chỉ để file Excel
    Lịch sử nhập xuất nở combo ra các sản phẩm con. Cố tình KHÔNG có cờ
    ``is_combo`` trên ``Product``: có ≥1 dòng ở đây nghĩa là combo, một nguồn sự
    thật duy nhất.

    Chỉ MỘT cấp — sản phẩm con không được tự nó là combo (chặn ở
    ``product_component_service``, hai chiều).
    """

    # db_index=False (audit 2026-07 mục 3.3): UniqueConstraint(parent, component)
    # bên dưới đã tự sinh index bắt đầu bằng parent — index FK mặc định trùng lặp.
    parent = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="components", db_index=False
    )
    # PROTECT: xoá cứng một SP đang là thành phần combo sẽ làm file Excel cũ mất
    # dòng mà không ai biết.
    # db_index=False (audit 2026-07 mục 3.3): Meta.indexes đã có prodcomp_component_idx
    # cùng cột — index FK mặc định tạo bản trùng lặp.
    component = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="in_combos", db_index=False
    )
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_components"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "component"], name="uniq_product_component"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="product_component_qty_positive"
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("component")), name="product_component_not_self"
            ),
        ]
        indexes = [
            # `parent` đã có index nhờ UniqueConstraint(parent, component) — chỉ
            # cần thêm chiều ngược "SKU này nằm trong những combo nào".
            models.Index(fields=["component"], name="prodcomp_component_idx"),
        ]


class PriceBook(models.Model):
    """Bảng giá theo nền tảng bán (facebook, web, ...).

    Đúng MỘT bảng giá được đánh ``is_default`` trong tập chưa xoá — bảo đảm bằng
    partial unique constraint bên dưới (cùng pattern ``ProductCategory.is_loyal_default``).
    Bảng mặc định là đích fallback khi sản phẩm chưa khai giá ở nền tảng đang chọn.
    """

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "price_books"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="pricebook_code_active_uniq",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True, deleted_at__isnull=True),
                name="pricebook_one_default",
            ),
        ]
        indexes = [
            models.Index(fields=["deleted_at"], name="pricebook_deleted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class ProductPackagePrice(models.Model):
    """Giá của một gói ở một bảng giá.

    Vắng dòng = nền tảng đó chưa khai giá → resolve fallback về bảng mặc định
    (xem ``price_book_service.package_prices``). Cân nặng/kích thước KHÔNG nằm ở
    đây: chúng dùng chung mọi nền tảng và vẫn thuộc ``ProductPackage``.
    """

    package = models.ForeignKey(ProductPackage, on_delete=models.CASCADE, related_name="book_prices")
    price_book = models.ForeignKey(PriceBook, on_delete=models.CASCADE, related_name="package_prices")
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_package_prices"
        constraints = [
            models.UniqueConstraint(fields=["package", "price_book"], name="uniq_package_price_book"),
        ]
        indexes = [
            models.Index(fields=["price_book", "package"], name="pkgprice_book_pkg_idx"),
        ]



PROFILE_SECTIONS = (
    "diem_noi_bat", "thong_so", "doi_tuong", "huong_dan", "su_co", "faq", "video",
)


class ProductProfile(models.Model):
    """Nội dung trang sản phẩm public (`/sp/<mã>`) — mô tả, thông số, hướng dẫn.

    Toàn bộ trường text lưu PLAIN TEXT giữ ký tự xuống dòng, KHÔNG lưu HTML:
    trang tiêu thụ nó là trang public không đăng nhập, giữ plain text là cách rẻ
    nhất để loại hẳn một lớp rủi ro XSS. Xem docs/product-scan-page-design.md §3.1.

    Cố ý KHÔNG có trường giá nào — chủ dự án chốt bỏ giá khỏi phạm vi, nên rủi ro
    rò giá nội bộ bị chặn từ tầng schema chứ không phải tầng serializer.
    """

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name="profile"
    )
    # Tên hiển thị trên trang public. Rỗng = dùng `product.name`.
    #
    # KHÔNG sửa thẳng `Product.name` cho việc này: tên đó do KiotViet làm chủ,
    # `_import_service_upsert_mixin` ghi đè lại mỗi lần tên lệch, nên sửa ở đó
    # là mất sau lần sync kế tiếp — và còn đổi luôn tên trong đơn hàng, báo cáo.
    # Tên KiotViet thường là cả chuỗi mô tả kỹ thuật ("... vỏ nhựa TPE+ABS,
    # Công suất:800W/220V, KT:35*16.5*45.5cm ... Mới 100%"), đọc không nổi khi
    # in thành `<h1>` của trang khách quét QR.
    display_name = models.CharField(max_length=255, blank=True, default="")
    short_description = models.CharField(max_length=500, blank=True, default="")
    highlights = models.JSONField(default=list, blank=True)       # [{title, detail}]
    specs = models.JSONField(default=list, blank=True)            # [{label, value}]
    audience = models.JSONField(default=dict, blank=True)         # {suitable[], caution[]}
    usage_steps = models.JSONField(default=list, blank=True)      # [{title, detail}]
    troubleshooting = models.JSONField(default=list, blank=True)  # [{symptom, cause, fix}]
    faqs = models.JSONField(default=list, blank=True)             # [{question, answer, visible}]
    videos = models.JSONField(default=list, blank=True)           # [{label, url}]
    visible_sections = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_profiles"
        indexes = [
            models.Index(fields=["is_published"], name="product_profile_pub_idx"),
        ]


class ProductProfileImage(models.Model):
    profile = models.ForeignKey(
        ProductProfile, on_delete=models.CASCADE, related_name="images"
    )
    object_key = models.CharField(max_length=255)
    content_type = models.CharField(max_length=64)
    alt_text = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "product_profile_images"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["profile", "sort_order"], name="product_profile_img_idx"),
        ]
