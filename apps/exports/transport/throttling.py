from rest_framework.throttling import SimpleRateThrottle

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin


class ExportCreateThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Trần tần suất TẠO job export.

    Cùng lý do với OrderPrintRateThrottle: dedupe chỉ chặn bấm lại y hệt, còn một
    script đổi tham số mỗi lượt vẫn tạo được job vô hạn và lấp kín queue `exports`.
    Key theo user (không phải IP) vì cả văn phòng ra internet chung một IP NAT.
    """

    scope = "export_create"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = (
            f"user-{user.pk}"
            if getattr(user, "is_authenticated", False)
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def allow_request(self, request, view):
        # Throttle chỉ tính cho POST. Panel poll GET mỗi 3s — tính chung thì người
        # dùng bị chặn oan trong vài phút mà chẳng tạo job nào.
        if request.method != "POST":
            return True
        return super().allow_request(request, view)
