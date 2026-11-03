"""Đẩy khách của đơn landing sang Foxia — chiều GHI duy nhất của tích hợp.

Foxia nhận một POST JSON tại `{base}/{idnguonkhachhang}`, KHÔNG xác thực. `id`
quyết định đơn được ghi vào nguồn khách hàng nào, và nó tra từ bảng liên kết
(kênh × loại nguồn) do người vận hành khai báo — spec 2026-09-09, đổi trục
2026-09-16: cột chọn theo `order_source` của đơn (landing/messenger/hotline/
zalo), không còn theo SKU. Mô tả sản phẩm khách chọn (`product_raw`) đi vào
trường `message` — ô "Khách hàng ghi chú" của cơ hội bên Foxia.

Ba ràng buộc bao trùm mọi thứ ở đây:
- **Không retry.** POST này không idempotent (Foxia chưa cấp khoá chống trùng),
  gửi lại = khách thứ hai. HttpClient dựng với `retries=0`.
- **Không raise.** Lỗi của đối tác không được làm bẩn hàng đợi hay sinh cảnh báo
  Celery; bằng chứng nằm ở dòng FoxiaOrderPush, không ở stack trace.
- **Luôn ghi một dòng.** Kể cả nhánh bỏ qua — phần bị bỏ phải nhìn thấy được.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from apps.common.http_client import HttpClient
from apps.foxia.domain.datetime_utils import to_foxia_utc
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

_ADDRESS_PARTS = ("address", "ward", "district", "province")

# Trùng FoxiaOrderPush.RESPONSE_LIMIT — không import model ở mức module để giữ
# lazy import như hiện tại (tránh vòng import lúc app chưa sẵn sàng).
FOXIA_RESPONSE_LIMIT = 2000


def build_address(order) -> str:
    """Ghép địa chỉ giao hàng thành một chuỗi cho Foxia.

    Rơi về `raw_address` khi bốn phần đã tách đều rỗng: đơn landing chưa được
    sale chuẩn hoá địa chỉ vẫn phải đẩy được — bên nhận cần biết khách tồn tại.
    """
    parts = [str(getattr(order, name, "") or "").strip() for name in _ADDRESS_PARTS]
    joined = ", ".join(part for part in parts if part)
    return joined or str(getattr(order, "raw_address", "") or "").strip()


def build_message(order) -> str:
    """`product_raw` của đơn — nguyên văn khách chọn trên landing (gói, số lượng,
    giá), sale Foxia đọc là hiểu, khác SKU nội bộ. Đơn không có → ""."""
    return str(order.product_raw or "").strip()


def build_salesperson_id(order) -> int | None:
    """`User.foxia_id` của sale đang gán cho đơn, dạng số; không có → None.

    `foxia_id` là ô chữ tự do trên form user, còn Foxia định danh nhân viên bằng
    id số — giá trị không phải số coi như chưa khai (gửi sai id còn tệ hơn không
    gửi: đơn rơi vào tay người khác bên Foxia).
    """
    staff = order.staff
    if staff is None:
        return None
    foxia_id = str(staff.foxia_id or "").strip()
    if not foxia_id.isdigit():
        if foxia_id:
            logger.warning(
                "Foxia push: order=%s staff=%s foxia_id khong phai so, bo qua staff_id trong payload",
                order.id, staff.id,
            )
        return None
    return int(foxia_id)


# `chance_id` Foxia: loại cơ hội — đơn mới hay resale.
CHANCE_NEW = 1
CHANCE_RESALE = 2


def build_chance_id(order) -> int:
    """Đơn thuộc kênh Resale → 2, còn lại → 1.

    Dùng đúng định nghĩa resale của công ty (đơn của kênh Resale,
    `resale_channel_ids()`), không suy từ lịch sử mua — xem
    `apps/orders/reporting/resale_reports.py`.
    """
    from apps.orders.reporting.shared import resale_channel_ids

    return CHANCE_RESALE if order.channel_id in resale_channel_ids() else CHANCE_NEW


def build_payload(order) -> dict:
    """Ba trường Foxia cần + `chance_id` + `created_at` (giờ tạo đơn, UTC theo quy
    ước Foxia) + `message` = `product_raw` (chỉ khi có) + `staff_id` (chỉ khi
    đơn đã có sale mang `foxia_id`). LƯU Ý: `staff_id` gửi đi là id nhân viên BÊN
    FOXIA (`User.foxia_id`), KHÔNG phải `Order.staff_id` nội bộ.

    Foxia nhận thêm facebook/email/utm_*/link, nhưng ta không có dữ liệu thật
    cho chúng — gửi chuỗi rỗng và không gửi là hai ý nghĩa khác nhau với bên
    nhận. Cùng lý do, `message`/`staff_id` bị bỏ hẳn khi không có.

    Sale được gán TRƯỚC khi xếp lịch đẩy (chỉ định lúc tạo, hoặc bộ chia tự động
    chạy trong cùng transaction tạo đơn), nên task đọc lại đơn là thấy. Đơn chia
    muộn (sweep, ngoài giờ) thì đã đẩy xong trước đó — KHÔNG đẩy lại vì POST
    không idempotent.
    """
    payload = {
        "name": str(order.name or "").strip(),
        "phone": str(order.phone or "").strip(),
        "address": build_address(order),
        "chance_id": build_chance_id(order),
        "created_at": to_foxia_utc(order.created_at),
    }
    message = build_message(order)
    if message:
        payload["message"] = message
    salesperson_id = build_salesperson_id(order)
    if salesperson_id is not None:
        payload["staff_id"] = salesperson_id
    return payload


@dataclass(frozen=True)
class PushResult:
    """Kết quả MỘT lần POST khách sang Foxia — thuần dữ liệu, không biết đơn hay lead."""

    url: str
    http_status: int | None
    response_body: str
    error: str
    duration_ms: int
    cohoi_id: int | None
    khachhang_id: int | None

    @property
    def ok(self) -> bool:
        return self.http_status is not None and 200 <= self.http_status < 300


def _foxia_id(value) -> int | None:
    """Id Foxia hợp lệ (số nguyên dương) hoặc None.

    Odoo trả `false` cho many2one rỗng — và `bool` là subclass của `int` nên
    `int(False) == 0` lọt qua mọi phép ép kiểu. 07/10/2026: 158 lần đẩy resale nhận
    `"cohoi_id": false` (Foxia KHÔNG tạo cơ hội) mà nhật ký ghi `cohoi_id=0`, trông
    như thành công.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_lead_ids(raw_body: str) -> tuple[int | None, int | None]:
    """`data.cohoi_id` / `data.khachhang_id` từ phản hồi Foxia; sai dạng → (None, None).

    Hợp đồng phản hồi chưa được chốt bằng văn bản, nên MỌI sai lệch (không phải
    JSON, thiếu khoá, `false`) chỉ là "không có id để ghi" — không phải lỗi.
    """
    try:
        data = json.loads(raw_body).get("data") or {}
    except Exception:  # noqa: BLE001 — xem docstring
        return None, None
    if not isinstance(data, dict):
        return None, None
    return _foxia_id(data.get("cohoi_id")), _foxia_id(data.get("khachhang_id"))


def push_customer(source_id: int, payload: dict) -> PushResult:
    """POST `payload` tới `{FOXIA_PUSH_BASE_URL}/{source_id}`. Không retry, không raise.

    Dùng chung cho đẩy đơn landing (`FoxiaOrderPushService`) và đẩy lead Pancake
    (`apps.pancake`). Bên gọi tự ghi nhật ký vào bảng của mình.
    """
    base = sysconfig.get("FOXIA_PUSH_BASE_URL").rstrip("/")
    url = f"{base}/{source_id}"
    limit = FOXIA_RESPONSE_LIMIT

    started = time.monotonic()
    http_status = None
    raw_body = ""
    error = ""
    try:
        with HttpClient(
            timeout=sysconfig.get("FOXIA_PUSH_TIMEOUT"),
            retries=0,
            default_headers={"Content-Type": "application/json"},
        ) as client:
            response = client.post(url, json=payload)
            http_status = response.status_code
            raw_body = str(response.text or "")
    except SoftTimeLimitExceeded:
        # Ngoại lệ DUY NHẤT được phép thoát khỏi hàm này: task sắp bị Celery giết
        # cứng (time_limit) — phải thoát ngay để nhả lock/tránh chạy tiếp giữa
        # chừng, không phải nuốt gọn rồi cố ghi nốt bản ghi FoxiaOrderPush.
        raise
    except Exception as exc:  # noqa: BLE001 — lỗi đối tác không được thoát ra task
        error = f"{type(exc).__name__}: {exc}"[:limit]

    duration_ms = int((time.monotonic() - started) * 1000)
    ok = http_status is not None and 200 <= http_status < 300
    cohoi_id, khachhang_id = _parse_lead_ids(raw_body) if ok else (None, None)
    return PushResult(
        url=url,
        http_status=http_status,
        response_body=raw_body[:limit],
        error=error,
        duration_ms=duration_ms,
        cohoi_id=cohoi_id,
        khachhang_id=khachhang_id,
    )


def _record_lead(order, result: PushResult) -> None:
    """Ghi cơ hội Foxia vừa tạo. Không có `cohoi_id` → không có gì để ghi, không phải lỗi."""
    from apps.foxia.models import FoxiaLeadLink

    if result.cohoi_id is None:
        logger.warning("Foxia push: order=%s khong doc duoc cohoi_id tu phan hoi", order.id)
        return
    try:
        FoxiaLeadLink.objects.get_or_create(
            cohoi_id=result.cohoi_id,
            defaults={"order": order, "khachhang_id": result.khachhang_id},
        )
    except Exception:  # noqa: BLE001 — "không raise" là hợp đồng của cả module (xem docstring)
        logger.warning("Foxia push: order=%s khong ghi duoc FoxiaLeadLink cohoi_id=%s", order.id, result.cohoi_id)


class FoxiaOrderPushService:
    """Xếp lịch (từ ``apps.orders``, xem ``OrderServiceCommands._schedule_foxia_push``
    — layering contract cấm ``orders`` import ``foxia`` trực tiếp) và thực thi
    (``push``, gọi từ task Celery ``push_order_to_foxia``) việc đẩy một đơn.
    """

    @staticmethod
    def push(order_id: int) -> str:
        """Thân của task. Trả về giá trị `FoxiaPushStatus`, hoặc `disabled`/`missing`."""
        from apps.foxia.models import (
            FoxiaLinkCell,
            FoxiaLinkKind,
            FoxiaOrderPush,
            FoxiaPushStatus,
        )
        from apps.orders.models import Order

        # Staging chạy snapshot dữ liệu prod: tuyệt đối không bắn đơn sang CRM thật.
        # Lớp 1 là registry (FOXIA_PUSH_ENABLED staging_forbidden → luôn False); đây là
        # lớp 2, chặn ngay cả khi ai đó override_sysconfig/bật nhầm trong shell staging.
        if getattr(settings, "IS_STAGING", False):
            return "disabled"
        # Task còn kẹt trong hàng đợi lúc tắt cờ KHÔNG được đẻ rác nhật ký.
        if not sysconfig.get("FOXIA_PUSH_ENABLED"):
            return "disabled"

        # select_related: log có mã kênh + payload có foxia_id của sale mà không
        # đẻ thêm query cho mỗi đơn.
        order = Order.objects.filter(id=order_id).select_related("channel", "staff").first()
        if order is None:
            logger.warning("Foxia push: không tìm thấy đơn %s", order_id)
            return "missing"

        # Id tra theo (kênh, cột = order_source của đơn) — không còn theo SKU.
        # Nguồn đơn không có cột (koc, shopee…), kênh chưa có hàng, hay ô trống
        # đều là "chưa cấu hình": không có gì để đẩy.
        kind = order.order_source if order.order_source in FoxiaLinkKind.values else None
        source_id = None
        if kind is not None:
            source_id = (
                FoxiaLinkCell.objects.filter(link_channel__channel_id=order.channel_id, kind=kind)
                .values_list("foxia_source_id", flat=True)
                .first()
            )
        if source_id is None:
            FoxiaOrderPush.objects.create(order=order, status=FoxiaPushStatus.SKIPPED_NO_MAPPING)
            logger.info(
                "Foxia push %s: order=%s kenh=%s nguon_don=%s — chua cau hinh id cho cap nay",
                FoxiaPushStatus.SKIPPED_NO_MAPPING, order_id, order.channel.code,
                order.order_source or "-",
            )
            return FoxiaPushStatus.SKIPPED_NO_MAPPING

        payload = build_payload(order)
        result = push_customer(source_id, payload)
        status = FoxiaPushStatus.OK if result.ok else FoxiaPushStatus.FAILED
        FoxiaOrderPush.objects.create(
            order=order,
            status=status,
            foxia_source_id=source_id,
            url=result.url,
            request_body=payload,
            http_status=result.http_status,
            response_body=result.response_body,
            error=result.error,
            duration_ms=result.duration_ms,
        )
        if result.ok:
            _record_lead(order, result)
        # Một dòng duy nhất mang đủ ngữ cảnh để đọc thẳng trong
        # `docker compose logs worker-foxia`, khỏi phải mở DB đối chiếu.
        # KHÔNG log tên/sđt/địa chỉ khách: log dễ bị gom đi nơi khác hơn DB, và
        # `order=<id>` đã đủ tra ngược tới `foxia_order_pushes.request_body`.
        (logger.info if result.ok else logger.warning)(
            "Foxia push %s: order=%s kenh=%s nguon_don=%s nguon=%s chance=%s staff_id=%s http=%s %sms url=%s%s",
            status, order_id, order.channel.code, kind, source_id,
            payload["chance_id"], payload.get("staff_id") or "-",
            result.http_status, result.duration_ms, result.url,
            f" error={result.error}" if result.error else "",
        )
        return status
