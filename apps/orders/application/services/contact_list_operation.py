"""Enrich tác nghiệp contact list — module tách lại từ contact_list_service.py.

Đây là phần xử lí CustomerOperationState để tính operation payload + counts
trên danh sách liên hệ (đơn mới nhất mỗi khách). Module tách vì file size rule
(~300 dòng tối đa).
"""
from __future__ import annotations

from apps.contact_flow.models import CustomerOperationState
from apps.contact_flow.application.services.buckets import bucket_of

# Khách không có CustomerOperationState (chưa vào flow tác nghiệp) — bucket "none" là
# giá trị cố định, KHÔNG suy ra từ bucket_of(None) (hàm đó trả "ended" cho due_at=null
# của khách ĐÃ vào flow nhưng kết thúc nhánh — 2 tình huống khác nhau).
NO_OPERATION_STATE = {
    "stage_name": "",
    "stage_color": "",
    "allow_edit_due": False,
    "due_at": None,
    "bucket": "none",
}


def operation_state_map(customer_ids: list[int]) -> dict[int, CustomerOperationState]:
    """1 query bulk (IN) — KHÔNG N+1 theo số khách."""
    if not customer_ids:
        return {}
    states = CustomerOperationState.objects.filter(
        customer_id__in=customer_ids
    ).select_related("current_stage")
    return {state.customer_id: state for state in states}


def operation_payload(state: CustomerOperationState | None, *, now) -> dict:
    """Render operation state thành payload cho frontend."""
    if state is None:
        return dict(NO_OPERATION_STATE)
    return {
        "stage_name": state.current_stage.name,
        "stage_color": state.current_stage.color,
        "allow_edit_due": state.current_stage.allow_edit_due,
        "due_at": state.due_at,
        "bucket": bucket_of(state.due_at, now=now),
    }


def refresh_operation_buckets(rows: list[dict], *, now) -> None:
    """Tính lại bucket theo `now` cho enriched rows lấy từ cache (in-place).

    `bucket` là trường DUY NHẤT trong enriched row phụ thuộc thời điểm ĐỌC
    (overdue/today/upcoming trôi theo đồng hồ) — mọi trường khác chỉ đổi khi dữ
    liệu đổi. Row có due_at=None giữ nguyên bucket đã cache: đó là "none" (chưa
    vào flow) hoặc "ended" (hết nhánh), cả hai bất biến theo thời gian — KHÔNG
    gọi bucket_of(None) vì hàm đó trả "ended" sai cho khách chưa vào flow.
    """
    for row in rows:
        due_at = row["operation"]["due_at"]
        if due_at is not None:
            row["operation"]["bucket"] = bucket_of(due_at, now=now)


def compute_operation_counts(enriched_rows: list[dict]) -> dict[str, dict[str, int]]:
    """Mirror `compute_action_counts`, key = bucket (5 bucket + "all")."""
    counts: dict[str, dict[str, int]] = {}
    all_total = 0
    all_pending = 0
    for row in enriched_rows:
        key = row["operation"]["bucket"]
        bucket = counts.setdefault(key, {"pending": 0, "total": 0})
        bucket["total"] += 1
        if row["pending"]:
            bucket["pending"] += 1

        all_total += 1
        if row["pending"] and key != "none":
            all_pending += 1

    counts["all"] = {"pending": all_pending, "total": all_total}
    return counts


def apply_operation_bucket_filter(rows: list[dict], *, operation_bucket) -> list[dict]:
    """Lọc danh sách theo operation bucket."""
    if not operation_bucket:
        return rows
    return [r for r in rows if r["operation"]["bucket"] == operation_bucket]
