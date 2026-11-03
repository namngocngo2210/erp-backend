"""Đồng bộ đơn hàng Foxia vào bảng `orders` (order_source="foxia").

Một lượt gồm 5 bước, lô 500 đơn một:

    1. `sale.order`      → upsert Order + FoxiaOrderLink
    2. `cohoi`           → enrich nguồn/kênh/thương hiệu (đơn KHÔNG mang các chiều này)
    3. `sale.order.line` → FoxiaOrderLine
    4. `donvanchuyen`    → FoxiaShipment + trạng thái DVVC
    5. tổng phí vận đơn  → Order.shipping_fee

Hai điều bắt buộc, mỗi cái vì một lý do đã trả giá ở nơi khác:

* **Mọi ghi vào `orders` đi qua bulk_create/bulk_update.** Không phải để nhanh
  (dù có nhanh), mà để KHÔNG phát `post_save`. Hai subscriber đang lắng nghe:
  `cnv_loyalty` sẽ đẩy đơn của đối tác sang hệ thống loyalty thật, và
  `customers.refresh_customer_type` chạy một count query mỗi đơn trên sentinel
  có hàng chục nghìn đơn.
* **Không publish `order.created`.** Sự kiện đó được phát tường minh ở
  `order_service_commands`, đường này không chạm tới — và phải giữ nguyên như
  vậy: một lượt 500 đơn là 500 event WebSocket, đã có hai sự cố production vì
  fanout của nó.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.foxia.application.services.sentinel_service import (
    get_sentinel_channel,
    get_sentinel_customer,
)
from apps.foxia.contracts.dtos import many2one_id, many2one_name
from apps.foxia.domain.datetime_utils import from_foxia_utc, to_foxia_utc
from apps.foxia.domain.order_specs import (
    LEAD_ENRICH_FIELDS,
    ORDER_FIELDS,
    ORDER_LINE_FIELDS,
    SHIPMENT_FIELDS,
    OrderSyncMode,
)
from apps.foxia.domain.status_text import normalize_status
from apps.foxia.metrics import observe_run
from apps.foxia.models import (
    FoxiaBrand,
    FoxiaBranch,
    FoxiaChannel,
    FoxiaOrderLine,
    FoxiaOrderLink,
    FoxiaSaleTeam,
    FoxiaShipment,
    FoxiaSource,
    FoxiaStage,
    FoxiaSyncCursor,
    FoxiaSyncRun,
    FoxiaSyncStatus,
    FoxiaUser,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 500


@dataclass
class OrderSyncResult:
    mode: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    error: str = ""
    unknown_statuses: set = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.error


class FoxiaOrderSyncService:
    def __init__(self, client):
        self.client = client
        # Bản ghi giữ chỗ dùng lại cho mọi đơn cùng chi nhánh; cache theo
        # vòng đời service, không phải theo trang.
        self._sentinel_cache: dict = {}
        self._sentinel_channel = None

    # ── điều phối ────────────────────────────────────────────────────────────

    def sync(self, mode: str, lookback_days: int | None = None) -> OrderSyncResult:
        date_field = OrderSyncMode.DATE_FIELD[mode]
        window_from, window_to = self._window(mode, lookback_days)
        run = FoxiaSyncRun.objects.create(
            resource=mode, window_from=window_from, window_to=window_to
        )
        result = OrderSyncResult(mode=mode)

        conditions = [
            [date_field, ">=", to_foxia_utc(window_from)],
            [date_field, "<=", to_foxia_utc(window_to)],
        ]
        try:
            for rows in self.client.iter_all(
                "sale.order",
                conditions=conditions,
                fields=list(ORDER_FIELDS),
                order=f"{date_field} asc, id asc",
                page_size=_PAGE_SIZE,
            ):
                result.fetched += len(rows)
                self._process_page(rows, result)
        except Exception as exc:  # noqa: BLE001 — ghi lại rồi ném lên cho task
            result.error = str(exc)
            self._finish(run, result, FoxiaSyncStatus.FAILED)
            raise

        self._finish(run, result, FoxiaSyncStatus.SUCCESS)
        # Mốc CHỈ tiến khi cả lượt thành công. Lượt lỗi giữa chừng để nguyên mốc
        # cũ, lượt sau quét lại — vô hại vì upsert idempotent theo foxia_id.
        self._advance_cursor(mode, window_to)
        return result

    def sync_window(self, mode: str, window_from, window_to) -> OrderSyncResult:
        """Quét một khoảng tường minh (backfill). Không đụng cursor."""
        run = FoxiaSyncRun.objects.create(
            resource=f"{mode}:backfill", window_from=window_from, window_to=window_to
        )
        result = OrderSyncResult(mode=mode)
        date_field = OrderSyncMode.DATE_FIELD[mode]
        conditions = [
            [date_field, ">=", to_foxia_utc(window_from)],
            [date_field, "<=", to_foxia_utc(window_to)],
        ]
        try:
            for rows in self.client.iter_all(
                "sale.order",
                conditions=conditions,
                fields=list(ORDER_FIELDS),
                order=f"{date_field} asc, id asc",
                page_size=_PAGE_SIZE,
            ):
                result.fetched += len(rows)
                self._process_page(rows, result)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            self._finish(run, result, FoxiaSyncStatus.FAILED)
            raise
        self._finish(run, result, FoxiaSyncStatus.SUCCESS)
        return result

    def _process_page(self, rows: list[dict], result: OrderSyncResult) -> None:
        rows = [row for row in rows if many2one_id(row.get("id"))]
        if not rows:
            return
        enrichment = self._fetch_lead_enrichment(rows)
        with transaction.atomic():
            links = self._upsert_orders(rows, enrichment, result)
            if not links:
                return
            self._sync_lines(links, result)
            self._sync_shipments(links, result)
            self._recompute_shipping_fee(links)

    # ── bước 1: đơn ──────────────────────────────────────────────────────────

    def _upsert_orders(self, rows, enrichment: dict, result: OrderSyncResult) -> dict:
        from apps.orders.models import Order, OrderSource, OrderStatus

        catalogs = self._catalog_index(rows, enrichment)
        if self._sentinel_channel is None:
            self._sentinel_channel = get_sentinel_channel()
        channel = self._sentinel_channel
        foxia_ids = [many2one_id(row["id"]) for row in rows]
        existing = {
            link.foxia_id: link
            for link in FoxiaOrderLink.objects.filter(foxia_id__in=foxia_ids).select_related("order")
        }

        new_orders, updated_orders, new_links, updated_links = [], [], [], []

        for row in rows:
            foxia_id = many2one_id(row["id"])
            lead = enrichment.get(many2one_id(row.get("cohoi_id")) or -1, {})
            branch_pk = catalogs["branch"].get(many2one_id(row.get("congtycon_id")))
            stage_name = normalize_status(row.get("giaidoan_id"))
            if stage_name and stage_name not in catalogs["known_stage_names"]:
                # Mã trạng thái chưa từng thấy vẫn LƯU bình thường — đếm lại chỉ
                # để người vận hành biết tập giá trị thực tế có những gì.
                result.unknown_statuses.add(stage_name)

            order_values = {
                "channel": channel,
                "customer": self._sentinel_for(row),
                "order_source": OrderSource.FOXIA,
                "status": OrderStatus.NEW,
                "partner_delivery_status": stage_name,
                "name": many2one_name(row.get("name"))[:255],
                "order_code": many2one_name(row.get("name"))[:100],
                "total_amount": _money(row.get("amount_total")),
                "total_after_discount": _money(row.get("tongsauchietkhaubosung")),
                "has_address": False,
            }
            link_values = {
                "foxia_id": foxia_id,
                "foxia_name": many2one_name(row.get("name"))[:100],
                "cohoi_id": many2one_id(row.get("cohoi_id")),
                "ngaytao": from_foxia_utc(row.get("ngaytao")),
                "ngayxacnhan": from_foxia_utc(row.get("ngayxacnhan")),
                "ngayhoanthanh": from_foxia_utc(row.get("ngayhoanthanh")),
                "marketer_id": catalogs["user"].get(many2one_id(row.get("nhanvienmarketing_id"))),
                "salesperson_id": catalogs["user"].get(many2one_id(row.get("user_id"))),
                "sale_team_id": catalogs["team"].get(many2one_id(row.get("team_id"))),
                "branch_id": branch_pk,
                "source_id": catalogs["source"].get(lead.get("source")),
                "channel_id": catalogs["channel"].get(lead.get("channel")),
                "brand_id": catalogs["brand"].get(lead.get("brand")),
                "stage_id": catalogs["stage"].get(many2one_id(row.get("giaidoan_id"))),
                "stage_class": many2one_name(row.get("phanloaigiaidoan"))[:64],
                "return_status": many2one_name(row.get("trangthaihoantra"))[:64],
                "amount_total": _money(row.get("amount_total")),
                "amount_after_extra_discount": _money(row.get("tongsauchietkhaubosung")),
                "amount_collected": _money(row.get("tongtiendathu")),
            }

            link = existing.get(foxia_id)
            if link is None:
                new_orders.append((Order(**order_values), link_values))
                result.created += 1
            else:
                order = link.order
                for attr, value in order_values.items():
                    setattr(order, attr, value)
                updated_orders.append(order)
                for attr, value in link_values.items():
                    setattr(link, attr, value)
                updated_links.append(link)
                result.updated += 1

        if new_orders:
            # bulk_create KHÔNG phát post_save — đó là điểm mấu chốt, xem docstring.
            created = Order.all_objects.bulk_create([order for order, _ in new_orders])
            for order, link_values in zip(created, [values for _, values in new_orders]):
                new_links.append(FoxiaOrderLink(order=order, **link_values))
            FoxiaOrderLink.objects.bulk_create(new_links)

        if updated_orders:
            Order.all_objects.bulk_update(
                updated_orders,
                [
                    "partner_delivery_status",
                    "name",
                    "order_code",
                    "total_amount",
                    "total_after_discount",
                ],
            )
        if updated_links:
            FoxiaOrderLink.objects.bulk_update(updated_links, list(_LINK_UPDATE_FIELDS))

        touched = {link.foxia_id: link for link in new_links}
        touched.update({link.foxia_id: link for link in updated_links})
        return touched

    def _sentinel_for(self, row):
        """Customer giữ chỗ theo chi nhánh, có nhớ trong suốt vòng đời service.

        Số chi nhánh Foxia đếm trên đầu ngón tay còn một trang là 500 đơn — không
        cache thì đây là 500 query cho cùng vài bản ghi.
        """
        branch_foxia_id = many2one_id(row.get("congtycon_id"))
        if branch_foxia_id not in self._sentinel_cache:
            self._sentinel_cache[branch_foxia_id] = get_sentinel_customer(branch_foxia_id)
        return self._sentinel_cache[branch_foxia_id]

    # ── bước 2: enrich chiều marketing từ cohoi ──────────────────────────────

    def _fetch_lead_enrichment(self, rows: list[dict]) -> dict:
        """Đơn KHÔNG mang nguồn/kênh/thương hiệu — ba chiều đó chỉ có trên `cohoi`.

        Một request cho cả lô, lọc theo danh sách id. Đây KHÔNG phải đồng bộ
        lead: không kéo lead không sinh đơn, không tính CPL.
        """
        cohoi_ids = sorted({cid for row in rows if (cid := many2one_id(row.get("cohoi_id")))})
        if not cohoi_ids:
            return {}
        try:
            leads = self.client.search_read(
                "cohoi",
                conditions=[["id", "in", cohoi_ids]],
                fields=list(LEAD_ENRICH_FIELDS),
                limit=_PAGE_SIZE,
                order="id asc",
            )
        except Exception as exc:  # noqa: BLE001
            # Thiếu chiều marketing làm báo cáo nghèo đi, nhưng mất cả lô đơn thì
            # tệ hơn nhiều. Đơn vẫn về, chỉ nằm ở nhóm "Chưa ánh xạ".
            logger.warning("Foxia enrich cohoi thất bại, bỏ qua chiều marketing: %s", exc)
            return {}
        return {
            many2one_id(lead["id"]): {
                "source": many2one_id(lead.get("nguonkhachhang_id")),
                "channel": many2one_id(lead.get("kenhmarketing_id")),
                "brand": many2one_id(lead.get("thuonghieu_id")),
            }
            for lead in leads
            if many2one_id(lead.get("id"))
        }

    # ── bước 3: dòng sản phẩm ────────────────────────────────────────────────

    def _sync_lines(self, links: dict, result: OrderSyncResult) -> None:
        rows = self._fetch_children("sale.order.line", "order_id", links, ORDER_LINE_FIELDS)
        instances = []
        for row in rows:
            line_id = many2one_id(row.get("id"))
            link = links.get(many2one_id(row.get("order_id")))
            if not line_id or link is None:
                result.skipped += 1
                continue
            instances.append(
                FoxiaOrderLine(
                    link=link,
                    foxia_line_id=line_id,
                    foxia_product_id=many2one_id(row.get("product_id")),
                    product_name=many2one_name(row.get("product_id"))[:255],
                    qty=_money(row.get("product_uom_qty")),
                    subtotal=_money(row.get("price_subtotal")),
                )
            )
        if instances:
            FoxiaOrderLine.objects.bulk_create(
                instances,
                update_conflicts=True,
                update_fields=["link", "foxia_product_id", "product_name", "qty", "subtotal", "synced_at"],
                unique_fields=["foxia_line_id"],
            )
        self._write_product_summary(links)

    def _write_product_summary(self, links: dict) -> None:
        """Gộp tên sản phẩm vào Order.product để tra cứu được mà không phải join."""
        from apps.orders.models import Order

        orders = []
        for link in links.values():
            names = list(
                FoxiaOrderLine.objects.filter(link=link)
                .order_by("foxia_line_id")
                .values_list("product_name", flat=True)
            )
            if not names:
                continue
            link.order.product = ", ".join(name for name in names if name)[:2000]
            orders.append(link.order)
        if orders:
            Order.all_objects.bulk_update(orders, ["product"])

    # ── bước 4: vận đơn ──────────────────────────────────────────────────────

    def _sync_shipments(self, links: dict, result: OrderSyncResult) -> None:
        rows = self._fetch_children("donvanchuyen", "donhang_id", links, SHIPMENT_FIELDS)
        instances = []
        latest_by_link: dict[int, tuple[int, str]] = {}
        for row in rows:
            shipment_id = many2one_id(row.get("id"))
            link = links.get(many2one_id(row.get("donhang_id")))
            if not shipment_id or link is None:
                result.skipped += 1
                continue
            raw_status = normalize_status(row.get("trangthai"))
            instances.append(
                FoxiaShipment(
                    link=link,
                    foxia_id=shipment_id,
                    name=many2one_name(row.get("name"))[:128],
                    raw_status=raw_status,
                    actual_ship_fee=_money(row.get("phivanchuyenthucte")),
                    completed_at=from_foxia_utc(row.get("ngayhoanthanhdon")),
                    returned_at=from_foxia_utc(row.get("ngayhoantradon")),
                )
            )
            # Đơn nhiều vận đơn (giao lại, hoàn) → trạng thái hiển thị lấy vận
            # đơn MỚI NHẤT theo id.
            previous = latest_by_link.get(link.foxia_id)
            if previous is None or shipment_id > previous[0]:
                latest_by_link[link.foxia_id] = (shipment_id, raw_status)

        if instances:
            FoxiaShipment.objects.bulk_create(
                instances,
                update_conflicts=True,
                update_fields=[
                    "link", "name", "raw_status", "actual_ship_fee",
                    "completed_at", "returned_at", "synced_at",
                ],
                unique_fields=["foxia_id"],
            )
        self._write_carrier_status(links, latest_by_link)

    @staticmethod
    def _write_carrier_status(links: dict, latest_by_link: dict) -> None:
        from apps.orders.models import Order

        orders = []
        for foxia_id, (_shipment_id, raw_status) in latest_by_link.items():
            link = links.get(foxia_id)
            if link is None:
                continue
            link.order.partner_dvvc_delivery_status = raw_status
            orders.append(link.order)
        if orders:
            Order.all_objects.bulk_update(orders, ["partner_dvvc_delivery_status"])

    # ── bước 5: phí ship ─────────────────────────────────────────────────────

    @staticmethod
    def _recompute_shipping_fee(links: dict) -> None:
        """Order.shipping_fee = TỔNG phí mọi vận đơn của đơn.

        Cộng tổng chứ không lấy vận đơn mới nhất: một đơn giao lại hai lần rồi
        hoàn là ba lần trả phí vận chuyển. Tính lại từ đầu mỗi lượt (không cộng
        dồn vào giá trị cũ) nên chạy lại đồng bộ không nhân đôi con số.

        GIẢ ĐỊNH CHƯA KIỂM CHỨNG: Foxia trả phí RIÊNG từng vận đơn. Nếu vận đơn
        sau đã cộng dồn vận đơn trước thì phép cộng này tính trùng — phải đối
        chiếu tay trên đơn nhiều vận đơn trước khi tin số.
        """
        from apps.orders.models import Order

        if not links:
            return
        totals = dict(
            FoxiaShipment.objects.filter(link__in=links.values())
            .values_list("link_id")
            .annotate(total=Sum("actual_ship_fee"))
        )
        orders = []
        for link in links.values():
            total = totals.get(link.pk)
            if total is None:
                continue
            link.order.shipping_fee = total
            orders.append(link.order)
        if orders:
            Order.all_objects.bulk_update(orders, ["shipping_fee"])

    # ── tiện ích ─────────────────────────────────────────────────────────────

    def _fetch_children(self, resource: str, parent_field: str, links: dict, fields) -> list[dict]:
        """Kéo bản ghi con theo danh sách id đơn của lô hiện tại."""
        order_ids = sorted(links.keys())
        if not order_ids:
            return []
        rows: list[dict] = []
        for pages in self.client.iter_all(
            resource,
            conditions=[[parent_field, "in", order_ids]],
            fields=list(fields),
            order=f"{parent_field} asc, id asc",
            page_size=_PAGE_SIZE,
        ):
            rows.extend(pages)
        return rows

    @staticmethod
    def _catalog_index(rows, enrichment) -> dict:
        """Dịch id Foxia → pk nội bộ cho mọi danh mục liên quan, một lượt query.

        Nạp sẵn theo id xuất hiện trong lô thay vì query từng đơn: 6 query cho
        cả trang 500 đơn thay vì hàng nghìn.
        """

        def ids(*keys):
            out = set()
            for row in rows:
                for key in keys:
                    value = many2one_id(row.get(key))
                    if value:
                        out.add(value)
            return out

        lead_ids = {"source": set(), "channel": set(), "brand": set()}
        for lead in enrichment.values():
            for key in lead_ids:
                if lead.get(key):
                    lead_ids[key].add(lead[key])

        def index(model, wanted):
            if not wanted:
                return {}
            return dict(model.objects.filter(foxia_id__in=wanted).values_list("foxia_id", "pk"))

        return {
            "branch": index(FoxiaBranch, ids("congtycon_id")),
            "user": index(FoxiaUser, ids("nhanvienmarketing_id", "user_id")),
            "team": index(FoxiaSaleTeam, ids("team_id")),
            "stage": index(FoxiaStage, ids("giaidoan_id")),
            "source": index(FoxiaSource, lead_ids["source"]),
            "channel": index(FoxiaChannel, lead_ids["channel"]),
            "brand": index(FoxiaBrand, lead_ids["brand"]),
            "known_stage_names": {
                normalize_status(name)
                for name in FoxiaStage.objects.values_list("name", flat=True)
            },
        }

    @staticmethod
    def _window(mode: str, lookback_days: int | None):
        """Cửa sổ quét: từ mốc đã đồng bộ lùi lại N ngày, tới hiện tại.

        Lùi lại là bắt buộc vì Foxia không cấp `write_date` — không có cách nào
        biết bản ghi nào vừa sửa, chỉ có thể quét lại một khoảng gần đây.
        """
        from django.conf import settings

        days = lookback_days
        if days is None:
            days = OrderSyncMode.DEFAULT_LOOKBACK_DAYS[mode]
            days = int(getattr(settings, "FOXIA_SYNC_LOOKBACK_DAYS", 0) or days)
        now = timezone.now()
        cursor = FoxiaSyncCursor.objects.filter(resource=mode).first()
        anchor = cursor.synced_until if cursor else now - timedelta(days=days)
        return anchor - timedelta(days=days), now

    @staticmethod
    def _advance_cursor(mode: str, window_to) -> None:
        FoxiaSyncCursor.objects.update_or_create(
            resource=mode,
            defaults={"synced_until": window_to, "last_run_at": timezone.now(), "last_error": ""},
        )

    @staticmethod
    def _finish(run: FoxiaSyncRun, result: OrderSyncResult, status: str) -> None:
        run.status = status
        run.fetched = result.fetched
        run.created = result.created
        run.updated = result.updated
        run.skipped = result.skipped
        run.error_message = result.error[:2000]
        if result.unknown_statuses and not result.error:
            # Không phải lỗi — là thông tin để dựng bảng ánh xạ trạng thái sau này.
            run.error_message = "Trạng thái chưa có trong danh mục: " + ", ".join(
                sorted(result.unknown_statuses)
            )[:2000]
        run.finished_at = timezone.now()
        observe_run(
            run.resource,
            status,
            run.fetched,
            (run.finished_at - run.started_at).total_seconds(),
        )
        run.save(
            update_fields=[
                "status", "fetched", "created", "updated", "skipped",
                "error_message", "finished_at",
            ]
        )


_LINK_UPDATE_FIELDS = (
    "foxia_name", "cohoi_id", "ngaytao", "ngayxacnhan", "ngayhoanthanh",
    "marketer", "salesperson", "sale_team", "branch",
    "source", "channel", "brand", "stage", "stage_class", "return_status",
    "amount_total", "amount_after_extra_discount", "amount_collected", "synced_at",
)


def _money(value) -> Decimal:
    """Ép số tiền/số lượng về Decimal.

    Odoo trả `False` cho ô trống và float cho số — đi thẳng vào DecimalField là
    lỗi kiểu hoặc sai số nhị phân. Qua str() để giữ đúng giá trị thập phân.
    """
    if value is None or value is False or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")
