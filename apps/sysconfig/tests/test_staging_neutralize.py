import pytest
from django.core.management import call_command

from apps.sysconfig.models import SystemSetting, SystemSettingChange

pytestmark = pytest.mark.django_db


def test_neutralize_deletes_forbidden_and_inbound_tokens(settings):
    settings.APP_ENV = "staging"
    settings.IS_STAGING = True
    settings.IS_PRODUCTION = False
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="k")
    SystemSetting.objects.create(key="HERMES_API_TOKEN", secret_value="t")
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="80")
    call_command("staging_neutralize", "--yes")
    assert set(SystemSetting.objects.values_list("key", flat=True)) == {"QA_PASS_SCORE"}
    assert SystemSettingChange.objects.filter(key="GEMINI_API_KEY", new_value="").exists()
