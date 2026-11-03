from django.apps import AppConfig


class CnvLoyaltyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cnv_loyalty"
    verbose_name = "CNV Loyalty"

    def ready(self):
        from apps.cnv_loyalty import subscribers

        subscribers.register()
