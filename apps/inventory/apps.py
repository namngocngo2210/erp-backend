from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"

    def ready(self):
        from apps.inventory import exports_definitions  # noqa: F401
        from apps.inventory import subscribers
        subscribers.register()
