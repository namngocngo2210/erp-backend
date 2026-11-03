"""Định nghĩa export của app inventory — đăng ký vào engine ở InventoryConfig.ready().

Ba `kind`, bám nguyên logic của 3 view đồng bộ cũ (cả 3 đã xoá ở Task 18):
- `stock`            ← `stock_views.export_stock`
- `dispatch_history` ← `flow_history_views.issue_export`
- `return_history`   ← `flow_history_views.return_export`

Không có nhánh nào trong 3 kind này cần lọc theo user (không có khái niệm "kho
riêng của user" hay "lịch sử riêng của user") — `check_permission` là nơi DUY
NHẤT cần `user` thật, và nó nhận `user` trực tiếp từ hợp đồng `ExportDefinition`
(không phải qua `params`) nên KHÔNG cần nhét `user_id` vào params như khuôn của
`OrdersExport` (đó là khuôn dùng khi `estimate`/`write` cũng cần biết user).
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.exports.application.registry import ExportDefinition, register_export
from apps.inventory.application.services.combo_expansion import ComboExpander
from apps.inventory.application.services.issue_document_history import MergedIssueHistory
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.stock_as_of_service import StockAsOfService
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.application.services.transfer_document_history import (
    MergedReturnHistory,
)
from apps.inventory.flow_filters import int_csv, int_or_none, parse_warehouse_ids
from apps.inventory.infrastructure.flow_history_excel import (
    _display_qty,
    _fmt_dt,
    _issue_order_code,
    _pending_mark,
    _truncation_rows,
)
from apps.inventory.infrastructure.stock_excel import _slug
from apps.inventory.models import PendingScanKind, StockState
from apps.orders.models import Order
from apps.users.auth_core import user_has_any_permission
from apps.warehouses.models import Warehouse

# Trần an toàn cho export tồn kho — MỘT kho khổng lồ không được kéo sập worker.
# Đặt tên riêng khác `stock_views._EXPORT_MAX_ROWS` vì đây là bản sao độc lập
# dùng cho luồng async (view đồng bộ cũ còn nguyên, xem module docstring).
_STOCK_MAX_ROWS = 20000

# Trần số dòng một file export lịch sử xuất/nhập hoàn — GIỮ NGUYÊN 5000, khớp
# `flow_history_views._EXPORT_LIMIT`. Vượt trần KHÔNG bị chặn (khác `stock` ở
# trên): vẫn xuất trang đầu, chèn dòng cảnh báo (`_truncation_rows`), Tổng vẫn
# tính trên TOÀN BỘ kỳ lọc.
_EXPORT_LIMIT = 5000

_STOCK_EXPORT_PERMISSION_DENIED = "Bạn không có quyền thực hiện hành động này."


def _valid_date(value: str) -> bool:
    if not value:
        return True
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _check_stock_export_permission(user) -> None:
    """Gate `stock:export` dùng chung cho cả 3 kind — mirror
    `require_permission("stock:export")` của cả 3 view đồng bộ cũ."""
    if not user_has_any_permission(user, "stock:export"):
        raise PermissionDenied(_STOCK_EXPORT_PERMISSION_DENIED)


def _common_filters(raw_params: dict) -> dict:
    """Chuẩn hoá bộ lọc chung cho 2 luồng lịch sử — mirror
    `transport/views/flow_history_shared._common_filters`, chỉ đổi nguồn đọc từ
    `request.query_params` (luôn là chuỗi) sang `raw_params` (dict JSON thô do
    FE gửi lên qua `create()`). `product_ids`/`warehouse_ids` GIỮ NGUYÊN quy tắc
    parse chuỗi "1,5,9" phân tách bởi dấu phẩy — FE gửi cùng định dạng chuỗi như
    query cũ; `int_csv` nhận thêm cả list để một job gửi mảng không bị bỏ lọc
    âm thầm.
    """
    date_from = str(raw_params.get("date_from") or "")
    date_to = str(raw_params.get("date_to") or "")
    if not _valid_date(date_from) or not _valid_date(date_to):
        raise ValidationError({"detail": "date_from/date_to phải dạng YYYY-MM-DD."})
    return {
        "date_from": date_from,
        "date_to": date_to,
        # Lọc NHIỀU kho: cùng luật đọc tham số với view (chấp nhận cả
        # `warehouse_id` đơn lẻ của job cũ đang nằm trong hàng đợi).
        "warehouse_ids": parse_warehouse_ids(
            raw_params.get("warehouse_ids"), raw_params.get("warehouse_id")
        ),
        "product": str(raw_params.get("product") or ""),
        "product_ids": int_csv(raw_params.get("product_ids")),
        "order": str(raw_params.get("order") or ""),
        "carrier": str(raw_params.get("carrier") or "").strip(),
    }


def _export_truncated(total: int) -> int | None:
    return total if total > _EXPORT_LIMIT else None


def _kiot_names_by_order(order_ids) -> dict[int, str]:
    """Tên kiot (kênh KiotViet) của từng đơn — MỘT query gộp cho cả file.

    Chỉ có ở file, không có trên màn hình: cột này để người xem đối chiếu đơn với
    kiot mà không phải mở lại từng đơn.

    Đọc qua join `kiotviet_channel__name` chứ KHÔNG import `apps.kiotviet`:
    `inventory` nằm dưới `kiotviet` trong phân tầng import (xem `.importlinter`)
    nên import ngược lên là vi phạm. Dòng không thuộc đơn nào (phiếu xuất kho,
    phiếu chuyển kho, mã chờ khớp) mang `order_id` None và không có mặt trong
    map — nơi gọi tra bằng `.get(..., "")` nên ô để trống.
    """
    ids = {oid for oid in order_ids if oid}
    if not ids:
        return {}
    return {
        oid: name or ""
        for oid, name in Order.objects.filter(id__in=ids).values_list(
            "id", "kiotviet_channel__name"
        )
    }


_STOCK_CHUNK_SIZE = 2000
_OVERFLOW = object()  # sentinel: `_stock_chunks` báo đã vượt `limit`


def _stock_chunks(iterator, *, limit: int):
    """Gom StockItem thành các lô ~`_STOCK_CHUNK_SIZE`, KHÔNG cắt đôi một cặp
    (kho, mã) — phần đuôi lô thuộc cặp cuối được giữ lại cho lô kế. Yield
    ``_OVERFLOW`` (rồi dừng) khi số dòng vượt `limit`: caller biết đã cắt mà
    không phải kéo hết resultset. Yêu cầu iterator đã sắp theo (mã, kho)."""
    buffer: list = []
    seen = 0
    for item in iterator:
        seen += 1
        if seen > limit:
            if buffer:
                yield buffer
            yield _OVERFLOW
            return
        buffer.append(item)
        if len(buffer) >= _STOCK_CHUNK_SIZE:
            key = (buffer[-1].warehouse_id, buffer[-1].product_id)
            cut = len(buffer)
            while cut > 0 and (buffer[cut - 1].warehouse_id, buffer[cut - 1].product_id) == key:
                cut -= 1
            if cut == 0:
                # Cả lô là MỘT cặp (chunk nhỏ hơn số state/cặp): xả ngay sẽ cắt đôi
                # cặp → pivot ra 2 dòng cho cùng (kho, mã). Giữ buffer chờ cặp kế
                # (bounded: một cặp tối đa 3 state).
                continue
            yield buffer[:cut]
            buffer = buffer[cut:]
    if buffer:
        yield buffer


class StockExport(ExportDefinition):
    """Tồn kho hiện tại của MỘT kho, pivot theo state — mirror `export_stock`."""

    kind = "stock"

    def validate(self, user, raw_params: dict) -> dict:
        # warehouse_id BẮT BUỘC: thiếu tham số này thì một job trần sẽ dump tồn
        # của toàn hệ thống — cùng lý do view đồng bộ cũ đòi tham số này.
        warehouse_id = int_or_none(raw_params.get("warehouse_id"))
        if not warehouse_id:
            raise ValidationError({"detail": "Thiếu tham số warehouse_id."})
        warehouse = Warehouse.objects.filter(id=warehouse_id).only("code").first()
        if warehouse is None:
            raise ValidationError({"detail": "Kho không tồn tại."})
        as_of = str(raw_params.get("as_of") or "")
        if not _valid_date(as_of):
            raise ValidationError({"detail": "as_of phải dạng YYYY-MM-DD."})
        if as_of and datetime.strptime(as_of, "%Y-%m-%d").date() > timezone.localdate():
            raise ValidationError({"detail": "as_of không được ở tương lai."})
        return {
            "warehouse_id": warehouse_id,
            # Cache mã kho ngay ở bước validate: `file_name` cần nó nhưng không
            # nhận `user`/request để tự tra lại, và tra lại ở `write` (chạy ở
            # worker, có thể trễ) chỉ tổ tốn thêm 1 query cho một giá trị đã có.
            "warehouse_code": warehouse.code,
            "search": str(raw_params.get("search") or ""),
            "as_of": as_of,
        }

    def check_permission(self, user, params: dict) -> None:
        _check_stock_export_permission(user)

    def estimate(self, params: dict) -> int | None:
        count = StockQueryService.list_stock(
            warehouse_id=params["warehouse_id"], search=params["search"]
        ).count()
        if count > _STOCK_MAX_ROWS:
            # Cắt bớt thầm lặng nguy hiểm hơn báo lỗi: người dùng sẽ đối chiếu
            # bằng file thiếu dòng mà không hề biết — GIỮ NGUYÊN thông báo cũ.
            raise ValidationError(
                {
                    "detail": (
                        f"Kho có hơn {_STOCK_MAX_ROWS} dòng tồn — thu hẹp bằng ô "
                        "tìm kiếm rồi xuất lại."
                    )
                }
            )
        return count

    def file_name(self, params: dict) -> str:
        slug = _slug(params["warehouse_code"])
        # Có mốc thì lấy CHÍNH mốc đó làm hậu tố, không phải ngày chạy job: hai
        # lần xuất cùng một mốc ở hai ngày khác nhau phải ra cùng tên file.
        stamp = (
            params["as_of"].replace("-", "")
            if params.get("as_of")
            else f"{timezone.localtime(timezone.now()):%Y%m%d}"
        )
        return f"ton-kho-{slug}-{stamp}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        headers = ["Kho", "SKU", "Tên sản phẩm", "Bán được", "Bảo hành", "Lỗi", "Tổng", "RR3", "RR7"]
        writer.write_header(headers)
        qs = StockQueryService.list_stock(
            warehouse_id=params["warehouse_id"], search=params["search"]
        )
        # Lấy dư 1 dòng để phát hiện vượt trần mà không cần COUNT ngay — CÙNG kỹ
        # thuật với view đồng bộ gốc (`export_stock`). BẮT BUỘC re-check ở ĐÂY,
        # KHÔNG tin `estimate()` đã chặn: kiến trúc async tách `estimate()`
        # (chạy 1 lần, đồng bộ, lúc tạo job) khỏi `write()` (chạy sau đó ở
        # worker, có thể trễ vài phút nếu hàng đợi đang bận) — giữa hai mốc đó
        # tồn kho có thể phình vượt trần mà job đã tạo rồi thì không huỷ được
        # nữa. Bỏ LIMIT ở đây đúng là kịch bản kéo cả resultset vào RAM mà
        # comment gốc cảnh báo — TOCTOU do chính kiến trúc async sinh ra, không
        # có ở view đồng bộ (check và fetch nằm chung 1 request ở đó).
        # Streaming theo LÔ (audit 2026-08-19 mở, đóng 2026-09-23): trước đây
        # `list(qs[:cap+1])` giữ tới 20.001 StockItem (+ product/warehouse) trong RAM
        # worker. Giờ `iterator(chunk_size)` + xử lý từng lô — as-of/run-rate/pivot
        # đều tính theo cặp (kho, mã) nên chỉ cần KHÔNG cắt một cặp qua hai lô
        # (`_stock_chunks` giữ lại đuôi lô cho lô sau; qs đã order theo sku, kho).
        # Trần vẫn được tự áp lại ở đây (TOCTOU do kiến trúc async — xem
        # docstring cũ): đọc dư 1 dòng để biết có vượt trần mà không COUNT.
        as_of = params.get("as_of") or ""
        as_of_date = (
            datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else None
        )
        use_as_of = as_of_date is not None and as_of_date < timezone.localdate()
        as_of_end = StockAsOfService.end_bound(as_of_date) if use_as_of else None

        count = 0
        consumed = 0
        truncated = False
        for chunk in _stock_chunks(
            qs.iterator(chunk_size=_STOCK_CHUNK_SIZE), limit=_STOCK_MAX_ROWS
        ):
            if chunk is _OVERFLOW:
                truncated = True
                break
            consumed += len(chunk)
            items = chunk
            if use_as_of:
                # Mốc = hôm nay thì tồn hiện tại đã đúng, khỏi trừ ngược.
                items = StockAsOfService.apply(
                    items, as_of_date, warehouse_id=params["warehouse_id"]
                )
            pairs = {(item.warehouse_id, item.product_id) for item in items}
            run_rates = StockQueryService.sales_run_rates(pairs, as_of_end=as_of_end)
            for row in StockQueryService.pivot_stock(items, run_rates):
                writer.write_row(
                    [
                        row["warehouse_name"],
                        row["product_sku"],
                        row["product_name"],
                        row[StockState.SELLABLE],
                        row[StockState.WARRANTY],
                        row[StockState.DEFECT],
                        row["total"],
                        row["rr3"],
                        row["rr7"],
                    ]
                )
                count += 1
                progress(count)
        total = None
        if truncated:
            # Vượt trần LÚC WRITE (dữ liệu phình sau khi job đã tạo): KHÔNG
            # raise — người dùng đã chờ job chạy xong, ném lỗi ở phút cuối tệ
            # hơn cắt bớt có cảnh báo (cùng tinh thần `_truncation_rows` của 2
            # luồng lịch sử). Đếm chính xác CHỈ khi thật sự vượt trần (nhánh
            # hiếm) để đường đi bình thường không tốn thêm 1 query COUNT.
            total = qs.count()
            message = (
                f"⚠️ Kho có {total} dòng tồn — file chỉ chứa {_STOCK_MAX_ROWS} dòng đầu "
                "(tồn kho tăng lên sau khi lệnh xuất được tạo). Vui lòng xuất lại để lấy đủ."
            )
            writer.write_row([message] + [""] * (len(headers) - 1))
            count += 1
            progress(count)


class DispatchHistoryExport(ExportDefinition):
    """Tab Xuất của Lịch sử nhập xuất — mirror `flow_history_views.issue_export`."""

    kind = "dispatch_history"

    def validate(self, user, raw_params: dict) -> dict:
        filters = _common_filters(raw_params)
        filters["group"] = "sku" if raw_params.get("group") == "sku" else ""
        return filters

    def check_permission(self, user, params: dict) -> None:
        _check_stock_export_permission(user)

    def estimate(self, params: dict) -> int | None:
        # Không có trần chặn ở luồng này (khác `stock`) — vượt `_EXPORT_LIMIT`
        # chỉ cắt bớt + cảnh báo, không từ chối. Đếm "rẻ" ở đây nghĩa là phải
        # chạy lại gần như nguyên vẹn `orders_page`/`skus_page` (nhiều query
        # gộp) — không rẻ, nên trả None đúng như hợp đồng cho phép.
        return None

    def file_name(self, params: dict) -> str:
        if params["group"] == "sku":
            return "lich-su-xuat-sku.xlsx"
        return "lich-su-xuat.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        filters = {
            k: params[k]
            for k in ("date_from", "date_to", "warehouse_ids", "product", "product_ids", "order", "carrier")
        }
        count = 0

        def _emit(row) -> None:
            nonlocal count
            writer.write_row(row)
            count += 1
            progress(count)

        if params["group"] == "sku":
            data = MergedIssueHistory.skus_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
            headers = [
                "SKU", "Tên", "Số đơn", "SL theo đơn", "SL phiếu kho",
                "SL chuyển kho", "SL xuất", "Trong đó từ combo",
            ]
            writer.write_header(headers)
            truncated = _export_truncated(data["total"])
            for row in _truncation_rows(headers, truncated, len(data["items"])):
                _emit(row)
            expander = ComboExpander.for_product_ids([r.get("product_id") for r in data["items"]])
            # `sale` là SL theo đơn đã chuẩn hoá qua `_display_qty` TRƯỚC khi nở —
            # nở rồi mới chuẩn hoá sẽ nhân cả phần đảo sổ.
            source = [
                {
                    "product_id": r.get("product_id"),
                    "sku": r.get("sku") or "",
                    "name": r.get("name") or "",
                    # Số đơn cộng dồn THÔ — xem docstring `merge_sku_rows`: một đơn
                    # vừa mua lẻ vừa mua combo chứa SKU đó sẽ bị đếm hai lần.
                    "count": r.get("order_count") or 0,
                    "sale": _display_qty(r.get("net"), r.get("dispatched")),
                    "doc": r.get("doc_quantity") or 0,
                    "transfer": r.get("transfer_quantity") or 0,
                    "total": _display_qty(
                        (r.get("net") or 0) + (r.get("doc_quantity") or 0) + (r.get("transfer_quantity") or 0),
                        (r.get("dispatched") or 0) + (r.get("doc_quantity") or 0) + (r.get("transfer_quantity") or 0),
                    ),
                }
                for r in data["items"]
            ]
            # Cộng dồn CHÍNH cột đã render ra file ("SL xuất") cho dòng tổng mới
            # bên dưới — đây là chế độ Theo SKU nên MỌI dòng chi tiết đều đã nở,
            # cộng đúng những gì người dùng nhìn thấy, không tính lại từ nguồn.
            merged_rows = list(expander.merge_sku_rows(source, ("sale", "doc", "transfer", "total")))
            for row in merged_rows:
                _emit(
                    [
                        row["sku"], row["name"], row["count"], row["sale"],
                        row["doc"], row["transfer"], row["total"], row["from_combo"],
                    ]
                )
            _emit(["Tổng theo đơn (SKU ghi sổ)", "", "", data["sale_quantity"], "", "", "", ""])
            _emit(["Tổng phiếu xuất kho (SKU ghi sổ)", "", "", "", data["doc_quantity"], "", "", ""])
            _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)", "", "", "", "", data["transfer_quantity"], "", ""])
            _emit(["Tổng cộng (SKU ghi sổ)", "", "", "", "", "", data["total_quantity"], ""])
            # Dòng tổng riêng của chế độ Theo SKU: cộng đúng cột "SL xuất" của
            # các dòng THẬT SỰ có trong file — xem module docstring của
            # `_display_qty` (đơn đảo sổ khiến SL per-line lệch aggregate theo
            # đơn NGAY CẢ khi kỳ lọc không có combo nào), không chỉ do nở combo.
            _emit(
                ["Tổng SL các dòng trong file", "", "", "", "", "", sum(row["total"] for row in merged_rows), ""]
            )
            return

        data = MergedIssueHistory.orders_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
        headers = [
            "Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kênh KiotViet", "Kho",
            "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Ghi chú",
            "Số SKU", "SL xuất",
        ]
        writer.write_header(headers)
        kiot_names = _kiot_names_by_order(r.get("order_id") for r in data["items"])
        # Ghi chú mọi mã quét gộp vào đúng dòng đơn (join theo mã vận đơn), rồi
        # nối tiếp vào ghi chú tay của CHÍNH dòng đó (`row["note"]`, do
        # `orders_page` trả sẵn) — cùng cách gộp của `ReturnHistoryExport`.
        notes_by_code = PendingScanService.scan_notes_by_code(
            kind=PendingScanKind.DISPATCH,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            warehouse_ids=filters["warehouse_ids"],
        )
        for row in data["items"]:
            # Ghi chú cũng gộp theo mã THỰC QUÉT (`scan_codes`, khớp tay vào đơn
            # mang mã khác) — join riêng theo mã vận đơn của đơn thì ghi chú
            # "trả bảo hành…" nhập lúc quét rớt khỏi file (ca prod 84859795173645).
            for code in [row.get("shipping_code"), *(row.get("scan_codes") or [])]:
                scan_note = notes_by_code.get(code) if code else None
                if not scan_note:
                    continue
                existing = (row.get("note") or "").strip()
                if not existing:
                    row["note"] = scan_note
                elif scan_note not in existing:
                    row["note"] = f"{existing}; {scan_note}"
        # Khối "chờ khớp" chỉ giữ mã KHÔNG trùng mã vận đơn với đơn đã xuất: một
        # đơn xuất kho tay + mã quét cùng mã báo FAILED ("đã xuất kho trước đó")
        # sẽ khiến mã vận đơn lặp 2 dòng nếu không loại — ghi chú của mã đó đã
        # gộp vào dòng đơn.
        dispatched_codes = {r.get("shipping_code") for r in data["items"] if r.get("shipping_code")}
        pending = [
            p
            for p in PendingScanService.list_unmatched(
                kind=PendingScanKind.DISPATCH,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                warehouse_ids=filters["warehouse_ids"],
                order=filters.get("order", ""),
                product=filters.get("product", ""),
                product_ids=filters.get("product_ids"),
                carrier=filters.get("carrier", ""),
            )
            if p.get("code") not in dispatched_codes
        ]
        truncated = _export_truncated(data["total"])
        for row in _truncation_rows(headers, truncated, len(data["items"])):
            _emit(row)

        # Nở SKU combo ra sản phẩm con — 2 query cho cả trang, không N+1.
        expander = ComboExpander.for_items(list(pending) + list(data["items"]))
        expanded_total = 0

        def _emit_lines(prefix: list, lines, suffix_before_qty: list) -> None:
            """Một dòng cho MỖI mã hàng; đơn chưa khớp (không có line) vẫn giữ 1 dòng."""
            nonlocal expanded_total
            rows = expander.expand(lines)
            if not rows:
                _emit(prefix + ["", "", ""] + suffix_before_qty + [""])
                return
            for r in rows:
                qty = _display_qty(r["qty"], r["gross"])
                expanded_total += qty
                _emit(prefix + [r["sku"], r["name"], r["combo_sku"]] + suffix_before_qty + [qty])

        for p in pending:
            _emit_lines(
                [
                    _fmt_dt(p.get("created_at")),
                    _pending_mark(p),
                    p.get("code") or "",
                    # Mã chờ khớp chưa thuộc đơn nào nên chưa có kiot.
                    "",
                    p.get("warehouse_name") or "",
                    p.get("performed_by") or "",
                ],
                p.get("lines"),
                [p.get("note") or "", p.get("sku_count") or ""],
            )
        for r in data["items"]:
            _emit_lines(
                [
                    _fmt_dt(r.get("last_at")),
                    _issue_order_code(r),
                    # Mã trên đơn + mã kho thực quét (nếu khác) cùng một ô để
                    # Ctrl+F mã trên nhãn kiện vẫn ra dòng đơn.
                    "; ".join(
                        c for c in [r.get("shipping_code"), *(r.get("scan_codes") or [])] if c
                    ),
                    # Phiếu xuất kho/chuyển kho không có đơn → ô trống.
                    kiot_names.get(r.get("order_id"), ""),
                    r.get("warehouse_name") or "",
                    r.get("performed_by") or "",
                ],
                r.get("lines"),
                [r.get("note") or "", r.get("sku_count") or 0],
            )
        # Footer ghi sổ (đối chiếu được với tồn kho) GIỮ NGUYÊN con số, chỉ nói rõ
        # trong nhãn là tính theo SKU ghi sổ — combo tính 1. Dòng cuối mới CỘNG
        # ĐÚNG các dòng CÓ TRONG FILE — nhãn KHÔNG nói "sau nổ combo" vì nó lệch
        # aggregate theo đơn NGAY CẢ khi kỳ lọc không có combo nào (đơn đảo sổ,
        # xem docstring `_display_qty`), không chỉ do nở combo.
        _emit(["Tổng theo đơn (SKU ghi sổ)"] + [""] * 10 + [data["sale_quantity"]])
        _emit(["Tổng phiếu xuất kho (SKU ghi sổ)"] + [""] * 10 + [data["doc_quantity"]])
        _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)"] + [""] * 10 + [data["transfer_quantity"]])
        _emit(["Tổng cộng (SKU ghi sổ)"] + [""] * 10 + [data["total_quantity"]])
        _emit(["Tổng SL các dòng trong file"] + [""] * 10 + [expanded_total])


class ReturnHistoryExport(ExportDefinition):
    """Tab Nhập hoàn của Lịch sử nhập xuất — mirror `flow_history_views.return_export`."""

    kind = "return_history"

    def validate(self, user, raw_params: dict) -> dict:
        filters = _common_filters(raw_params)
        filters["group"] = "sku" if raw_params.get("group") == "sku" else ""
        return filters

    def check_permission(self, user, params: dict) -> None:
        _check_stock_export_permission(user)

    def estimate(self, params: dict) -> int | None:
        # Cùng lý do với DispatchHistoryExport: không có trần chặn ở luồng này,
        # đếm "rẻ" đòi chạy gần trọn `list_page`/`skus_page` nên trả None.
        return None

    def file_name(self, params: dict) -> str:
        if params["group"] == "sku":
            return "lich-su-nhap-hoan-sku.xlsx"
        return "lich-su-nhap-hoan.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        filters = {
            k: params[k]
            for k in ("date_from", "date_to", "warehouse_ids", "product", "product_ids", "order", "carrier")
        }
        count = 0

        def _emit(row) -> None:
            nonlocal count
            writer.write_row(row)
            count += 1
            progress(count)

        if params["group"] == "sku":
            data = MergedReturnHistory.skus_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
            headers = [
                "SKU", "Tên", "Số phiếu", "SL hoàn", "SL chuyển kho", "SL nhập",
                "Trong đó từ combo",
            ]
            writer.write_header(headers)
            truncated = _export_truncated(data["total"])
            for row in _truncation_rows(headers, truncated, len(data["items"])):
                _emit(row)
            expander = ComboExpander.for_product_ids([r.get("product_id") for r in data["items"]])
            source = [
                {
                    "product_id": r.get("product_id"),
                    "sku": r.get("sku") or "",
                    "name": r.get("name") or "",
                    # Số phiếu cộng dồn THÔ — xem docstring `merge_sku_rows`: một
                    # phiếu vừa hoàn lẻ vừa hoàn combo chứa SKU đó sẽ bị đếm hai lần.
                    "count": r.get("return_count") or 0,
                    "qty": r.get("qty_total") or 0,
                    "transfer": r.get("transfer_quantity") or 0,
                    "total": (r.get("qty_total") or 0) + (r.get("transfer_quantity") or 0),
                }
                for r in data["items"]
            ]
            merged_rows = list(expander.merge_sku_rows(source, ("qty", "transfer", "total")))
            for row in merged_rows:
                _emit(
                    [
                        row["sku"], row["name"], row["count"], row["qty"],
                        row["transfer"], row["total"], row["from_combo"],
                    ]
                )
            _emit(["Tổng hoàn theo đơn (SKU ghi sổ)", "", "", data["return_quantity"], "", "", ""])
            _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)", "", "", "", data["transfer_quantity"], "", ""])
            _emit(["Tổng cộng (SKU ghi sổ)", "", "", "", "", data["total_quantity"], ""])
            # Dòng tổng riêng của chế độ Theo SKU — cùng lý do với nhánh dispatch
            # ở trên: cộng đúng cột "SL nhập" của các dòng thật sự có trong file.
            _emit(["Tổng SL các dòng trong file", "", "", "", "", sum(row["total"] for row in merged_rows), ""])
            return

        data = MergedReturnHistory.list_page(pagination={"limit": _EXPORT_LIMIT, "offset": 0}, **filters)
        headers = [
            "Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn",
            "Kênh KiotViet", "Kho", "Người thực hiện", "SKU", "Tên sản phẩm",
            "Thuộc combo", "Loại hoàn", "Ghi chú", "SL hoàn",
        ]
        writer.write_header(headers)
        # Kiot của phiếu trả = kiot của ĐƠN NGUỒN; phiếu trả không gắn đơn (hoàn
        # tay, phiếu chuyển kho) để trống.
        kiot_names = _kiot_names_by_order(
            r.get("source_order_id") for r in data["items"]
        )
        # Khối "chờ khớp" chỉ giữ mã KHÔNG trùng mã vận đơn với phiếu trả đã ghi
        # sổ — tránh lặp mã vận đơn 2 dòng. NHƯNG scan bị loại có thể là FAILED
        # (quét lại mã đã có phiếu → guard chống trùng chặn) mang ghi chú người
        # dùng KHÔNG nằm ở đâu khác — gộp note đó vào dòng phiếu trả cùng mã,
        # nối "; " nếu phiếu đã có note riêng (audit 2026-07-15b).
        entry_codes = {r.get("shipping_code") for r in data["items"] if r.get("shipping_code")}
        scan_notes = PendingScanService.scan_notes_by_code(
            kind=PendingScanKind.RETURN,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            warehouse_ids=filters["warehouse_ids"],
        )
        for row in data["items"]:
            code = row.get("shipping_code")
            scan_note = scan_notes.get(code) if code else None
            if not scan_note:
                continue
            existing = (row.get("note") or "").strip()
            if not existing:
                row["note"] = scan_note
            elif scan_note not in existing:
                row["note"] = f"{existing}; {scan_note}"
        pending = [
            p
            for p in PendingScanService.list_unmatched(
                kind=PendingScanKind.RETURN,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                warehouse_ids=filters["warehouse_ids"],
                with_return_note=True,
                order=filters.get("order", ""),
                product=filters.get("product", ""),
                product_ids=filters.get("product_ids"),
                carrier=filters.get("carrier", ""),
            )
            if p.get("code") not in entry_codes
        ]
        truncated = _export_truncated(data["total"])
        for row in _truncation_rows(headers, truncated, len(data["items"])):
            _emit(row)

        # Nở SKU combo ra sản phẩm con — 2 query cho cả trang, không N+1.
        expander = ComboExpander.for_items(list(pending) + list(data["items"]))
        expanded_total = 0

        def _emit_lines(prefix: list, lines, tail: list) -> None:
            """Một dòng cho MỖI mã hàng; phiếu chưa khớp (không có line) vẫn giữ 1 dòng."""
            nonlocal expanded_total
            rows = expander.expand(lines)
            if not rows:
                _emit(prefix + ["", "", ""] + tail + [""])
                return
            for r in rows:
                expanded_total += r["qty"]
                _emit(prefix + [r["sku"], r["name"], r["combo_sku"]] + tail + [r["qty"]])

        for p in pending:
            _emit_lines(
                [
                    _fmt_dt(p.get("created_at")),
                    _pending_mark(p),
                    "",
                    p.get("code") or "",
                    # Mã chờ khớp chưa thuộc đơn nào nên chưa có kiot.
                    "",
                    p.get("warehouse_name") or "",
                    p.get("performed_by") or "",
                ],
                p.get("lines"),
                ["", p.get("note") or ""],
            )
        for r in data["items"]:
            _emit_lines(
                [
                    _fmt_dt(r.get("created_at")),
                    r.get("code") or "",
                    r.get("source_order_invoice_code") or r.get("source_order_code") or "",
                    r.get("shipping_code") or "",
                    kiot_names.get(r.get("source_order_id"), ""),
                    r.get("warehouse_name") or "",
                    r.get("performed_by") or "",
                ],
                r.get("lines"),
                [r.get("return_type_label") or "", r.get("note") or ""],
            )
        # Footer ghi sổ (đối chiếu được với tồn kho) GIỮ NGUYÊN con số, chỉ nói rõ
        # trong nhãn là tính theo SKU ghi sổ — combo tính 1. Dòng cuối mới CỘNG
        # ĐÚNG các dòng CÓ TRONG FILE — nhãn KHÔNG nói "sau nổ combo" vì nó lệch
        # aggregate theo đơn NGAY CẢ khi kỳ lọc không có combo nào (đơn đảo sổ,
        # xem docstring `_display_qty`), không chỉ do nở combo.
        _emit(["Tổng hoàn theo đơn (SKU ghi sổ)"] + [""] * 11 + [data["return_quantity"]])
        _emit(["Tổng phiếu chuyển kho (SKU ghi sổ)"] + [""] * 11 + [data["transfer_quantity"]])
        _emit(["Tổng cộng (SKU ghi sổ)"] + [""] * 11 + [data["total_quantity"]])
        _emit(["Tổng SL các dòng trong file"] + [""] * 11 + [expanded_total])


register_export(StockExport())
register_export(DispatchHistoryExport())
register_export(ReturnHistoryExport())
