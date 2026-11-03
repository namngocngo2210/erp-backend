import pytest

from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyActivation, WarrantyCode, WarrantyCodeScanLog

pytestmark = pytest.mark.django_db

URL = "/api/warranty-portal/activate"
_PHONE = "0901234567"


def _code(product_id=None) -> WarrantyCode:
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=product_id, created_by=None)
    return WarrantyCode.objects.filter(batch=batch).first()


def test_activate_valid_code_returns_warranty_and_registers_phone(public_client, product_factory):
    """Bản sửa đổi 08/08: SĐT quay lại BẮT BUỘC, nhưng chỉ để đăng ký bảo hành
    điện tử theo SĐT cho CSKH — mã dưới lớp cào vẫn là thứ xác thực quyền sở hữu,
    không còn token phiên."""
    product = product_factory(name="Bồn ngâm chân", sku="BNC02")
    code = _code(product.id)
    r = public_client.post(URL, {"keyword": code.code, "phone": _PHONE}, format="json")
    assert r.status_code == 201, r.data
    assert "token" not in r.data
    w = r.data["warranty"]
    assert (w["serial"], w["code"], w["product_sku"], w["months"]) == (code.serial, code.code, "BNC02", 24)
    assert w["days_left"] > 700
    activation = WarrantyActivation.objects.get(code=code)
    assert activation.phone == _PHONE


def test_activate_missing_phone_returns_400(public_client):
    code = _code()
    r = public_client.post(URL, {"keyword": code.code}, format="json")
    assert r.status_code == 400, r.data
    assert WarrantyActivation.objects.count() == 0


def test_activate_invalid_phone_returns_400(public_client):
    code = _code()
    r = public_client.post(URL, {"keyword": code.code, "phone": "123"}, format="json")
    assert r.status_code == 400, r.data
    assert WarrantyActivation.objects.count() == 0


def test_activate_normalizes_international_phone_format(public_client):
    code = _code()
    r = public_client.post(
        URL, {"keyword": code.code, "phone": "+84 901 234 567"}, format="json"
    )
    assert r.status_code == 201, r.data
    activation = WarrantyActivation.objects.get(code=code)
    assert activation.phone == "0901234567"


def test_activate_same_code_again_returns_viewed_without_overwriting_phone(public_client):
    code = _code()
    r1 = public_client.post(URL, {"keyword": code.code, "phone": "0901111111"}, format="json")
    assert r1.status_code == 201
    r2 = public_client.post(URL, {"keyword": code.code, "phone": "0902222222"}, format="json")
    assert r2.status_code == 200, r2.data
    assert "token" not in r2.data
    assert WarrantyActivation.objects.filter(code=code).count() == 1
    activation = WarrantyActivation.objects.get(code=code)
    # SĐT đăng ký lần đầu là chuẩn — lần "xem lại" với SĐT khác không ghi đè.
    assert activation.phone == "0901111111"


def test_activate_by_serial_returns_404(public_client):
    """`serial` in công khai trên tem, KHÔNG bao giờ dùng để kích hoạt/xem lại."""
    code = _code()
    r = public_client.post(URL, {"keyword": code.serial, "phone": _PHONE}, format="json")
    assert r.status_code == 404
    assert r.data["code"] == "not_found"
    assert WarrantyActivation.objects.count() == 0


def test_activate_unknown_code_returns_404_and_logs(public_client):
    r = public_client.post(URL, {"keyword": "SAI", "phone": _PHONE}, format="json")
    assert r.status_code == 404
    assert r.data["code"] == "not_found"
    log = WarrantyCodeScanLog.objects.get()
    assert log.result == "not_found"


def test_activate_records_client_ip_user_agent_and_submitted_phone(public_client):
    code = _code()
    public_client.post(
        URL, {"keyword": code.code, "phone": _PHONE}, format="json",
        HTTP_CF_CONNECTING_IP="8.8.8.8", HTTP_USER_AGENT="pytest-agent",
    )
    log = WarrantyCodeScanLog.objects.get()
    assert (log.ip_address, log.user_agent, log.phone) == ("8.8.8.8", "pytest-agent", _PHONE)


def test_activate_scan_log_records_submitted_phone_on_viewed_branch(public_client):
    """Nhánh 'xem lại' không ghi đè `activation.phone`, nhưng scan log vẫn ghi
    lại SĐT khách VỪA NHẬP lần này (khác SĐT đăng ký lần đầu)."""
    code = _code()
    public_client.post(URL, {"keyword": code.code, "phone": "0901111111"}, format="json")
    public_client.post(URL, {"keyword": code.code, "phone": "0902222222"}, format="json")
    viewed_log = WarrantyCodeScanLog.objects.get(result="viewed")
    assert viewed_log.phone == "0902222222"


def test_zalo_channel_requires_token(public_client, settings):
    settings.WARRANTY_PORTAL_ZALO_TOKEN = "secret-token"
    code = _code()
    r = public_client.post(
        URL, {"keyword": code.code, "phone": _PHONE, "channel": "zalo_oa"}, format="json"
    )
    assert r.status_code == 403
    r = public_client.post(
        URL, {"keyword": code.code, "phone": _PHONE, "channel": "zalo_oa"}, format="json",
        HTTP_X_API_TOKEN="secret-token",
    )
    assert r.status_code == 201


def test_zalo_channel_rejects_non_ascii_token_without_500(public_client, settings):
    """secrets.compare_digest với str non-ASCII raise TypeError nếu không encode() trước —
    token gõ nhầm có dấu tiếng Việt phải trả 403, không phải 500 (M3)."""
    settings.WARRANTY_PORTAL_ZALO_TOKEN = "secret-token"
    code = _code()
    r = public_client.post(
        URL, {"keyword": code.code, "phone": _PHONE, "channel": "zalo_oa"}, format="json",
        HTTP_X_API_TOKEN="tokenkhôngđúng",
    )
    assert r.status_code == 403
