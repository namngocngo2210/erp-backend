from django.apps import AppConfig


class SysconfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sysconfig"
    verbose_name = "Cấu hình hệ thống"

    def ready(self) -> None:
        # Kiểm tra registry lúc boot: key trùng / group lạ / default sai kiểu → chết sớm.
        from apps.sysconfig.registry import SETTINGS, validate_registry

        validate_registry(SETTINGS)

        from apps.common import runtime_config
        from apps.sysconfig.service import sysconfig

        runtime_config.register_provider(sysconfig.get)
