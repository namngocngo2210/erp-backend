"""Ranh giới giữa đơn NỘI BỘ và đơn MIRROR từ hệ thống đối tác.

Bối cảnh: đơn kéo về từ CRM đối tác (Foxia) nằm chung bảng `orders` để tái dùng
hạ tầng sẵn có, phân biệt bằng `order_source`. Nhưng chúng KHÔNG phải đơn của
công ty — cộng vào doanh thu, tồn kho hay báo cáo khách hàng là sai số liệu mà
không ai phát hiện ra ngay.

Hàng rào đặt ở HAI tầng vì không tầng nào phủ hết:

1. **ORM** — `Order.objects` là manager đã lọc (xem `apps.orders.models`). Phủ
   toàn bộ ~170 điểm query hiện có mà không phải sửa từng chỗ, và mọi code viết
   sau này mặc định an toàn. Đường Foxia phải chọn tham gia tường minh bằng
   `Order.all_objects`.
2. **SQL thô** — khoảng 15 báo cáo viết SQL tay (`apps/customers/report_*`,
   `lark_ads_report_sync`) đi thẳng xuống bảng, không qua manager. Dùng
   `sql_exclude_mirror_orders()` để ghép mệnh đề lọc vào WHERE của chúng.

Hằng số sống ở `apps.common` (tầng dưới cùng) vì cả `apps.orders` lẫn
`apps.customers`/`apps.facebook_ads` đều cần, mà chúng không được import nhau.
Test `test_mirror_sources_match_enum` khẳng định danh sách này khớp với
`OrderSource`, nên hai nơi không thể trôi khỏi nhau trong im lặng.
"""

from __future__ import annotations

# Giá trị `order_source` của đơn mirror. Thêm đối tác mới thì thêm vào đây —
# một dòng, và cả hai tầng hàng rào tự phủ.
MIRROR_ORDER_SOURCES: tuple[str, ...] = ("foxia", "cnv")


def sql_exclude_mirror_orders(alias: str = "o") -> str:
    """Mệnh đề SQL giữ lại đơn nội bộ, dùng ghép vào WHERE của query thô.

    Phải xử lý NULL tường minh: `order_source` là cột nullable và phần lớn đơn
    cũ để trống, mà trong SQL `NULL <> 'foxia'` cho ra NULL chứ không phải TRUE
    — viết mỗi `<>` là mất sạch đơn cũ khỏi báo cáo.

    Trả về chuỗi hằng, không tham số hoá: giá trị lấy từ hằng số nội bộ của mã
    nguồn (không phải input người dùng) và nhiều query gọi nó ở vị trí không
    truyền params được.
    """
    prefix = f"{alias}." if alias else ""
    values = ", ".join(f"'{source}'" for source in MIRROR_ORDER_SOURCES)
    return f"({prefix}order_source IS NULL OR {prefix}order_source NOT IN ({values}))"
