"""Bộ lý do bom 7 nhóm (2026-10-01): toàn vẹn catalog, data migration remap mã cũ,
và ghi chú chi tiết (`bomb_reason_text`) không còn bị ghi đè bằng nhãn loại."""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.crm_catalog import (
    BOMB_REASON_GROUP_LABELS,
    BOMB_REASON_GROUP_SEED,
    BOMB_REASON_TYPE_GROUP_MAP,
    BOMB_REASON_TYPE_SEED,
)
from apps.orders.models import Order, OrderAssessment

remap_migration = importlib.import_module("apps.orders.migrations.0149_remap_bomb_reason_taxonomy_v2")


# ── catalog ──────────────────────────────────────────────────────────────────


def test_catalog_co_7_nhom_dung_thu_tu():
    assert [g["name_vi"] for g in sorted(BOMB_REASON_GROUP_SEED, key=lambda g: g["sort_order"])] == [
        "Khách hàng",
        "DVVC / Giao hàng",
        "Xử lý đơn (lỗi sale / vận đơn)",
        "Lỗi đóng gói",
        "Kỳ vọng khách hàng về sản phẩm",
        "Quảng cáo / Thông tin sản phẩm",
        "Sản phẩm / Chất lượng",
    ]


def test_moi_loai_thuoc_mot_nhom_hop_le_va_ma_khong_trung():
    codes = [t["code"] for t in BOMB_REASON_TYPE_SEED]
    assert len(codes) == len(set(codes))
    assert {t["group_code"] for t in BOMB_REASON_TYPE_SEED} <= set(BOMB_REASON_GROUP_LABELS)
    active_counts = {}
    for t in BOMB_REASON_TYPE_SEED:
        if t.get("is_active", True):
            active_counts[t["group_code"]] = active_counts.get(t["group_code"], 0) + 1
    assert active_counts == {
        "customer": 8,
        "shipping_unit": 7,
        "order_processing": 7,
        "packaging": 6,
        "product_expectation": 8,
        "advertising_info": 4,
        "product_quality": 5,
    }


def test_migration_type_group_khop_catalog():
    """Mapping đóng băng trong migration phải khớp catalog tại thời điểm tạo."""
    assert remap_migration.TYPE_GROUP == BOMB_REASON_TYPE_GROUP_MAP
    assert {new for new, _ in remap_migration.TYPE_REMAP.values()} <= set(BOMB_REASON_TYPE_GROUP_MAP)
    assert not set(remap_migration.TYPE_REMAP) & set(BOMB_REASON_TYPE_GROUP_MAP)


# ── data migration ───────────────────────────────────────────────────────────


@pytest.fixture
def make_assessment(db):
    channel = Channel.objects.create(code="bomb-taxonomy", name="Bomb taxonomy")
    customer = Customer.objects.create(full_name="Khach bom", phone_raw="0909000111", phone_normalized="0909000111")

    def _make(**fields):
        order = Order.objects.create(channel=channel, customer=customer, name="X", phone="0909000111")
        return OrderAssessment.objects.create(order=order, **fields)

    return _make


def test_remap_doi_ma_cu_va_giu_nhan_cu_lam_ghi_chu(make_assessment):
    empty_note = make_assessment(bomb_reason_group_code="customer", bomb_reason_type_code="found_cheaper_elsewhere")
    with_note = make_assessment(
        bomb_reason_group_code="trust_information",
        bomb_reason_type_code="refuse_china_need_japan",
        bomb_reason_text="khách đòi hàng Nhật",
    )

    remap_migration.remap_bomb_reasons(django_apps, None)

    empty_note.refresh_from_db()
    with_note.refresh_from_db()
    assert (empty_note.bomb_reason_group_code, empty_note.bomb_reason_type_code) == (
        "product_expectation",
        "price_expectation",
    )
    assert empty_note.bomb_reason_text == "Thấy giá chỗ khác rẻ hơn"
    assert (with_note.bomb_reason_group_code, with_note.bomb_reason_type_code) == (
        "product_expectation",
        "origin_expectation",
    )
    assert with_note.bomb_reason_text == "khách đòi hàng Nhật"


def test_remap_doi_nhom_cho_ma_giu_nguyen_va_xoa_nhom_cu_mo_coi(make_assessment):
    regrouped = make_assessment(bomb_reason_group_code="finance", bomb_reason_type_code="insufficient_cash_on_delivery")
    unchanged = make_assessment(bomb_reason_group_code="customer", bomb_reason_type_code="no_answer")
    orphan_group = make_assessment(bomb_reason_group_code="trust_information")
    legacy = make_assessment(bomb_reason_group_code="customer", bomb_reason_type_code="other_reason")

    remap_migration.remap_bomb_reasons(django_apps, None)

    for row in (regrouped, unchanged, orphan_group, legacy):
        row.refresh_from_db()
    assert regrouped.bomb_reason_group_code == "customer"
    assert (unchanged.bomb_reason_group_code, unchanged.bomb_reason_type_code) == ("customer", "no_answer")
    assert unchanged.bomb_reason_text == ""
    assert orphan_group.bomb_reason_group_code is None
    assert (legacy.bomb_reason_group_code, legacy.bomb_reason_type_code) == ("customer", "other_reason")


# ── ghi chú chi tiết ─────────────────────────────────────────────────────────


def test_doi_loai_khong_ghi_de_ghi_chu(make_assessment):
    assessment = make_assessment(
        bomb_reason_group_code="customer",
        bomb_reason_type_code="no_answer",
        bomb_reason_text="gọi 3 lần không nghe",
    )

    changed = OrderServiceHelpers._apply_assessment_payload(
        assessment, {"bomb_reason_group_code": "shipping_unit", "bomb_reason_type_code": "parcel_lost"}
    )

    assert set(changed) == {"bomb_reason_group_code", "bomb_reason_type_code"}
    assert assessment.bomb_reason_text == "gọi 3 lần không nghe"
