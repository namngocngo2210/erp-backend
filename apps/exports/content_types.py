"""Content-Type của file export — dùng chung giữa lúc UPLOAD lên MinIO và lúc
TRẢ file về trình duyệt.

Để hai nơi tự đoán riêng là mở đường cho lệch: object nằm trong MinIO mang một
ContentType, còn header lúc tải về lại mang cái khác (trước đây phía tải về phó
mặc cho ``mimetypes`` của hệ điều hành đoán từ tên file).
"""

import os

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv; charset=utf-8"
DEFAULT = "application/octet-stream"

CONTENT_TYPES = {"xlsx": XLSX, "csv": CSV}


def guess_content_type(file_name: str) -> str:
    # Không có đuôi thì coi là .xlsx — đúng quy ước của `_make_writer`: chỉ tên
    # file kết thúc bằng .csv mới đi đường CSV, còn lại đều là xlsx.
    suffix = (os.path.splitext(file_name)[1] or ".xlsx").lstrip(".").lower()
    return CONTENT_TYPES.get(suffix, DEFAULT)
