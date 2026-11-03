"""4 route API Foxia chưa có test: reports/marketing, channel-maps, channel-maps/upsert, sync-status.

Trục kiểm: cửa quyền (401 chưa đăng nhập / 403 sai section / `:edit` cho đường
ghi) + shape payload + validate input. Số liệu báo cáo đã có test riêng ở
`test_foxia_report.py` — ở đây chỉ kiểm view nối đúng vào `report_service.compute`.
"""

from datetime import datetime, timedelta, UTC
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.foxia.models import (
    FoxiaChannel,
    FoxiaChannelMap,
    FoxiaChannelMapKind,
    FoxiaConnection,
    FoxiaOrderLink,
    FoxiaSource,
    FoxiaSyncRun,
)
from apps.orders.models import Order, OrderSource, OrderStatus
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

pytestmark = pytest.mark.django_db

BASE = "/api/foxia"
REPORT_URL = f"{BASE}/reports/marketing"
MAPS_URL = f"{BASE}/channel-maps"
UPSERT_URL = f"{BASE}/channel-maps/upsert"
STATUS_URL = f"{BASE}/sync-status"


@pytest.fixture
def viewer():
    """Chỉ có `foxia-report:view` — đọc được, không ghi được."""
    client = APIClient()
    client.force_authenticate(user=make_user_with_permissions("foxia_viewer", ["foxia-report:view"]))
    return client


@pytest.fixture
def editor():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("foxia_editor", ["foxia-report"]))
    return client


@pytest.fixture
def outsider():
    """Có quyền app khác, không có section foxia-report."""
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("foxia_outsider", ["orders"]))
    return client


# ── cửa quyền chung ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method, url",
    [("get", REPORT_URL), ("get", MAPS_URL), ("put", UPSERT_URL), ("get", STATUS_URL)],
)
def test_chua_dang_nhap_401(method, url):
    response = getattr(APIClient(), method)(url, {}, format="json")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "method, url",
    [("get", REPORT_URL), ("get", MAPS_URL), ("put", UPSERT_URL), ("get", STATUS_URL)],
)
def test_khong_co_section_foxia_report_403(outsider, method, url):
    response = getattr(outsider, method)(url, {}, format="json")
    assert response.status_code == 403


def test_upsert_can_quyen_edit_view_khong_du(viewer):
    response = viewer.put(UPSERT_URL, {"kind": "source", "foxia_id": 1, "channel_id": None}, format="json")
    assert response.status_code == 403
    assert FoxiaChannelMap.objects.count() == 0


# ── reports/marketing ────────────────────────────────────────────────────────


def _mirror_order(source: FoxiaSource, *, confirmed_at):
    channel = Channel.objects.create(code=f"fx-{source.foxia_id}", name="FX")
    customer = Customer.objects.create(code=f"FX-{source.foxia_id}", full_name="[Foxia]")
    order = Order.objects.create(
        channel=channel, customer=customer, order_source=OrderSource.FOXIA, status=OrderStatus.NEW
    )
    return FoxiaOrderLink.objects.create(
        order=order,
        foxia_id=order.pk + 90000,
        source=source,
        ngayxacnhan=confirmed_at,
        amount_after_extra_discount=Decimal("500000"),
        amount_collected=Decimal("500000"),
    )


def test_marketing_report_mac_dinh_30_ngay_group_by_source(viewer):
    source = FoxiaSource.objects.create(foxia_id=77, name="FB-LP-77")
    today = timezone.now()
    _mirror_order(source, confirmed_at=today)
    # Ngoài cửa sổ 30 ngày → không đếm.
    _mirror_order(FoxiaSource.objects.create(foxia_id=78, name="Cũ"), confirmed_at=today - timedelta(days=45))

    response = viewer.get(REPORT_URL)

    assert response.status_code == 200
    data = response.json()
    assert data["group_by"] == "source"
    assert data["date_to"] == timezone.localdate().isoformat()
    assert data["date_from"] == (timezone.localdate() - timedelta(days=29)).isoformat()
    assert set(data) == {"group_by", "date_from", "date_to", "rows", "totals", "unmapped_orders"}
    # key là pk nội bộ của FoxiaSource (không phải foxia_id) — FE dùng để lọc tiếp.
    assert [(row["key"], row["label"], row["orders"]) for row in data["rows"]] == [(str(source.pk), "FB-LP-77", 1)]
    assert data["totals"]["orders"] == 1
    assert data["totals"]["revenue"] == 500000.0
    assert data["unmapped_orders"] == 0
    assert set(data["rows"][0]) >= {"cpo", "cps", "delivery_rate", "return_rate", "roas", "spend", "mapped"}


def test_marketing_report_mac_dinh_lay_hom_nay_theo_gio_vn(viewer):
    """20:00 UTC ngày 20 = 03:00 VN ngày 21: 'hôm nay' phải là ngày 21 (giờ VN),
    không phải ngày 20 (UTC của container)."""
    with patch("django.utils.timezone.now", return_value=datetime(2026, 9, 20, 20, 0, tzinfo=UTC)):
        response = viewer.get(REPORT_URL)

    assert response.status_code == 200
    assert response.json()["date_to"] == "2026-09-21"
    assert response.json()["date_from"] == "2026-08-23"


def test_marketing_report_nhan_khoang_ngay_va_group_by(viewer):
    source = FoxiaSource.objects.create(foxia_id=79, name="S79")
    _mirror_order(source, confirmed_at=timezone.now().replace(year=2026, month=8, day=15))

    response = viewer.get(REPORT_URL, {"date_from": "2026-08-01", "date_to": "2026-08-31", "group_by": "branch"})

    assert response.status_code == 200
    data = response.json()
    assert (data["group_by"], data["date_from"], data["date_to"]) == ("branch", "2026-08-01", "2026-08-31")
    # Đơn không có branch → gom vào nhóm "Chưa ánh xạ".
    assert [(row["key"], row["mapped"], row["orders"]) for row in data["rows"]] == [("unmapped", False, 1)]
    assert data["unmapped_orders"] == 1


@pytest.mark.parametrize(
    "params, detail",
    [
        ({"date_from": "2026-13-01"}, "date_from/date_to phải theo định dạng YYYY-MM-DD."),
        ({"date_to": "hom-nay"}, "date_from/date_to phải theo định dạng YYYY-MM-DD."),
        ({"date_from": "2026-09-10", "date_to": "2026-09-01"}, "date_from không được sau date_to."),
        ({"date_from": "2025-01-01", "date_to": "2026-06-01"}, "Khoảng ngày tối đa 366 ngày."),
        ({"group_by": "sale"}, "group_by không hợp lệ: sale"),
    ],
)
def test_marketing_report_input_sai_400(viewer, params, detail):
    response = viewer.get(REPORT_URL, params)

    assert response.status_code == 400
    assert response.json() == {"detail": detail}


def test_marketing_report_366_ngay_van_hop_le(viewer):
    response = viewer.get(REPORT_URL, {"date_from": "2025-06-01", "date_to": "2026-06-02"})
    assert response.status_code == 200


# ── channel-maps ─────────────────────────────────────────────────────────────


def test_list_channel_maps_tra_danh_muc_kem_channel_da_anh_xa(viewer):
    channel = Channel.objects.create(code="lp-a", name="Landing A")
    FoxiaSource.objects.create(foxia_id=2, name="Nguồn B")
    FoxiaSource.objects.create(foxia_id=1, name="Nguồn A")
    FoxiaChannel.objects.create(foxia_id=5, name="Kênh FB")
    FoxiaChannelMap.objects.create(kind=FoxiaChannelMapKind.SOURCE, foxia_id=1, channel=channel)
    # Ánh xạ cùng foxia_id nhưng kind khác — không được lẫn sang sources.
    FoxiaChannelMap.objects.create(kind=FoxiaChannelMapKind.CHANNEL, foxia_id=2, channel=channel)

    response = viewer.get(MAPS_URL)

    assert response.status_code == 200
    assert response.json() == {
        "sources": [
            {"kind": "source", "foxia_id": 1, "name": "Nguồn A", "channel_id": channel.id},
            {"kind": "source", "foxia_id": 2, "name": "Nguồn B", "channel_id": None},
        ],
        "channels": [{"kind": "channel", "foxia_id": 5, "name": "Kênh FB", "channel_id": None}],
    }


def test_list_channel_maps_rong(viewer):
    assert viewer.get(MAPS_URL).json() == {"sources": [], "channels": []}


# ── channel-maps/upsert ──────────────────────────────────────────────────────


def test_upsert_gan_roi_doi_kenh_roi_go(editor):
    channel_a = Channel.objects.create(code="a", name="A")
    channel_b = Channel.objects.create(code="b", name="B")

    created = editor.put(UPSERT_URL, {"kind": "source", "foxia_id": "9", "channel_id": channel_a.id}, format="json")
    assert created.status_code == 200
    assert created.json() == {"kind": "source", "foxia_id": 9, "channel_id": channel_a.id}
    assert FoxiaChannelMap.objects.get(kind="source", foxia_id=9).channel_id == channel_a.id

    changed = editor.put(UPSERT_URL, {"kind": "source", "foxia_id": 9, "channel_id": channel_b.id}, format="json")
    assert changed.status_code == 200
    assert FoxiaChannelMap.objects.filter(kind="source", foxia_id=9).count() == 1
    assert FoxiaChannelMap.objects.get(kind="source", foxia_id=9).channel_id == channel_b.id

    removed = editor.put(UPSERT_URL, {"kind": "source", "foxia_id": 9, "channel_id": None}, format="json")
    assert removed.status_code == 200
    assert removed.json() == {"kind": "source", "foxia_id": 9, "channel_id": None}
    assert not FoxiaChannelMap.objects.filter(kind="source", foxia_id=9).exists()


@pytest.mark.parametrize("empty", [None, "", 0])
def test_upsert_channel_id_rong_la_go_ke_ca_khi_chua_co(editor, empty):
    response = editor.put(UPSERT_URL, {"kind": "channel", "foxia_id": 3, "channel_id": empty}, format="json")

    assert response.status_code == 200
    assert response.json() == {"kind": "channel", "foxia_id": 3, "channel_id": None}
    assert FoxiaChannelMap.objects.count() == 0


@pytest.mark.parametrize(
    "payload, detail",
    [
        ({"kind": "brand", "foxia_id": 1, "channel_id": 1}, "kind phải thuộc ['source', 'channel']."),
        ({"foxia_id": 1}, "kind phải thuộc ['source', 'channel']."),
        ({"kind": "source", "foxia_id": "abc", "channel_id": 1}, "foxia_id không hợp lệ."),
        ({"kind": "source", "channel_id": 1}, "foxia_id không hợp lệ."),
    ],
)
def test_upsert_input_sai_400(editor, payload, detail):
    response = editor.put(UPSERT_URL, payload, format="json")

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert FoxiaChannelMap.objects.count() == 0


def test_upsert_kenh_khong_ton_tai_hoac_da_xoa_400(editor):
    deleted = Channel.objects.create(code="gone", name="Gone", deleted_at=timezone.now())

    missing = editor.put(UPSERT_URL, {"kind": "source", "foxia_id": 1, "channel_id": 999999}, format="json")
    soft_deleted = editor.put(UPSERT_URL, {"kind": "source", "foxia_id": 1, "channel_id": deleted.id}, format="json")

    assert missing.status_code == 400 and missing.json() == {"detail": "Kênh không tồn tại."}
    assert soft_deleted.status_code == 400 and soft_deleted.json() == {"detail": "Kênh không tồn tại."}
    assert FoxiaChannelMap.objects.count() == 0


# ── sync-status ──────────────────────────────────────────────────────────────


def test_sync_status_khong_co_connection_va_khong_co_run(viewer):
    response = viewer.get(STATUS_URL)

    assert response.status_code == 200
    assert response.json() == {"connection": None, "runs": []}


def test_sync_status_tra_connection_active_moi_nhat_va_20_run_gan_nhat(viewer):
    FoxiaConnection.objects.create(
        name="Cũ", endpoint_url="https://old/jsonrpc", database="db_old", username="u", api_key="k", is_active=False
    )
    active = FoxiaConnection.objects.create(
        name="Foxia prod",
        endpoint_url="https://crm/jsonrpc",
        database="foxia_prod",
        username="integration",
        api_key="secret",
        auth_status="ok",
        auth_error="",
    )
    for index in range(21):
        FoxiaSyncRun.objects.create(
            resource="orders" if index % 2 else "sources",
            status="success",
            fetched=index,
            created=1,
            updated=2,
            skipped=3,
        )
    # Run 0 là cũ nhất — đẩy lùi started_at để chắc chắn bị cắt khỏi top 20.
    oldest = FoxiaSyncRun.objects.order_by("id").first()
    FoxiaSyncRun.objects.filter(pk=oldest.pk).update(started_at=timezone.now() - timedelta(days=1))

    response = viewer.get(STATUS_URL)

    assert response.status_code == 200
    data = response.json()
    assert data["connection"] == {
        "name": "Foxia prod",
        "database": "foxia_prod",
        "auth_status": "ok",
        "auth_error": "",
        "uid_checked_at": None,
    }
    assert "api_key" not in str(data)
    assert active.pk  # connection active mới nhất được chọn, không phải bản is_active=False
    assert len(data["runs"]) == 20
    assert all(run["fetched"] != 0 for run in data["runs"])
    newest = data["runs"][0]
    assert set(newest) == {
        "resource", "status", "fetched", "created", "updated", "skipped",
        "error_message", "started_at", "finished_at",
    }
    assert (newest["fetched"], newest["created"], newest["updated"], newest["skipped"]) == (20, 1, 2, 3)
    assert newest["finished_at"] is None
    fetched_desc = [run["fetched"] for run in data["runs"]]
    assert fetched_desc == sorted(fetched_desc, reverse=True)
