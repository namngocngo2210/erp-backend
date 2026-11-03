from datetime import timedelta

import pytest
from django.utils import timezone


@pytest.fixture
def cnv_connection(db):
    from apps.cnv_loyalty.models import CnvConnection

    return CnvConnection.objects.create(
        name="CNV Test",
        client_id="test-client",
        client_secret="test-secret",
        access_token="valid-token",
        refresh_token="valid-refresh",
        token_type="Bearer",
        token_expires_at=timezone.now() + timedelta(hours=1),
    )


@pytest.fixture
def order_factory(order_factory):
    """Mặc định riêng cnv_loyalty trên fixture gốc (backend/conftest.py): đơn luôn có
    1 line item (payload đẩy CNV cần `line_items`) và tổng 200.000."""

    def _make(items: int = 1, **kwargs):
        kwargs.setdefault("prefix", "cnv")
        kwargs.setdefault("total_amount", 200_000)
        kwargs.setdefault("total_after_discount", 200_000)
        return order_factory(items=items, **kwargs)

    return _make
