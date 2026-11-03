import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings_parts.base import assert_no_registry_env_leftovers, find_registry_env_leftovers


def test_finds_registry_keys_present_in_env():
    env = {"GEMINI_API_KEY": "x", "QA_PASS_SCORE": "80", "POSTGRES_HOST": "db", "LARK_APP_ID": ""}
    assert find_registry_env_leftovers(env) == ["GEMINI_API_KEY", "QA_PASS_SCORE"]


def test_raises_unless_allowed():
    env = {"GEMINI_API_KEY": "x"}
    with pytest.raises(ImproperlyConfigured, match="GEMINI_API_KEY"):
        assert_no_registry_env_leftovers(env)
    # Cửa thoát trong cửa sổ chuyển đổi: chỉ warning
    assert_no_registry_env_leftovers({**env, "SYSCONFIG_ALLOW_ENV_KEYS": "1"})
