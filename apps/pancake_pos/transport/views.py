"""API Pancake POS (docs/pancake-pos-product-sync.md §3.5; ghép kho + tồn đầu kỳ:
docs/pancake-pos-two-way-sync.md §3, §7 A1).

Quyền: xem (lượt chạy, liên kết, trạng thái, kiểm tra kết nối) = `pancake-pos:view`;
chạy đồng bộ / thử lại liên kết / chọn cách xuất đơn = `pancake-pos:edit`. Mã đơn
Pancake của một đơn CRM (trang chi tiết đơn) = `pancake-pos:view` HOẶC quyền xem trang
đơn. API key KHÔNG bao giờ trả ra — nhập/xoá key ở Cài đặt → Cấu hình hệ thống.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError
from django.db.models import Count, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.pancake_pos.application.services import initial_stock_jobs, order_link_admin, sync_jobs
from apps.pancake_pos.application.services.factory import build_client
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.infrastructure.stock_reader import SqlWarehouseReader
from apps.pancake_pos.domain.order_push import PartPlan, RoutingError
from apps.pancake_pos.models import (
    LinkStatus,
    OrderLinkStatus,
    PancakePosOrderLink,
    PancakePosSyncRun,
    PancakePosVariationLink,
    SyncRunMode,
)
from apps.pancake_pos.repositories import (
    DirtyQueue,
    WarehouseLinkError,
    WarehouseLinkRepository,
    WarehouseLinkState,
)
from apps.pancake_pos.tasks import push_orders_for, run_initial_stock, run_sync
from apps.pancake_pos.transport.serializers import (
    PancakePosHealthSerializer,
    PancakePosInitialStockRequestSerializer,
    PancakePosInitialStockStatusSerializer,
    PancakePosLinkPageSerializer,
    PancakePosLinkSerializer,
    PancakePosOrderLinkPageSerializer,
    PancakePosOrderLinkSerializer,
    PancakePosOrderRefsSerializer,
    PancakePosOrderRetryRequestSerializer,
    PancakePosOrderRetryResultSerializer,
    PancakePosRouteRequestSerializer,
    PancakePosRouteResultSerializer,
    PancakePosRoutingContextSerializer,
    PancakePosStatusSerializer,
    PancakePosSyncAcceptedSerializer,
    PancakePosSyncRequestSerializer,
    PancakePosSyncRunDetailSerializer,
    PancakePosSyncRunPageSerializer,
    PancakePosSyncRunSerializer,
    PancakePosRemoteWarehousesSerializer,
    PancakePosWarehouseLinksSerializer,
    PancakePosWarehouseLinksUpdateSerializer,
)
from apps.sysconfig import sysconfig
from apps.users.permissions import ORDER_SECTIONS, require_method_permission, require_section

logger = logging.getLogger(__name__)

SECTION = "pancake-pos"
EDIT = f"{SECTION}:edit"
MAX_LIMIT = 200


def _page(params) -> tuple[int, int] | None:
    try:
        limit = max(1, min(int(params.get("limit", 50)), MAX_LIMIT))
        offset = max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        return None
    return limit, offset


def _bad(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


_MODE_PARAM = OpenApiParameter(
    "mode", OpenApiTypes.STR, description="Lọc theo mode, nhiều giá trị cách nhau dấu phẩy"
)

_PAGE_PARAMS = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="Mặc định 50, tối đa 200"),
    OpenApiParameter("offset", OpenApiTypes.INT),
]


@extend_schema(
    operation_id="pancake_pos_sync_start",
    request=PancakePosSyncRequestSerializer,
    responses={
        202: PancakePosSyncAcceptedSerializer,
        400: OpenApiTypes.OBJECT,
        409: OpenApiResponse(description="Đang có lượt đồng bộ khác chưa xong / chưa bật đồng bộ ghi"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def start_sync(request):
    serializer = PancakePosSyncRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    mode = data["mode"]
    # Chặn sớm (trước khi xếp hàng) lỗi cấu hình hiển nhiên — task vẫn kiểm lại.
    if mode == "full" and not sysconfig.get("PANCAKE_POS_ENABLED"):
        return Response(
            {"detail": "PANCAKE_POS_ENABLED đang tắt — chỉ chạy thử được."}, status=status.HTTP_409_CONFLICT
        )
    try:
        run_id = sync_jobs.create_manual_run(mode, request.user.id)
    except sync_jobs.SyncAlreadyRunning as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    skus = [s for s in data.get("skus") or [] if s]
    try:
        run_sync.delay(run_id, mode, data.get("limit"), skus or None, request.user.id)
    except Exception:  # noqa: BLE001 — broker chết: đóng nhật ký để không chặn lượt sau
        sync_jobs.fail_run(run_id, "Không xếp được việc vào hàng đợi Celery")
        raise
    return Response({"run_id": run_id}, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    operation_id="pancake_pos_sync_runs_list",
    parameters=[_MODE_PARAM, *_PAGE_PARAMS],
    responses={200: PancakePosSyncRunPageSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_sync_runs(request):
    page = _page(request.query_params)
    if page is None:
        return _bad("limit/offset không hợp lệ.")
    limit, offset = page
    qs = PancakePosSyncRun.objects.select_related("triggered_by").order_by("-started_at", "-id")
    # `mode` nhận một hoặc nhiều giá trị cách nhau dấu phẩy (UI tách đồng bộ SP / tồn đầu kỳ).
    modes = [m for m in str(request.query_params.get("mode") or "").split(",") if m.strip()]
    if modes:
        if any(m.strip() not in SyncRunMode.values for m in modes):
            return _bad("mode không hợp lệ.")
        qs = qs.filter(mode__in=[m.strip() for m in modes])
    total = qs.count()
    # `report` có thể vài chục KB (mẫu payload) — danh sách không kéo, chỉ lấy `fatal`.
    rows = qs.defer("report")[offset : offset + limit]
    items = PancakePosSyncRunSerializer(_with_fatal(rows), many=True).data
    return Response({"items": items, "total": total, "limit": limit, "offset": offset})


def _with_fatal(rows) -> list[PancakePosSyncRun]:
    """Nạp riêng `report->fatal` cho trang hiện tại (1 query) thay vì cả cột `report`."""
    rows = list(rows)
    fatal = dict(
        PancakePosSyncRun.objects.filter(id__in=[r.id for r in rows]).values_list("id", "report__fatal")
    )
    for row in rows:
        row.report = {"fatal": fatal.get(row.id)} if fatal.get(row.id) else {}
    return rows


@extend_schema(
    operation_id="pancake_pos_sync_run_detail",
    responses={200: PancakePosSyncRunDetailSerializer, 404: OpenApiResponse(description="Không tìm thấy")},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def sync_run_detail(request, pk: int):
    run = PancakePosSyncRun.objects.select_related("triggered_by").filter(pk=pk).first()
    if run is None:
        return Response({"detail": "Không tìm thấy lượt đồng bộ."}, status=status.HTTP_404_NOT_FOUND)
    return Response(PancakePosSyncRunDetailSerializer(run).data)


@extend_schema(
    operation_id="pancake_pos_links_list",
    parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, description="pending | synced | conflict | error | skipped"),
        OpenApiParameter("q", OpenApiTypes.STR, description="Tìm theo SKU / tên sản phẩm"),
        *_PAGE_PARAMS,
    ],
    responses={200: PancakePosLinkPageSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_links(request):
    page = _page(request.query_params)
    if page is None:
        return _bad("limit/offset không hợp lệ.")
    limit, offset = page
    qs = PancakePosVariationLink.objects.select_related("product", "product_link")
    link_status = request.query_params.get("status")
    if link_status:
        if link_status not in LinkStatus.values:
            return _bad("status không hợp lệ.")
        qs = qs.filter(status=link_status)
    q = str(request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(product__sku__icontains=q) | Q(product__name__icontains=q))
    qs = qs.order_by("-updated_at", "-id")
    total = qs.count()
    items = PancakePosLinkSerializer(qs[offset : offset + limit], many=True).data
    return Response({"items": items, "total": total, "limit": limit, "offset": offset})


@extend_schema(
    operation_id="pancake_pos_link_retry",
    request=None,
    responses={
        202: PancakePosLinkSerializer,
        404: OpenApiResponse(description="Không tìm thấy liên kết"),
        409: OpenApiResponse(description="Chưa bật đồng bộ ghi"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def retry_link(request, pk: int):
    link = PancakePosVariationLink.objects.select_related("product", "product_link").filter(pk=pk).first()
    if link is None:
        return Response({"detail": "Không tìm thấy liên kết."}, status=status.HTTP_404_NOT_FOUND)
    if not sysconfig.get("PANCAKE_POS_ENABLED"):
        return Response({"detail": "PANCAKE_POS_ENABLED đang tắt."}, status=status.HTTP_409_CONFLICT)
    link.status = LinkStatus.PENDING
    link.save(update_fields=["status", "updated_at"])
    DirtyQueue.mark([link.product_id])
    return Response(PancakePosLinkSerializer(link).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(operation_id="pancake_pos_status", responses={200: PancakePosStatusSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def sync_status(request):
    """Trạng thái cục bộ (không gọi Pancake): cấu hình, hàng đợi, số liên kết theo trạng thái."""
    counts = dict(
        PancakePosVariationLink.objects.values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    order_counts = dict(
        PancakePosOrderLink.objects.values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    active = sync_jobs.active_manual_run()
    return Response({
        "enabled": bool(sysconfig.get("PANCAKE_POS_ENABLED")),
        "api_key_set": bool(sysconfig.get("PANCAKE_POS_API_KEY")),
        "shop_id": int(sysconfig.get("PANCAKE_POS_SHOP_ID") or 0),
        "dirty_count": DirtyQueue.count(),
        "link_counts": {s: int(counts.get(s, 0)) for s in LinkStatus.values},
        "active_run_id": active.id if active else None,
        "push_orders_enabled": bool(sysconfig.get("PANCAKE_POS_PUSH_ORDERS_ENABLED")),
        "order_link_counts": {s: int(order_counts.get(s, 0)) for s in OrderLinkStatus.values},
    })


@extend_schema(operation_id="pancake_pos_health", responses={200: PancakePosHealthSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def health(request):
    """Gọi `GET /shops` để kiểm tra API key; trả danh sách shop (id, tên) — KHÔNG trả key."""
    shop_id = int(sysconfig.get("PANCAKE_POS_SHOP_ID") or 0)
    out = {"ok": False, "error": "", "shops": [], "shop_id": shop_id, "shop_found": False}
    try:
        client = build_client()
        try:
            shops = client.list_shops()
        finally:
            client.close()
    except PancakePosError as exc:
        out["error"] = str(exc)
        return Response(out)
    out["shops"] = [{"id": int(s["id"]), "name": s["name"]} for s in shops if str(s.get("id")).isdigit()]
    out["ok"] = True
    out["shop_found"] = any(s["id"] == shop_id for s in out["shops"])
    return Response(out)


# ── ghép kho (two-way-sync §3) ──────────────────────────────────────────────
def _warehouse_rows() -> dict:
    links = {link.warehouse_id: link for link in WarehouseLinkRepository.all_links()}
    items = []
    for warehouse in SqlWarehouseReader().list_warehouses():
        link = links.get(warehouse.id)
        items.append({
            "warehouse_id": warehouse.id,
            "code": warehouse.code,
            "name": warehouse.name,
            "is_active": warehouse.is_active,
            "pancake_warehouse_id": link.pancake_warehouse_id if link else None,
            "pancake_warehouse_name": link.pancake_warehouse_name if link else "",
            "is_default_for_push": bool(link and link.is_default_for_push),
            "priority": link.priority if link else None,
        })
    return {"items": items}


@extend_schema(
    methods=["GET"], operation_id="pancake_pos_warehouse_links", responses={200: PancakePosWarehouseLinksSerializer}
)
@extend_schema(
    methods=["PUT"],
    operation_id="pancake_pos_warehouse_links_update",
    request=PancakePosWarehouseLinksUpdateSerializer,
    responses={200: PancakePosWarehouseLinksSerializer, 400: OpenApiTypes.OBJECT},
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_method_permission(GET=f"{SECTION}:view", PUT=EDIT)])
def warehouse_links(request):
    """GET: mọi kho CRM (chưa xoá) kèm ghép. PUT: thay TOÀN BỘ danh sách ghép."""
    if request.method == "GET":
        return Response(_warehouse_rows())
    serializer = PancakePosWarehouseLinksUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    links = [WarehouseLinkState(**row) for row in serializer.validated_data["links"]]
    known = {w.id for w in SqlWarehouseReader().list_warehouses()}
    unknown = sorted({link.warehouse_id for link in links} - known)
    if unknown:
        return _bad(f"Kho CRM không tồn tại: {', '.join(map(str, unknown))}.")
    try:
        WarehouseLinkRepository.replace_all(links)
    except WarehouseLinkError as exc:
        return _bad(str(exc))
    except IntegrityError:
        return _bad("Danh sách ghép kho vi phạm ràng buộc duy nhất.")
    return Response(_warehouse_rows())


@extend_schema(operation_id="pancake_pos_remote_warehouses", responses={200: PancakePosRemoteWarehousesSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def pancake_warehouses(request):
    """Kho trên shop Pancake (gọi `GET /shops/{id}/warehouses`) để chọn khi ghép."""
    out: dict = {"ok": False, "error": "", "warehouses": []}
    try:
        client = build_client()
        try:
            warehouses = client.list_warehouses()
        finally:
            client.close()
    except PancakePosError as exc:
        out["error"] = str(exc)
        return Response(out)
    out["ok"] = True
    out["warehouses"] = [{"id": w.id, "name": w.name} for w in warehouses]
    return Response(out)


# ── tồn đầu kỳ (two-way-sync §7 A1) ─────────────────────────────────────────
@extend_schema(
    operation_id="pancake_pos_initial_stock_start",
    request=PancakePosInitialStockRequestSerializer,
    responses={
        202: PancakePosSyncAcceptedSerializer,
        400: OpenApiTypes.OBJECT,
        409: OpenApiResponse(description="Đang có lượt khác / chưa bật ghi / thiếu dry-run hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def start_initial_stock(request):
    serializer = PancakePosInitialStockRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    dry_run = data["mode"] == "dry_run"
    if dry_run:
        warehouse_ids = data.get("warehouse_ids") or None
        skus = [s for s in data.get("skus") or [] if s] or None
    else:
        if not sysconfig.get("PANCAKE_POS_ENABLED"):
            return Response({"detail": "PANCAKE_POS_ENABLED đang tắt — chỉ chạy thử được."}, status=status.HTTP_409_CONFLICT)
        try:
            scope = initial_stock_jobs.check_apply_allowed(data["dry_run_id"])
        except initial_stock_jobs.DryRunRequired as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        warehouse_ids, skus = scope["warehouse_ids"], scope["skus"]
    try:
        run_id = initial_stock_jobs.create_run(dry_run=dry_run, user_id=request.user.id)
    except initial_stock_jobs.InitialStockAlreadyRunning as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    try:
        run_initial_stock.delay(run_id, dry_run, warehouse_ids, skus, request.user.id)
    except Exception:  # noqa: BLE001 — broker chết: đóng nhật ký để không chặn lượt sau
        sync_jobs.fail_run(run_id, "Không xếp được việc vào hàng đợi Celery")
        raise
    return Response({"run_id": run_id}, status=status.HTTP_202_ACCEPTED)


@extend_schema(operation_id="pancake_pos_initial_stock_status", responses={200: PancakePosInitialStockStatusSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def initial_stock_status(request):
    """Dry-run gần nhất + có được ghi không (≤ 30 phút, không lỗi) + lượt đang chạy."""
    latest = initial_stock_jobs.latest_dry_run()
    active = initial_stock_jobs.active_run()
    deadline = initial_stock_jobs.apply_deadline(latest)
    return Response({
        "latest_dry_run": PancakePosSyncRunSerializer(latest).data if latest else None,
        "apply_allowed": deadline is not None,
        "apply_deadline": deadline,
        "active_run_id": active.id if active else None,
        "warehouse_links": len(WarehouseLinkRepository.all_links()),
    })


# ── đẩy đơn (two-way-sync §4.1, §6b, A2) ────────────────────────────────────
_ORDER_LINK_VALUES = (
    "id", "order_id", "part_no", "pancake_order_id", "origin", "status", "pancake_status", "warehouse_id",
    "last_error", "attempts", "needs_lookup", "pushed_at", "updated_at",
)


def _enqueue_orders(order_ids: list[int], *, force: bool = False) -> None:
    """Người điều phối vừa thao tác → đẩy ngay (không đợi beat). Broker lỗi: beat quét bù
    (trừ "Vẫn đẩy": beat không bỏ luật chống trùng → người dùng bấm lại)."""
    try:
        push_orders_for.delay(order_ids, True, force)
    except Exception:  # noqa: BLE001
        logger.exception("pancake_pos: không xếp được task đẩy đơn %s", order_ids)


@extend_schema(
    operation_id="pancake_pos_order_links_list",
    parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, description="Lọc trạng thái, nhiều giá trị cách dấu phẩy"),
        OpenApiParameter("q", OpenApiTypes.STR, description="Mã đơn CRM / mã đơn Pancake / tên khách"),
        *_PAGE_PARAMS,
    ],
    responses={200: PancakePosOrderLinkPageSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_order_links(request):
    page = _page(request.query_params)
    if page is None:
        return _bad("limit/offset không hợp lệ.")
    limit, offset = page
    qs = PancakePosOrderLink.objects.all()
    statuses = [x.strip() for x in str(request.query_params.get("status") or "").split(",") if x.strip()]
    if statuses:
        if any(x not in OrderLinkStatus.values for x in statuses):
            return _bad("status không hợp lệ.")
        qs = qs.filter(status__in=statuses)
    q = str(request.query_params.get("q") or "").strip()
    if q:
        cond = Q(pancake_order_id=q) | Q(order__name__icontains=q)
        if q.isdigit():
            cond |= Q(order_id=int(q))
        qs = qs.filter(cond)
    qs = qs.order_by("-updated_at", "-id")
    total = qs.count()
    rows = list(
        qs.values(*_ORDER_LINK_VALUES, "warehouse__name", "order__name", "order__order_source")[offset : offset + limit]
    )
    items = [
        {
            **{k: row[k] for k in _ORDER_LINK_VALUES},
            "warehouse_name": row["warehouse__name"] or "",
            "order_name": row["order__name"] or "",
            "order_source": row["order__order_source"] or "",
            "last_error": row["last_error"] or "",
        }
        for row in rows
    ]
    return Response({
        "items": PancakePosOrderLinkSerializer(items, many=True).data, "total": total, "limit": limit, "offset": offset,
    })


@extend_schema(operation_id="pancake_pos_order_refs", responses={200: PancakePosOrderRefsSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION, *ORDER_SECTIONS)])
def order_refs(request, order_id: int):
    """Mã đơn Pancake của một đơn CRM (trang chi tiết đơn). Chỉ id + trạng thái liên kết."""
    rows = PancakePosOrderLink.objects.filter(order_id=order_id).order_by("part_no").values(
        "part_no", "pancake_order_id", "status"
    )
    return Response({"items": list(rows)})


@extend_schema(
    operation_id="pancake_pos_order_link_retry",
    request=PancakePosOrderRetryRequestSerializer,
    responses={
        202: PancakePosOrderRetryResultSerializer,
        404: OpenApiResponse(description="Không tìm thấy liên kết"),
        409: OpenApiResponse(description="Chưa bật đẩy đơn / trạng thái không thử lại được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def retry_order_link(request, pk: int):
    if not (sysconfig.get("PANCAKE_POS_ENABLED") and sysconfig.get("PANCAKE_POS_PUSH_ORDERS_ENABLED")):
        return Response({"detail": "Đẩy đơn đang tắt."}, status=status.HTTP_409_CONFLICT)
    serializer = PancakePosOrderRetryRequestSerializer(data=request.data or {})
    serializer.is_valid(raise_exception=True)
    force = bool(serializer.validated_data.get("force"))
    try:
        order_id = order_link_admin.retry_link(pk, force=force)
    except order_link_admin.OrderNotFound:
        return Response({"detail": "Không tìm thấy liên kết."}, status=status.HTTP_404_NOT_FOUND)
    except order_link_admin.OrderLinkConflict as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    if force:
        logger.info("pancake_pos: user %s bấm Vẫn đẩy đơn %s (bỏ luật chống trùng)", request.user.pk, order_id)
    _enqueue_orders([order_id], force=force)
    return Response({"order_id": order_id}, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    methods=["GET"],
    operation_id="pancake_pos_order_routing",
    responses={200: PancakePosRoutingContextSerializer, 404: OpenApiResponse(description="Không tìm thấy đơn")},
)
@extend_schema(
    methods=["POST"],
    operation_id="pancake_pos_order_route",
    request=PancakePosRouteRequestSerializer,
    responses={
        202: PancakePosRouteResultSerializer,
        400: OpenApiTypes.OBJECT,
        404: OpenApiResponse(description="Không tìm thấy đơn"),
        409: OpenApiResponse(description="Đơn đã/đang đẩy hoặc đẩy đơn đang tắt"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=f"{SECTION}:view", POST=EDIT)])
def order_routing(request, order_id: int):
    """GET: dòng hàng + tồn từng kho đã ghép. POST: chọn một kho hoặc tách kiện."""
    if request.method == "GET":
        try:
            return Response(order_link_admin.routing_context(order_id))
        except order_link_admin.OrderNotFound:
            return Response({"detail": "Không tìm thấy đơn."}, status=status.HTTP_404_NOT_FOUND)
    if not (sysconfig.get("PANCAKE_POS_ENABLED") and sysconfig.get("PANCAKE_POS_PUSH_ORDERS_ENABLED")):
        return Response({"detail": "Đẩy đơn đang tắt."}, status=status.HTTP_409_CONFLICT)
    serializer = PancakePosRouteRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    parts = [
        PartPlan(
            warehouse_id=part["warehouse_id"],
            items=tuple((item["product_id"], item["quantity"]) for item in part.get("items") or ()),
        )
        for part in serializer.validated_data["parts"]
    ]
    try:
        link_ids = order_link_admin.route_order(order_id, parts)
    except order_link_admin.OrderNotFound:
        return Response({"detail": "Không tìm thấy đơn."}, status=status.HTTP_404_NOT_FOUND)
    except RoutingError as exc:
        return _bad(str(exc))
    except order_link_admin.OrderLinkConflict as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    _enqueue_orders([order_id])
    return Response({"link_ids": link_ids}, status=status.HTTP_202_ACCEPTED)
