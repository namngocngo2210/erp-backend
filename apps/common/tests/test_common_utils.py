"""Tests cho các util nhỏ của apps.common: masking + lark_open_api.

Gộp từ: test_masking.py, test_lark_open_api.py (restructure 2026-08-05).
"""

import uuid
from unittest.mock import patch

import pytest

from apps.common import lark_open_api
from apps.common.lark_open_api import LarkCredentials, LarkOpenAPIError
from apps.common.masking import mask_keep_last


class TestMaskKeepLast:
    def test_masks_all_but_last_3_by_default(self):
        assert mask_keep_last("0912345678") == "*******678"

    def test_custom_keep_length(self):
        assert mask_keep_last("0912345678", keep=4) == "******5678"

    def test_value_shorter_than_keep_masked_entirely(self):
        assert mask_keep_last("12", keep=3) == "**"

    def test_empty_value_returns_empty(self):
        assert mask_keep_last("") == ""
        assert mask_keep_last(None) == ""

    def test_non_string_value_coerced(self):
        assert mask_keep_last(912345678) == "******678"


class TestTenantTokenCache:
    """apps.common.lark_open_api — token cache tách theo app_id."""

    def test_token_cached_and_isolated_per_app(self):
        # app_id unique per run: cache là Redis dùng chung, không dọn bằng cache.clear().
        app_a = LarkCredentials(f"cli_a_{uuid.uuid4().hex}", "secret_a")
        app_b = LarkCredentials(f"cli_b_{uuid.uuid4().hex}", "secret_b")
        try:
            with patch.object(
                lark_open_api,
                "_fetch_tenant_access_token",
                side_effect=[("tok_a", 7200), ("tok_b", 7200)],
            ) as fetch:
                assert lark_open_api.get_tenant_access_token(app_a) == "tok_a"
                assert lark_open_api.get_tenant_access_token(app_a) == "tok_a"
                assert lark_open_api.get_tenant_access_token(app_b) == "tok_b"
            assert fetch.call_count == 2
        finally:
            lark_open_api.invalidate_tenant_access_token(app_a)
            lark_open_api.invalidate_tenant_access_token(app_b)

    def test_missing_default_credentials_raise(self, settings):
        settings.LARK_APP_ID = ""
        settings.LARK_APP_SECRET = ""
        with pytest.raises(LarkOpenAPIError):
            lark_open_api.get_tenant_access_token()
