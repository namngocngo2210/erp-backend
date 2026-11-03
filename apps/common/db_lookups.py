"""Lookup ``__ilike_contains`` — tìm chuỗi con KHÔNG phân biệt hoa thường mà vẫn
dùng được index GIN trigram.

Django dịch ``__icontains`` thành ``UPPER(col::text) LIKE UPPER(%s)``. Mọi index
``gin_trgm_ops`` trong repo này nằm trên CỘT THÔ (``orders.order_code``,
``invoice_code``, ``shipping_code``, ...), mà ``UPPER(col)`` là một biểu thức
khác nên planner KHÔNG dùng được index đó — kết quả là seq scan cả bảng.

``col ILIKE %s`` khớp đúng index. pg_trgm hỗ trợ cả LIKE lẫn ILIKE nên không cần
thêm index mới.

Lưu ý khi đặt tên lookup: tránh mọi tên bắt đầu bằng ``i`` mà Postgres backend
coi là case-insensitive (``iexact``/``icontains``/...), vì ``lookup_cast`` sẽ tự
bọc ``UPPER()`` quanh vế trái và ta quay lại đúng vấn đề đang sửa.
"""
from django.db.models import CharField, Lookup, TextField


class ILikeContains(Lookup):
    lookup_name = "ilike_contains"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        # `prep_for_like_query` escape `%`, `_`, `\` trong chuỗi người dùng gõ —
        # mã hoá đơn TikTok dạng `HDTTS_5852...` có dấu gạch dưới, không escape
        # thì nó thành ký tự đại diện.
        term = connection.ops.prep_for_like_query(self.rhs)
        return f"{lhs} ILIKE %s", (*lhs_params, f"%{term}%")


CharField.register_lookup(ILikeContains)
TextField.register_lookup(ILikeContains)
