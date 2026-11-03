"""Loyalty ranking, segmentation & reporting service.

Extracted from the former 810-line ``CustomerService`` god-class. Owns the
"khách hàng thân thiết" use case: the heavy GROUP-BY ranking (Redis-cached),
segment classification, per-customer product/channel rollups, problem-signal
detection and the loyal-note upsert.

Behaviour is identical to the original methods; only the owning class changed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.orders.models import Order, OrderItem
from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.customers.services import customer_phone_masker
from apps.users.models import User

from . import segmentation
from .models import Customer, CustomerSegmentCategory, LoyalCustomerNote
from .order_status_constants import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS
from .segment_catalog import segment_catalog

# Số khách hàng tối đa trong bảng xếp hạng thân thiết.
LOYAL_CUSTOMERS_TOP_N = 100
# Số sản phẩm hiển thị tối đa cho mỗi khách (top theo số lượng mua).
LOYAL_TOP_PRODUCTS = 5
# Cache bảng xếp hạng (GROUP BY toàn bộ đơn) qua Redis — phần truy vấn nặng. Key kèm
# khoảng thời gian để mỗi bộ lọc cache riêng. Bump version khi đổi cấu trúc.
LOYAL_RANKING_CACHE_PREFIX = "loyal_customers:ranking:v5"
LOYAL_RANKING_CACHE_TTL = 300  # 5 phút


def _parse_loyal_date_bounds(date_from: str | None, date_to: str | None):
    """Chuẩn hóa khoảng lọc theo created_at.

    Trả về (from_dt, to_dt_exclusive, reference_dt) — đều tz-aware hoặc None.
    ``to_dt_exclusive`` là 00:00 ngày kế tiếp (lọc nửa mở). ``reference_dt`` dùng để
    tính độ mới khi phân loại: mốc cuối khoảng nếu có ``date_to``, ngược lại là hiện tại.
    """

    def _parse(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    from_dt = _parse(date_from)
    to_dt = _parse(date_to)
    to_dt_exclusive = to_dt + timedelta(days=1) if to_dt else None
    reference_dt = to_dt_exclusive or timezone.now()
    return from_dt, to_dt_exclusive, reference_dt


class CustomerLoyaltyService:
    @staticmethod
    def _compute_loyal_ranking(from_dt=None, to_dt_exclusive=None) -> list[dict]:
        """Truy vấn GROUP BY nặng: top khách hàng theo tổng chi tiêu (đơn giao thành công)."""
        queryset = (
            Order.objects.filter(
                deleted_at__isnull=True,
                status=DELIVERED_SUCCESS_ORDER_STATUS,
                customer__deleted_at__isnull=True,
            )
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)  # loại đơn đã hủy hóa đơn
        )
        if from_dt is not None:
            queryset = queryset.filter(created_at__gte=from_dt)
        if to_dt_exclusive is not None:
            queryset = queryset.filter(created_at__lt=to_dt_exclusive)
        return list(
            queryset.values("customer_id")
            .annotate(
                total_spent=Sum("total_after_discount"),
                purchase_count=Count("id"),
                last_purchase_at=Max("created_at"),
            )
            .order_by("-total_spent", "-purchase_count", "customer_id")[:LOYAL_CUSTOMERS_TOP_N]
        )

    @staticmethod
    def get_loyal_ranking(*, from_dt=None, to_dt_exclusive=None, cache_suffix: str = "", force_refresh: bool = False) -> list[dict]:
        """Bảng xếp hạng (cache Redis theo khoảng lọc). ``force_refresh`` để tính lại bỏ qua cache."""
        cache_key = f"{LOYAL_RANKING_CACHE_PREFIX}:{cache_suffix}"
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        ranking = CustomerLoyaltyService._compute_loyal_ranking(from_dt, to_dt_exclusive)
        cache.set(cache_key, ranking, timeout=LOYAL_RANKING_CACHE_TTL)
        return ranking

    @staticmethod
    def list_loyal_customers(
        *,
        limit: int,
        offset: int,
        date_from: str | None = None,
        date_to: str | None = None,
        segment: str | None = None,
        category: str | None = None,
        force_refresh: bool = False,
        user=None,
    ) -> dict:
        """Top khách hàng thân thiết theo tổng chi tiêu (đơn giao thành công).

        Có thể lọc theo khoảng ``created_at`` của đơn — số liệu mua, sản phẩm và phân khúc
        đều tính trong khoảng; độ mới khi phân loại lấy mốc cuối khoảng. ``segment`` lọc theo
        mã phân khúc (S1–S7) trong phạm vi top. Bảng xếp hạng nặng cache Redis; ghi chú lấy tươi.

        Hạng là vị trí tuyệt đối trong bảng xếp hạng (không đổi khi lọc phân khúc).
        """
        from_dt, to_dt_exclusive, reference_dt = _parse_loyal_date_bounds(date_from, date_to)
        cache_suffix = f"{date_from or ''}:{date_to or ''}"
        ranking = CustomerLoyaltyService.get_loyal_ranking(
            from_dt=from_dt,
            to_dt_exclusive=to_dt_exclusive,
            cache_suffix=cache_suffix,
            force_refresh=force_refresh,
        )

        # Phân loại toàn bộ top để lọc/đếm theo phân khúc, giữ hạng tuyệt đối.
        all_ids = [row["customer_id"] for row in ranking]
        problem_ids = CustomerLoyaltyService._customers_with_problem_signal(all_ids, from_dt, to_dt_exclusive)
        classified: list[tuple[int, dict, str, int | None]] = []
        for index, row in enumerate(ranking):
            last_purchase_at = row["last_purchase_at"]
            days_since_last = (reference_dt - last_purchase_at).days if last_purchase_at else None
            segment_code = segmentation.classify_segment(
                delivered_count=row["purchase_count"] or 0,
                total_spent=row["total_spent"],
                days_since_last_purchase=days_since_last,
                has_problem=row["customer_id"] in problem_ids,
            )
            classified.append((index + 1, row, segment_code, days_since_last))

        if segment:
            classified = [item for item in classified if item[2] == segment]

        if category:
            try:
                category_id = int(category)
            except (TypeError, ValueError):
                category_id = None
            if category_id is not None:
                cat_ids = CustomerLoyaltyService._customers_in_product_category(
                    all_ids, category_id, from_dt, to_dt_exclusive
                )
                classified = [item for item in classified if item[1]["customer_id"] in cat_ids]
            else:
                classified = []

        total = len(classified)
        page = classified[offset : offset + limit]
        customer_ids = [row["customer_id"] for _, row, _, _ in page]
        if not customer_ids:
            return {"results": [], "total": total}

        customers = {c.id: c for c in Customer.objects.filter(id__in=customer_ids)}
        notes = {
            n.customer_id: n
            for n in LoyalCustomerNote.objects.filter(customer_id__in=customer_ids).select_related("assigned_sale")
        }
        # Che SĐT khách KHÔNG phải của mình: "của mình" = được gán chăm sóc
        # (LoyalCustomerNote.assigned_sale) HOẶC có ít nhất một đơn do mình phụ trách.
        masker = customer_phone_masker(user, customer_ids)
        products_map = CustomerLoyaltyService._top_products_by_customer(customer_ids, from_dt, to_dt_exclusive)
        purchase_channels_map = CustomerLoyaltyService._purchase_channels_by_customer(customer_ids, from_dt, to_dt_exclusive)
        catalog = CustomerLoyaltyService._segment_catalog()

        results = []
        for rank, row, segment_code, days_since_last in page:
            customer_id = row["customer_id"]
            customer = customers.get(customer_id)
            if customer is None:
                continue
            note = notes.get(customer_id)
            sale = note.assigned_sale if note and note.assigned_sale_id else None
            segment_meta = catalog.get(segment_code)
            results.append(
                {
                    "rank": rank,
                    "customer_id": customer_id,
                    "code": customer.code,
                    "full_name": customer.full_name,
                    "phone": masker.apply(
                        customer.phone_raw or customer.phone_normalized,
                        customer_id,
                        owner_id=sale.id if sale else None,
                    ),
                    "purchase_count": row["purchase_count"] or 0,
                    "days_since_last_purchase": days_since_last,
                    "total_spent": str(row["total_spent"] or Decimal("0")),
                    "products": products_map.get(customer_id, []),
                    "last_purchase_at": row["last_purchase_at"],
                    "sale_staff_id": sale.id if sale else None,
                    "sale_staff_name": (sale.display_name or sale.username) if sale else "",
                    "last_care_call_at": note.last_care_call_at if note else None,
                    "purchase_channels": purchase_channels_map.get(customer_id, []),
                    "segment_code": segment_code,
                    "segment_name": segment_meta["name"] if segment_meta else "",
                    "segment_care_priority": segment_meta["care_priority"] if segment_meta else "",
                    "sale_action": note.sale_action if note else "",
                    "special_note": note.special_note if note else "",
                }
            )
        return {"results": results, "total": total}

    @staticmethod
    def list_segment_categories() -> list[dict]:
        """Danh mục phân khúc S1–S7 (cho dropdown lọc)."""
        return [
            {
                "code": c.code,
                "name": c.name,
                "criteria": c.criteria,
                "care_priority": c.care_priority,
            }
            for c in CustomerSegmentCategory.objects.filter(is_active=True)
        ]

    @staticmethod
    def _customers_in_product_category(customer_ids, category_id, from_dt=None, to_dt_exclusive=None) -> set[int]:
        """customer_ids (trong phạm vi top) đã mua >=1 sản phẩm thuộc danh mục, trong các đơn giao thành công."""
        item_qs = OrderItem.objects.filter(
            order__customer_id__in=customer_ids,
            order__status=DELIVERED_SUCCESS_ORDER_STATUS,
            order__deleted_at__isnull=True,
            product__categories__id=category_id,
        ).exclude(order__invoice_status=CANCELLED_INVOICE_STATUS).exclude(order__branch_id__in=EXCLUDED_BRANCH_IDS)
        if from_dt is not None:
            item_qs = item_qs.filter(order__created_at__gte=from_dt)
        if to_dt_exclusive is not None:
            item_qs = item_qs.filter(order__created_at__lt=to_dt_exclusive)
        return set(item_qs.values_list("order__customer_id", flat=True).distinct())

    @staticmethod
    def _top_products_by_customer(customer_ids: list[int], from_dt=None, to_dt_exclusive=None) -> dict[int, list[dict]]:
        """Top sản phẩm theo SKU (gộp số lượng) của đơn giao thành công, mỗi khách tối đa
        ``LOYAL_TOP_PRODUCTS``. Trả về {customer_id: [{sku, quantity}, ...]} sắp xếp giảm dần."""
        item_qs = OrderItem.objects.filter(
            order__customer_id__in=customer_ids,
            order__status=DELIVERED_SUCCESS_ORDER_STATUS,
            order__deleted_at__isnull=True,
        ).exclude(order__invoice_status=CANCELLED_INVOICE_STATUS)
        if from_dt is not None:
            item_qs = item_qs.filter(order__created_at__gte=from_dt)
        if to_dt_exclusive is not None:
            item_qs = item_qs.filter(order__created_at__lt=to_dt_exclusive)
        # Gộp số lượng ngay trong DB (1 dòng/sản phẩm-mỗi-khách) thay vì kéo từng dòng
        # OrderItem về Python — giảm mạnh dữ liệu truyền với khách có nhiều đơn.
        rows = (
            item_qs.values("order__customer_id", "sku", "product__sku", "product_name")
            .annotate(qty=Sum("quantity"))
        )
        # Gộp tiếp theo SKU hiệu lực (sku → product.sku → tên) cho các tổ hợp trùng nhãn.
        totals: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in rows:
            label = (row["sku"] or "").strip() or (row["product__sku"] or "").strip() or (row["product_name"] or "").strip()
            if not label:
                continue
            totals[row["order__customer_id"]][label] += int(row["qty"] or 0)

        products_map: dict[int, list[dict]] = {}
        for customer_id, sku_totals in totals.items():
            ranked = sorted(sku_totals.items(), key=lambda item: (-item[1], item[0]))
            products_map[customer_id] = [
                {"sku": sku, "quantity": quantity} for sku, quantity in ranked[:LOYAL_TOP_PRODUCTS]
            ]
        return products_map

    @staticmethod
    def _purchase_channels_by_customer(customer_ids: list[int], from_dt=None, to_dt_exclusive=None) -> dict[int, list[str]]:
        order_qs = (
            Order.objects.filter(
                customer_id__in=customer_ids,
                status=DELIVERED_SUCCESS_ORDER_STATUS,
                deleted_at__isnull=True,
            )
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
        )
        if from_dt is not None:
            order_qs = order_qs.filter(created_at__gte=from_dt)
        if to_dt_exclusive is not None:
            order_qs = order_qs.filter(created_at__lt=to_dt_exclusive)

        channels_map: dict[int, list[str]] = defaultdict(list)
        seen: dict[int, set[str]] = defaultdict(set)
        rows = order_qs.values("customer_id", "channel__name", "channel__code").distinct()
        for row in rows:
            customer_id = row["customer_id"]
            label = (row["channel__name"] or "").strip() or (row["channel__code"] or "").strip()
            if not label or label in seen[customer_id]:
                continue
            seen[customer_id].add(label)
            channels_map[customer_id].append(label)
        return {customer_id: sorted(labels) for customer_id, labels in channels_map.items()}

    @staticmethod
    def _customers_with_problem_signal(customer_ids: list[int], from_dt=None, to_dt_exclusive=None) -> set[int]:
        """Khách có tín hiệu "có vấn đề": hoàn hàng / khiếu nại / đánh giá xấu / bảo hành."""
        problem_orders = Order.objects.filter(
            customer_id__in=customer_ids,
            deleted_at__isnull=True,
        )
        if from_dt is not None:
            problem_orders = problem_orders.filter(created_at__gte=from_dt)
        if to_dt_exclusive is not None:
            problem_orders = problem_orders.filter(created_at__lt=to_dt_exclusive)
        problem_orders = problem_orders.filter(
            Q(status__in=segmentation.PROBLEM_ORDER_STATUSES)
            | Q(assessment__sales_status_code__in=segmentation.PROBLEM_SALES_STATUS_CODES)
            | Q(assessment__delivery_business_status_code__in=segmentation.PROBLEM_DELIVERY_STATUS_CODES)
            | Q(assessment__post_purchase_rating_code__in=segmentation.PROBLEM_POST_PURCHASE_RATING_CODES)
        )
        return set(problem_orders.values_list("customer_id", flat=True).distinct())

    @staticmethod
    def _segment_catalog() -> dict[str, dict]:
        """Danh mục phân khúc {code: {name, care_priority}} từ DB."""
        return segment_catalog()

    @staticmethod
    def upsert_loyal_note(
        customer: Customer,
        *,
        sale_action: str | None = None,
        special_note: str | None = None,
        resale_status: str | None = None,
        resale_suggestion: str | None = None,
        assigned_sale: User | None = None,
        has_assigned_sale: bool = False,
        last_care_call_at=None,
        has_last_care_call_at: bool = False,
        updated_by=None,
    ) -> LoyalCustomerNote:
        note, _ = LoyalCustomerNote.objects.get_or_create(customer=customer)
        update_fields: list[str] = []
        if sale_action is not None:
            note.sale_action = sale_action
            update_fields.append("sale_action")
        if special_note is not None:
            note.special_note = special_note
            update_fields.append("special_note")
        if resale_status is not None:
            note.resale_status = resale_status
            update_fields.append("resale_status")
        if resale_suggestion is not None:
            note.resale_suggestion = resale_suggestion
            update_fields.append("resale_suggestion")
        if has_assigned_sale:
            note.assigned_sale = assigned_sale
            update_fields.append("assigned_sale")
        if has_last_care_call_at:
            note.last_care_call_at = last_care_call_at
            update_fields.append("last_care_call_at")
        if update_fields:
            note.updated_by = updated_by
            update_fields.extend(["updated_by", "updated_at"])
            note.save(update_fields=list(dict.fromkeys(update_fields)))
        return note
