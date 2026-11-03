from __future__ import annotations
from datetime import date, timedelta


class SalaryRuleError(ValueError):
    pass


def close_previous(*, current_from: date, new_from: date) -> date:
    """Trả về effective_to của bản lương cũ khi thêm bản mới hiệu lực ``new_from``."""
    if new_from <= current_from:
        raise SalaryRuleError("Mốc hiệu lực mới phải sau bản lương hiện tại.")
    return new_from - timedelta(days=1)
