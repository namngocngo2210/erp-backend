"""Test command `regenerate_warranty_codes` — sinh lại mã dưới lớp cào cho
TOÀN BỘ mã đã có (chuyển 10 → 8 ký tự, quyết định chủ 18/09/2026, tem chưa in).

Bất biến khoá ở đây: chỉ đổi cột `code`, giữ nguyên seq/serial/lô/sản phẩm;
mặc định dry-run; từ chối chạy khi đã có mã bị quét/kích hoạt; reset
`exported_at` để bảng lô hiện "chưa xuất" (file Excel cũ vô giá trị).
"""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.warranty_codes.application.services import activation_service
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.domain.serial import CODE_LENGTH
from apps.warranty_codes.models import WarrantyCode, WarrantyCodeBatch

pytestmark = pytest.mark.django_db


def _snapshot():
    return {
        c.id: (c.seq, c.serial, c.code, c.batch_id, c.product_id)
        for c in WarrantyCode.objects.all()
    }


def test_dry_run_by_default_changes_nothing():
    WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    before = _snapshot()
    out = StringIO()
    call_command("regenerate_warranty_codes", stdout=out)
    assert _snapshot() == before
    assert "DRY-RUN" in out.getvalue()
    assert "3" in out.getvalue()


def test_apply_rewrites_only_code_and_resets_exported_at(product_factory):
    product = product_factory(sku="SKU-REGEN")
    batch = WarrantyCodeBatchService.create_batch(quantity=5, product_id=product.id, created_by=None)
    WarrantyCodeBatch.objects.filter(id=batch.id).update(exported_at=timezone.now())
    # Giả lập mã cũ 10 ký tự như prod trước hotfix.
    for i, c in enumerate(WarrantyCode.objects.order_by("seq")):
        WarrantyCode.objects.filter(id=c.id).update(code=f"OLD{i:07d}")
    before = _snapshot()

    out = StringIO()
    call_command("regenerate_warranty_codes", "--apply", stdout=out)

    after = _snapshot()
    assert set(after) == set(before)
    for cid, (seq, serial, code, batch_id, product_id) in before.items():
        n_seq, n_serial, n_code, n_batch, n_product = after[cid]
        assert (n_seq, n_serial, n_batch, n_product) == (seq, serial, batch_id, product_id)
        assert n_code != code
        assert len(n_code) == CODE_LENGTH
    assert len({v[2] for v in after.values()}) == 5  # vẫn unique
    batch.refresh_from_db()
    assert batch.exported_at is None
    assert "5" in out.getvalue()


def test_refuses_when_any_code_already_used():
    batch = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    code = WarrantyCode.objects.filter(batch=batch).order_by("seq").first()
    activation_service.activate(keyword=code.code, phone="0901234567", channel="web", ip=None, user_agent="")
    before = _snapshot()
    with pytest.raises(CommandError, match="đã kích hoạt|đã quét"):
        call_command("regenerate_warranty_codes", "--apply")
    assert _snapshot() == before
