import pytest

from apps.users.tests.helpers import make_user_with_sections
from apps.warranty_codes.models import ScanResult, WarrantyCodeScanLog

pytestmark = pytest.mark.django_db

URL = "/api/warranty-codes/scan-logs"


def _log(**kwargs):
    data = {
        "keyword": "ABC123", "result": ScanResult.NOT_FOUND, "channel": "web",
        "ip_address": "1.2.3.4", "user_agent": "UA", "phone": "0901234567",
    }
    data.update(kwargs)
    return WarrantyCodeScanLog.objects.create(**data)


def test_requires_section(db):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(make_user_with_sections("other", ["orders"]))
    assert client.get(f"{URL}?limit=10&offset=0").status_code == 403


def test_list_returns_rows_with_masked_phone(auth_client):
    _log()
    r = auth_client.get(f"{URL}?limit=10&offset=0")
    assert r.status_code == 200, r.data
    row = r.data["results"][0]
    assert row["keyword"] == "ABC123"
    assert row["result_label"] == "Không tìm thấy mã"
    assert row["phone_masked"] == "090****567"


def test_filter_by_keyword_prefix_and_ip(auth_client):
    _log(keyword="ABC123", ip_address="1.1.1.1")
    _log(keyword="XYZ999", ip_address="2.2.2.2")
    assert auth_client.get(f"{URL}?keyword=ABC&limit=10&offset=0").data["total"] == 1
    assert auth_client.get(f"{URL}?ip_address=2.2.2.2&limit=10&offset=0").data["total"] == 1


def test_filter_by_result(auth_client):
    _log(result=ScanResult.NOT_FOUND)
    _log(result=ScanResult.ACTIVATED)
    assert auth_client.get(f"{URL}?result=activated&limit=10&offset=0").data["total"] == 1


def test_invalid_date_does_not_500(auth_client):
    _log()
    assert auth_client.get(f"{URL}?date_from=xyz&limit=10&offset=0").status_code == 200
