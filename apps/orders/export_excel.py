from django.utils.timezone import localtime
from apps.orders.models import OrderStatus
from apps.users.phone_visibility import PhoneVisibility, is_public_phone_source
from apps.orders.crm_catalog import (
    AGE_GROUP_LABELS,
    DELIVERY_BUSINESS_STATUS_LABELS,
    SALES_STATUS_LABELS,
    CUSTOMER_CLASSIFICATION_LABELS,
    NOT_CLOSED_REASON_LABELS,
    OCCUPATION_LABELS,
    PAIN_LEVEL_LABELS,
    PAIN_DURATION_LABELS,
    CALL_DURATION_LABELS,
    POST_PURCHASE_RATING_LABELS,
    DELAX_SOURCE_LABELS,
    LIFESTYLE_HABIT_LABELS,
    RELAX_TIME_LABELS,
    PURCHASE_BARRIER_LABELS,
    POTENTIAL_LEVEL_LABELS,
    BOMB_REASON_GROUP_LABELS,
    BOMB_REASON_TYPE_LABELS,
)

# Cột cờ/mã đơn giá trị: xuất NHÃN tiếng Việt, không xuất mã — file Excel là để
# người đọc. Giá trị ngoài bảng (NULL ở đơn cũ) xuất rỗng.
_LABELED_FIELD_COLUMNS: dict[str, dict] = {
    "shipping_type": {"shipment": "Giao vận", "direct": "Bán trực tiếp"},
    "koc_order_type": {"sample_gift": "Tặng mẫu", "renewal": "Gia hạn", "recall": "Thu hồi"},
    # Cờ bất biến ghi lúc ingest (sale điền địa chỉ sau vẫn giữ False).
    "has_address": {True: "Có địa chỉ", False: "Không có địa chỉ"},
}


def order_row_values(order, columns, phone_visibility=None) -> list:
    """Giá trị một dòng export theo `columns`.

    Tách khỏi `export_orders_to_excel` để `OrdersExport.write` (apps/orders/
    exports_definitions.py, chạy ở worker) dùng lại được mà không phải kéo theo
    openpyxl/Workbook — worker chỉ cần list giá trị, writer streaming tự lo phần
    ghi xlsx/csv. Chuyển NGUYÊN khối if/elif cũ, không đổi logic một dòng nào.

    `phone_visibility` (apps/users/phone_visibility.py) che cột SĐT của đơn KHÔNG do
    người xuất phụ trách. FAIL-CLOSED khi không truyền: file Excel mang cả tệp khách
    ra khỏi hệ thống, là đúng chỗ KHÔNG được phép rò vì quên một tham số.
    """
    if phone_visibility is None:
        phone_visibility = PhoneVisibility(user_id=None, can_view_full=False)
    status_dict = dict(OrderStatus.choices)
    row = []
    for col in columns:
        key = col.get("key")
        val = ""

        if key == "kiotviet":
            val = order.kiotviet_channel.name if order.kiotviet_channel else ""
        elif key == "branch_id":
            val = order.branch_id
        elif key == "channel_code":
            val = order.channel.code if order.channel else ""
        elif key == "staff":
            val = order.staff.username if order.staff else ""
        elif key == "name":
            val = order.name
        elif key == "phone":
            val = phone_visibility.apply(
                order.phone,
                order.staff_id,
                public_source=is_public_phone_source(order.order_source),
            )
        elif key == "address":
            val = order.address
        elif key == "kiotviet_location":
            val = order.kiotviet_location
        elif key == "kiotviet_ward":
            val = order.kiotviet_ward
        elif key == "sku":
            val = order.sku
        elif key == "quantity":
            val = order.quantity
        elif key == "total_amount":
            val = float(order.total_amount) if order.total_amount else 0
        elif key == "customer_prepaid":
            val = float(order.customer_prepaid) if order.customer_prepaid else 0
        elif key == "invoice_code":
            val = order.invoice_code
        elif key == "shipping_code":
            val = order.shipping_code
        elif key == "invoice_status":
            val = order.invoice_status if order.invoice_status is not None else ""
        elif key == "status":
            val = status_dict.get(order.status, order.status)
        elif key == "customer_gender":
            val = order.customer.gender if hasattr(order, 'customer') and order.customer else ""
        elif key == "created_at":
            val = localtime(order.created_at).strftime("%Y-%m-%d %H:%M:%S") if order.created_at else ""
        elif key == "sales_closed_at":
            # Nhánh tường minh, KHÔNG để rơi vào `getattr(order, key)` cuối hàm:
            # giá trị là datetime timezone-aware, openpyxl từ chối ghi ("Excel does
            # not support timezones in datetimes").
            val = (
                localtime(order.sales_closed_at).strftime("%Y-%m-%d %H:%M:%S")
                if order.sales_closed_at
                else ""
            )
        elif key == "customer_code":
            val = order.customer.code if hasattr(order, 'customer') and order.customer else ""
        elif key == "customer_type":
            if hasattr(order, 'customer') and order.customer:
                val = "Khách mới" if order.customer.is_new_customer else "Khách cũ"
        elif key == "fulfillment_warehouse":
            # Order.fulfillment_warehouse là FK (apps.warehouses.Warehouse) — list view
            # hiển thị fulfillment_warehouse_name (order_core_serializers.py), tức tên
            # kho chứ không phải object. getattr(order, key) trần trả về instance
            # Warehouse, WriteOnlyCell không nhận kiểu này (TypeError khi ghi xlsx).
            val = order.fulfillment_warehouse.name if order.fulfillment_warehouse else ""
        elif key in _LABELED_FIELD_COLUMNS:
            val = _LABELED_FIELD_COLUMNS[key].get(getattr(order, key), "")
        elif key in ["delivery_business_status", "sales_status", "bomb_reason", "bomb_reason_group", "bomb_reason_type", "internal_note", "customer_classification", "not_closed_reason", "potential_level", "age_text", "occupation", "pain_level", "pain_duration", "used_massage_before", "call_duration", "post_purchase_rating", "post_purchase_feedback", "comparing_brand", "bomb_reason_text", "customer_attitude"]:
            if hasattr(order, 'assessment') and order.assessment:
                if key == "delivery_business_status": val = DELIVERY_BUSINESS_STATUS_LABELS.get(order.assessment.delivery_business_status_code, order.assessment.delivery_business_status_code) or ""
                elif key == "sales_status": val = SALES_STATUS_LABELS.get(order.assessment.sales_status_code, order.assessment.sales_status_code) or ""
                elif key in ("bomb_reason", "bomb_reason_type"): val = BOMB_REASON_TYPE_LABELS.get(order.assessment.bomb_reason_type_code, order.assessment.bomb_reason_type_code) or ""
                elif key == "bomb_reason_group": val = BOMB_REASON_GROUP_LABELS.get(order.assessment.bomb_reason_group_code, order.assessment.bomb_reason_group_code) or ""
                elif key == "internal_note": val = order.assessment.internal_note or ""
                elif key == "customer_classification": val = CUSTOMER_CLASSIFICATION_LABELS.get(order.assessment.customer_classification_code, order.assessment.customer_classification_code) or ""
                elif key == "not_closed_reason": val = NOT_CLOSED_REASON_LABELS.get(order.assessment.not_closed_reason_code, order.assessment.not_closed_reason_code) or ""
                elif key == "potential_level": val = POTENTIAL_LEVEL_LABELS.get(order.assessment.potential_level, str(order.assessment.potential_level)) if order.assessment.potential_level else ""
                elif key == "age_text": val = AGE_GROUP_LABELS.get(order.assessment.age_text, order.assessment.age_text) or ""
                elif key == "occupation": val = OCCUPATION_LABELS.get(order.assessment.occupation_code, order.assessment.occupation_code) or ""
                elif key == "pain_level": val = PAIN_LEVEL_LABELS.get(order.assessment.pain_level_code, order.assessment.pain_level_code) or ""
                elif key == "pain_duration": val = PAIN_DURATION_LABELS.get(order.assessment.pain_duration_code, order.assessment.pain_duration_code) or ""
                elif key == "used_massage_before": val = "Có" if order.assessment.used_massage_before else ("Chưa" if order.assessment.used_massage_before is False else "")
                elif key == "call_duration": val = CALL_DURATION_LABELS.get(order.assessment.call_duration_code, order.assessment.call_duration_code) or ""
                elif key == "post_purchase_rating": val = POST_PURCHASE_RATING_LABELS.get(order.assessment.post_purchase_rating_code, order.assessment.post_purchase_rating_code) or ""
                elif key == "post_purchase_feedback": val = order.assessment.post_purchase_feedback_text or ""
                elif key == "comparing_brand": val = order.assessment.comparing_brand_text or ""
                elif key == "bomb_reason_text": val = order.assessment.bomb_reason_text or ""
                elif key == "customer_attitude": val = order.assessment.customer_attitude_raw or ""
        elif key in ("recall_code", "recall_created_date", "recall_received_date"):
            # 3 cột nay nằm trên N bản ghi OrderRecallEntry (1-N, "recall_entries").
            # Nối theo ĐÚNG thứ tự nhóm và GIỮ vị trí rỗng (không dồn): dồn lại sẽ
            # làm 3 cột lệch nhau, mất tính tương ứng theo vị trí giữa mã và ngày.
            entries = list(order.recall_entries.all())
            if key == "recall_code":
                val = ", ".join(entry.recall_code for entry in entries)
            elif key == "recall_created_date":
                val = ", ".join(
                    entry.recall_created_date.isoformat() if entry.recall_created_date else ""
                    for entry in entries
                )
            else:
                val = ", ".join(
                    entry.recall_received_date.isoformat() if entry.recall_received_date else ""
                    for entry in entries
                )
        elif key == "return_slip_code":
            # getattr thay vì order.recall trực tiếp: đơn không có bản ghi recall sẽ
            # ném RelatedObjectDoesNotExist nếu truy cập thẳng.
            recall = getattr(order, "recall", None)
            val = ", ".join(recall.return_slip_code or []) if recall else ""
        elif key == "needs":
            val = ", ".join([n.need_type.name_vi for n in order.assessment.needs.all()]) if hasattr(order, 'assessment') and order.assessment else ""
        elif key == "delax_sources":
            val = ", ".join(filter(None, [DELAX_SOURCE_LABELS.get(c, c) for c in order.assessment.delax_source_codes])) if hasattr(order, 'assessment') and order.assessment and order.assessment.delax_source_codes else ""
        elif key == "lifestyle_habits":
            val = ", ".join(filter(None, [LIFESTYLE_HABIT_LABELS.get(c, c) for c in order.assessment.lifestyle_habit_codes])) if hasattr(order, 'assessment') and order.assessment and order.assessment.lifestyle_habit_codes else ""
        elif key == "relax_times":
            val = ", ".join(filter(None, [RELAX_TIME_LABELS.get(c, c) for c in order.assessment.relax_time_codes])) if hasattr(order, 'assessment') and order.assessment and order.assessment.relax_time_codes else ""
        elif key == "purchase_barriers":
            val = ", ".join(filter(None, [PURCHASE_BARRIER_LABELS.get(c, c) for c in order.assessment.purchase_barrier_codes])) if hasattr(order, 'assessment') and order.assessment and order.assessment.purchase_barrier_codes else ""
        else:
            val = getattr(order, key, "")

        row.append(val)
    return row


# `export_orders_to_excel` (dựng .xlsx đồng bộ, trả HttpResponse) đã xoá ở
# Task 18 cùng endpoint `POST /api/orders/bulk-export-excel` (`bulk_export_
# excel`, view duy nhất từng gọi hàm này) — luồng async `OrdersExport.write`
# (apps/orders/exports_definitions.py) chỉ dùng lại `order_row_values` ở trên,
# ghi trực tiếp qua writer streaming của engine, không dựng Workbook riêng.
