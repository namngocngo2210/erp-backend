"""Các bộ lọc THUỘC TÍNH của danh sách đơn (status / kênh / sản phẩm / NV /
nguồn / sheet / kho / loại đơn) — các nhánh giữa của ``apply_list_filters``
(list_queries.py), giữ ĐÚNG thứ tự áp như bản gốc.

Tách nguyên trạng từ ``OrderRepository._apply_list_filters`` (audit T3/P2-3) —
KHÔNG đổi hành vi, chỉ dời chỗ. Mỗi hàm nhận `(queryset, filters)` và trả
queryset đã lọc; caller ghép theo thứ tự.
"""
from django.db.models import Exists, OuterRef, Q

from apps.catalog.models import Product
from apps.orders.models import OrderItem
from apps.orders.reporting import CANCELLED_ORDER_Q
from apps.orders.sheet_rules import consulting_q, info_incomplete_q


def apply_status_and_channel_filters(queryset, filters: dict):
    """status → channel → channel_ids → channel_type."""
    status_values = filters.get("status")
    if status_values:
        if not isinstance(status_values, list):
            status_values = [status_values]
        parsed_statuses: list[int] = []
        for value in status_values:
            try:
                parsed_statuses.append(int(value))
            except (TypeError, ValueError):
                continue
        # Always exclude status=6 (cancelled orders)
        parsed_statuses = [s for s in parsed_statuses if s != 6]
        if not parsed_statuses:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(status__in=parsed_statuses)
    channel = filters.get("channel")
    if channel:
        if isinstance(channel, list):
            queryset = queryset.filter(channel__code__in=channel)
        else:
            queryset = queryset.filter(channel__code=channel)
    channel_ids = filters.get("channel_ids")
    if channel_ids:
        channel_id_values = channel_ids if isinstance(channel_ids, list) else [channel_ids]
        parsed_channel_ids = []
        for value in channel_id_values:
            try:
                parsed_channel_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if parsed_channel_ids:
            queryset = queryset.filter(channel_id__in=parsed_channel_ids)
        else:
            queryset = queryset.none()
    channel_type_filter = filters.get("channel_type")
    if channel_type_filter:
        ct_values = (
            channel_type_filter
            if isinstance(channel_type_filter, list)
            else [channel_type_filter]
        )
        # Lọc theo CODE của loại kênh, không theo id — cùng lý do đã ghi ở
        # Channel.is_koc và reporting/shared.py: id sinh tự động nên khác giữa
        # các môi trường, code thì ổn định.
        ct_values = [str(v).strip() for v in ct_values if str(v).strip()]
        if ct_values:
            # "none" = kênh CHƯA phân loại (channel_type_id IS NULL), cùng quy
            # ước token với shipping_type bên dưới. Backfill của migration 0024
            # cố ý để một phần kênh NULL nên nhóm này không hề nhỏ.
            concrete = [v for v in ct_values if v != "none"]
            channel_type_q = Q(channel__channel_type__code__in=concrete) if concrete else Q()
            if "none" in ct_values:
                channel_type_q |= Q(channel__channel_type__isnull=True)
            queryset = queryset.filter(channel_type_q)
        else:
            queryset = queryset.none()
    return queryset


def apply_product_filter(queryset, filters: dict):
    product_filter = filters.get("product")
    if not product_filter:
        return queryset
    product_values = product_filter if isinstance(product_filter, list) else [product_filter]
    product_values = [str(value).strip() for value in product_values if str(value).strip()]
    numeric_product_ids = [int(value) for value in product_values if value.isdigit()]
    text_products = [value for value in product_values if not value.isdigit()]

    product_query = Q()
    if numeric_product_ids:
        queryset = queryset.annotate(
            _product_match_ids=Exists(
                OrderItem.objects.filter(
                    order_id=OuterRef("pk"),
                    product_id__in=numeric_product_ids,
                )
            )
        )
        product_query |= Q(_product_match_ids=True)

    if text_products:
        # Trước đây: Exists(OrderItem JOIN products) với icontains → seq-scan TOÀN BỘ
        # order_items (~855k) mỗi lần lọc (~2.5s). Giờ resolve product_id trên bảng
        # products (nhỏ ~2.5k → seq scan rẻ, case-insensitive) rồi Exists match bằng
        # product_id__in (index order_item_product_idx). product_name/sku denormalized
        # giữ làm fallback qua __contains (trigram ord_item_*_trgm_idx sẵn có); còn
        # orders.product qua __icontains dùng order_product_upper_trgm_idx.
        prod_lookup = Q()
        for value in text_products:
            prod_lookup |= Q(name__icontains=value) | Q(sku__icontains=value)
        matched_pids = list(Product.objects.filter(prod_lookup).values_list("id", flat=True))

        item_q = Q()
        if matched_pids:
            item_q |= Q(product_id__in=matched_pids)
        for value in text_products:
            item_q |= Q(product_name__contains=value) | Q(sku__contains=value)

        product_text_query = Q()
        for value in text_products:
            product_text_query |= Q(product__icontains=value)

        queryset = queryset.annotate(
            _product_match_text=Exists(
                OrderItem.objects.filter(order_id=OuterRef("pk")).filter(item_q)
            )
        )
        product_query |= Q(_product_match_text=True) | product_text_query

    if product_query:
        queryset = queryset.filter(product_query)
    else:
        queryset = queryset.none()
    return queryset


def apply_staff_and_source_filters(queryset, filters: dict):
    """staff → owner_user_id → kiotviet_channel_null → order_source(+present/exclude)."""
    staff_filter = filters.get("staff")
    if staff_filter:
        staff_values = staff_filter if isinstance(staff_filter, list) else [staff_filter]
        staff_values = [str(value).strip() for value in staff_values if str(value).strip()]
        numeric_staff_ids = [int(value) for value in staff_values if value.isdigit()]
        if numeric_staff_ids:
            queryset = queryset.filter(staff_id__in=numeric_staff_ids)
        else:
            queryset = queryset.none()
    owner_user_id = filters.get("owner_user_id")
    if owner_user_id:
        queryset = queryset.filter(staff_id=owner_user_id)
    kiotviet_channel_null = str(filters.get("kiotviet_channel_null") or "").strip().lower()
    if kiotviet_channel_null == "true":
        queryset = queryset.filter(kiotviet_channel_id__isnull=True)
    elif kiotviet_channel_null == "false":
        queryset = queryset.filter(kiotviet_channel_id__isnull=False)
    order_source = str(filters.get("order_source") or "").strip()
    if order_source:
        queryset = queryset.filter(order_source=order_source)
    # order_source_present=true: chỉ lấy đơn có nguồn (khác null/rỗng), bất kể là nguồn nào.
    order_source_present = str(filters.get("order_source_present") or "").strip().lower()
    if order_source_present == "true":
        queryset = queryset.filter(order_source__isnull=False).exclude(order_source="")
    # order_source_exclude: loại bỏ các nguồn cho trước (vd Landing loại đơn koc).
    order_source_exclude = filters.get("order_source_exclude")
    if order_source_exclude:
        exclude_values = (
            order_source_exclude
            if isinstance(order_source_exclude, list)
            else [order_source_exclude]
        )
        exclude_values = [str(v).strip() for v in exclude_values if str(v).strip()]
        if exclude_values:
            queryset = queryset.exclude(order_source__in=exclude_values)
    return queryset


def apply_sheet_filters(queryset, filters: dict, raw_q: str):
    """info_complete → consulting → show_orders (bỏ qua show_orders khi đang search)."""
    # info_complete: "true" = có Tên+SĐT+Địa chỉ; "false" = thiếu.
    # Q dùng chung với sheet_rules để list view / export / bộ chia đơn không lệch.
    info_complete = str(filters.get("info_complete") or "").strip().lower()
    if info_complete in ("true", "false"):
        incomplete = info_incomplete_q()
        if info_complete == "true":
            queryset = queryset.exclude(incomplete)
        else:
            queryset = queryset.filter(incomplete)
    # consulting: trục phân tab Landing. "only" = board Tư vấn (Sheet 3),
    # "exclude" = Sheet 1/2. Khác info_complete ở chỗ có tính cờ bất biến
    # has_address, nên đơn đổ về không địa chỉ KHÔNG rời Tư vấn khi được
    # điền đủ thông tin (yêu cầu 2026-08-11).
    consulting = str(filters.get("consulting") or "").strip().lower()
    if consulting == "only":
        queryset = queryset.filter(consulting_q())
    elif consulting == "exclude":
        queryset = queryset.exclude(consulting_q())
    show_orders = filters.get("show_orders")
    # Khi đang tìm kiếm (q), bỏ qua lọc show_orders để tìm được đơn bất kể
    # đơn đó có hiển thị trên danh sách orders hay không — giống trang Landing
    # (vốn không lọc show_orders), nên search theo mã hóa đơn luôn ra kết quả.
    if show_orders is not None and not raw_q:
        if isinstance(show_orders, str):
            normalized = show_orders.strip().lower()
            if normalized == "true":
                queryset = queryset.filter(show_orders=True)
            elif normalized == "false":
                queryset = queryset.filter(show_orders=False)
        else:
            queryset = queryset.filter(show_orders=bool(show_orders))
    return queryset


def apply_location_filters(queryset, filters: dict):
    """kiotviet_channel → branch_id → fulfillment_warehouse."""
    kiotviet_channel = filters.get("kiotviet_channel")
    if kiotviet_channel:
        kv_values = kiotviet_channel if isinstance(kiotviet_channel, list) else [kiotviet_channel]
        include_none = "none" in [str(v).strip().lower() for v in kv_values]
        kv_ids = []
        for v in kv_values:
            try:
                kv_ids.append(int(v))
            except (TypeError, ValueError):
                continue
        kv_conditions = Q()
        if kv_ids:
            kv_conditions |= Q(kiotviet_channel_id__in=kv_ids)
        if include_none:
            kv_conditions |= Q(kiotviet_channel_id__isnull=True)
        if kv_conditions:
            queryset = queryset.filter(kv_conditions)
        else:
            queryset = queryset.none()
    branch_id_filter = filters.get("branch_id")
    if branch_id_filter:
        branch_values = branch_id_filter if isinstance(branch_id_filter, list) else [branch_id_filter]
        branch_values = [str(v).strip() for v in branch_values if str(v).strip()]
        if branch_values:
            queryset = queryset.filter(branch_id__in=branch_values)
        else:
            queryset = queryset.none()
    # Kho xuất: id kho (int) và/hoặc token "none" = chưa gán kho — cùng quy ước
    # với kiotviet_channel. Không lọc theo is_active: đơn cũ vẫn giữ FK tới kho
    # đã vô hiệu hoá nên vẫn phải tìm được.
    warehouse_filter = filters.get("fulfillment_warehouse")
    if warehouse_filter:
        wh_values = warehouse_filter if isinstance(warehouse_filter, list) else [warehouse_filter]
        include_no_warehouse = "none" in [str(v).strip().lower() for v in wh_values]
        wh_ids = []
        for v in wh_values:
            try:
                wh_ids.append(int(v))
            except (TypeError, ValueError):
                continue
        wh_conditions = Q()
        if wh_ids:
            wh_conditions |= Q(fulfillment_warehouse_id__in=wh_ids)
        if include_no_warehouse:
            wh_conditions |= Q(fulfillment_warehouse_id__isnull=True)
        if wh_conditions:
            queryset = queryset.filter(wh_conditions)
        else:
            queryset = queryset.none()
    return queryset


def _apply_string_in_filter(queryset, filters: dict, key: str, field: str):
    """Lọc `field__in` theo danh sách chuỗi (đã strip, bỏ rỗng); có key mà rỗng → none()."""
    raw = filters.get(key)
    if not raw:
        return queryset
    values = raw if isinstance(raw, list) else [raw]
    values = [str(v).strip() for v in values if str(v).strip()]
    if values:
        return queryset.filter(**{f"{field}__in": values})
    return queryset.none()


def apply_type_filters(queryset, filters: dict):
    """invoice_status → vat → order_type → koc_order_type → shipping_type."""
    invoice_status_filter = filters.get("invoice_status")
    if invoice_status_filter:
        if not isinstance(invoice_status_filter, list):
            invoice_status_filter = [invoice_status_filter]
        parsed_invoice_statuses: list[int] = []
        for value in invoice_status_filter:
            try:
                parsed_invoice_statuses.append(int(value))
            except (TypeError, ValueError):
                continue
        if parsed_invoice_statuses:
            invoice_q = Q(invoice_status__in=parsed_invoice_statuses)
            # Tab "Đã hủy" gửi invoice_status=2 → mở rộng theo CANCELLED_ORDER_Q
            # (status=6 OR invoice_status=2, quy tắc chung với status_counts_for)
            # để tab hiển thị ĐỦ cả hai nguồn hủy, thay vì chỉ lọc invoice_status=2
            # và bỏ sót các đơn status=6.
            if 2 in parsed_invoice_statuses:
                invoice_q |= CANCELLED_ORDER_Q
            queryset = queryset.filter(invoice_q)
        else:
            queryset = queryset.none()
    queryset = _apply_string_in_filter(queryset, filters, "vat", "vat")
    queryset = _apply_string_in_filter(queryset, filters, "order_type", "order_type")
    queryset = _apply_string_in_filter(queryset, filters, "koc_order_type", "koc_order_type")
    shipping_type_filter = filters.get("shipping_type")
    if shipping_type_filter:
        st_values = (
            shipping_type_filter
            if isinstance(shipping_type_filter, list)
            else [shipping_type_filter]
        )
        st_values = [str(v).strip() for v in st_values if str(v).strip()]
        if st_values:
            # "none" = đơn chưa xác định (NULL). Đơn cũ đều nằm nhóm này nên bỏ qua
            # nó là làm mất một tập lớn khỏi bộ lọc.
            concrete = [v for v in st_values if v != "none"]
            shipping_type_q = Q(shipping_type__in=concrete) if concrete else Q()
            if "none" in st_values:
                shipping_type_q = (
                    shipping_type_q | Q(shipping_type__isnull=True)
                    if concrete
                    else Q(shipping_type__isnull=True)
                )
            queryset = queryset.filter(shipping_type_q)
        else:
            queryset = queryset.none()
    return queryset
