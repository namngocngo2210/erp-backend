from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogOption:
    code: str
    label: str


CUSTOMER_CLASSIFICATION_OPTIONS = [
    CatalogOption("easy_close", "Dễ tính - chốt ngay"),
    CatalogOption("hesitant", "Do dự"),
    CatalogOption("difficult", "Khó tính"),
    CatalogOption("purchased_delivered", "Đã mua - giao thành công"),
    CatalogOption("purchased_shipping", "Đã mua - đang giao"),
    CatalogOption("bombed", "Bom hàng"),
    CatalogOption("spam", "Spam"),
    CatalogOption("no_pickup", "Không nhấc máy"),
]

SALES_STATUS_OPTIONS = [
    CatalogOption("closed", "Đã chốt"),
    CatalogOption("closed_express", "Đã chốt/Hỏa tốc"),
    CatalogOption("closed_scheduled_date", "Đã chốt, hẹn ngày"),
    CatalogOption("created_order", "Đã lên đơn"),
    CatalogOption("no_answer_1", "Không nghe máy lần 1"),
    CatalogOption("no_answer_2", "Không nghe máy lần 2"),
    CatalogOption("no_answer_3", "Không nghe máy lần 3"),
    CatalogOption("no_answer_4", "Không nghe máy lần 4"),
    CatalogOption("no_answer_5", "Không nghe máy lần 5"),
    CatalogOption("no_answer_6", "Không nghe máy lần 6"),
    CatalogOption("unreachable", "Không liên hệ được"),
    CatalogOption("wrong_number", "Sai số"),
    CatalogOption("customer_bombed", "Khách bom hàng"),
    CatalogOption("spam", "Spam"),
    CatalogOption("duplicate", "Lặp"),
    CatalogOption("poor_quality_data", "Data không chất lượng"),
    CatalogOption("need_more_consulting", "Tư vấn thêm"),
    CatalogOption("not_buying", "Không mua"),
    CatalogOption("underlying_condition", "Khách bị bệnh nền"),
    CatalogOption("scheduled", "Hẹn lịch"),
    CatalogOption("call_back_later", "Gọi lại sau"),
    CatalogOption("wants_store_visit", "Khách muốn đến cửa hàng"),
    CatalogOption("cancelled", "Huỷ đơn"),
    CatalogOption("warranty", "Bảo hành"),
    CatalogOption("archived", "Đóng case (win-back)"),
]

NOT_CLOSED_REASON_OPTIONS = [
    CatalogOption("too_expensive", "Đắt"),
    CatalogOption("competitor_cheaper", "Mua bên khác rẻ"),
    CatalogOption("china_product", "Hàng trung quốc"),
    CatalogOption("wants_bundle_with_chair", "Muốn mua bộ có sẵn ghế"),
    CatalogOption("family_disapproved", "Người thân không cho mua"),
    CatalogOption("kids_playing", "Trẻ con nghịch"),
    CatalogOption("family_already_bought", "Người thân mua rồi"),
    CatalogOption("wrong_phone_number", "Số không đúng"),
    CatalogOption("competitor_closed_first", "Bên khác chốt trước"),
    CatalogOption("watching_demo", "Đang xem demo"),
    CatalogOption("waiting_for_money", "Chờ tiền"),
    CatalogOption("spouse_not_approved", "Vợ/chồng chưa duyệt"),
    CatalogOption("no_need_anymore", "Không có nhu cầu nữa"),
    CatalogOption("other", "Khác"),
]

OCCUPATION_OPTIONS = [
    CatalogOption("office", "Văn phòng"),
    CatalogOption("teacher", "Giáo viên"),
    CatalogOption("healthcare", "Y tế/điều dưỡng"),
    CatalogOption("driver", "Lái xe"),
    CatalogOption("heavy_labor", "Lao động nặng"),
    CatalogOption("business", "Kinh doanh"),
    CatalogOption("housewife", "Nội trợ"),
    CatalogOption("retired", "Hưu trí"),
]

# Bộ nhóm tuổi chuẩn của Facebook Ads — giữ khớp để đối chiếu chân dung khách
# với báo cáo audience trong apps/facebook_ads.
AGE_GROUP_OPTIONS = [
    CatalogOption("18_24", "18-24"),
    CatalogOption("25_34", "25-34"),
    CatalogOption("35_44", "35-44"),
    CatalogOption("45_54", "45-54"),
    CatalogOption("55_64", "55-64"),
    CatalogOption("65_plus", "65+"),
    # "Đã hỏi, khách không nói" — khác với cột rỗng nghĩa là "sale chưa hỏi".
    CatalogOption("not_disclosed", "Không xác định"),
]

DELAX_SOURCE_OPTIONS = [
    CatalogOption("facebook", "Facebook hiển thị"),
    CatalogOption("friend_referral", "Bạn bè giới thiệu"),
    CatalogOption("youtube", "Youtube"),
    CatalogOption("koc", "KOC giới thiệu"),
    CatalogOption("tiktok", "Tiktok"),
    CatalogOption("shopee", "Shopee"),
]

PAIN_LEVEL_OPTIONS = [
    CatalogOption("light", "Nhẹ"),
    CatalogOption("moderate", "Trung bình"),
    CatalogOption("severe", "Nặng"),
    CatalogOption("very_severe_chronic", "Rất nặng/Lâu năm"),
]

PAIN_DURATION_OPTIONS = [
    CatalogOption("under_3m", "<3 tháng"),
    CatalogOption("3_to_6m", "3–6 tháng"),
    CatalogOption("6_to_12m", "6–12 tháng"),
    CatalogOption("over_1y", "Trên 1 năm"),
]

LIFESTYLE_HABIT_OPTIONS = [
    CatalogOption("sit_a_lot", "Ngồi nhiều"),
    CatalogOption("lie_a_lot", "Nằm nhiều"),
    CatalogOption("walk_a_lot", "Đi nhiều"),
    CatalogOption("heavy_labor", "Lao động nặng"),
    CatalogOption("low_activity", "Ít vận động"),
    CatalogOption("sports", "Chơi thể thao"),
]

RELAX_TIME_OPTIONS = [
    CatalogOption("morning", "Sáng"),
    CatalogOption("noon", "Trưa"),
    CatalogOption("afternoon", "Chiều"),
    CatalogOption("evening", "Tối"),
]

PURCHASE_BARRIER_OPTIONS = [
    CatalogOption("price", "Giá"),
    CatalogOption("quality_durability", "Chất lượng - Độ bền"),
    CatalogOption("effectiveness", "Hiệu quả"),
    CatalogOption("brand", "Thương hiệu"),
    CatalogOption("safety", "Tính an toàn"),
    CatalogOption("warranty", "Bảo hành"),
    CatalogOption("waiting_family", "Chờ hỏi gia đình"),
    CatalogOption("comparing_others", "Đang so sánh với bên khác"),
]

CALL_DURATION_OPTIONS = [
    CatalogOption("under_1m", "1 phút"),
    CatalogOption("1_to_3m", "1-3 phút"),
    CatalogOption("3_to_5m", "3-5 phút"),
    CatalogOption("over_5m", "> 5 phút"),
]

POST_PURCHASE_RATING_OPTIONS = [
    CatalogOption("good", "Tốt"),
    CatalogOption("average", "Trung bình"),
    CatalogOption("poor", "Kém, không có tác dụng gì"),
]

USED_MASSAGE_BEFORE_OPTIONS = [
    CatalogOption("yes", "Có"),
    CatalogOption("no", "Chưa"),
]

DELIVERY_BUSINESS_STATUS_OPTIONS = [
    CatalogOption("warehouse_processing", "Kho đang xử lý"),
    CatalogOption("dispatched", "Đã xuất kho"),
    CatalogOption("received", "Đã nhận"),
    CatalogOption("not_received", "Chưa nhận"),
    CatalogOption("stalled_complaint", "Đơn dừng hành trình nhiều ngày, đang khiếu nại"),
    CatalogOption("post_office_lost", "Bưu cục làm mất"),
]

# Mã trạng thái giao hàng nghĩa là "đơn đã xuất kho". CHỈ luồng xuất kho ghi mã này
# (`update_order_inline` khi chuyển sang "Đã xuất kho" — PATCH dropdown, quét kho,
# cron khớp mã chờ) nên đây là dấu hiệu tin cậy để chặn xuất trùng, kể cả khi sổ tồn
# trống. Un-dispatch đưa mã về "" / "warehouse_processing" nên vẫn cho quét lại.
DISPATCHED_DELIVERY_CODE = "dispatched"

# Mã trạng thái giao hàng nghĩa là đơn CHƯA rời kho → tồn chưa bị trừ. Un-dispatch đưa
# mã về đúng hai giá trị này, nên mọi mã còn lại (`dispatched`, `received`,
# `not_received`, `stalled_complaint`, `post_office_lost`) đều nằm SAU bước xuất kho và
# đồng nghĩa "tồn đã trừ ở kho xuất hiện tại".
PRE_DISPATCH_DELIVERY_CODES = frozenset({"", "warehouse_processing"})

POTENTIAL_LEVEL_LABELS = {
    1: "Rất thấp",
    2: "Thấp",
    3: "Trung bình",
    4: "Cao",
    5: "Rất cao",
}

NEED_GROUP_SEED = [
    {"code": "pain_relief", "name_vi": "Giảm đau nhức", "sort_order": 10},
    {"code": "relax_stress_relief", "name_vi": "Thư giãn giảm stress", "sort_order": 20},
    {"code": "rehab_support", "name_vi": "Hỗ trợ điều trị - phục hồi", "sort_order": 30},
    {"code": "buy_for_self", "name_vi": "Mua cho bản thân", "sort_order": 35},
    {"code": "gift", "name_vi": "Quà tặng", "sort_order": 40},
]

NEED_TYPE_SEED = [
    {
        "code": "neck_shoulder_nape",
        "group_code": "pain_relief",
        "name_vi": "Cổ - vai - gáy",
        "sort_order": 10,
    },
    {
        "code": "palm",
        "group_code": "pain_relief",
        "name_vi": "Bàn tay",
        "sort_order": 20,
    },
    {
        "code": "whole_arm",
        "group_code": "pain_relief",
        "name_vi": "Toàn tay",
        "sort_order": 30,
    },
    {
        "code": "shoulder_blade",
        "group_code": "pain_relief",
        "name_vi": "Đau bả vai",
        "sort_order": 35,
    },
    {
        "code": "whole_back",
        "group_code": "pain_relief",
        "name_vi": "Toàn lưng",
        "sort_order": 40,
    },
    {
        "code": "lower_back_waist",
        "group_code": "pain_relief",
        "name_vi": "Lưng dưới - thắt lưng",
        "sort_order": 50,
    },
    {
        "code": "numb_cold_limbs",
        "group_code": "pain_relief",
        "name_vi": "Tê bì chân tay - lạnh chân",
        "sort_order": 60,
    },
    {
        "code": "whole_leg_pain",
        "group_code": "pain_relief",
        "name_vi": "Đau toàn chân",
        "sort_order": 70,
    },
    {
        "code": "knee_pain",
        "group_code": "pain_relief",
        "name_vi": "Đau đầu gối",
        "sort_order": 80,
    },
    {
        "code": "calf_pain",
        "group_code": "pain_relief",
        "name_vi": "Đau bắp chân",
        "sort_order": 90,
    },
    {
        "code": "foot_pain",
        "group_code": "pain_relief",
        "name_vi": "Đau bàn chân",
        "sort_order": 100,
    },
    {
        "code": "muscle_pain_after_heavy_activity",
        "group_code": "pain_relief",
        "name_vi": "Đau cơ sau vận động nặng",
        "sort_order": 110,
    },
    {
        "code": "whole_body_pain",
        "group_code": "pain_relief",
        "name_vi": "Đau toàn thân",
        "sort_order": 115,
    },
    {
        "code": "relax_after_work",
        "group_code": "relax_stress_relief",
        "name_vi": "Thư giãn sau giờ làm",
        "sort_order": 10,
    },
    {
        "code": "poor_sleep_insomnia",
        "group_code": "relax_stress_relief",
        "name_vi": "Ngủ kém - khó ngủ",
        "sort_order": 20,
    },
    {
        "code": "work_stress",
        "group_code": "relax_stress_relief",
        "name_vi": "Căng thẳng - stress công việc",
        "sort_order": 30,
    },
    {
        "code": "sitting_too_much",
        "group_code": "relax_stress_relief",
        "name_vi": "Ngồi nhiều - ít vận động",
        "sort_order": 40,
    },
    {
        "code": "light_massage",
        "group_code": "relax_stress_relief",
        "name_vi": "Muốn massage nhẹ",
        "sort_order": 60,
    },
    {
        "code": "elderly_low_activity",
        "group_code": "rehab_support",
        "name_vi": "Người lớn tuổi ít vận động",
        "sort_order": 10,
    },
    {
        "code": "post_mild_stroke",
        "group_code": "rehab_support",
        "name_vi": "Người sau tai biến nhẹ",
        "sort_order": 20,
    },
    {
        "code": "post_injury",
        "group_code": "rehab_support",
        "name_vi": "Sau chấn thương",
        "sort_order": 30,
    },
    {
        "code": "self_use",
        "group_code": "buy_for_self",
        "name_vi": "Mua cho bản thân dùng",
        "sort_order": 10,
    },
    {
        "code": "gift_for_grandparents",
        "group_code": "gift",
        "name_vi": "Quà biếu ông bà",
        "sort_order": 10,
    },
    {
        "code": "gift_for_parents",
        "group_code": "gift",
        "name_vi": "Quà biếu bố mẹ",
        "sort_order": 15,
    },
    {
        "code": "gift_for_couple",
        "group_code": "gift",
        "name_vi": "Quà tặng vợ chồng",
        "sort_order": 20,
    },
    {
        "code": "gift_holiday_birthday",
        "group_code": "gift",
        "name_vi": "Quà tết - sinh nhật - lễ",
        "sort_order": 30,
    },
    {
        "code": "gift_for_teacher_boss",
        "group_code": "gift",
        "name_vi": "Quà cho thầy cô - sếp",
        "sort_order": 40,
    },
    {
        "code": "gift_for_customer",
        "group_code": "gift",
        "name_vi": "Quà cho khách hàng",
        "sort_order": 50,
    },
    {
        "code": "buy_for_family_shared_use",
        "group_code": "gift",
        "name_vi": "Mua để gia đình dùng chung",
        "sort_order": 60,
    },
]

# Bộ lý do bom 2 cấp (nhóm → loại), chốt 2026-10-01. Chi tiết cụ thể của từng đơn
# ghi vào `OrderAssessment.bomb_reason_text` (ô "Ghi chú chi tiết"), không thêm loại
# "Khác". Đổi/xoá mã phải kèm data migration remap (xem orders/0149) để báo cáo cũ
# không rơi vào "Chưa phân loại".
BOMB_REASON_GROUP_SEED = [
    {"code": "customer", "name_vi": "Khách hàng", "sort_order": 10},
    {"code": "shipping_unit", "name_vi": "DVVC / Giao hàng", "sort_order": 20},
    {"code": "order_processing", "name_vi": "Xử lý đơn (lỗi sale / vận đơn)", "sort_order": 30},
    {"code": "packaging", "name_vi": "Lỗi đóng gói", "sort_order": 40},
    {"code": "product_expectation", "name_vi": "Kỳ vọng khách hàng về sản phẩm", "sort_order": 50},
    {"code": "advertising_info", "name_vi": "Quảng cáo / Thông tin sản phẩm", "sort_order": 60},
    {"code": "product_quality", "name_vi": "Sản phẩm / Chất lượng", "sort_order": 70},
]

BOMB_REASON_TYPE_SEED = [
    {
        "code": "no_answer",
        "group_code": "customer",
        "name_vi": "Không nghe máy",
        "sort_order": 10,
    },
    {
        "code": "customer_away_business_trip",
        "group_code": "customer",
        "name_vi": "Khách đi công tác/về quê/đi viện/đi vắng",
        "sort_order": 20,
    },
    {
        "code": "customer_uncooperative",
        "group_code": "customer",
        "name_vi": "Khách không phối hợp nhận hàng",
        "sort_order": 30,
    },
    {
        "code": "changed_mind_not_buying",
        "group_code": "customer",
        "name_vi": "Khách đổi ý/không còn nhu cầu",
        "sort_order": 40,
    },
    {
        "code": "family_disallow_acceptance",
        "group_code": "customer",
        "name_vi": "Người nhà không đồng ý",
        "sort_order": 50,
    },
    {
        "code": "received_from_other_seller_first",
        "group_code": "customer",
        "name_vi": "Khách mua/nhận sản phẩm bên khác trước",
        "sort_order": 60,
    },
    {
        "code": "insufficient_cash_on_delivery",
        "group_code": "customer",
        "name_vi": "Không đủ tiền khi nhận",
        "sort_order": 70,
    },
    {
        "code": "cancelled_after_dispatch",
        "group_code": "customer",
        "name_vi": "Khách hủy đơn sau khi đã gửi hàng",
        "sort_order": 80,
    },
    {
        "code": "shipper_route_no_time_support",
        "group_code": "shipping_unit",
        "name_vi": "Shipper giao theo tuyến, không hỗ trợ giao giờ khách muốn nhận",
        "sort_order": 10,
    },
    {
        "code": "shipper_no_trial_allowed",
        "group_code": "shipping_unit",
        "name_vi": "Shipper không cho khách thử hàng/xem hàng",
        "sort_order": 20,
    },
    {
        "code": "shipper_auto_returned",
        "group_code": "shipping_unit",
        "name_vi": "Shipper không giao, tự ý tích hoàn",
        "sort_order": 30,
    },
    {
        "code": "hub_overload_not_delivered",
        "group_code": "shipping_unit",
        "name_vi": "Bưu cục quá tải không giao được hàng",
        "sort_order": 40,
    },
    {
        "code": "delivery_too_slow",
        "group_code": "shipping_unit",
        "name_vi": "Giao hàng chậm",
        "sort_order": 50,
    },
    {
        "code": "customer_shipper_conflict",
        "group_code": "shipping_unit",
        "name_vi": "Khách và shipper chửi nhau",
        "sort_order": 60,
    },
    {
        "code": "parcel_lost",
        "group_code": "shipping_unit",
        "name_vi": "Thất lạc hàng",
        "sort_order": 70,
    },
    {
        "code": "wrong_address",
        "group_code": "order_processing",
        "name_vi": "Sai địa chỉ",
        "sort_order": 10,
    },
    {
        "code": "wrong_product",
        "group_code": "order_processing",
        "name_vi": "Sai sản phẩm",
        "sort_order": 20,
    },
    {
        "code": "wrong_price",
        "group_code": "order_processing",
        "name_vi": "Sai giá",
        "sort_order": 30,
    },
    {
        "code": "duplicate_order",
        "group_code": "order_processing",
        "name_vi": "Trùng đơn",
        "sort_order": 40,
    },
    {
        "code": "cancelled_before_dispatch_still_sent",
        "group_code": "order_processing",
        "name_vi": "Khách báo hủy trước khi gửi nhưng vẫn bị gửi",
        "sort_order": 50,
    },
    {
        "code": "slow_order_processing",
        "group_code": "order_processing",
        "name_vi": "Chậm xử lý/gửi đơn",
        "sort_order": 60,
    },
    {
        "code": "underlying_condition",
        "group_code": "order_processing",
        "name_vi": "Khách có bệnh nền",
        "sort_order": 70,
    },
    {
        "code": "wrong_item_packed",
        "group_code": "packaging",
        "name_vi": "Đóng sai sản phẩm",
        "sort_order": 10,
    },
    {
        "code": "missing_gifts",
        "group_code": "packaging",
        "name_vi": "Thiếu quà tặng/Thẻ bảo hành",
        "sort_order": 20,
    },
    {
        "code": "missing_items",
        "group_code": "packaging",
        "name_vi": "Đóng thiếu hàng",
        "sort_order": 30,
    },
    {
        "code": "mixed_up_item_packed",
        "group_code": "packaging",
        "name_vi": "Đóng nhầm hàng",
        "sort_order": 40,
    },
    {
        "code": "used_product",
        "group_code": "packaging",
        "name_vi": "Hàng đã qua sử dụng (cũ)",
        "sort_order": 50,
    },
    {
        "code": "poor_packaging_damaged",
        "group_code": "packaging",
        "name_vi": "Đóng gói không đảm bảo",
        "sort_order": 60,
    },
    {
        "code": "product_not_suitable",
        "group_code": "product_expectation",
        "name_vi": "Không phù hợp nhu cầu",
        "sort_order": 10,
    },
    {
        "code": "complain_machine_weak",
        "group_code": "product_expectation",
        "name_vi": "Kỳ vọng máy mạnh hơn",
        "sort_order": 20,
    },
    {
        "code": "expected_other_features",
        "group_code": "product_expectation",
        "name_vi": "Kỳ vọng tính năng khác",
        "sort_order": 30,
    },
    {
        "code": "not_satisfied_after_inspection",
        "group_code": "product_expectation",
        "name_vi": "Không ưng khi kiểm hàng",
        "sort_order": 40,
    },
    {
        "code": "size_not_suitable",
        "group_code": "product_expectation",
        "name_vi": "Không phù hợp kích thước",
        "sort_order": 50,
    },
    {
        "code": "not_suitable_for_user",
        "group_code": "product_expectation",
        "name_vi": "Không phù hợp với người sử dụng",
        "sort_order": 60,
    },
    {
        "code": "origin_expectation",
        "group_code": "product_expectation",
        "name_vi": "Kỳ vọng về nguồn gốc/xuất xứ sản phẩm",
        "sort_order": 70,
    },
    {
        "code": "price_expectation",
        "group_code": "product_expectation",
        "name_vi": "Kỳ vọng về giá",
        "sort_order": 80,
    },
    {
        "code": "not_as_advertised",
        "group_code": "advertising_info",
        "name_vi": "Nội dung quảng cáo không đúng thực tế",
        "sort_order": 10,
    },
    {
        "code": "media_misleading",
        "group_code": "advertising_info",
        "name_vi": "Hình ảnh/video khiến khách hiểu khác về sản phẩm",
        "sort_order": 20,
    },
    {
        "code": "unclear_feature_description",
        "group_code": "advertising_info",
        "name_vi": "Mô tả tính năng chưa rõ",
        "sort_order": 30,
    },
    {
        "code": "overpromising_wording",
        "group_code": "advertising_info",
        "name_vi": "Cách diễn đạt tạo kỳ vọng cao",
        "sort_order": 40,
    },
    {
        "code": "defective_not_working",
        "group_code": "product_quality",
        "name_vi": "Sản phẩm lỗi/không hoạt động",
        "sort_order": 10,
    },
    {
        "code": "missing_accessories",
        "group_code": "product_quality",
        "name_vi": "Thiếu phụ kiện/sai phụ kiện",
        "sort_order": 20,
    },
    {
        "code": "damaged_appearance",
        "group_code": "product_quality",
        "name_vi": "Ngoại quan/kiện hàng có vấn đề",
        "sort_order": 30,
    },
    {
        "code": "abnormally_weak",
        "group_code": "product_quality",
        "name_vi": "Máy hoạt động yếu bất thường",
        "sort_order": 40,
    },
    {
        "code": "poor_experience_product_issue",
        "group_code": "product_quality",
        "name_vi": "Trải nghiệm thực tế không đạt do vấn đề sản phẩm",
        "sort_order": 50,
    },
    # Mã cũ còn trên dữ liệu lịch sử (không có loại tương đương trong bộ mới): giữ
    # trong whitelist để hiển thị/báo cáo đúng nhãn, `is_active=False` để UI ẩn khỏi
    # dropdown chọn mới.
    {
        "code": "other_reason",
        "group_code": "customer",
        "name_vi": "Lí do khác (cũ)",
        "sort_order": 990,
        "is_active": False,
    },
]

EVENT_TYPE_OPTIONS = [
    CatalogOption("assessment_created", "Tạo đánh giá CRM"),
    CatalogOption("customer_classification_changed", "Đổi phân loại khách"),
    CatalogOption("sales_status_changed", "Đổi trạng thái chốt sale"),
    CatalogOption("not_closed_reason_changed", "Đổi lý do không chốt"),
    CatalogOption("delivery_business_status_changed", "Đổi trạng thái giao hàng"),
    CatalogOption("potential_level_changed", "Đổi mức độ tiềm năng"),
    CatalogOption("assessment_note_updated", "Cập nhật ghi chú CRM"),
    # Đã ghi từ lâu nhưng thiếu nhãn → UI phải hiện mã raw.
    CatalogOption("sale_contact", "Ghi liên hệ"),
    CatalogOption("case_closed", "Đóng case"),
    CatalogOption("case_reopened", "Mở lại case"),
    # Vòng đời sale phụ trách + field nghiệp vụ ngoài khối CRM.
    CatalogOption("staff_assigned", "Chia đơn cho sale"),
    CatalogOption("staff_changed", "Đổi sale phụ trách"),
    CatalogOption("staff_ingest_designated", "Chỉ định sale từ đơn ingest"),
    CatalogOption("order_field_changed", "Sửa thông tin đơn"),
    CatalogOption("order_created", "Tạo đơn"),
    CatalogOption("order_items_changed", "Đổi sản phẩm / số lượng"),
    # Auto-fill từ transcript cuộc gọi — actor=None để timeline phân biệt với người.
    CatalogOption("assessment_ai_filled", "AI điền từ cuộc gọi"),
]


def _to_map(options: list[CatalogOption]) -> dict[str, str]:
    return {option.code: option.label for option in options}


CUSTOMER_CLASSIFICATION_LABELS = _to_map(CUSTOMER_CLASSIFICATION_OPTIONS)
SALES_STATUS_LABELS = _to_map(SALES_STATUS_OPTIONS)

# Nhóm trạng thái coi như "đã chốt". Nguồn gốc là Báo cáo Sale
# (`reporting/shared.py`) nhưng ĐẶT Ở ĐÂY vì tầng service cũng cần: luật ghi
# `Order.sales_closed_at` phải biết trạng thái nào là chốt, mà service không
# được import ngược vào tầng báo cáo. `reporting.shared` re-export lại tên này
# nên mọi đường import cũ vẫn sống.
CLOSED_SALES_STATUS_CODES = (
    "closed",
    "closed_express",
    "closed_scheduled_date",
    "created_order",
)
NOT_CLOSED_REASON_LABELS = _to_map(NOT_CLOSED_REASON_OPTIONS)
DELIVERY_BUSINESS_STATUS_LABELS = _to_map(DELIVERY_BUSINESS_STATUS_OPTIONS)
EVENT_TYPE_LABELS = _to_map(EVENT_TYPE_OPTIONS)
OCCUPATION_LABELS = _to_map(OCCUPATION_OPTIONS)
AGE_GROUP_LABELS = _to_map(AGE_GROUP_OPTIONS)
DELAX_SOURCE_LABELS = _to_map(DELAX_SOURCE_OPTIONS)
PAIN_LEVEL_LABELS = _to_map(PAIN_LEVEL_OPTIONS)
PAIN_DURATION_LABELS = _to_map(PAIN_DURATION_OPTIONS)
LIFESTYLE_HABIT_LABELS = _to_map(LIFESTYLE_HABIT_OPTIONS)
RELAX_TIME_LABELS = _to_map(RELAX_TIME_OPTIONS)
PURCHASE_BARRIER_LABELS = _to_map(PURCHASE_BARRIER_OPTIONS)
CALL_DURATION_LABELS = _to_map(CALL_DURATION_OPTIONS)
POST_PURCHASE_RATING_LABELS = _to_map(POST_PURCHASE_RATING_OPTIONS)
USED_MASSAGE_BEFORE_LABELS = _to_map(USED_MASSAGE_BEFORE_OPTIONS)
BOMB_REASON_GROUP_LABELS = {item["code"]: item["name_vi"] for item in BOMB_REASON_GROUP_SEED}
BOMB_REASON_TYPE_LABELS = {item["code"]: item["name_vi"] for item in BOMB_REASON_TYPE_SEED}
BOMB_REASON_TYPE_GROUP_MAP = {item["code"]: item["group_code"] for item in BOMB_REASON_TYPE_SEED}


CRM_CODE_LABELS = {
    "customer_classification_code": CUSTOMER_CLASSIFICATION_LABELS,
    "sales_status_code": SALES_STATUS_LABELS,
    "not_closed_reason_code": NOT_CLOSED_REASON_LABELS,
    "delivery_business_status_code": DELIVERY_BUSINESS_STATUS_LABELS,
    "occupation_code": OCCUPATION_LABELS,
    "age_text": AGE_GROUP_LABELS,
    "pain_level_code": PAIN_LEVEL_LABELS,
    "pain_duration_code": PAIN_DURATION_LABELS,
    "call_duration_code": CALL_DURATION_LABELS,
    "post_purchase_rating_code": POST_PURCHASE_RATING_LABELS,
}


def build_crm_catalog_payload() -> dict:
    from apps.orders.dynamic_assessment import get_active_defs

    return {
        "customer_classification_options": [
            {"code": option.code, "label": option.label} for option in CUSTOMER_CLASSIFICATION_OPTIONS
        ],
        "sales_status_options": [
            {"code": option.code, "label": option.label} for option in SALES_STATUS_OPTIONS
        ],
        "not_closed_reason_options": [
            {"code": option.code, "label": option.label} for option in NOT_CLOSED_REASON_OPTIONS
        ],
        "delivery_business_status_options": [
            {"code": option.code, "label": option.label} for option in DELIVERY_BUSINESS_STATUS_OPTIONS
        ],
        "potential_level_options": [
            {"value": value, "label": label} for value, label in POTENTIAL_LEVEL_LABELS.items()
        ],
        "event_type_options": [{"code": option.code, "label": option.label} for option in EVENT_TYPE_OPTIONS],
        "need_groups": NEED_GROUP_SEED,
        "need_types": NEED_TYPE_SEED,
        "bomb_reason_groups": BOMB_REASON_GROUP_SEED,
        "bomb_reason_types": BOMB_REASON_TYPE_SEED,
        "occupation_options": [{"code": o.code, "label": o.label} for o in OCCUPATION_OPTIONS],
        "age_group_options": [{"code": o.code, "label": o.label} for o in AGE_GROUP_OPTIONS],
        "delax_source_options": [{"code": o.code, "label": o.label} for o in DELAX_SOURCE_OPTIONS],
        "pain_level_options": [{"code": o.code, "label": o.label} for o in PAIN_LEVEL_OPTIONS],
        "pain_duration_options": [{"code": o.code, "label": o.label} for o in PAIN_DURATION_OPTIONS],
        "lifestyle_habit_options": [{"code": o.code, "label": o.label} for o in LIFESTYLE_HABIT_OPTIONS],
        "relax_time_options": [{"code": o.code, "label": o.label} for o in RELAX_TIME_OPTIONS],
        "purchase_barrier_options": [{"code": o.code, "label": o.label} for o in PURCHASE_BARRIER_OPTIONS],
        "call_duration_options": [{"code": o.code, "label": o.label} for o in CALL_DURATION_OPTIONS],
        "post_purchase_rating_options": [{"code": o.code, "label": o.label} for o in POST_PURCHASE_RATING_OPTIONS],
        "used_massage_before_options": [
            {"code": o.code, "label": o.label} for o in USED_MASSAGE_BEFORE_OPTIONS
        ],
        "dynamic_fields": [
            {
                "code": spec.code,
                "label": spec.label,
                "kind": spec.kind,
                "options": list(spec.options),
                "sort_order": spec.sort_order,
            }
            for spec in get_active_defs()
        ],
    }
