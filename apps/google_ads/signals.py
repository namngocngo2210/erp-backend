"""Dọn mapping kênh khi GoogleAdsAccount bị xoá cứng (kể cả CASCADE từ GoogleAdsConfig).

ChannelAdAccount không FK cứng sang bảng ad account (channels_core ở tầng dưới —
xem backend/.importlinter), nên integrity đảm bảo tại đây: app tầng trên được
phép import channels_core. Fix gốc bug "ads phồng" do id mồ côi trong mapping.
"""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.channels_core.models import ChannelAdAccount

from .models import GoogleAdsAccount


@receiver(
    post_delete,
    sender=GoogleAdsAccount,
    dispatch_uid="google_ads.cleanup_channel_ad_account_links",
)
def cleanup_channel_ad_account_links(sender, instance, **kwargs):
    ChannelAdAccount.objects.filter(
        provider=ChannelAdAccount.Provider.GOOGLE, account_id=instance.pk
    ).delete()
