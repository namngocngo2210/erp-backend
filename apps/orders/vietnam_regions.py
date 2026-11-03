"""Map tỉnh/thành → vùng miền cho Báo cáo tổng.

Nguồn sự thật DUY NHẤT của việc gom vùng: cả tên vùng lẫn danh sách tỉnh đều ở
đây, đừng hardcode lại ở repository/FE.

Hai điểm dữ liệu thực tế phải xử lý (rà từ prod 2026-07):

1. ``Order.province`` lưu tên TỰ DO do nhiều luồng ingest ghi vào, nên cùng một
   tỉnh có nhiều biến thể chính tả: "Hoà Bình" / "Hòa Bình" / "hòa Bình". Khoá
   tra cứu vì thế bỏ dấu + lowercase (``_key``) — không có 2 tỉnh nào của VN
   trùng nhau sau khi bỏ dấu nên phép chuẩn hoá này an toàn.
2. Dữ liệu đang dùng bộ tên 63 tỉnh CŨ (trước sáp nhập 2025) và "Huế". Bộ tên
   mới sau sáp nhập đều là tên tỉnh đã có trong bảng này (tỉnh được giữ lại lấy
   tên cũ của một trong các tỉnh thành phần) nên map vẫn đúng khi dữ liệu đổi
   sang tên mới — trừ khi VN đặt tên hoàn toàn mới, lúc đó bổ sung vào đây.
"""

import unicodedata

# Key vùng → nhãn hiển thị. Thứ tự khai báo = thứ tự hiển thị (Bắc → Nam).
REGION_LABELS: dict[str, str] = {
    "northern_mountains": "Trung du & miền núi phía Bắc",
    "red_river_delta": "Đồng bằng sông Hồng",
    "north_central": "Bắc Trung Bộ",
    "south_central": "Nam Trung Bộ",
    "central_highlands": "Tây Nguyên",
    "southeast": "Đông Nam Bộ",
    "mekong_delta": "Tây Nam Bộ",
}

# Tỉnh không map được (tên lạ, dữ liệu rác) rơi vào nhóm này.
UNKNOWN_REGION = "unknown"

# Nhãn cho đơn CHƯA có địa chỉ. Phải có nhóm này thay vì loại đơn ra, nếu không
# tổng của khối địa lý sẽ nhỏ hơn tổng doanh thu ở các khối khác mà người đọc
# không biết vì sao (phát hiện 2026-07-20).
#
# Đây KHÔNG phải dữ liệu hỏng: rà 30 ngày thấy 705 đơn trống ``province`` thì
# 654 đơn ở trạng thái Mới (status=0) — đơn vừa vào, sale chưa nhập địa chỉ.
# Nhóm này vì thế là một thông tin có ích (doanh thu đang nằm ở đơn chưa xử lý),
# không phải rác cần giấu đi.
UNKNOWN_PROVINCE_LABEL = "Chưa có địa chỉ"

# Vùng → danh sách tỉnh (tên chuẩn dùng để hiển thị). Đông Bắc gộp chung với Tây
# Bắc thành "Trung du & miền núi phía Bắc" theo chốt của business 2026-07-20.
REGION_PROVINCES: dict[str, tuple[str, ...]] = {
    "northern_mountains": (
        "Hà Giang",
        "Cao Bằng",
        "Bắc Kạn",
        "Tuyên Quang",
        "Lào Cai",
        "Yên Bái",
        "Thái Nguyên",
        "Lạng Sơn",
        "Bắc Giang",
        "Phú Thọ",
        "Quảng Ninh",
        "Điện Biên",
        "Lai Châu",
        "Sơn La",
        "Hòa Bình",
    ),
    "red_river_delta": (
        "Hà Nội",
        "Hải Phòng",
        "Hải Dương",
        "Hưng Yên",
        "Vĩnh Phúc",
        "Bắc Ninh",
        "Thái Bình",
        "Nam Định",
        "Hà Nam",
        "Ninh Bình",
    ),
    "north_central": (
        "Thanh Hóa",
        "Nghệ An",
        "Hà Tĩnh",
        "Quảng Bình",
        "Quảng Trị",
        "Huế",
    ),
    "south_central": (
        "Đà Nẵng",
        "Quảng Nam",
        "Quảng Ngãi",
        "Bình Định",
        "Phú Yên",
        "Khánh Hòa",
        "Ninh Thuận",
        "Bình Thuận",
    ),
    "central_highlands": (
        "Kon Tum",
        "Gia Lai",
        "Đắk Lắk",
        "Đắk Nông",
        "Lâm Đồng",
    ),
    "southeast": (
        "Hồ Chí Minh",
        "Bà Rịa - Vũng Tàu",
        "Bình Dương",
        "Bình Phước",
        "Đồng Nai",
        "Tây Ninh",
    ),
    "mekong_delta": (
        "Long An",
        "Tiền Giang",
        "Bến Tre",
        "Trà Vinh",
        "Vĩnh Long",
        "Đồng Tháp",
        "An Giang",
        "Kiên Giang",
        "Cần Thơ",
        "Hậu Giang",
        "Sóc Trăng",
        "Bạc Liêu",
        "Cà Mau",
    ),
}

# Alias cho tên viết khác trong dữ liệu (viết tắt, thiếu/thừa tiền tố). Biến thể
# chỉ khác DẤU không cần khai ở đây — _key() đã bỏ dấu.
_PROVINCE_ALIASES: dict[str, str] = {
    "tp hcm": "Hồ Chí Minh",
    "tphcm": "Hồ Chí Minh",
    "sai gon": "Hồ Chí Minh",
    "thanh pho ho chi minh": "Hồ Chí Minh",
    "ha noi city": "Hà Nội",
    "thua thien hue": "Huế",
    "thanh pho hue": "Huế",
    "ba ria vung tau": "Bà Rịa - Vũng Tàu",
    "brvt": "Bà Rịa - Vũng Tàu",
    "dak lak": "Đắk Lắk",
    "daklak": "Đắk Lắk",
    "dac lac": "Đắk Lắk",
    "dak nong": "Đắk Nông",
    "daknong": "Đắk Nông",
}


def _key(value: str) -> str:
    """Khoá tra cứu: bỏ dấu, lowercase, gom khoảng trắng, bỏ dấu gạch nối."""
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    text = text.replace("-", " ").replace(".", " ").replace(",", " ")
    return " ".join(text.lower().split())


def _build_lookup() -> tuple[dict[str, str], dict[str, str]]:
    """(key → tên tỉnh chuẩn, key → vùng) dựng 1 lần lúc import."""
    canonical: dict[str, str] = {}
    region: dict[str, str] = {}
    for region_key, provinces in REGION_PROVINCES.items():
        for province in provinces:
            canonical[_key(province)] = province
            region[_key(province)] = region_key
    for alias, province in _PROVINCE_ALIASES.items():
        canonical[_key(alias)] = province
        region[_key(alias)] = region[_key(province)]
    return canonical, region


_CANONICAL_BY_KEY, _REGION_BY_KEY = _build_lookup()


def canonical_province(raw: str) -> str:
    """Tên tỉnh chuẩn để gom nhóm; rỗng → nhãn "Chưa có địa chỉ".

    Nhờ hàm này mà "Hoà Bình"/"Hòa Bình"/"hòa Bình" về chung một dòng báo cáo.
    Tên lạ (không rỗng) giữ nguyên bản trim để người đọc thấy dữ liệu thật.
    """
    text = str(raw or "").strip()
    if not text:
        return UNKNOWN_PROVINCE_LABEL
    return _CANONICAL_BY_KEY.get(_key(text), text)


def region_of(raw: str) -> str:
    """Key vùng của một tỉnh; không nhận diện được → ``UNKNOWN_REGION``."""
    return _REGION_BY_KEY.get(_key(raw), UNKNOWN_REGION)


# Tên tỉnh sắp theo độ dài GIẢM DẦN để khớp tiền tố dài nhất trước: cắt
# "Bà Rịa - Vũng Tàu - Thành phố Phú Mỹ" theo dấu gạch đầu tiên sẽ ra "Bà Rịa"
# (sai tỉnh), nên phải thử "Bà Rịa - Vũng Tàu" trước.
_PROVINCE_KEYS_BY_LENGTH = sorted(_CANONICAL_BY_KEY, key=len, reverse=True)


def province_from_location(location: str) -> str:
    """Suy tỉnh từ ``Order.kiotviet_location``: "<Tỉnh> - <Quận/Huyện>" hoặc "Tỉnh <X>".

    Dùng khi ``Order.province`` trống nhưng địa chỉ KiotViet vẫn có tỉnh (rà
    30 ngày: 17 đơn thuộc diện này). Không nhận ra tỉnh nào → chuỗi rỗng.
    """
    key = _key(location)
    if not key:
        return ""
    # Khu vực theo cấu trúc 2 cấp (từ 09/2026): "Tỉnh X" / "Thành phố X" — bỏ loại
    # đơn vị để khớp tên tỉnh; "Thành phố" cũng có thể là cấp huyện ("Hà Nội -
    # Thành phố Sơn Tây") nhưng dạng đó đã khớp tiền tố tỉnh ở vòng dưới.
    for prefix in ("tinh ", "thanh pho "):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    for province_key in _PROVINCE_KEYS_BY_LENGTH:
        if key == province_key or key.startswith(f"{province_key} "):
            return _CANONICAL_BY_KEY[province_key]
    return ""


def resolve_province(province: str, location: str = "") -> str:
    """Tỉnh dùng cho báo cáo: ưu tiên ``province``, hụt thì suy từ ``location``.

    Không có cả hai → ``UNKNOWN_PROVINCE_LABEL`` (đơn chưa nhập địa chỉ).
    """
    text = str(province or "").strip()
    if text:
        return canonical_province(text)
    from_location = province_from_location(location)
    return from_location or UNKNOWN_PROVINCE_LABEL
