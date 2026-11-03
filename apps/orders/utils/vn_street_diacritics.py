"""Vietnamese street name diacritics correction.

Most Vietnamese streets are named after historical figures.
This module provides a lookup table to restore diacritics from
ASCII/incomplete input, e.g. "nguyen" -> "Nguyễn".
"""

import re
import unicodedata
from functools import lru_cache

# ── Single-word lookup: normalized (no diacritics, lowercase) -> correct form ──
# Only single words here. Compound phrases go in _COMPOUND_MAP below.
_SINGLE_WORD_MAP: dict[str, str] = {
    # ── Surnames ──
    "nguyen": "Nguyễn",
    "tran": "Trần",
    "le": "Lê",
    "pham": "Phạm",
    "huynh": "Huỳnh",
    "hoang": "Hoàng",
    "phan": "Phan",
    "vu": "Vũ",
    "vo": "Võ",
    "dang": "Đặng",
    "bui": "Bùi",
    "do": "Đỗ",
    "ho": "Hồ",
    "ngo": "Ngô",
    "duong": "Dương",
    "ly": "Lý",
    "ha": "Hà",
    "doan": "Đoàn",
    "luong": "Lương",
    "trinh": "Trịnh",
    "dao": "Đào",
    "mai": "Mai",
    "tang": "Tăng",
    "dinh": "Đinh",
    "truong": "Trương",
    "cao": "Cao",
    "lac": "Lạc",
    "chau": "Châu",
    "lam": "Lâm",
    "ta": "Tạ",
    "quach": "Quách",
    "bach": "Bạch",
    "nghiem": "Nghiêm",
    "ton": "Tôn",
    "phung": "Phùng",
    "kieu": "Kiều",
    "to": "Tô",
    "chu": "Chu",
    # ── Given/middle names ──
    "van": "Văn",
    "thi": "Thị",
    "huu": "Hữu",
    "duc": "Đức",
    "thanh": "Thanh",
    "minh": "Minh",
    "quoc": "Quốc",
    "anh": "Anh",
    "trung": "Trung",
    "hong": "Hồng",
    "xuan": "Xuân",
    "huy": "Huy",
    "quang": "Quang",
    "tai": "Tài",
    "trong": "Trọng",
    "ngoc": "Ngọc",
    "hung": "Hưng",
    "tu": "Tự",
    "khac": "Khắc",
    "duy": "Duy",
    "cong": "Công",
    "nhat": "Nhật",
    "chi": "Chí",
    "si": "Sĩ",
    "tien": "Tiến",
    "tan": "Tân",
    "phuc": "Phúc",
    "binh": "Bình",
    "son": "Sơn",
    "hai": "Hải",
    "long": "Long",
    "hoa": "Hòa",
    "an": "An",
    "thinh": "Thịnh",
    "truc": "Trực",
    "khanh": "Khánh",
    "loi": "Lợi",
    "hien": "Hiền",
    "canh": "Cảnh",
    "nhan": "Nhân",
    "tri": "Trí",
    "tuong": "Tường",
    "dat": "Đạt",
    "toan": "Toàn",
    "loc": "Lộc",
    "phu": "Phú",
    "vinh": "Vinh",
    "dien": "Diên",
    "phong": "Phong",
    "cuong": "Cường",
    "quyen": "Quyền",
    "danh": "Danh",
    "nghia": "Nghĩa",
    "tuan": "Tuấn",
    "dung": "Dũng",
    "lien": "Liên",
    "tam": "Tâm",
    "trieu": "Triệu",
    "thang": "Thắng",
    "hoan": "Hoàn",
    "kiem": "Kiếm",
    "khoi": "Khởi",
    "thiep": "Thiệp",
    "tuyen": "Tuyên",
    "kiet": "Kiệt",
    "khiem": "Khiêm",
    "luan": "Luận",
    "linh": "Linh",
    "chieu": "Chiểu",
    "trach": "Trạch",
    "cau": "Cầu",
    "trac": "Trắc",
    "nhi": "Nhị",
    "vuong": "Vương",
    "diep": "Diệp",
    "dong": "Đông",
    "tay": "Tây",
    "nam": "Nam",
    "bac": "Bắc",
    "hue": "Huệ",
    "tat": "Tất",
    "thuyet": "Thuyết",
    "khuyen": "Khuyến",
    "sieu": "Siêu",
    "tong": "Tông",
    "lai": "Lai",
    "chan": "Chân",
    "than": "Thân",
    "trai": "Trãi",
    "noi": "Nội",
    "hau": "Hậu",
    "moi": "Mới",
    "viet": "Việt",
    "bang": "Bằng",
    "giap": "Giáp",
    "sau": "Sáu",
    "phong": "Phong",
    "tho": "Thọ",
    "hung": "Hùng",
    # ── Street-related terms ──
    "duong": "Đường",
    "pho": "Phố",
    "ngo": "Ngõ",
    "ngach": "Ngách",
    "hem": "Hẻm",
    "so": "Số",
    "khu": "Khu",
    "ap": "Ấp",
    "thon": "Thôn",
    "xom": "Xóm",
    "doi": "Đội",
    # ── Administrative unit prefixes ──
    "phuong": "Phường",
    "xa": "Xã",
    "quan": "Quận",
    "huyen": "Huyện",
    "tinh": "Tỉnh",
    "tp": "TP",
    "pasteur": "Pasteur",
}

# ── Compound phrases (2+ words) ──
# These take priority over single-word lookup.
_COMPOUND_MAP: dict[str, str] = {
    # Administrative
    "thanh pho": "Thành Phố",
    "thi xa": "Thị Xã",
    "thi tran": "Thị Trấn",
    # Famous events/places
    "dien bien phu": "Điện Biên Phủ",
    "cach mang thang tam": "Cách Mạng Tháng Tám",
    "cach mang thang 8": "Cách Mạng Tháng 8",
    "ba thang hai": "Ba Tháng Hai",
    "ba thang 2": "Ba Tháng 2",
    "hai ba trung": "Hai Bà Trưng",
    "hung vuong": "Hùng Vương",
    "quang trung": "Quang Trung",
    "nam ky khoi nghia": "Nam Kỳ Khởi Nghĩa",
    "bach dang": "Bạch Đằng",
    "cong hoa": "Cộng Hòa",
    "dong khoi": "Đồng Khởi",
    "dien bien": "Điện Biên",
    "giai phong": "Giải Phóng",
    "dong da": "Đống Đa",
    "trang tien": "Tràng Tiền",
    "trang an": "Tràng An",
    # Nguyễn
    "nguyen trai": "Nguyễn Trãi",
    "nguyen hue": "Nguyễn Huệ",
    "nguyen du": "Nguyễn Du",
    "nguyen dinh chieu": "Nguyễn Đình Chiểu",
    "nguyen chi thanh": "Nguyễn Chí Thanh",
    "nguyen luong bang": "Nguyễn Lương Bằng",
    "nguyen van troi": "Nguyễn Văn Trỗi",
    "nguyen thi minh khai": "Nguyễn Thị Minh Khai",
    "nguyen van nghi": "Nguyễn Văn Nghi",
    "nguyen oanh": "Nguyễn Oanh",
    "nguyen thai hoc": "Nguyễn Thái Học",
    "nguyen khuyen": "Nguyễn Khuyến",
    "nguyen cong tru": "Nguyễn Công Trứ",
    "nguyen sieu": "Nguyễn Siêu",
    "nguyen tri phuong": "Nguyễn Tri Phương",
    "nguyen thi dinh": "Nguyễn Thị Định",
    "nguyen van dau": "Nguyễn Văn Đậu",
    "nguyen tat thanh": "Nguyễn Tất Thành",
    "nguyen binh khiem": "Nguyễn Bỉnh Khiêm",
    "nguyen an ninh": "Nguyễn An Ninh",
    "nguyen trong tuyen": "Nguyễn Trọng Tuyển",
    "nguyen dinh thi": "Nguyễn Đình Thi",
    "nguyen van huyen": "Nguyễn Văn Huyên",
    "nguyen phuc nguyen": "Nguyễn Phúc Nguyên",
    "nguyen canh chan": "Nguyễn Cảnh Chân",
    "nguyen van cu": "Nguyễn Văn Cừ",
    "nguyen van linh": "Nguyễn Văn Linh",
    "nguyen van bao": "Nguyễn Văn Bảo",
    "nguyen kiem": "Nguyễn Kiệm",
    "nguyen huu tho": "Nguyễn Hữu Thọ",
    "nguyen co thach": "Nguyễn Cơ Thạch",
    "nguyen anh thu": "Nguyễn Ảnh Thủ",
    "nguyen huu canh": "Nguyễn Hữu Cảnh",
    "nguyen thuong hien": "Nguyễn Thượng Hiền",
    "nguyen thai binh": "Nguyễn Thái Bình",
    "nguyen phuc lai": "Nguyễn Phúc Lai",
    "nguyen tuan": "Nguyễn Tuân",
    "nguyen trong tuyen": "Nguyễn Trọng Tuyển",
    "nguyen duc canh": "Nguyễn Đức Cảnh",
    # Hồ
    "ho chi minh": "Hồ Chí Minh",
    "ho xuan huong": "Hồ Xuân Hương",
    "ho tung mau": "Hồ Tùng Mậu",
    # Võ
    "vo thi sau": "Võ Thị Sáu",
    "vo van tan": "Võ Văn Tần",
    "vo van kiet": "Võ Văn Kiệt",
    "vo nguyen giap": "Võ Nguyên Giáp",
    # Trần
    "tran hung dao": "Trần Hưng Đạo",
    "tran quoc toan": "Trần Quốc Toản",
    "tran nhan tong": "Trần Nhân Tông",
    "tran binh trong": "Trần Bình Trọng",
    "tran dai nghia": "Trần Đại Nghĩa",
    "tran quang khai": "Trần Quang Khải",
    "tran nhat duat": "Trần Nhật Duật",
    "tran cao van": "Trần Cao Vân",
    "tran quoc thao": "Trần Quốc Thảo",
    "tran dinh xu": "Trần Đình Xu",
    "tran duy hung": "Trần Duy Hưng",
    "tran khanh du": "Trần Khánh Dư",
    "tran khat chan": "Trần Khát Chân",
    "tran phu": "Trần Phú",
    "tran dang ninh": "Trần Đăng Ninh",
    # Lê
    "le hong phong": "Lê Hồng Phong",
    "le van sy": "Lê Văn Sỹ",
    "le van luong": "Lê Văn Lương",
    "le van viet": "Lê Văn Việt",
    "le van hien": "Lê Văn Hiến",
    "le van tam": "Lê Văn Tám",
    "le van khuong": "Lê Văn Khương",
    "le duan": "Lê Duẩn",
    "le loi": "Lê Lợi",
    "le lai": "Lê Lai",
    "le quy don": "Lê Quý Đôn",
    "le trong tan": "Lê Trọng Tấn",
    "le dai hanh": "Lê Đại Hành",
    "le thanh nghi": "Lê Thanh Nghị",
    "le thanh ton": "Lê Thánh Tông",
    "le duc tho": "Lê Đức Thọ",
    # Phan
    "phan dinh phung": "Phan Đình Phùng",
    "phan boi chau": "Phan Bội Châu",
    "phan dang luu": "Phan Đăng Lưu",
    "phan chu trinh": "Phan Chu Trinh",
    "phan van tri": "Phan Văn Trị",
    "phan xich long": "Phan Xích Long",
    "phan ke binh": "Phan Kế Bính",
    "phan huy ich": "Phan Huy Ích",
    "phan van han": "Phan Văn Hân",
    # Hoàng
    "hoang dieu": "Hoàng Diệu",
    "hoang hoa tham": "Hoàng Hoa Thám",
    "hoang van thu": "Hoàng Văn Thụ",
    "hoang quoc viet": "Hoàng Quốc Việt",
    "hoang sa": "Hoàng Sa",
    "hoang ngan": "Hoàng Ngân",
    # Nguyên Hồng (nhà văn) - "nguyen" here is Nguyên, not Nguyễn
    "nguyen hong": "Nguyên Hồng",
    # Trương/Trường
    "truong sa": "Trường Sa",
    "truong dinh": "Trương Định",
    "truong chinh": "Trường Chinh",
    "truong cong dinh": "Trương Công Định",
    # Phạm
    "pham van dong": "Phạm Văn Đồng",
    "pham hung": "Phạm Hùng",
    "pham ngu lao": "Phạm Ngũ Lão",
    "pham ngoc thach": "Phạm Ngọc Thạch",
    "pham viet chanh": "Phạm Viết Chánh",
    "pham the hien": "Phạm Thế Hiển",
    "pham van bach": "Phạm Văn Bạch",
    # Tôn
    "ton duc thang": "Tôn Đức Thắng",
    "ton that tung": "Tôn Thất Tùng",
    "ton that thuyet": "Tôn Thất Thuyết",
    "ton that dam": "Tôn Thất Đạm",
    # Lý
    "ly thuong kiet": "Lý Thường Kiệt",
    "ly thai to": "Lý Thái Tổ",
    "ly nam de": "Lý Nam Đế",
    "ly tu trong": "Lý Tự Trọng",
    "ly chinh thang": "Lý Chính Thắng",
    # Bùi
    "bui thi xuan": "Bùi Thị Xuân",
    # Đặng
    "dang van ngu": "Đặng Văn Ngữ",
    "dang thai mai": "Đặng Thai Mai",
    "dang tien dong": "Đặng Tiến Đông",
    # Đào
    "dao duy tu": "Đào Duy Từ",
    "dao duy anh": "Đào Duy Anh",
    "dao tri uc": "Đào Trí Úc",
    # Ngô
    "ngo quyen": "Ngô Quyền",
    "ngo gia tu": "Ngô Gia Tự",
    "ngo duc ke": "Ngô Đức Kế",
    # Đỗ
    "do xuan hop": "Đỗ Xuân Hợp",
    "do duc duc": "Đỗ Đức Dục",
    "tran duc duc": "Trần Đức Dục",
    "do quang dau": "Đỗ Quang Đẩu",
    # Huỳnh
    "huynh tan phat": "Huỳnh Tấn Phát",
    # Other names
    "mac dinh chi": "Mạc Đĩnh Chi",
    "su van hanh": "Sư Vạn Hạnh",
    "lac long quan": "Lạc Long Quân",
    "au co": "Âu Cơ",
    "ba huyen thanh quan": "Bà Huyện Thanh Quan",
    "doi can": "Đội Cấn",
    "de la thanh": "Đê La Thành",
    "kim ma": "Kim Mã",
    "lang ha": "Láng Hạ",
    "thai ha": "Thái Hà",
    "thai thinh": "Thái Thịnh",
    "chu van an": "Chu Văn An",
    "dinh tien hoang": "Đinh Tiên Hoàng",
    "doan tran nghiep": "Đoàn Trần Nghiệp",
    "phu dong thien vuong": "Phù Đổng Thiên Vương",
    "yet kieu": "Yết Kiêu",
    "ham nghi": "Hàm Nghi",
    "kham thien": "Khâm Thiên",
    "cat linh": "Cát Linh",
    "xa dan": "Xã Đàn",
    "man thien": "Mân Thiện",
    "dong van cong": "Đồng Văn Cống",
    "mai chi tho": "Mai Chí Thọ",
    "ha huy giap": "Hà Huy Giáp",
    "to hieu": "Tô Hiệu",
    "to vinh dien": "Tô Vĩnh Diện",
    "duong dinh nghe": "Dương Đình Nghệ",
    "trung kinh": "Trung Kính",
    "trung hoa": "Trung Hòa",
    "vu trong phung": "Vũ Trọng Phụng",
    "vu ngoc phan": "Vũ Ngọc Phan",
    "vu trong khanh": "Vũ Trọng Khánh",
    "hong phong": "Hồng Phong",
    "tay ho": "Tây Hồ",
    "tay son": "Tây Sơn",
    "xuan thuy": "Xuân Thủy",
    "cau giay": "Cầu Giấy",
    "co nhue": "Cổ Nhuế",
    "yen bai": "Yên Bái",
    # Hàng streets
    "hang bai": "Hàng Bài",
    "hang khay": "Hàng Khay",
    "hang trong": "Hàng Trống",
    "hang dao": "Hàng Đào",
    "hang bong": "Hàng Bông",
    "hang gai": "Hàng Gai",
    "hang ma": "Hàng Mã",
    "hang buom": "Hàng Buồm",
    "hang ngang": "Hàng Ngang",
    "hang duong": "Hàng Đường",
    "hang bac": "Hàng Bạc",
    "hang bo": "Hàng Bồ",
    "hang can": "Hàng Cân",
    "hang chieu": "Hàng Chiếu",
    "hang cot": "Hàng Cót",
    "hang da": "Hàng Da",
    "hang dau": "Hàng Dầu",
    "hang dieu": "Hàng Điếu",
    "hang giay": "Hàng Giấy",
    "hang hom": "Hàng Hòm",
    "hang khoai": "Hàng Khoai",
    "hang muoi": "Hàng Muối",
    "hang non": "Hàng Nón",
    "hang phen": "Hàng Phèn",
    "hang thiec": "Hàng Thiếc",
    "hang tre": "Hàng Tre",
    "hang vai": "Hàng Vải",
}

# Merge into single lookup (compounds override singles for matching)
_WORD_MAP: dict[str, str] = {**_SINGLE_WORD_MAP, **_COMPOUND_MAP}


def _strip_diacritics(text: str) -> str:
    """Remove Vietnamese diacritics and lowercase."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_text.lower().replace("đ", "d").replace("Đ", "d")


def _has_vietnamese_diacritics(word: str) -> bool:
    """Check if a word already contains Vietnamese diacritics."""
    stripped = _strip_diacritics(word)
    return stripped != word.lower()


@lru_cache(maxsize=1)
def _build_compound_lookup() -> list[tuple[str, str]]:
    """Build sorted compound phrase lookup (longest first, multi-word only)."""
    compounds = list(_COMPOUND_MAP.items())
    compounds.sort(key=lambda x: len(x[0]), reverse=True)
    return compounds


def _capitalize_word(word: str) -> str:
    """Capitalize first letter of a word, preserving the rest."""
    if not word or not word[0].isalpha():
        return word
    return word[0].upper() + word[1:]


def restore_diacritics(text: str) -> str:
    """Restore Vietnamese diacritics for a street address string.

    Strategy:
    1. Split text into words
    2. Try compound phrase matching (longest first) on consecutive words
    3. Fall back to single-word lookup
    4. Preserve words that already have diacritics
    5. Capitalize remaining words
    """
    if not text or not text.strip():
        return text

    # Split into words (preserving separators)
    words: list[str] = []
    for part in re.split(r'(\s+)', text):
        if not part:
            continue
        # Further split by comma but keep the comma
        sub_parts = re.split(r'(,)', part)
        words.extend(p for p in sub_parts if p)

    # Build normalized word list
    norm_words: list[str] = []
    for w in words:
        if w.isspace() or w == ',':
            norm_words.append(w)
        else:
            norm_words.append(_strip_diacritics(w))

    result: list[str] = []
    used: set[int] = set()

    i = 0
    while i < len(words):
        if i in used:
            i += 1
            continue

        w = words[i]

        # Skip whitespace and punctuation
        if w.isspace() or w == ',':
            result.append(w)
            i += 1
            continue

        # Collect upcoming content word indices for compound matching
        content_indices: list[int] = []
        j = i
        while j < len(words) and len(content_indices) < 6:
            if not words[j].isspace() and words[j] != ',':
                content_indices.append(j)
            j += 1

        # Try compound matching (longest first)
        matched_compound = False
        for length in range(len(content_indices), 1, -1):
            selected = content_indices[:length]
            compound_norm = " ".join(norm_words[idx] for idx in selected)
            if compound_norm in _COMPOUND_MAP:
                # Emit any whitespace between i and first selected
                for k in range(i, selected[0]):
                    if k not in used:
                        result.append(words[k])
                        used.add(k)
                result.append(_COMPOUND_MAP[compound_norm])
                for k in range(selected[0], selected[-1] + 1):
                    used.add(k)
                i = selected[-1] + 1
                matched_compound = True
                break

        if matched_compound:
            continue

        # Single word lookup
        norm_w = norm_words[i]
        if _has_vietnamese_diacritics(w):
            result.append(_capitalize_word(w))
        elif norm_w in _SINGLE_WORD_MAP:
            result.append(_SINGLE_WORD_MAP[norm_w])
        else:
            result.append(_capitalize_word(w))

        used.add(i)
        i += 1

    return "".join(result)
