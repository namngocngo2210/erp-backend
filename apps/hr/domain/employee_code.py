from __future__ import annotations

PREFIX = "NV"


def next_employee_code(latest_code: str | None) -> str:
    """Sinh mã NV kế tiếp dạng NV0001. ``latest_code`` là mã lớn nhất hiện có."""
    if not latest_code or not latest_code.startswith(PREFIX):
        return f"{PREFIX}0001"
    try:
        number = int(latest_code[len(PREFIX):])
    except ValueError:
        return f"{PREFIX}0001"
    return f"{PREFIX}{number + 1:04d}"
