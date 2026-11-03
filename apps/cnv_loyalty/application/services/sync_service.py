"""Đẩy từng entity sang CNV Loyalty, ghi kết quả vào outbox CnvSyncRecord.

Nguyên tắc:
- Đọc trạng thái MỚI NHẤT từ DB tại thời điểm chạy (last-write-wins) — trigger
  trùng nhau (signal + bus event + retry cron) vô hại.
- KHÔNG raise khỏi các method sync_*: mọi thất bại nằm ở record
  ``failed + last_error + attempts``; cron retry với backoff lo phần còn lại.
- Dependency của đơn (customer, products) được sync inline trước khi đẩy đơn.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.cnv_loyalty.contracts.dtos import CnvApi
from apps.cnv_loyalty.domain import mappers
from apps.cnv_loyalty.exceptions import CnvApiError
from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord, CnvSyncStatus

logger = logging.getLogger(__name__)

_ERROR_MAX_LEN = 500


class CnvSyncService:
    def __init__(self, client: CnvApi):
        self.client = client

    # -- public -------------------------------------------------------------

    def sync_customer(self, customer_id: int) -> CnvSyncRecord:
        from apps.customers.models import Customer

        record = self._get_record(CnvEntityType.CUSTOMER, customer_id)
        customer = Customer.objects.filter(pk=customer_id, deleted_at__isnull=True).first()
        if customer is None:
            return self._mark_failed(record, "customer không tồn tại hoặc đã xóa")
        try:
            # CNV chưa mô tả PUT /customers — khách đã sync rồi thì thông tin
            # định danh (phone) không đổi, coi như xong.
            if record.cnv_id:
                return self._mark_synced(record, record.cnv_id)
            try:
                data = self.client.create_customer(mappers.map_customer(customer))
                cnv_id = self._extract_id(data, "customer")
            except CnvApiError as exc:
                # Shop đã có sẵn contact trên CNV (app loyalty chạy trước CRM) nên
                # create trùng SĐT bị từ chối.
                #
                # ĐÍNH CHÍNH 2026-08-19: probe 06/08 kết luận "CNV không cho lọc
                # khách theo SĐT" vì thử tham số trên `/customers.json` — CNV có
                # endpoint RIÊNG `/customers/get-by-phone.json`. Nhờ nó lấy lại
                # được `cnv_id` thay vì đánh dấu synced với id rỗng. Việc này
                # quan trọng hơn vẻ ngoài: `cnv_id` của đơn là tầng chống trùng
                # số 2 khi kéo đơn từ CNV về (spec 2026-08-19 §4).
                if "existed" not in str(exc).lower():
                    raise
                record.extra["existing_on_cnv"] = True
                cnv_id = self._lookup_customer_id_by_phone(customer)
                logger.info(
                    "CNV: customer#%s đã tồn tại phía CNV — tra theo SĐT được id=%s",
                    customer_id, cnv_id or "-",
                )
                return self._mark_synced(record, cnv_id)
            return self._mark_synced(record, cnv_id)
        except Exception as exc:  # noqa: BLE001 — mọi lỗi về record, cron retry
            return self._mark_failed(record, exc)

    def sync_product(self, product_id: int) -> CnvSyncRecord:
        from apps.catalog.models import Product

        record = self._get_record(CnvEntityType.PRODUCT, product_id)
        product = (
            Product.objects.filter(pk=product_id, deleted_at__isnull=True)
            .prefetch_related("categories")
            .first()
        )
        if product is None:
            return self._mark_failed(record, "product không tồn tại hoặc đã xóa")
        try:
            payload = mappers.map_product(product)
            if record.cnv_id:
                data = self.client.update_product(record.cnv_id, payload)
                cnv_id = record.cnv_id
            else:
                data = self.client.create_product(payload)
                cnv_id = self._extract_id(data, "product")
            variant_id = self._extract_variant_id(data)
            if variant_id:
                record.extra["variant_id"] = variant_id
            self._link_collections(record, product, cnv_id)
            return self._mark_synced(record, cnv_id)
        except Exception as exc:  # noqa: BLE001
            return self._mark_failed(record, exc)

    def sync_collection(self, category_id: int) -> CnvSyncRecord:
        from apps.catalog.models import ProductCategory

        record = self._get_record(CnvEntityType.CUSTOM_COLLECTION, category_id)
        category = ProductCategory.objects.filter(
            pk=category_id, deleted_at__isnull=True
        ).first()
        if category is None:
            return self._mark_failed(record, "category không tồn tại hoặc đã xóa")
        try:
            if record.cnv_id:
                return self._mark_synced(record, record.cnv_id)
            data = self.client.create_custom_collection(
                mappers.map_custom_collection(category)
            )
            cnv_id = self._extract_id(data, "custom_collection")
            return self._mark_synced(record, cnv_id)
        except Exception as exc:  # noqa: BLE001
            return self._mark_failed(record, exc)

    def sync_order(self, order_id: int) -> CnvSyncRecord:
        from apps.orders.models import Order

        record = self._get_record(CnvEntityType.ORDER, order_id)
        order = (
            Order.objects.filter(pk=order_id, deleted_at__isnull=True)
            .select_related("customer")
            .prefetch_related("items")
            .first()
        )
        if order is None:
            return self._mark_failed(record, "order không tồn tại hoặc đã xóa")
        if order.customer_id is None:
            return self._mark_failed(record, "order chưa gắn customer")
        try:
            customer_record = self._ensure_dependency(
                CnvEntityType.CUSTOMER, order.customer_id, self.sync_customer
            )
            if customer_record.status != CnvSyncStatus.SYNCED:
                return self._mark_failed(
                    record, f"customer#{order.customer_id} chưa sync: {customer_record.last_error}"
                )

            items = list(order.items.all())
            product_cnv_map: dict[int, dict] = {}
            for item in items:
                if item.product_id is None:
                    continue
                product_record = self._ensure_dependency(
                    CnvEntityType.PRODUCT, item.product_id, self.sync_product
                )
                if product_record.status == CnvSyncStatus.SYNCED:
                    product_cnv_map[item.product_id] = {
                        "id": product_record.cnv_id,
                        "variant_id": product_record.extra.get("variant_id", 0),
                    }

            payload = mappers.map_order(order, items, customer_record.cnv_id, product_cnv_map)
            if record.cnv_id:
                self.client.update_order(record.cnv_id, payload)
                cnv_id = record.cnv_id
            else:
                data = self.client.create_order(payload)
                cnv_id = self._extract_id(data, "order")
            return self._mark_synced(record, cnv_id)
        except Exception as exc:  # noqa: BLE001
            return self._mark_failed(record, exc)

    # -- helpers ------------------------------------------------------------

    def _link_collections(self, record: CnvSyncRecord, product, product_cnv_id: int) -> None:
        """Gắn product vào các collection ĐÃ sync; nhớ danh mục đã gắn để không collect trùng."""
        linked = set(record.extra.get("collected_category_ids", []))
        for category in product.categories.all():
            if category.pk in linked or category.deleted_at is not None:
                continue
            collection_record = CnvSyncRecord.objects.filter(
                entity_type=CnvEntityType.CUSTOM_COLLECTION,
                local_id=category.pk,
                status=CnvSyncStatus.SYNCED,
            ).first()
            if collection_record is None or not collection_record.cnv_id:
                continue
            self.client.create_collect(collection_record.cnv_id, product_cnv_id)
            linked.add(category.pk)
        record.extra["collected_category_ids"] = sorted(linked)

    def _ensure_dependency(
        self, entity_type: str, local_id: int, sync_fn
    ) -> CnvSyncRecord:
        existing = CnvSyncRecord.objects.filter(
            entity_type=entity_type, local_id=local_id, status=CnvSyncStatus.SYNCED
        ).first()
        if existing is not None:
            return existing
        return sync_fn(local_id)

    @staticmethod
    def _get_record(entity_type: str, local_id: int) -> CnvSyncRecord:
        record, _created = CnvSyncRecord.objects.get_or_create(
            entity_type=entity_type, local_id=local_id
        )
        return record

    def _lookup_customer_id_by_phone(self, customer) -> int | None:
        """Tra `cnv_id` của khách đã tồn tại sẵn bên CNV. None nếu không tra được.

        Nuốt mọi lỗi có chủ đích: đây là đường CỨU VÃN sau khi create đã fail vì
        trùng. Tra không ra thì hành vi quay về đúng như trước (synced với id
        rỗng, đơn vẫn đẩy được nhờ payload mang SĐT) — không được biến một cải
        tiến thành nguyên nhân fail mới.
        """
        phone = (
            getattr(customer, "phone_normalized", "")
            or getattr(customer, "phone_raw", "")
            or ""
        ).strip()
        if not phone:
            return None
        lookup = getattr(self.client, "get_customer_by_phone", None)
        if lookup is None:
            return None
        try:
            data = lookup(phone)
        except Exception:  # noqa: BLE001 — xem docstring
            logger.info("CNV: tra khách theo SĐT thất bại (customer#%s)", customer.pk)
            return None
        return self._first_customer_id(data)

    @staticmethod
    def _first_customer_id(data) -> int | None:
        """Moi id khách ra khỏi response chưa biết chắc hình dạng.

        Swagger CNV không khai response body của endpoint nào, nên chấp nhận cả
        `{"customer": {...}}`, `{"customers": [...]}`, `{"data": ...}` và object trần.
        """
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        for key in ("customer", "data", "customers", "results"):
            value = data.get(key)
            if isinstance(value, (dict, list)):
                found = CnvSyncService._first_customer_id(value)
                if found:
                    return found
        try:
            return int(data["id"]) or None
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _extract_id(data: dict, resource: str) -> int:
        body = data.get(resource) if isinstance(data, dict) else None
        cnv_id = (body or {}).get("id")
        if not cnv_id:
            raise ValueError(f"CNV không trả {resource}.id: {str(data)[:200]}")
        return int(cnv_id)

    @staticmethod
    def _extract_variant_id(data: dict) -> int:
        variants = (data.get("product") or {}).get("variants") or []
        if variants and isinstance(variants, list) and variants[0].get("id"):
            return int(variants[0]["id"])
        return 0

    @staticmethod
    def _mark_synced(record: CnvSyncRecord, cnv_id: int | None) -> CnvSyncRecord:
        # cnv_id=None: entity coi như xong nhưng mình KHÔNG cầm id phía CNV
        # (khách đã tồn tại sẵn ở CNV). Đơn của khách đó vẫn đẩy được nhờ payload
        # mang SĐT — xem mappers.map_order_customer.
        record.cnv_id = cnv_id
        record.status = CnvSyncStatus.SYNCED
        record.last_error = ""
        record.last_synced_at = timezone.now()
        record.save(
            update_fields=[
                "cnv_id", "status", "last_error", "last_synced_at", "extra", "updated_at",
            ]
        )
        return record

    @staticmethod
    def _mark_failed(record: CnvSyncRecord, error: Exception | str) -> CnvSyncRecord:
        record.status = CnvSyncStatus.FAILED
        record.attempts += 1
        record.last_error = str(error)[:_ERROR_MAX_LEN]
        record.save(update_fields=["status", "attempts", "last_error", "extra", "updated_at"])
        logger.warning(
            "CNV sync failed: %s#%s attempt=%s error=%s",
            record.entity_type, record.local_id, record.attempts, record.last_error,
        )
        return record
