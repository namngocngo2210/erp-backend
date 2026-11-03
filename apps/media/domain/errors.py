"""Dịch mã lỗi ads-crm (§5 tài liệu) sang thông báo tiếng Việt cho toast frontend."""

from __future__ import annotations

UPSTREAM_MESSAGES: dict[str, str] = {
    "invalid_json": "Dữ liệu gửi lên không đúng định dạng JSON.",
    "missing_required_fields": "Thiếu trường bắt buộc.",
    "invalid_uploaded_at": "Ngày upload không hợp lệ.",
    "invalid_due_date": "Hạn hoàn thành không hợp lệ.",
    "invalid_expires_at": "Ngày hết hạn không hợp lệ.",
    "invalid_url": "URL không hợp lệ (cần scheme + host).",
    "invalid_rating": "Điểm đánh giá phải từ 0 đến 5, bước 0.5.",
    "invalid_evaluation": "Đánh giá trống: cần điểm hoặc ghi chú.",
    "invalid_status": "Trạng thái không hợp lệ.",
    "invalid_role": "Vai trò không hợp lệ.",
    "invalid_content_type": "Loại nội dung không hợp lệ.",
    "invalid_product": "Sản phẩm không hợp lệ hoặc đã tắt.",
    "invalid_category": "Danh mục không hợp lệ.",
    "invalid_channel": "Kênh ads không hợp lệ.",
    "invalid_source": "Nguồn không hợp lệ.",
    "product_not_found": "Sản phẩm không tồn tại.",
    "category_not_found": "Danh mục không tồn tại.",
    "duplicate_record": "Bản ghi đã tồn tại (trùng tên/mã).",
    "forbidden": "Tài khoản ads-crm không đủ quyền cho thao tác này.",
    "not_content_owner": "Bạn không phải người tạo nội dung này.",
    "not_proposal_owner": "Bạn không phải người tạo đề xuất này.",
    "content_not_found": "Nội dung không tồn tại hoặc đã xoá.",
    "evaluation_not_found": "Đánh giá không tồn tại.",
    "proposal_not_found": "Đề xuất không tồn tại.",
    "taxonomy_not_found": "Bản ghi danh mục không tồn tại.",
    "photo_post": "Bài dạng ảnh, không có video để tải.",
    "rate_limited": "Hệ thống Media đang giới hạn tốc độ, thử lại sau.",
    "download_failed": "Không tải được video từ nguồn.",
    "busy": "Nguồn tải đang bận, thử lại sau vài giây.",
    "video_cache_not_configured": "ads-crm chưa cấu hình kho cache video.",
    "video_queue_full": "Hàng đợi cache video đang đầy, thử lại sau.",
    "query_failed": "Lỗi phía ads-crm (query_failed).",
    "internal_error": "Lỗi phía ads-crm (internal_error).",
}


def upstream_detail(code: str) -> str:
    return UPSTREAM_MESSAGES.get(code, code)
