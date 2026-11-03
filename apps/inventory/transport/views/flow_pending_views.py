"""Mã vận đơn chờ khớp trên trang Lịch sử nhập xuất — tra cứu đơn, khớp thủ
công, phiếu bù, gỡ khớp và các thao tác trên một dòng mã quét.

Tách từ `flow_history_views.py` (refactor 2026-08); urls.py vẫn import qua
facade `flow_history_views`. LƯU Ý phân quyền (sweep N2 giai đoạn 2): danh sách
ĐỌC qua "stock:view", các hành động GHI gate bằng "stock:edit" (manual-match:
any-of stock/shipments/returns :edit), và khớp/gỡ/tạo phiếu bù soi thêm section
hành động thật ("shipments"/"returns") — xem docstring từng view.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.masking import mask_keep_last
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.replacement_payload import parse_replacement
from apps.inventory.models import PendingScanKind, PendingShipmentScan
from apps.inventory.transport.views.flow_history_shared import _int_or_none
from apps.orders.repositories import OrderRepository
from apps.users.auth_core import user_has_section
from apps.users.permissions import (
    require_method_permission,
    require_permission,
    require_section,
)

_ORDER_LOOKUP_ITEM = {
    "id": drf.IntegerField(),
    "order_code": drf.CharField(allow_blank=True),
    "invoice_code": drf.CharField(allow_blank=True),
    "shipping_code": drf.CharField(allow_blank=True),
    "kiotviet_channel": drf.CharField(allow_blank=True),
    "name": drf.CharField(allow_blank=True),
    "phone": drf.CharField(allow_blank=True),
    # Đơn đã xuất kho chưa — panel khớp tay dựa vào đây để hỏi "hàng gửi bù?".
    "already_dispatched": drf.BooleanField(),
    "items": inline_serializer(
        "FlowOrderLookupOrderItem",
        many=True,
        fields={
            "product_id": drf.IntegerField(),
            "sku": drf.CharField(allow_blank=True),
            "quantity": drf.IntegerField(),
        },
    ),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "FlowOrderLookup",
            fields={
                "items": inline_serializer(
                    "FlowOrderLookupItem", many=True, fields=_ORDER_LOOKUP_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def flow_order_lookup(request):
    """Tìm đơn theo mã hóa đơn / mã đơn / mã vận đơn để khớp thủ công.

    Kèm ``already_dispatched`` + ``items`` để panel khớp tay biết có phải hỏi
    "hàng gửi bù?" hay không. Cờ này CHỈ để dựng UI — quyết định thật là ở
    `PendingScanService._create_replacement_issue` (đọc warning của chính lần khớp).
    """
    from apps.catalog.combo_lines import expand_order_lines_bulk
    from apps.inventory.application.services.stock_sales_service import (
        order_ids_with_outstanding_stock,
    )
    from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
    from apps.orders.models import OrderAssessment, OrderItem

    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return Response({"items": []}, status=status.HTTP_200_OK)
    rows = list(OrderRepository.search_for_lookup(q, limit=20))
    order_ids = [o.id for o in rows]
    # Hai tín hiệu, đúng một cái là đủ — cùng luật với `_is_dispatched` ở luồng quét:
    # đơn không khớp sản phẩm nào xuất kho với lines=[] nên KHÔNG sinh bút toán, chỉ
    # đọc sổ tồn sẽ báo "chưa xuất" vĩnh viễn.
    dispatched_ids = set(order_ids_with_outstanding_stock(order_ids))
    dispatched_ids |= set(
        OrderAssessment.objects.filter(
            order_id__in=order_ids,
            delivery_business_status_code=DISPATCHED_DELIVERY_CODE,
        ).values_list("order_id", flat=True)
    )
    # 1 query gộp cho cả trang kết quả, không N+1.
    # `item_rows`, KHÔNG phải `rows`: `rows` ở trên đang giữ danh sách Order của
    # trang kết quả và còn được dùng ở cuối hàm.
    item_rows = list(
        OrderItem.objects.filter(order_id__in=order_ids, product_id__isnull=False)
        .order_by("id")
        .values("order_id", "product_id", "sku", "quantity")
    )
    # Nở SKU combo ra mã lẻ: tồn được trừ theo mã lẻ (`expand_desired_quantities`)
    # nên hạn mức phiếu hoàn (`_guard_double_credit`, đọc `outstanding_by_key`) cũng
    # tính theo mã lẻ. Trả dòng combo về đây thì kho chọn đúng thứ UI gợi ý vẫn bị
    # chặn "đơn nguồn chỉ còn 0 đơn vị". Luật nở DÙNG CHUNG với màn quét trả hàng
    # (`orders.shipping_lookup_view`) — trước 07/09 mỗi bên một bản, vá bên này thì
    # bên kia vẫn chặn (xem docs/combo-stock-expansion-2026-08-20.md §8).
    rows_by_order: dict[int, list[dict]] = {}
    for row in item_rows:
        rows_by_order.setdefault(row["order_id"], []).append(
            {
                "product_id": row["product_id"],
                "sku": row["sku"] or "",
                "quantity": row["quantity"],
            }
        )
    items_map = expand_order_lines_bulk(rows_by_order, with_name=False)
    items = [
        {
            "id": o.id,
            "order_code": o.order_code or "",
            "invoice_code": o.invoice_code or "",
            "shipping_code": o.shipping_code or "",
            "kiotviet_channel": o.kiotviet_channel.name if o.kiotviet_channel_id else "",
            "name": o.name or "",
            # Chỉ cần đủ để đối chiếu khớp mã, không cần SĐT đầy đủ.
            "phone": mask_keep_last(o.phone or ""),
            "already_dispatched": o.id in dispatched_ids,
            "items": items_map.get(o.id, []),
        }
        for o in rows
    ]
    return Response({"items": items}, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "FlowManualMatchRequest",
        fields={
            "order_id": drf.IntegerField(),
            # {"lines": [{"product_id", "quantity"}], "note"} — đánh dấu HÀNG GỬI BÙ.
            "replacement": drf.JSONField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "FlowManualMatchResult",
            fields={
                "ok": drf.BooleanField(),
                "result_ref": drf.CharField(allow_blank=True),
                "note": drf.CharField(allow_blank=True),
                "order_id": drf.IntegerField(),
                "stock_doc_code": drf.CharField(allow_blank=True),
                "replacement_skipped": drf.CharField(allow_blank=True),
            },
        )
    },
)
# Sweep N2 giai đoạn 2 — LỰA CHỌN GATE: any-of (stock:edit, shipments:edit,
# returns:edit) thay vì chỉ stock:edit, vì endpoint phục vụ CẢ HAI kind
# (dispatch/return) và kind chỉ biết được sau khi đọc DB — decorator không chọn
# trước được code đúng. Gate theo kind thật vẫn nằm BÊN TRONG (user_has_section
# theo "shipments"/"returns", giữ nguyên như luồng quét chuẩn) nên returns-only
# vẫn không khớp được mã xuất và ngược lại. Grandfather 0039 cấp :edit cho role
# có :view tương ứng → tổ hợp quyền đang chạy được hôm nay vẫn chạy ngày deploy.
@api_view(["POST"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(POST=("stock:edit", "shipments:edit", "returns:edit")),
])
@never_cache
def flow_manual_match(request, scan_id):
    """Khớp mã chờ khớp với đơn người dùng chọn (xuất kho / tạo phiếu trả).

    Hành vi GHI nên decorator đòi quyền :edit (xem khối chú thích trên). HÀNH
    ĐỘNG khớp thực sự xuất kho (kind=dispatch) hoặc tạo/ghi phiếu trả
    (kind=return) — vẫn soi thêm đúng section theo kind như luồng quét chuẩn
    (pending_scan_views.py dùng "shipments", return_views.py dùng "returns").
    """
    order_id = _int_or_none(request.data.get("order_id"))
    if not order_id:
        return Response(
            {"detail": "order_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    replacement, replacement_error = parse_replacement(request.data.get("replacement"))
    if replacement_error:
        return Response(
            {"detail": replacement_error}, status=status.HTTP_400_BAD_REQUEST
        )

    # Fail-closed: scan không tồn tại (kind=None) → user thiếu quyền hành động vẫn
    # nhận 403, KHÔNG rơi xuống 400 "không tìm thấy" của service — 403/400 lệch nhau
    # thành oracle cho user chỉ-có-stock dò scan_id nào tồn tại.
    kind = (
        PendingShipmentScan.objects.filter(id=scan_id).values_list("kind", flat=True).first()
    )
    if kind is None:
        allowed = user_has_section(request.user, "shipments", "returns")
    else:
        required_section = "returns" if kind == PendingScanKind.RETURN else "shipments"
        allowed = user_has_section(request.user, required_section)
    if not allowed:
        return Response(
            {"detail": "Bạn không có quyền thực hiện hành động này."},
            status=status.HTTP_403_FORBIDDEN,
        )

    result = PendingScanService.manual_match(
        scan_id=scan_id, order_id=order_id, actor=request.user, replacement=replacement
    )
    if not result.get("ok"):
        return Response(
            {"detail": result.get("error") or "Khớp thất bại."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "FlowReplacementIssueRequest",
        fields={"replacement": drf.JSONField()},
    ),
    responses={
        200: inline_serializer(
            "FlowReplacementIssueResult",
            fields={
                "ok": drf.BooleanField(),
                "stock_doc_code": drf.CharField(allow_blank=True),
            },
        )
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_pending_replacement_issue(request, scan_id):
    """Tạo Phiếu Xuất kho hàng gửi bù cho một mã ĐÃ khớp từ trước (ca lịch sử).

    Hành động thực sự là xuất kho nên soi section "shipments" như luồng quét chuẩn,
    dù danh sách đọc được qua "stock".
    """
    if not user_has_section(request.user, "shipments"):
        return Response(
            {"detail": "Bạn không có quyền thực hiện hành động này."},
            status=status.HTTP_403_FORBIDDEN,
        )
    replacement, replacement_error = parse_replacement(request.data.get("replacement"))
    if replacement_error or replacement is None:
        return Response(
            {"detail": replacement_error or "Thiếu dữ liệu hàng gửi bù."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    result = PendingScanService.create_replacement_issue_for_scan(
        scan_id=scan_id, replacement=replacement, actor=request.user
    )
    if not result.get("ok"):
        return Response(
            {"detail": result.get("error") or "Tạo phiếu bù thất bại."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    request=None,
    responses={
        200: inline_serializer(
            "FlowPendingUnmatchResult",
            fields={
                "ok": drf.BooleanField(),
                # True = lần khớp này đã ghi SALE và vừa được hoàn tồn.
                "released_stock": drf.BooleanField(),
                "order_id": drf.IntegerField(),
            },
        )
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_pending_unmatch(request, scan_id):
    """Gỡ khớp mã XUẤT đã khớp nhầm đơn (hoàn tồn + trả đơn về Kho đang xử lý).

    Hành động thực sự đảo bút toán xuất kho nên soi section "shipments" như luồng
    quét chuẩn, dù danh sách đọc được qua "stock".
    """
    if not user_has_section(request.user, "shipments"):
        return Response(
            {"detail": "Bạn không có quyền thực hiện hành động này."},
            status=status.HTTP_403_FORBIDDEN,
        )
    result = PendingScanService.unmatch_dispatch(scan_id=scan_id, actor=request.user)
    if not result.get("ok"):
        return Response(
            {"detail": result.get("error") or "Gỡ khớp thất bại."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(request=None, responses={200: OpenApiResponse(description="Đã đánh dấu")})
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_pending_mark_invalid(request, scan_id):
    """Đánh dấu mã chờ khớp là 'không đúng' (cron ngừng thử, giữ bản ghi)."""
    if not PendingScanService.mark_invalid(scan_id):
        return Response(
            {"detail": "Không tìm thấy mã chờ khớp hoặc mã đã khớp."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response({"ok": True}, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "FlowPendingNoteUpdate", fields={"note": drf.CharField(allow_blank=True)}
    ),
    responses={
        200: inline_serializer(
            "FlowPendingNoteResult",
            fields={"id": drf.IntegerField(), "note": drf.CharField(allow_blank=True)},
        ),
        404: OpenApiResponse(description="Không tìm thấy mã chờ khớp"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_pending_note_update(request, scan_id):
    """Sửa ghi chú của một dòng mã chờ khớp ngay trên trang Lịch sử nhập xuất."""
    note = request.data.get("note", "")
    if not isinstance(note, str):
        return Response(
            {"detail": "note phải là chuỗi."}, status=status.HTTP_400_BAD_REQUEST
        )
    scan = PendingScanService.update_note(scan_id=scan_id, note=note)
    if scan is None:
        return Response(
            {"detail": "Không tìm thấy mã chờ khớp."}, status=status.HTTP_404_NOT_FOUND
        )
    return Response({"id": scan.id, "note": scan.note}, status=status.HTTP_200_OK)


@extend_schema(responses={204: OpenApiResponse(description="Đã xoá")})
@api_view(["DELETE"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_pending_delete(request, scan_id):
    """Xoá mã chờ khớp (chỉ khi chưa khớp đơn)."""
    if not PendingScanService.delete_scan(scan_id):
        return Response(
            {"detail": "Không xoá được (không tồn tại hoặc đã khớp đơn)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(status=status.HTTP_204_NO_CONTENT)
