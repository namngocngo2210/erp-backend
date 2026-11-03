import pytest

from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.domain.serial import CODE_LENGTH
from apps.warranty_codes.models import WarrantyCode, WarrantyCodeBatch, WarrantyCodeBatchStatus

pytestmark = pytest.mark.django_db


def test_create_batch_allocates_contiguous_range(product_factory):
    b1 = WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)
    b2 = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    assert (b1.start_seq, b1.end_seq, b1.quantity) == (1, 5, 5)
    assert (b2.start_seq, b2.end_seq) == (6, 8)
    assert b1.status == WarrantyCodeBatchStatus.RESERVED
    serials = list(WarrantyCode.objects.order_by("seq").values_list("serial", flat=True))
    assert serials[0] == "DX-000001"
    assert serials[-1] == "DX-000008"
    assert WarrantyCode.objects.count() == 8


def test_create_batch_with_product_is_assigned(product_factory):
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=2, product_id=p.id, created_by=None)
    assert b.status == WarrantyCodeBatchStatus.ASSIGNED
    assert b.product_id == p.id
    assert set(WarrantyCode.objects.filter(batch=b).values_list("product_id", flat=True)) == {p.id}


def test_create_batch_codes_are_unique_code_length_chars(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=50, product_id=None, created_by=None)
    codes = list(WarrantyCode.objects.filter(batch=b).values_list("code", flat=True))
    assert len(set(codes)) == 50
    assert all(len(c) == CODE_LENGTH for c in codes)


def test_batch_code_format_and_daily_counter(product_factory):
    b1 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    b2 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    assert b1.batch_code.startswith("LOT-")
    assert b1.batch_code.endswith("-001")
    assert b2.batch_code.endswith("-002")


def test_create_batch_validates_quantity(product_factory):
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.create_batch(quantity=0, product_id=None, created_by=None)
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.create_batch(quantity=1, start_seq=0, product_id=None, created_by=None)


# ── Tạo lô theo khoảng số (start_seq tuỳ chọn) ──


def test_create_batch_with_start_seq_allocates_requested_range(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=10, start_seq=20_001, product_id=None, created_by=None)
    assert (b.start_seq, b.end_seq, b.quantity) == (20_001, 20_010, 10)
    assert not b.is_generating
    serials = list(WarrantyCode.objects.filter(batch=b).order_by("seq").values_list("serial", flat=True))
    assert serials == [f"DX-{n:06d}" for n in range(20_001, 20_011)]
    # Counter nhảy theo số lớn nhất → lô "nối tiếp" sau đó chạy tiếp từ 20011.
    nxt = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    assert (nxt.start_seq, nxt.end_seq) == (20_011, 20_012)


def test_create_batch_start_seq_may_fill_gap_below_counter(product_factory):
    WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)  # 1..5
    WarrantyCodeBatchService.create_batch(quantity=5, start_seq=100, product_id=None, created_by=None)
    gap = WarrantyCodeBatchService.create_batch(quantity=5, start_seq=10, product_id=None, created_by=None)
    assert (gap.start_seq, gap.end_seq) == (10, 14)
    nxt = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    assert nxt.start_seq == 105  # counter không tụt về sau lô lấp khoảng trống


def test_create_batch_rejects_overlapping_range(product_factory):
    existing = WarrantyCodeBatchService.create_batch(quantity=5, start_seq=10, product_id=None, created_by=None)
    for start, qty in ((8, 3), (12, 1), (14, 5), (1, 100)):
        with pytest.raises(ValueError) as exc:
            WarrantyCodeBatchService.create_batch(quantity=qty, start_seq=start, product_id=None, created_by=None)
        assert existing.batch_code in str(exc.value)
    # Sát biên thì được.
    WarrantyCodeBatchService.create_batch(quantity=2, start_seq=8, product_id=None, created_by=None)
    WarrantyCodeBatchService.create_batch(quantity=2, start_seq=15, product_id=None, created_by=None)
    assert WarrantyCodeBatch.objects.count() == 3


def test_serial_beyond_pad_width_is_not_truncated(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=1, start_seq=1_000_000, product_id=None, created_by=None)
    assert WarrantyCode.objects.get(batch=b).serial == "DX-1000000"


# ── Sinh mã chạy nền cho lô lớn ──


def test_large_batch_is_generated_in_background(settings, monkeypatch, django_capture_on_commit_callbacks):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 10
    from apps.warranty_codes import tasks

    delayed: list[int] = []
    monkeypatch.setattr(tasks.generate_batch_codes, "delay", lambda batch_id: delayed.append(batch_id))
    with django_capture_on_commit_callbacks(execute=True):
        b = WarrantyCodeBatchService.create_batch(quantity=11, product_id=None, created_by=None)
    assert delayed == [b.id]
    assert b.is_generating and b.generated_count == 0
    assert WarrantyCode.objects.filter(batch=b).count() == 0

    assert WarrantyCodeBatchService.generate_codes(batch_id=b.id) == 11
    b.refresh_from_db()
    assert not b.is_generating and b.generated_count == 11
    assert WarrantyCode.objects.filter(batch=b).count() == 11


def test_small_batch_is_generated_synchronously(settings, monkeypatch):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 10
    from apps.warranty_codes import tasks

    monkeypatch.setattr(tasks.generate_batch_codes, "delay", lambda batch_id: pytest.fail("không được đẩy nền"))
    b = WarrantyCodeBatchService.create_batch(quantity=10, product_id=None, created_by=None)
    assert not b.is_generating
    assert WarrantyCode.objects.filter(batch=b).count() == 10


def test_sync_generate_failure_hands_off_to_task(settings, monkeypatch, django_capture_on_commit_callbacks):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 100
    from apps.warranty_codes import tasks

    delayed: list[int] = []
    monkeypatch.setattr(tasks.generate_batch_codes, "delay", lambda batch_id: delayed.append(batch_id))
    monkeypatch.setattr(
        WarrantyCodeBatchService, "generate_codes", staticmethod(lambda *, batch_id: (_ for _ in ()).throw(RuntimeError("DB tèo")))
    )
    with django_capture_on_commit_callbacks(execute=True):
        b = WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)
    assert delayed == [b.id]
    assert b.is_generating  # dải đã cấp, lô tồn tại, task nền sẽ sinh nốt


def test_generate_codes_resumes_after_chunk_failure(settings, monkeypatch):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 0
    from apps.warranty_codes.application.services import batch_service as mod

    monkeypatch.setattr(mod, "_CHUNK_SIZE", 3)
    b = WarrantyCodeBatchService.create_batch(quantity=8, product_id=None, created_by=None)
    assert b.is_generating

    real_insert = WarrantyCodeBatchService._insert_chunk
    calls = {"n": 0}

    def flaky(batch, product, chunk_start, chunk_end):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("DB tèo giữa chừng")
        real_insert(batch, product, chunk_start, chunk_end)

    monkeypatch.setattr(WarrantyCodeBatchService, "_insert_chunk", staticmethod(flaky))
    with pytest.raises(RuntimeError):
        WarrantyCodeBatchService.generate_codes(batch_id=b.id)
    b.refresh_from_db()
    assert b.generated_seq == b.start_seq + 2  # chunk 1 đã chốt, chunk 2 rollback
    assert WarrantyCode.objects.filter(batch=b).count() == 3

    monkeypatch.setattr(WarrantyCodeBatchService, "_insert_chunk", staticmethod(real_insert))
    WarrantyCodeBatchService.generate_codes(batch_id=b.id)
    b.refresh_from_db()
    assert not b.is_generating
    seqs = list(WarrantyCode.objects.filter(batch=b).order_by("seq").values_list("seq", flat=True))
    assert seqs == list(range(b.start_seq, b.end_seq + 1))


def test_generate_codes_is_noop_when_complete(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    assert WarrantyCodeBatchService.generate_codes(batch_id=b.id) == 0
    assert WarrantyCode.objects.filter(batch=b).count() == 3


def test_assign_and_split_blocked_while_generating(settings, product_factory):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 0
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)
    with pytest.raises(ValueError, match="đang sinh"):
        WarrantyCodeBatchService.assign_product(batch_id=b.id, product_id=p.id, assigned_by=None)
    with pytest.raises(ValueError, match="đang sinh"):
        WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.start_seq + 1, user=None)


def test_split_batch_keeps_generated_seq_consistent(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=10, product_id=None, created_by=None)
    parent, child = WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.start_seq + 3, user=None)
    assert parent.generated_seq == parent.end_seq
    assert child.generated_seq == child.end_seq
    assert not parent.is_generating and not child.is_generating


def test_create_batch_unknown_product(product_factory):
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.create_batch(quantity=1, product_id=999_999, created_by=None)


def test_assign_product_updates_batch_and_codes(product_factory):
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    b = WarrantyCodeBatchService.assign_product(batch_id=b.id, product_id=p.id, assigned_by=None)
    assert b.status == WarrantyCodeBatchStatus.ASSIGNED
    assert b.assigned_at is not None
    assert set(WarrantyCode.objects.filter(batch=b).values_list("product_id", flat=True)) == {p.id}


def test_assign_product_rejects_assigned_batch(product_factory):
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=1, product_id=p.id, created_by=None)
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.assign_product(batch_id=b.id, product_id=p.id, assigned_by=None)


def test_split_batch_moves_tail_codes(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=10, product_id=None, created_by=None)
    parent, child = WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.start_seq + 3, user=None)
    assert (parent.start_seq, parent.end_seq, parent.quantity) == (b.start_seq, b.start_seq + 3, 4)
    assert (child.start_seq, child.end_seq, child.quantity) == (b.start_seq + 4, b.end_seq, 6)
    assert child.parent_id == parent.id
    assert child.batch_code != parent.batch_code
    assert WarrantyCode.objects.filter(batch=parent).count() == 4
    assert WarrantyCode.objects.filter(batch=child).count() == 6


def test_split_batch_boundary_validation(product_factory):
    b = WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.end_seq, user=None)
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.start_seq - 1, user=None)


def test_split_batch_rejects_assigned(product_factory):
    p = product_factory()
    b = WarrantyCodeBatchService.create_batch(quantity=5, product_id=p.id, created_by=None)
    with pytest.raises(ValueError):
        WarrantyCodeBatchService.split_batch(batch_id=b.id, cut_seq=b.start_seq + 1, user=None)
