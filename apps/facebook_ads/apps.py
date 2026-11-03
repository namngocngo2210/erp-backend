from django.apps import AppConfig


class FacebookAdsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.facebook_ads"
    verbose_name = "Facebook Ads"

    def ready(self):
        from . import signals  # noqa: F401 — đăng ký post_delete dọn ChannelAdAccount
        from . import adapters

        # Cắm provider chi tiêu vào registry của orders — xem
        # apps/orders/contracts/ads_spend_port.py.
        adapters.register()
