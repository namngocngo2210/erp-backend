"""Signal của facebook_ads: dọn mapping kênh, và reset trạng thái token khi đổi token.

ChannelAdAccount không FK cứng sang bảng ad account (channels_core ở tầng dưới —
xem backend/.importlinter), nên integrity đảm bảo tại đây: app tầng trên được
phép import channels_core. Fix gốc bug "ads phồng" do id mồ côi trong mapping.
"""
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from apps.channels_core.models import ChannelAdAccount

from .models import FacebookAdAccount, FacebookApp


@receiver(
    post_delete,
    sender=FacebookAdAccount,
    dispatch_uid="facebook_ads.cleanup_channel_ad_account_links",
)
def cleanup_channel_ad_account_links(sender, instance, **kwargs):
    ChannelAdAccount.objects.filter(
        provider=ChannelAdAccount.Provider.FACEBOOK, account_id=instance.pk
    ).delete()


@receiver(
    pre_save,
    sender=FacebookApp,
    dispatch_uid="facebook_ads.reset_token_state_on_token_change",
)
def reset_token_state_on_token_change(sender, instance, update_fields=None, raw=False, **kwargs):
    """Token đổi ⇒ mọi hiểu biết cũ về nó (hạn, trạng thái, lỗi) đều vô giá trị.

    Đặt ở signal thay vì trong view/admin để không call site nào quên: người vận
    hành dán token mới qua ``PATCH /facebook-ads/apps/{id}`` hay Django admin đều
    thoát ngắt mạch ngay ở lượt cron kế tiếp, không phải chờ cron kiểm tra ngày mai.

    ``token_service`` cố tình ghi bằng ``QuerySet.update()`` nên không đụng signal
    này — nó đã tự set trạng thái đúng khi gia hạn token.
    """
    if raw or instance.pk is None:
        return
    if update_fields is not None and "access_token" not in update_fields:
        return

    previous = (
        FacebookApp.objects.filter(pk=instance.pk)
        .values_list("access_token", flat=True)
        .first()
    )
    if previous is None or previous == instance.access_token:
        return

    instance.token_status = FacebookApp.TOKEN_UNKNOWN
    instance.token_expires_at = None
    instance.data_access_expires_at = None
    instance.token_checked_at = None
    instance.token_invalid_since = None
    instance.token_error = ""
