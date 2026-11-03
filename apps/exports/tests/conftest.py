import pytest
from rest_framework.test import APIClient

from apps.exports.application import registry as export_registry
from apps.exports.application.registry import ExportDefinition, register_export
from apps.users.tests.helpers import make_user_with_sections


class FakeExport(ExportDefinition):
    """Định nghĩa giả để test engine mà không phụ thuộc app nghiệp vụ nào."""

    kind = "fake"

    def validate(self, user, raw_params):
        return {"n": int(raw_params.get("n", 2))}

    def check_permission(self, user, params):
        return None

    def estimate(self, params):
        return params["n"]

    def file_name(self, params):
        return "fake.xlsx"

    def write(self, params, writer, progress):
        writer.write_header(["i"])
        for i in range(params["n"]):
            writer.write_row([i])
            progress(i + 1)


@pytest.fixture
def fake_export():
    """Đăng ký `fake` — dọn dẹp đã có sẵn ở `_reset_export_registry` (autouse bên
    dưới), không lặp lại ở đây để tránh hai fixture cùng pop chồng nhau.
    """
    if "fake" not in export_registry._REGISTRY:
        register_export(FakeExport())
    return export_registry._REGISTRY["fake"]


@pytest.fixture
def export_user(db):
    return make_user_with_sections("export-user", ["orders"])


@pytest.fixture
def export_client(export_user):
    client = APIClient()
    client.force_authenticate(export_user)
    return client


@pytest.fixture(autouse=True)
def _reset_export_registry():
    """Dọn CHỈ những kind mà chính test này đăng ký thêm.

    `_REGISTRY` là state cấp module, sống suốt tiến trình pytest. Nếu không dọn,
    một test đăng ký "dummy_test_kind" sẽ làm test chạy sau tưởng nhầm là trùng
    kind — lỗi phụ thuộc thứ tự chạy, cùng loại sự cố đã gặp với event_bus
    (xem event-bus-subscriber-test-ordering trong memory).

    KHÔNG được `_REGISTRY.clear()` toàn bộ dict: các app nghiệp vụ thật (orders,
    inventory, warranty, ...) đăng ký `ExportDefinition` của mình một lần duy nhất
    trong `AppConfig.ready()` khi tiến trình pytest khởi động, không đăng ký lại
    giữa các test. `apps/exports/tests/` chạy trước các app đó theo thứ tự
    alphabet của `pytest apps`, nên nếu clear() cả dict ở đây, các kind thật sẽ
    biến mất vĩnh viễn sau khi bộ test này chạy xong — làm test ở app sau gọi
    `get_definition(...)` ăn `UnknownExportKind`: pass khi chạy riêng, fail khi
    chạy cả suite. Vì vậy chỉ snapshot rồi xoá đúng những key mới xuất hiện.
    """
    before = set(export_registry._REGISTRY)
    yield
    added = set(export_registry._REGISTRY) - before
    for kind in added:
        export_registry._REGISTRY.pop(kind, None)
