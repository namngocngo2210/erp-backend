"""Định nghĩa số liệu của API Daily Report — GHÉP từ hằng số Báo cáo tổng / Báo
cáo sale (apps/orders/reporting/shared.py), không chép mã. Đổi luật ở đó là API
đổi theo, số của trợ lý không thể lệch màn hình CRM một cách im lặng.
"""

from __future__ import annotations

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.orders.models import OrderSource, OrderStatus
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS
from apps.orders.reporting.shared import (
    ADS_DAILY_COST_MULTIPLIER,
    AWAITING_DELIVERY_ORDER_STATUSES,
    CLOSED_REVENUE_SALES_STATUS_CODES,
    CLOSED_SALES_STATUS_CODES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    DIRTY_SALES_STATUS_CODES,
    FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    NOT_BUYING_SALES_STATUS_CODE,
    NURTURE_SALES_STATUS_CODES_FOR_CAPACITY,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    SALE_CHANNEL_GROUPS,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    UNREACHABLE_SALES_STATUS_CODES,
    UNREACHED_SALES_STATUS_CODES,
    WANTS_STORE_VISIT_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
)

# ── Trạng thái sale → bucket (rời nhau, phủ hết crm_catalog.SALES_STATUS_OPTIONS) ──
CLOSED_CODES = CLOSED_SALES_STATUS_CODES
CLOSED_REVENUE_CODES = CLOSED_REVENUE_SALES_STATUS_CODES
# no_answer_1..5: còn nhịp gọi tiếp — "đang gọi", chưa phải không tiếp cận được.
CALLING_CODES = tuple(c for c in UNREACHABLE_SALES_STATUS_CODES if c not in UNREACHED_SALES_STATUS_CODES)
CANCELLED_SALE_CODE = "cancelled"
ARCHIVED_SALE_CODE = "archived"

BUCKET_CODES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("closed", CLOSED_CODES),
    ("calling", CALLING_CODES),
    ("unreached", UNREACHED_SALES_STATUS_CODES),
    ("nurture", NURTURE_SALES_STATUS_CODES_FOR_CAPACITY),
    ("wants_store_visit", (WANTS_STORE_VISIT_SALES_STATUS_CODE,)),
    ("dirty", DIRTY_SALES_STATUS_CODES),
    ("not_buying", (NOT_BUYING_SALES_STATUS_CODE,)),
    ("underlying_condition", (UNDERLYING_CONDITION_SALES_STATUS_CODE,)),
    ("warranty", (WARRANTY_SALES_STATUS_CODE,)),
    ("cancelled", (CANCELLED_SALE_CODE,)),
    ("archived", (ARCHIVED_SALE_CODE,)),
)
LEAD_BUCKETS: tuple[str, ...] = tuple(bucket for bucket, _ in BUCKET_CODES)
_CODE_TO_BUCKET = {code: bucket for bucket, codes in BUCKET_CODES for code in codes}
ALL_BUCKETED_CODES: tuple[str, ...] = tuple(_CODE_TO_BUCKET)


def bucket_of(code: str | None) -> str:
    return _CODE_TO_BUCKET.get(code or "", "other")


# Mẫu số tỷ lệ chốt (summary_close_rate_by_staff): loại 2 mã ngoài tầm sale online.
CLOSE_RATE_EXCLUDED_CODES = (UNDERLYING_CONDITION_SALES_STATUS_CODE, WANTS_STORE_VISIT_SALES_STATUS_CODE)

# Lead KHÔNG còn có thể chốt → rời pipeline.
PIPELINE_TERMINAL_CODES: tuple[str, ...] = (
    *CLOSED_CODES,
    *DIRTY_SALES_STATUS_CODES,
    NOT_BUYING_SALES_STATUS_CODE,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
    CANCELLED_SALE_CODE,
    ARCHIVED_SALE_CODE,
)

# ── Đơn (Báo cáo tổng) ──
DELIVERED_STATUS = DELIVERED_SUCCESS_ORDER_STATUS
RETURNED_STATUSES = RETURN_ORDER_STATUSES
RETURN_RISK_STATUSES = RETURN_RISK_ORDER_STATUSES
AWAITING_STATUSES = AWAITING_DELIVERY_ORDER_STATUSES
CANCELLED_STATUS = OrderStatus.CANCELLED.value
CANCELLED_INVOICE = CANCELLED_INVOICE_STATUS
EXCLUDED_BRANCHES: tuple[str, ...] = tuple(EXCLUDED_BRANCH_IDS) + tuple(OVERVIEW_EXCLUDED_BRANCH_IDS)
CHANNEL_GROUPS = SALE_CHANNEL_GROUPS
# Nguồn duy nhất mang URL landing có utm → mẫu số độ phủ quy về chiến dịch (/ads meta.attribution).
LANDING_SOURCE = OrderSource.LANDING.value


def order_bucket_of(code: int | None) -> str | None:
    """Rổ giao hàng của Báo cáo tổng. Mã lạ rơi vào 'awaiting' (phần bù) để không
    rớt khỏi tổng; None = đơn chưa có trạng thái."""
    if code is None:
        return None
    if code == CANCELLED_STATUS:
        return "cancelled"
    if code == DELIVERED_STATUS:
        return "delivered"
    if code in RETURNED_STATUSES:
        return "returned"
    if code in RETURN_RISK_STATUSES:
        return "return_risk"
    return "awaiting"


def cost_multiplier_rules() -> dict:
    """Phát biểu luật hệ số để Hermes hiểu `multiplier` trong dòng /ads. Không chép số."""
    facebook = [
        {"effective_from": eff.isoformat(), "multiplier": float(m)} for eff, m in FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE
    ]
    facebook.append({"effective_from": None, "multiplier": float(ADS_DAILY_COST_MULTIPLIER)})
    return {
        "facebook": {
            "schedule": facebook,
            "rule": "Chọn theo ngày BẮT ĐẦU kỳ (`from`): mốc effective_from gần nhất ≤ from; cả kỳ dùng một hệ số. "
                    "effective_from null = hệ số gốc khi from trước mọi mốc.",
        },
        "google": {
            "schedule": [{"effective_from": None, "multiplier": float(GOOGLE_ADS_DAILY_COST_MULTIPLIER)}],
            "rule": "Cố định.",
        },
    }


ORDER_BUCKET_LABELS = {
    "delivered": "Giao thành công",
    "awaiting": "Chờ giao (mọi trạng thái chưa có kết quả cuối)",
    "returned": "Hoàn (đang/đã chuyển hoàn)",
    "return_risk": "Rủi ro hoàn (chờ giao lại)",
    "cancelled": "Đã hủy (không nằm trong tổng)",
}

METRIC_GLOSSARY: dict[str, str] = {
    "rows": "API trả TỪNG DÒNG, không trả số gộp (từ 16/09/2026). Mọi chỉ số cũ tính lại bằng cách đếm/cộng dòng theo các luật dưới đây. Kéo hết trang bằng `next_cursor` trước khi cộng.",
    "lead": "Một dòng của /leads/stats = một đơn nội bộ (không tính đơn mirror foxia/cnv, không tính đơn gốc đã được khôi phục thành bản sao) tạo trong kỳ, theo ngày tạo giờ Việt Nam — mọi kênh, mọi nhân viên, kể cả lead chưa gán. Rộng hơn phạm vi Báo cáo sale (chỉ kênh có nhân viên Ads phụ trách và nhân viên phòng Sale).",
    "bucket": "`sales_status.bucket` (lead) và `order_status.bucket` (đơn) là luật rổ của CRM, tính ở server. Cộng theo bucket, không tự tra mã. `sales_status.bucket` null = chưa đánh giá (unassessed); `staff` null = chưa gán (unassigned).",
    "closed": "Số dòng /leads/stats có `sales_status.bucket = closed` (closed, closed_express, closed_scheduled_date, created_order).",
    "closed_revenue": "Σ `amounts.total` của dòng /leads/stats có `sales_status.code` ∈ {closed, closed_express, created_order} — KHÔNG tính closed_scheduled_date, đúng luật Báo cáo sale. Góc nhìn sale; khác `total_revenue` (đã lên hoá đơn) — không cộng vào nhau.",
    "close_rate": "closed / (số dòng /leads/stats − dòng có sales_status.code ∈ {underlying_condition, wants_store_visit}). Cùng CÔNG THỨC mẫu số với Báo cáo sale nhưng tính trên toàn bộ đơn nội bộ, nên có thể THẤP hơn màn hình. Không chia khi mẫu số 0.",
    "calling": "Không nghe máy lần 1..5 — còn nhịp gọi tiếp.",
    "unreached": "Không nghe máy lần 6 hoặc không liên hệ được.",
    "nurture": "Tư vấn thêm / hẹn lịch / gọi lại sau.",
    "dirty": "Spam, lặp, sai số, khách bom hàng, data không chất lượng.",
    "other": "Có trạng thái sale nhưng mã không thuộc từ điển hiện tại.",
    "orders": "Một dòng của /orders/stats = một đơn đã lên hoá đơn KiotViet (invoice_status khác null), không huỷ (status ≠ 6, invoice_status ≠ 2), tạo trong kỳ, loại chi nhánh nội bộ và kho Đại Mỗ. Số dòng = total_orders cũ; đếm theo `order_status.bucket` = delivered / awaiting / returned / return_risk cũ.",
    "total_revenue": "Σ `amounts.total` (VND) của mọi dòng /orders/stats. Σ theo bucket cộng lại đúng bằng tổng (bất biến).",
    "net_revenue": "total_revenue − Σ `amounts.total` của dòng có `order_status.bucket = returned` (status 4, 5, 12, 13).",
    "revenue_lines": "Một dòng của /revenue = một sản phẩm trong một đơn thuộc tập /orders/stats (nối `order_id`). Doanh thu theo sản phẩm = Σ `amount` theo `sku`. Σ `amount` theo đơn có thể khác `amounts.total` bên đơn vì phí ship / giảm giá cấp đơn.",
    "channel_group": "shopee / tiktok (theo nguồn đơn hoặc prefix mã hoá đơn HDSPE_/HDTTS_) → facebook (kênh facebook-*) → web (google-ads-web) → off_other. Sàn xét trước kênh.",
    "channel": "Kênh (`channel`) trong CRM = một Digital: nhân viên/đội Ads phụ trách, ví dụ `facebook-cuong` = Facebook Cường, `google-ads-web` = Google Ads/Web. KHÔNG phải fanpage. Hỏi 'theo Digital' thì nhóm dòng đơn theo `channel.id`/`channel.name` và nối chi phí qua `channels` của dòng /ads (hoặc `ad_account_channels`). Hỏi 'theo fanpage' thì API này không có — hội thoại theo fanpage đọc ở /api/ai/hermes/conversations?page=.",
    "open_leads": "Một dòng của /pipeline = lead tạo trong lookback_days ngày, chưa lên hoá đơn, chưa huỷ, trạng thái sale chưa thuộc nhóm kết thúc (closed, dirty, not_buying, underlying_condition, warranty, cancelled, archived). Rổ tuổi cũ theo `age_days`: 0-1d / 2-3d / 4-7d / 8d+.",
    "ads_rows": "Một dòng của /ads = (ngày × nền tảng × TKQC × chiến dịch). Facebook cộng từ grain ad (ad unique toàn cục), Google Max theo (ngày, customer, campaign) vì hai MCC lặp cùng số tiền. `spend` CHƯA nhân hệ số; `spend_adjusted` = round(spend × multiplier). Không có ad set / ad riêng lẻ. Từ 21/09/2026 dòng chiến dịch Facebook mang thêm `leads` / `closed` / `orders` / `revenue` (xem `campaign_attribution`).",
    "campaign_attribution": "Đơn/lead quy về chiến dịch Facebook qua `fb_campaign_id` trên dòng /leads/stats, /orders/stats, /pipeline (= `utm_id` Meta điền trên URL landing, chỉ đơn nguồn landing có URL gắn utm; Google chưa gắn tag). Trên dòng /ads (chỉ dòng chiến dịch, dòng dư = null): `leads` = số dòng /leads/stats tạo trong `day` có fb_campaign_id = campaign_id; `closed` = trong đó sales_status.bucket = closed; `orders` / `revenue` = số dòng và Σ `amounts.total` của /orders/stats quy về campaign. Quy theo ngày TẠO lead, không theo ngày chốt. Chiến dịch có đơn nhưng không tiêu tiền trong ngày vẫn có dòng spend 0; chiến dịch chưa sync danh mục có `account_id` rỗng. CPA = spend_adjusted / closed hoặc / orders; ROAS = revenue / spend_adjusted — chỉ tính trên phần nối được, luôn nêu độ phủ `meta.attribution` (leads_with_campaign / leads; landing_leads_with_campaign / landing_leads) vì phần lớn đơn không nối được là landing không gửi URL, không phải campaign không ra đơn.",
    "remainder_row": "Mỗi (ngày × nền tảng × TKQC) có đúng MỘT dòng `is_remainder = true`, `campaign_id = null`: chi tiêu TKQC (bản sao gộp Max) − Σ chiến dịch. Có thể ÂM khi grain ad sync lệch nhịp. Σ mọi dòng theo TKQC = chi tiêu TKQC (by_account cũ); Σ dòng có campaign_id = by_campaign cũ; Σ dòng dư = cost_outside_campaigns cũ. Đừng cộng dòng dư vào chiến dịch.",
    "cost_multiplier": "Hệ số VAT/phụ phí: xem `cost_multiplier` ở cấp `data` của /definitions. Chọn theo ngày BẮT ĐẦU kỳ (`from`), cả kỳ một hệ số — cố ý khác mart `ai.chi_phi_quang_cao` (áp theo từng ngày), nên kỳ bắc qua mốc 01/08 hay 01/09/2026 cho hai tổng khác nhau.",
    "ad_account_channels": "Bản đồ TKQC → Digital hiện hành (không theo kỳ). `channels` rỗng = TKQC chưa map kênh — tiền vẫn trong tổng TKQC, nêu ra khi cộng theo Digital. TKQC map nhiều kênh: API không chia, để nguyên mảng (màn Báo cáo Ads chia đều).",
}
