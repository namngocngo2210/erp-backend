"""Múi giờ dùng chung cho mọi matview trong schema `dwh`.

MỘT hằng số duy nhất — đừng gõ lại chuỗi tên timezone ở migration khác.
`0003_fct_order_enriched` (created_date) và `0004_customer_360`
(days_since_last_purchase) từng lệch nhau ('Asia/Ho_Chi_Minh' vs UTC ngầm định
của `now()::date` dưới USE_TZ=True), khiến một đơn mua sáng sớm giờ VN vừa là
"hôm nay" ở view này vừa là "hôm qua" ở view kia.

PHẢI khớp offset với `settings.TIME_ZONE` ("Asia/Bangkok", cùng +07). Hai tên
khác nhau, một offset — cố tình chọn 'Asia/Ho_Chi_Minh' vì đây là dữ liệu
khách hàng Việt Nam, không đổi theo `settings.TIME_ZONE` nếu sau này server
đổi tên tz nhưng vẫn +07.
"""

BUSINESS_TZ = "Asia/Ho_Chi_Minh"
