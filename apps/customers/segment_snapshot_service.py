"""Build/đọc snapshot phân khúc khách hàng (tách classify realtime khỏi request)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Q
from django.utils import timezone

from apps.orders.models import Order, OrderAssessment
from apps.common.business_constants import EXCLUDED_BRANCH_IDS

from . import segmentation
from .models import Customer, CustomerSegmentSnapshot
from .customer_loyalty_service import CustomerLoyaltyService
from .profile_service import CustomerProfileService

_BATCH = 2000

# Lùi mốc nước một khoảng trước khi quét "ai đã đổi". Hai lý do, cả hai đều là
# mất dữ liệu thật nếu bỏ qua:
#   1. Mốc nước = MAX(updated_at) của bảng snapshot, tức thời điểm chu kỳ trước
#      GHI XONG. Đơn nào được sửa TRONG lúc chu kỳ đó đang chạy (chạy vài phút)
#      đã bị đọc bằng giá trị cũ, nhưng updated_at của nó lại nhỏ hơn mốc nước —
#      chu kỳ sau sẽ bỏ qua nó vĩnh viễn.
#   2. `auto_now` lấy giờ của TIẾN TRÌNH APP, không phải của Postgres; lệch đồng
#      hồ vài giây giữa container là chuyện bình thường.
# 30 phút rộng gấp nhiều lần thời gian chạy thật của một chu kỳ (soft_time_limit
# 600s) mà vẫn nhỏ hơn nhịp beat 2h, nên chi phí quét thừa là không đáng kể.
_WATERMARK_OVERLAP = timedelta(minutes=30)


# Các cột nghiệp vụ của snapshot — đúng `update_fields` của bulk_create trừ
# `updated_at`. Dùng để nhận ra dòng không đổi ở đường full (xem `rebuild`).
_COMPARE_FIELDS = (
    "segment_code",
    "total_orders",
    "total_spent",
    "last_purchase_at",
    "has_problem",
    "latest_sales_status_code",
    "is_problem_order_customer",
)


class SegmentSnapshotService:
    @staticmethod
    def rebuild(customer_ids: list[int] | None = None, *, skip_unchanged: bool = False) -> int:
        """Tính lại snapshot. Trả về số dòng THỰC SỰ được ghi.

        ``skip_unchanged=True`` bỏ qua những dòng đã có giá trị y hệt bản vừa
        tính. Chỉ dùng cho đường FULL (lưới an toàn hằng ngày): đo prod
        2026-08-17 cho thấy lần full ghi đè cả 755k dòng để thay đổi vài trăm
        dòng, và đó là toàn bộ nguồn dead tuple làm heap phồng 6,4×.

        Mặc định TẮT, và đường tăng dần phải giữ nguyên mặc định đó: mốc nước là
        ``MAX(updated_at)`` của chính bảng này, nên một chu kỳ không ghi gì sẽ để
        mốc nước đứng im và làm cửa sổ quét của các chu kỳ sau mở rộng dần.
        """
        if customer_ids is None:
            customer_ids = list(
                Customer.objects.filter(deleted_at__isnull=True).values_list("id", flat=True)
            )
        total = 0
        for i in range(0, len(customer_ids), _BATCH):
            batch = customer_ids[i : i + _BATCH]
            total += SegmentSnapshotService._rebuild_batch(batch, skip_unchanged=skip_unchanged)
        return total

    @staticmethod
    def last_rebuild_at():
        """Thời điểm chu kỳ gần nhất ghi xong, hoặc ``None`` nếu bảng còn rỗng.

        Cố ý suy từ chính bảng snapshot thay vì nuôi một bảng "trạng thái job"
        riêng: mốc nước và dữ liệu nó mô tả luôn đi cùng nhau, không có cửa sổ
        nào để hai thứ lệch pha (ghi snapshot xong rồi chết trước khi lưu mốc).
        Cũng KHÔNG lưu ở Redis — cache giờ chạy `allkeys-lru` và có thể bị đẩy
        ra bất cứ lúc nào, mất mốc nước là âm thầm bỏ qua cả một khoảng thời gian.
        """
        return CustomerSegmentSnapshot.objects.aggregate(v=Max("updated_at"))["v"]

    @staticmethod
    def affected_customer_ids(since) -> list[int]:
        """Id khách cần tính lại vì có thay đổi từ ``since`` trở đi.

        Ba nguồn, thiếu nguồn nào cũng ra snapshot sai:
          - ``orders``: đơn mới/sửa/xoá mềm đổi total_orders, total_spent, ngày mua
            cuối và cả `is_problem_order_customer` (theo `status`).
          - ``order_assessments``: phân khúc phụ thuộc `sales_status_code`,
            `delivery_business_status_code`, `post_purchase_rating_code` — sửa
            đánh giá KHÔNG chạm `orders.updated_at`, bỏ nguồn này là snapshot
            đứng im trong khi màn hình đánh giá đã đổi.
          - ``customers``: khách mới (auto_now đặt cả lúc INSERT) và khách vừa
            được khôi phục/đổi thông tin.
        """
        ids: set[int] = set()
        ids.update(
            Order.objects.filter(updated_at__gte=since, customer_id__isnull=False)
            .values_list("customer_id", flat=True)
            .distinct()
        )
        ids.update(
            OrderAssessment.objects.filter(
                updated_at__gte=since, order__customer_id__isnull=False
            )
            .values_list("order__customer_id", flat=True)
            .distinct()
        )
        ids.update(
            Customer.objects.filter(deleted_at__isnull=True, updated_at__gte=since)
            .values_list("id", flat=True)
        )
        if not ids:
            return []
        # Lọc lại qua bảng customers: hai nguồn đầu lấy `customer_id` từ đơn nên
        # có thể kéo theo khách đã xoá mềm, mà đường rebuild toàn bộ thì loại hẳn
        # nhóm đó. Không lọc là hai đường cho ra hai tập khác nhau — đúng loại lệch
        # chỉ lộ ra sau nhiều tuần, khi báo cáo full và báo cáo tăng dần vênh nhau.
        return list(
            Customer.objects.filter(id__in=ids, deleted_at__isnull=True)
            .order_by("id")
            .values_list("id", flat=True)
        )

    @staticmethod
    def rebuild_incremental() -> tuple[int, bool]:
        """Tính lại snapshot cho riêng phần đã đổi. Trả về ``(số dòng, đã_full)``.

        Rơi về rebuild toàn bộ khi chưa có mốc nước (bảng rỗng / lần chạy đầu).
        """
        since = SegmentSnapshotService.last_rebuild_at()
        if since is None:
            return SegmentSnapshotService.rebuild(), True
        ids = SegmentSnapshotService.affected_customer_ids(since - _WATERMARK_OVERLAP)
        if not ids:
            return 0, False
        return SegmentSnapshotService.rebuild(ids), False

    @staticmethod
    def _unchanged_ids(objs: list[CustomerSegmentSnapshot]) -> set[int]:
        """Id của các snapshot đã có trong DB với mọi cột nghiệp vụ y hệt bản vừa tính.

        Một SELECT cho cả lô 2000 — rẻ hơn nhiều so với 2000 lượt UPDATE mà kết
        quả không đổi gì.
        """
        existing = {
            row["customer_id"]: row
            for row in CustomerSegmentSnapshot.objects.filter(
                customer_id__in=[o.customer_id for o in objs]
            ).values("customer_id", *_COMPARE_FIELDS)
        }
        return {
            obj.customer_id
            for obj in objs
            if (old := existing.get(obj.customer_id)) is not None
            and all(old[f] == getattr(obj, f) for f in _COMPARE_FIELDS)
        }

    @staticmethod
    def _rebuild_batch(ids: list[int], *, skip_unchanged: bool = False) -> int:
        metrics = CustomerProfileService._metrics_map(ids)
        problem_ids = CustomerLoyaltyService._customers_with_problem_signal(ids)
        latest_sales = CustomerProfileService._latest_sales_map(ids)

        # Compute is_problem_order_customer using EXACTLY the same criteria as
        # CustomerBehaviorReportService._problem_count (unfiltered variant):
        # deleted orders excluded, EXCLUDED_BRANCH_IDS excluded, problem signals OR'd.
        problem_order_customers = set(
            Order.objects.filter(customer_id__in=ids, deleted_at__isnull=True)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .filter(
                Q(status__in=segmentation.PROBLEM_ORDER_STATUSES)
                | Q(assessment__sales_status_code__in=segmentation.PROBLEM_SALES_STATUS_CODES)
                | Q(assessment__delivery_business_status_code__in=segmentation.PROBLEM_DELIVERY_STATUS_CODES)
                | Q(assessment__post_purchase_rating_code__in=segmentation.PROBLEM_POST_PURCHASE_RATING_CODES)
            )
            .values_list("customer_id", flat=True)
            .distinct()
        )

        now = timezone.now()
        objs = []
        for cid in ids:
            m = metrics.get(cid, {})
            total_orders = m.get("total_orders", 0) or 0
            last = m.get("last_purchase_at")
            days = (now - last).days if last else None
            code = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days,
                has_problem=cid in problem_ids,
                latest_sales_status_code=latest_sales.get(cid),
            )
            objs.append(CustomerSegmentSnapshot(
                customer_id=cid, segment_code=code, total_orders=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                last_purchase_at=last, has_problem=cid in problem_ids,
                latest_sales_status_code=latest_sales.get(cid),
                is_problem_order_customer=cid in problem_order_customers,
            ))
        if skip_unchanged:
            unchanged = SegmentSnapshotService._unchanged_ids(objs)
            objs = [o for o in objs if o.customer_id not in unchanged]
        if not objs:
            return 0

        CustomerSegmentSnapshot.objects.bulk_create(
            objs, update_conflicts=True, unique_fields=["customer"],
            update_fields=[*_COMPARE_FIELDS, "updated_at"],
        )
        return len(objs)

    @staticmethod
    def segment_map(customer_ids: list[int]) -> dict[int, str]:
        if not customer_ids:
            return {}
        rows = (CustomerSegmentSnapshot.objects
                .filter(customer_id__in=customer_ids)
                .values_list("customer_id", "segment_code"))
        return dict(rows)
