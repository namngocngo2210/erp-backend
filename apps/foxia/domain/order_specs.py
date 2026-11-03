"""Đặc tả tài nguyên đơn hàng Foxia: tên field và cửa sổ quét.

Tách khỏi service để danh sách field khớp đúng tài liệu Foxia mà không lẫn với
logic đồng bộ — khi Foxia bổ sung field thì sửa đúng một chỗ.
"""

from __future__ import annotations

# §7 tài liệu: đơn hàng. KHÔNG lấy nhóm field đối soát (ngaydoisoat,
# trangthaiketoan, tongcongnoconlai) — ngoài phạm vi giai đoạn này.
ORDER_FIELDS = (
    "id",
    "name",
    "cohoi_id",
    "ngaytao",
    "ngayxacnhan",
    "ngayhoanthanh",
    "nhanvienmarketing_id",
    "user_id",
    "team_id",
    "congtycon_id",
    "giaidoan_id",
    "phanloaigiaidoan",
    "amount_total",
    "tongsauchietkhaubosung",
    "tongtiendathu",
    "trangthaihoantra",
)

# §7.1: dòng sản phẩm, lọc theo danh sách order_id của lô vừa kéo.
ORDER_LINE_FIELDS = ("id", "order_id", "product_id", "product_uom_qty", "price_subtotal")

# §8.1: vận đơn. `phivanchuyenthucte` lấy Ở ĐÂY chứ không từ luồng đối soát
# (§8.2) — cùng con số nhưng đường này không kéo theo cả nhánh đối soát.
SHIPMENT_FIELDS = (
    "id",
    "name",
    "donhang_id",
    "trangthai",
    "phivanchuyenthucte",
    "ngayhoanthanhdon",
    "ngayhoantradon",
)

# §6: cơ hội/lead — CHỈ dùng để enrich chiều marketing cho đơn đã kéo, lọc theo
# danh sách id. Không phải đồng bộ lead (không kéo lead không sinh đơn).
LEAD_ENRICH_FIELDS = (
    "id",
    "nguonkhachhang_id",
    "kenhmarketing_id",
    "thuonghieu_id",
    "nhanvienmarketing_id",
)


class OrderSyncMode:
    """Hai lượt quét đơn, theo hai trường ngày khác nhau.

    Một đơn chốt tháng 7 có thể giao thành công tháng 8 — lọc theo `ngayxacnhan`
    sẽ không bao giờ thấy nó đổi trạng thái. Vì vậy phải có lượt thứ hai theo
    `ngayhoanthanh`, cửa sổ dài hơn.
    """

    CONFIRMED = "sale.order.confirmed"
    DELIVERED = "sale.order.delivered"

    DATE_FIELD = {CONFIRMED: "ngayxacnhan", DELIVERED: "ngayhoanthanh"}
    # Số ngày lùi lại mỗi lượt. Đây là giá phải trả cho việc Foxia không cấp
    # `write_date`: bản ghi sửa trong quá khứ chỉ bắt được nếu còn trong cửa sổ.
    DEFAULT_LOOKBACK_DAYS = {CONFIRMED: 3, DELIVERED: 14}
