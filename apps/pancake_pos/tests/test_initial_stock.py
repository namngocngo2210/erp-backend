"""Đẩy tồn đầu kỳ Hàng hoá CRM → Pancake (two-way-sync §7 A1): kế hoạch thuần, SQL
đọc tồn (chỉ `sellable`, không N+1), dry-run không ghi, lô ≤ 100, âm → 0, thiếu liên
kết, idempotent, lô lỗi cô lập, API (dry-run bắt buộc ≤ 30 phút) và lệnh."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.inventory.models import StockItem
from apps.pancake_pos.application.services.initial_stock_service import InitialStockPushService
from apps.pancake_pos.domain.entities import CrmStockRow, LinkedVariation
from apps.pancake_pos.domain.initial_stock import WarehousePair, batches, plan_initial_stock
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.infrastructure.stock_reader import SqlStockReader, SqlWarehouseReader
from apps.pancake_pos.models import (
    LinkStatus,
    PancakePosProductLink,
    PancakePosSyncRun,
    PancakePosVariationLink,
    PancakePosWarehouseLink,
    SyncRunMode,
)
from apps.pancake_pos.tests.fakes import FakePancake
from apps.sysconfig import override_sysconfig
from apps.users.tests.helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse

VIEWS = "apps.pancake_pos.transport.views"


# ── kế hoạch (thuần) ────────────────────────────────────────────────────────
def test_ke_hoach_thuan():
    pairs = [WarehousePair(1, "ĐM1", "pw1"), WarehousePair(2, "ĐM2", "pw2")]
    stock = [
        CrmStockRow(1, 10, "A", 10), CrmStockRow(2, 10, "A", -2),
        CrmStockRow(2, 11, "B", 3), CrmStockRow(1, 12, "C", 3),
        CrmStockRow(9, 10, "A", 99),  # kho chưa ghép → bỏ
    ]
    linked = [LinkedVariation(10, "A", "vA"), LinkedVariation(11, "B", "vB"), LinkedVariation(13, "D", "vD")]
    pancake = {"vA": {"pw1": 10}, "vD": {"pw1": 4}}  # vB không đọc được → chưa biết
    plan = plan_initial_stock(warehouses=pairs, stock=stock, linked=linked, pancake_quantities=pancake)

    assert len(plan.lines) == 6
    writes = {(line.sku, line.warehouse_id): (line.push_qty, line.pancake_qty) for line in plan.to_write}
    assert writes == {("B", 1): (0, None), ("B", 2): (3, None), ("D", 1): (0, 4)}
    [negative] = plan.negatives
    assert (negative.sku, negative.crm_qty, negative.push_qty, negative.needs_write) == ("A", -2, 0, False)
    assert [(m.sku, m.quantities) for m in plan.missing_links] == [("C", {"ĐM1": 3})]
    assert plan.unknown_on_pancake == 2
    assert [line.as_entry() for line in plan.to_write if line.sku == "D"] == [
        {"variation_id": "vD", "warehouse_id": "pw1", "remain_quantity": 0}
    ]


def test_chia_lo():
    assert [len(b) for b in batches(list(range(250)), 100)] == [100, 100, 50]
    assert batches([], 100) == []


# ── fixture DB ──────────────────────────────────────────────────────────────
@pytest.fixture
def env(db):
    dm1 = Warehouse.objects.create(code="DM1", name="Đại Mỗ 1")
    dm2 = Warehouse.objects.create(code="DM2", name="Đại Mỗ 2")
    vp = Warehouse.objects.create(code="VP", name="Văn phòng")
    PancakePosWarehouseLink.objects.create(warehouse=dm1, pancake_warehouse_id="pw1", pancake_warehouse_name="P-ĐM1")
    PancakePosWarehouseLink.objects.create(warehouse=dm2, pancake_warehouse_id="pw2", pancake_warehouse_name="P-ĐM2")
    a, b, c, d = (Product.objects.create(sku=s, name=f"SP {s}") for s in ("A01", "B01", "C01", "D01"))
    group = PancakePosProductLink.objects.create(group_key="G", pancake_product_id="pp")
    for product, vid in ((a, "vA"), (b, "vB"), (d, "vD")):
        PancakePosVariationLink.objects.create(
            product=product, product_link=group, pancake_variation_id=vid, status=LinkStatus.SYNCED
        )
    for wh, product, state, qty in (
        (dm1, a, "sellable", 10), (dm1, a, "defect", 5), (dm2, a, "sellable", -2), (vp, a, "sellable", 7),
        (dm2, b, "sellable", 3), (dm1, c, "sellable", 3),
    ):
        StockItem.objects.create(warehouse=wh, product=product, state=state, quantity=qty)
    pancake = FakePancake()
    pancake.seed("G", [{"id": "vA", "custom_id": "A01"}, {"id": "vB", "custom_id": "B01"},
                       {"id": "vD", "custom_id": "D01"}], product_id="pp")
    pancake.stock = {("vA", "pw1"): 10, ("vD", "pw1"): 4}
    return {"dm1": dm1, "dm2": dm2, "vp": vp, "pancake": pancake, "products": (a, b, c, d)}


def _service(pancake: FakePancake, **kw) -> InitialStockPushService:
    return InitialStockPushService(
        gateway=pancake, stock_reader=SqlStockReader(), warehouse_reader=SqlWarehouseReader(), **kw
    )


# ── reader SQL ──────────────────────────────────────────────────────────────
def test_reader_chi_doc_sellable_kho_chon_va_so_query_co_dinh(env):
    reader = SqlStockReader()
    wh_ids = [env["dm1"].id, env["dm2"].id]

    def count_queries() -> int:
        with CaptureQueriesContext(connection) as ctx:
            reader.sellable_stock(warehouse_ids=wh_ids)
            reader.linked_variations()
        return len(ctx.captured_queries)

    before = count_queries()
    extra = Product.objects.bulk_create([Product(sku=f"N{i}", name="x") for i in range(20)])
    StockItem.objects.bulk_create([StockItem(warehouse=env["dm1"], product=p, quantity=1) for p in extra])
    assert count_queries() == before  # không N+1 theo số SKU
    rows = reader.sellable_stock(warehouse_ids=wh_ids)
    linked = reader.linked_variations()
    rows = [r for r in rows if not r.sku.startswith("N")]
    assert {(r.sku, r.warehouse_id, r.quantity) for r in rows} == {
        ("A01", env["dm1"].id, 10), ("A01", env["dm2"].id, -2), ("B01", env["dm2"].id, 3), ("C01", env["dm1"].id, 3),
    }
    assert {v.sku for v in linked} == {"A01", "B01", "D01"}
    assert {r.sku for r in reader.sellable_stock(warehouse_ids=[env["dm1"].id], skus=["a01"])} == {"A01"}
    assert reader.sellable_stock(warehouse_ids=[]) == []


# ── service ─────────────────────────────────────────────────────────────────
def test_dry_run_khong_goi_ghi_va_bao_cao_day_du(env):
    pancake = env["pancake"]
    report = _service(pancake).run(dry_run=True)

    assert not [c for c in pancake.calls if c[0] == "update_quantity"]
    t = report.totals
    assert (t["lines"], t["to_write"], t["negative"], t["missing_link"], t["written"]) == (6, 2, 1, 1, 0)
    assert {(d["sku"], d["warehouse_name"], d["push_qty"], d["pancake_qty"]) for d in report.diffs} == {
        ("B01", "Đại Mỗ 2", 3, 0), ("D01", "Đại Mỗ 1", 0, 4),
    }
    assert report.negatives == [{"sku": "A01", "warehouse_id": env["dm2"].id, "warehouse_name": "Đại Mỗ 2",
                                 "crm_qty": -2}]
    assert report.missing_links[0]["sku"] == "C01" and report.missing_links[0]["quantity"] == 3
    run = PancakePosSyncRun.objects.get(id=report.run_id)
    assert run.mode == SyncRunMode.INITIAL_STOCK_DRY_RUN and run.finished_at is not None
    assert (run.updated, run.skipped) == (2, 1)
    assert run.report["kind"] == "initial_stock" and run.report["scope"] == {"warehouse_ids": None, "skus": None}


def test_ghi_that_roi_chay_lai_khong_ghi_gi_idempotent(env):
    pancake = env["pancake"]
    first = _service(pancake).run(dry_run=False)
    [call] = [c for c in pancake.calls if c[0] == "update_quantity"]
    assert call[1]["actual"] is True
    assert sorted((e["variation_id"], e["warehouse_id"], e["remain_quantity"]) for e in call[1]["entries"]) == [
        ("vB", "pw2", 3), ("vD", "pw1", 0),
    ]
    assert first.totals["written"] == 2
    assert PancakePosSyncRun.objects.get(id=first.run_id).mode == SyncRunMode.INITIAL_STOCK

    pancake.calls.clear()
    second = _service(pancake).run(dry_run=False)
    assert second.totals["to_write"] == 0 and second.totals["unchanged"] == 6
    assert not [c for c in pancake.calls if c[0] == "update_quantity"]


def test_loc_theo_kho_va_sku(env):
    pancake = env["pancake"]
    report = _service(pancake).run(dry_run=True, warehouse_ids=[env["dm1"].id], skus=["d01"])
    assert [(d["sku"], d["warehouse_name"]) for d in report.diffs] == [("D01", "Đại Mỗ 1")]
    assert report.scope == {"warehouse_ids": [env["dm1"].id], "skus": ["D01"]}
    assert [w["pancake_warehouse_id"] for w in report.warehouses] == ["pw1"]
    assert ("iter", "D01") in pancake.calls  # có SKU → tìm theo SKU, không duyệt cả shop


def _bulk_links(count: int) -> None:
    group = PancakePosProductLink.objects.get(group_key="G")
    products = Product.objects.bulk_create([Product(sku=f"BULK{i:03d}", name="x") for i in range(count)])
    PancakePosVariationLink.objects.bulk_create([
        PancakePosVariationLink(product=p, product_link=group, pancake_variation_id=f"vb{i}", status=LinkStatus.SYNCED)
        for i, p in enumerate(products)
    ])


def test_lo_mac_dinh_20_tran_100(env):
    pancake = env["pancake"]
    _bulk_links(45)
    # 45 mẫu mã lạ (chưa biết số Pancake) + D01@ĐM1 (0 vs 4); B01@ĐM1 0 vs 0 không ghi
    report = _service(pancake).run(dry_run=False, warehouse_ids=[env["dm1"].id])
    calls = [c for c in pancake.calls if c[0] == "update_quantity"]
    assert [len(c[1]["entries"]) for c in calls] == [20, 20, 6]
    assert (report.totals["batches"], report.totals["written"], report.totals["failed"]) == (3, 46, 0)
    assert InitialStockPushService(
        gateway=pancake, stock_reader=SqlStockReader(), warehouse_reader=SqlWarehouseReader(), batch_size=500
    )._batch_size == 100


def test_lo_500_chia_nho_gui_tung_dong(env):
    """Như API thật: lô > N dòng trả 500 → gửi lại từng dòng; lô sau vẫn chạy."""
    pancake = env["pancake"]
    _bulk_links(25)
    pancake.max_quantity_batch = 5
    report = _service(pancake).run(dry_run=False, warehouse_ids=[env["dm1"].id])

    sizes = [len(c[1]["entries"]) for c in pancake.calls if c[0] == "update_quantity"]
    assert sizes == [20, *([1] * 20), 6, *([1] * 6)]
    assert report.totals["split_batches"] == 2
    assert (report.totals["written"], report.totals["failed"]) == (26, 0)
    assert report.errors == []
    assert pancake.stock[("vD", "pw1")] == 0


def test_dong_loi_trong_results_va_dong_don_loi(env):
    pancake = env["pancake"]
    pancake.reject_variations = {"vB"}
    report = _service(pancake).run(dry_run=False)
    assert report.totals["written"] == 1 and report.totals["failed"] == 1
    [err] = report.line_errors
    assert (err["sku"], err["warehouse_name"], err["message"]) == ("B01", "Đại Mỗ 2", "bị từ chối")
    run = PancakePosSyncRun.objects.get(id=report.run_id)
    assert (run.updated, run.errors) == (1, 1) and run.report["line_errors"][0]["sku"] == "B01"

    # Dòng đơn lỗi cả request (sau khi đã chia) → lỗi dòng, không dừng dòng khác
    pancake.reject_variations = set()
    pancake.fail_quantity_batches = {pancake._quantity_calls + 1}
    again = _service(pancake).run(dry_run=False)
    assert again.totals["to_write"] == 1 and again.totals["failed"] == 1
    assert again.errors[0]["size"] == 1 and "500" in again.line_errors[0]["message"]


def test_chua_ghep_kho_la_fatal(env):
    PancakePosWarehouseLink.objects.all().delete()
    with pytest.raises(PancakePosError, match="Chưa ghép kho"):
        _service(env["pancake"]).run(dry_run=True)
    run = PancakePosSyncRun.objects.get()
    assert run.finished_at is not None and "Chưa ghép kho" in run.report["fatal"]


def test_kho_chon_chua_ghep_la_fatal(env):
    with pytest.raises(PancakePosError):
        _service(env["pancake"]).run(dry_run=True, warehouse_ids=[env["vp"].id])


# ── API ─────────────────────────────────────────────────────────────────────
def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def editor(db):
    return _client(make_user_with_permissions("is_editor", ["pancake-pos:view", "pancake-pos:edit"]))


def _dry_run(minutes_ago: int = 1, *, fatal: str = "", scope=None) -> PancakePosSyncRun:
    report = {"kind": "initial_stock", "dry_run": True,
              "scope": scope or {"warehouse_ids": [7], "skus": ["A01"]}}
    if fatal:
        report["fatal"] = fatal
    return PancakePosSyncRun.objects.create(
        mode=SyncRunMode.INITIAL_STOCK_DRY_RUN, finished_at=timezone.now() - timedelta(minutes=minutes_ago),
        report=report,
    )


def test_api_dry_run_xep_hang(editor):
    with patch(f"{VIEWS}.run_initial_stock") as task:
        res = editor.post("/api/pancake-pos/initial-stock",
                          {"mode": "dry_run", "warehouse_ids": [3], "skus": ["A01"]}, format="json")
    assert res.status_code == 202
    run = PancakePosSyncRun.objects.get(id=res.json()["run_id"])
    assert run.mode == SyncRunMode.INITIAL_STOCK_DRY_RUN
    task.delay.assert_called_once_with(run.id, True, [3], ["A01"], run.triggered_by_id)


def test_api_apply_dung_pham_vi_cua_dry_run(editor):
    dry = _dry_run()
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(f"{VIEWS}.run_initial_stock") as task:
        res = editor.post("/api/pancake-pos/initial-stock",
                          {"mode": "apply", "dry_run_id": dry.id, "skus": ["KHAC"]}, format="json")
    assert res.status_code == 202, res.content
    run_id = res.json()["run_id"]
    assert PancakePosSyncRun.objects.get(id=run_id).mode == SyncRunMode.INITIAL_STOCK
    task.delay.assert_called_once()
    assert task.delay.call_args.args[1:4] == (False, [7], ["A01"])


@pytest.mark.parametrize(
    "setup",
    ["none", "stale", "fatal", "not_latest"],
)
def test_api_apply_thieu_dry_run_hop_le_409(editor, setup):
    dry_id = 999_999
    if setup == "stale":
        dry_id = _dry_run(31).id
    elif setup == "fatal":
        dry_id = _dry_run(1, fatal="HTTP 401").id
    elif setup == "not_latest":
        dry_id = _dry_run(5).id
        _dry_run(1)
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(f"{VIEWS}.run_initial_stock") as task:
        res = editor.post("/api/pancake-pos/initial-stock", {"mode": "apply", "dry_run_id": dry_id}, format="json")
    assert res.status_code == 409
    task.delay.assert_not_called()


def test_api_apply_khi_tat_co_409_va_thieu_id_400(editor):
    dry = _dry_run()
    with override_sysconfig(PANCAKE_POS_ENABLED=False):
        assert editor.post("/api/pancake-pos/initial-stock", {"mode": "apply", "dry_run_id": dry.id},
                           format="json").status_code == 409
    assert editor.post("/api/pancake-pos/initial-stock", {"mode": "apply"}, format="json").status_code == 400


def test_api_khong_chay_2_luot_song_song(editor):
    PancakePosSyncRun.objects.create(mode=SyncRunMode.INITIAL_STOCK_DRY_RUN)
    with patch(f"{VIEWS}.run_initial_stock") as task:
        res = editor.post("/api/pancake-pos/initial-stock", {"mode": "dry_run"}, format="json")
    assert res.status_code == 409
    task.delay.assert_not_called()


def test_api_quyen(db):
    viewer = _client(make_user_with_permissions("is_viewer", ["pancake-pos:view"]))
    stranger = _client(make_user_with_permissions("is_stranger", ["orders:view"]))
    assert viewer.post("/api/pancake-pos/initial-stock", {"mode": "dry_run"}, format="json").status_code == 403
    assert viewer.get("/api/pancake-pos/initial-stock/status").status_code == 200
    assert stranger.get("/api/pancake-pos/initial-stock/status").status_code == 403


def test_api_trang_thai(editor):
    res = editor.get("/api/pancake-pos/initial-stock/status").json()
    assert res["latest_dry_run"] is None and res["apply_allowed"] is False and res["apply_deadline"] is None
    dry = _dry_run(10)
    res = editor.get("/api/pancake-pos/initial-stock/status").json()
    assert res["latest_dry_run"]["id"] == dry.id and res["apply_allowed"] is True and res["apply_deadline"]
    _dry_run(40).delete()  # không ảnh hưởng
    PancakePosSyncRun.objects.filter(id=dry.id).update(finished_at=timezone.now() - timedelta(minutes=45))
    assert editor.get("/api/pancake-pos/initial-stock/status").json()["apply_allowed"] is False


def test_api_danh_sach_luot_loc_nhieu_mode(editor):
    _dry_run()
    PancakePosSyncRun.objects.create(mode=SyncRunMode.FULL, finished_at=timezone.now())
    res = editor.get("/api/pancake-pos/sync-runs?mode=initial_stock_dry,initial_stock")
    assert [r["mode"] for r in res.json()["items"]] == ["initial_stock_dry"]
    assert editor.get("/api/pancake-pos/sync-runs?mode=abc").status_code == 400


# ── lệnh ────────────────────────────────────────────────────────────────────
FACTORY = "apps.pancake_pos.management.commands.pancake_pos_initial_stock.build_initial_stock_service"


def test_lenh_dry_run(env):
    out = StringIO()
    with patch(FACTORY, return_value=_service(env["pancake"])) as factory:
        call_command("pancake_pos_initial_stock", "--dry-run", "--warehouse", "dm1", "--sku", "D01", stdout=out)
    factory.assert_called_once_with(dry_run=True)
    text = out.getvalue()
    assert "cần ghi 1" in text and "D01 @ Đại Mỗ 1" in text and "dry-run" in text
    assert not [c for c in env["pancake"].calls if c[0] == "update_quantity"]


def test_lenh_kho_khong_ton_tai(env):
    with pytest.raises(CommandError, match="Không có kho"):
        call_command("pancake_pos_initial_stock", "--dry-run", "--warehouse", "XYZ")


def test_lenh_ghi_that_can_bat_co(env):
    with (
        override_sysconfig(PANCAKE_POS_ENABLED=False, PANCAKE_POS_API_KEY="k", PANCAKE_POS_SHOP_ID=1),
        pytest.raises(CommandError, match="PANCAKE_POS_ENABLED"),
    ):
        call_command("pancake_pos_initial_stock")
