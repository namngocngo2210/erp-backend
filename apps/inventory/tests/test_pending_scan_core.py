"""PendingShipmentScan — lõi: model constraint, tạo batch, API, bounds, task lock, purge.

Gộp từ test_pending_scan_model / _service_create / _api / _bounds / _task / _purge
(đợt tái cấu trúc test, xem docs/test-restructure-2026-08-05.md).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory import tasks
from apps.inventory.application.services import pending_scan_service as svc
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import (
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    ReturnStatus,
)
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

pytestmark = pytest.mark.django_db


# ─────────────────────── Model constraints ───────────────────────


def test_create_pending_scan_defaults(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 0
    assert scan.error == ""


def test_duplicate_pending_same_code_kind_rejected(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PendingShipmentScan.objects.create(
            code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
        )


def test_same_code_different_kind_allowed(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    assert PendingShipmentScan.objects.count() == 2


def test_same_code_allowed_after_matched(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    assert PendingShipmentScan.objects.count() == 2


# ─────────────────────── Service: create_batch ───────────────────────


def test_create_batch_creates_pending_rows(warehouse):
    results = PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id,
        codes=["SPX1", "  SPX2  "],
    )
    assert all(r["ok"] for r in results)
    codes = set(PendingShipmentScan.objects.values_list("code", flat=True))
    assert codes == {"SPX1", "SPX2"}  # code được strip


def test_create_batch_duplicate_reports_per_code(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id, codes=["SPX1"]
    )
    results = PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id, codes=["SPX1", "SPX3"]
    )
    by_code = {r["code"]: r for r in results}
    assert by_code["SPX1"] == {"code": "SPX1", "ok": False, "reason": "duplicate"}
    assert by_code["SPX3"]["ok"] is True


def test_create_batch_return_kind_keeps_return_type(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id,
        codes=["SPX9"], return_type="delivery_returned",
    )
    scan = PendingShipmentScan.objects.get(code="SPX9")
    assert scan.return_type == "delivery_returned"


def test_create_batch_dispatch_kind_ignores_return_type(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id,
        codes=["SPX8"], return_type="defect",
    )
    assert PendingShipmentScan.objects.get(code="SPX8").return_type == ""


def test_create_batch_bad_warehouse_raises():
    # NOT NULL check ngay tại INSERT (không deferrable) → IntegrityError nổ trong
    # service; không phải trùng mã nên phải được re-raise, không nuốt thành duplicate.
    with pytest.raises(IntegrityError), transaction.atomic():
        PendingScanService.create_batch(
            kind=PendingScanKind.DISPATCH, warehouse_id=None, codes=["SPXX"]
        )


# ───────── Nhập hoàn: 1 mã = 1 lần bắn sống (chặn trùng + thu hồi) ─────────
#
# Nghiệp vụ: mã đã bắn nhập hoàn rồi thì bắn lần 2 phải bị CHẶN, muốn bắn lại
# phải huỷ lần bắn trước. "Lần bắn còn sống" = scan return chưa thu hồi HOẶC
# nhóm đơn hoàn chưa huỷ — kiểm một vế là thủng một nửa (đơn tìm thấy ngay lúc
# bắn đi thẳng ra ReturnEntry, không sinh scan).


def _live_return_entry(warehouse, code: str):
    """Nhóm đơn hoàn ĐÃ ghi sổ mang mã vận đơn `code` (không cần dòng hàng)."""
    note = ReturnNote.objects.create(
        code=f"PTH-{code}", warehouse=warehouse, doc_date=timezone.localdate()
    )
    return ReturnEntry.objects.create(
        return_note=note, shipping_code=code, status=ReturnStatus.POSTED
    )


def test_create_batch_return_blocked_by_live_scan(warehouse):
    PendingShipmentScan.objects.create(
        code="RT1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    results = PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id, codes=["RT1"]
    )
    assert results[0] == {"code": "RT1", "ok": False, "reason": "duplicate"}
    assert PendingShipmentScan.objects.filter(code="RT1").count() == 1


def test_create_batch_return_allowed_after_scan_voided(warehouse):
    PendingShipmentScan.objects.create(
        code="RT2", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, voided_at=timezone.now(),
    )
    results = PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id, codes=["RT2"]
    )
    assert results[0]["ok"] is True
    assert PendingShipmentScan.objects.filter(code="RT2", voided_at__isnull=True).count() == 1


def test_create_batch_return_blocked_by_live_entry(warehouse):
    # Không có dòng scan nào — mã đi thẳng ra nhóm đơn hoàn lúc bắn (đơn có sẵn).
    _live_return_entry(warehouse, "RT3")
    results = PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id, codes=["RT3"]
    )
    assert results[0] == {"code": "RT3", "ok": False, "reason": "duplicate"}


def test_create_batch_return_allowed_after_entry_cancelled(warehouse):
    entry = _live_return_entry(warehouse, "RT4")
    entry.status = ReturnStatus.CANCELLED
    entry.save(update_fields=["status"])
    results = PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id, codes=["RT4"]
    )
    assert results[0]["ok"] is True


def test_create_batch_dispatch_not_blocked_by_return_history(warehouse):
    # Luật 1-mã-1-lần chỉ áp cho NHẬP HOÀN. Cùng mã vận đơn dĩ nhiên vừa được
    # quét xuất giao vừa được quét nhập hoàn (hàng đi rồi quay về).
    _live_return_entry(warehouse, "RT5")
    PendingShipmentScan.objects.create(
        code="RT5", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    results = PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id, codes=["RT5"]
    )
    assert results[0]["ok"] is True


def test_voided_scan_hidden_from_list(warehouse):
    PendingShipmentScan.objects.create(
        code="RT6", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, voided_at=timezone.now(),
    )
    PendingShipmentScan.objects.create(
        code="RT7", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    codes = [
        it["code"]
        for it in PendingScanService.list_unmatched(
            kind=PendingScanKind.RETURN, match_filter="all"
        )
    ]
    assert codes == ["RT7"]


def test_voided_scan_hidden_from_export_notes(warehouse):
    PendingShipmentScan.objects.create(
        code="RT8", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, note="ghi chú đã thu hồi",
        voided_at=timezone.now(),
    )
    assert PendingScanService.scan_notes_by_code(kind=PendingScanKind.RETURN) == {}


# ─────────────────────── API ───────────────────────


@pytest.fixture
def api_client():
    user = make_user_with_sections("pscan_staff", ["shipments"])
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def api_client_no_section():
    # User có quyền ở section khác (không phải "shipments") — vẫn phải bị 403.
    user = make_user_with_permissions("pscan_no_section", ["orders:view"])
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_batch_create_and_duplicate(api_client, warehouse):
    resp = api_client.post(
        "/api/inventory/pending-scans/batch",
        {"kind": "dispatch", "warehouse_id": warehouse.id, "codes": ["A1", "A2"]},
        format="json",
    )
    assert resp.status_code == 201
    assert all(r["ok"] for r in resp.data["results"])

    resp2 = api_client.post(
        "/api/inventory/pending-scans/batch",
        {"kind": "dispatch", "warehouse_id": warehouse.id, "codes": ["A1"]},
        format="json",
    )
    assert resp2.data["results"][0]["reason"] == "duplicate"


def test_batch_create_invalid_return_type_rejected(api_client, warehouse):
    resp = api_client.post(
        "/api/inventory/pending-scans/batch",
        {"kind": "return", "warehouse_id": warehouse.id,
         "return_type": "xxx", "codes": ["B1"]},
        format="json",
    )
    assert resp.status_code == 400


def test_list_default_excludes_matched(api_client, warehouse):
    PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    PendingShipmentScan.objects.create(
        code="F", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.FAILED
    )
    PendingShipmentScan.objects.create(
        code="M", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.MATCHED
    )
    resp = api_client.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 200
    codes = {i["code"] for i in resp.data["items"]}
    assert codes == {"P", "F"}
    assert resp.data["total"] == 2


def test_list_endpoint_hides_voided_scans(api_client, warehouse):
    """Dòng đã thu hồi biến mất khỏi MỌI danh sách, không riêng tab Nhập hoàn.

    Sót một đường đọc là dòng đã huỷ vẫn hiện ở chỗ khác và người dùng lại tưởng
    đã nhập 2 lần — đúng lớp lỗi "sửa 1 trong N endpoint" đã cắn nhiều lần.
    """
    PendingShipmentScan.objects.create(code="V1", kind="return", warehouse=warehouse)
    PendingShipmentScan.objects.create(
        code="V2", kind="return", warehouse=warehouse, voided_at=timezone.now()
    )
    resp = api_client.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 200
    assert {i["code"] for i in resp.data["items"]} == {"V1"}
    assert resp.data["total"] == 1


def test_list_default_includes_cancelled_with_reason(api_client, warehouse):
    """Mã dừng vì đơn đã hủy PHẢI hiện mặc định trên màn kho, kèm lý do.

    Nếu lọc mất trạng thái này thì kho quét xong không thấy mã đâu, cron cũng không thử
    lại → mã bị nuốt im lặng (trước đây ca này ra `failed` nên vẫn hiện đỏ).
    """
    PendingShipmentScan.objects.create(
        code="C1",
        kind="dispatch",
        warehouse=warehouse,
        status=PendingScanStatus.CANCELLED,
        error="Đơn đã hủy — không xuất kho.",
    )
    resp = api_client.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 200
    items = {i["code"]: i for i in resp.data["items"]}
    assert "C1" in items
    assert items["C1"]["status"] == PendingScanStatus.CANCELLED
    assert items["C1"]["error"] == "Đơn đã hủy — không xuất kho."


def test_retry_only_failed(api_client, warehouse):
    failed = PendingShipmentScan.objects.create(
        code="F", kind="dispatch", warehouse=warehouse,
        status=PendingScanStatus.FAILED, error="boom",
    )
    pending = PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    ok = api_client.post(f"/api/inventory/pending-scans/{failed.id}/retry")
    assert ok.status_code == 200
    failed.refresh_from_db()
    assert failed.status == PendingScanStatus.PENDING
    assert failed.error == ""
    bad = api_client.post(f"/api/inventory/pending-scans/{pending.id}/retry")
    assert bad.status_code == 400


def test_delete_blocks_matched(api_client, warehouse):
    matched = PendingShipmentScan.objects.create(
        code="M", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.MATCHED
    )
    pending = PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    assert api_client.delete(f"/api/inventory/pending-scans/{pending.id}").status_code == 204
    assert api_client.delete(f"/api/inventory/pending-scans/{matched.id}").status_code == 400


def test_requires_shipments_section(api_client_no_section):
    resp = api_client_no_section.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 403


# ─────────────────────── Bounds list_unmatched ───────────────────────
# Bounds cho list_unmatched (audit followups 2026-07 mục 3.2): default 30 ngày
# khi thiếu cả date_from/date_to + chặn cứng số dòng trả về.


def _make_scan_bounds(warehouse, *, days_old, code, status=PendingScanStatus.PENDING):
    scan = PendingShipmentScan.objects.create(
        code=code, kind=PendingScanKind.DISPATCH, warehouse=warehouse, status=status,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=timezone.now() - timedelta(days=days_old)
    )
    scan.refresh_from_db()
    return scan


def test_list_unmatched_defaults_to_last_30_days(warehouse):
    _make_scan_bounds(warehouse, days_old=5, code="RECENT")
    _make_scan_bounds(warehouse, days_old=45, code="OLD")

    items = PendingScanService.list_unmatched(kind=PendingScanKind.DISPATCH)

    assert {it["code"] for it in items} == {"RECENT"}


def test_list_unmatched_explicit_date_from_only_not_defaulted(warehouse):
    # Chỉ truyền 1 trong 2 mốc → KHÔNG áp default 30 ngày, giữ hành vi cũ (nửa-mở).
    old = _make_scan_bounds(warehouse, days_old=45, code="OLD")
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH,
        date_from=old.created_at.date().isoformat(),
    )
    assert {it["code"] for it in items} == {"OLD"}


def test_list_unmatched_caps_at_max_rows(monkeypatch, warehouse, caplog):
    monkeypatch.setattr(svc, "_LIST_UNMATCHED_MAX_ROWS", 3)
    for i in range(5):
        _make_scan_bounds(warehouse, days_old=1, code=f"CODE-{i}")

    with caplog.at_level(logging.WARNING):
        items = PendingScanService.list_unmatched(
            kind=PendingScanKind.DISPATCH,
            date_from="2020-01-01",
            date_to="2099-01-01",
        )

    assert len(items) == 3
    assert any("chạm giới hạn" in r.message for r in caplog.records)


def test_list_unmatched_under_limit_no_warning(monkeypatch, warehouse, caplog):
    monkeypatch.setattr(svc, "_LIST_UNMATCHED_MAX_ROWS", 3)
    _make_scan_bounds(warehouse, days_old=1, code="ONLY-ONE")

    with caplog.at_level(logging.WARNING):
        items = PendingScanService.list_unmatched(kind=PendingScanKind.DISPATCH)

    assert len(items) == 1
    assert not any("chạm giới hạn" in r.message for r in caplog.records)


# ─────────────────────── Task match_pending_shipment_scans (celery lock) ───────────────────────

LOCK_KEY = "inventory:match_pending_scans:lock"


def test_task_runs_service_and_returns_summary(monkeypatch):
    called = {}

    def fake_match(limit=200):
        called["yes"] = True
        return {"processed": 0, "matched": 0, "failed": 0, "unmatched": 0}

    monkeypatch.setattr(
        "apps.inventory.application.services.pending_scan_service.PendingScanService.match_pending_scans",
        staticmethod(fake_match),
    )
    cache.delete(LOCK_KEY)
    result = tasks.match_pending_shipment_scans.apply().result
    assert called.get("yes")
    assert result["matched"] == 0
    assert cache.get(LOCK_KEY) is None  # lock được nhả


def test_task_skips_when_lock_held():
    cache.add(LOCK_KEY, 1, 30)
    try:
        result = tasks.match_pending_shipment_scans.apply().result
        assert result == {"skipped": "lock_held"}
    finally:
        cache.delete(LOCK_KEY)


# ─────────────────────── Purge (retention) ───────────────────────
# Retention PendingShipmentScan MATCHED (audit followups 2026-07 mục 3.2).
#
# Chỉ dòng status=matched cũ hơn retention bị xoá; pending/failed/invalid/cancelled
# KHÔNG BAO GIỜ bị task này đụng tới.


def _make_scan_purge(warehouse, *, status, days_old, code):
    scan = PendingShipmentScan.objects.create(
        code=code, kind=PendingScanKind.DISPATCH, warehouse=warehouse, status=status,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=timezone.now() - timedelta(days=days_old)
    )
    return scan


def test_purges_only_matched_older_than_retention(settings, warehouse):
    settings.PENDING_SHIPMENT_SCAN_RETENTION_DAYS = 90
    old_matched = _make_scan_purge(warehouse, status=PendingScanStatus.MATCHED, days_old=120, code="OLD-MATCHED")
    recent_matched = _make_scan_purge(warehouse, status=PendingScanStatus.MATCHED, days_old=10, code="RECENT-MATCHED")
    old_pending = _make_scan_purge(warehouse, status=PendingScanStatus.PENDING, days_old=200, code="OLD-PENDING")
    old_failed = _make_scan_purge(warehouse, status=PendingScanStatus.FAILED, days_old=200, code="OLD-FAILED")
    old_invalid = _make_scan_purge(warehouse, status=PendingScanStatus.INVALID, days_old=200, code="OLD-INVALID")
    old_cancelled = _make_scan_purge(warehouse, status=PendingScanStatus.CANCELLED, days_old=200, code="OLD-CANCELLED")

    deleted = tasks.purge_matched_pending_scans()

    assert deleted == 1
    assert not PendingShipmentScan.objects.filter(pk=old_matched.pk).exists()
    for kept in (recent_matched, old_pending, old_failed, old_invalid, old_cancelled):
        assert PendingShipmentScan.objects.filter(pk=kept.pk).exists()


def test_retention_zero_disables_purge(settings, warehouse):
    settings.PENDING_SHIPMENT_SCAN_RETENTION_DAYS = 0
    old_matched = _make_scan_purge(warehouse, status=PendingScanStatus.MATCHED, days_old=400, code="X")

    deleted = tasks.purge_matched_pending_scans()

    assert deleted == 0
    assert PendingShipmentScan.objects.filter(pk=old_matched.pk).exists()


def test_skips_when_lock_held(settings, warehouse):
    settings.PENDING_SHIPMENT_SCAN_RETENTION_DAYS = 90
    old_matched = _make_scan_purge(warehouse, status=PendingScanStatus.MATCHED, days_old=200, code="Y")

    with mock.patch("apps.inventory.tasks.single_flight") as sf:
        sf.return_value.__enter__.return_value = False
        deleted = tasks.purge_matched_pending_scans()

    assert deleted == -1
    assert PendingShipmentScan.objects.filter(pk=old_matched.pk).exists()


def test_purges_in_batches(settings, warehouse):
    settings.PENDING_SHIPMENT_SCAN_RETENTION_DAYS = 90
    for i in range(3):
        _make_scan_purge(warehouse, status=PendingScanStatus.MATCHED, days_old=200, code=f"BATCH-{i}")

    deleted = tasks.purge_matched_pending_scans(batch_size=1)

    assert deleted == 3
    assert PendingShipmentScan.objects.filter(status=PendingScanStatus.MATCHED).count() == 0
