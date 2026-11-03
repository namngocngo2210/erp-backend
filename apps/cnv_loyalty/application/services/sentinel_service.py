"""Bản ghi giữ chỗ để đơn mirror CNV thoả ràng buộc NOT NULL của bảng `orders`.

`Order.channel` là FK PROTECT NOT NULL còn kênh là khái niệm nội bộ — CNV không
có gì tương ứng, nên phải có một Channel giữ chỗ (đặt `is_active=False` để không
lọt vào ô chọn kênh ở form tạo đơn: nó không phải kênh bán, chỉ là chỗ neo khoá
ngoại).

Khách hàng thì KHÁC Foxia. Foxia không trả PII nên mỗi chi nhánh một sentinel;
CNV có trả tên + SĐT, nên:

* khớp được `phone_normalized` với khách có sẵn → dùng khách THẬT (chỉ đọc);
* không khớp → một sentinel dùng chung, còn tên/SĐT thô ghi thẳng vào
  `Order.name` / `Order.phone`.

Vì sao không tạo `Customer` mới cho khách lạ: (a) `.save()` kích
`_on_customer_saved` → đẩy ngược khách lên CNV, đúng vòng lặp echo mà cả thiết
kế này tránh; (b) phình bảng `customers` và bẩn phép đếm "khách mới" bằng những
bản ghi mà không luồng CSKH nào chạm tới. Dữ liệu thô vẫn nguyên trên `Order`
nên ngày nào chốt tính doanh thu CNV thì promote được, không mất gì.
"""

from __future__ import annotations

from apps.channels_core.models import Channel
from apps.customers.models import Customer

CNV_CHANNEL_CODE = "cnv"
CNV_SENTINEL_CUSTOMER_CODE = "CNV-UNKNOWN"


def get_sentinel_channel() -> Channel:
    """Channel giữ chỗ cho mọi đơn CNV. Tạo một lần, dùng lại mãi."""
    channel = Channel.objects.filter(code=CNV_CHANNEL_CODE, deleted_at__isnull=True).first()
    if channel is not None:
        return channel
    return Channel.objects.create(
        code=CNV_CHANNEL_CODE,
        name="CNV Loyalty (app khách hàng)",
        owner="",
        is_active=False,
    )


def get_sentinel_customer() -> Customer:
    """Customer giữ chỗ dùng chung cho đơn CNV không khớp được khách nội bộ.

    `phone_normalized` để rỗng có chủ đích: ràng buộc unique số điện thoại là
    partial (bỏ qua chuỗi rỗng) và quan trọng hơn — sentinel không bao giờ khớp
    nhầm với khách thật khi tra theo số.
    """
    customer = Customer.objects.filter(
        code=CNV_SENTINEL_CUSTOMER_CODE, deleted_at__isnull=True
    ).first()
    if customer is not None:
        return customer
    return Customer.objects.create(
        code=CNV_SENTINEL_CUSTOMER_CODE,
        full_name="[CNV] Khách chưa khớp",
        phone_raw="",
        phone_normalized="",
        # Sentinel không phải khách mới, nó không phải khách. Đặt False để nếu
        # có lọt vào phép đếm nào thì cũng không làm phồng nhóm "khách mới".
        is_new_customer=False,
    )


def resolve_customer(phone_normalized: str) -> tuple[Customer, bool]:
    """Trả (customer, matched). `matched=False` nghĩa là đang dùng sentinel."""
    phone = (phone_normalized or "").strip()
    if phone:
        customer = (
            Customer.objects.filter(phone_normalized=phone, deleted_at__isnull=True)
            .order_by("id")
            .first()
        )
        if customer is not None:
            return customer, True
    return get_sentinel_customer(), False
