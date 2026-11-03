"""Bản ghi giữ chỗ để đơn mirror thoả ràng buộc NOT NULL của bảng `orders`.

`Order` có hai FK bắt buộc mà dữ liệu Foxia không cấp được:

* `customer` (PROTECT, NOT NULL) — API Foxia KHÔNG trả tên/điện thoại/địa chỉ
  khách trên `sale.order`. Không có gì để tạo Customer thật.
* `channel` (PROTECT, NOT NULL) — kênh nội bộ là khái niệm của hệ thống ta.

Giải pháp: mỗi chi nhánh Foxia một Customer sentinel, và một Channel sentinel
dùng chung. Vì sao sentinel theo chi nhánh chứ không phải mỗi đơn một khách:
Foxia có thể sinh hàng chục nghìn đơn/tháng, tạo từng ấy Customer rỗng sẽ phình
bảng `customers` và bẩn mọi báo cáo khách hàng (loyal customers, resale
tracking, phân loại khách mới/cũ) mà không thêm một thông tin hữu ích nào.

Channel sentinel đặt `is_active=False` để không lọt vào ô chọn kênh ở form tạo
đơn — nó không phải kênh bán hàng, chỉ là chỗ neo khoá ngoại.
"""

from __future__ import annotations

from apps.channels_core.models import Channel
from apps.customers.models import Customer

FOXIA_CHANNEL_CODE = "foxia"
FOXIA_CUSTOMER_CODE_PREFIX = "FOXIA-"


def get_sentinel_channel() -> Channel:
    """Channel giữ chỗ cho mọi đơn Foxia. Tạo một lần, dùng lại mãi."""
    channel = Channel.objects.filter(code=FOXIA_CHANNEL_CODE, deleted_at__isnull=True).first()
    if channel is not None:
        return channel
    return Channel.objects.create(
        code=FOXIA_CHANNEL_CODE,
        name="Foxia (đối tác)",
        owner="",
        # Không phải kênh bán: ẩn khỏi ô chọn kênh của form tạo đơn.
        is_active=False,
    )


def get_sentinel_customer(branch_foxia_id: int | None) -> Customer:
    """Customer giữ chỗ theo chi nhánh Foxia.

    `phone_normalized` để rỗng có chủ đích: ràng buộc unique số điện thoại là
    partial (bỏ qua chuỗi rỗng), nên nhiều sentinel cùng tồn tại được — và
    quan trọng hơn, sentinel không bao giờ khớp nhầm với khách thật khi tra
    theo số.
    """
    code = f"{FOXIA_CUSTOMER_CODE_PREFIX}{branch_foxia_id or 0}"
    customer = Customer.objects.filter(code=code, deleted_at__isnull=True).first()
    if customer is not None:
        return customer
    return Customer.objects.create(
        code=code,
        full_name=f"[Foxia] Khách chi nhánh {branch_foxia_id or 'không xác định'}",
        phone_raw="",
        phone_normalized="",
        # Không để mặc định True: sentinel không phải khách mới, nó không phải
        # khách. Đặt False để nếu có lọt vào phép đếm nào thì cũng không làm
        # phồng nhóm "khách mới".
        is_new_customer=False,
    )
