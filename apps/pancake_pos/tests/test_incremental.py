"""S2 tăng dần: event catalog → hàng đợi dirty (sau commit), claim SKIP LOCKED, beat
`process_dirty` (cờ bật/tắt, khoá, trả lại hàng đợi khi lỗi), lượt chạy tay (khoá)."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.db.models.functions import Now

from apps.catalog.models import Product, ProductGroup
from apps.orders.application.services.product_component_service import ProductComponentService
from apps.pancake_pos import tasks
from apps.pancake_pos.application.services import sync_jobs
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosApiError
from apps.pancake_pos.models import PancakePosDirtyProduct, PancakePosSyncRun
from apps.pancake_pos.repositories import DirtyQueue
from apps.sysconfig import override_sysconfig

pytestmark = pytest.mark.django_db

JOBS = "apps.pancake_pos.application.services.sync_jobs"


def _dirty() -> set[int]:
    return set(PancakePosDirtyProduct.objects.values_list("product_id", flat=True))


@pytest.fixture
def lock_free():
    cache.delete(sync_jobs.LOCK_KEY)
    yield
    cache.delete(sync_jobs.LOCK_KEY)


# ── hook sau commit ─────────────────────────────────────────────────────────
def test_luu_san_pham_danh_dau_dirty_sau_commit(django_capture_on_commit_callbacks):
    with override_sysconfig(PANCAKE_POS_ENABLED=True):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            product = Product.objects.create(sku="OHI01-DEN", name="Ghế")
            assert _dirty() == set()  # chưa commit → chưa đánh dấu
        for callback in callbacks:
            callback()
    assert _dirty() == {product.id}


def test_co_tat_thi_khong_danh_dau(django_capture_on_commit_callbacks):
    with override_sysconfig(PANCAKE_POS_ENABLED=False), django_capture_on_commit_callbacks(execute=True):
        Product.objects.create(sku="OHI01-DEN", name="Ghế")
    assert _dirty() == set()


def test_sua_va_xoa_mem_deu_danh_dau(django_capture_on_commit_callbacks):
    product = Product.objects.create(sku="OHI01-DEN", name="Ghế")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        product.price = 90000
        product.save()
    assert _dirty() == {product.id}
    DirtyQueue.claim(10)
    with override_sysconfig(PANCAKE_POS_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        product.deleted_at = product.updated_at
        product.save(update_fields=["deleted_at", "updated_at"])
    assert _dirty() == {product.id}


def test_sua_ten_ngan_danh_dau_dirty(django_capture_on_commit_callbacks):
    """Sửa tên ngắn (form → catalog_write_service.update_product → save(); lệnh
    set_product_short_names → save(update_fields)) phải đẩy lại lên Pancake."""
    product = Product.objects.create(sku="OHI01-DEN", name="Ghế")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        product.short_name = "Ghế OHI01 Đen"
        product.save(update_fields=["short_name", "updated_at"])
    assert _dirty() == {product.id}


def test_sua_nhom_danh_dau_moi_san_pham_cua_nhom(django_capture_on_commit_callbacks):
    group = ProductGroup.objects.create(code="OHI01", name="OHI01")
    a = Product.objects.create(sku="OHI01-DEN", name="Đen", group=group)
    b = Product.objects.create(sku="OHI01-TRANG", name="Trắng", group=group)
    Product.objects.create(sku="GOI01", name="Gối")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        group.code = "OHI02"
        group.save()
    assert _dirty() == {a.id, b.id}


def test_khai_thanh_phan_combo_phat_products_changed(django_capture_on_commit_callbacks):
    combo = Product.objects.create(sku="COMBO01", name="Combo")
    part = Product.objects.create(sku="GOI01", name="Gối")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        ProductComponentService.replace(combo.id, [{"component_id": part.id, "quantity": 2}])
    assert combo.id in _dirty()


def test_rollback_thi_khong_danh_dau():
    with override_sysconfig(PANCAKE_POS_ENABLED=True), pytest.raises(RuntimeError), transaction.atomic():
        Product.objects.create(sku="OHI01-DEN", name="Ghế")
        raise RuntimeError("rollback")
    assert _dirty() == set()


# ── hàng đợi ────────────────────────────────────────────────────────────────
def test_dirty_queue_fifo_va_khong_trung():
    assert DirtyQueue.mark([3, 1, 3, 0]) == 2
    assert DirtyQueue.mark([1]) == 0  # đã có → giữ marked_at cũ
    DirtyQueue.mark([2])
    # Trong test mọi thứ chung 1 transaction (now() bằng nhau) → dời tay cho rõ thứ tự.
    PancakePosDirtyProduct.objects.filter(product_id=1).update(marked_at=Now() + timedelta(seconds=5))
    assert DirtyQueue.count() == 3
    assert DirtyQueue.claim(2) == [2, 3]
    assert DirtyQueue.claim(10) == [1]
    assert DirtyQueue.claim(10) == []


@pytest.mark.django_db(transaction=True)
def test_claim_bo_qua_dong_dang_bi_khoa():
    DirtyQueue.mark([1, 2, 3])
    locked = threading.Event()
    release = threading.Event()

    def hold_row_1() -> None:
        conn = connections.create_connection("default")
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute("SELECT product_id FROM pancake_pos_dirty_products WHERE product_id = 1 FOR UPDATE")
                locked.set()
                release.wait(10)
                cur.execute("ROLLBACK")
        finally:
            conn.close()

    worker = threading.Thread(target=hold_row_1)
    worker.start()
    try:
        assert locked.wait(10)
        assert DirtyQueue.claim(10) == [2, 3]
    finally:
        release.set()
        worker.join(10)
    assert DirtyQueue.claim(10) == [1]
    connection.close()


# ── beat ────────────────────────────────────────────────────────────────────
def test_beat_tat_co_khong_lam_gi(lock_free):
    DirtyQueue.mark([1])
    with override_sysconfig(PANCAKE_POS_ENABLED=False), patch(f"{JOBS}.build_product_sync_service") as build:
        assert tasks.sync_dirty_products() == {"reason": "disabled"}
    build.assert_not_called()
    assert _dirty() == {1}


def test_beat_dang_co_khoa_thi_bo_qua_va_giu_hang_doi(lock_free):
    DirtyQueue.mark([1])
    cache.add(sync_jobs.LOCK_KEY, "other", 60)
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(f"{JOBS}.build_product_sync_service") as build:
        assert sync_jobs.process_dirty() == {"reason": "locked"}
    build.assert_not_called()
    assert _dirty() == {1}


def test_beat_xu_ly_theo_lo(lock_free):
    DirtyQueue.mark([5, 6, 7])
    service = MagicMock()
    service.sync_products.return_value.run_id = 9
    service.sync_products.return_value.counters.as_dict.return_value = {"updated": 3}
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(
        f"{JOBS}.build_product_sync_service", return_value=service
    ):
        result = sync_jobs.process_dirty(batch=2)
    assert result["products"] == 2 and result["run_id"] == 9
    assert service.sync_products.call_args.args[0] == [5, 6]
    assert _dirty() == {7}
    assert cache.get(sync_jobs.LOCK_KEY) is None  # nhả khoá


def test_beat_loi_tra_id_ve_hang_doi(lock_free):
    DirtyQueue.mark([5, 6])
    service = MagicMock()
    service.sync_products.side_effect = PancakePosApiError("HTTP 401 — key sai")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(
        f"{JOBS}.build_product_sync_service", return_value=service
    ):
        result = sync_jobs.process_dirty()
    assert result["reason"] == "error"
    assert _dirty() == {5, 6}

    service.sync_products.side_effect = RuntimeError("bug")
    with override_sysconfig(PANCAKE_POS_ENABLED=True), patch(
        f"{JOBS}.build_product_sync_service", return_value=service
    ), pytest.raises(RuntimeError):
        sync_jobs.process_dirty()
    assert _dirty() == {5, 6}


# ── lượt chạy tay ───────────────────────────────────────────────────────────
def test_khong_tao_2_luot_chay_tay_song_song():
    run_id = sync_jobs.create_manual_run("dry_run", None)
    with pytest.raises(sync_jobs.SyncAlreadyRunning):
        sync_jobs.create_manual_run("full", None)
    PancakePosSyncRun.objects.filter(id=run_id).update(finished_at=PancakePosSyncRun.objects.get(id=run_id).started_at)
    assert sync_jobs.create_manual_run("full", None) != run_id


def test_luot_chay_tay_bi_khoa_thi_task_thu_lai_roi_bo(lock_free):
    run_id = sync_jobs.create_manual_run("dry_run", None)
    cache.add(sync_jobs.LOCK_KEY, "other", 60)
    assert sync_jobs.execute_manual_run(run_id, mode="dry_run") == {"reason": "locked"}

    tasks.run_sync.push_request(retries=0)
    try:
        with patch.object(tasks.run_sync, "retry", side_effect=RuntimeError("retry")) as retry, pytest.raises(
            RuntimeError, match="retry"
        ):
            tasks.run_sync.run(run_id, "dry_run")
        retry.assert_called_once_with(countdown=tasks.LOCKED_RETRY_COUNTDOWN)
    finally:
        tasks.run_sync.pop_request()
    run = PancakePosSyncRun.objects.get(id=run_id)
    assert run.finished_at is None

    tasks.run_sync.push_request(retries=tasks.LOCKED_MAX_RETRIES)
    try:
        assert tasks.run_sync.run(run_id, "dry_run") == {"reason": "locked"}
    finally:
        tasks.run_sync.pop_request()
    run.refresh_from_db()
    assert run.finished_at is not None and "khoá" in run.report["fatal"]


def test_luot_chay_tay_loi_cau_hinh_dong_nhat_ky(lock_free):
    run_id = sync_jobs.create_manual_run("full", None)
    with override_sysconfig(PANCAKE_POS_ENABLED=False):
        result = sync_jobs.execute_manual_run(run_id, mode="full")
    assert result["reason"] == "error"
    run = PancakePosSyncRun.objects.get(id=run_id)
    assert run.finished_at is not None and "PANCAKE_POS_ENABLED" in run.report["fatal"]


def test_luot_chay_tay_dung_run_id_da_tao(lock_free):
    run_id = sync_jobs.create_manual_run("dry_run", None)
    service = MagicMock()
    service.full_sync.return_value.run_id = run_id
    service.full_sync.return_value.counters.as_dict.return_value = {"created": 0}
    with patch(f"{JOBS}.build_product_sync_service", return_value=service) as build:
        result = sync_jobs.execute_manual_run(run_id, mode="dry_run", limit=5, skus=["A"], user_id=None)
    build.assert_called_once_with(dry_run=True)
    assert service.full_sync.call_args.kwargs == {"dry_run": True, "limit": 5, "skus": ["A"], "user_id": None, "run_id": run_id}
    assert result["run_id"] == run_id
