"""Scope helper dùng chung cho các endpoint thao tác trên 1 khách hàng theo
section `contacts` — dùng bởi apps/orders (timeline liên hệ, chủ sở hữu domain
contacts) và apps/contact_flow (runtime tác nghiệp, action `contacts` KHÔNG
phải `contact_flow`, xem docstring runtime_views.py).

Trước fix (audit 2026-07-15b) mỗi consumer tự viết lại logic scope này —
1 bản trong apps/orders, 1 bản copy trong apps/contact_flow — rủi ro lệch khi
sửa 1 nơi quên nơi kia. Gộp về 1 hàm duy nhất.

BÀI HỌC audit 2026-07-15b: mọi mức DataScope (OWN/CHANNEL/DEPARTMENT/ALL) đều
phải được enforce — trước đây chỉ OWN được kiểm, user scope CHANNEL đọc/ghi
được dữ liệu của mọi khách.
"""
from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.customers.models import Customer
from apps.orders.application.services.contact_activity_service import ContactActivityService
from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes


def enforce_contacts_scope_or_404(user, customer_id: int) -> Customer:
    """Trả về `Customer` nếu tồn tại và nằm trong scope `contacts` của `user`.

    404 (không lộ tồn tại) nếu khách không tồn tại HOẶC nằm ngoài scope. Phủ
    đủ 4 mức DataScope: OWN kiểm theo đơn mình phụ trách; CHANNEL/DEPARTMENT
    kiểm theo kênh được cấp; ALL không kiểm thêm.
    """
    customer = get_object_or_404(Customer, pk=customer_id)
    codes = resolved_channel_codes(user, "contacts")
    if codes is None:
        return customer
    if codes is OWN_SCOPE_SENTINEL:
        if not ContactActivityService.user_owns_customer(customer_id, user):
            raise Http404
        return customer
    if not ContactActivityService.customer_in_channels(customer_id, codes):
        raise Http404
    return customer
