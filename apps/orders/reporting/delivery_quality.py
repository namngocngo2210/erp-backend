"""Chất lượng data & giao hàng — Nhóm 3 (data theo kênh, nhà mạng), Nhóm 5a
(kết quả giao theo staff/kênh), Nhóm 6 (lý do bom), Nhóm 7 (tỷ lệ hoàn theo SP).

Tách nguyên trạng từ ``apps/orders/_repositories_reports.py`` — KHÔNG đổi hành
vi. Công thức nhạy cảm giữ nguyên chỗ: "% bom" = bomb/(total − in_delivery)
(MR !1232, FE tự chia), Nhóm 7 dùng rổ ``RETURN_ORDER_STATUSES`` gồm cả mã 12
(MR !1246).
"""
from datetime import timedelta

from django.db.models import Case, CharField, Count, F, Func, Q, Value, When
from django.db.models.functions import Concat, Substr
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.orders.phone_carrier import (
    CARRIER_LABELS,
    CARRIER_PREFIXES,
    UNKNOWN_CARRIER,
    UNKNOWN_CARRIER_LABEL,
    carrier_for_prefix,
)

from .shared import (
    BOMB_ORDER_STATUSES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    DIRTY_SALES_STATUS_CODES,
    IN_DELIVERY_ORDER_STATUSES,
    PENDING_DELIVERY_ORDER_STATUSES,
    PENDING_DELIVERY_THRESHOLD_DAYS,
    RETURN_ORDER_STATUSES,
    RETURNED_ORDER_STATUS,
    SENT_OUT_ORDER_STATUSES,
    UNREACHED_SALES_STATUS_CODES,
    sale_report_channel_ids,
)


class DeliveryQualityReportsMixin:
    """summary_* chất lượng data/giao hàng; ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @staticmethod
    def _fill_missing_channel_rows(rows, sale_channel_ids, zero_fields):
        """Bổ sung dòng 0 cho kênh trong scope nhưng không có đơn nào trong kỳ.

        Các summary theo kênh group từ queryset đơn hàng, nên kênh thuộc phạm vi
        báo cáo sale (có NV Ads gán) mà 0 đơn/0 doanh thu trong kỳ sẽ không có
        dòng nào và biến mất khỏi báo cáo. Dòng 0 xếp cuối theo tên kênh — khớp
        thứ tự sẵn có (``-total, channel__name``).
        """
        present_ids = {row["channel_id"] for row in rows}
        missing_ids = [cid for cid in sale_channel_ids if cid not in present_ids]
        if not missing_ids:
            return rows
        zeros = dict.fromkeys(zero_fields, 0)
        return rows + [
            {
                "channel_id": ch["id"],
                "channel__code": ch["code"],
                "channel__name": ch["name"],
                **zeros,
            }
            for ch in Channel.objects.filter(id__in=missing_ids, deleted_at__isnull=True)
            .order_by("name")
            .values("id", "code", "name")
        ]

    @classmethod
    def summary_data_quality_by_channel(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        # Group 3 ("Chất lượng Data theo kênh") đo độ "sạch" của nguồn lead, tính trên
        # mọi đơn có nguồn (order_source khác null/rỗng), không chỉ riêng landing.
        # Phạm vi kênh đã do _sales_report_orders_queryset() lo (kênh có gán NV Ads);
        # ở đây chỉ thu hẹp thêm theo nguồn lead (order_source).
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        queryset = (
            cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
            .filter(order_source__isnull=False)
            .exclude(order_source="")
        )
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        rows = list(
            queryset.values("channel_id", "channel__code", "channel__name")
            .annotate(
                total=Count("id"),
                unreached=Count(
                    "id", filter=Q(assessment__sales_status_code__in=UNREACHED_SALES_STATUS_CODES)
                ),
                dirty=Count(
                    "id", filter=Q(assessment__sales_status_code__in=DIRTY_SALES_STATUS_CODES)
                ),
            )
            .order_by("-total", "channel__name")
        )
        return cls._fill_missing_channel_rows(
            rows, sale_channel_ids, ("total", "unreached", "dirty")
        )

    @classmethod
    def summary_data_by_carrier(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Số lượng data theo nhà mạng (nhận diện qua đầu số SĐT).

        Cùng scope với các block báo cáo sale khác (kênh có NV Ads gán +
        filter ngày theo created_at). Normalize phone NGAY TRONG SQL
        (bỏ ký tự lạ, ``84…``→``0…``, thêm lại số 0 đầu bị mất) rồi group
        theo 3 số đầu — 1 query aggregate, không kéo từng dòng về app.
        Map đầu số → nhà mạng làm ở Python (``phone_carrier.py``).
        """
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)

        digits = Func(
            F("phone"),
            Value(r"[^0-9]"),
            Value(""),
            Value("g"),
            function="regexp_replace",
            output_field=CharField(),
        )
        rows = (
            queryset.annotate(_digits=digits)
            .annotate(
                _norm=Case(
                    # +84/84xxxxxxxxx → 0xxxxxxxxx
                    When(_digits__regex=r"^84\d{9}", then=Concat(Value("0"), Substr("_digits", 3))),
                    # Số 9 chữ số mất số 0 đầu (thường do Excel) → thêm lại 0.
                    When(_digits__regex=r"^[1-9]\d{8}$", then=Concat(Value("0"), F("_digits"))),
                    default=F("_digits"),
                    output_field=CharField(),
                )
            )
            .annotate(_prefix=Substr("_norm", 1, 3))
            .values("_prefix")
            .annotate(total=Count("id"))
        )

        totals: dict[str, int] = dict.fromkeys(CARRIER_PREFIXES, 0)
        totals[UNKNOWN_CARRIER] = 0
        for row in rows:
            totals[carrier_for_prefix(row["_prefix"] or "")] += row["total"]
        result = [
            {
                "carrier": key,
                "label": CARRIER_LABELS.get(key, UNKNOWN_CARRIER_LABEL),
                "total": total,
            }
            for key, total in totals.items()
        ]
        result.sort(key=lambda item: (-item["total"], item["label"]))
        return result

    @staticmethod
    def _delivery_quality_aggregations():
        # pending-over-threshold uses created_at as proxy (no shipped_at field exists).
        pending_cutoff = timezone.now() - timedelta(days=PENDING_DELIVERY_THRESHOLD_DAYS)
        return {
            "total": Count("id"),
            "sent": Count("id", filter=Q(status__in=SENT_OUT_ORDER_STATUSES)),
            # `returned` chỉ nuôi ô KPI tổng của Nhóm 7 (Nhóm 5a/5b đã chuyển
            # sang cặp bomb/delivered từ 2026-08-15), nên phải cùng rổ với
            # summary_return_rate_by_product — bằng không ô tổng lệch bảng chi
            # tiết ngay bên dưới nó. Trùng giá trị với `bomb` là CỐ Ý: hai chỉ
            # số khác nghĩa, chỉ đang khớp rổ trạng thái.
            "returned": Count("id", filter=Q(status__in=RETURN_ORDER_STATUSES)),
            "delivered": Count("id", filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS)),
            "in_delivery": Count("id", filter=Q(status__in=IN_DELIVERY_ORDER_STATUSES)),
            "bomb": Count("id", filter=Q(status__in=BOMB_ORDER_STATUSES)),
            "pending_over_threshold": Count(
                "id",
                filter=Q(status__in=PENDING_DELIVERY_ORDER_STATUSES, created_at__lt=pending_cutoff),
            ),
        }

    @classmethod
    def _delivery_quality_base_queryset(
        cls,
        date_from: str | None,
        date_to: str | None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        return cls._apply_created_at_date_filters(queryset, date_from, date_to)

    @classmethod
    def summary_delivery_quality_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        queryset = cls._delivery_quality_base_queryset(
            date_from, date_to, sale_channel_ids=sale_channel_ids
        )
        return list(
            queryset.values("staff_id", "staff__username", "staff__display_name")
            .annotate(**cls._delivery_quality_aggregations())
            .order_by("staff__username", "staff_id")
        )

    @classmethod
    def summary_delivery_quality_by_channel(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        # Phạm vi kênh (kênh có gán NV Ads) đã do _sales_report_orders_queryset() lo.
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        queryset = cls._delivery_quality_base_queryset(
            date_from, date_to, sale_channel_ids=sale_channel_ids
        )
        rows = list(
            queryset.values("channel_id", "channel__code", "channel__name")
            .annotate(**cls._delivery_quality_aggregations())
            .order_by("-total", "channel__name")
        )
        return cls._fill_missing_channel_rows(
            rows,
            sale_channel_ids,
            ("total", "sent", "returned", "delivered", "in_delivery", "bomb", "pending_over_threshold"),
        )

    @classmethod
    def summary_bomb_reasons(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        from apps.orders.crm_catalog import BOMB_REASON_TYPE_LABELS

        queryset = cls._delivery_quality_base_queryset(
            date_from, date_to, sale_channel_ids=sale_channel_ids
        ).filter(status=RETURNED_ORDER_STATUS)
        rows = list(
            queryset.values("assessment__bomb_reason_type_code")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        for row in rows:
            code = row.get("assessment__bomb_reason_type_code")
            row["label"] = BOMB_REASON_TYPE_LABELS.get(code, "Chưa phân loại" if not code else code)
        return rows

    @classmethod
    def summary_return_rate_by_product(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Tỷ lệ hoàn theo sản phẩm — % hoàn = đơn hoàn / đơn đã gửi.

        Tử số dùng ``RETURN_ORDER_STATUSES`` (4/5/12/13): tính cả đơn ĐANG trên
        đường hoàn, không chỉ đơn đã hoàn xong. Mọi mã trong rổ này đều nằm
        trong ``SENT_OUT_ORDER_STATUSES`` nên tỷ lệ không thể vượt 100%.

        Gom theo ``OrderItem.product``. Mỗi đơn được tính 1 lần cho mỗi sản phẩm
        nó chứa (đơn nhiều SP xuất hiện ở từng SP). Đơn KHÔNG có dòng sản phẩm
        khớp ``Product`` (SKU không khớp danh mục) bị loại khỏi báo cáo này.
        """
        queryset = cls._delivery_quality_base_queryset(
            date_from, date_to, sale_channel_ids=sale_channel_ids
        ).filter(items__product__isnull=False)
        return list(
            queryset.values(
                "items__product",
                "items__product__sku",
                "items__product__name",
            )
            .annotate(
                total=Count("id", distinct=True),
                sent=Count(
                    "id", filter=Q(status__in=SENT_OUT_ORDER_STATUSES), distinct=True
                ),
                returned=Count(
                    "id", filter=Q(status__in=RETURN_ORDER_STATUSES), distinct=True
                ),
            )
            .order_by("-returned", "items__product__name")
        )
