"""CnvSyncService với FakeClient (pattern apps/best_express/tests/test_shipment_service.py).

Gộp thêm test_backfill_service.py (tái cấu trúc 2026-08-05) — CnvBackfillService
điều phối CnvSyncService; docstring gốc giữ ở comment section cuối file.
"""

from unittest.mock import MagicMock

import pytest

from apps.cnv_loyalty.application.services.backfill_service import CnvBackfillService
from apps.cnv_loyalty.application.services.sync_service import CnvSyncService
from apps.cnv_loyalty.exceptions import CnvApiError
from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord, CnvSyncStatus

pytestmark = pytest.mark.django_db


class FakeClient:
    def __init__(self):
        self.created_customers = []
        self.created_products = []
        self.updated_products = []
        self.created_collections = []
        self.collects = []
        self.created_orders = []
        self.updated_orders = []
        self.fail_with: Exception | None = None
        self.fail_customer_create_with: Exception | None = None
        self.existing_customers: list[dict] = []
        self._next_id = 1000

    def _allocate(self):
        self._next_id += 1
        return self._next_id

    def _maybe_fail(self):
        if self.fail_with is not None:
            raise self.fail_with

    def create_customer(self, payload):
        self._maybe_fail()
        if self.fail_customer_create_with is not None:
            raise self.fail_customer_create_with
        self.created_customers.append(payload)
        return {"customer": {"id": self._allocate()}}

    def list_customers(self, params=None):
        return {"customers": self.existing_customers}

    def create_product(self, payload):
        self._maybe_fail()
        self.created_products.append(payload)
        product_id = self._allocate()
        return {"product": {"id": product_id, "variants": [{"id": self._allocate()}]}}

    def update_product(self, product_id, payload):
        self._maybe_fail()
        self.updated_products.append((product_id, payload))
        return {"product": {"id": product_id, "variants": []}}

    def create_custom_collection(self, payload):
        self._maybe_fail()
        self.created_collections.append(payload)
        return {"custom_collection": {"id": self._allocate()}}

    def create_collect(self, collection_id, product_id):
        self._maybe_fail()
        self.collects.append((collection_id, product_id))
        return {}

    def create_order(self, payload):
        self._maybe_fail()
        self.created_orders.append(payload)
        return {"order": {"id": self._allocate()}}

    def update_order(self, order_id, payload):
        self._maybe_fail()
        self.updated_orders.append((order_id, payload))
        return {"order": {"id": order_id}}


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def service(fake_client):
    return CnvSyncService(fake_client)


class TestSyncCustomer:
    def test_creates_and_maps(self, service, fake_client, order_factory):
        order = order_factory()
        record = service.sync_customer(order.customer_id)
        assert record.status == CnvSyncStatus.SYNCED
        assert record.cnv_id is not None
        assert fake_client.created_customers[0]["customer"]["phone"]

    def test_already_synced_skips_api(self, service, fake_client, order_factory):
        order = order_factory()
        first = service.sync_customer(order.customer_id)
        second = service.sync_customer(order.customer_id)
        assert second.cnv_id == first.cnv_id
        assert len(fake_client.created_customers) == 1

    def test_missing_customer_marks_failed(self, service):
        record = service.sync_customer(999_999)
        assert record.status == CnvSyncStatus.FAILED
        assert "không tồn tại" in record.last_error

    def test_api_error_marks_failed_and_counts_attempts(
        self, service, fake_client, order_factory
    ):
        fake_client.fail_with = CnvApiError("CNV HTTP 500", status_code=500)
        order = order_factory()
        record = service.sync_customer(order.customer_id)
        assert record.status == CnvSyncStatus.FAILED
        assert record.attempts == 1
        assert "500" in record.last_error

    def test_existing_contact_is_skipped_not_failed(
        self, service, fake_client, order_factory
    ):
        """Khách đã có sẵn trên CNV → bỏ qua create, KHÔNG tính là lỗi.

        CNV không cho tra contact theo SĐT (probe prod 2026-08-06) nên mình không
        cầm được id; record vẫn 'synced' để cron thôi retry vô ích.
        """
        order = order_factory()
        fake_client.fail_customer_create_with = CnvApiError(
            "CNV POST /customers.json: Contact email or phone is existed"
        )
        record = service.sync_customer(order.customer_id)
        assert record.status == CnvSyncStatus.SYNCED
        assert record.cnv_id is None
        assert record.extra["existing_on_cnv"] is True
        assert record.attempts == 0


class TestSyncProduct:
    def test_creates_with_variant_id(self, service, fake_client, order_factory):
        order = order_factory()
        product_id = order.items.first().product_id
        record = service.sync_product(product_id)
        assert record.status == CnvSyncStatus.SYNCED
        assert record.extra["variant_id"] > 0

    def test_second_sync_updates(self, service, fake_client, order_factory):
        order = order_factory()
        product_id = order.items.first().product_id
        first = service.sync_product(product_id)
        second = service.sync_product(product_id)
        assert second.cnv_id == first.cnv_id
        assert len(fake_client.updated_products) == 1

    def test_links_synced_collections_once(self, service, fake_client, order_factory):
        from apps.catalog.models import ProductCategory

        order = order_factory()
        product = order.items.first().product
        category = ProductCategory.objects.create(code="cnv-cat", name="Danh mục CNV")
        product.categories.add(category)
        service.sync_collection(category.pk)

        service.sync_product(product.pk)
        service.sync_product(product.pk)
        assert len(fake_client.collects) == 1


class TestSyncOrder:
    def test_syncs_dependencies_then_order(self, service, fake_client, order_factory):
        order = order_factory(items=2)
        record = service.sync_order(order.pk)
        assert record.status == CnvSyncStatus.SYNCED
        assert len(fake_client.created_customers) == 1
        assert len(fake_client.created_products) == 2
        body = fake_client.created_orders[0]["order"]
        assert body["unique_id"] == f"desi-{order.pk}"
        assert len(body["line_items"]) == 2
        assert all(item["product_exists"] for item in body["line_items"])

    def test_existing_cnv_id_updates(self, service, fake_client, order_factory):
        order = order_factory()
        first = service.sync_order(order.pk)
        second = service.sync_order(order.pk)
        assert second.cnv_id == first.cnv_id
        assert len(fake_client.created_orders) == 1
        assert len(fake_client.updated_orders) == 1

    def test_soft_deleted_order_marks_failed(self, service, order_factory):
        from django.utils import timezone

        order = order_factory()
        order.deleted_at = timezone.now()
        order.save(update_fields=["deleted_at"])
        record = service.sync_order(order.pk)
        assert record.status == CnvSyncStatus.FAILED

    def test_customer_failure_blocks_order(self, service, fake_client, order_factory):
        fake_client.fail_with = CnvApiError("CNV HTTP 503", status_code=503)
        order = order_factory()
        record = service.sync_order(order.pk)
        assert record.status == CnvSyncStatus.FAILED
        assert "customer" in record.last_error
        assert not fake_client.created_orders

    def test_order_of_existing_customer_still_syncs_with_phone(
        self, service, fake_client, order_factory
    ):
        """Khách đã có sẵn ở CNV (không có id) thì đơn vẫn phải đẩy được:
        payload mang SĐT + tên để CNV tự khớp về đúng contact."""
        order = order_factory()
        fake_client.fail_customer_create_with = CnvApiError("phone is existed")
        record = service.sync_order(order.pk)
        assert record.status == CnvSyncStatus.SYNCED
        body = fake_client.created_orders[0]["order"]
        assert "id" not in body["customer"]
        assert body["customer"]["phone"] == order.customer.phone_normalized
        assert body["customer"]["first_name"]

    def test_unique_record_per_entity(self, service, order_factory):
        order = order_factory()
        service.sync_order(order.pk)
        service.sync_order(order.pk)
        assert (
            CnvSyncRecord.objects.filter(
                entity_type=CnvEntityType.ORDER, local_id=order.pk
            ).count()
            == 1
        )


# ---------------------------------------------------------------------------
# Backfill: keyset đi hết dữ liệu, skip entity đã synced, dry-run không gọi API.
# (CnvBackfillService điều phối CnvSyncService — ở đây sync service là MagicMock.)
# ---------------------------------------------------------------------------


def _record(status=CnvSyncStatus.SYNCED, **kwargs):
    record = MagicMock()
    record.status = status
    return record


@pytest.fixture
def sync_service():
    service = MagicMock()
    for name in ("sync_customer", "sync_product", "sync_order", "sync_collection"):
        getattr(service, name).side_effect = lambda pk: _record()
    return service


def test_backfills_orders_with_keyset_batches(sync_service, order_factory):
    orders = [order_factory() for _ in range(3)]
    backfill = CnvBackfillService(sync_service, batch_size=2, sleep_seconds=0)
    stats = backfill.run(entities=["orders"])
    assert stats["orders"]["seen"] == 3
    assert stats["orders"]["synced"] == 3
    synced_ids = [call[0][0] for call in sync_service.sync_order.call_args_list]
    assert synced_ids == sorted(order.pk for order in orders)


def test_skips_already_synced(sync_service, order_factory):
    order = order_factory()
    CnvSyncRecord.objects.create(
        entity_type=CnvEntityType.ORDER, local_id=order.pk, status=CnvSyncStatus.SYNCED
    )
    stats = CnvBackfillService(sync_service, sleep_seconds=0).run(entities=["orders"])
    assert stats["orders"]["skipped"] == 1
    sync_service.sync_order.assert_not_called()


def test_failed_record_not_skipped(sync_service, order_factory):
    order = order_factory()
    CnvSyncRecord.objects.create(
        entity_type=CnvEntityType.ORDER, local_id=order.pk, status=CnvSyncStatus.FAILED
    )
    CnvBackfillService(sync_service, sleep_seconds=0).run(entities=["orders"])
    sync_service.sync_order.assert_called_once_with(order.pk)


def test_dry_run_calls_nothing(sync_service, order_factory):
    order_factory()
    stats = CnvBackfillService(sync_service, sleep_seconds=0).run(
        entities=["orders", "customers"], dry_run=True
    )
    assert stats["orders"]["seen"] == 1
    sync_service.sync_order.assert_not_called()
    sync_service.sync_customer.assert_not_called()


def test_default_order_collections_first(sync_service):
    backfill = CnvBackfillService(sync_service, sleep_seconds=0)
    stats = backfill.run()
    assert list(stats.keys()) == ["collections", "products", "customers", "orders"]
