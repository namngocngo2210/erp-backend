import pytest
from django.utils import timezone

from apps.warranty_codes.application.services import activation_service
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyActivation, WarrantyCode, WarrantyCodeScanLog

pytestmark = pytest.mark.django_db

_PHONE = "0901234567"


def _code(product_id=None) -> WarrantyCode:
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=product_id, created_by=None)
    return WarrantyCode.objects.filter(batch=batch).first()


def test_activate_creates_activation_and_log(product_factory):
    product = product_factory(sku="SKU-ACT")
    code = _code(product.id)
    result = activation_service.activate(
        keyword=code.code, phone=_PHONE, channel="web", ip="1.2.3.4", user_agent="UA"
    )
    assert result.status == "activated"
    assert result.activation.phone == _PHONE
    months = 24
    expected_year_month = (result.activation.activated_at.date().year * 12
                           + result.activation.activated_at.date().month + months)
    assert result.activation.expires_at.year * 12 + result.activation.expires_at.month == expected_year_month
    log = WarrantyCodeScanLog.objects.get()
    assert (log.result, log.code_id, log.product_sku, log.phone) == ("activated", code.id, "SKU-ACT", _PHONE)


def test_activate_by_serial_returns_not_found_before_activation():
    """Serial (DX-000123) tuần tự, đoán được — không dùng để kích hoạt lần đầu."""
    code = _code()
    result = activation_service.activate(
        keyword=code.serial.lower(), phone=_PHONE, channel="web", ip=None, user_agent=""
    )
    assert result.status == "not_found"
    assert WarrantyActivation.objects.count() == 0
    assert WarrantyCodeScanLog.objects.get().result == "not_found"


def test_activate_by_serial_after_activation_still_returns_not_found():
    """Bản sửa đổi 07/08: serial KHÔNG bao giờ có nhánh 'xem lại' — mọi lần nhập
    serial (trước hay sau khi mã đã kích hoạt) đều not_found, để không xác nhận
    serial đó có thật/đã bị chiếm. SĐT là thông tin CSKH, không ảnh hưởng nhánh này."""
    code = _code()
    activation_service.activate(keyword=code.code, phone=_PHONE, channel="web", ip=None, user_agent="")
    result = activation_service.activate(
        keyword=code.serial, phone=_PHONE, channel="web", ip=None, user_agent=""
    )
    assert result.status == "not_found"
    assert WarrantyActivation.objects.count() == 1


def test_activate_by_code_still_works():
    code = _code()
    result = activation_service.activate(keyword=code.code, phone=_PHONE, channel="web", ip=None, user_agent="")
    assert result.status == "activated"


def test_activate_unknown_keyword_logs_not_found():
    result = activation_service.activate(
        keyword="KHONGTONTAI", phone=_PHONE, channel="web", ip=None, user_agent=""
    )
    assert result.status == "not_found"
    assert result.activation is None
    assert WarrantyCodeScanLog.objects.get().result == "not_found"


def test_activate_same_code_again_returns_viewed_without_duplicate():
    """Không còn khái niệm chặn theo SĐT khác nên mọi lần nhập lại đều 'xem lại',
    không bao giờ chặn (không còn nhánh already_activated sinh ra bản ghi mới)."""
    code = _code()
    first = activation_service.activate(keyword=code.code, phone=_PHONE, channel="web", ip=None, user_agent="")
    second = activation_service.activate(keyword=code.code, phone=_PHONE, channel="web", ip=None, user_agent="")
    assert second.status == "viewed"
    assert second.activation.id == first.activation.id
    assert WarrantyActivation.objects.count() == 1
    assert WarrantyCodeScanLog.objects.filter(result="viewed").count() == 1


def test_activate_viewed_does_not_overwrite_phone_from_first_registration():
    """Bản sửa đổi 08/08: SĐT là thông tin đăng ký CSKH lần đầu — xem lại với SĐT
    khác không được ghi đè `activation.phone`, nhưng SĐT vừa nhập vẫn phải vào log."""
    code = _code()
    first = activation_service.activate(
        keyword=code.code, phone="0901111111", channel="web", ip=None, user_agent=""
    )
    second = activation_service.activate(
        keyword=code.code, phone="0902222222", channel="web", ip=None, user_agent=""
    )
    assert second.status == "viewed"
    assert second.activation.id == first.activation.id
    second.activation.refresh_from_db()
    assert second.activation.phone == "0901111111"

    viewed_log = WarrantyCodeScanLog.objects.filter(result="viewed").get()
    assert viewed_log.phone == "0902222222"


def _hide_existing_activation_once(monkeypatch):
    """Mô phỏng cửa sổ race THẬT (TOCTOU): lần gọi ``WarrantyActivation.objects
    .filter(code=...).first()`` ĐẦU TIÊN (check "đã kích hoạt chưa" ở đầu
    ``activate()``) trả None dù bản ghi đối thủ đã có sẵn trong DB — y hệt việc
    request này đọc xong check TRƯỚC khi đối thủ insert. Từ lần gọi thứ 2 trở đi
    (trong nhánh `except IntegrityError`) dùng `filter()` THẬT — để `create()`
    của service tự đụng UNIQUE constraint thật của Postgres trên `code` (OneToOne)
    và bắn `IntegrityError` thật, không mock."""
    real_filter = WarrantyActivation.objects.filter
    calls = {"n": 0}

    class _EmptyQuerySet:
        def first(self):
            return None

    def _filter(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _EmptyQuerySet()
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(WarrantyActivation.objects, "filter", _filter)


def test_activate_race_returns_viewed_with_one_activation(monkeypatch):
    """Hai request cùng lúc kích hoạt cùng mã: request kia thắng cuộc đua insert
    (bản ghi đối thủ đã tồn tại thật trong DB, IntegrityError thật khi service tự
    insert) → phải quy về đúng 1 activation, status "viewed", giữ SĐT của bản ghi
    thắng (không bị ghi đè bằng SĐT của request thua)."""
    code = _code()
    now = timezone.now()
    competing = WarrantyActivation.objects.create(
        code=code, phone="0901111111", channel="web",
        activated_at=now, expires_at=timezone.localdate(now), ip_address=None, user_agent="",
    )

    _hide_existing_activation_once(monkeypatch)

    result = activation_service.activate(
        keyword=code.code, phone="0902222222", channel="web", ip=None, user_agent=""
    )
    assert result.status == "viewed"
    assert result.activation.id == competing.id
    assert WarrantyActivation.objects.count() == 1
    result.activation.refresh_from_db()
    assert result.activation.phone == "0901111111"


def test_expires_at_is_activated_date_plus_months():
    from dateutil.relativedelta import relativedelta

    code = _code()
    result = activation_service.activate(keyword=code.code, phone=_PHONE, channel="web", ip=None, user_agent="")
    expected = timezone.localdate(result.activation.activated_at) + relativedelta(months=24)
    assert result.activation.expires_at == expected


def test_activate_rejects_unknown_channel():
    code = _code()
    with pytest.raises(ValueError):
        activation_service.activate(keyword=code.code, phone=_PHONE, channel="sms", ip=None, user_agent="")
