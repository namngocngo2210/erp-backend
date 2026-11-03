from django.contrib import admin

from apps.cnv_loyalty.models import (
    CnvConnection,
    CnvInboundCursor,
    CnvOrderLink,
    CnvSyncRecord,
    CnvWebhookEvent,
)


@admin.register(CnvConnection)
class CnvConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "name", "account_id", "client_id", "is_active", "token_expires_at", "updated_at",
    )
    # Secret/token mã hoá — không sửa tay qua admin, chỉ xem trạng thái.
    readonly_fields = ("access_token", "refresh_token", "token_expires_at", "scope")


@admin.register(CnvSyncRecord)
class CnvSyncRecordAdmin(admin.ModelAdmin):
    list_display = (
        "entity_type", "local_id", "cnv_id", "status", "attempts", "last_synced_at",
    )
    list_filter = ("entity_type", "status")
    search_fields = ("local_id", "cnv_id")
    readonly_fields = ("created_at", "updated_at")


# ── Chiều ngược CNV → desi-order (spec 2026-08-19) ───────────────────────────
# Toàn bộ chỉ-đọc: đây là dữ liệu do hệ thống ngoài đổ về, sửa tay qua admin thì
# lần sync sau ghi đè và người sửa không hiểu vì sao thay đổi biến mất.


@admin.register(CnvWebhookEvent)
class CnvWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "topic", "cnv_object_id", "processed", "attempts", "received_at")
    list_filter = ("processed", "topic")
    search_fields = ("cnv_object_id", "payload_hash")
    readonly_fields = tuple(
        field.name for field in CnvWebhookEvent._meta.fields if field.name != "id"
    )


@admin.register(CnvOrderLink)
class CnvOrderLinkAdmin(admin.ModelAdmin):
    list_display = (
        "cnv_order_id", "cnv_name", "order_id", "financial_status",
        "fulfillment_status", "cancelled_status", "cnv_updated_at",
    )
    search_fields = ("cnv_order_id", "cnv_name", "unique_id")
    readonly_fields = tuple(
        field.name for field in CnvOrderLink._meta.fields if field.name != "id"
    )


@admin.register(CnvInboundCursor)
class CnvInboundCursorAdmin(admin.ModelAdmin):
    list_display = ("resource", "synced_until", "last_run_at")
    # synced_until SỬA ĐƯỢC có chủ đích: kéo lùi mốc là cách quét lại một khoảng
    # thời gian mà không cần lệnh riêng.
    readonly_fields = ("last_run_at", "last_error")
