from django.contrib import admin

from apps.foxia.models import (
    FoxiaBranch,
    FoxiaBrand,
    FoxiaChannel,
    FoxiaChannelMap,
    FoxiaConnection,
    FoxiaLeadLink,
    FoxiaLinkCell,
    FoxiaLinkChannel,
    FoxiaMarketingTeam,
    FoxiaOrderPush,
    FoxiaResalePush,
    FoxiaSaleTeam,
    FoxiaSource,
    FoxiaStage,
    FoxiaSyncCursor,
    FoxiaSyncRun,
    FoxiaUser,
    FoxiaUserMap,
)


@admin.register(FoxiaConnection)
class FoxiaConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "database", "username", "auth_status", "uid", "is_active", "updated_at")
    list_filter = ("auth_status", "is_active")
    # uid/trạng thái do hệ thống ghi sau mỗi lần authenticate — sửa tay chỉ tạo
    # sai lệch với thực tế bên Foxia. api_key vẫn sửa được để xoay khoá.
    readonly_fields = ("uid", "uid_checked_at", "auth_status", "auth_error")


@admin.register(FoxiaSyncCursor)
class FoxiaSyncCursorAdmin(admin.ModelAdmin):
    list_display = ("resource", "synced_until", "last_run_at")
    search_fields = ("resource",)


@admin.register(FoxiaSyncRun)
class FoxiaSyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "resource", "status", "fetched", "created", "updated", "skipped", "started_at", "finished_at",
    )
    list_filter = ("status", "resource")
    readonly_fields = tuple(f.name for f in FoxiaSyncRun._meta.fields)


class _CatalogAdmin(admin.ModelAdmin):
    """Danh mục là bản sao đọc từ Foxia — xem để đối chiếu, không sửa tay."""

    list_display = ("foxia_id", "name", "synced_at")
    search_fields = ("foxia_id", "name")
    readonly_fields = ("foxia_id", "name", "synced_at")


admin.site.register(FoxiaBranch, _CatalogAdmin)
admin.site.register(FoxiaMarketingTeam, _CatalogAdmin)
admin.site.register(FoxiaChannel, _CatalogAdmin)
admin.site.register(FoxiaBrand, _CatalogAdmin)


@admin.register(FoxiaSaleTeam)
class FoxiaSaleTeamAdmin(_CatalogAdmin):
    list_display = ("foxia_id", "name", "branch", "synced_at")
    readonly_fields = ("foxia_id", "name", "branch", "synced_at")


@admin.register(FoxiaStage)
class FoxiaStageAdmin(_CatalogAdmin):
    list_display = ("foxia_id", "name", "phanloai", "synced_at")
    readonly_fields = ("foxia_id", "name", "phanloai", "synced_at")


@admin.register(FoxiaUser)
class FoxiaUserAdmin(_CatalogAdmin):
    list_display = ("foxia_id", "login", "name", "is_active_foxia", "synced_at")
    search_fields = ("foxia_id", "login", "name")
    list_filter = ("is_active_foxia",)
    readonly_fields = (
        "foxia_id", "login", "name", "is_active_foxia", "marketing_team", "sale_team", "synced_at",
    )


@admin.register(FoxiaSource)
class FoxiaSourceAdmin(_CatalogAdmin):
    list_display = ("foxia_id", "name", "channel", "brand", "marketer", "branch", "synced_at")
    readonly_fields = (
        "foxia_id", "name", "channel", "brand", "marketer", "marketing_team", "branch", "synced_at",
    )


@admin.register(FoxiaUserMap)
class FoxiaUserMapAdmin(admin.ModelAdmin):
    list_display = ("foxia_user", "user", "created_at")
    raw_id_fields = ("foxia_user", "user")


@admin.register(FoxiaChannelMap)
class FoxiaChannelMapAdmin(admin.ModelAdmin):
    list_display = ("kind", "foxia_id", "channel", "created_at")
    list_filter = ("kind",)
    raw_id_fields = ("channel",)


@admin.register(FoxiaOrderPush)
class FoxiaOrderPushAdmin(admin.ModelAdmin):
    """Chỉ ĐỌC. Đây là nhật ký đối soát — sửa/xoá tay là phá bằng chứng."""

    list_display = ("order", "status", "foxia_source_id", "http_status", "duration_ms", "created_at")
    list_filter = ("status", "created_at")
    # "=order__id": order__id là BigAutoField — không có "=" thì Django sinh
    # order__id__icontains, lỗi Postgres ("operator does not exist: bigint ~~ text")
    # ngay khi ai gõ ký tự không phải số vào ô tìm kiếm.
    search_fields = ("=order__id", "order__phone")
    readonly_fields = tuple(f.name for f in FoxiaOrderPush._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FoxiaLeadLink)
class FoxiaLeadLinkAdmin(admin.ModelAdmin):
    """Chỉ ĐỌC. `foxia_order_id` do job đối chiếu điền — sửa tay là dựng số giả."""

    list_display = ("cohoi_id", "khachhang_id", "order", "foxia_order_id", "matched_at", "created_at")
    list_filter = ("created_at", ("foxia_order_id", admin.EmptyFieldListFilter))
    # "=order__id": xem ghi chú ở FoxiaOrderPushAdmin — thiếu "=" là lỗi Postgres.
    search_fields = ("=cohoi_id", "=khachhang_id", "=foxia_order_id", "=order__id", "order__phone")
    readonly_fields = tuple(f.name for f in FoxiaLeadLink._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FoxiaLinkChannel)
class FoxiaLinkChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "sort_order")


@admin.register(FoxiaLinkCell)
class FoxiaLinkCellAdmin(admin.ModelAdmin):
    list_display = ("id", "link_channel", "kind", "foxia_source_id", "updated_at")
    list_select_related = ("link_channel__channel",)
    list_filter = ("kind",)


@admin.register(FoxiaResalePush)
class FoxiaResalePushAdmin(admin.ModelAdmin):
    """Chỉ ĐỌC — nhật ký job cơ hội resale. Xoá dòng = cho phép đẩy lại đơn đó, việc
    này phải là quyết định có chủ đích qua shell, không phải một nút bấm."""

    list_display = ("order", "delivered_on", "status", "staff", "foxia_source_id", "http_status", "cohoi_id", "duration_ms", "created_at")
    list_filter = ("status", "delivered_on")
    # "=order__id": xem ghi chú ở FoxiaOrderPushAdmin — thiếu "=" là lỗi Postgres.
    search_fields = ("=order__id", "=cohoi_id", "phone")
    readonly_fields = tuple(f.name for f in FoxiaResalePush._meta.fields)
    raw_id_fields = ("order", "staff")
    list_select_related = ("staff",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
