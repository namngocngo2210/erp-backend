"""Đồng bộ danh mục Foxia: idempotent, dịch many2one, ghi nhật ký lượt chạy."""

import pytest

from apps.foxia.application.services.catalog_sync_service import FoxiaCatalogSyncService
from apps.foxia.models import (
    FoxiaBranch,
    FoxiaBrand,
    FoxiaChannel,
    FoxiaMarketingTeam,
    FoxiaSaleTeam,
    FoxiaSource,
    FoxiaStage,
    FoxiaSyncRun,
    FoxiaSyncStatus,
    FoxiaUser,
)

pytestmark = pytest.mark.django_db


class FakeFoxiaClient:
    """Client giả trả dữ liệu đúng hình dạng tài liệu Foxia mô tả.

    Quan hệ many2one trả ``[id, "tên"]`` — không mock kiểu tự nghĩ, vì chính hình
    dạng này là thứ mapper phải xử lý đúng.
    """

    def __init__(self, data: dict, page_size: int = 500):
        self.data = data
        self.page_size = page_size
        self.calls: list[str] = []

    def iter_all(self, resource, fields=None, order="id asc", page_size=500):
        self.calls.append(resource)
        rows = self.data.get(resource, [])
        size = min(page_size, self.page_size)
        for start in range(0, len(rows), size):
            yield rows[start : start + size]


def _full_dataset():
    return {
        "congtycon": [{"id": 12, "name": "Chi nhánh Hà Nội"}],
        "doinhommarketing": [{"id": 3, "name": "Team MKT 1"}],
        "kenhmarketing": [{"id": 7, "name": "Facebook"}],
        "thuonghieu": [{"id": 9, "name": "Delax"}],
        "giaidoandonhang": [{"id": 5, "name": "Đã chốt", "phanloai": "confirmed"}],
        "crm.team": [{"id": 21, "name": "Sale A", "congtycon_id": [12, "Chi nhánh Hà Nội"]}],
        "res.users": [
            {
                "id": 100,
                "login": "mkt.an",
                "name": "Nguyễn An",
                "active": True,
                "doinhommarketing_id": [3, "Team MKT 1"],
                "sale_team_id": [21, "Sale A"],
            }
        ],
        "utm.source": [
            {
                "id": 55,
                "name": "FB-Landing-01",
                "thuonghieu_id": [9, "Delax"],
                "kenhmarketing_id": [7, "Facebook"],
                "nhanvienmarketing_id": [100, "Nguyễn An"],
                "doinhommarketing_id": [3, "Team MKT 1"],
                "congtycon_id": [12, "Chi nhánh Hà Nội"],
            }
        ],
    }


def test_sync_all_populates_every_catalog():
    client = FakeFoxiaClient(_full_dataset())

    results = FoxiaCatalogSyncService(client).sync_all()

    assert all(r.ok for r in results), [r.error for r in results if not r.ok]
    assert FoxiaBranch.objects.get(foxia_id=12).name == "Chi nhánh Hà Nội"
    assert FoxiaMarketingTeam.objects.count() == 1
    assert FoxiaChannel.objects.count() == 1
    assert FoxiaBrand.objects.count() == 1
    assert FoxiaUser.objects.count() == 1
    assert FoxiaSource.objects.count() == 1


def test_many2one_relations_resolve_to_local_fk():
    client = FakeFoxiaClient(_full_dataset())

    FoxiaCatalogSyncService(client).sync_all()

    source = FoxiaSource.objects.get(foxia_id=55)
    assert source.channel.foxia_id == 7
    assert source.brand.foxia_id == 9
    assert source.marketer.login == "mkt.an"
    assert source.branch.foxia_id == 12
    assert FoxiaSaleTeam.objects.get(foxia_id=21).branch.foxia_id == 12
    assert FoxiaUser.objects.get(foxia_id=100).sale_team.foxia_id == 21


def test_catalogs_sync_in_dependency_order():
    """Danh mục được tham chiếu phải đi trước — nếu không FK rỗng ở lượt đầu."""
    client = FakeFoxiaClient(_full_dataset())

    FoxiaCatalogSyncService(client).sync_all()

    order = client.calls
    assert order.index("congtycon") < order.index("crm.team")
    assert order.index("crm.team") < order.index("res.users")
    assert order.index("res.users") < order.index("utm.source")


def test_sync_twice_is_idempotent_and_updates_name():
    data = _full_dataset()
    FoxiaCatalogSyncService(FakeFoxiaClient(data)).sync_all()

    data["congtycon"][0]["name"] = "Chi nhánh HN (đổi tên)"
    FoxiaCatalogSyncService(FakeFoxiaClient(data)).sync_all()

    assert FoxiaBranch.objects.filter(foxia_id=12).count() == 1
    assert FoxiaBranch.objects.get(foxia_id=12).name == "Chi nhánh HN (đổi tên)"


def test_pagination_covers_every_record():
    data = {
        "congtycon": [{"id": i, "name": f"CN {i}"} for i in range(1, 8)],
    }
    client = FakeFoxiaClient(data, page_size=3)

    FoxiaCatalogSyncService(client).sync_one(_branch_spec())

    assert FoxiaBranch.objects.count() == 7


def test_odoo_false_does_not_become_string_false():
    """Odoo trả `False` cho chuỗi rỗng — lưu thẳng sẽ ra chuỗi 'False' trong DB."""
    data = {"res.users": [{"id": 1, "login": False, "name": False, "active": False}]}

    FoxiaCatalogSyncService(FakeFoxiaClient(data)).sync_one(_users_spec())

    user = FoxiaUser.objects.get(foxia_id=1)
    assert user.login == ""
    assert user.name == ""
    assert user.is_active_foxia is False


def test_row_without_id_is_skipped_and_counted():
    data = {"congtycon": [{"id": 1, "name": "OK"}, {"name": "Không có id"}]}

    result = FoxiaCatalogSyncService(FakeFoxiaClient(data)).sync_one(_branch_spec())

    assert result.written == 1
    assert result.skipped == 1
    assert FoxiaBranch.objects.count() == 1


def test_sync_run_records_success():
    FoxiaCatalogSyncService(FakeFoxiaClient(_full_dataset())).sync_one(_branch_spec())

    run = FoxiaSyncRun.objects.get(resource="catalog:congtycon")
    assert run.status == FoxiaSyncStatus.SUCCESS
    assert run.fetched == 1
    assert run.finished_at is not None


def test_failing_catalog_does_not_stop_the_others():
    class ExplodingClient(FakeFoxiaClient):
        def iter_all(self, resource, **kwargs):
            if resource == "crm.team":
                raise RuntimeError("Foxia 500")
            yield from super().iter_all(resource, **kwargs)

    results = FoxiaCatalogSyncService(ExplodingClient(_full_dataset())).sync_all()

    failed = [r for r in results if not r.ok]
    assert [r.resource for r in failed] == ["crm.team"]
    # Các danh mục sau vẫn chạy.
    assert FoxiaSource.objects.count() == 1
    run = FoxiaSyncRun.objects.get(resource="catalog:crm.team")
    assert run.status == FoxiaSyncStatus.FAILED
    assert "Foxia 500" in run.error_message


def test_stage_keeps_phanloai():
    FoxiaCatalogSyncService(FakeFoxiaClient(_full_dataset())).sync_one(_stage_spec())

    assert FoxiaStage.objects.get(foxia_id=5).phanloai == "confirmed"


# ── helper ───────────────────────────────────────────────────────────────────


def _spec(resource: str):
    from apps.foxia.domain.catalog_specs import ORDERED_SPECS

    return next(spec for spec in ORDERED_SPECS if spec.resource == resource)


def _branch_spec():
    return _spec("congtycon")


def _users_spec():
    return _spec("res.users")


def _stage_spec():
    return _spec("giaidoandonhang")
