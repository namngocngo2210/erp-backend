"""Trạng thái vận chuyển thực từ Pancake → 2 field partner của đơn CRM (two-way-sync §5.1, MR D).

Quyết định 01/10: `orders.status` vẫn do KiotViet quyết định — Pancake KHÔNG BAO GIỜ ghi
`status`. Pancake chỉ ghi:
- `partner_delivery_status` = trạng thái đơn Pancake, tên tiếng Việt (bảng `status_map`;
  API trả `status_name` là mã tiếng Anh `shipped`/`delivered`… nên không dùng nguyên văn).
- `partner_dvvc_delivery_status` = trạng thái ĐVVC mới nhất, xem `dvvc_status`.

Không import Django.

Phát hiện từ API thật (GET orders read-only, 800 đơn cập nhật gần nhất, 01/10/2026):
- `partner.partner_status` (enum spec) CHỈ có ở một phần đơn (Shopee Xpress, VTP, một số
  GHN): `delivered`, `on_the_way`, `request_received`, `returning`, và `cancelled` (spec ghi
  `canceled` — thực tế có chữ l kép). Đơn TikTok (GHN/J&T/Best) để null.
- Đơn TikTok có `partner.extend_update[]` = hành trình ĐVVC, **mới nhất đứng đầu**, khoá thực
  tế `action_code` (int), `description`, `status` (câu tiếng Anh kèm địa danh, có khi kèm SĐT
  tài xế), `updated_at`, `update_time_millis` (spec ghi `key`/`update_at` — không đúng).
  Mã `action_code` gặp: 20101 chờ lấy, 20201 đang lấy, 20301 lấy không thành, 30901/36201
  đã lấy, 31301/31401 qua kho phân loại, 40101 tới bưu cục giao, 40501/41001/41101/41201 đang
  giao, 40601/40801 giao không thành (sẽ giao lại), 41801/41901 không giao được → hoàn,
  50101/50102 đã giao, 70201–70212 đang hoàn, 70301 chờ tiêu huỷ, 80101 đã hoàn về shop.
- `sub_status` luôn null; `status_history` có ở mọi đơn (`old_status`, `status`, `updated_at`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Tên tiếng Việt theo spec (`x-enum-descriptions-i18n.vie`) — dự phòng khi `status_map`
# thiếu dòng. Seed `status_map` dùng cùng bộ tên (huỷ/xoá viết kiểu "uỷ"/"oá").
PANCAKE_STATUS_NAMES: dict[int, str] = {
    0: "Mới", 17: "Chờ xác nhận", 11: "Chờ hàng", 12: "Chờ in", 13: "Đã in", 20: "Đã đặt hàng",
    1: "Đã xác nhận", 8: "Đang đóng hàng", 9: "Chờ chuyển hàng", 2: "Đã gửi hàng", 3: "Đã nhận",
    16: "Đã thu tiền", 4: "Đang hoàn", 15: "Hoàn một phần", 5: "Đã hoàn", 6: "Đã huỷ", 7: "Đã xoá",
}

SHIPPED = 2
CANCELLED = 6
DELETED = 7
DEAD = frozenset({CANCELLED, DELETED})

# Báo cáo "đơn treo": nhóm CHƯA GỬI (theo yêu cầu: Mới / Đã xác nhận / Chờ in / Đã in /
# Đang đóng hàng / Chờ chuyển hàng). Chờ xác nhận / Chờ hàng / Đã đặt hàng không tính —
# đó là chờ khách/nhà cung cấp, kho chưa thể làm.
NOT_SENT = frozenset({0, 1, 12, 13, 8, 9})

# Tiến trình để chọn kiện CHẬM NHẤT của đơn tách N kiện. Trước gửi theo thứ tự xử lý kho;
# "Đã gửi" < "Đang hoàn" < trạng thái kết thúc (nhận/thu tiền/hoàn/hoàn một phần).
_PROGRESS: dict[int, int] = {
    0: 0, 17: 1, 11: 2, 20: 3, 1: 4, 12: 5, 13: 6, 8: 7, 9: 8,
    2: 10, 4: 11, 3: 20, 16: 20, 15: 20, 5: 20,
}

# partner.partner_status (enum spec + biến thể thật) → nhãn tiếng Việt của spec.
DVVC_STATUS_LABELS: dict[str, str] = {
    "waiting": "Chờ xử lý",
    "request_received": "ĐVVC đã tiếp nhận đơn",
    "processing_picked_up": "Đang xử lý lấy hàng",
    "picking_up": "Đang lấy hàng",
    "delay_pickup": "Trễ lấy hàng",
    "picked_up": "Đã lấy hàng",
    "waiting_on_the_way": "Chờ trung chuyển",
    "on_the_way": "Đang trung chuyển",
    "contact_delivery_company": "Liên hệ ĐVVC",
    "out_for_delivery": "Đang giao hàng",
    "delay_delivery": "Trễ giao hàng",
    "inform_recipient": "Thông báo cho người nhận",
    "undeliverable": "Giao không thành",
    "waiting_for_return": "Chờ chuyển hoàn",
    "delivered": "Đã giao hàng",
    "delivered_cod": "Đã giao hàng, đã đối soát COD",
    "returning": "Đang chuyển hoàn",
    "returned": "Đã chuyển hoàn",
    "returned_cod": "Đã chuyển hoàn, đã đối soát COD",
    "canceled": "Đã hủy vận đơn",
    "cancelled": "Đã hủy vận đơn",
}

# `extend_update.action_code` (đơn TikTok) → nhãn cùng bộ từ vựng với `partner_status`.
# Khoá = mã chính xác, rồi tới tiền tố 3 chữ số (mã mới cùng họ vẫn có nhãn).
_ACTION_CODE_LABELS: dict[int, str] = {
    20101: "Chờ xử lý",
    40101: "Đang trung chuyển",
    70301: "Chờ tiêu huỷ (giao/hoàn thất bại)",
    80101: "Đã chuyển hoàn",
}
_ACTION_PREFIX_LABELS: dict[str, str] = {
    "202": "Đang lấy hàng",
    "203": "Trễ lấy hàng",
    "309": "Đã lấy hàng",
    "362": "Đã lấy hàng",
    "313": "Đang trung chuyển",
    "314": "Đang trung chuyển",
    "405": "Đang giao hàng",
    "410": "Đang giao hàng",
    "411": "Đang giao hàng",
    "412": "Đang giao hàng",
    "406": "Giao không thành",
    "408": "Giao không thành",
    "418": "Chờ chuyển hoàn",
    "419": "Chờ chuyển hoàn",
    "501": "Đã giao hàng",
    "702": "Đang chuyển hoàn",
    "801": "Đã chuyển hoàn",
}

MAX_FIELD_LEN = 64  # orders.partner_* là varchar(64)
_PHONE_RE = re.compile(r"\+?\d[\d .-]{6,}\d")
_SPACES_RE = re.compile(r"\s+")


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    text = _str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _millis(value: Any) -> datetime | None:
    ms = _int(value)
    if ms is None or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def clip(text: str) -> str:
    return text[:MAX_FIELD_LEN]


def status_label(code: int | None, names: dict[int, str] | None = None, raw_name: str = "") -> str:
    """Tên tiếng Việt của trạng thái đơn Pancake: `status_map` → bảng spec → `status_name` thô."""
    if code is None:
        return clip(raw_name)
    return clip((names or {}).get(code) or PANCAKE_STATUS_NAMES.get(code) or raw_name or str(code))


def sanitize(text: str) -> str:
    """Câu mô tả ĐVVC thô: che SĐT (tài xế/hotline), gộp khoảng trắng, cắt 64 ký tự."""
    return clip(_SPACES_RE.sub(" ", _PHONE_RE.sub("***", text or "")).strip())


def action_label(code: int | None) -> str:
    if code is None:
        return ""
    return _ACTION_CODE_LABELS.get(code) or _ACTION_PREFIX_LABELS.get(str(code)[:3], "")


@dataclass(frozen=True)
class DvvcStatus:
    label: str
    at: datetime | None


def dvvc_status(partner: Any) -> DvvcStatus:
    """Trạng thái ĐVVC mới nhất của một đơn Pancake.

    1. `partner_status` (enum) → nhãn spec; mốc = `partner.updated_at`.
    2. Không có → mục MỚI NHẤT của `extend_update` (theo `update_time_millis`/`updated_at`,
       không tin thứ tự mảng): nhãn theo `action_code`; mã lạ → câu mô tả đã che SĐT.
    3. Không có gì → rỗng (ĐVVC chưa nhận đơn).
    """
    if not isinstance(partner, dict):
        return DvvcStatus("", None)
    code = _str(partner.get("partner_status")).lower()
    if code:
        return DvvcStatus(clip(DVVC_STATUS_LABELS.get(code, code)), _parse_dt(partner.get("updated_at")))
    updates = [u for u in partner.get("extend_update") or () if isinstance(u, dict)]
    if not updates:
        return DvvcStatus("", None)

    def when(u: dict[str, Any]) -> datetime | None:
        return _millis(u.get("update_time_millis")) or _parse_dt(u.get("updated_at") or u.get("update_at"))

    # Thiếu mốc → giữ thứ tự mảng (thực tế mới nhất đứng đầu): max ổn định lấy phần tử đầu.
    floor = datetime.min.replace(tzinfo=UTC)
    newest = max(enumerate(updates), key=lambda iu: (when(iu[1]) or floor, -iu[0]))[1]
    label = action_label(_int(newest.get("action_code"))) or sanitize(
        _str(newest.get("status")) or _str(newest.get("description"))
    )
    return DvvcStatus(label, when(newest))


def progress(code: int | None) -> int:
    return _PROGRESS.get(code if code is not None else -1, -1)


@dataclass(frozen=True)
class PartState:
    """Một kiện (một đơn Pancake) của đơn CRM, như đang lưu ở `pancake_pos_inbound_orders`."""

    pancake_order_id: str
    pancake_status: int | None
    status_name: str
    dvvc_status: str


def slowest_part(parts: Sequence[PartState]) -> PartState | None:
    """Kiện đại diện cho 2 field của đơn CRM tách N kiện:
    - bỏ kiện huỷ/xoá; mọi kiện huỷ/xoá → kiện đầu (hiện "Đã huỷ");
    - còn lại lấy kiện CHẬM nhất theo tiến trình (đơn chỉ hiện "Đã gửi hàng" khi mọi kiện
      đã gửi; "Đã nhận" khi mọi kiện đã kết thúc). Trạng thái lạ (không có trong bảng
      tiến trình) xếp chậm nhất. Hoà → kiện đứng trước (thứ tự kiện)."""
    if not parts:
        return None
    alive = [p for p in parts if p.pancake_status not in DEAD]
    if not alive:
        return parts[0]
    return min(enumerate(alive), key=lambda ip: (progress(ip[1].pancake_status), ip[0]))[1]


# ── "Trạng thái vận chuyển thực" CRM (order_assessments.delivery_business_status_code) ──
# Catalog `apps.orders.crm_catalog.DELIVERY_BUSINESS_STATUS_OPTIONS`. Ngữ nghĩa đang dùng:
# - `dispatched` = DẤU HIỆU ĐÃ TRỪ TỒN / chặn xuất trùng — CHỈ luồng xuất kho được ghi
#   (`dispatch_orders_batch`); Pancake chỉ đi đường đó khi bật PANCAKE_POS_DISPATCH_ENABLED.
# - `not_received` nằm trong nhóm "đang giao" của `kiotviet_validation` (chặn tạo đơn
#   trùng SĐT) — hợp với đơn đang hoàn/đã hoàn (khách không nhận).
# - `stalled_complaint` / `post_office_lost` chỉ người nhập tay (khiếu nại, mất hàng) —
#   `stalled_complaint` còn là tín hiệu phân khúc khách (`customers.segmentation`).
# - Mã cũ `delivered_success`/`delivering`/`bombed` ở `customers/services.py` không còn
#   trong catalog (dữ liệu lịch sử) — không ghi.
WAREHOUSE_PROCESSING = "warehouse_processing"
DISPATCHED = "dispatched"
RECEIVED = "received"
NOT_RECEIVED = "not_received"
MANUAL_CODES = frozenset({"stalled_complaint", "post_office_lost"})
DELIVERY_CODES = frozenset({WAREHOUSE_PROCESSING, DISPATCHED, RECEIVED, NOT_RECEIVED, *MANUAL_CODES})
_TERMINAL = frozenset({RECEIVED, NOT_RECEIVED})
# Chỉ tiến, không lùi: rỗng < kho đang xử lý < đã xuất kho < (đã nhận | chưa nhận).
_RANK = {"": 0, WAREHOUSE_PROCESSING: 1, DISPATCHED: 2, RECEIVED: 3, NOT_RECEIVED: 3}

# Seed `status_map.crm_delivery_code` (sửa được trong bảng). Đã huỷ/Đã xoá, Hoàn một phần
# (khách đã nhận một phần) và nhóm chờ khách/nhà cung cấp → không đổi.
DEFAULT_DELIVERY_CODES: dict[int, str] = {
    0: WAREHOUSE_PROCESSING, 1: WAREHOUSE_PROCESSING, 12: WAREHOUSE_PROCESSING,
    13: WAREHOUSE_PROCESSING, 8: WAREHOUSE_PROCESSING, 9: WAREHOUSE_PROCESSING,
    2: DISPATCHED, 3: RECEIVED, 16: RECEIVED, 4: NOT_RECEIVED, 5: NOT_RECEIVED,
}

# Lý do không áp mã Pancake (để báo cáo lệch / log).
BLOCK_NEEDS_DISPATCH = "needs_dispatch"  # Pancake đã gửi (hoặc đã nhận khi cờ bật), CRM chưa xuất kho
BLOCK_MANUAL = "manual_code"  # mã do người nhập — không ghi đè
BLOCK_REGRESS = "regress"  # Pancake lùi trạng thái — chỉ tiến
BLOCK_DISPATCHED_LOCKED = "dispatched_locked"  # cờ xuất kho tắt: không đụng đơn đã xuất
BLOCK_UNKNOWN = "unknown_code"


@dataclass(frozen=True)
class DeliveryDecision:
    """`write` = mã ghi qua đường sửa đánh giá (journey), `dispatch` = phải chạy luồng xuất
    kho trước (trừ tồn); `blocked` = lý do không áp được mã đích (rỗng = không có gì để làm)."""

    write: str | None = None
    dispatch: bool = False
    blocked: str = ""


def decide_delivery_code(
    current: str | None, target: str | None, *, owned_by_pancake: bool, dispatch_enabled: bool,
    allow_dispatch: bool,
) -> DeliveryDecision:
    """Mã "Trạng thái vận chuyển thực" CRM cần ghi theo trạng thái Pancake.

    - Mã người nhập (`stalled_complaint`, `post_office_lost`, và `received`/`not_received`
      không do Pancake đặt — `owned_by_pancake=False`) không bao giờ bị ghi đè.
    - Chỉ tiến (`_RANK`); hai mã kết thúc đổi lẫn nhau chỉ khi Pancake đã đặt mã hiện tại.
    - `dispatched` (và đi tắt tới mã kết thúc từ trước xuất kho) cần luồng xuất kho:
      chỉ khi `dispatch_enabled` (cờ PANCAKE_POS_DISPATCH_ENABLED) và `allow_dispatch`
      (backfill = False). Không được → `blocked=needs_dispatch` (vào báo cáo lệch).
    - Cờ tắt (kho chạy trên Pancake, CRM không giữ tồn): mã kết thúc `received`/`not_received`
      ghi thẳng, không qua xuất kho (không trừ tồn) — kể cả đơn đang `dispatched`; riêng
      `dispatched` vẫn không ghi vì nó là dấu hiệu đã trừ tồn CRM."""
    cur = (current or "").strip()
    if not target or target == cur:
        return DeliveryDecision()
    if target not in _RANK:
        return DeliveryDecision(blocked=BLOCK_UNKNOWN)
    if cur in MANUAL_CODES:
        return DeliveryDecision(blocked=BLOCK_MANUAL)
    if cur not in _RANK:
        return DeliveryDecision(blocked=BLOCK_UNKNOWN)
    if cur in _TERMINAL and not owned_by_pancake:
        return DeliveryDecision(blocked=BLOCK_MANUAL)
    if _RANK[target] < _RANK[cur]:
        return DeliveryDecision(blocked=BLOCK_REGRESS)
    if target in _TERMINAL and not dispatch_enabled:
        return DeliveryDecision(write=target)
    if cur == DISPATCHED and not dispatch_enabled:
        return DeliveryDecision(blocked=BLOCK_DISPATCHED_LOCKED)
    if target == WAREHOUSE_PROCESSING or _RANK[cur] >= _RANK[DISPATCHED]:
        return DeliveryDecision(write=target)
    # Đích ≥ dispatched mà CRM chưa xuất kho → phải qua luồng xuất kho (trừ tồn).
    if dispatch_enabled and allow_dispatch:
        return DeliveryDecision(write=target if target in _TERMINAL else None, dispatch=True)
    return DeliveryDecision(blocked=BLOCK_NEEDS_DISPATCH)


# ── đối chiếu lệch KiotViet (orders.status) ↔ Pancake (chỉ báo cáo) ──────────────
# CRM OrderStatus: 0 mới, 1 chờ xử lý, 2 đang giao, 3 giao thành công, 4 đang hoàn,
# 5 đã hoàn, 6 huỷ, 7 đang lấy hàng, 8 chờ lấy lại, 9 đã lấy hàng, 10 chờ giao lại,
# 11 chờ giao, 12 chờ chuyển hoàn, 13 chờ xác nhận hoàn (apps.orders.models.OrderStatus).
@dataclass(frozen=True)
class MismatchRule:
    code: str
    label: str
    crm_statuses: frozenset[int]
    pancake_statuses: frozenset[int]


MISMATCH_RULES: tuple[MismatchRule, ...] = (
    MismatchRule("crm_cancelled_pancake_shipped", "CRM đã huỷ nhưng Pancake đã gửi/đã nhận",
                 frozenset({6}), frozenset({2, 3, 16})),
    MismatchRule("crm_delivered_pancake_returning", "CRM giao thành công nhưng Pancake đang hoàn/đã hoàn",
                 frozenset({3}), frozenset({4, 5})),
    MismatchRule("crm_returned_pancake_delivered", "CRM hoàn nhưng Pancake đã nhận",
                 frozenset({4, 5, 12, 13}), frozenset({3, 16})),
    MismatchRule("crm_shipping_pancake_cancelled", "CRM đang giao/đã giao nhưng Pancake đã huỷ",
                 frozenset({2, 3, 9, 10}), frozenset({6, 7})),
    MismatchRule("crm_pending_pancake_delivered", "CRM chưa giao nhưng Pancake đã nhận",
                 frozenset({0, 1, 7, 11}), frozenset({3, 16})),
)


def mismatch(crm_status: int | None, pancake_status: int | None) -> MismatchRule | None:
    if crm_status is None or pancake_status is None:
        return None
    return next(
        (r for r in MISMATCH_RULES if crm_status in r.crm_statuses and pancake_status in r.pancake_statuses), None
    )


def codes_by_name(names: dict[int, str]) -> dict[str, int]:
    """Tên hiển thị → mã Pancake (đảo `status_map` + bảng spec) để đọc ngược field đơn CRM."""
    out = {name: code for code, name in PANCAKE_STATUS_NAMES.items()}
    out.update({name: code for code, name in names.items() if name})
    return out


def names_for(codes: Iterable[int], names: dict[int, str]) -> list[str]:
    """Mọi tên có thể của các mã (bảng `status_map` + spec) — dùng lọc SQL theo tên."""
    out: set[str] = set()
    for code in codes:
        out.add(PANCAKE_STATUS_NAMES.get(code, ""))
        out.add(names.get(code, ""))
    return sorted(n for n in out if n)
