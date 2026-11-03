import pytest
from django.db import IntegrityError, connection, transaction

from apps.sysconfig.models import SystemSetting, SystemSettingChange

pytestmark = pytest.mark.django_db


def test_secret_value_is_encrypted_at_rest():
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="plain-secret")
    with connection.cursor() as cur:
        cur.execute("SELECT secret_value FROM system_settings WHERE key = %s", ["GEMINI_API_KEY"])
        (stored,) = cur.fetchone()
    assert stored != "plain-secret"
    assert SystemSetting.objects.get(key="GEMINI_API_KEY").secret_value == "plain-secret"


def test_key_unique():
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="80")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SystemSetting.objects.create(key="QA_PASS_SCORE", value="90")


def test_change_log_orders_newest_first():
    a = SystemSettingChange.objects.create(key="K", old_value="", new_value="1")
    b = SystemSettingChange.objects.create(key="K", old_value="1", new_value="2")
    assert list(SystemSettingChange.objects.filter(key="K")) == [b, a]
