from django.contrib import admin

from apps.pancake.models import (
    PancakeComment,
    PancakeConnection,
    PancakeLead,
    PancakePage,
    PancakePost,
    PancakeStaffMap,
)


@admin.register(PancakeConnection)
class PancakeConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "auth_status", "is_active", "last_pages_synced_at", "updated_at")
    readonly_fields = ("auth_status", "auth_error", "last_pages_synced_at")


@admin.register(PancakePage)
class PancakePageAdmin(admin.ModelAdmin):
    list_display = (
        "name", "pancake_page_id", "platform", "channel", "is_enabled", "has_page_token",
        "polled_until", "last_polled_at",
    )
    list_filter = ("platform", "is_enabled", "is_activated")
    search_fields = ("name", "pancake_page_id")
    # Token/cursor do hệ thống ghi; sửa tay chỉ tạo lệch với Pancake. `page_access_token`
    # không nằm trong readonly_fields nữa — `exclude` gạt hẳn nó khỏi form, không hiện ra
    # (kể cả đọc), cùng nguyên tắc "không endpoint nào trả token" với API.
    readonly_fields = ("token_error", "polled_until", "last_polled_at", "last_error", "synced_at")
    exclude = ("page_access_token",)

    @admin.display(boolean=True, description="Có token page")
    def has_page_token(self, obj: PancakePage) -> bool:
        return bool(obj.page_access_token)


@admin.register(PancakeLead)
class PancakeLeadAdmin(admin.ModelAdmin):
    list_display = (
        "phone", "customer_name", "page", "channel", "push_status", "order", "cohoi_id", "created_at",
    )
    list_filter = ("push_status", "page")
    search_fields = ("phone", "customer_name", "conversation_id")
    readonly_fields = ("response_body", "error", "http_status", "pushed_at", "created_at")
    list_select_related = ("page", "channel", "order")


@admin.register(PancakeStaffMap)
class PancakeStaffMapAdmin(admin.ModelAdmin):
    list_display = ("pancake_name", "user", "last_seen_at")
    search_fields = ("pancake_name",)
    # `User` đăng ký bằng `admin.site.register(User)` (không có `search_fields`)
    # nên `autocomplete_fields` sẽ lỗi — dùng `raw_id_fields` thay thế.
    raw_id_fields = ("user",)


@admin.register(PancakePost)
class PancakePostAdmin(admin.ModelAdmin):
    list_display = ("post_id", "page", "short_message", "first_seen_at")
    list_filter = ("page",)
    search_fields = ("post_id", "message")
    readonly_fields = ("first_seen_at", "updated_at")
    list_select_related = ("page",)

    @admin.display(description="Nội dung")
    def short_message(self, obj: PancakePost) -> str:
        return obj.message[:80]


@admin.register(PancakeComment)
class PancakeCommentAdmin(admin.ModelAdmin):
    list_display = (
        "commented_at", "page", "from_name", "short_message", "is_page_reply", "has_phone", "is_removed",
    )
    list_filter = ("is_page_reply", "is_removed", "is_hidden", "has_phone", "page")
    search_fields = ("message", "from_name", "comment_id", "post__post_id")
    readonly_fields = ("first_seen_at", "updated_at")
    list_select_related = ("page",)
    raw_id_fields = ("post",)
    date_hierarchy = "commented_at"

    @admin.display(description="Nội dung")
    def short_message(self, obj: PancakeComment) -> str:
        return obj.message[:80]
