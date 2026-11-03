"""Nhóm mã TĨNH cho mart hội thoại chat — không nhóm nào lấy được từ crm_catalog
hay enum model, nên khai ở đây và đây là nguồn sự thật duy nhất.

nhu_cau / nhom_nhu_cau KHÔNG nằm ở đây: chúng có bảng thật (need_types,
need_groups) và phải bám theo bảng — xem need_dictionary.py.
"""

# Giá trị mã = ĐÚNG chuỗi thô của nguồn (pages.platform, conversations.type,
# messages.direction) để ai.nhan() tra thẳng, không cần bảng ánh xạ trung gian.
NEN_TANG_CHAT = [
    ("facebook", "Facebook"),
    ("zalo", "Zalo OA"),
    ("personal_zalo", "Zalo cá nhân"),
    ("tiktok_shop", "TikTok Shop"),
    ("shopee_official", "Shopee"),
    ("youtube", "YouTube"),
]

LOAI_TIN_CHAT = [
    ("INBOX", "Tin nhắn"),
    ("COMMENT", "Bình luận"),
    ("RATING", "Đánh giá"),
]

CHIEU_TIN_CHAT = [
    ("in", "Khách gửi"),
    ("out", "Page gửi"),
]

# Giai đoạn hội thoại — danh mục ĐÓNG, LLM chỉ được chọn trong đây (spec §3.1).
GIAI_DOAN_CHAT = [
    ("hoi_gia", "Hỏi giá"),
    ("hoi_thong_tin", "Hỏi thông tin sản phẩm"),
    ("dang_can_nhac", "Đang cân nhắc"),
    ("tu_choi", "Từ chối"),
    ("da_chot", "Đã chốt"),
    ("sau_ban", "Sau bán / bảo hành"),
    ("khong_xac_dinh", "Không xác định"),
]

MUC_DO_QUAN_TAM = [
    ("cao", "Cao"),
    ("trung_binh", "Trung bình"),
    ("thap", "Thấp"),
]

# Trạng thái hàng đợi gán nhãn (ai_stg.hoi_thoai_nhan.extract_status).
# "chua_xep_hang" = chưa có dòng nào trong hàng đợi — KHÁC "pending" (đã xếp
# hàng, đang chờ tới lượt). Không có mã này thì trang_thai_gan_nhan_text NULL
# và người đọc không phân biệt được hai tình huống.
TRANG_THAI_GAN_NHAN = [
    ("chua_xep_hang", "Chưa xếp hàng"),
    ("pending", "Chờ gán nhãn"),
    ("processing", "Đang gán nhãn"),
    ("done", "Đã gán nhãn"),
    ("failed", "Gán nhãn lỗi"),
    ("skipped", "Bỏ qua (quá ngắn)"),
]

CHAT_DICTIONARY: dict[str, list[tuple[str, str]]] = {
    "nen_tang_chat": NEN_TANG_CHAT,
    "loai_tin_chat": LOAI_TIN_CHAT,
    "chieu_tin_chat": CHIEU_TIN_CHAT,
    "giai_doan_chat": GIAI_DOAN_CHAT,
    "muc_do_quan_tam": MUC_DO_QUAN_TAM,
    "trang_thai_gan_nhan": TRANG_THAI_GAN_NHAN,
}
