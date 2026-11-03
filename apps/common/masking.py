"""Che một phần chuỗi nhạy cảm (SĐT,...) — giữ N ký tự cuối để vẫn đối soát được.

Khác với redact toàn phần (``***REDACTED***``): một số trường (vd SĐT khách hàng)
vẫn cần xuất hiện một phần trong log/API để nhân sự đối chiếu, chỉ che phần định
danh. Dùng chung giữa audit middleware và các endpoint trả SĐT rút gọn.
"""


# Mã quyền RBAC "xem đầy đủ SĐT khách". Định nghĩa ở apps.common (tầng ĐÁY) vì ba
# nơi cùng cần: resolver quyền (apps/users/phone_visibility.py), các app nghiệp vụ, và
# consumer WebSocket của channels_core — consumer KHÔNG được import
# apps.users.phone_visibility theo contract phân tầng (backend/.importlinter chỉ miễn
# trừ 3 module users mà nó đã dùng). Dùng qua apps.users.phone_visibility ở nơi nào
# import được.
CUSTOMER_PHONE_PERMISSION = "customer-phone:view"


def mask_keep_last(value: str | int | None, *, keep: int = 3) -> str:
    """Che `value` bằng '*', chừa lại `keep` ký tự cuối cùng.

    Chuỗi rỗng trả nguyên; chuỗi ngắn hơn hoặc bằng `keep` bị che toàn bộ (không đủ
    dài để giữ phần "vô hại" mà không lộ hết).
    """
    text = str(value or "")
    if not text:
        return text
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def mask_phone_middle(value: str | int | None, *, keep_prefix: int = 3, keep_suffix: int = 3) -> str:
    """Che GIỮA SĐT, chừa `keep_prefix` số đầu + `keep_suffix` số cuối: 0912345678 -> 091****678.

    Khác `mask_keep_last` (chỉ chừa đuôi): giữ cả đầu số để người xem vẫn nhận ra
    nhà mạng / đối soát được đơn, nhưng không đủ để gọi hay mang dữ liệu đi. Độ dài
    chuỗi được giữ nguyên (số ký tự '*' = phần bị che) nên cột SĐT không bị co giãn.

    Chuỗi rỗng trả nguyên; chuỗi ngắn hơn hoặc bằng `keep_prefix + keep_suffix` bị
    che toàn bộ (không đủ dài để chừa hai đầu mà không lộ hết).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "*" in text:
        # Đã bị che sẵn từ nguồn (vd TikTok Shop trả "(+84)037*****54") — che tiếp chỉ
        # ăn mất mấy chữ số còn lại mà không giấu thêm được gì.
        return text
    if len(text) <= keep_prefix + keep_suffix:
        return "*" * len(text)
    return text[:keep_prefix] + "*" * (len(text) - keep_prefix - keep_suffix) + text[-keep_suffix:]


def is_masked_phone(value: str | None) -> bool:
    """True nếu chuỗi là SĐT ĐÃ BỊ CHE (do chính API trả ra) chứ không phải số thật.

    Dùng ở luồng GHI: client hiển thị số đã che rồi gửi nguyên chuỗi đó lên trong
    payload sửa đơn — ghi đè là hỏng dữ liệu gốc. Xem `strip_masked_phone`.
    """
    return "*" in str(value or "")
