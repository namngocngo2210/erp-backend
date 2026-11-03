from django.contrib import admin

from apps.sysconfig.models import SystemSetting, SystemSettingChange


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "has_secret", "updated_by", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("key", "value", "has_secret", "updated_by", "updated_at")
    exclude = ("secret_value",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True, description="Có secret")
    def has_secret(self, obj):
        return bool(obj.secret_value)


@admin.register(SystemSettingChange)
class SystemSettingChangeAdmin(admin.ModelAdmin):
    list_display = ("key", "old_value", "new_value", "changed_by", "changed_at")
    search_fields = ("key",)
    list_filter = ("key",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
