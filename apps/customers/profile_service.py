from decimal import Decimal

from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Count, Max, Min, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from . import segmentation
from .models import Customer, CustomerSegmentCategory, LoyalCustomerNote
from .customer_loyalty_service import CustomerLoyaltyService
from .order_status_constants import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS
from .repositories import CustomerRepository
from .services import customer_phone_masker
from apps.orders.crm_catalog import AGE_GROUP_LABELS
from apps.orders.models import Order
from apps.common.business_constants import EXCLUDED_BRANCH_IDS

# Trạng thái đơn hoàn/hủy — loại khỏi metrics mua hàng.
class CustomerProfileService:
    # Các trường assessment đưa nguyên vào nhóm persona.
    _PERSONA_FIELDS = (
        "customer_classification_code", "pain_level_code", "pain_duration_code",
        "lifestyle_habit_codes", "used_massage_before", "relax_time_codes",
        "purchase_barrier_codes", "delax_source_codes", "comparing_brand_text",
        "call_duration_code", "post_purchase_feedback_text", "post_purchase_rating_code",
    )

    @staticmethod
    def list_profiles(*, q: str, limit: int, offset: int,
                      channel: str | None = None, sales_status: str | None = None,
                      segment: str | None = None, user=None) -> dict:
        """Danh sách hồ sơ khách hàng tổng hợp, phân trang trên bảng customers,
        làm giàu chỉ cho các customer_id của trang hiện tại."""
        base = CustomerRepository.list_customers_queryset(q)

        # Lọc theo "đơn mới nhất" của khách qua subquery tương quan.
        latest_order_qs = (
            Order.objects.filter(customer_id=OuterRef("pk"), deleted_at__isnull=True)
            .order_by("-created_at")
        )
        if channel:
            base = base.annotate(
                _latest_channel=Subquery(latest_order_qs.values("channel_id")[:1])
            ).filter(_latest_channel=channel)
        if sales_status:
            base = base.annotate(
                _latest_sales=Subquery(latest_order_qs.values("assessment__sales_status_code")[:1])
            ).filter(_latest_sales=sales_status)

        if segment:
            # Phân khúc là giá trị tính toán → phải tính cho toàn bộ id khớp rồi mới phân trang.
            all_ids = list(base.values_list("id", flat=True))
            metrics_all = CustomerProfileService._metrics_map(all_ids)
            problem_ids_all = CustomerLoyaltyService._customers_with_problem_signal(all_ids)
            latest_all = CustomerProfileService._latest_order_map(all_ids)
            latest_sales_all = {
                cid: getattr(getattr(o, "assessment", None), "sales_status_code", None)
                for cid, o in latest_all.items()
            }
            now = timezone.now()
            matched = []
            for cid in all_ids:
                m = metrics_all.get(cid, {})
                total_orders = m.get("total_orders", 0) or 0
                last = m.get("last_purchase_at")
                days = (now - last).days if last else None
                code = segmentation.classify_segment(
                    delivered_count=total_orders,
                    total_spent=m.get("total_spent") or Decimal("0"),
                    days_since_last_purchase=days,
                    has_problem=cid in problem_ids_all,
                    latest_sales_status_code=latest_sales_all.get(cid),
                )
                if code == segment:
                    matched.append(cid)
            total = len(matched)
            page_ids = matched[offset : offset + limit]
            page = list(Customer.objects.filter(id__in=page_ids))
            page.sort(key=lambda c: page_ids.index(c.id))
        else:
            total = base.count()
            page = list(base[offset : offset + limit])
        customer_ids = [c.id for c in page]
        if not customer_ids:
            return {"results": [], "total": total}

        # Che SĐT của khách KHÔNG có đơn nào do mình phụ trách (thiếu
        # `customer-phone:view`) — 1 query cho cả trang.
        masker = customer_phone_masker(user, customer_ids)
        metrics = CustomerProfileService._metrics_map(customer_ids)
        latest_map = CustomerProfileService._latest_order_map(customer_ids)
        problem_ids = CustomerLoyaltyService._customers_with_problem_signal(customer_ids)
        notes = {n.customer_id: n for n in LoyalCustomerNote.objects.filter(customer_id__in=customer_ids)}
        segment_names = dict(CustomerSegmentCategory.objects.values_list("code", "name"))
        results = []
        now = timezone.now()
        for customer in page:
            m = metrics.get(customer.id, {})
            total_orders = m.get("total_orders", 0) or 0
            last_purchase_at = m.get("last_purchase_at")
            days_since = (now - last_purchase_at).days if last_purchase_at else None
            order = latest_map.get(customer.id)
            assessment = getattr(order, "assessment", None) if order else None
            # ArrayAgg với filter loại hết dòng có thể trả [None] hoặc None tùy phiên bản
            # Django → chuẩn hóa về list sạch (bỏ None/rỗng).
            skus = [s for s in (m.get("distinct_skus") or []) if s]
            segment_code = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days_since,
                has_problem=customer.id in problem_ids,
                latest_sales_status_code=getattr(assessment, "sales_status_code", None) if assessment else None,
            )
            persona = {
                field: getattr(assessment, field, None) if assessment else None
                for field in CustomerProfileService._PERSONA_FIELDS
            }
            need_items = [
                {
                    "group_code": n.need_type.group.code,
                    "group_name": n.need_type.group.name_vi,
                    "type_code": n.need_type.code,
                    "type_name": n.need_type.name_vi,
                }
                for n in (assessment.needs.all() if assessment else [])
            ]
            results.append({
                "identity": {"id": customer.id, "code": customer.code,
                             "full_name": customer.full_name,
                             "phone": masker.apply(
                                 customer.phone_raw or customer.phone_normalized,
                                 customer.id,
                             )},
                "demographics": {
                    "gender": customer.gender,
                    "province": order.province if order else "",
                    "address": order.address if order else "",
                    "age_text": getattr(assessment, "age_text", "") if assessment else "",
                    "age_group_label": AGE_GROUP_LABELS.get(
                        getattr(assessment, "age_text", "") if assessment else "",
                    ),
                    "occupation_code": getattr(assessment, "occupation_code", None) if assessment else None,
                },
                "channel": {
                    "channel_id": order.channel_id if order else None,
                    "channel_name": order.channel.name if order and order.channel_id else "",
                },
                "segment": {"segment_code": segment_code,
                            "segment_name": segment_names.get(segment_code, "")},
                "purchase": {
                    "first_purchase_at": m.get("first_purchase_at"),
                    "last_purchase_at": last_purchase_at,
                    "total_orders": total_orders,
                    "total_spent": str(m.get("total_spent") or Decimal("0")),
                    "distinct_skus": skus,
                },
                "latest_order": {
                    "sku": order.sku if order else None,
                    "product": order.product if order else "",
                    "last_order_amount": str(order.total_after_discount) if order else None,
                    "last_order_status": order.status if order else None,
                    "sales_status_code": getattr(assessment, "sales_status_code", None) if assessment else None,
                    "not_closed_reason_code": getattr(assessment, "not_closed_reason_code", None) if assessment else None,
                },
                "persona": persona,
                "needs": {
                    "need_group_code": need_items[0]["group_code"] if need_items else "",
                    "need_group_name": need_items[0]["group_name"] if need_items else "",
                    "items": need_items,
                },
                "resale": {
                    "last_resale_at": last_purchase_at if total_orders >= 2 else None,
                    "resale_status": notes[customer.id].resale_status if customer.id in notes else "",
                    "resale_suggestion": notes[customer.id].resale_suggestion if customer.id in notes else "",
                },
            })
        return {"results": results, "total": total}

    @staticmethod
    def _metrics_map(customer_ids: list[int]) -> dict[int, dict]:
        rows = (
            Order.objects.filter(
                customer_id__in=customer_ids,
                deleted_at__isnull=True,
                status=DELIVERED_SUCCESS_ORDER_STATUS,
            )
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .values("customer_id")
            .annotate(
                total_orders=Count("id"),
                total_spent=Sum("total_after_discount"),
                first_purchase_at=Min("created_at"),
                last_purchase_at=Max("created_at"),
                distinct_skus=ArrayAgg("sku", distinct=True, filter=Q(sku__gt="")),
            )
        )
        return {r["customer_id"]: r for r in rows}

    @staticmethod
    def _latest_order_map(customer_ids: list[int]) -> dict[int, Order]:
        """1 đơn mới nhất/khách (kể cả đơn hoàn/hủy — phản ánh trạng thái hiện tại),
        kèm channel + assessment. DISTINCT ON (customer_id) ORDER BY created_at DESC."""
        orders = (
            Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
            .select_related("channel", "assessment")
            .prefetch_related("assessment__needs__need_type__group")
            .order_by("customer_id", "-created_at")
            .distinct("customer_id")
        )
        return {o.customer_id: o for o in orders}

    @staticmethod
    def _latest_sales_map(customer_ids: list[int]) -> dict[int, str | None]:
        """sales_status_code của đơn mới nhất mỗi khách. Đọc nhẹ: chỉ 2 cột,
        DISTINCT ON, không select_related/prefetch (report chỉ cần mã này)."""
        if not customer_ids:
            return {}
        rows = (
            Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
            .order_by("customer_id", "-created_at")
            .distinct("customer_id")
            .values_list("customer_id", "assessment__sales_status_code")
        )
        return dict(rows)
