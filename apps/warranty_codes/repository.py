"""Truy vấn danh sách cho trang Mã bảo hành (mirror pattern claim_repository)."""

from apps.warranty_codes.models import WarrantyCode, WarrantyCodeBatch, WarrantyCodeScanLog


def filter_batches(qs, *, q, product_id, status, date_from, date_to):
    """Bộ lọc lô dùng CHUNG cho list endpoint và export "xuất theo bộ lọc" —
    một chỗ định nghĩa để tập xuất luôn khớp tập người dùng đang nhìn."""
    if q:
        qs = qs.filter(batch_code__istartswith=q.strip())
    if product_id:
        qs = qs.filter(product_id=product_id)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def list_batches(*, q, product_id, status, date_from, date_to, limit, offset):
    qs = filter_batches(
        WarrantyCodeBatch.objects.select_related("product", "created_by", "assigned_by").order_by("-created_at"),
        q=q, product_id=product_id, status=status, date_from=date_from, date_to=date_to,
    )
    total = qs.count()
    return list(qs[offset : offset + limit]), total


def list_scan_logs(*, keyword, ip_address, result, channel, date_from, date_to, limit, offset):
    qs = WarrantyCodeScanLog.objects.select_related("code").order_by("-created_at")
    if keyword:
        qs = qs.filter(keyword__startswith=keyword.strip().upper())
    if ip_address:
        qs = qs.filter(ip_address=ip_address.strip())
    if result:
        qs = qs.filter(result=result)
    if channel:
        qs = qs.filter(channel=channel)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    total = qs.count()
    return list(qs[offset : offset + limit]), total


def list_codes(*, search, product_id, batch_id, date_from, date_to, limit, offset):
    qs = WarrantyCode.objects.select_related("batch", "product").order_by("-seq")
    if search:
        term = search.strip().upper()
        # serial/code lưu uppercase → dùng startswith (case-sensitive) để ăn index varchar_pattern_ops.
        from django.db.models import Q

        qs = qs.filter(Q(serial__startswith=term) | Q(code__startswith=term))
    if product_id:
        qs = qs.filter(product_id=product_id)
    if batch_id:
        qs = qs.filter(batch_id=batch_id)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    total = qs.count()
    return list(qs[offset : offset + limit]), total
