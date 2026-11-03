"""Resale-tracking service.

Extracted from the former 810-line ``CustomerService`` god-class. Owns the
"đơn giao thành công + lịch sử mua + theo dõi resale" use case end-to-end:
listing, per-customer history rollups, and the contact/appointment/order
follow-up mutations.

Behaviour is identical to the original methods; only the owning class changed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.orders.crm_catalog import (
    CUSTOMER_CLASSIFICATION_LABELS,
    DELIVERY_BUSINESS_STATUS_LABELS,
    NOT_CLOSED_REASON_LABELS,
    POST_PURCHASE_RATING_LABELS,
    SALES_STATUS_LABELS,
)
from apps.orders.models import Order, OrderItem
from apps.common.business_constants import EXCLUDED_BRANCH_IDS

from .models import CustomerResaleFollowUp, CustomerSegmentSnapshot
from .order_status_constants import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS
from apps.common.phone_utils import phone_search_candidates
from apps.users.phone_visibility import (
    PhoneVisibility,
    is_public_phone_source,
    phone_visibility_for,
)
from .segment_catalog import segment_catalog

# Phân khúc khả dĩ trên trang resale: chỉ khách đã có đơn giao thành công, nên không
# bao giờ rơi vào S1 (chưa mua) / S7 (từ chối). Dùng để dựng facet cho dropdown.
RESALE_SEGMENT_CODES = ("S2", "S3", "S4", "S5", "S6")

CONTACT_STATUS_NONE = "none"
CONTACT_STATUS_CONTACTED = "contacted"
CONTACT_STATUS_COMPLETED = "completed"

# Count (455k delivered orders) và facets (DISTINCT sale toàn scope) là 2 query nặng
# chạy mỗi lần load trang nhưng kết quả đổi rất chậm (theo nhịp giao đơn). Cache Redis
# TTL ngắn để cắt ~600ms/lượt mà vẫn gần realtime. Bump version khi đổi cấu trúc/filter.
# v3: đổi tập cột tìm kiếm của `q` (bỏ phone thô, thêm phone_normalized) → count/facet
# cache theo cùng một `q` của bản v2 không còn khớp kết quả mới; bump để không phục vụ
# số đếm cũ sau khi deploy.
RESALE_CACHE_PREFIX = "resale_tracking:v3"
RESALE_COUNT_TTL = 60  # giây — count phụ thuộc cả contact_status (đổi khi tick liên hệ)
RESALE_FACETS_TTL = 300  # giây — danh sách sale gần như cố định


def _resale_cache_suffix(*parts) -> str:
    """Khóa cache ổn định, có giới hạn độ dài từ bộ filter (q có thể dài → hash)."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_delivered_bounds(date_from: str | None, date_to: str | None):
    """Chuẩn hóa khoảng lọc theo ngày giao thành công (``Order.updated_at``).

    Trả về ``(from_dt, to_dt_exclusive)`` tz-aware hoặc ``None``. ``to_dt_exclusive``
    là 00:00 ngày kế tiếp để lọc nửa mở (bao trọn cả ngày ``date_to``).
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
    return from_dt, to_dt_exclusive


class CustomerResaleService:
    @staticmethod
    def list_resale_tracking(
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sale: int | None = None,
        contact_status: str | None = None,
        segment: str | None = None,
        min_days: int | None = None,
        user=None,
    ) -> dict:
        """Don giao thanh cong, kem lich su mua hang cua khach.

        Lọc theo ngày giao (``date_from``/``date_to`` trên ``updated_at``), sale,
        trạng thái liên hệ — đều ở tầng SQL. ``segment``/``min_days`` (tình trạng
        lạnh) là trường tính toán nên dùng full-scan path. ``facets`` cấp danh sách
        sale/phân khúc để dropdown không bị kẹt khi đang lọc.
        """
        scoped = (
            Order.objects.filter(
                deleted_at__isnull=True,
                status=DELIVERED_SUCCESS_ORDER_STATUS,
                customer__deleted_at__isnull=True,
                order_source__isnull=False,
            )
            .exclude(order_source="")
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .select_related("customer", "channel", "staff", "assessment", "resale_followup")
            .prefetch_related(
                Prefetch("items", queryset=OrderItem.objects.select_related("product"))
            )
            .order_by("-updated_at", "-id")
        )

        from_dt, to_dt_exclusive = _parse_delivered_bounds(date_from, date_to)
        if from_dt:
            scoped = scoped.filter(updated_at__gte=from_dt)
        if to_dt_exclusive:
            scoped = scoped.filter(updated_at__lt=to_dt_exclusive)

        query_text = (q or "").strip()
        if query_text:
            # 8 nhánh `icontains` cũ sinh `UPPER(col) LIKE UPPER(%s)` → planner bỏ toàn
            # bộ trigram GIN bare-column, seq-scan 500k dòng `orders` mỗi lần gõ ô tìm
            # kiếm (audit 2026-07-26 C2). `__contains` (LIKE case-sensitive) dùng được
            # index; mã đơn/hóa đơn/vận đơn trong hệ này là CHỮ IN + số nên không mất
            # kết quả. `orders.name` giữ `icontains` vì đã có functional index
            # `order_name_upper_trgm_idx` trên `UPPER(name::text)`; còn
            # `customers.full_name` chỉ có GIN trigram BARE-COLUMN
            # (`cust_fullname_trgm_idx`) nên phải đi `__ilike_contains` — `icontains`
            # ở đây sinh `UPPER(full_name) LIKE` và seq-scan `customers`
            # (audit 2026-09-23 C1; apps/common/db_lookups.py).
            search = Q(name__icontains=query_text) | Q(customer__full_name__ilike_contains=query_text)
            if len(query_text) >= 3:
                # < 3 ký tự thì trigram GIN không vào được → không thêm nhánh mã.
                search |= Q(shipping_code__contains=query_text)
                search |= Q(order_code__contains=query_text)
                search |= Q(invoice_code__contains=query_text)
            # SĐT: so trên cột CHUẨN HÓA (khớp được `+84…`/có dấu cách người dùng gõ),
            # kèm cột THÔ để không mất đơn có SĐT không chuẩn hóa được (ô nhập 2 số →
            # `phone_normalized` rỗng). Cả 4 cột đều có trigram GIN bare-column
            # (`order_phone_norm_trgm_idx`, `order_phone_trgm_idx`,
            # `cust_phone_norm_trgm_idx`, `cust_phone_raw_trgm_idx`) nên `__contains`
            # index-scan được, khác hẳn `icontains` cũ.
            for candidate in phone_search_candidates(query_text):
                search |= Q(phone_normalized__contains=candidate)
                search |= Q(phone__contains=candidate)
                search |= Q(customer__phone_normalized__contains=candidate)
                search |= Q(customer__phone_raw__contains=candidate)
            scoped = scoped.filter(search)

        # Facet được tính trên phạm vi ngày + tìm kiếm (trước khi áp sale/segment/...)
        # để người dùng luôn chuyển được giữa các sale/phân khúc. Chỉ phụ thuộc q + ngày
        # nên cache theo đúng 3 tham số đó (DISTINCT toàn scope vốn ~100ms).
        facets_key = f"{RESALE_CACHE_PREFIX}:facets:{_resale_cache_suffix(query_text, date_from, date_to)}"
        facets = cache.get(facets_key)
        if facets is None:
            facets = CustomerResaleService._resale_facets(scoped)
            cache.set(facets_key, facets, timeout=RESALE_FACETS_TTL)

        listing = scoped
        if sale:
            listing = listing.filter(staff_id=sale)
        if contact_status == CONTACT_STATUS_NONE:
            listing = listing.filter(
                Q(resale_followup__isnull=True) | Q(resale_followup__contact_1_at__isnull=True)
            )
        elif contact_status == CONTACT_STATUS_CONTACTED:
            listing = listing.filter(resale_followup__contact_1_at__isnull=False)
        elif contact_status == CONTACT_STATUS_COMPLETED:
            listing = listing.filter(resale_followup__contact_3_at__isnull=False)

        # segment/min_days là trường phân khúc đã tính sẵn trong bảng rollup
        # ``customer_segment_snapshot`` (SegmentSnapshotService, cron rebuild mỗi 2h).
        # Đẩy filter + phân trang xuống SQL qua JOIN snapshot thay vì full-scan nạp
        # toàn bộ đơn vào Python. Khách chưa có snapshot sẽ không lọt filter phân khúc
        # (chấp nhận trễ tối đa 1 chu kỳ rebuild — snapshot cũng là nguồn hiển thị).
        if segment:
            listing = listing.filter(customer__segment_snapshot__segment_code=segment)
        if min_days:
            cutoff = timezone.now() - timedelta(days=min_days)
            listing = listing.filter(
                customer__segment_snapshot__last_purchase_at__isnull=False,
                customer__segment_snapshot__last_purchase_at__lte=cutoff,
            )

        # count() nặng (quét scope) nhưng đổi chậm → cache theo TOÀN BỘ bộ filter.
        count_key = (
            f"{RESALE_CACHE_PREFIX}:count:"
            f"{_resale_cache_suffix(query_text, date_from, date_to, sale, contact_status, segment, min_days)}"
        )
        total = cache.get(count_key)
        if total is None:
            total = listing.count()
            cache.set(count_key, total, timeout=RESALE_COUNT_TTL)
        page_orders = list(listing[offset : offset + limit])
        results = CustomerResaleService._build_rows(page_orders, user=user)

        return {"results": results, "total": total, "facets": facets}

    @staticmethod
    def _build_rows(orders: list[Order], *, user=None) -> list[dict]:
        customer_ids = list({order.customer_id for order in orders})
        history = CustomerResaleService._resale_order_history_by_customer(customer_ids)
        snapshots = CustomerResaleService._resale_snapshot_map(customer_ids)
        # Mỗi dòng của trang này LÀ một đơn giao thành công nên "của mình" xét thẳng
        # theo sale của đơn, không cần tra ngược theo khách như trang KH thân thiết.
        visibility = phone_visibility_for(user)
        return [
            CustomerResaleService._resale_row(
                order,
                history.get(order.customer_id, []),
                snapshots.get(order.customer_id),
                visibility=visibility,
            )
            for order in orders
        ]

    @staticmethod
    def _resale_snapshot_map(customer_ids: list[int]) -> dict[int, CustomerSegmentSnapshot]:
        """Snapshot phân khúc của các khách ở trang hiện tại — nguồn hiển thị
        ``segment_code``/``days_since_last_purchase`` (khớp đúng nguồn dùng để lọc)."""
        if not customer_ids:
            return {}
        rows = CustomerSegmentSnapshot.objects.filter(customer_id__in=customer_ids).only(
            "segment_code", "last_purchase_at"
        )
        return {row.customer_id: row for row in rows}

    @staticmethod
    def _resale_facets(scoped) -> dict:
        sale_rows = (
            scoped.exclude(staff__isnull=True)
            .values_list("staff_id", "staff__display_name", "staff__username")
            .distinct()
        )
        sales = sorted(
            (
                {"id": staff_id, "name": (display_name or username or "").strip()}
                for staff_id, display_name, username in sale_rows
            ),
            key=lambda item: item["name"].lower(),
        )
        catalog = segment_catalog()
        segments = [
            {"code": code, "name": catalog.get(code, {}).get("name", code)}
            for code in RESALE_SEGMENT_CODES
        ]
        return {"sales": sales, "segments": segments}

    # Trần lịch sử mua mỗi khách trên một dòng resale (audit 2026-08-19 mở, đóng
    # 2026-09-23): trước không LIMIT — khách mua 200 lần kéo 200 đơn + items + product
    # cho MỖI dòng của trang. Mới nhất trước nên cắt đuôi là cắt đơn cũ nhất; UI chỉ
    # cần vài chục đơn gần nhất để nhìn nhịp mua.
    RESALE_HISTORY_MAX_ORDERS_PER_CUSTOMER = 30

    @staticmethod
    def _resale_order_history_by_customer(customer_ids: list[int]) -> dict[int, list[dict]]:
        if not customer_ids:
            return {}
        from django.db.models import F, Window
        from django.db.models.functions import RowNumber

        cap = CustomerResaleService.RESALE_HISTORY_MAX_ORDERS_PER_CUSTOMER
        orders = (
            Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            # ROW_NUMBER() theo khách, mới nhất trước, rồi lọc ≤ cap ngay trong SQL
            # (Django ≥ 4.2 cho phép filter trên window) — LIMIT theo TỪNG khách,
            # không phải LIMIT tổng làm khách đứng sau mất lịch sử.
            .annotate(
                _history_rank=Window(
                    expression=RowNumber(),
                    partition_by=[F("customer_id")],
                    order_by=[F("created_at").desc(), F("id").desc()],
                )
            )
            .filter(_history_rank__lte=cap)
            .select_related("assessment")
            .prefetch_related(
                Prefetch("items", queryset=OrderItem.objects.select_related("product"))
            )
            .order_by("customer_id", "-created_at", "-id")
        )
        grouped: dict[int, list[dict]] = defaultdict(list)
        for order in orders:
            grouped[order.customer_id].append(CustomerResaleService._resale_history_order(order))
        return grouped

    @staticmethod
    def _order_code_label(order: Order) -> str:
        return order.order_code or order.invoice_code or order.shipping_code or f"#{order.id}"

    @staticmethod
    def _product_labels(order: Order) -> list[str]:
        items = list(getattr(order, "items", []).all()) if hasattr(getattr(order, "items", None), "all") else []
        labels = []
        for item in items:
            sku = item.sku or getattr(item.product, "sku", "")
            if sku:
                labels.append(str(sku).strip())
        if labels:
            return labels
        fallback = (order.sku or order.product or "").strip()
        return [fallback] if fallback else []

    @staticmethod
    def _assessment_summary(order: Order) -> dict:
        assessment = getattr(order, "assessment", None)
        if not assessment:
            return {
                "customer_classification": "",
                "sales_status": "",
                "not_closed_reason": "",
                "delivery_business_status": "",
                "post_purchase_rating": "",
                "note": "",
            }
        return {
            "customer_classification": CUSTOMER_CLASSIFICATION_LABELS.get(
                assessment.customer_classification_code,
                assessment.customer_classification_code or "",
            ),
            "sales_status": SALES_STATUS_LABELS.get(assessment.sales_status_code, assessment.sales_status_code or ""),
            "not_closed_reason": NOT_CLOSED_REASON_LABELS.get(
                assessment.not_closed_reason_code,
                assessment.not_closed_reason_code or "",
            ),
            "delivery_business_status": DELIVERY_BUSINESS_STATUS_LABELS.get(
                assessment.delivery_business_status_code,
                assessment.delivery_business_status_code or "",
            ),
            "post_purchase_rating": POST_PURCHASE_RATING_LABELS.get(
                assessment.post_purchase_rating_code,
                assessment.post_purchase_rating_code or "",
            ),
            "note": assessment.internal_note or assessment.post_purchase_feedback_text or "",
        }

    @staticmethod
    def _resale_history_order(order: Order) -> dict:
        return {
            "order_id": order.id,
            "order_code": CustomerResaleService._order_code_label(order),
            "created_at": order.created_at,
            "status": int(order.status),
            "products": CustomerResaleService._product_labels(order),
            "total_amount": str(order.total_after_discount or order.total_amount or Decimal("0")),
            "assessment": CustomerResaleService._assessment_summary(order),
        }

    @staticmethod
    def _resale_row(
        order: Order,
        history: list[dict],
        snapshot: CustomerSegmentSnapshot | None = None,
        *,
        visibility: PhoneVisibility | None = None,
    ) -> dict:
        # FAIL-CLOSED khi thiếu `visibility` (cùng lý lẽ với OrderSerializer).
        visibility = visibility or PhoneVisibility(user_id=None, can_view_full=False)
        followup = getattr(order, "resale_followup", None)
        customer = order.customer
        # Phân khúc + ngày mua gần nhất lấy TỪ snapshot (nguồn sự thật duy nhất,
        # trùng khít với bộ lọc segment/min_days). Khách chưa có snapshot →
        # phân khúc trống, days = None (sẽ được cron rebuild ở chu kỳ kế tiếp).
        segment_code = snapshot.segment_code if snapshot else ""
        last_purchase_at = snapshot.last_purchase_at if snapshot else None
        days_since_last = (timezone.now() - last_purchase_at).days if last_purchase_at else None
        segment_meta = segment_catalog().get(segment_code)
        staff = getattr(order, "staff", None)
        return {
            "order_id": order.id,
            "order_code": CustomerResaleService._order_code_label(order),
            "shipping_code": order.shipping_code,
            "delivered_at": order.updated_at,
            "recipient": {
                "name": order.name or customer.full_name,
                "phone": visibility.apply(
                    order.phone or customer.phone_raw or customer.phone_normalized,
                    order.staff_id,
                    public_source=is_public_phone_source(order.order_source),
                ),
                "address": order.address or order.raw_address,
                "province": order.province,
                "district": order.district,
                "ward": order.ward,
            },
            "customer": {
                "id": customer.id,
                "code": customer.code,
                "full_name": customer.full_name,
                "phone": visibility.apply(
                    customer.phone_raw or customer.phone_normalized,
                    order.staff_id,
                    public_source=is_public_phone_source(order.order_source),
                ),
            },
            "channel": order.channel.name or order.channel.code,
            "sale_staff_id": staff.id if staff else None,
            "sale_staff_name": (staff.display_name or staff.username) if staff else "",
            "segment_code": segment_code,
            "segment_name": segment_meta["name"] if segment_meta else "",
            "segment_care_priority": segment_meta["care_priority"] if segment_meta else "",
            "last_purchase_at": last_purchase_at,
            "days_since_last_purchase": days_since_last,
            "products": CustomerResaleService._product_labels(order),
            "order_value": str(order.total_after_discount or order.total_amount or Decimal("0")),
            "order_assessment": history,
            "contact_1_at": followup.contact_1_at if followup else None,
            "contact_2_at": followup.contact_2_at if followup else None,
            "contact_3_at": followup.contact_3_at if followup else None,
            "contact_logs": followup.contact_logs if followup else [],
            "appointment": (
                {
                    "appointment_at": followup.appointment_at,
                    "channel": followup.appointment_channel,
                    "purpose": followup.appointment_purpose,
                    "note": followup.appointment_note,
                }
                if followup and followup.appointment_at
                else None
            ),
            "resale_orders": followup.resale_orders if followup else [],
        }

    @staticmethod
    def _get_resale_tracking_order(order_id: int) -> Order:
        order = (
            Order.objects.filter(
                id=order_id,
                deleted_at__isnull=True,
                status=DELIVERED_SUCCESS_ORDER_STATUS,
            )
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .first()
        )
        if order is None:
            raise ValueError("Khong tim thay don hang.")
        return order

    @staticmethod
    def update_resale_contact(
        order_id: int,
        contact_no: int,
        *,
        checked: bool,
        channel: str = "",
        result: str = "",
        customer_status: str = "",
        updated_by=None,
    ) -> CustomerResaleFollowUp:
        if contact_no not in {1, 2, 3}:
            raise ValueError("contact_no phai la 1, 2 hoac 3.")
        order = CustomerResaleService._get_resale_tracking_order(order_id)
        followup, _ = CustomerResaleFollowUp.objects.get_or_create(order=order)
        field = f"contact_{contact_no}_at"
        now = timezone.now()
        setattr(followup, field, now if checked else None)
        if checked:
            logs = list(followup.contact_logs or [])
            logs.append(
                {
                    "contact_no": contact_no,
                    "channel": str(channel or "").strip(),
                    "result": str(result or "").strip(),
                    "customer_status": str(customer_status or "").strip(),
                    "created_at": now.isoformat(),
                }
            )
            followup.contact_logs = logs
        followup.updated_by = updated_by
        followup.save(update_fields=[field, "contact_logs", "updated_by", "updated_at"])
        return followup

    @staticmethod
    def update_resale_appointment(order_id: int, *, appointment_at, channel: str = "", purpose: str = "", note: str = "", updated_by=None) -> CustomerResaleFollowUp:
        order = CustomerResaleService._get_resale_tracking_order(order_id)
        followup, _ = CustomerResaleFollowUp.objects.get_or_create(order=order)
        followup.appointment_at = appointment_at
        followup.appointment_channel = str(channel or "").strip()
        followup.appointment_purpose = str(purpose or "").strip()
        followup.appointment_note = str(note or "").strip()
        followup.updated_by = updated_by
        followup.save(
            update_fields=[
                "appointment_at",
                "appointment_channel",
                "appointment_purpose",
                "appointment_note",
                "updated_by",
                "updated_at",
            ]
        )
        # (Re)schedule the Lark reminder so the sale gets nudged at the
        # appointment time. Import locally to avoid an app-init import cycle
        # (orders service ↔ customers service).
        from apps.orders.application.services.sale_reminder_service import (
            schedule_resale_appointment_reminder,
        )

        schedule_resale_appointment_reminder(
            order=order,
            appointment_at=followup.appointment_at,
            actor=updated_by,
            note=" · ".join(
                part
                for part in (followup.appointment_purpose, followup.appointment_channel)
                if part
            ),
        )
        return followup

    @staticmethod
    def create_resale_order_note(order_id: int, *, product: str, quantity: int, order_value, channel: str = "", note: str = "", updated_by=None) -> CustomerResaleFollowUp:
        order = CustomerResaleService._get_resale_tracking_order(order_id)
        followup, _ = CustomerResaleFollowUp.objects.get_or_create(order=order)
        now = timezone.now()
        entries = list(followup.resale_orders or [])
        entries.append(
            {
                "product": str(product or "").strip(),
                "quantity": int(quantity or 1),
                "order_value": str(order_value or "0"),
                "channel": str(channel or "").strip(),
                "note": str(note or "").strip(),
                "created_at": now.isoformat(),
            }
        )
        followup.resale_orders = entries
        followup.updated_by = updated_by
        followup.save(update_fields=["resale_orders", "updated_by", "updated_at"])
        return followup
