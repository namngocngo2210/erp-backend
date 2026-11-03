from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

# DEPRECATED re-export: EncryptedCharField đã chuyển về apps/common/fields.py
# (audit 2026-08-19 mục 1.6 — tiện ích hạ tầng thuần, không thuộc kiotviet).
# Giữ alias để code/migration cũ trỏ `apps.kiotviet.models.EncryptedCharField`
# không gãy ngay; sẽ gỡ khi 5 app (best_express, cnv_loyalty, facebook_ads,
# foxia, google_ads) đã đổi hết sang import từ apps.common.
from apps.common.encryption import get_encryption  # noqa: F401
from apps.common.fields import _DECRYPT_ERRORS, EncryptedCharField  # noqa: F401


class KiotVietChannel(models.Model):
    """
    KiotViet Channel model to store KiotViet channel information.
    """

    name = models.CharField(null=True, max_length=255, verbose_name="Channel Name")
    branch_id = models.CharField(null=True, max_length=100, verbose_name="Branch ID")
    old_sync_branch_id = models.CharField(
        null=True,
        blank=True,
        max_length=100,
        verbose_name="Old Sync Branch ID",
        help_text=(
            "Branch ID dùng khi đồng bộ từ kênh mới sang kênh Kiot cũ đã ghép. "
            "Fallback khi kho nguồn không có trong old_sync_branch_map."
        ),
    )
    old_sync_branch_map = models.JSONField(
        blank=True,
        default=list,
        verbose_name="Old Sync Branch Map",
        help_text=(
            "Danh sách cặp kho đồng bộ new→old: "
            '[{"source_branch_id": "...", "target_branch_id": "..."}]. '
            "source_branch_id thuộc branch_ids của kênh này, target_branch_id thuộc kênh Kiot cũ đã ghép."
        ),
    )
    branch_ids = ArrayField(models.TextField(), blank=True, default=list, verbose_name="Branch IDs")
    branch_names = ArrayField(
        models.TextField(),
        blank=True,
        default=list,
        verbose_name="Branch Names",
        help_text="Tên chi nhánh, khớp thứ tự với branch_ids (branch_names[i] là tên của branch_ids[i])",
    )
    client_id = models.CharField(null=True, max_length=255, verbose_name="Client ID")
    client_secret = EncryptedCharField(null=True, max_length=255, verbose_name="Client Secret")
    retailer = models.CharField(max_length=255, verbose_name="Retailer")
    username = models.CharField(null=True, blank=True, max_length=255, verbose_name="Username")
    password = EncryptedCharField(null=True, blank=True, max_length=255, verbose_name="Password")
    old = models.BooleanField(default=False, verbose_name="Old Channel")
    # Mã thuế (TaxId) trên KiotViet gắn cho từng dòng hàng khi đẩy/sync sang kênh này.
    # NULL = kênh không kê khai VAT (vd hộ kinh doanh) → không gửi *DetailTaxs, pricingMode=0.
    # Tách khỏi cờ `old`: trước 18/09/2026 mọi kênh old=False đều bị gửi TaxId 3, kể cả HKD.
    vat_tax_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="VAT Tax ID",
        help_text="Mã thuế KiotViet gắn cho dòng hàng khi đẩy đơn (vd 3 = VAT 8%). Để trống = kênh không áp VAT.",
    )
    paired_old_channel = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paired_new_channels",
        verbose_name="Paired Old Channel",
    )
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Deleted At")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "KiotViet Channel"
        verbose_name_plural = "KiotViet Channels"
        db_table = "kiotviet_channels"
        indexes = [
            models.Index(fields=["branch_id"], name="kiotviet_branch_idx"),
            models.Index(fields=["retailer"], name="kiotviet_retailer_idx"),
            models.Index(fields=["paired_old_channel"], name="kiotviet_paired_old_idx"),
            models.Index(fields=["deleted_at"], name="kiotviet_deleted_idx"),
        ]

    def __str__(self):
        return self.name


def channel_vat_tax_id(channel: "KiotVietChannel | None") -> int | None:
    """TaxId gắn cho dòng hàng khi đẩy/sync sang `channel`; None = kênh không áp VAT.

    Chấp nhận channel None (builder chạy dry-run/không có kênh đích) → không thuế.
    """
    if channel is None:
        return None
    value = getattr(channel, "vat_tax_id", None)
    return int(value) if value else None


class KiotVietAccessToken(models.Model):
    """
    KiotViet Access Token model to store OAuth access tokens.
    Tokens are cached and automatically refreshed when expired.
    """

    channel = models.ForeignKey(
        KiotVietChannel,
        on_delete=models.CASCADE,
        related_name="access_tokens",
        verbose_name="KiotViet Channel",
    )
    access_token = models.TextField(verbose_name="Access Token")
    refresh_token = models.TextField(null=True, blank=True, verbose_name="Refresh Token")
    expires_in = models.IntegerField(default=86400, verbose_name="Expires In (seconds)")
    token_type = models.CharField(max_length=50, default="Bearer", verbose_name="Token Type")
    scope = models.TextField(null=True, blank=True, verbose_name="Scope")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "KiotViet Access Token"
        verbose_name_plural = "KiotViet Access Tokens"
        db_table = "kiotviet_access_tokens"
        indexes = [
            models.Index(fields=["channel", "created_at"], name="kiotviet_token_channel_idx"),
        ]

    def __str__(self):
        return f"Token for {self.channel.name}"

    def is_expired(self, buffer_seconds=60):
        """Check if the token is expired or about to expire."""
        expires_at = self.created_at + timezone.timedelta(seconds=self.expires_in)
        return timezone.now() >= (expires_at - timezone.timedelta(seconds=buffer_seconds))


class KiotVietDailyRevenue(models.Model):
    """
    Daily revenue snapshot per KiotViet channel and branch.
    Sourced from the Web reporting API (Sale.BigSaleProfitByInvoice report).
    """

    channel = models.ForeignKey(
        KiotVietChannel,
        on_delete=models.CASCADE,
        related_name="daily_revenues",
        verbose_name="KiotViet Channel",
    )
    branch_id = models.CharField(max_length=100, verbose_name="Branch ID")
    date = models.DateField(verbose_name="Report Date")
    total_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0, verbose_name="Tổng tiền hàng")
    discount = models.DecimalField(max_digits=20, decimal_places=2, default=0, verbose_name="Giảm giá")
    revenue = models.DecimalField(max_digits=20, decimal_places=2, default=0, verbose_name="Doanh thu")
    total_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0, verbose_name="Tổng giá vốn")
    gross_profit = models.DecimalField(max_digits=20, decimal_places=2, default=0, verbose_name="Lợi nhuận gộp")
    last_synced_at = models.DateTimeField(auto_now=True, verbose_name="Last Synced At")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "KiotViet Daily Revenue"
        verbose_name_plural = "KiotViet Daily Revenues"
        db_table = "kiotviet_daily_revenues"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "branch_id", "date"],
                name="kv_daily_rev_chan_branch_date_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["date"], name="kv_daily_rev_date_idx"),
            models.Index(fields=["channel", "date"], name="kv_daily_rev_chan_date_idx"),
            models.Index(fields=["branch_id", "date"], name="kv_daily_rev_branch_date_idx"),
        ]

    def __str__(self):
        return f"{self.channel_id}/{self.branch_id} @ {self.date}: {self.revenue}"


class KiotVietRevenueLarkRecord(models.Model):
    """
    Maps an aggregated daily revenue row (summed across every KiotViet channel
    + branch) to its Lark Bitable record ID. Lets us upsert (PUT vs POST) when
    re-pushing the same date.
    """

    date = models.DateField(unique=True, verbose_name="Report Date")
    lark_record_id = models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")
    last_synced_at = models.DateTimeField(auto_now=True, verbose_name="Last Synced At")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "KiotViet Revenue Lark Record"
        verbose_name_plural = "KiotViet Revenue Lark Records"
        db_table = "kiotviet_revenue_lark_records"

    def __str__(self):
        return f"{self.date} -> {self.lark_record_id}"
