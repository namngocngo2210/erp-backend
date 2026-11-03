"""Báo cáo tổng (``summary_overview``) — 4 rổ doanh thu theo trạng thái sale
(MR !1216), chia nhóm kênh bán nội/ngoại sàn, phân bổ địa lý, chi phí Ads.

Tách nguyên trạng từ ``apps/orders/_repositories_reports.py`` — KHÔNG đổi
hành vi.
"""
from decimal import Decimal

from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    Exists,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import ExtractHour, TruncDate

from apps.orders.contracts.ads_spend_port import AdsSpendSource, ads_spend_provider
from apps.orders.models import Order, OrderItem
from apps.orders.vietnam_regions import (
    REGION_LABELS,
    UNKNOWN_PROVINCE_LABEL,
    UNKNOWN_REGION,
    region_of,
    resolve_province,
)

from .shared import (
    CANCELLED_ORDER_Q,
    DELIVERED_SUCCESS_ORDER_STATUS,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    LEAD_ORDER_Q,
    MARKETPLACE_ORDER_Q,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    SALE_CHANNEL_CASE_BRANCHES,
    SALE_CHANNEL_FACEBOOK,
    SALE_CHANNEL_GROUP_FILTERS,
    SALE_CHANNEL_MARKETPLACE,
    SALE_CHANNEL_OFF_MARKETPLACE,
    SALE_CHANNEL_OFF_OTHER,
    SALE_CHANNEL_WEB,
    facebook_ads_cost_multiplier,
    rolled_sale_channel,
    sale_channel_group_filter,
    sale_channel_groups_for,
    _parse_report_date,
)


class OverviewReportMixin:
    """``summary_overview``; ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @classmethod
    def summary_overview(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        product_ids: list[int] | None = None,
        sale_channel: str | None = None,
        sale_channel_group: str | None = None,
    ) -> dict:
        """Báo cáo tổng toàn công ty theo khoảng ngày tạo đơn.

        Tập đơn cơ sở: chưa xóa, loại chi nhánh nội bộ + kho Đại Mỗ, loại clone
        khôi phục còn sống, MỌI kênh/nguồn — khác các summary_ads_* vốn giới hạn
        scope kênh Ads.

        DOANH THU = MỌI ĐƠN ĐÃ LÊN HÓA ĐƠN KIOTVIET, TRỪ ĐƠN HỦY (user chốt
        2026-08-20): ``~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q``. Tổng đó
        chia đúng 4 rổ RỜI NHAU theo trạng thái giao hàng — giao thành công (3),
        chờ giao (``AWAITING_DELIVERY_ORDER_STATUSES``), hoàn
        (``RETURN_ORDER_STATUSES``), rủi ro hoàn (``RETURN_RISK_ORDER_STATUSES``)
        — nên 4 rổ cộng lại LUÔN bằng tổng, cả tiền lẫn số đơn. Mẫu số của mọi ô
        % trên FE là tổng này.

        Trước đó phạm vi gác bằng TRẠNG THÁI SALE (``OVERVIEW_CLOSED_SCOPE_Q``:
        4 mã chốt, HOẶC đã có hóa đơn mà chưa gắn trạng thái). Bỏ đi vì hai lẽ:
        doanh thu không nên phụ thuộc việc sale có gắn trạng thái hay chưa, và
        đơn có hóa đơn mang trạng thái ngoài 4 mã chốt (vd "khách bom hàng") bị
        loại oan. Đo prod 20/08 trước khi đổi: chênh −35 đơn ≈ −58 triệu trong
        tháng 8 (−0,9%) — phần rớt ra là lead sale đã chốt nhưng chưa kịp lên
        hóa đơn. KHÔNG cần thêm điều kiện ``status != 0``: đơn đã lên hóa đơn
        luôn mang mã trạng thái thật của KiotViet, prod tháng 8 có 0 đơn như vậy.

        Chi phí Ads: HAI ô rời — ``ads_cost_facebook`` (hệ số đổi theo mốc: ×1.08
        nếu kỳ xem bắt đầu trong tháng 8/2026, ×1.1 cho trước đó và từ 01/09/2026
        trở đi — xem ``facebook_ads_cost_multiplier``) và
        ``ads_cost_google`` (×1.08 — cùng hệ số hiển thị với Báo cáo Ads) của
        TOÀN BỘ ad account theo ``spend.date``, không giới hạn theo kênh gán NV;
        ``ads_cost`` là tổng hai ô, giữ cho consumer cũ. Phạm vi đang xem phải là
        NGOẠI SÀN thì mới có số, và chọn kênh lá thì thu hẹp tiếp về đúng kênh đó
        (user chốt 2026-08-24): ``facebook`` → chỉ ô FB, ``web`` → chỉ ô Google,
        ``off_other`` (Resale/KOC/quầy) → cả hai về 0. Đây là tiền chi ra, KHÔNG
        tham gia phép cộng 4 rổ.

        ``sale_channel`` — ô "NGUỒN" trên trang: nội sàn (Shopee/TikTok) hoặc
        ngoại sàn (phần còn lại).

        ``sale_channel_group`` — ô "KÊNH BÁN" (thêm 2026-08-24): lọc sâu thêm một
        tầng, giá trị lấy từ chính danh sách nhóm mà biểu đồ đang hiển thị
        (``sale_channel_groups_for``) nên bộ lọc không thể lệch với dữ liệu. Nhóm
        không thuộc tầng đang chọn bị bỏ qua. Khi có nhóm, cả 3 donut lẫn biểu đồ
        diễn biến co lại còn ĐÚNG nhóm đó — "biểu đồ luôn là dữ liệu đang lọc".

        ``product_ids``: lọc theo sản phẩm. Khi có, MỌI số tiền trên trang là
        tiền CỦA RIÊNG các sản phẩm đó (tổng ``OrderItem.line_total`` khớp sản
        phẩm), không phải giá trị cả đơn — đơn mua kèm SP khác chỉ đóng góp phần
        của SP được lọc, nên cộng số liệu của mọi SP lại đúng bằng tổng. Số ĐƠN
        vẫn đếm theo đơn (đơn có chứa SP). Chi phí Ads KHÔNG tách được theo sản
        phẩm nên giữ nguyên tổng chi tiêu — FE phải nói rõ điều này.
        """
        base = cls._apply_created_at_date_filters(
            cls._exclude_hidden_branches(Order.objects.filter(deleted_at__isnull=True)),
            date_from,
            date_to,
        ).exclude(branch_id__in=OVERVIEW_EXCLUDED_BRANCH_IDS)
        # Đơn hủy có bản khôi phục (clone) còn sống bị loại khỏi base: clone kế
        # thừa created_at của đơn gốc nên rơi vào CÙNG kỳ báo cáo → nếu không loại,
        # đơn gốc (đã bị ~CANCELLED_ORDER_Q loại khỏi total_q) và clone (vào
        # total_q bình thường) cùng cộng vào total_revenue, thổi phồng mẫu số
        # của mọi ô %. Đơn hủy vẫn hiển thị bình thường ở tab "Đã hủy" trên UI
        # (status_counts_for KHÔNG áp exclude này).
        #
        # DÙNG Exists(), KHÔNG `.exclude(restored_orders__deleted_at__isnull=True)`:
        # lookup `__isnull=True` xuyên quan hệ to-many dùng LEFT OUTER JOIN, nên nó
        # khớp CẢ đơn không có bản khôi phục nào (join ra NULL) lẫn đơn có bản khôi
        # phục còn sống — exclude() khi đó xoá gần như toàn bộ bảng orders.
        live_restore_exists = Order.objects.filter(
            restored_from_id=OuterRef("pk"), deleted_at__isnull=True
        )
        base = base.exclude(Exists(live_restore_exists))

        # Lọc nhóm kênh bán: "marketplace" (nội sàn) / "off_marketplace" (ngoại
        # sàn). Giá trị lạ bị bỏ qua (coi như không lọc) thay vì trả rỗng.
        if sale_channel == SALE_CHANNEL_MARKETPLACE:
            base = base.filter(MARKETPLACE_ORDER_Q)
        elif sale_channel == SALE_CHANNEL_OFF_MARKETPLACE:
            base = base.exclude(MARKETPLACE_ORDER_Q)

        # Ô "Kênh bán": lọc sâu thêm một tầng. Điều kiện là phần bù luỹ tiến khớp
        # từng nhánh của `sale_channel_case` bên dưới (cùng dựng từ
        # SALE_CHANNEL_CASE_BRANCHES) nên tập đơn lọc ra luôn TRÙNG KHỚP với lát
        # donut mang tên nhóm đó.
        group_filter = sale_channel_group_filter(sale_channel, sale_channel_group)
        if group_filter:
            base = base.filter(SALE_CHANNEL_GROUP_FILTERS[group_filter])

        # `amount` = số tiền dùng cho mọi phép cộng bên dưới. Không lọc SP thì là
        # giá trị đơn; có lọc thì là tiền của riêng SP trong đơn đó (đơn không
        # chứa SP bị loại vì Subquery trả NULL).
        if product_ids:
            matched_lines = (
                OrderItem.objects.filter(order_id=OuterRef("pk"), product_id__in=product_ids)
                .values("order_id")
                .annotate(total=Sum("line_total"))
                .values("total")[:1]
            )
            base = base.annotate(
                amount=Subquery(
                    matched_lines, output_field=DecimalField(max_digits=18, decimal_places=2)
                )
            ).filter(amount__isnull=False)
        else:
            base = base.annotate(amount=F("total_after_discount"))
        total_q = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q
        delivered_q = total_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
        # PHỦ ĐỊNH, không liệt kê dương: Order.status là PositiveSmallIntegerField
        # với choices không ép ở tầng DB, và luồng import KiotViet ghi thẳng
        # int(status_val) từ payload API, không qua whitelist. Mã trạng thái lạ
        # (KiotViet thêm mới) vẫn phải rơi vào MỘT rổ — dùng phần bù để "chờ
        # giao" tự nhận, thay vì rớt khỏi cả 4 rổ và làm tổng lệch.
        awaiting_q = total_q & ~Q(
            status__in=(
                DELIVERED_SUCCESS_ORDER_STATUS,
                *RETURN_ORDER_STATUSES,
                *RETURN_RISK_ORDER_STATUSES,
            )
        )
        returned_q = total_q & Q(status__in=RETURN_ORDER_STATUSES)
        # Doanh thu THUẦN = tổng trừ đơn hoàn. Nuôi ô KPI cùng tên và 2 khối địa
        # lý. Dùng phần bù của `returned_q` để không thể lệch định nghĩa.
        net_revenue_q = total_q & ~Q(status__in=RETURN_ORDER_STATUSES)
        return_risk_q = total_q & Q(status__in=RETURN_RISK_ORDER_STATUSES)
        agg = base.aggregate(
            total_revenue=Sum("amount", filter=total_q),
            total_orders=Count("id", filter=total_q),
            delivered_revenue=Sum("amount", filter=delivered_q),
            delivered_orders=Count("id", filter=delivered_q),
            awaiting_revenue=Sum("amount", filter=awaiting_q),
            awaiting_orders=Count("id", filter=awaiting_q),
            returned_revenue=Sum("amount", filter=returned_q),
            returned_orders=Count("id", filter=returned_q),
            return_risk_revenue=Sum("amount", filter=return_risk_q),
            return_risk_orders=Count("id", filter=return_risk_q),
        )
        # Một lượt group-by duy nhất cho cả 3 bucket (FILTER trên từng cột) —
        # tránh 3 vòng quét bảng orders cho 3 khối biểu đồ.
        # Nhóm kênh bán tính ngay trong SQL — mọi đơn rơi vào đúng 1 trong 5
        # nhóm (nhánh default hứng phần còn lại) nên tổng 3 donut luôn khớp ô KPI.
        #
        # THỨ TỰ CÁC NHÁNH LÀ NGHIỆP VỤ, KHÔNG PHẢI CÚ PHÁP: hai sàn xét TRƯỚC
        # kênh. Kênh `delax-chinh-hang` mang 786 đơn Shopee và prefix HDSPE_ nằm
        # trên 6 kênh khác nhau (đo prod 08/2026) — đảo thứ tự là gán nhầm ~1,8
        # tỉ doanh thu Shopee sang Facebook mà không có gì báo lỗi.
        #
        # Hai nhánh Facebook/Web join sang `channels`; FK non-null nên là INNER
        # JOIN, không mất dòng. Bảng channels vài chục dòng, join không đáng kể.
        # Đang lọc một nhóm cụ thể thì tầng chỉ còn đúng nhóm đó — donut một lát
        # 100%, biểu đồ diễn biến một series. `base` đã bị thu hẹp ở trên nên
        # không nhóm nào khác có thể xuất hiện.
        groups_of_level = (group_filter,) if group_filter else sale_channel_groups_for(sale_channel)
        # Dùng chung cho cả group-by nhóm kênh bán lẫn query diễn biến bên dưới —
        # hai chỗ lệch nhánh là hai biểu đồ trên cùng màn hình chia nhóm khác nhau.
        # Dựng từ ``SALE_CHANNEL_CASE_BRANCHES`` để không thể lệch với điều kiện
        # lọc của ô "Kênh bán" (``SALE_CHANNEL_GROUP_FILTERS`` cùng nguồn).
        sale_channel_case = Case(
            *(When(branch_q, then=Value(key)) for key, branch_q in SALE_CHANNEL_CASE_BRANCHES),
            default=Value(SALE_CHANNEL_OFF_OTHER),
            output_field=CharField(),
        )
        sale_channel_rows = list(
            base.annotate(
                sale_channel=sale_channel_case,
            )
            .values("sale_channel")
            .annotate(
                revenue=Sum("amount", filter=total_q),
                orders=Count("id", filter=total_q),
                delivered_revenue=Sum("amount", filter=delivered_q),
                delivered_orders=Count("id", filter=delivered_q),
                returned_revenue=Sum("amount", filter=returned_q),
                returned_orders=Count("id", filter=returned_q),
                return_risk_revenue=Sum("amount", filter=return_risk_q),
                return_risk_orders=Count("id", filter=return_risk_q),
            )
            .order_by("-revenue")
        )

        def _total_spend(source: AdsSpendSource) -> Decimal:
            return ads_spend_provider(source).total_spend(
                date_from=_parse_report_date(date_from),
                date_to=_parse_report_date(date_to),
            )

        # Hệ số FB đổi theo mốc 01/08/2026 và chọn theo NGÀY BẮT ĐẦU kỳ đang xem,
        # nên phải hỏi lại mỗi lần dựng báo cáo thay vì dùng hằng số.
        facebook_multiplier = facebook_ads_cost_multiplier(_parse_report_date(date_from))

        # Chi phí Ads CHỈ có số khi đang xem ngoại sàn (user chốt 2026-08-20):
        # tiền FB/Google chỉ đẻ ra đơn ngoại sàn, đặt cạnh doanh thu gồm cả
        # Shopee/TikTok là sai mẫu số. Chặn ngay ở đây cũng bỏ luôn 2 lượt query
        # bảng spend cho phần lớn lượt xem trang (mặc định là "Tất cả").
        #
        # Phạm vi lấy theo ô SÂU NHẤT đang chọn (user chốt 2026-08-24): ô "Kênh
        # bán" thắng ô "Nguồn", nên xem riêng Facebook thì ô Google về 0 thay vì
        # bày ra tiền của kênh mình không xem. `off_other` (Resale/KOC/bán quầy)
        # không tiêu tiền quảng cáo nên cả hai ô đều 0.
        ads_scope = group_filter or sale_channel or ""
        show_facebook_cost = ads_scope in (SALE_CHANNEL_OFF_MARKETPLACE, SALE_CHANNEL_FACEBOOK)
        show_google_cost = ads_scope in (SALE_CHANNEL_OFF_MARKETPLACE, SALE_CHANNEL_WEB)
        ads_cost_facebook = (
            _total_spend(AdsSpendSource.FACEBOOK) * facebook_multiplier
            if show_facebook_cost
            else Decimal("0")
        )
        ads_cost_google = (
            _total_spend(AdsSpendSource.GOOGLE) * GOOGLE_ADS_DAILY_COST_MULTIPLIER
            if show_google_cost
            else Decimal("0")
        )

        # Phân bổ địa lý: group theo province thô rồi gom về tên tỉnh CHUẨN ở
        # Python (dữ liệu có nhiều biến thể chính tả — xem vietnam_regions), và
        # cộng tiếp lên vùng miền.
        #
        # CƠ SỞ LÀ DOANH THU THUẦN — tổng TRỪ đơn hoàn (user chốt 2026-08-22),
        # khớp ô KPI "Doanh thu thuần" chứ KHÔNG phải ô "Tổng doanh thu". Đây là
        # điểm khác CỐ Ý so với 3 khối theo kênh bán, vốn vẫn ở cơ sở tổng: người
        # xem hỏi số vùng miền để so với doanh thu thuần bên KiotViet, còn khối
        # kênh bán dùng để đối chiếu với 4 rổ. HAI khối địa lý phải cùng cơ sở —
        # tỉnh cộng lên đúng bằng vùng, đổi một bên là hai donut cạnh nhau lệch
        # nhau đúng bằng tiền hoàn.
        #
        # Đơn RỦI RO HOÀN (mã 10) VẪN nằm trong: hàng chưa quay về kho nên
        # KiotViet chưa lập phiếu trả, trừ nó là lệch xa KiotViet hơn.
        #
        # KHÔNG loại đơn trống tỉnh: `province` hụt thì suy từ `kiotviet_location`
        # ("<Tỉnh> - <Quận/Huyện>"), hụt cả hai mới vào nhóm "Chưa có địa chỉ" —
        # nhờ vậy tổng 2 khối địa lý luôn khớp ô Doanh thu thuần.
        geo_rows = (
            base.filter(net_revenue_q)
            .values("province", "kiotviet_location")
            .annotate(
                revenue=Sum("amount"),
                orders=Count("id"),
                delivered_revenue=Sum("amount", filter=delivered_q),
                delivered_orders=Count("id", filter=delivered_q),
            )
        )

        def _new_geo_entry(key: str, label: str) -> dict:
            return {
                key: label,
                "revenue": Decimal("0"),
                "orders": 0,
                "delivered_revenue": Decimal("0"),
                "delivered_orders": 0,
            }

        by_province: dict[str, dict] = {}
        by_region: dict[str, dict] = {}
        for row in geo_rows:
            province = resolve_province(row["province"], row["kiotviet_location"] or "")
            region = region_of(province)
            province_entry = by_province.setdefault(province, _new_geo_entry("province", province))
            region_entry = by_region.setdefault(region, _new_geo_entry("region", region))
            for entry in (province_entry, region_entry):
                entry["revenue"] += row["revenue"] or Decimal("0")
                entry["orders"] += row["orders"] or 0
                entry["delivered_revenue"] += row["delivered_revenue"] or Decimal("0")
                entry["delivered_orders"] += row["delivered_orders"] or 0

        def _geo_rows(entries: dict[str, dict], key: str, order: list[str] | None) -> list[dict]:
            """Vùng giữ thứ tự Bắc→Nam khai báo sẵn; tỉnh sort theo doanh thu.

            Nhóm "Không xác định" luôn xếp cuối ở cả hai (nhóm gộp, không phải
            một địa phương thật).
            """
            rows = [
                {**entry, "revenue": str(entry["revenue"]),
                 "delivered_revenue": str(entry["delivered_revenue"])}
                for entry in entries.values()
            ]
            if order is not None:
                rank = {value: idx for idx, value in enumerate(order)}
                return sorted(rows, key=lambda r: rank.get(r[key], len(rank)))
            return sorted(
                rows,
                key=lambda r: (
                    r[key] == UNKNOWN_PROVINCE_LABEL,
                    -Decimal(r["revenue"]),
                    r[key],
                ),
            )

        def _bucket_rows(revenue_key: str, orders_key: str, *, with_delivered: bool) -> list[dict]:
            """Rows theo nhóm kênh bán của một bucket; bỏ nhóm 0 đơn.

            Nhóm GỘP LÊN đúng tầng của bộ lọc đang chọn (xem
            ``sale_channel_groups_for``): "Tất cả" gộp về nội/ngoại sàn, còn khi
            đã lọc nội hoặc ngoại sàn thì giữ nguyên nhóm con. Đang lọc ô "Kênh
            bán" thì chỉ còn đúng một nhóm — donut một lát 100%. Thứ tự cố định
            theo tầng (không sort theo giá trị) để một nhóm luôn cùng vị trí và
            cùng màu ở cả 3 biểu đồ lẫn biểu đồ diễn biến.
            """
            merged: dict[str, dict] = {}
            for row in sale_channel_rows:
                orders = row[orders_key] or 0
                if orders == 0:
                    continue
                source = rolled_sale_channel(row["sale_channel"], sale_channel)
                entry = merged.setdefault(
                    source,
                    {
                        "sale_channel": source,
                        "revenue": Decimal("0"),
                        "orders": 0,
                        **({"delivered_revenue": Decimal("0"), "delivered_orders": 0}
                           if with_delivered else {}),
                    },
                )
                entry["revenue"] += row[revenue_key] or Decimal("0")
                entry["orders"] += orders
                if with_delivered:
                    entry["delivered_revenue"] += row["delivered_revenue"] or Decimal("0")
                    entry["delivered_orders"] += row["delivered_orders"] or 0
            rows = [
                {
                    **entry,
                    "revenue": str(entry["revenue"]),
                    **({"delivered_revenue": str(entry["delivered_revenue"])}
                       if with_delivered else {}),
                }
                for entry in merged.values()
            ]
            rank = {group: idx for idx, group in enumerate(groups_of_level)}
            return sorted(rows, key=lambda r: rank.get(r["sale_channel"], len(rank)))

        # DIỄN BIẾN THEO THỜI GIAN — cơ sở DOANH THU THUẦN, cùng cơ sở với ô KPI
        # cùng tên và 2 khối địa lý.
        #
        # MỘT query duy nhất ở grain (ngày, giờ, nhóm) rồi cuộn ra cả 3 mức ở
        # Python — không phải 3 query. Trần kích thước: 31 ngày × 24 giờ × 5 nhóm
        # = 3.720 dòng.
        #
        # `TruncDate`/`ExtractHour` cắt theo ``settings.TIME_ZONE`` (Asia/Bangkok,
        # UTC+7) chứ KHÔNG phải UTC — nếu không, đơn tạo lúc 23h VN sẽ rơi sang
        # ngày hôm trước và cột "theo giờ" lệch đúng 7 tiếng.
        #
        # Chỉ trả bucket CÓ dữ liệu. FE tự dựng đủ trục (0–23 giờ, 2–8 thứ, mọi
        # ngày trong kỳ) và điền 0 — nó có sẵn khoảng ngày đang lọc, còn BE thì
        # không phải sinh cả tích chéo bucket × nhóm.
        timeseries_rows = (
            base.filter(net_revenue_q)
            .annotate(
                sale_channel=sale_channel_case,
                bucket_date=TruncDate("created_at"),
                bucket_hour=ExtractHour("created_at"),
            )
            .values("bucket_date", "bucket_hour", "sale_channel")
            .annotate(revenue=Sum("amount"), orders=Count("id"))
        )

        by_day: dict[tuple[str, str], dict] = {}
        by_hour: dict[tuple[str, str], dict] = {}
        by_weekday: dict[tuple[str, str], dict] = {}

        def _add(target: dict, bucket: str, group: str, revenue, orders: int) -> None:
            entry = target.setdefault(
                (bucket, group),
                {"bucket": bucket, "sale_channel": group, "revenue": Decimal("0"), "orders": 0},
            )
            entry["revenue"] += revenue
            entry["orders"] += orders

        for row in timeseries_rows:
            bucket_date = row["bucket_date"]
            if bucket_date is None:
                continue
            group = rolled_sale_channel(row["sale_channel"], sale_channel)
            revenue = row["revenue"] or Decimal("0")
            orders = row["orders"] or 0
            _add(by_day, bucket_date.isoformat(), group, revenue, orders)
            _add(by_hour, str(row["bucket_hour"]), group, revenue, orders)
            # isoweekday: 1 = Thứ Hai … 7 = Chủ nhật. FE đổi sang nhãn tiếng Việt.
            _add(by_weekday, str(bucket_date.isoweekday()), group, revenue, orders)

        # TOP SẢN PHẨM (user chốt 2026-09-03) — cơ sở DOANH THU THUẦN, cộng theo
        # DÒNG HÀNG: tổng ``OrderItem.line_total`` của từng SP trong các đơn thuộc
        # ``net_revenue_q`` (đã lên hóa đơn, trừ hủy, trừ hoàn). Cùng tập đơn với 2
        # khối địa lý và biểu đồ diễn biến; số ĐƠN đếm theo đơn chứa SP (distinct),
        # nên đơn mua kèm 2 SP đếm 1 ở mỗi SP.
        #
        # TỔNG CÁC SP KHÔNG BUỘC PHẢI BẰNG ô KPI Doanh thu thuần: ô đó cộng
        # ``total_after_discount`` của ĐƠN, còn dòng hàng không gánh chiết khấu
        # cấp đơn / phí ship. Đây là khác biệt cố ý, FE ghi chú ngay dưới tiêu đề.
        #
        # Đang lọc SP thì chỉ còn đúng các SP đó — tiền của SP khác mua kèm trong
        # cùng đơn không được lẫn vào bảng (``base`` chỉ lọc ĐƠN chứa SP).
        product_lines = OrderItem.objects.filter(
            order_id__in=base.filter(net_revenue_q).values("id")
        )
        if product_ids:
            product_lines = product_lines.filter(product_id__in=product_ids)
        delivered_line_q = Q(order__status=DELIVERED_SUCCESS_ORDER_STATUS)
        top_products = [
            {
                "product_id": row["product_id"],
                "sku": row["product__sku"] or "",
                "name": row["product__name"] or "",
                "quantity": row["quantity"] or 0,
                "orders": row["orders"] or 0,
                "revenue": str(row["revenue"] or Decimal("0")),
                "delivered_orders": row["delivered_orders"] or 0,
                "delivered_revenue": str(row["delivered_revenue"] or Decimal("0")),
            }
            for row in product_lines.values("product_id", "product__sku", "product__name")
            .annotate(
                revenue=Sum("line_total"),
                quantity=Sum("quantity"),
                orders=Count("order_id", distinct=True),
                delivered_revenue=Sum("line_total", filter=delivered_line_q),
                delivered_orders=Count("order_id", distinct=True, filter=delivered_line_q),
            )
            .order_by("-revenue", "product__sku")
        ]

        group_rank = {group: idx for idx, group in enumerate(groups_of_level)}

        def _series(target: dict, *, numeric_bucket: bool) -> list[dict]:
            """Sort theo bucket rồi theo thứ tự nhóm của tầng — FE dựng stack theo đúng thứ tự này."""
            return [
                {**entry, "revenue": str(entry["revenue"])}
                for entry in sorted(
                    target.values(),
                    key=lambda e: (
                        int(e["bucket"]) if numeric_bucket else e["bucket"],
                        group_rank.get(e["sale_channel"], len(group_rank)),
                    ),
                )
            ]

        return {
            "total_revenue": str(agg["total_revenue"] or Decimal("0")),
            "total_orders": agg["total_orders"] or 0,
            "delivered_revenue": str(agg["delivered_revenue"] or Decimal("0")),
            "delivered_orders": agg["delivered_orders"] or 0,
            "awaiting_revenue": str(agg["awaiting_revenue"] or Decimal("0")),
            "awaiting_orders": agg["awaiting_orders"] or 0,
            "returned_revenue": str(agg["returned_revenue"] or Decimal("0")),
            "returned_orders": agg["returned_orders"] or 0,
            "return_risk_revenue": str(agg["return_risk_revenue"] or Decimal("0")),
            "return_risk_orders": agg["return_risk_orders"] or 0,
            # Hai ô rời trên hàng KPI; `ads_cost` là tổng, giữ lại cho các
            # consumer cũ của endpoint.
            "ads_cost": str(ads_cost_facebook + ads_cost_google),
            "ads_cost_facebook": str(ads_cost_facebook),
            "ads_cost_google": str(ads_cost_google),
            # Chia theo NHÓM KÊNH BÁN (nội sàn / ngoại sàn) — thay cho chia nhỏ
            # theo order_source trước đây.
            "revenue_by_sale_channel": _bucket_rows("revenue", "orders", with_delivered=True),
            "returned_by_sale_channel": _bucket_rows(
                "returned_revenue", "returned_orders", with_delivered=False
            ),
            "return_risk_by_sale_channel": _bucket_rows(
                "return_risk_revenue", "return_risk_orders", with_delivered=False
            ),
            "revenue_by_region": _geo_rows(by_region, "region", list(REGION_LABELS) + [UNKNOWN_REGION]),
            "revenue_by_province": _geo_rows(by_province, "province", None),
            # Diễn biến doanh thu THUẦN theo thời gian, chia theo nhóm của tầng
            # đang lọc. Ba mức dùng CÙNG một tập đơn nên tổng của cả ba bằng nhau.
            "revenue_timeseries": {
                "by_day": _series(by_day, numeric_bucket=False),
                "by_hour": _series(by_hour, numeric_bucket=True),
                "by_weekday": _series(by_weekday, numeric_bucket=True),
            },
            # Top SP theo doanh thu THUẦN cộng theo dòng hàng, giảm dần — đủ mọi SP
            # có dòng hàng trong kỳ, FE tự cắt top N cho biểu đồ.
            "top_products": top_products,
        }
