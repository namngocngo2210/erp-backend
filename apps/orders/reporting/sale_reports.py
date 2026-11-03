"""Báo cáo Sale — Nhóm 1/2 (tỷ lệ chốt, năng lực), Nhóm 9 (doanh thu chốt),
Nhóm 10 (theo sheet Landing), báo cáo theo ngày và doanh thu Sale FB.

Tách nguyên trạng từ ``apps/orders/_repositories_reports.py`` — KHÔNG đổi hành
vi. Mọi method là classmethod: các helper nền (``_sales_report_orders_queryset``,
``_apply_created_at_date_filters``, ``_base_orders_queryset``) resolve qua MRO
khi mixin được ghép vào ``OrderRepository`` (xem ``reporting/__init__.py``).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Case,
    CharField,
    Count,
    DateTimeField,
    DecimalField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest, Least, TruncDate

from apps.orders.models import OrderJourneyEvent
from apps.users.constants import DepartmentID

from .shared import (
    CLOSED_REVENUE_SALES_STATUS_CODES,
    CLOSED_SALES_STATUS_CODES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    DIRTY_SALES_STATUS_CODES,
    LEAD_TIME_CAP,
    NOT_BUYING_SALES_STATUS_CODE,
    NURTURE_SALES_STATUS_CODES,
    NURTURE_SALES_STATUS_CODES_FOR_CAPACITY,
    RETURN_ORDER_STATUSES,
    SALE_FB_CHANNEL_CODE,
    SALES_STATUS_EVENT_TYPE,
    SALE_SOURCE_REPORT_ORDER_SOURCES,
    STAFF_HANDOVER_EVENT_TYPES,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    UNREACHABLE_SALES_STATUS_CODES,
    UNREACHED_SALES_STATUS_CODES,
    WANTS_STORE_VISIT_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
)


def _duration_seconds(value) -> int:
    """Số giây (làm tròn) của một tổng DurationField.

    Postgres trả ``timedelta``; giữ nhánh số để không vỡ nếu chạy trên backend
    lưu duration bằng micro giây.
    """
    if value is None:
        return 0
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    return int(value // 1_000_000)


class SaleReportsMixin:
    """summary_* báo cáo Sale; ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @classmethod
    def summary_landing_delivered_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids).filter(status=3)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        return list(
            queryset.values(
                "staff_id",
                "staff__username",
                "staff__display_name",
            )
            .annotate(total=Count("id"))
            .order_by("-total", "staff__username")
        )

    @classmethod
    def summary_close_rate_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        # Khách bị bệnh nền không thể mua, khách muốn đến cửa hàng thì chốt ngoài
        # tầm sale online — loại hẳn khỏi tỷ lệ chốt. Giữ khớp với mẫu số Nhóm 1
        # mà FE tự dựng từ `sale_performance_by_staff`.
        queryset = queryset.exclude(
            assessment__sales_status_code__in=(
                UNDERLYING_CONDITION_SALES_STATUS_CODE,
                WANTS_STORE_VISIT_SALES_STATUS_CODE,
            )
        )
        spam_filter = Q(assessment__sales_status_code="spam") | Q(assessment__customer_classification_code="spam")
        return list(
            queryset.values(
                "staff_id",
                "staff__username",
                "staff__display_name",
            )
            .annotate(
                total=Count("id"),
                closed=Count("id", filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)),
                spam=Count("id", filter=spam_filter),
                unreachable=Count(
                    "id", filter=Q(assessment__sales_status_code__in=UNREACHABLE_SALES_STATUS_CODES)
                ),
            )
            .order_by("staff__username", "staff_id")
        )

    @classmethod
    def summary_sale_performance_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)

        # Per-order flag: did this order ever pass through a nurture status?
        # Used as the denominator of follow-up conversion + to identify closed
        # orders that came back from a nurture state.
        nurture_history = OrderJourneyEvent.objects.filter(
            order_id=OuterRef("pk"),
            event_type=SALES_STATUS_EVENT_TYPE,
            to_code__in=NURTURE_SALES_STATUS_CODES,
        )

        # Group 1 (Tỷ lệ chốt): `total` = all orders in sale scope, FE trừ tiếp
        # underlying_condition + wants_store_visit.
        # Group 2 (Năng lực Sale, "Data tiếp cận"): `total_assessed` = orders with
        # assessment.sales_status_code, minus no_answer_6 / unreachable / spam /
        # duplicate / wrong_number / poor_quality_data / warranty / wants_store_visit.
        has_assessment = Q(assessment__sales_status_code__isnull=False) & ~Q(
            assessment__sales_status_code=""
        )
        rows = list(
            queryset.annotate(had_nurture=Exists(nurture_history))
            .values("staff_id", "staff__username", "staff__display_name")
            .annotate(
                total=Count("id"),
                total_assessed=Count("id", filter=has_assessment),
                closed=Count(
                    "id", filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)
                ),
                not_buying=Count(
                    "id", filter=Q(assessment__sales_status_code=NOT_BUYING_SALES_STATUS_CODE)
                ),
                # Rổ nurture của Nhóm 2 KHÔNG gồm "đến cửa hàng" — đơn đó đã bị
                # trừ khỏi mẫu số nên cũng phải rời tử số.
                nurture=Count(
                    "id",
                    filter=Q(
                        assessment__sales_status_code__in=NURTURE_SALES_STATUS_CODES_FOR_CAPACITY
                    ),
                ),
                unreached=Count(
                    "id", filter=Q(assessment__sales_status_code__in=UNREACHED_SALES_STATUS_CODES)
                ),
                dirty=Count(
                    "id", filter=Q(assessment__sales_status_code__in=DIRTY_SALES_STATUS_CODES)
                ),
                warranty=Count(
                    "id", filter=Q(assessment__sales_status_code=WARRANTY_SALES_STATUS_CODE)
                ),
                # FE trừ khỏi mẫu số "% chốt" (Nhóm 1) — xem CloseRateSection.
                underlying_condition=Count(
                    "id",
                    filter=Q(
                        assessment__sales_status_code=UNDERLYING_CONDITION_SALES_STATUS_CODE
                    ),
                ),
                # FE trừ khỏi mẫu số của CẢ Nhóm 1 (`total`) lẫn Nhóm 2
                # (`total_assessed`) — xem CloseRateSection / dashboard-utils.
                wants_store_visit=Count(
                    "id",
                    filter=Q(
                        assessment__sales_status_code=WANTS_STORE_VISIT_SALES_STATUS_CODE
                    ),
                ),
                revenue=Sum(
                    "total_after_discount",
                    filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
                # Doanh thu CHỐT: tiền của các đơn ở trạng thái chốt, không phụ
                # thuộc kết quả giao hàng — tính cả đơn sau đó bị hoàn. Đơn huỷ
                # đã bị loại ở _base_orders_queryset() nên không lọc thêm.
                # Khác `revenue` phía trên (chỉ đơn giao thành công, Nhóm 4).
                closed_revenue=Sum(
                    "total_after_discount",
                    filter=Q(
                        assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES
                    ),
                ),
                # Số đơn tương ứng với closed_revenue — cùng bộ lọc để trong bảng
                # Nhóm 9 tiền và số đơn không lệch tiêu chí. Luôn ≤ `closed` ở trên
                # (`closed` có tính thêm "đã chốt, hẹn ngày").
                closed_revenue_orders=Count(
                    "id",
                    filter=Q(
                        assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES
                    ),
                ),
                delivered_count=Count(
                    "id",
                    filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
                nurture_total_ever=Count("id", filter=Q(had_nurture=True)),
                nurture_followup_closed=Count(
                    "id",
                    filter=Q(had_nurture=True)
                    & Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
            )
            .order_by("staff__username", "staff_id")
        )
        for row in rows:
            row["revenue"] = str(row["revenue"] or Decimal("0"))
            row["closed_revenue"] = str(row["closed_revenue"] or Decimal("0"))
        return rows

    @classmethod
    def summary_sale_daily_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Một hàng cho mỗi cặp (ngày tạo đơn, nhân viên sale).

        Nguồn của cả hai tab "Báo cáo theo sale" (FE gộp theo nhân viên) và "Báo
        cáo chi tiết theo sale" (FE lọc 1 nhân viên rồi trải theo ngày) — một
        payload dùng chung để không phải gọi hai endpoint.

        Ba mốc doanh thu (spec 2026-08-04-landing-confirmed-revenue, sửa đổi
        cùng ngày theo yêu cầu chủ sở hữu nghiệp vụ):
        - ``total_revenue``: tiền các đơn ĐÃ CHỐT / ĐÃ LÊN ĐƠN — không còn tính
          data chưa chốt (đơn huỷ vẫn bị loại sẵn ở ``_base_orders_queryset``).
        - ``confirmed_revenue``: tiền khách TỰ đăng ký trên landing lúc đơn đổ về
          (snapshot ``landing_confirmed_amount``, cắt trần bằng ``Least`` theo
          giá trị cuối để ca down-sell không sinh số âm).
        - ``closed_revenue``: phần sale tư vấn thêm = giá trị cuối − snapshot.

        CẢ BA cột tiền dùng CÙNG tập đơn 4 mã ``CLOSED_SALES_STATUS_CODES``
        (phân biệt 3-mã/4-mã trước đây của tab này đã bỏ), nên bảng tự cân:
        ``total = confirmed + closed``. ``orders`` cũng dùng tập đó để tỉ lệ chốt
        (orders / data_count) và AOV ((confirmed + closed) / orders, tính ở FE)
        không lệch tiêu chí; ``data_count`` vẫn đếm MỌI data nhận trong kỳ.

        Lead time xử lý data (2026-08-24): ``lead_time_seconds`` /
        ``lead_time_orders`` là TỔNG và SỐ ĐƠN, không phải trung bình — FE chia
        sau khi đã cộng, nên gộp theo nhân viên (tab tổng hợp) hay theo ngày (tab
        chi tiết) đều ra đúng một con số. Trả sẵn trung bình thì cộng ngang các
        hàng sẽ sai vì mỗi hàng có mẫu số khác nhau.

        Mốc GỐC là lúc data đến tay sale (``STAFF_HANDOVER_EVENT_TYPES``), KHÔNG
        phải ``created_at`` — xem giải thích ở hằng số đó. Hàng vẫn xếp theo NGÀY
        TẠO đơn (``d``) như mọi cột khác của bảng, chỉ phép đo đổi mốc.

        ``queue_wait_seconds`` là chặng TRƯỚC đó: data nằm chờ được chia. Dùng
        CHUNG mẫu số ``lead_time_orders`` nên hai cột cộng lại ra đúng tổng thời
        gian từ lúc đơn đổ về tới lúc xử lý (trừ ca cả hai chặng đều đụng trần
        ``LEAD_TIME_CAP`` — trần áp cho từng chặng một cách độc lập).
        """
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        # Snapshot bị cắt trần về giá trị cuối (Least) để ca down-sell không sinh
        # số âm; nhờ đó confirmed + closed luôn = SUM(total_after_discount) của
        # đúng tập đơn chốt.
        confirmed_expr = Least(F("landing_confirmed_amount"), F("total_after_discount"))
        upsell_expr = ExpressionWrapper(
            F("total_after_discount") - confirmed_expr,
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        # Mốc đầu tiên sale nhập kết quả tác nghiệp. Index
        # ord_journey_ord_type_cr_idx (order, event_type, created_at) phủ đúng
        # subquery này nên chỉ tốn một index scan mỗi đơn.
        first_touch_at = Subquery(
            OrderJourneyEvent.objects.filter(
                order_id=OuterRef("pk"),
                event_type=SALES_STATUS_EVENT_TYPE,
            )
            .order_by("created_at")
            .values("created_at")[:1]
        )
        # Mốc data ĐẾN TAY sale: lần bàn giao GẦN NHẤT còn hiệu lực tại thời điểm
        # tác nghiệp (created_at <= first_touch_at), không phải lần chia đầu tiên
        # — đơn chuyển tay giữa chừng phải tính từ lúc người ĐANG cầm nhận nó.
        # Lấy `-created_at` LIMIT 1 nên vẫn là một index scan.
        handover_at = Subquery(
            OrderJourneyEvent.objects.filter(
                order_id=OuterRef("pk"),
                event_type__in=STAFF_HANDOVER_EVENT_TYPES,
                created_at__lte=OuterRef("first_touch_at"),
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        # Không có event bàn giao nào thì rơi về lúc đơn đổ về: đơn tạo tay đã có
        # sẵn sale, và đơn cũ có trước khi hệ thống ghi event chia, đều không có
        # mốc nào tốt hơn.
        start_at = Coalesce(
            F("handover_at"), F("created_at"), output_field=DateTimeField()
        )
        # Thời gian data NẰM CHỜ CHIA: từ lúc đơn đổ về tới lúc đến tay sale.
        # Đơn không có mốc bàn giao -> start_at = created_at -> chờ 0, đúng nghĩa
        # "đơn tạo tay đã có sẵn sale".
        queue_wait_expr = Greatest(
            Value(timedelta(0), output_field=DurationField()),
            Least(
                ExpressionWrapper(
                    start_at - F("created_at"), output_field=DurationField()
                ),
                Value(LEAD_TIME_CAP, output_field=DurationField()),
                output_field=DurationField(),
            ),
            output_field=DurationField(),
        )
        # Greatest(0, ...) chặn dữ liệu backfill có event ghi lùi trước mốc bàn
        # giao kéo tổng xuống âm; Least(..., CAP) cắt trần đuôi dài.
        lead_time_expr = Greatest(
            Value(timedelta(0), output_field=DurationField()),
            Least(
                ExpressionWrapper(
                    F("first_touch_at") - start_at, output_field=DurationField()
                ),
                Value(LEAD_TIME_CAP, output_field=DurationField()),
                output_field=DurationField(),
            ),
            output_field=DurationField(),
        )
        # BẮT BUỘC lọc null: trong Postgres LEAST/GREATEST BỎ QUA đối số NULL, nên
        # đơn chưa ai đụng tới sẽ rơi ra đúng mức trần thay vì bị loại khỏi Sum.
        touched_q = Q(first_touch_at__isnull=False)
        rows = list(
            queryset.annotate(first_touch_at=first_touch_at)
            .annotate(handover_at=handover_at, d=TruncDate("created_at"))
            .values("d", "staff_id", "staff__username", "staff__display_name")
            .annotate(
                data_count=Count("id"),
                lead_time_total=Sum(lead_time_expr, filter=touched_q),
                lead_time_orders=Count("id", filter=touched_q),
                # CỐ Ý dùng chung `touched_q` (và do đó chung mẫu số
                # `lead_time_orders`) với lead time: hai cột đo hai chặng LIÊN TIẾP
                # của cùng một tập đơn nên cộng lại đúng bằng tổng thời gian từ
                # lúc đơn đổ về tới lúc xử lý. Tách mẫu số là hai cột hết cộng được.
                queue_wait_total=Sum(queue_wait_expr, filter=touched_q),
                orders=Count(
                    "id",
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
                total_revenue=Sum(
                    "total_after_discount",
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
                closed_revenue=Sum(
                    upsell_expr,
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
                confirmed_revenue=Sum(
                    confirmed_expr,
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
            )
            .order_by("d", "staff__username", "staff_id")
        )
        result: list[dict] = []
        for row in rows:
            day = row.pop("d")
            if day is None:
                # Đơn không có created_at không thể xếp vào ngày nào — bỏ qua thay
                # vì dồn vào một ngày giả làm lệch bảng chi tiết.
                continue
            row["date"] = day.isoformat()
            for field in ("total_revenue", "closed_revenue", "confirmed_revenue"):
                row[field] = str(row[field] or Decimal("0"))
            row["lead_time_seconds"] = _duration_seconds(row.pop("lead_time_total"))
            row["queue_wait_seconds"] = _duration_seconds(row.pop("queue_wait_total"))
            row["lead_time_orders"] = row["lead_time_orders"] or 0
            result.append(row)
        return result

    @classmethod
    def summary_closed_revenue_by_staff_sheet(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Nhóm 10 — một hàng cho mỗi cặp (nhân viên sale, sheet Landing).

        Trục cột là 3 tab của trang Landing (``sheet1`` Landing xác nhận đơn /
        ``sheet2`` Messenger xác nhận đơn / ``sheet3`` Tư vấn), phân bằng ĐÚNG
        ``sheet_rules.sheet_q`` mà list view dùng — báo cáo và màn hình không
        thể lệch nhau. Vì Sheet 3 dính theo cờ bất biến ``has_address``, cột Tư
        vấn chính là doanh thu chốt được từ data đổ về không có địa chỉ.

        Trả hàng PHẲNG; FE tự dựng ma trận sale × sheet. Tối đa ~25 sale × 3
        sheet = 75 hàng, một query group-by.

        Chỉ nhìn ``SALE_SOURCE_REPORT_ORDER_SOURCES`` (đúng 4 nguồn của
        ``SHEET3_SOURCES``) — điều kiện này tự loại luôn đơn có ``order_source``
        NULL/rỗng, nên tổng của Nhóm 10 CỐ Ý nhỏ hơn Nhóm 9 đúng bằng phần đơn
        sàn / KOC / chưa gắn nguồn.

        Bốn số, tất cả tính trên cùng tập "đơn đã chốt"
        (``CLOSED_REVENUE_SALES_STATUS_CODES`` — đã chốt / hoả tốc / đã lên đơn,
        KHÔNG gồm "đã chốt, hẹn ngày", cùng tiêu chí Nhóm 9):

        - ``closed_orders`` / ``closed_revenue``: số đơn chốt và tổng tiền của
          chính tập đơn đó, kể cả đơn sau này bị hoàn.
        - ``returned_orders``: trong tập trên, bao nhiêu đơn rơi vào rổ hoàn
          ``RETURN_ORDER_STATUSES`` (4/5/12/13) — cả đơn đang trên đường hoàn,
          không chỉ đơn đã hoàn xong.
        - ``net_revenue``: tiền của các đơn đã GIAO THÀNH CÔNG (``status=3``)
          trong tập đó — đúng nghĩa cột "DThu giao thành công" trên màn hình.
          KHÔNG phải ``closed_revenue`` trừ đơn hoàn: cách cũ (đến 2026-09-01)
          giữ lại cả đơn còn trong kho / đang đi đường / chờ giao lại, nên ở kỳ
          gần đây — lúc phần lớn đơn chưa có kết quả giao — cột này phồng gấp
          nhiều lần doanh thu thật (vd. 16 đơn giao TC ~18tr bị hiện ~99tr).
          Vì vậy ``closed_revenue - net_revenue`` KHÔNG còn bằng tiền đơn hoàn:
          phần chênh gồm cả đơn chưa có kết quả giao.

        Đơn đã huỷ đã bị ``_base_orders_queryset`` loại, không cần điều kiện thêm.
        """
        from apps.orders.sheet_rules import sheet_q

        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        queryset = queryset.filter(order_source__in=SALE_SOURCE_REPORT_ORDER_SOURCES)
        # sheet3 xét TRƯỚC: ba Q rời nhau nên thứ tự không đổi kết quả, nhưng
        # đặt vậy để đọc khớp thứ tự ưu tiên nghiệp vụ (mirror classify_sheet).
        sheet_case = Case(
            When(sheet_q("sheet3"), then=Value("sheet3")),
            When(sheet_q("sheet2"), then=Value("sheet2")),
            When(sheet_q("sheet1"), then=Value("sheet1")),
            default=Value(""),
            output_field=CharField(),
        )
        closed_q = Q(assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES)
        returned_q = closed_q & Q(status__in=RETURN_ORDER_STATUSES)
        # Doanh thu giao thành công = CHỈ đơn đã giao xong (status=3), cùng
        # tiêu chí đơn giao TC của Nhóm 4. Lọc "khác rổ hoàn" như trước LÀM LỌT
        # cả đơn chưa ra khỏi kho / đang đi đường vào doanh thu.
        delivered_q = closed_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
        rows = list(
            queryset.annotate(sheet=sheet_case)
            .values(
                "staff_id",
                "staff__username",
                "staff__display_name",
                "sheet",
            )
            .annotate(
                closed_orders=Count("id", filter=closed_q),
                closed_revenue=Sum("total_after_discount", filter=closed_q),
                returned_orders=Count("id", filter=returned_q),
                net_revenue=Sum("total_after_discount", filter=delivered_q),
            )
            .order_by("staff__username", "staff_id", "sheet")
        )
        for row in rows:
            for field in ("closed_revenue", "net_revenue"):
                row[field] = str(row[field] or Decimal("0"))
            for field in ("closed_orders", "returned_orders"):
                row[field] = row[field] or 0
        return rows

    @classmethod
    def summary_sale_fb_revenue_by_staff(cls, date_from: str | None = None, date_to: str | None = None):
        # Sale FB là channel riêng (id 24), không nằm SalesReportChannel.ALL.
        # Build queryset từ base + staff Sales (không qua _sales_report_orders_queryset
        # để tránh conflict với channel_id__in filter).
        # NOTE: bỏ filter staff__role=STAFF cũ; staff__department_id=SALES đã đủ
        # phân định nhân viên sale (admin không gán vào phòng Sales).
        queryset = cls._base_orders_queryset().filter(
            channel__code=SALE_FB_CHANNEL_CODE,
            assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES,
            staff__department_id=DepartmentID.SALES,
            staff__deleted_at__isnull=True,
        )
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        rows = list(
            queryset.values(
                "staff_id",
                "staff__username",
                "staff__display_name",
            )
            .annotate(
                closed_orders=Count("id"),
                revenue=Sum("total_after_discount"),
            )
            .order_by("-revenue", "staff__username")
        )
        for row in rows:
            row["revenue"] = str(row["revenue"] or Decimal("0"))
        total_revenue = sum((Decimal(row["revenue"]) for row in rows), Decimal("0"))
        total_orders = sum(row["closed_orders"] for row in rows)
        return {
            "by_staff": rows,
            "team_revenue": str(total_revenue),
            "team_closed_orders": total_orders,
        }
