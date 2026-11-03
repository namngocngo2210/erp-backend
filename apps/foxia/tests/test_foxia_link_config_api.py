"""API trang Cấu hình liên kết Foxia. Toàn bộ CHỈ superuser.

Bảng này quyết định đơn của ai chảy vào nguồn nào bên đối tác — sai một ô là
gán nhầm doanh số marketing, nên đường ghi không mở cho role thường.
"""

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkKind
from apps.users.tests.helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db

BASE = "/api/foxia/link-config"


# ⚠️ `resolve_effective_permissions().is_superuser` chỉ bật khi user có role
# `is_system` (apps/users/rbac.py) — nó KHÔNG đọc cờ `is_superuser` của Django.
# `User.objects.create_superuser(...)` ở đây sẽ nhận 403. Dùng helper chuẩn của repo.
@pytest.fixture
def su_client():
    client = APIClient()
    client.force_authenticate(user=make_admin("foxia_link_admin"))
    return client


@pytest.fixture
def normal_client():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("sale", ["orders"]))
    return client


def _grid():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    row = FoxiaLinkChannel.objects.create(channel=channel)
    cell = FoxiaLinkCell.objects.create(link_channel=row, kind="landing", foxia_source_id=1234)
    return channel, row, cell


def test_get_tra_ve_hang_cot_va_o(su_client):
    channel, row, _ = _grid()

    data = su_client.get(BASE).json()

    assert data["channels"] == [
        {"id": row.id, "channel_id": channel.id, "code": "kenh-a", "name": "Kênh A", "sort_order": 0}
    ]
    # Thứ tự cột cố định do BE quyết — FE render đúng thứ tự này.
    assert [k["value"] for k in data["kinds"]] == ["landing", "messenger", "hotline", "zalo", "resale"]
    assert data["kinds"][0] == {"value": "landing", "label": "Landing"}
    assert data["cells"] == [
        {"link_channel_id": row.id, "kind": "landing", "foxia_source_id": 1234, "source_name": ""}
    ]


def test_get_kem_ten_nguon_khi_tra_duoc(su_client):
    from apps.foxia.models import FoxiaSource

    _, row, _ = _grid()
    FoxiaSource.objects.create(foxia_id=1234, name="Nguồn landing A")

    data = su_client.get(BASE).json()

    assert data["cells"][0]["source_name"] == "Nguồn landing A"


def test_them_kenh_trung_la_idempotent(su_client):
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")

    su_client.post(f"{BASE}/channels", {"channel_ids": [channel.id]}, format="json")
    su_client.post(f"{BASE}/channels", {"channel_ids": [channel.id]}, format="json")

    assert FoxiaLinkChannel.objects.filter(channel=channel).count() == 1


def test_them_kenh_trung_ngay_trong_mot_payload_khong_vo(su_client):
    """Id lặp lại NGAY TRONG một mảng gửi lên — không phải chỉ lặp giữa hai lần
    gọi. `channel` là OneToOneField nên hai bản ghi cùng giá trị sẽ vỡ ràng
    buộc unique nếu code không tự khử trùng lặp trước khi bulk_create."""
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")

    resp = su_client.post(f"{BASE}/channels", {"channel_ids": [channel.id, channel.id]}, format="json")

    assert resp.status_code == 200
    assert FoxiaLinkChannel.objects.filter(channel=channel).count() == 1


def test_xoa_hang_xoa_o_cua_hang_do_va_tra_dung_so_luong(su_client):
    _, row, cell = _grid()
    FoxiaLinkCell.objects.create(link_channel=row, kind="zalo", foxia_source_id=1235)
    other_channel = Channel.objects.create(code="kenh-b", name="Kênh B")
    other_row = FoxiaLinkChannel.objects.create(channel=other_channel)
    other_cell = FoxiaLinkCell.objects.create(link_channel=other_row, kind="landing", foxia_source_id=5678)

    resp = su_client.delete(f"{BASE}/channels/{row.id}")

    assert resp.json() == {"deleted_cells": 2}
    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.filter(id=other_cell.id).exists()


def test_put_cells_upsert_va_xoa_trong_mot_lo(su_client):
    _, row, cell = _grid()

    resp = su_client.put(
        f"{BASE}/cells",
        {
            "cells": [
                {"link_channel_id": row.id, "kind": "landing", "foxia_source_id": None},
                {"link_channel_id": row.id, "kind": "resale", "foxia_source_id": 4321},
            ]
        },
        format="json",
    )

    assert resp.status_code == 200
    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.get(link_channel=row, kind="resale").foxia_source_id == 4321


def test_put_cells_ghi_de_o_da_co(su_client):
    _, row, cell = _grid()

    resp = su_client.put(
        f"{BASE}/cells",
        {"cells": [{"link_channel_id": row.id, "kind": "landing", "foxia_source_id": 9999}]},
        format="json",
    )

    assert resp.status_code == 200
    cell.refresh_from_db()
    assert cell.foxia_source_id == 9999
    assert FoxiaLinkCell.objects.filter(link_channel=row).count() == 1


def test_put_cells_mot_o_sai_thi_ca_lo_khong_ghi(su_client):
    _, row, cell = _grid()

    resp = su_client.put(
        f"{BASE}/cells",
        {
            "cells": [
                {"link_channel_id": row.id, "kind": "hotline", "foxia_source_id": 4321},
                {"link_channel_id": row.id, "kind": "landing", "foxia_source_id": 0},
            ]
        },
        format="json",
    )

    assert resp.status_code == 400
    assert not FoxiaLinkCell.objects.filter(link_channel=row, kind="hotline").exists()
    assert FoxiaLinkCell.objects.get(id=cell.id).foxia_source_id == 1234


def test_put_cells_loai_nguon_la_thi_400(su_client):
    _, row, _ = _grid()

    resp = su_client.put(
        f"{BASE}/cells",
        {"cells": [{"link_channel_id": row.id, "kind": "sku", "foxia_source_id": 1}]},
        format="json",
    )

    assert resp.status_code == 400
    assert set(FoxiaLinkCell.objects.values_list("kind", flat=True)) <= set(FoxiaLinkKind.values)


def test_put_cells_kenh_khong_con_thi_400(su_client):
    resp = su_client.put(
        f"{BASE}/cells",
        {"cells": [{"link_channel_id": 999999, "kind": "landing", "foxia_source_id": 1}]},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "", None),
        ("post", "/channels", {"channel_ids": []}),
        ("delete", "/channels/1", None),
        ("put", "/cells", {"cells": []}),
    ],
)
def test_nguoi_dung_thuong_bi_chan(normal_client, method, path, payload):
    call = getattr(normal_client, method)
    resp = call(f"{BASE}{path}", payload, format="json") if payload is not None else call(f"{BASE}{path}")
    assert resp.status_code == 403
