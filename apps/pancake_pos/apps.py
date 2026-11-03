from django.apps import AppConfig


class PancakePosConfig(AppConfig):
    """Đồng bộ danh mục CRM → Pancake POS (Open API). Độc lập với `apps.pancake`
    (Pages/chat). Thiết kế: docs/pancake-pos-product-sync.md."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pancake_pos"
    verbose_name = "Pancake POS"

    def ready(self):
        from apps.pancake_pos import subscribers

        subscribers.register()
