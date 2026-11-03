from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class GoogleAdsConfig(models.Model):
    """Một bộ credential Google Ads API (thường gắn với 1 tài khoản MCC).

    Mirror của FacebookApp. Các field nhạy cảm được mã hóa bằng EncryptedCharField
    (dùng chung ENCRYPTION_KEY / Fernet với phần còn lại của hệ thống).
    """

    name = models.CharField(max_length=255, verbose_name="Tên cấu hình")
    developer_token = EncryptedCharField(max_length=512, verbose_name="Developer Token")
    client_id = models.CharField(max_length=255, verbose_name="OAuth Client ID")
    client_secret = EncryptedCharField(max_length=512, verbose_name="OAuth Client Secret")
    refresh_token = EncryptedCharField(max_length=1024, verbose_name="Refresh Token")
    login_customer_id = models.CharField(
        max_length=20,
        unique=True,
        help_text="Customer ID của tài khoản MCC (10 chữ số, không dấu gạch)",
    )
    note = models.TextField(blank=True, default="", verbose_name="Ghi chú")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Config"
        verbose_name_plural = "Google Ads Configs"
        db_table = "google_ads_configs"
        indexes = [
            models.Index(fields=["login_customer_id"], name="gads_cfg_login_idx"),
            models.Index(fields=["deleted_at"], name="gads_cfg_deleted_idx"),
        ]

    def __str__(self):
        return self.name


class GoogleAdsAccount(models.Model):
    """Một customer (tài khoản con) thuộc MCC. Mirror của FacebookAdAccount."""

    STATUS_ACTIVE = "active"
    STATUS_REMOVED = "removed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REMOVED, "Removed"),
    ]

    config = models.ForeignKey(
        GoogleAdsConfig,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    name = models.CharField(max_length=255)
    customer_id = models.CharField(max_length=20, help_text="10 chữ số, không dấu gạch")
    currency = models.CharField(max_length=10, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Account"
        verbose_name_plural = "Google Ads Accounts"
        db_table = "google_ads_accounts"
        constraints = [
            models.UniqueConstraint(fields=["config", "customer_id"], name="gads_acc_cfg_cust_uniq"),
        ]
        indexes = [
            models.Index(fields=["config", "status"], name="gads_acc_cfg_status_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.customer_id})"


class GoogleAdsAccountDailySpend(models.Model):
    """Chi phí theo ngày của một customer Google Ads.

    Schema giữ tương thích với FacebookAdAccountDailySpend để tầng report gộp chung dễ dàng.
    """

    ad_account = models.ForeignKey(
        GoogleAdsAccount,
        on_delete=models.CASCADE,
        related_name="daily_spends",
    )
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Account Daily Spend"
        verbose_name_plural = "Google Ads Account Daily Spends"
        db_table = "google_ads_account_daily_spends"
        constraints = [
            models.UniqueConstraint(fields=["ad_account", "date"], name="gads_dailyspend_acc_date_uniq"),
        ]
        indexes = [
            models.Index(fields=["date"], name="gads_dailyspend_date_idx"),
        ]

    def __str__(self):
        return f"{self.ad_account_id} @ {self.date}: {self.spend}"


class GoogleAdsCampaign(models.Model):
    """Danh mục chiến dịch của một customer.

    Nạp từ cùng truy vấn GAQL với chi phí (`FROM campaign` trả cả hai) nên không tốn
    thêm quota. `status` lưu thô như Google trả (ENABLED/PAUSED/REMOVED) — không ánh xạ
    về active/removed như account vì chiến dịch tạm dừng vẫn là chiến dịch có ý nghĩa
    trên báo cáo lịch sử.
    """

    ad_account = models.ForeignKey(
        GoogleAdsAccount,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    campaign_id = models.CharField(max_length=32, help_text="ID chiến dịch bên Google")
    name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Gán sản phẩm ──────────────────────────────────────────────────────────
    # Cùng ngữ nghĩa với FacebookAdCampaign — xem docs/ads-campaign-product-mapping.md.
    # Google phủ tự động thấp hơn hẳn Facebook (~41% vs 95,5% chi phí) vì chiến dịch
    # Shopping/Pmax đặt tên theo NHÓM HÀNG và chạy cả feed; chúng CỐ Ý nằm ở "chưa gán"
    # thay vì bị ép về 1 SKU.
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
        verbose_name = "Google Ads Campaign"
        verbose_name_plural = "Google Ads Campaigns"
        db_table = "google_ads_campaigns"
        constraints = [
            models.UniqueConstraint(
                fields=["ad_account", "campaign_id"], name="gads_camp_acc_cid_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["ad_account", "status"], name="gads_camp_acc_status_idx"),
            models.Index(fields=["product"], name="gads_camp_product_idx"),
        ]

    def __str__(self):
        return f"{self.name or self.campaign_id}"


class GoogleAdsCampaignDailySpend(models.Model):
    """Chi phí theo ngày của một chiến dịch.

    KHÔNG có cột `reach`: Google Ads API không trả reach ở grain campaign cho mọi loại
    chiến dịch, một cột luôn bằng 0 trông y hệt "reach thật bằng 0".
    """

    campaign = models.ForeignKey(
        GoogleAdsCampaign,
        on_delete=models.CASCADE,
        related_name="daily_spends",
    )
    date = models.DateField()
    spend = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Ads Campaign Daily Spend"
        verbose_name_plural = "Google Ads Campaign Daily Spends"
        db_table = "google_ads_campaign_daily_spends"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "date"], name="gads_camp_spend_camp_date_uniq",
            ),
        ]
        indexes = [
            # Báo cáo luôn lọc khoảng ngày trước tiên.
            models.Index(fields=["date"], name="gads_camp_spend_date_idx"),
        ]

    def __str__(self):
        return f"{self.campaign_id} @ {self.date}: {self.spend}"
