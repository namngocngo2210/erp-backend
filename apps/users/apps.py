from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"

    def ready(self):
        from . import rbac  # noqa: F401  đăng ký signal bust cache

        rbac._connect_user_channel_signals()

        from apps.users import subscribers

        subscribers.register()

        # Đăng ký OpenAPI auth extension (D7). Guard: core auth không vỡ nếu
        # drf-spectacular chưa cài (vd container cũ chưa rebuild).
        try:
            from apps.users import schema  # noqa: F401
        except ImportError:
            pass

