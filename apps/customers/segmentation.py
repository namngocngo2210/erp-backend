"""Phân loại khách hàng S1–S7 theo tiêu chí nghiệp vụ.

GTC = giao thành công (Order.status = DELIVERED_SUCCESS). Một khách có thể thỏa nhiều
tiêu chí nên hàm ``classify_segment`` đánh giá theo thứ tự ưu tiên (match đầu tiên thắng):

    S5 (có vấn đề) → S6 (B2B) → S3 (trung thành) → S4 (ngủ đông)
    → S2 (mới mua) → S1 (tiềm năng) → S7 (từ chối) → không phân loại

S5 đứng đầu vì cần xử lý trước khi resale; các bậc quan hệ/giá trị (S6, S3) đứng trên
trạng thái theo độ mới (S4, S2); cuối cùng là nhóm chưa mua (S1, S7).
"""

from __future__ import annotations

from decimal import Decimal

# Mã phân khúc.
S1 = "S1"  # Tiềm năng chưa mua
S2 = "S2"  # Mới mua 1 lần
S3 = "S3"  # Trung thành
S4 = "S4"  # Ngủ đông
S5 = "S5"  # Có vấn đề
S6 = "S6"  # B2B / Đối tác
S7 = "S7"  # Từ chối

# Ngưỡng tiêu chí.
LOYAL_TOTAL_SPENT_THRESHOLD = Decimal("2000000")  # ≥2 triệu → trung thành
LOYAL_MIN_DELIVERED = 2  # ≥2 đơn GTC → trung thành
B2B_MIN_DELIVERED = 3  # ≥3 đơn GTC → B2B/Đối tác
NEW_CUSTOMER_MAX_DAYS = 90  # mua 1 lần trong vòng 90 ngày → mới mua
DORMANT_MIN_DAYS = 180  # >180 ngày không mua → ngủ đông

# Trạng thái gọi tương ứng "Tư vấn thêm / Hẹn lịch / Gọi lại sau / Khách muốn
# đến cửa hàng" — lead còn sống, chưa chốt.
NURTURE_SALES_STATUS_CODES = frozenset(
    {"need_more_consulting", "scheduled", "call_back_later", "wants_store_visit"}
)
NOT_BUYING_SALES_STATUS_CODE = "not_buying"

# Tín hiệu "có vấn đề" lấy từ assessment / trạng thái đơn.
PROBLEM_SALES_STATUS_CODES = frozenset({"warranty", "customer_bombed"})
PROBLEM_DELIVERY_STATUS_CODES = frozenset({"stalled_complaint"})
PROBLEM_POST_PURCHASE_RATING_CODES = frozenset({"poor"})
# Order.status: 4 = IN_RETURN, 5 = RETURNED (hoàn hàng).
PROBLEM_ORDER_STATUSES = frozenset({4, 5})

# Seed danh mục (S1–S7) — dùng cho data migration và làm nhãn dự phòng khi bảng rỗng.
SEGMENT_CATALOG_SEED = [
    {"code": S1, "name": "Tiềm năng chưa mua", "criteria": "Khách có trạng thái gọi \"Tư vấn thêm, Hẹn lịch, Gọi lại sau\"", "care_priority": "Chốt đơn", "sort_order": 10},
    {"code": S2, "name": "Mới mua 1 lần", "criteria": "1 đơn GTC ≤90 ngày", "care_priority": "Upsell trong 30–60 ngày", "sort_order": 20},
    {"code": S3, "name": "Trung thành", "criteria": "≥2 đơn GTC hoặc ≥2 triệu", "care_priority": "Chăm 30–60 ngày/lần", "sort_order": 30},
    {"code": S4, "name": "Ngủ đông", "criteria": ">180 ngày không mua", "care_priority": "Tái kích hoạt ngay", "sort_order": 40},
    {"code": S5, "name": "Có vấn đề", "criteria": "Hoàn / khiếu nại / đánh giá xấu / bảo hành", "care_priority": "Xử lý trước khi resale", "sort_order": 50},
    {"code": S6, "name": "B2B / Đối tác", "criteria": "≥3 đơn GTC hoặc phòng khám/đại lý", "care_priority": "Chăm riêng, quy trình B2B", "sort_order": 60},
    {"code": S7, "name": "Từ chối", "criteria": "Tiếp cận được nhưng không mua", "care_priority": "Remarketing sau 30–60 ngày", "sort_order": 70},
]

SEGMENT_NAME_BY_CODE = {item["code"]: item["name"] for item in SEGMENT_CATALOG_SEED}


def classify_segment(
    *,
    delivered_count: int,
    total_spent: Decimal | int | float | None,
    days_since_last_purchase: int | None,
    has_problem: bool,
    latest_sales_status_code: str | None = None,
) -> str:
    """Trả về mã phân khúc (S1–S7) hoặc chuỗi rỗng nếu không khớp tiêu chí nào."""
    spent = Decimal(total_spent) if total_spent is not None else Decimal("0")

    if has_problem:
        return S5
    if delivered_count >= B2B_MIN_DELIVERED:
        return S6
    if delivered_count >= LOYAL_MIN_DELIVERED or spent >= LOYAL_TOTAL_SPENT_THRESHOLD:
        return S3
    if delivered_count >= 1 and days_since_last_purchase is not None and days_since_last_purchase > DORMANT_MIN_DAYS:
        return S4
    if delivered_count == 1 and days_since_last_purchase is not None and days_since_last_purchase <= NEW_CUSTOMER_MAX_DAYS:
        return S2
    if delivered_count == 0 and latest_sales_status_code in NURTURE_SALES_STATUS_CODES:
        return S1
    if delivered_count == 0 and latest_sales_status_code == NOT_BUYING_SALES_STATUS_CODE:
        return S7
    return ""
