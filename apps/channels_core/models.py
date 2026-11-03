from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models


class ChannelType(models.Model):
    """Loại kênh bán theo nền tảng (Shopee / TikTok / Facebook / Web).

    Bảng tra cứu nhỏ và ĐÓNG: 4 dòng được seed sẵn trong migration 0024. Cố ý
    dựng thành bảng thay vì ``TextChoices`` để mở thêm nền tảng (Lazada, Sendo...)
    chỉ cần thêm 1 dòng dữ liệu, không phải deploy code.

    ``code`` trùng đúng giá trị của ``SALE_CHANNEL_*`` trong
    ``apps/orders/reporting/shared.py`` (shopee/tiktok/facebook/web) để sau này
    Báo cáo tổng có thể chuyển sang đọc thẳng loại kênh thay vì đoán theo prefix
    mã kênh — xem docs/channel-type.md. Ở MR này BÁO CÁO CHƯA ĐỔI.
    """

    SHOPEE = "shopee"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    WEB = "web"

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Mã loại kênh, ổn định giữa các môi trường (shopee/tiktok/facebook/web).",
    )
    name = models.CharField(max_length=100, help_text="Tên hiển thị trên giao diện.")
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Thứ tự hiển thị trong dropdown; nhỏ hơn đứng trước."
    )
    is_active = models.BooleanField(
        default=True, help_text="Tắt để ẩn khỏi dropdown mà không xoá dữ liệu kênh đã gán."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_types"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.name


class Channel(models.Model):
    KOC_CODE = "koc"

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, blank=True, default="")
    landingpage_source = ArrayField(models.TextField(), blank=True, default=list)
    channel_id = models.CharField(max_length=255, blank=True, null=True, default=None, help_text="ID kênh từ bên ngoài")
    old_channel_id = models.CharField(max_length=255, blank=True, null=True, default=None, help_text="ID kênh cũ từ bên ngoài")
    departments = models.ManyToManyField(
        "users.Department",
        blank=True,
        related_name="channels",
        help_text="Các phòng ban được dùng kênh này (lọc kênh theo phòng ban ở form tạo đơn). Rỗng = dùng chung.",
    )
    channel_type = models.ForeignKey(
        ChannelType,
        on_delete=models.PROTECT,
        related_name="channels",
        null=True,
        blank=True,
        help_text="Loại kênh bán (Shopee/TikTok/Facebook/Web). Rỗng = chưa phân loại.",
    )
    price_book = models.ForeignKey(
        "catalog.PriceBook",
        on_delete=models.SET_NULL,
        related_name="channels",
        null=True,
        blank=True,
        help_text="Bảng giá áp dụng cho đơn của kênh này. Rỗng = dùng bảng giá mặc định.",
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channels"
        indexes = [
            models.Index(fields=["deleted_at"], name="channel_deleted_idx"),
        ]
        constraints = [
            # Mã kênh duy nhất trong tập kênh đang active (audit 2026-07 mục 3.6);
            # kênh đã soft-delete được phép trùng mã để tái sử dụng code cũ.
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="channel_code_active_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    @property
    def is_koc(self) -> bool:
        """Kênh KOC: nhận diện theo id cấu hình hoặc mã kênh "koc".

        Mã kênh ổn định giữa các môi trường (id channel sinh tự động nên có thể
        khác KOC_CHANNEL_ID), nên dùng làm tín hiệu chính, giữ id để tương thích.
        """
        return self.id == settings.KOC_CHANNEL_ID or str(self.code or "").strip().lower() == self.KOC_CODE

    @property
    def fb_ads_accounts(self) -> list[int]:
        """ID các FacebookAdAccount map vào kênh (giữ nguyên contract API của ArrayField cũ)."""
        return self._ad_account_ids(ChannelAdAccount.Provider.FACEBOOK)

    @property
    def google_ads_accounts(self) -> list[int]:
        """ID các GoogleAdsAccount map vào kênh (giữ nguyên contract API của ArrayField cũ)."""
        return self._ad_account_ids(ChannelAdAccount.Provider.GOOGLE)

    def _ad_account_ids(self, provider: str) -> list[int]:
        # .all() tận dụng prefetch cache khi serialize danh sách; lọc provider bằng
        # Python thay vì .filter() để không nổ thêm query trên instance đã prefetch.
        return [
            link.account_id
            for link in self.ad_account_links.all()
            if link.provider == provider
        ]


class ChannelAdAccount(models.Model):
    """Bảng nối kênh ↔ tài khoản quảng cáo (thay 2 ArrayField fb/google_ads_accounts).

    Không FK cứng sang FacebookAdAccount/GoogleAdsAccount vì channels_core nằm ở
    tầng DƯỚI facebook_ads/google_ads (contract layering trong backend/.importlinter);
    integrity được đảm bảo bằng signal post_delete phía app ads (tầng trên được phép
    import channels_core) — xoá ad account thì xoá luôn row mapping, hết id mồ côi
    (fix gốc bug "ads phồng", audit 2026-07 mục DB #6).
    """

    class Provider(models.TextChoices):
        FACEBOOK = "fb", "Facebook Ads"
        GOOGLE = "google", "Google Ads"

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="ad_account_links")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    account_id = models.BigIntegerField(
        help_text="PK của FacebookAdAccount (provider=fb) hoặc GoogleAdsAccount (provider=google)."
    )
    position = models.PositiveIntegerField(
        default=0, help_text="Giữ thứ tự list id đúng như client gửi lên (contract ArrayField cũ)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_ad_accounts"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "provider", "account_id"], name="chan_adacc_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["provider", "account_id"], name="chan_adacc_prov_acc_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.channel_id} ↔ {self.provider}:{self.account_id}"


class UserChannel(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="channel_links")
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="user_links")
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_channels"
        unique_together = ("user", "channel")
        indexes = [
            models.Index(fields=["user", "is_active"], name="userchannel_user_active_idx"),
            models.Index(fields=["channel", "is_active"], name="userchannel_channel_active_idx"),
        ]


class ChannelApiToken(models.Model):
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="api_tokens")
    token_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_api_tokens"
        indexes = [
            models.Index(fields=["channel", "is_active"], name="channel_token_active_idx"),
            models.Index(fields=["token_hash"], name="channel_token_hash_idx"),
        ]


class ChannelKiotvietMapping(models.Model):
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="kiotviet_mappings")
    kiotviet_channel = models.ForeignKey(
        "kiotviet.KiotVietChannel",
        on_delete=models.CASCADE,
        related_name="channel_mappings",
    )
    external_sale_channel_id = models.CharField(
        max_length=255,
        help_text="ID saleChannel bên KiotViet tương ứng với cặp (Channel, KiotVietChannel) này",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_kiotviet_mappings"
        unique_together = ("channel", "kiotviet_channel")
        indexes = [
            models.Index(
                fields=["kiotviet_channel", "external_sale_channel_id"],
                name="ckvm_kv_extid_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel_id} ↔ {self.kiotviet_channel_id} = {self.external_sale_channel_id}"
