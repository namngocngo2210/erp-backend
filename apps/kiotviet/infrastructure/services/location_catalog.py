"""
Validate KiotViet location names against a retailer's LIVE /locations catalog.

Vì sao cần: hai retailer KiotViet khác nhau có thể đang ở phiên bản cấu trúc địa
giới khác nhau (vd cấu trúc cũ "Tỉnh - Huyện" vs cấu trúc mới đã sáp nhập "Tỉnh X").
Một snapshot tĩnh (kiotviet_address_final.json) chứa lẫn cả hai format nên không phản
ánh đúng những gì một retailer cụ thể chấp nhận — gửi locationName lạ sẽ bị KiotViet
trả về 420 KvValidateLocationException ("Khu vực không tồn tại").

Giải pháp: fetch danh mục /locations LIVE của retailer đích đúng một lần, cache theo
retailer trong vòng đời process, và chỉ giữ locationName khi nó thực sự tồn tại trong
danh mục đó. Khi không xác thực được (fetch lỗi/rỗng) thì coi như không hợp lệ
(fail-closed) để tránh rủi ro fail cả đơn — KiotViet vẫn tạo đơn được từ address text.
"""
import logging
import threading

from apps.orders.utils.kiotviet_address_mapping import _normalize

from .gateway_kship_customer_webhook import _kiotviet_request

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_cache: dict[str, set[str]] = {}
_lock = threading.Lock()


def _cache_key(channel) -> str:
    return str(getattr(channel, "retailer", "") or getattr(channel, "id", "") or "")


def reset_cache() -> None:
    """Xóa cache (dùng cho test / sau khi danh mục thay đổi)."""
    with _lock:
        _cache.clear()


def get_valid_location_names(channel) -> set[str]:
    """Trả về set tên location (đã normalize) mà retailer của `channel` chấp nhận.

    Fetch từ /locations live và cache theo retailer. Mọi lỗi -> set rỗng, caller
    hiểu là "không xác thực được".
    """
    key = _cache_key(channel)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        names = _fetch_location_names(channel)
        _cache[key] = names
        return names


def _fetch_location_names(channel) -> set[str]:
    names: set[str] = set()
    current_item = 0
    while True:
        try:
            resp = _kiotviet_request(
                order=None,
                channel=channel,
                method="GET",
                endpoint="/locations",
                params={"currentItem": current_item, "pageSize": _PAGE_SIZE},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "location_catalog.fetch_failed retailer=%s err=%s", _cache_key(channel), exc
            )
            return set()
        if resp.status_code != 200:
            logger.warning(
                "location_catalog.fetch_bad_status retailer=%s status=%s",
                _cache_key(channel), resp.status_code,
            )
            return set()
        body = resp.json() if resp.content else {}
        data = body.get("data") or body.get("Data") or []
        if not data:
            break
        for item in data:
            name = str(item.get("name") or item.get("Name") or "").strip()
            norm = _normalize(name)
            if norm:
                names.add(norm)
        total = body.get("total") or body.get("Total") or 0
        current_item += len(data)
        if len(data) < _PAGE_SIZE or (total and current_item >= int(total)):
            break
    logger.info(
        "location_catalog.loaded retailer=%s count=%d", _cache_key(channel), len(names)
    )
    return names


def is_location_valid(channel, location_name: str) -> bool:
    """True nếu `location_name` tồn tại trong danh mục live của retailer.

    Fail-closed: danh mục rỗng/không lấy được -> False (caller bỏ field này đi
    thay vì để KiotViet reject cả đơn).
    """
    if not location_name:
        return False
    valid = get_valid_location_names(channel)
    if not valid:
        return False
    return _normalize(location_name) in valid
