"""API connect/callback/status: quyền admin, state một lần, đổi code lưu token."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils.http import urlencode
from rest_framework.test import APIClient

from apps.cnv_loyalty.contracts.dtos import TokenBundle
from apps.cnv_loyalty.models import CnvConnection
from apps.sysconfig import override_sysconfig, sysconfig
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    user = User.objects.create_user(username="cnv-admin", password="x")
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def user_client():
    user = User.objects.create_user(username="cnv-user", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestConnect:
    def test_requires_admin(self, user_client, cnv_connection):
        assert user_client.get("/api/cnv/connect").status_code == 403

    def test_returns_authorize_url(self, admin_client, cnv_connection):
        with override_sysconfig(CNV_SSO_REDIRECT_URI="https://crm.example.com/api/cnv/callback"):
            response = admin_client.get("/api/cnv/connect")
        assert response.status_code == 200
        assert response.data["authorize_url"].startswith(sysconfig.get("CNV_SSO_SERVER_URI"))
        assert "state=" in response.data["authorize_url"]

    def test_no_connection_is_400(self, admin_client, db):
        response = admin_client.get("/api/cnv/connect")
        assert response.status_code == 400


class TestCallback:
    def _state_for(self, connection):
        cache.set("cnv_sso_state:good-state", connection.pk, timeout=60)
        return "good-state"

    def test_exchanges_code_and_saves_tokens(self, admin_client, cnv_connection):
        state = self._state_for(cnv_connection)
        bundle = TokenBundle(
            access_token="cb-token", refresh_token="cb-refresh", expires_in=3600
        )
        client = APIClient()  # AllowAny: browser redirect không mang JWT
        with patch(
            "apps.cnv_loyalty.transport.api_views.CnvSsoClient"
        ) as sso:
            sso.return_value.exchange_code.return_value = bundle
            response = client.get(
                f"/api/cnv/callback?{urlencode({'code': 'c1', 'state': state})}"
            )
        assert response.status_code == 200
        fresh = CnvConnection.objects.get(pk=cnv_connection.pk)
        assert fresh.access_token == "cb-token"
        assert fresh.refresh_token == "cb-refresh"

    def test_invalid_state_is_400(self, cnv_connection):
        response = APIClient().get("/api/cnv/callback?code=c1&state=bogus")
        assert response.status_code == 400

    def test_state_is_single_use(self, cnv_connection):
        state = self._state_for(cnv_connection)
        bundle = TokenBundle(access_token="t", expires_in=60)
        with patch(
            "apps.cnv_loyalty.transport.api_views.CnvSsoClient"
        ) as sso:
            sso.return_value.exchange_code.return_value = bundle
            first = APIClient().get(f"/api/cnv/callback?code=c&state={state}")
            second = APIClient().get(f"/api/cnv/callback?code=c&state={state}")
        assert first.status_code == 200
        assert second.status_code == 400

    def test_missing_params_is_400(self, cnv_connection):
        assert APIClient().get("/api/cnv/callback").status_code == 400


class TestStatus:
    def test_requires_admin(self, user_client):
        assert user_client.get("/api/cnv/status").status_code == 403

    def test_reports_connection_and_records(self, admin_client, cnv_connection, settings):
        from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord, CnvSyncStatus

        settings.CNV_SYNC_ENABLED = True
        CnvSyncRecord.objects.create(
            entity_type=CnvEntityType.ORDER, local_id=1, status=CnvSyncStatus.FAILED
        )
        response = admin_client.get("/api/cnv/status")
        assert response.status_code == 200
        assert response.data["enabled"] is True
        assert response.data["connected"] is True
        assert response.data["records"]["order"]["failed"] == 1
