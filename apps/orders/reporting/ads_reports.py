"""Báo cáo Ads FB/Google — phạm vi kênh/TKQC theo quyền user + các summary_ads_*.

Tách nguyên trạng từ ``apps/orders/_repositories_reports.py`` — KHÔNG đổi hành
vi. Gồm cả lớp phân quyền (``_accessible_ads_channel_ids``, ``*_account_scope``)
lẫn số liệu (revenue theo kênh/tỉnh/giới tính, top sản phẩm, diễn biến ngày):
hai nửa dùng chung ``_ads_landing_orders_queryset``/``_ads_report_channel_qs``
nên tách nữa sẽ cắt đôi một mạch nghiệp vụ.
"""
from datetime import date as date_cls
from decimal import Decimal
from typing import NamedTuple

from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate

from apps.catalog.models import Product
from apps.channels_core.models import Channel, ChannelAdAccount
from apps.orders.contracts.ads_spend_port import (
    AdAccountScope,
    AdsSpendSource,
    ads_spend_provider,
)
from apps.orders.models import Order, OrderItem

from .shared import (
    CLOSED_SALES_STATUS_CODES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    _ads_excluded_channel_ids,
    _parse_report_date,
    _user_sees_all_ads_channels,
    facebook_ads_cost_multiplier,
    sale_report_channel_ids,
)


class _AdsCostScope(NamedTuple):
    """Bản đồ chi phí của MỘT nguồn quảng cáo sau khi đã gộp bản sao TKQC.

    ``account_ids``: mọi PK thuộc các nhóm — kể cả bản sao chưa được map kênh, vì
    chi tiêu có thể treo dưới bản đó. ``pk_to_group``: PK → chỉ số nhóm (tài khoản
    THẬT). ``group_channels``: nhóm → danh sách kênh nhận chi phí (chia đều 1/N).
    """

    account_ids: list[int]
    pk_to_group: dict[int, int]
    group_channels: dict[int, list[int]]
    multiplier: Decimal


class _AdsCostChannelMaps(NamedTuple):
    """Phạm vi kênh + bản đồ TKQC của cả hai nguồn, cho một request báo cáo ads."""

    channel_labels: dict[int, dict]
    facebook: _AdsCostScope
    google: _AdsCostScope


class AdsReportsMixin:
    """summary_ads_* + phân quyền TKQC; ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @staticmethod
    def _accessible_ads_channel_ids(user, *, excluded_ids: set[int] | None = None) -> list[int] | None:
        """Channel IDs the user can see in the ads report.

        Returns ``None`` if no further channel filter is required (scope ads-report
        = ALL — admin hoặc role xem-toàn-bộ, hoặc caller nội bộ không truyền user).

        ``excluded_ids``: truyền sẵn ``_ads_excluded_channel_ids()`` khi caller đã
        tính (compose 1 lần/request); ``None`` thì tự tính (giữ hành vi cũ).
        """
        if _user_sees_all_ads_channels(user):
            return None
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        return list(
            Channel.objects.filter(
                deleted_at__isnull=True,
                user_links__user=user,
                user_links__is_active=True,
            )
            .exclude(id__in=excluded_ids)
            .distinct()
            .values_list("id", flat=True)
        )

    @staticmethod
    def _accessible_ads_ad_account_ids(user) -> list[int]:
        """Union FB ad-account ids (bảng nối ChannelAdAccount) across channels visible to user."""
        qs = Channel.objects.filter(deleted_at__isnull=True).exclude(id__in=_ads_excluded_channel_ids())
        if not _user_sees_all_ads_channels(user):
            qs = qs.filter(user_links__user=user, user_links__is_active=True).distinct()
        return list(
            ChannelAdAccount.objects.filter(
                channel__in=qs, provider=ChannelAdAccount.Provider.FACEBOOK
            )
            .order_by()  # bỏ Meta.ordering để DISTINCT chỉ áp lên account_id
            .values_list("account_id", flat=True)
            .distinct()
        )

    @classmethod
    def ads_ad_account_scope(cls, user) -> AdAccountScope:
        """Phạm vi TKQC Facebook user được xem trong báo cáo ads.

        Trả ``ids=None`` cho user xem-toàn-bộ thay vì list: ``_accessible_ads_ad_account_ids``
        chỉ liệt kê TKQC ĐÃ map vào kênh, nên dùng thẳng nó sẽ làm biến mất chi phí
        của TKQC chưa map — đúng kiểu hụt số âm thầm mà báo cáo này sinh ra để chữa.

        Với user bị giới hạn, PK từ mapping kênh được TRẢI qua port sang mọi bản sao
        của cùng một TKQC thật. Không trải thì user vẫn mù: mapping trỏ được đúng một
        dòng, còn ad và chi tiêu có thể treo dưới dòng của app khác (prod 2026-08-10).
        """
        if _user_sees_all_ads_channels(user):
            return AdAccountScope(ids=None, count=None)
        mapped_ids = cls._accessible_ads_ad_account_ids(user)
        return ads_spend_provider(AdsSpendSource.FACEBOOK).account_scope(mapped_ids)

    @classmethod
    def accessible_ads_ad_account_ids(cls, user) -> list[int] | None:
        """TKQC user được xem. ``None`` = không cần lọc. Giữ chữ ký cũ cho call-site."""
        return cls.ads_ad_account_scope(user).ids

    @staticmethod
    def _accessible_google_ads_account_ids(user) -> list[int]:
        """Union id TKQC Google (bảng nối ChannelAdAccount) qua các kênh user thấy."""
        qs = Channel.objects.filter(deleted_at__isnull=True).exclude(id__in=_ads_excluded_channel_ids())
        if not _user_sees_all_ads_channels(user):
            qs = qs.filter(user_links__user=user, user_links__is_active=True).distinct()
        return list(
            ChannelAdAccount.objects.filter(
                channel__in=qs, provider=ChannelAdAccount.Provider.GOOGLE
            )
            .order_by()  # bỏ Meta.ordering để DISTINCT chỉ áp lên account_id
            .values_list("account_id", flat=True)
            .distinct()
        )

    @classmethod
    def google_ads_account_scope(cls, user) -> AdAccountScope:
        """Phạm vi TKQC Google. Cùng luật với bản Facebook — xem ``ads_ad_account_scope``."""
        if _user_sees_all_ads_channels(user):
            return AdAccountScope(ids=None, count=None)
        mapped_ids = cls._accessible_google_ads_account_ids(user)
        return ads_spend_provider(AdsSpendSource.GOOGLE).account_scope(mapped_ids)

    @classmethod
    def accessible_google_ads_account_ids(cls, user) -> list[int] | None:
        """TKQC Google user được xem. ``None`` = không cần lọc."""
        return cls.google_ads_account_scope(user).ids

    @classmethod
    def _ads_report_channel_qs(
        cls,
        user,
        sale_channel_ids: list[int],
        excluded_ids: set[int],
        channel_ids: list[int] | None = None,
    ):
        """Tập kênh hiển thị của báo cáo ads cho user + bộ lọc kênh hiện tại.

        Dùng chung cho zero-fill ``summary_ads_revenue_by_channel`` và phân bổ
        chi phí ``summary_ads_daily_breakdown`` — hai khối phải cùng một tập kênh
        thì tổng chi phí (bảng theo kênh / KPI / diễn biến ngày) mới khớp nhau.
        """
        qs = Channel.objects.filter(
            deleted_at__isnull=True, id__in=sale_channel_ids
        ).exclude(id__in=excluded_ids)
        if not _user_sees_all_ads_channels(user):
            qs = qs.filter(user_links__user=user, user_links__is_active=True).distinct()
        if channel_ids:
            qs = qs.filter(id__in=channel_ids)
        return qs

    @classmethod
    def _ads_landing_orders_queryset(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ):
        # sale_channel_ids/excluded_ids: truyền sẵn khi caller compose đã tính 1 lần
        # cho cả request ads-report (tránh re-query sale_report_channel_ids()/
        # _ads_excluded_channel_ids() nhiều lần — audit 2026-07 follow-up task 11).
        # None -> tự tính, giữ hành vi cũ cho caller không truyền.
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        # Tính mọi đơn có nguồn (order_source khác null/rỗng), không chỉ riêng landing.
        queryset = cls._exclude_hidden_branches(
            Order.objects.filter(deleted_at__isnull=True, order_source__isnull=False)
            .exclude(order_source="")
            .exclude(status=6)
            .exclude(invoice_status=2)
        ).exclude(channel_id__in=excluded_ids)
        # Chỉ tính kênh CÓ NV phòng Ads đang gán (cùng nguồn sự thật với báo cáo
        # sale — sale_report_channel_ids): kênh chưa gán ads (WEB, sàn, Resale,
        # page chưa có người phụ trách...) không vào báo cáo ads.
        queryset = queryset.filter(channel_id__in=sale_channel_ids)
        accessible_ids = cls._accessible_ads_channel_ids(user, excluded_ids=excluded_ids)
        if accessible_ids is not None:
            queryset = queryset.filter(channel_id__in=accessible_ids)
        if channel_ids:
            queryset = queryset.filter(channel_id__in=channel_ids)
        return cls._apply_created_at_date_filters(queryset, date_from, date_to)

    @classmethod
    def summary_ads_revenue_by_channel(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ):
        # Zero-fill kênh trong scope nhưng 0 đơn chốt trong kỳ: FE lấy chi phí
        # per-kênh từ daily_breakdown rồi CHỈ cộng các kênh có mặt ở đây, nên kênh
        # có spend mà không có đơn chốt từng bị rớt khỏi bảng + KPI "Tổng chi phí"
        # (lệch với khối "Diễn biến doanh thu / chi phí" vốn cộng đủ mọi kênh).
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        queryset = cls._ads_landing_orders_queryset(
            date_from,
            date_to,
            user=user,
            channel_ids=channel_ids,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ).filter(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)
        rows = list(
            queryset.values(
                "channel_id",
                "channel__code",
                "channel__name",
            )
            .annotate(
                closed_orders=Count("id"),
                revenue=Sum("total_after_discount"),
                delivered_orders=Count(
                    "id", filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
                ),
                delivered_revenue=Sum(
                    "total_after_discount",
                    filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
            )
            .order_by("-revenue", "channel__name")
        )
        for row in rows:
            row["revenue"] = str(row["revenue"] or Decimal("0"))
            row["delivered_revenue"] = str(row["delivered_revenue"] or Decimal("0"))
        present_ids = {row["channel_id"] for row in rows}
        rows.extend(
            {
                "channel_id": ch["id"],
                "channel__code": ch["code"],
                "channel__name": ch["name"],
                "closed_orders": 0,
                "revenue": "0",
                "delivered_orders": 0,
                "delivered_revenue": "0",
            }
            for ch in cls._ads_report_channel_qs(
                user, sale_channel_ids, excluded_ids, channel_ids
            )
            .exclude(id__in=present_ids)
            .order_by("name")
            .values("id", "code", "name")
        )
        return rows

    @classmethod
    def summary_ads_top_products(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ):
        order_ids = cls._ads_landing_orders_queryset(
            date_from,
            date_to,
            user=user,
            channel_ids=channel_ids,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ).filter(
            assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES
        ).values_list("id", flat=True)
        rows = list(
            OrderItem.objects.filter(order_id__in=order_ids)
            .values(
                "product_id",
                "product__name",
                "product__sku",
                "order__channel_id",
                "order__channel__code",
                "order__channel__name",
            )
            .annotate(
                closed_orders=Count("order_id", distinct=True),
                quantity=Sum("quantity"),
                revenue=Sum("line_total"),
                delivered_orders=Count(
                    "order_id",
                    distinct=True,
                    filter=Q(order__status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
                delivered_revenue=Sum(
                    "line_total",
                    filter=Q(order__status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
            )
        )
        products: dict[tuple, dict] = {}
        for row in rows:
            key = (row["product_id"], row["product__sku"])
            product = products.get(key)
            if product is None:
                product = {
                    "product_id": row["product_id"],
                    "product__name": row["product__name"],
                    "product__sku": row["product__sku"],
                    "closed_orders": 0,
                    "quantity": 0,
                    "revenue": Decimal("0"),
                    "delivered_orders": 0,
                    "delivered_revenue": Decimal("0"),
                    "channels": [],
                }
                products[key] = product
            row_revenue = row["revenue"] or Decimal("0")
            row_delivered_revenue = row["delivered_revenue"] or Decimal("0")
            row_quantity = row["quantity"] or 0
            product["closed_orders"] += row["closed_orders"]
            product["quantity"] += row_quantity
            product["revenue"] += row_revenue
            product["delivered_orders"] += row["delivered_orders"]
            product["delivered_revenue"] += row_delivered_revenue
            product["channels"].append({
                "channel_id": row["order__channel_id"],
                "channel__code": row["order__channel__code"],
                "channel__name": row["order__channel__name"],
                "closed_orders": row["closed_orders"],
                "quantity": row_quantity,
                "revenue": str(row_revenue),
                "delivered_orders": row["delivered_orders"],
                "delivered_revenue": str(row_delivered_revenue),
            })
        for product in products.values():
            product["revenue"] = str(product["revenue"])
            product["delivered_revenue"] = str(product["delivered_revenue"])
            product["channels"].sort(
                key=lambda c: (-c["closed_orders"], c["channel__name"] or "")
            )
        return sorted(
            products.values(),
            key=lambda p: (-p["closed_orders"], p["product__name"] or ""),
        )

    @classmethod
    def _ads_cost_channel_maps(
        cls,
        user,
        sale_channel_ids: list[int],
        excluded_ids: set[int],
        channel_ids: list[int] | None,
        date_from: date_cls | None,
    ) -> _AdsCostChannelMaps:
        """Nhãn kênh + bản đồ TKQC→kênh đã gộp bản sao, cho MỌI đường chi phí ads.

        ``ChannelAdAccount`` lưu PK, mà một TKQC thật có nhiều dòng bản sao và mỗi
        bản lại có daily spend đầy đủ của cùng số tiền. Cộng thẳng theo PK sai theo
        cả hai chiều: hai bản sao map vào hai kênh thì cộng ĐÔI cùng một đồng (prod
        2026-08-19: TKQC 3804581706506110 map bằng pk 127 vào kênh 42 và pk 55 vào
        kênh 4), còn kênh trỏ bản sao không có dòng chi tiêu thì HỤT hẳn về 0 (lớp
        lỗi MR !1199).

        Vì vậy hỏi port nhóm bản sao trước, rồi caller lấy chi tiêu của MỌI PK trong
        nhóm — kể cả bản chưa map — và tự gộp lại theo grain của mình.

        Dùng chung giữa ``summary_ads_daily_breakdown`` (chi phí theo ngày/kênh) và
        ``summary_ads_product_breakdown`` (bung theo sản phẩm): lệch cách gộp ở đây
        là hai bảng đá nhau về tổng chi phí.

        ``date_from`` (ngày bắt đầu kỳ, đã parse) CHỈ dùng để chọn hệ số chi phí FB
        — hệ số này đổi từ mốc 01/08/2026. Cố ý KHÔNG có giá trị mặc định: quên
        truyền thì báo cáo lặng lẽ về hệ số cũ, đúng lớp lỗi khó thấy nhất.
        """
        # Cùng phạm vi với phía doanh thu: chỉ kênh CÓ NV phòng Ads gán — chi phí
        # của ad account map vào kênh chưa gán ads cũng không tính vào báo cáo.
        channel_qs = cls._ads_report_channel_qs(user, sale_channel_ids, excluded_ids, channel_ids)
        channel_labels: dict[int, dict] = {
            ch["id"]: {"channel__code": ch["code"], "channel__name": ch["name"]}
            for ch in channel_qs.values("id", "code", "name")
        }
        account_to_channels: dict[str, dict[int, list[int]]] = {"facebook": {}, "google": {}}
        link_rows = (
            ChannelAdAccount.objects.filter(channel__in=channel_qs)
            .order_by()
            .values_list("provider", "account_id", "channel_id")
        )
        for provider, acc_id, ch_id in link_rows:
            key = (
                "facebook" if provider == ChannelAdAccount.Provider.FACEBOOK else "google"
            )
            account_to_channels[key].setdefault(acc_id, []).append(ch_id)

        def _scope(source: AdsSpendSource, key: str, multiplier: Decimal) -> _AdsCostScope:
            mapping = account_to_channels[key]
            groups = ads_spend_provider(source).account_groups(list(mapping.keys()))
            # Kênh của nhóm = hợp của kênh mọi bản sao trong nhóm, giữ thứ tự xuất
            # hiện để phần chia đều không đổi chỗ giữa hai lần gọi.
            group_channels: dict[int, list[int]] = {}
            for idx, group in enumerate(groups):
                channels: list[int] = []
                for pk in group:
                    for ch_id in mapping.get(pk, []):
                        if ch_id not in channels:
                            channels.append(ch_id)
                group_channels[idx] = channels
            return _AdsCostScope(
                account_ids=[pk for group in groups for pk in group],
                pk_to_group={pk: idx for idx, group in enumerate(groups) for pk in group},
                group_channels=group_channels,
                multiplier=multiplier,
            )

        return _AdsCostChannelMaps(
            channel_labels=channel_labels,
            # Hệ số FB đổi theo mốc 01/08/2026, chọn theo NGÀY BẮT ĐẦU kỳ đang xem
            # (xem ``facebook_ads_cost_multiplier``) — Google giữ hệ số cố định.
            facebook=_scope(
                AdsSpendSource.FACEBOOK, "facebook", facebook_ads_cost_multiplier(date_from)
            ),
            google=_scope(AdsSpendSource.GOOGLE, "google", GOOGLE_ADS_DAILY_COST_MULTIPLIER),
        )

    @classmethod
    def summary_ads_daily_breakdown(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
        product_ids: list[int] | None = None,
    ):
        """Per-day revenue (closed landing orders) and FB ad spend, with per-channel breakdown.

        Cost = sum from FacebookAdAccountDailySpend for ad accounts mapped to
        channels visible to user. `ratio_pct` = cost / revenue × 100; None when
        revenue=0 (avoid divide-by-zero). Each day also includes a ``channels``
        list with revenue/cost/ratio split by channel; an ad account mapped to
        N channels has its spend distributed evenly (1/N) so the daily total
        stays consistent with the sum across channels.

        ``leads`` (mức ngày và mức kênh) = số data: MỌI lead/đơn phát sinh trong
        kỳ bất kể trạng thái chốt — mẫu số của CPL và tỉ lệ chốt ở tab "Báo cáo
        theo digital". Luôn ≥ ``closed_orders``.

        ``sale_channel_ids``/``excluded_ids``: truyền sẵn khi caller compose đã
        tính 1 lần cho cả request (tránh re-query — audit 2026-07 follow-up task 11).

        ``product_ids``: lọc theo sản phẩm (2 tab "Báo cáo theo digital"). Khi có:

        * Doanh thu là tiền CỦA RIÊNG các SP đó (tổng ``OrderItem.line_total`` khớp),
          không phải giá trị cả đơn — cùng ngữ nghĩa với ``summary_overview``. Số ĐƠN
          và số DATA vẫn đếm theo đơn (đơn có chứa SP).
        * Chi phí là chi tiêu THẬT của các chiến dịch đã gán về SP đó, đọc ở grain
          ad/campaign — KHÔNG phân bổ theo tỉ trọng doanh thu. Chiến dịch chưa gán
          không đóng góp vào bất kỳ SP nào; phần đó nằm ở
          ``summary_ads_unassigned_cost`` để báo cáo nói rõ ra thay vì giấu.

        Không truyền ``product_ids`` thì mọi con số giữ nguyên như trước.
        """
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        scoped_qs = cls._ads_landing_orders_queryset(
            date_from,
            date_to,
            user=user,
            channel_ids=channel_ids,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        )
        if product_ids:
            # Tiền của RIÊNG sản phẩm trong đơn. Đơn không chứa SP bị loại vì Subquery
            # trả NULL — cùng cách summary_overview làm, để hai báo cáo không lệch
            # định nghĩa "doanh thu theo sản phẩm".
            matched_lines = (
                OrderItem.objects.filter(order_id=OuterRef("pk"), product_id__in=product_ids)
                .values("order_id")
                .annotate(total=Sum("line_total"))
                .values("total")[:1]
            )
            scoped_qs = scoped_qs.annotate(
                amount=Subquery(
                    matched_lines, output_field=DecimalField(max_digits=18, decimal_places=2)
                )
            ).filter(amount__isnull=False)
        else:
            scoped_qs = scoped_qs.annotate(amount=F("total_after_discount"))

        order_qs = scoped_qs.filter(
            assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES
        )
        revenue_rows = (
            order_qs.annotate(d=TruncDate("created_at"))
            .values("d", "channel_id", "channel__code", "channel__name")
            .annotate(revenue=Sum("amount"), closed_orders=Count("id"))
        )
        # "Số data" của báo cáo theo digital: MỌI lead/đơn phát sinh trong kỳ, không
        # lọc trạng thái chốt (đơn huỷ đã bị loại từ _ads_landing_orders_queryset).
        # Tách query riêng thay vì thêm annotate vào revenue_rows vì hai bên khác
        # mẫu số — gộp lại sẽ phá tổng doanh thu/đơn chốt đang có.
        lead_rows = (
            scoped_qs.annotate(d=TruncDate("created_at"))
            .values("d", "channel_id", "channel__code", "channel__name")
            .annotate(leads=Count("id"))
        )

        # Bản đồ TKQC→kênh (đã gộp bản sao) — dùng chung với đường bung theo sản
        # phẩm, xem ``_ads_cost_channel_maps``.
        cost_maps = cls._ads_cost_channel_maps(
            user, sale_channel_ids, excluded_ids, channel_ids, _parse_report_date(date_from),
        )
        channel_labels = cost_maps.channel_labels
        fb_scope, google_scope = cost_maps.facebook, cost_maps.google
        fb_pk_group, fb_group_channels = fb_scope.pk_to_group, fb_scope.group_channels
        google_pk_group = google_scope.pk_to_group
        google_group_channels = google_scope.group_channels
        cost_rows = ads_spend_provider(AdsSpendSource.FACEBOOK).spend_rows_by_account(
            account_ids=fb_scope.account_ids,
            date_from=_parse_report_date(date_from),
            date_to=_parse_report_date(date_to),
        )
        google_cost_rows = ads_spend_provider(AdsSpendSource.GOOGLE).spend_rows_by_account(
            account_ids=google_scope.account_ids,
            date_from=_parse_report_date(date_from),
            date_to=_parse_report_date(date_to),
        )

        def _new_day(key: str) -> dict:
            return {
                "date": key,
                "revenue": Decimal("0"),
                "cost": Decimal("0"),
                "closed_orders": 0,
                "leads": 0,
                "_channels": {},
            }

        def _channel_entry(day: dict, channel_id, code, name) -> dict:
            return day["_channels"].setdefault(
                channel_id,
                {
                    "channel_id": channel_id,
                    "channel__code": code,
                    "channel__name": name,
                    "revenue": Decimal("0"),
                    "cost": Decimal("0"),
                    "closed_orders": 0,
                    "leads": 0,
                },
            )

        by_date: dict[str, dict] = {}
        for row in revenue_rows:
            day = row["d"]
            if day is None:
                continue
            key = day.isoformat()
            entry = by_date.setdefault(key, _new_day(key))
            revenue = row["revenue"] or Decimal("0")
            closed = row["closed_orders"] or 0
            entry["revenue"] += revenue
            entry["closed_orders"] += closed
            ch = _channel_entry(entry, row["channel_id"], row["channel__code"], row["channel__name"])
            ch["revenue"] += revenue
            ch["closed_orders"] += closed

        for row in lead_rows:
            day = row["d"]
            if day is None:
                continue
            key = day.isoformat()
            entry = by_date.setdefault(key, _new_day(key))
            leads = row["leads"] or 0
            entry["leads"] += leads
            ch = _channel_entry(entry, row["channel_id"], row["channel__code"], row["channel__name"])
            ch["leads"] += leads

        def _apply_cost(rows, pk_to_group, group_channels, multiplier):
            """Cộng spend (đã nhân hệ số) vào tổng ngày + chia đều theo các kênh map.

            Mỗi (nhóm bản sao, ngày) chỉ góp MỘT số: ``max`` các bản sao. Chi tiêu
            trong ngày chỉ tăng nên bản lớn nhất là bản sync gần nhất — cùng luật
            với ``AdsSpendPort.total_spend``, để ô chi phí ở Báo cáo tổng và ở đây
            không bao giờ lệch nhau vì cách gộp.
            """
            per_group_day: dict[tuple[int, str], Decimal] = {}
            for row in rows:
                day = row["date"]
                if day is None:
                    continue
                group = pk_to_group.get(row["ad_account_id"])
                if group is None:
                    continue
                key = (group, day.isoformat())
                spend = row["spend"] or Decimal("0")
                current = per_group_day.get(key)
                if current is None or spend > current:
                    per_group_day[key] = spend
            for (group, key), raw_spend in per_group_day.items():
                ch_ids = group_channels.get(group) or []
                if not ch_ids:
                    continue
                spend = raw_spend * multiplier
                entry = by_date.setdefault(key, _new_day(key))
                entry["cost"] += spend
                share = spend / Decimal(len(ch_ids))
                for ch_id in ch_ids:
                    label = channel_labels.get(ch_id, {})
                    ch = _channel_entry(
                        entry, ch_id, label.get("channel__code"), label.get("channel__name")
                    )
                    ch["cost"] += share

        def _apply_product_cost(rows, pk_to_group, group_channels, multiplier):
            """Cộng chi tiêu đã lọc theo sản phẩm — KHÔNG gộp lại bằng ``max``.

            Rows ở đây do adapter trả về và ĐÃ khử bản sao TKQC theo đúng grain của
            từng nền tảng (xem ``AdsSpendPort.spend_rows_by_account_product``). Gộp
            thêm bằng ``max`` như đường chi phí theo tài khoản sẽ làm HỤT tiền: ở
            grain ad, hai dòng cùng (nhóm, ngày) là hai quảng cáo khác nhau.
            """
            for row in rows:
                day = row["date"]
                if day is None or row["product_id"] not in product_id_set:
                    continue
                group = pk_to_group.get(row["ad_account_id"])
                if group is None:
                    continue
                ch_ids = group_channels.get(group) or []
                if not ch_ids:
                    continue
                spend = (row["spend"] or Decimal("0")) * multiplier
                entry = by_date.setdefault(day.isoformat(), _new_day(day.isoformat()))
                entry["cost"] += spend
                share = spend / Decimal(len(ch_ids))
                for ch_id in ch_ids:
                    label = channel_labels.get(ch_id, {})
                    ch = _channel_entry(
                        entry, ch_id, label.get("channel__code"), label.get("channel__name")
                    )
                    ch["cost"] += share

        if product_ids:
            product_id_set = set(product_ids)
            _apply_product_cost(
                ads_spend_provider(AdsSpendSource.FACEBOOK).spend_rows_by_account_product(
                    account_ids=list(fb_pk_group.keys()),
                    date_from=_parse_report_date(date_from),
                    date_to=_parse_report_date(date_to),
                ),
                fb_pk_group,
                fb_group_channels,
                fb_scope.multiplier,
            )
            _apply_product_cost(
                ads_spend_provider(AdsSpendSource.GOOGLE).spend_rows_by_account_product(
                    account_ids=list(google_pk_group.keys()),
                    date_from=_parse_report_date(date_from),
                    date_to=_parse_report_date(date_to),
                ),
                google_pk_group,
                google_group_channels,
                GOOGLE_ADS_DAILY_COST_MULTIPLIER,
            )
        else:
            # Gộp chi phí FB và Google vào cùng cost theo ngày/kênh. Hệ số lấy từ
            # scope (FB đổi theo mốc 01/08/2026), không đọc lại hằng số tại chỗ.
            _apply_cost(cost_rows, fb_pk_group, fb_group_channels, fb_scope.multiplier)
            _apply_cost(
                google_cost_rows,
                google_pk_group,
                google_group_channels,
                GOOGLE_ADS_DAILY_COST_MULTIPLIER,
            )

        result: list[dict] = []
        for key in sorted(by_date.keys()):
            entry = by_date[key]
            revenue = entry["revenue"] or Decimal("0")
            cost = entry["cost"] or Decimal("0")
            ratio_pct: float | None = None
            if revenue > 0:
                ratio_pct = float((cost / revenue) * Decimal("100"))
            channels_list: list[dict] = []
            for ch in entry["_channels"].values():
                ch_revenue = ch["revenue"] or Decimal("0")
                ch_cost = ch["cost"] or Decimal("0")
                ch_ratio: float | None = None
                if ch_revenue > 0:
                    ch_ratio = float((ch_cost / ch_revenue) * Decimal("100"))
                channels_list.append(
                    {
                        "channel_id": ch["channel_id"],
                        "channel__code": ch["channel__code"],
                        "channel__name": ch["channel__name"],
                        "revenue": str(ch_revenue),
                        "cost": str(ch_cost),
                        "closed_orders": ch["closed_orders"],
                        "leads": ch["leads"],
                        "ratio_pct": ch_ratio,
                    }
                )
            channels_list.sort(
                key=lambda c: (-Decimal(c["revenue"]), -Decimal(c["cost"]), c["channel__name"] or "")
            )
            result.append(
                {
                    "date": key,
                    "revenue": str(revenue),
                    "cost": str(cost),
                    "closed_orders": entry["closed_orders"],
                    "leads": entry["leads"],
                    "ratio_pct": ratio_pct,
                    "channels": channels_list,
                }
            )
        return result

    @classmethod
    def summary_ads_product_breakdown(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
        product_ids: list[int] | None = None,
        group_by_date: bool = False,
    ):
        """Bung một dòng digital ra thành các dòng SẢN PHẨM (2 tab "theo digital").

        Tập sản phẩm CỐ Ý lấy từ phía CHI PHÍ: chỉ những SP đã gán cho chiến dịch có
        chi tiêu trong kỳ, thuộc TKQC gắn vào chính kênh đó. Hệ quả phải nói rõ trên
        UI: SP bán được ở kênh nhưng không chạy ads sẽ không có dòng, nên tổng doanh
        thu các dòng con NHỎ HƠN dòng cha.

        Chi phí "chưa quy được về SP nào" KHÔNG trả ở đây — frontend lấy
        ``chi phí dòng cha − tổng dòng con`` ra rổ "Chưa gán chiến dịch", cùng lý do
        với ``summary_ads_assigned_cost``: chi tiêu grain ad/campaign không khớp
        tuyệt đối grain account, và phần chênh grain đó đúng là chưa gán. Nhờ phép
        trừ, tổng chi phí dòng con LUÔN khớp dòng cha.

        ``group_by_date=False`` → mỗi dòng là (kênh, SP) gộp cả kỳ, nuôi tab "Báo
        cáo theo digital". ``True`` → thêm chiều ngày cho tab "Báo cáo chi tiết".

        Các cột lấy đúng định nghĩa của đường lọc ``product_ids`` ở
        ``summary_ads_daily_breakdown`` để hai bên không bao giờ lệch: doanh thu là
        tiền của RIÊNG sản phẩm (``OrderItem.line_total``), còn số đơn/số data đếm
        theo ĐƠN có chứa SP — nên đơn nhiều SP được đếm ở mọi dòng SP của nó và tổng
        số data các dòng con LỚN HƠN dòng cha.
        """
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        cost_maps = cls._ads_cost_channel_maps(
            user, sale_channel_ids, excluded_ids, channel_ids, _parse_report_date(date_from),
        )

        product_filter = set(product_ids) if product_ids else None
        # (ngày|None, kênh, SP) -> chi phí đã nhân hệ số và chia đều theo kênh.
        cost: dict[tuple[str | None, int, int], Decimal] = {}
        for source, scope in (
            (AdsSpendSource.FACEBOOK, cost_maps.facebook),
            (AdsSpendSource.GOOGLE, cost_maps.google),
        ):
            if not scope.account_ids:
                continue
            rows = ads_spend_provider(source).spend_rows_by_account_product(
                account_ids=scope.account_ids,
                date_from=_parse_report_date(date_from),
                date_to=_parse_report_date(date_to),
            )
            for row in rows:
                product_id = row["product_id"]
                day = row["date"]
                # product_id None = chiến dịch chưa gán: thuộc rổ "chưa gán" mà FE tự
                # tính bằng phép trừ, không được thành một dòng SP ở đây.
                if product_id is None or day is None:
                    continue
                if product_filter is not None and product_id not in product_filter:
                    continue
                group = scope.pk_to_group.get(row["ad_account_id"])
                if group is None:
                    continue
                ch_ids = scope.group_channels.get(group) or []
                if not ch_ids:
                    continue
                # Cộng dồn chứ không ``max``: rows đã khử bản sao TKQC theo grain của
                # từng nền tảng (xem AdsSpendPort.spend_rows_by_account_product).
                share = (row["spend"] or Decimal("0")) * scope.multiplier / Decimal(len(ch_ids))
                date_key = day.isoformat() if group_by_date else None
                for ch_id in ch_ids:
                    key = (date_key, ch_id, product_id)
                    cost[key] = cost.get(key, Decimal("0")) + share

        if not cost:
            return []

        wanted_products = {product_id for _, _, product_id in cost}
        order_ids = cls._ads_landing_orders_queryset(
            date_from,
            date_to,
            user=user,
            channel_ids=channel_ids,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ).values_list("id", flat=True)
        closed = Q(order__assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)
        item_qs = OrderItem.objects.filter(
            order_id__in=order_ids, product_id__in=wanted_products
        )
        group_fields = ["order__channel_id", "product_id"]
        if group_by_date:
            item_qs = item_qs.annotate(d=TruncDate("order__created_at"))
            group_fields.insert(0, "d")
        metrics: dict[tuple[str | None, int, int], dict] = {}
        for row in item_qs.values(*group_fields).annotate(
            leads=Count("order_id", distinct=True),
            closed_orders=Count("order_id", distinct=True, filter=closed),
            revenue=Sum("line_total", filter=closed),
        ):
            day = row.get("d")
            if group_by_date and day is None:
                continue
            key = (
                day.isoformat() if group_by_date else None,
                row["order__channel_id"],
                row["product_id"],
            )
            metrics[key] = row

        # Nhãn SP đọc thẳng từ catalog: SP có chi tiêu mà chưa ra đơn nào thì không
        # có dòng OrderItem để mượn tên.
        labels = {
            row["id"]: row
            for row in Product.objects.filter(id__in=wanted_products).values("id", "sku", "name")
        }

        result: list[dict] = []
        for (date_key, channel_id, product_id), spend in cost.items():
            row = metrics.get((date_key, channel_id, product_id)) or {}
            label = labels.get(product_id) or {}
            result.append(
                {
                    "date": date_key,
                    "channel_id": channel_id,
                    "product_id": product_id,
                    "product__sku": label.get("sku"),
                    "product__name": label.get("name"),
                    "revenue": str(row.get("revenue") or Decimal("0")),
                    "cost": str(spend),
                    "closed_orders": row.get("closed_orders") or 0,
                    "leads": row.get("leads") or 0,
                }
            )
        result.sort(
            key=lambda r: (
                r["date"] or "",
                -Decimal(r["revenue"]),
                -Decimal(r["cost"]),
                r["product__name"] or "",
            )
        )
        return result

    @classmethod
    def summary_ads_assigned_cost(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ) -> str:
        """Chi phí ads đã quy được về một sản phẩm, trong cùng phạm vi kênh của báo cáo.

        Frontend lấy ``tổng chi phí (bảng đang hiện) − số này`` ra rổ "Chưa gán chiến
        dịch". Làm bằng phép trừ chứ không cộng riêng phần ``product_id IS NULL``, để
        hai vế LUÔN khớp tổng: chi tiêu Facebook đọc ở grain ad không khớp tuyệt đối
        với grain account (chốt so khớp ~0.98), và phần chênh grain đó đúng là "chưa
        quy được về sản phẩm nào" nên thuộc về rổ chưa gán.
        """
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        if excluded_ids is None:
            excluded_ids = _ads_excluded_channel_ids()
        channel_qs = cls._ads_report_channel_qs(user, sale_channel_ids, excluded_ids, channel_ids)
        link_rows = (
            ChannelAdAccount.objects.filter(channel__in=channel_qs)
            .order_by()
            .values_list("provider", "account_id")
        )
        fb_ids = [
            acc for provider, acc in link_rows if provider == ChannelAdAccount.Provider.FACEBOOK
        ]
        google_ids = [
            acc for provider, acc in link_rows if provider != ChannelAdAccount.Provider.FACEBOOK
        ]

        total = Decimal("0")
        for source, account_ids, multiplier in (
            (
                AdsSpendSource.FACEBOOK,
                fb_ids,
                facebook_ads_cost_multiplier(_parse_report_date(date_from)),
            ),
            (AdsSpendSource.GOOGLE, google_ids, GOOGLE_ADS_DAILY_COST_MULTIPLIER),
        ):
            if not account_ids:
                continue
            provider = ads_spend_provider(source)
            # Trải sang mọi bản sao TKQC — chi tiêu có thể treo dưới bản chưa được map.
            scope = provider.account_scope(account_ids)
            rows = provider.spend_rows_by_account_product(
                account_ids=scope.ids or [],
                date_from=_parse_report_date(date_from),
                date_to=_parse_report_date(date_to),
            )
            for row in rows:
                if row["product_id"] is None:
                    continue
                total += (row["spend"] or Decimal("0")) * multiplier
        return str(total)

    @classmethod
    def _summary_ads_closed_grouped(
        cls,
        group_field: str,
        date_from: str | None,
        date_to: str | None,
        *,
        user,
        channel_ids: list[int] | None,
        sale_channel_ids: list[int] | None,
        excluded_ids: set[int] | None,
    ):
        """Đơn đã chốt của báo cáo ads GROUP BY ``group_field`` (cùng bộ metrics
        với ``summary_ads_revenue_by_channel``, không có cột chi phí — spend
        không tách được theo thuộc tính khách hàng)."""
        return (
            cls._ads_landing_orders_queryset(
                date_from,
                date_to,
                user=user,
                channel_ids=channel_ids,
                sale_channel_ids=sale_channel_ids,
                excluded_ids=excluded_ids,
            )
            .filter(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)
            .values(group_field)
            .annotate(
                closed_orders=Count("id"),
                revenue=Sum("total_after_discount"),
                delivered_orders=Count(
                    "id", filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
                ),
                delivered_revenue=Sum(
                    "total_after_discount",
                    filter=Q(status=DELIVERED_SUCCESS_ORDER_STATUS),
                ),
            )
        )

    @classmethod
    def summary_ads_revenue_by_province(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ):
        """Phân bổ đơn chốt/doanh thu theo tỉnh/thành (``Order.province``).

        Tên tỉnh gộp theo giá trị đã trim (KiotViet đôi khi trả kèm khoảng
        trắng); rỗng → ``province=None`` để FE hiển thị "Không rõ". Không
        chuẩn hóa biến thể tên (Hà Nội vs Thành phố Hà Nội) — hiển thị nguyên
        văn, cân nhắc sau nếu dữ liệu thật nhiễu.
        """
        merged: dict[str | None, dict] = {}
        for row in cls._summary_ads_closed_grouped(
            "province",
            date_from,
            date_to,
            user=user,
            channel_ids=channel_ids,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ):
            key = (row["province"] or "").strip() or None
            entry = merged.setdefault(
                key,
                {
                    "province": key,
                    "closed_orders": 0,
                    "revenue": Decimal("0"),
                    "delivered_orders": 0,
                    "delivered_revenue": Decimal("0"),
                },
            )
            entry["closed_orders"] += row["closed_orders"]
            entry["revenue"] += row["revenue"] or Decimal("0")
            entry["delivered_orders"] += row["delivered_orders"]
            entry["delivered_revenue"] += row["delivered_revenue"] or Decimal("0")
        rows = sorted(
            merged.values(),
            key=lambda r: (-r["revenue"], r["province"] or ""),
        )
        for entry in rows:
            entry["revenue"] = str(entry["revenue"])
            entry["delivered_revenue"] = str(entry["delivered_revenue"])
        return rows

    @classmethod
    def summary_ads_revenue_by_gender(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        user=None,
        channel_ids: list[int] | None = None,
        sale_channel_ids: list[int] | None = None,
        excluded_ids: set[int] | None = None,
    ):
        """Phân bổ đơn chốt/doanh thu theo giới tính khách hàng.

        ``gender``: 0 = Nữ, 1 = Nam (Customer.gender), ``None`` = chưa có
        thông tin — nhãn hiển thị do FE quyết định.
        """
        rows = list(
            cls._summary_ads_closed_grouped(
                "customer__gender",
                date_from,
                date_to,
                user=user,
                channel_ids=channel_ids,
                sale_channel_ids=sale_channel_ids,
                excluded_ids=excluded_ids,
            )
        )
        for row in rows:
            row["gender"] = row.pop("customer__gender")
            row["revenue"] = str(row["revenue"] or Decimal("0"))
            row["delivered_revenue"] = str(row["delivered_revenue"] or Decimal("0"))
        return sorted(
            rows,
            key=lambda r: (-Decimal(r["revenue"]), r["gender"] if r["gender"] is not None else -1),
        )
