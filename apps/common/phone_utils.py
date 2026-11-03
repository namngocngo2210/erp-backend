import re


def normalize_phone(phone: str) -> str:
    """Chuẩn hóa số điện thoại VN về dạng nội bộ bắt đầu bằng 0."""
    if not phone:
        return ""

    raw = str(phone).strip()
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""

    if digits.startswith("0084"):
        digits = digits[4:]
    elif digits.startswith("84") and len(digits) >= 11:
        # Only treat leading "84" as country code when total length matches a
        # full international number ("84" + 9 or 10 digit local). Otherwise a
        # domestic mobile starting with prefix "084" entered without the leading
        # zero (e.g. "846123456") would be wrongly stripped.
        digits = digits[2:]

    if digits.startswith("0"):
        normalized = digits
    elif len(digits) == 9:
        normalized = f"0{digits}"
    elif len(digits) == 10 and digits.startswith("2"):
        normalized = f"0{digits}"
    else:
        normalized = digits

    if not re.fullmatch(r"0\d{9,10}", normalized):
        return ""
    return normalized


def phone_search_candidates(text: str) -> list[str]:
    """Các chuỗi CHỮ SỐ để tìm SĐT bằng ``phone_normalized__contains``.

    Vì sao cần đủ 2 dạng: cột ``phone_normalized`` luôn ở dạng nội bộ ``0…`` (do
    ``Order.save()`` derive), trong khi ô tìm kiếm nhận mọi kiểu người dùng gõ:
    - gõ ĐẦY ĐỦ dạng quốc tế ``+84 901 234 567`` → chỉ bản canonical ``0901234567``
      mới khớp được, nên phải thêm ``normalize_phone``;
    - gõ MỘT ĐOẠN ``1234`` → ``normalize_phone`` trả "" (không đủ 10-11 số) nên phải
      giữ luôn chuỗi chữ số thô thì tìm theo đuôi số mới ra.

    Bỏ chuỗi < 3 ký tự: trigram GIN không phục vụ được ``LIKE '%ab%'`` 1-2 ký tự →
    nhánh đó sẽ seq-scan toàn bảng mà kết quả thì rộng vô nghĩa.

    Từ khóa CÓ CHỮ CÁI thì không phải SĐT (là mã đơn/tên) → trả rỗng. Nếu không, gõ
    ``HD1234567`` sẽ rút ra "1234567" và kéo về mọi đơn có SĐT chứa dãy số đó — đúng lỗi
    mà `_apply_list_filters` đã chặn bằng cùng quy tắc.
    """
    if re.search(r"[A-Za-z]", str(text or "")):
        return []
    digits = re.sub(r"\D+", "", str(text or ""))
    canonical = normalize_phone(text)
    return [value for value in dict.fromkeys((digits, canonical)) if len(value) >= 3]


def phone_lookup_candidates(phone: str) -> list[str]:
    """
    Trả về các biến thể số điện thoại để tìm kiếm tương thích dữ liệu cũ.
    Ưu tiên định dạng mới (0xxxxxxxxx), fallback thêm +84xxxxxxxxx.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return []

    candidates = [normalized]
    if normalized.startswith("0") and len(normalized) >= 2:
        legacy_plus84 = f"+84{normalized[1:]}"
        if legacy_plus84 not in candidates:
            candidates.append(legacy_plus84)
    return candidates
