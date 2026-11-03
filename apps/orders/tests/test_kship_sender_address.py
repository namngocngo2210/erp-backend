"""Địa chỉ người gửi KShip — gộp 2026-08-05: model seed, resolver, API CRUD,
và luồng sender_address_id qua job giao vận.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.application.services.kship_sender_service import (
    default_sender_code_for_order,
    resolve_default_sender_address,
    resolve_kship_sender_info,
)
from apps.orders.models import KshipSenderAddress, Order, OrderKiotVietJob
from apps.users.tests.helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db

DELIVERY_URL = "/api/orders/kship-delivery"
LIST_URL = "/api/orders/kship-sender-addresses"

# Seed migration: (location_id, ward_id) của 2 bản ghi hardcode cũ.
SEED_VAN_QUAN = (238, 260)
SEED_DAI_MO = (255, 209)


# ---------------------------------------------------------------------------
# Bảng địa chỉ người gửi KShip: seed migration phải tạo đúng 2 bản ghi hardcode cũ.
# (Gộp từ test_kship_sender_address_model.py.)
# ---------------------------------------------------------------------------


def test_seed_rows_match_legacy_hardcode():
    van_quan = KshipSenderAddress.objects.get(code="van-quan")
    assert (van_quan.location_id, van_quan.ward_id) == SEED_VAN_QUAN
    assert van_quan.address == "số 60 Đại Mỗ"
    assert van_quan.mobile == "+84 359 529 280"

    dai_mo = KshipSenderAddress.objects.get(code="dai-mo")
    assert (dai_mo.location_id, dai_mo.ward_id) == SEED_DAI_MO


def test_job_has_sender_address_id_field():
    field = OrderKiotVietJob._meta.get_field("kship_sender_address_id")
    assert field.null is True


def test_two_rows_without_code_do_not_collide():
    a = KshipSenderAddress.objects.create(
        name="Kho A", location_id=1, ward_id=1, address="1 A", mobile="0900"
    )
    b = KshipSenderAddress.objects.create(
        name="Kho B", location_id=2, ward_id=2, address="2 B", mobile="0901"
    )
    assert a.code is None and b.code is None


def test_empty_string_code_normalized_to_null():
    row = KshipSenderAddress.objects.create(
        name="Kho C", code="", location_id=3, ward_id=3, address="3 C", mobile="0902"
    )
    row.refresh_from_db()
    assert row.code is None


# ---------------------------------------------------------------------------
# Resolver sender KShip: id được chọn → seed theo rule kênh/branch → hằng số fallback.
# (Gộp từ test_kship_sender_resolver.py.)
# ---------------------------------------------------------------------------


def _order(channel_old: bool, branch_id: str):
    return SimpleNamespace(
        id=1,
        branch_id=branch_id,
        kiotviet_channel=SimpleNamespace(old=channel_old),
    )


def test_rule_new_channel_is_van_quan():
    assert default_sender_code_for_order(_order(channel_old=False, branch_id="123")) == "van-quan"


def test_rule_legacy_dai_mo_branch_is_van_quan():
    assert default_sender_code_for_order(_order(channel_old=True, branch_id="1000000477")) == "van-quan"


def test_rule_old_channel_branch_89084_is_dai_mo():
    assert default_sender_code_for_order(_order(channel_old=True, branch_id="89084")) == "dai-mo"


def test_explicit_id_wins():
    row = KshipSenderAddress.objects.create(
        name="Kho test", location_id=999, ward_id=888, address="1 Test", mobile="0900000000"
    )
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"), row.id)
    assert info == {"location_id": 999, "ward_id": 888, "address": "1 Test", "mobile": "0900000000"}


def test_deleted_id_falls_back_to_seed_rule():
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"), 999999)
    assert (info["location_id"], info["ward_id"]) == SEED_VAN_QUAN


def test_no_id_uses_seed_by_rule():
    info = resolve_kship_sender_info(_order(channel_old=True, branch_id="89084"))
    assert (info["location_id"], info["ward_id"]) == SEED_DAI_MO


def test_seed_deleted_falls_back_to_constants():
    KshipSenderAddress.objects.filter(code="van-quan").delete()
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"))
    assert (info["location_id"], info["ward_id"]) == SEED_VAN_QUAN
    assert info["address"] == "số 60 Đại Mỗ"


def test_resolve_default_row():
    row = resolve_default_sender_address(_order(channel_old=False, branch_id="1"))
    assert row is not None and row.code == "van-quan"


# ---------------------------------------------------------------------------
# CRUD địa chỉ gửi KShip: đọc = quyền orders, sửa = kiotviet:edit, GET trả default_id.
# (Gộp từ test_kship_sender_address_api.py.)
# ---------------------------------------------------------------------------


@pytest.fixture
def viewer():
    return make_user_with_permissions("kship-addr-viewer", ["orders:view"])


@pytest.fixture
def editor():
    return make_user_with_permissions("kship-addr-editor", ["orders:view", "kiotviet:edit"])


def _auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_viewer_can_list_but_not_mutate(viewer):
    client = _auth_client(viewer)
    response = client.get(LIST_URL)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert {"van-quan", "dai-mo"} <= codes

    create = client.post(
        LIST_URL,
        {"name": "Kho A", "location_id": 1, "ward_id": 2, "address": "1 A", "mobile": "0900"},
        format="json",
    )
    assert create.status_code == 403


def test_editor_can_crud(editor):
    client = _auth_client(editor)
    created = client.post(
        LIST_URL,
        {"name": "Kho A", "location_id": 10, "ward_id": 20, "address": "1 A", "mobile": "0900000000"},
        format="json",
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]
    assert created.json()["code"] is None  # code read-only, không set được qua API

    patched = client.patch(f"{LIST_URL}/{entry_id}", {"name": "Kho B"}, format="json")
    assert patched.status_code == 200
    assert patched.json()["name"] == "Kho B"

    deleted = client.delete(f"{LIST_URL}/{entry_id}")
    assert deleted.status_code == 204


def test_default_id_follows_channel_rule(viewer):
    channel = Channel.objects.create(code="kship-addr-ch", name="KShip Addr")
    kv_new = KiotVietChannel.objects.create(name="kv-new", retailer="r-new", old=False)
    customer = Customer.objects.create(
        full_name="KH",
        phone_raw="0911111111",
        phone_normalized="0911111111",
    )
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code="ORD-KSHIP-ADDR-1",
        name="KH",
        phone="0911111111",
        total_amount="0",
        kiotviet_channel=kv_new,
        branch_id="123",
    )
    client = _auth_client(viewer)
    response = client.get(f"{LIST_URL}?ids={order.id}")
    assert response.status_code == 200
    van_quan_id = KshipSenderAddress.objects.get(code="van-quan").id
    assert response.json()["default_id"] == van_quan_id


def test_default_id_null_without_ids(viewer):
    response = _auth_client(viewer).get(LIST_URL)
    assert response.json()["default_id"] is None


# ---------------------------------------------------------------------------
# sender_address_id đi từ API vào OrderKiotVietJob và tới execute_action.
# (Gộp từ test_kship_delivery_sender_address.py.)
# ---------------------------------------------------------------------------


def _make_order() -> Order:
    suffix = uuid.uuid4().hex[:8]
    channel = Channel.objects.create(code=f"kship-{suffix}", name=f"KShip {suffix}")
    kv = KiotVietChannel.objects.create(
        name=f"kv-{suffix}", retailer=f"r-{suffix}", old=False,
        username="u", password="p",
    )
    customer = Customer.objects.create(
        full_name=f"KH {suffix}",
        phone_raw="0911111111",
        phone_normalized="0911111111",
    )
    return Order.objects.create(
        channel=channel,
        customer=customer,
        order_code=f"ORD-{suffix}",
        name="KH",
        phone="0911111111",
        total_amount="0",
        kiotviet_channel=kv,
        invoice_code=f"HD-{suffix}",
    )


def _admin_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=make_admin(f"kship-sender-{uuid.uuid4().hex[:6]}"))
    return client


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_sender_address_id_saved_on_job(mock_dispatch):
    order = _make_order()
    row = KshipSenderAddress.objects.get(code="dai-mo")
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD", "sender_address_id": row.id},
        format="json",
    )
    assert response.status_code == 200, response.content
    job = OrderKiotVietJob.objects.get(order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY)
    assert job.kship_sender_address_id == row.id


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_unknown_sender_address_id_is_400(mock_dispatch):
    order = _make_order()
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD", "sender_address_id": 999999},
        format="json",
    )
    assert response.status_code == 400


@patch("apps.orders.application.services.order_kiotviet_job_service.dispatch")
def test_omitted_sender_address_id_saves_null(mock_dispatch):
    order = _make_order()
    response = _admin_client().post(
        DELIVERY_URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 200, response.content
    job = OrderKiotVietJob.objects.get(order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY)
    assert job.kship_sender_address_id is None


# Patch tại nơi class thật sự sống: từ Lô 2, `ingest_job_tasks` gọi KiotViet qua
# port chứ không import `KiotVietService` nữa. Patch thuộc tính trên class có hiệu
# lực toàn cục nên vẫn chặn đúng call đi qua adapter.
@patch("apps.kiotviet.infrastructure.services.service_actions.KiotVietService.execute_action")
def test_task_passes_sender_address_id(mock_execute):
    from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_kiotviet_job

    order = _make_order()
    row = KshipSenderAddress.objects.get(code="van-quan")
    job = OrderKiotVietJob.objects.create(
        order=order,
        action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY,
        kship_provider_code="BEST",
        kship_service_code="STD",
        kship_sender_address_id=row.id,
    )
    mock_execute.return_value = {"status": "success", "payload": {}}
    process_kiotviet_job(job_id=job.id)
    assert mock_execute.call_args.kwargs.get("sender_address_id") == row.id
