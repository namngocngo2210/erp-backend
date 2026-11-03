from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class FacebookApp(models.Model):
    # ── Vòng đời token ────────────────────────────────────────────────────────
    # Sự cố 25–27/07/2026: token user hết hạn lúc nửa đêm, hệ thống không biết hạn
    # nên chỉ phát hiện khi Graph API từ chối — sau 2 ngày mất dữ liệu chi tiêu.
    # Xem docs/facebook-token-lifecycle-2026-07-27.md.
    TOKEN_UNKNOWN = "unknown"
    TOKEN_OK = "ok"
    TOKEN_EXPIRING = "expiring"
    TOKEN_INVALID = "invalid"
    TOKEN_STATUS_CHOICES = [
        (TOKEN_UNKNOWN, "Chưa kiểm tra"),
        (TOKEN_OK, "Hợp lệ"),
        (TOKEN_EXPIRING, "Sắp hết hạn"),
        (TOKEN_INVALID, "Hỏng / hết hạn"),
    ]

    name = models.CharField(max_length=255, verbose_name="App Name")
    app_id = models.CharField(max_length=100, unique=True, verbose_name="App ID")
    app_secret = EncryptedCharField(max_length=512, verbose_name="App Secret")
    access_token = EncryptedCharField(max_length=1024, verbose_name="Access Token")
    note = models.TextField(blank=True, default="", verbose_name="Note")

    token_status = models.CharField(
        max_length=16,
        choices=TOKEN_STATUS_CHOICES,
        default=TOKEN_UNKNOWN,
        verbose_name="Trạng thái token",
    )
    # null = không hết hạn (System User token của Business Manager) — đây là dạng
    # token duy nhất nên dùng cho máy-gọi-máy.
    token_expires_at = models.DateTimeField(null=True, blank=True)
    # FB cắt quyền đọc insights 90 ngày sau lần user cuối tương tác với app, kể cả
    # khi access_token vẫn hợp lệ. Hai mốc này độc lập nhau.
    data_access_expires_at = models.DateTimeField(null=True, blank=True)
    token_checked_at = models.DateTimeField(null=True, blank=True)
    # Mốc bắt đầu hỏng — giữ nguyên qua các lần kiểm tra để biết sự cố kéo dài bao lâu.
    token_invalid_since = models.DateTimeField(null=True, blank=True)
    token_error = models.TextField(blank=True, default="")

    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facebook App"
        verbose_name_plural = "Facebook Apps"
        db_table = "facebook_apps"
        indexes = [
            models.Index(fields=["deleted_at"], name="fb_app_deleted_idx"),
            models.Index(fields=["token_status"], name="fb_app_token_status_idx"),
        ]

    def __str__(self):
        return self.name

    @property
    def token_never_expires(self) -> bool:
        """Đã soi qua ``debug_token`` và Graph API trả ``expires_at = 0``.

        Phải kèm điều kiện "đã kiểm tra": app vừa tạo cũng có ``token_expires_at``
        rỗng, nhưng đó là *chưa biết* chứ không phải *không hết hạn*.
        """
        return self.token_checked_at is not None and self.token_expires_at is None


class FacebookAdAccount(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_REMOVED = "removed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REMOVED, "Removed"),
    ]

    app = models.ForeignKey(
        FacebookApp,
        on_delete=models.CASCADE,
        related_name="ad_accounts",
    )
    name = models.CharField(max_length=255)
    account_id = models.CharField(max_length=64)
    fb_id = models.CharField(max_length=80, help_text="act_<account_id>")
    spend_cap = models.CharField(max_length=64, default="0")
    currency = models.CharField(max_length=10, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facebook Ad Account"
        verbose_name_plural = "Facebook Ad Accounts"
        db_table = "facebook_ad_accounts"
        constraints = [
            models.UniqueConstraint(fields=["app", "account_id"], name="fb_adacc_app_account_uniq"),
        ]
        indexes = [
            models.Index(fields=["app", "status"], name="fb_adacc_app_status_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.account_id})"


class FacebookAdAccountDailySpend(models.Model):
    ad_account = models.ForeignKey(
        FacebookAdAccount,
        on_delete=models.CASCADE,
        related_name="daily_spends",
    )
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    reach = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facebook Ad Account Daily Spend"
        verbose_name_plural = "Facebook Ad Account Daily Spends"
        db_table = "facebook_ad_account_daily_spends"
        constraints = [
            models.UniqueConstraint(fields=["ad_account", "date"], name="fb_dailyspend_acc_date_uniq"),
        ]
        indexes = [
            models.Index(fields=["date"], name="fb_dailyspend_date_idx"),
        ]

    def __str__(self):
        return f"{self.ad_account_id} @ {self.date}: {self.spend}"


class FacebookPage(models.Model):
    """Danh mục fanpage. Không gắn FK app: một page có thể được nhiều BM chạy quảng cáo."""

    fb_page_id = models.CharField(max_length=64, unique=True, verbose_name="Page ID")
    name = models.CharField(max_length=255, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Facebook Page"
        verbose_name_plural = "Facebook Pages"
        db_table = "facebook_pages"

    def __str__(self):
        return self.name or f"Page {self.fb_page_id}"


class FacebookAdCampaign(models.Model):
    """Danh mục chiến dịch Facebook — đối xứng với ``GoogleAdsCampaign``.

    Trước đây Facebook không có bảng này: ``campaign_id``/``campaign_name`` là hai cột
    phi chuẩn hoá nằm trên TỪNG dòng ``FacebookAd``, nên không có chỗ nào treo được
    thuộc tính của chiến dịch (sản phẩm nó chạy, lịch sử tên...).

    ``fb_campaign_id`` UNIQUE TOÀN CỤC, KHÔNG phải unique theo (TKQC, campaign_id):
    khảo sát prod 2026-08-24 thấy 21 campaign_id nằm trên nhiều ``ad_account_id`` —
    tra sâu thì cả 21 đều là BẢN SAO TKQC (cùng ``FacebookAdAccount.account_id`` thật,
    khác PK), không có chiến dịch nào thật sự chạy trên 2 tài khoản. Đây đúng lớp lỗi
    MR !1199. Khoá unique đặt ở đây khiến mọi bản sao TKQC quy về đúng một dòng chiến
    dịch — bẫy bản sao tự chết ở tầng này.
    """

    fb_campaign_id = models.CharField(max_length=64, unique=True, verbose_name="Campaign ID")
    # Nullable: ad đã xoá trên FB vẫn còn chi tiêu lịch sử trong insights nhưng không
    # còn tra được TKQC — cùng lý do ``FacebookAd.page`` nullable.
    ad_account = models.ForeignKey(
        FacebookAdAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # ── Gán sản phẩm ──────────────────────────────────────────────────────────
    # 1 chiến dịch chỉ chạy 1 sản phẩm (quyết định nghiệp vụ D1, xem
    # docs/ads-campaign-product-mapping.md). NULL = chưa gán: chi phí của nó rơi vào
    # rổ "Chưa gán chiến dịch" trên báo cáo digital chứ KHÔNG bị giấu đi.
    SOURCE_AUTO = "auto"
    # Máy phải ĐOÁN: mã gốc có nhiều biến thể nên chọn theo doanh thu, hoặc tên nhắc
    # nhiều sản phẩm nên lấy cái đầu, hoặc gán theo bí danh học được. Tách riêng khỏi
    # `auto` để người soát chỉ phải xem lại đúng phần đáng ngờ.
    SOURCE_AUTO_LOOSE = "auto2"
    SOURCE_MANUAL = "manual"
    PRODUCT_SOURCE_CHOICES = [
        (SOURCE_AUTO, "Máy đọc từ tên chiến dịch"),
        (SOURCE_AUTO_LOOSE, "Máy đoán — cần soát"),
        (SOURCE_MANUAL, "Người gán"),
    ]

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )
    product_source = models.CharField(
        max_length=8, blank=True, default="", choices=PRODUCT_SOURCE_CHOICES,
    )
    product_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    product_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Facebook Ad Campaign"
        verbose_name_plural = "Facebook Ad Campaigns"
        db_table = "facebook_ad_campaigns"
        # fb_camp_acc_idx / fb_camp_product_idx đã bỏ (audit 2026-09-23 T2): trùng
        # tuyệt đối với index FK tự sinh của ad_account / product.

    def __str__(self):
        return f"{self.name or self.fb_campaign_id}"


class FacebookAd(models.Model):
    """Danh mục quảng cáo — cầu nối giữa chi tiêu (grain ad) và page.

    ``page`` nullable vì ad đã xoá trên FB vẫn còn chi tiêu lịch sử trong insights
    nhưng không còn tra được creative để suy ra page (xem spec §5.4).
    """

    fb_ad_id = models.CharField(max_length=64, unique=True, verbose_name="Ad ID")
    ad_account = models.ForeignKey(
        FacebookAdAccount, on_delete=models.CASCADE, related_name="ads",
    )
    page = models.ForeignKey(
        FacebookPage, on_delete=models.SET_NULL, null=True, blank=True, related_name="ads",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    # Thay 2 cột chuỗi `campaign_id`/`campaign_name` cũ. KHÔNG giữ song song hai nguồn:
    # sync chỉ ghi một chỗ nên bản còn lại sẽ trôi lệch ngay, và Django cũng cấm —
    # attname của FK này đúng bằng `campaign_id` (models.E006).
    campaign = models.ForeignKey(
        FacebookAdCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ads",
    )
    adset_id = models.CharField(max_length=64, blank=True, default="")
    adset_name = models.CharField(max_length=255, blank=True, default="")
    # Lần cuối ad còn xuất hiện trong /ads. Ad ngừng chạy vẫn giữ lại để chi tiêu
    # lịch sử không mất chỗ bám.
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Facebook Ad"
        verbose_name_plural = "Facebook Ads"
        db_table = "facebook_ads"
        indexes = [
            models.Index(fields=["ad_account", "page"], name="fb_ad_acc_page_idx"),
        ]

    def __str__(self):
        return f"{self.name or self.fb_ad_id}"


class FacebookAdDailySpend(models.Model):
    ad = models.ForeignKey(FacebookAd, on_delete=models.CASCADE, related_name="daily_spends")
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    # Lưu nhưng KHÔNG cộng dồn khi hiển thị — reach không cộng được giữa các ngày
    # (spec §9).
    reach = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facebook Ad Daily Spend"
        verbose_name_plural = "Facebook Ad Daily Spends"
        db_table = "facebook_ad_daily_spends"
        constraints = [
            models.UniqueConstraint(fields=["ad", "date"], name="fb_ad_spend_ad_date_uniq"),
        ]
        indexes = [
            models.Index(fields=["date"], name="fb_ad_spend_date_idx"),
        ]

    def __str__(self):
        return f"{self.ad_id} @ {self.date}: {self.spend}"
