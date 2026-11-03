from __future__ import annotations

from django.db.models import Exists, F, OuterRef
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.movement_window import local_day_bounds
from apps.inventory.models import (
    DocStatus,
    DocType,
    StockDocument,
    StockDocumentAttachment,
)
from apps.users.permissions import require_section
from apps.warehouses.models import Warehouse

# Phiếu trả hàng đã rời khỏi sổ này (gỡ UI 2026-07-14): đơn hoàn theo dõi ở tab
# Nhập hoàn của Lịch sử nhập xuất (return-history/*), sổ phiếu chỉ còn phiếu kho.
_STOCK_TYPES = {DocType.RECEIPT, DocType.ISSUE, DocType.ADJUSTMENT, DocType.TRANSFER}
_DOC_TYPE_LABELS = dict(DocType.choices)
_DOC_STATUS_LABELS = dict(DocStatus.choices)


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# Sổ phiếu kho phân trang (nhập/xuất/điều chỉnh/chuyển kho).
@extend_schema(
    responses={
        200: inline_serializer(
            "LedgerDocumentsResponse",
            fields={
                "items": inline_serializer(
                    "LedgerDocumentItem",
                    many=True,
                    fields={
                        "id": drf_serializers.IntegerField(),
                        "code": drf_serializers.CharField(),
                        "type": drf_serializers.CharField(),
                        "type_label": drf_serializers.CharField(),
                        "status": drf_serializers.CharField(),
                        "status_label": drf_serializers.CharField(),
                        "warehouse_label": drf_serializers.CharField(),
                        "has_attachments": drf_serializers.BooleanField(),
                        "created_at": drf_serializers.DateTimeField(),
                    },
                ),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock-documents")])
def ledger_documents(request):
    types = request.query_params.getlist("type")
    statuses = request.query_params.getlist("status")
    q = (request.query_params.get("q") or "").strip()
    date_from = request.query_params.get("date_from") or ""
    date_to = request.query_params.get("date_to") or ""
    limit = max(1, min(_int(request.query_params.get("limit"), 50), 200))
    offset = max(0, _int(request.query_params.get("offset"), 0))

    stock_types = [t for t in types if t in {c.value for c in _STOCK_TYPES}]
    # Client gửi type toàn giá trị lạ (vd "return" cũ) → rỗng, không phải tất cả.
    if types and not stock_types:
        return Response({"items": [], "total": 0, "limit": limit, "offset": offset})

    qs = StockDocument.objects.all()
    if stock_types:
        qs = qs.filter(doc_type__in=stock_types)
    if statuses:
        qs = qs.filter(status__in=statuses)
    if q:
        qs = qs.filter(code__icontains=q)
    # Nửa-mở [00:00 date_from, 00:00 date_to+1) trên CỘT TRẦN thay cho `__date`:
    # Django dịch `__date` thành `(col AT TIME ZONE ...)::date` — biểu thức đó làm
    # index trên created_at vô dụng. Cùng luật (và mượn helper) của
    # movement_window.py (audit 2026-07-28 DB-15).
    date_start, date_end = local_day_bounds(date_from, date_to)
    if date_start:
        qs = qs.filter(created_at__gte=date_start)
    if date_end:
        qs = qs.filter(created_at__lt=date_end)
    qs = (
        qs.annotate(
            has_att=Exists(
                StockDocumentAttachment.objects.filter(document_id=OuterRef("pk"))
            ),
            src_wh=F("source_warehouse_id"),
            dest_wh=F("dest_warehouse_id"),
        )
        .order_by("-created_at", "-id")
        .values(
            "id", "code", "doc_type", "status", "src_wh", "dest_wh",
            "has_att", "created_at",
        )
    )
    total = qs.count()
    page = list(qs[offset : offset + limit])

    # Resolve labels for the page (batched, no N+1).
    wh_ids = {row["src_wh"] for row in page if row["src_wh"]} | {
        row["dest_wh"] for row in page if row["dest_wh"]
    }
    wh_names = dict(Warehouse.objects.filter(id__in=wh_ids).values_list("id", "name"))

    items = []
    for row in page:
        if row["doc_type"] == DocType.TRANSFER:
            wl = f"{wh_names.get(row['src_wh'], '?')} → {wh_names.get(row['dest_wh'], '?')}"
        else:
            wl = wh_names.get(row["src_wh"]) or wh_names.get(row["dest_wh"]) or "—"
        items.append({
            "id": row["id"],
            "code": row["code"],
            "type": row["doc_type"],
            "type_label": _DOC_TYPE_LABELS.get(row["doc_type"], row["doc_type"]),
            "status": row["status"],
            "status_label": _DOC_STATUS_LABELS.get(row["status"], row["status"]),
            "warehouse_label": wl,
            "has_attachments": row["has_att"],
            "created_at": row["created_at"],
        })

    return Response({"items": items, "total": total, "limit": limit, "offset": offset})
