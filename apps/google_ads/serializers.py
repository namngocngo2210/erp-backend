from rest_framework import serializers

from .models import GoogleAdsAccount, GoogleAdsConfig


class GoogleAdsConfigSerializer(serializers.ModelSerializer):
    accounts_count = serializers.SerializerMethodField()

    class Meta:
        model = GoogleAdsConfig
        fields = (
            "id",
            "name",
            "developer_token",
            "client_id",
            "client_secret",
            "refresh_token",
            "login_customer_id",
            "note",
            "accounts_count",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "developer_token": {"write_only": True},
            "client_secret": {"write_only": True},
            "refresh_token": {"write_only": True},
        }

    def get_accounts_count(self, obj: GoogleAdsConfig) -> int:
        return obj.accounts.filter(status=GoogleAdsAccount.STATUS_ACTIVE).count()


class GoogleAdsConfigUpsertSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    developer_token = serializers.CharField(max_length=512)
    client_id = serializers.CharField(max_length=255)
    client_secret = serializers.CharField(max_length=512)
    refresh_token = serializers.CharField(max_length=1024)
    login_customer_id = serializers.CharField(max_length=20)
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_login_customer_id(self, value: str) -> str:
        digits = value.replace("-", "").strip()
        if not digits.isdigit():
            raise serializers.ValidationError("Login Customer ID chỉ gồm chữ số (MCC ID).")
        return digits


class GoogleAdsConfigPartialUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    developer_token = serializers.CharField(max_length=512, required=False)
    client_id = serializers.CharField(max_length=255, required=False)
    client_secret = serializers.CharField(max_length=512, required=False)
    refresh_token = serializers.CharField(max_length=1024, required=False)
    login_customer_id = serializers.CharField(max_length=20, required=False)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate_login_customer_id(self, value: str) -> str:
        digits = value.replace("-", "").strip()
        if not digits.isdigit():
            raise serializers.ValidationError("Login Customer ID chỉ gồm chữ số (MCC ID).")
        return digits


class GoogleAdsAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoogleAdsAccount
        fields = (
            "id",
            "config",
            "name",
            "customer_id",
            "currency",
            "status",
            "last_synced_at",
            "created_at",
            "updated_at",
        )
