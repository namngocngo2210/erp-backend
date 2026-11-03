"""Cấu hình OpenAPI RIÊNG cho file tĩnh gửi trợ lý (drf-spectacular --custom-settings).

Sinh: python manage.py spectacular --urlconf apps.daily_report.urls
        --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS
        --file ../docs/openapi/daily-report.yaml
"""

SPECTACULAR_SETTINGS = {
    "TITLE": "CRM Daily Report API",
    "DESCRIPTION": (
        "API chỉ đọc cho trợ lý báo cáo ngày. Xác thực bằng header X-Api-Token. "
        "Từ 16/09/2026 mọi endpoint trả TỪNG DÒNG (đơn, dòng hàng, lead mở, chi tiêu ads) với "
        "phân trang keyset `cursor`/`limit`, không còn số gộp và không còn `group_by` — bên gọi "
        "tự đếm/cộng theo `bucket`. Ngày theo múi giờ Asia/Ho_Chi_Minh; tiền là số nguyên VND; "
        "mọi response có meta.data_as_of là mốc dữ liệu (mart cập nhật mỗi 30 phút). "
        "Cách tính lại từng chỉ số cũ: GET /definitions → metrics."
    ),
    "VERSION": "2.0.0",
    "SERVERS": [
        {
            "url": "{base}/api/daily-report",
            "variables": {"base": {"default": "https://crm.example.vn", "description": "Domain CRM"}},
        }
    ],
    "SECURITY": [{"ApiKeyAuth": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Token"},
        }
    },
    "PREPROCESSING_HOOKS": [],
    "SCHEMA_PATH_PREFIX": "",
}
