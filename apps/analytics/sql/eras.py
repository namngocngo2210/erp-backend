"""Mốc chia thời kỳ ngành hàng và các ngưỡng phân nhóm hành vi.

Mốc 2025-01-01 SUY TỪ DỮ LIỆU, không phải quy ước: giá trị đơn trung bình theo năm
trên prod là 75k (2020), 198k (2021), 163k (2022), 184k (2023), 297k (2024) rồi nhảy
lên 1.455k (2025) và 1.240k (2026) — đứt gãy khi chuyển sang máy massage Delax.

Ngưỡng giá suy từ phân bố của CHÍNH thời kỳ hiện tại (53.470 khách): dưới 1tr 29,6%,
1-2tr 64,2%, từ 2tr 6,2%. Không dùng phân vị động — ngưỡng phải ổn định giữa các lần
refresh, nếu không khách sẽ nhảy nhóm mà bản thân họ không thay đổi gì.
"""

CURRENT_ERA_START = "2025-01-01"

PRICE_TIER_MID_FLOOR = 1_000_000
PRICE_TIER_HIGH_FLOOR = 2_000_000

RECENCY_ACTIVE_MAX_DAYS = 90
RECENCY_COOLING_MAX_DAYS = 180
RECENCY_AT_RISK_MAX_DAYS = 365
