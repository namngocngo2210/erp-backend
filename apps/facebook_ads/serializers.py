from rest_framework import serializers

from .models import FacebookAdAccount, FacebookApp


class FacebookAppSerializer(serializers.ModelSerializer):
    ad_accounts_count = serializers.SerializerMethodField()

    class Meta:
        model = FacebookApp
        fields = (
            "id",
            "name",
            "app_id",
            "app_secret",
            "access_token",
            "note",
            "ad_accounts_count",
            "token_status",
            "token_expires_at",
            "data_access_expires_at",
            "token_checked_at",
            "token_invalid_since",
            "token_error",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "app_secret": {"write_only": True},
            "access_token": {"write_only": True},
            # Trạng thái token do hệ thống ghi (debug_token / kết quả sync), không
            # cho client set tay — sửa tay là mở lại ngắt mạch với token vẫn chết.
            "token_status": {"read_only": True},
            "token_expires_at": {"read_only": True},
            "data_access_expires_at": {"read_only": True},
            "token_checked_at": {"read_only": True},
            "token_invalid_since": {"read_only": True},
            "token_error": {"read_only": True},
        }

    def get_ad_accounts_count(self, obj: FacebookApp) -> int:
        return obj.ad_accounts.filter(status=FacebookAdAccount.STATUS_ACTIVE).count()


class FacebookAppUpsertSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    app_id = serializers.CharField(max_length=100)
    app_secret = serializers.CharField(max_length=512)
    access_token = serializers.CharField(max_length=1024)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class FacebookAppPartialUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    app_id = serializers.CharField(max_length=100, required=False)
    app_secret = serializers.CharField(max_length=512, required=False)
    access_token = serializers.CharField(max_length=1024, required=False)
    note = serializers.CharField(required=False, allow_blank=True)


class FacebookAdAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacebookAdAccount
        fields = (
            "id",
            "app",
            "name",
            "account_id",
            "fb_id",
            "spend_cap",
            "currency",
            "status",
            "last_synced_at",
            "created_at",
            "updated_at",
        )


class FacebookInsightRowSerializer(serializers.Serializer):
    """Schema-only (OpenAPI): 1 dòng insight thô từ Graph API.

    Field hiện diện tùy ``level`` (account/campaign/adset/ad); Graph API trả
    số liệu dưới dạng chuỗi. Không dùng để validate/serialize runtime.
    """

    campaign_id = serializers.CharField(required=False)
    campaign_name = serializers.CharField(required=False)
    adset_id = serializers.CharField(required=False)
    adset_name = serializers.CharField(required=False)
    ad_id = serializers.CharField(required=False)
    ad_name = serializers.CharField(required=False)
    spend = serializers.CharField(required=False)
    impressions = serializers.CharField(required=False)
    clicks = serializers.CharField(required=False)
    ctr = serializers.CharField(required=False)
    cpm = serializers.CharField(required=False)
    reach = serializers.CharField(required=False)
    date_start = serializers.CharField(required=False)
    date_stop = serializers.CharField(required=False)
