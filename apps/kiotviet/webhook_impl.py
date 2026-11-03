import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)


def _extract_payload_items(payload: dict) -> list[dict]:
    notifications = payload.get("Notifications")
    if not isinstance(notifications, list):
        return []

    items: list[dict] = []
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        data_items = notification.get("Data")
        if not isinstance(data_items, list):
            continue
        for item in data_items:
            if isinstance(item, dict):
                items.append(item)
    return items


def _first_present(source: dict, keys: list[str]):
    for key in keys:
        if key in source:
            return source.get(key)
    return None


def _parse_modified_datetime(value) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def _extract_payment_method(source: dict) -> str:
    payments = _first_present(source, ["Payments", "payments"])
    methods: list[str] = []
    if isinstance(payments, list):
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            method = str(_first_present(payment, ["Method", "method"]) or "").strip()
            if method and method not in methods:
                methods.append(method)
    if not methods:
        method = str(_first_present(source, ["Method", "method"]) or "").strip()
        if method:
            methods.append(method)
    return ", ".join(methods)[:100]


def _build_signature_variants(signature: str) -> set[str]:
    raw = str(signature or "").strip()
    if not raw:
        return set()

    normalized = raw
    if "=" in raw:
        _, normalized = raw.split("=", 1)
        normalized = normalized.strip()

    variants = {raw.lower(), normalized.lower()}
    if not raw.lower().startswith("sha256="):
        variants.add(f"sha256={normalized.lower()}")
    return variants


def verify_webhook_signature(*, raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False

    digest = hmac.new(
        key=str(secret).encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    expected_variants = {digest.lower(), f"sha256={digest.lower()}"}
    provided_variants = _build_signature_variants(signature)
    if not provided_variants:
        return False

    for provided in provided_variants:
        for expected in expected_variants:
            if hmac.compare_digest(provided, expected):
                return True
    return False


def _resolve_webhook_signing_secrets() -> tuple[list[str], str | None]:
    raw_secret = sysconfig.get("KIOTVIET_WEBHOOK_SECRET")
    candidates: list[str] = []
    if raw_secret:
        # Thứ tự: secret thô (một số tích hợp ký bằng bytes gốc) rồi bản base64 đã
        # đăng ký với KiotViet — giữ đúng hai ứng viên như trước khi bỏ khóa _B64.
        candidates.append(raw_secret)
        candidates.append(base64.b64encode(raw_secret.encode("utf-8")).decode("ascii"))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)

    valid = [secret for secret in deduped if len(secret) >= 8]
    if valid:
        return valid, None

    return [], "Webhook secret is not configured or too short."


def _find_order_for_invoice_event(*, channel_id: int, code: str) -> Order | None:
    """Đơn SỞ HỮU mã này — chỉ khớp chính xác `invoice_code`/`order_code`.

    KHÔNG tra `reattached_invoice_codes` ở đây: đơn kế thừa không sở hữu mã cũ nữa, mọi
    dữ liệu của hoá đơn đã chết (trạng thái giao, phí, mã vận đơn…) không được phép ghi
    đè lên nó. Mã chết được xử lý riêng qua `_find_order_superseding_code`.
    """
    if not code:
        return None
    return (
        Order.objects.select_related("channel")
        .filter(deleted_at__isnull=True, kiotviet_channel_id=channel_id)
        .filter(Q(invoice_code=code) | Q(order_code=code))
        .order_by("-updated_at", "-id")
        .first()
    )


def _find_order_superseding_code(*, channel_id: int, code: str) -> Order | None:
    """Đơn đã NUỐT mã này khi KiotViet huỷ-xuất lại hoá đơn (`code` → `code.NN`).

    Dùng để BỎ QUA sự kiện của mã đã chết: không auto-create dòng đơn huỷ trùng, và
    cũng không áp dữ liệu của hoá đơn chết lên đơn kế thừa đang sống.
    """
    if not code:
        return None
    return (
        Order.objects.filter(deleted_at__isnull=True, kiotviet_channel_id=channel_id)
        .filter(reattached_invoice_codes__contains=[code])
        .order_by("-updated_at", "-id")
        .first()
    )


def _create_order_from_invoice_event(*, channel: KiotVietChannel, code: str) -> Order | None:
    invoice_code = str(code or "").strip()
    if not invoice_code:
        return None

    try:
        from apps.kiotviet.infrastructure.services.import_invoices_service import (
            ImportInvoicesService,
        )
        from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code

        invoice_payload = get_invoice_by_code(
            invoice_code=invoice_code,
            order=None,
            channel=channel,
        )
        if not isinstance(invoice_payload, dict) or not invoice_payload:
            logger.warning(
                "KiotViet webhook create skip: empty invoice payload (channel_id=%s, code=%s)",
                channel.id,
                invoice_code,
            )
            return None

        # Reuse import service upsert logic to keep mapping behavior identical
        # between pull-invoice command and webhook auto-create path.
        ImportInvoicesService.upsert_invoice(
            invoice=invoice_payload,
            kv_channel=channel,
            dry_run=False,
        )
    except Exception:
        logger.exception(
            "KiotViet webhook auto-create failed (channel_id=%s, code=%s)",
            channel.id,
            invoice_code,
        )
        return None

    created_order = _find_order_for_invoice_event(channel_id=channel.id, code=invoice_code)
    if created_order is None:
        logger.warning(
            "KiotViet webhook auto-create finished but order still not found (channel_id=%s, code=%s)",
            channel.id,
            invoice_code,
        )
    else:
        try:
            from apps.orders.application.services.lead_filter_service import LeadFilterService

            LeadFilterService.register_claim(
                phone_raw=created_order.phone,
                channel=created_order.channel,
                order=created_order,
            )
        except Exception:
            logger.exception(
                "Failed to register phone claim from KiotViet webhook (order_id=%s)",
                created_order.id,
            )
    return created_order


# KiotViet "hủy → xuất lại hóa đơn" sinh mã mới dạng "<mã gốc>.NN" cho cùng đơn hàng.
_REISSUED_INVOICE_CODE_RE = re.compile(r"^(?P<base>.+)\.\d{1,3}$")


def _reattach_reissued_invoice(*, channel: KiotVietChannel, code: str) -> Order | None:
    """Nối hóa đơn xuất lại (``<gốc>.NN``) vào đơn gốc thay vì tạo dòng đơn mới.

    KiotViet cho phép "cập nhật hóa đơn đã hoàn thành" bằng cách hủy bản gốc và sinh
    bản mới cùng mã + hậu tố ``.NN``. Không nối lại thì webhook auto-create sẽ tạo một
    dòng đơn trùng: dòng cũ (mang CRM/assessment/NV gán) rơi vào tab Đã hủy, dòng mới
    trắng thông tin — người dùng thấy như "đơn tự hủy".

    Chỉ nối khi chắc chắn là re-issue: hóa đơn mới còn sống, orderCode khớp (khi cả hai
    phía có), và bản gốc THẬT SỰ đã hủy trên KiotViet (tin DB nếu đã lật; chưa lật —
    webhook ``.NN`` thường đến trước — thì hỏi realtime). Nghi ngờ → trả ``None`` để giữ
    nguyên luồng auto-create.
    """
    match = _REISSUED_INVOICE_CODE_RE.match(code)
    if not match:
        return None
    base_code = match.group("base")

    # Reattach lần 2 (`X` → `X.01` → `X.02`): base của `X.02` là `X`, nhưng sau lần đầu
    # KHÔNG đơn nào còn mang `invoice_code = X` (đơn kế thừa đang là `X.01`, mã gốc nằm
    # trong `reattached_invoice_codes`). Không tra mảng này thì lại rơi vào auto-create
    # → đúng dòng đơn trùng mà cơ chế reattach sinh ra để diệt (audit 2026-07-14 vòng 2).
    #
    # ƯU TIÊN đơn kế thừa (mảng chứa `base_code`) hơn đơn mang `invoice_code = base_code`:
    # prod còn tồn dòng đơn TRÙNG do bug cũ, chúng cũng mang `invoice_code = X` và vừa bị
    # lật huỷ nên `updated_at` rất mới → sắp xếp thuần theo `-updated_at` rất dễ repoint
    # nhằm dòng rác, bỏ đơn thật kẹt ở mã chết (audit 2026-07-14 vòng 3).
    candidate = (
        Order.objects.filter(deleted_at__isnull=True, kiotviet_channel_id=channel.id)
        .filter(reattached_invoice_codes__contains=[base_code])
        .order_by("-updated_at", "-id")
        .first()
    ) or (
        Order.objects.filter(deleted_at__isnull=True, kiotviet_channel_id=channel.id)
        .filter(invoice_code=base_code)
        .order_by("-updated_at", "-id")
        .first()
    )
    if candidate is None:
        return None

    try:
        from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code

        payload = get_invoice_by_code(code, None, channel)
    except Exception:
        logger.exception(
            "KiotViet webhook reattach skip: fetch re-issued invoice failed (channel_id=%s, code=%s)",
            channel.id,
            code,
        )
        return None
    if not isinstance(payload, dict) or not payload:
        return None

    if _resolve_invoice_status_from_payload(payload) == 2:
        return None

    payload_order_code = str(_first_present(payload, ["orderCode", "OrderCode"]) or "").strip()
    candidate_order_code = str(candidate.order_code or "").strip()
    if payload_order_code and candidate_order_code and payload_order_code != candidate_order_code:
        return None

    # Mã đang bị thay thế = mã đơn ĐANG giữ (lần reattach đầu thì trùng `base_code`; lần
    # sau, đơn đang ở `X.01` và bị thay bởi `X.02`) — đó mới là hoá đơn cần xác minh đã huỷ.
    superseded_code = str(candidate.invoice_code or "").strip() or base_code

    already_cancelled_in_db = candidate.invoice_status == 2 or int(candidate.status) == 6
    if not already_cancelled_in_db:
        if _verify_invoice_cancelled_on_channel(channel=channel, code=superseded_code) is not True:
            return None

    invoice_id_raw = _first_present(payload, ["id", "Id", "invoiceId", "InvoiceId"])
    try:
        new_invoice_id = int(str(invoice_id_raw).strip()) if invoice_id_raw not in (None, "") else None
    except (TypeError, ValueError):
        new_invoice_id = None

    update_values: dict = {"invoice_code": code}
    if new_invoice_id is not None:
        update_values["invoice_id"] = new_invoice_id

    # Giữ vết MỌI mã đã chết trong chuỗi. Sau khi repoint, không đơn nào còn mang chúng;
    # sự kiện huỷ đến muộn (webhook `.NN` thường tới trước) hoặc cron import quét lại
    # hoá đơn huỷ trong tháng sẽ không tìm thấy chủ nhân và tạo dòng đơn huỷ TRÙNG.
    #
    # Suy mã chết từ CHUỖI MÃ, không từ trạng thái đơn: `X → X.01 → X.02` mà event của
    # `X.01` không kịp xử lý (đến sau `X.02`, hoặc fetch timeout) thì lúc xử lý `X.02`,
    # `candidate.invoice_code` vẫn là `X` → chỉ lưu `X`, `X.01` không bao giờ vào mảng và
    # về sau vẫn sinh dòng trùng. Vì `X.02` chỉ tồn tại khi `X.01` đã chết, ta suy thẳng:
    # mọi `X.kk` với kk < NN đều đã chết (audit 2026-07-14 vòng 3).
    dead_candidates = [base_code, superseded_code]
    suffix = code[len(base_code) + 1 :]
    if suffix.isdigit():
        width = len(suffix)
        dead_candidates.extend(
            f"{base_code}.{k:0{width}d}" for k in range(1, int(suffix))
        )

    # Khoá đơn rồi ĐỌC LẠI mảng trong lock: giữa lúc đọc `candidate` và lúc ghi có 2 lần
    # gọi API KiotViet (hàng trăm ms). Hai webhook chạy song song (2 worker, KiotViet
    # retry) mà ghi mảng dựng từ bản đọc cũ thì mã chết của nhau bị mất — quay lại đúng
    # bug dòng đơn trùng.
    with transaction.atomic():
        locked = (
            Order.objects.select_for_update()
            .filter(id=candidate.id)
            .only("id", "invoice_code", "reattached_invoice_codes")
            .first()
        )
        if locked is None:
            return None
        previous_codes = list(locked.reattached_invoice_codes or [])
        # Mã đơn đang giữ lúc này mới là mã thật sự bị thay (có thể đã đổi do webhook khác).
        current_code = str(locked.invoice_code or "").strip()
        if current_code and current_code != code:
            dead_candidates.append(current_code)
        if current_code == code:
            return locked  # webhook song song đã repoint xong — không ghi đè
        dead_codes = [
            dead
            for dead in dict.fromkeys(dead_candidates)
            if dead and dead != code and dead not in previous_codes
        ]
        if dead_codes:
            update_values["reattached_invoice_codes"] = [*previous_codes, *dead_codes]
        Order.objects.filter(id=candidate.id).update(**update_values)
    logger.info(
        "KiotViet webhook reattached re-issued invoice "
        "(channel_id=%s, order_id=%s, old_invoice_code=%s, new_invoice_code=%s, new_invoice_id=%s)",
        channel.id,
        candidate.id,
        base_code,
        code,
        new_invoice_id,
    )

    try:
        from apps.kiotviet.infrastructure.services.import_invoices_service import (
            ImportInvoicesService,
        )

        ImportInvoicesService.upsert_invoice(invoice=payload, kv_channel=channel, dry_run=False)
    except Exception:
        # Repoint đã xong — đơn không còn hiển thị "Đã hủy"; field chi tiết sẽ được
        # webhook/đối soát lần sau làm tươi.
        logger.exception(
            "KiotViet webhook reattach: refresh from payload failed (order_id=%s, code=%s)",
            candidate.id,
            code,
        )

    candidate.refresh_from_db()
    return candidate


def _resolve_invoice_status_from_payload(payload: dict) -> int | None:
    if not isinstance(payload, dict):
        return None
    raw = _first_present(
        payload,
        ["status", "Status", "invoiceStatus", "InvoiceStatus"],
    )
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _verify_invoice_cancelled_on_channel(*, channel: KiotVietChannel, code: str) -> bool | None:
    """Xác minh hóa đơn ``code`` có THỰC SỰ bị hủy (status=2) trên ĐÚNG channel này.

    Trả về:
    - ``True``  : KiotViet xác nhận status=2 trên channel này.
    - ``False`` : KiotViet trả về hóa đơn với status != 2 (đơn vẫn bình thường).
    - ``None``  : không xác minh được (404/empty/lỗi gọi API).

    Mã hóa đơn KiotViet chỉ unique theo từng retailer nên webhook void/cancel của một
    retailer khác có thể bị match nhầm vào đơn cùng mã ở channel này. Gọi lại
    ``get_invoice_by_code`` trên đúng channel để chỉ ghi ``invoice_status=2`` khi
    KiotViet xác nhận — nếu code không thuộc retailer này thì API trả 404/khác status.
    """
    invoice_code = str(code or "").strip()
    if not invoice_code:
        return None
    try:
        from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code

        payload = get_invoice_by_code(invoice_code, None, channel)
    except Exception:
        logger.exception(
            "KiotViet webhook verify-cancel failed (channel_id=%s, code=%s)",
            getattr(channel, "id", None),
            invoice_code,
        )
        return None
    status = _resolve_invoice_status_from_payload(payload)
    if status is None:
        return None
    return status == 2


def _publish_webhook_order_updated_event(
    *,
    order: Order,
    previous_status: int | None,
    status_changed: bool,
    shipping_code_changed: bool,
) -> None:
    from apps.orders.contracts import OrderEventContract
    from apps.orders.application.services.order_service_impl import OrderService

    event_kwargs = {
        "order_id": order.id,
        "channel_code": order.channel.code,
        "status": int(order.status),
        "invoice_status": order.invoice_status,
        # Always carry the current invoice_code so the FE can update it in realtime
        # when KiotViet finalizes/changes the invoice after a push. to_payload()
        # strips empty values, so an order without an invoice code won't overwrite
        # anything on the client.
        "invoice_code": str(order.invoice_code or "").strip(),
        "source": "kiotviet.webhook.invoice",
    }
    if shipping_code_changed:
        event_kwargs["shipping_code"] = str(order.shipping_code or "").strip()

    if status_changed and previous_status is not None and int(previous_status) != int(order.status):
        event_kwargs["from_status"] = int(previous_status)
        event_kwargs["to_status"] = int(order.status)

    OrderService.push_event(OrderEventContract.build_order_updated(**event_kwargs))


def process_invoice_webhook(*, channel_id: int, raw_body: bytes, signature: str) -> dict:
    logger.info(
        "KiotViet webhook received (channel_id=%s, body_bytes=%s, has_signature=%s)",
        channel_id,
        len(raw_body or b""),
        bool(str(signature or "").strip()),
    )

    channel = KiotVietChannel.objects.filter(id=channel_id, deleted_at__isnull=True).first()
    if channel is None:
        logger.warning("KiotViet webhook rejected: channel not found (channel_id=%s)", channel_id)
        return {"ok": False, "status_code": 404, "detail": "KiotViet channel not found."}

    secrets, secret_error = _resolve_webhook_signing_secrets()
    if not secrets:
        logger.error(
            "KiotViet webhook rejected: signing secret misconfigured (channel_id=%s, reason=%s)",
            channel_id,
            secret_error,
        )
        return {"ok": False, "status_code": 500, "detail": secret_error}

    is_valid_signature = any(
        verify_webhook_signature(raw_body=raw_body, signature=signature, secret=secret)
        for secret in secrets
    )
    if not is_valid_signature:
        logger.warning(
            "KiotViet webhook rejected: invalid signature (channel_id=%s, candidates=%s)",
            channel_id,
            len(secrets),
        )
        return {"ok": False, "status_code": 401, "detail": "Invalid X-Hub-Signature."}

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        logger.warning("KiotViet webhook rejected: invalid JSON (channel_id=%s)", channel_id)
        return {"ok": False, "status_code": 400, "detail": "Invalid JSON payload."}

    items = _extract_payload_items(payload)
    logger.info(
        "KiotViet webhook payload parsed (channel_id=%s, notifications=%s, items=%s)",
        channel_id,
        len(payload.get("Notifications", []) if isinstance(payload, dict) else []),
        len(items),
    )
    if not items:
        return {
            "ok": True,
            "status_code": 200,
            "detail": "No invoice data in webhook payload.",
            "stats": {"items": 0, "matched_orders": 0, "updated_orders": 0},
        }
    # Mỗi item có thể kéo theo vài call KiotViet ĐỒNG BỘ trong request — body 10MB
    # nhét đủ item là chiếm worker tới timeout (hardening audit vòng 5). KiotViet
    # thực tế gửi lẻ vài item; vượt trần gần như chắc chắn là replay/bom.
    # Đọc settings TRONG hàm để test override được (cache Redis test là shared,
    # không flush giữa các lần chạy).
    max_items = int(getattr(settings, "KIOTVIET_WEBHOOK_MAX_ITEMS", 200))
    dedupe_ttl = int(getattr(settings, "KIOTVIET_WEBHOOK_DEDUPE_TTL", 600))
    if len(items) > max_items:
        logger.warning(
            "KiotViet webhook rejected: too many items (channel_id=%s, items=%s)",
            channel_id,
            len(items),
        )
        return {
            "ok": False,
            "status_code": 400,
            "detail": f"Too many items in one webhook request (>{max_items}).",
        }

    # Dedupe theo Id thông báo: request đã-ký hợp lệ bị replay (retry hạ tầng, hoặc
    # cố ý) sẽ bị nuốt trong TTL thay vì lại gọi N lần KiotViet. Ghi là idempotent
    # nên đây chỉ là chống lãng phí/khuếch đại, không phải chốt đúng-sai. TTL<=0 = tắt.
    notification_id = str(_first_present(payload, ["Id", "id"]) or "").strip()
    if notification_id and dedupe_ttl > 0:
        dedupe_key = f"kv:webhook:seen:{channel_id}:{notification_id}"
        if not cache.add(dedupe_key, 1, timeout=dedupe_ttl):
            logger.info(
                "KiotViet webhook skip: duplicate notification (channel_id=%s, id=%s)",
                channel_id,
                notification_id,
            )
            return {
                "ok": True,
                "status_code": 200,
                "detail": "Duplicate notification — already processed.",
                "stats": {"items": len(items), "matched_orders": 0, "updated_orders": 0},
            }

    matched_orders = 0
    updated_orders = 0
    created_orders = 0
    reattached_orders = 0
    skipped_not_found = 0
    skipped_missing_code = 0
    skipped_superseded = 0

    for item in items:
        code = str(_first_present(item, ["Code", "code"]) or "").strip()
        if not code:
            skipped_missing_code += 1
            logger.info(
                "KiotViet webhook skip: missing invoice code in payload item (channel_id=%s)",
                channel_id,
            )
            continue

        order = _find_order_for_invoice_event(channel_id=channel_id, code=code)
        if order is None:
            order = _reattach_reissued_invoice(channel=channel, code=code)
            if order is not None:
                reattached_orders += 1
        if order is None:
            # Mã đã bị KiotViet huỷ-xuất lại và đơn đã chuyển sang mã `.NN`: sự kiện của
            # mã CHẾT không được tạo dòng đơn huỷ trùng, cũng không được áp dữ liệu lên
            # đơn kế thừa đang sống (audit 2026-07-14 vòng 2).
            superseded_by = _find_order_superseding_code(channel_id=channel_id, code=code)
            if superseded_by is not None:
                skipped_superseded += 1
                logger.info(
                    "KiotViet webhook skip: invoice code superseded by re-issue "
                    "(channel_id=%s, code=%s, order_id=%s, current_invoice_code=%s)",
                    channel_id,
                    code,
                    superseded_by.id,
                    superseded_by.invoice_code,
                )
                continue
            order = _create_order_from_invoice_event(channel=channel, code=code)
            if order is not None:
                created_orders += 1

                logger.info(
                    "KiotViet webhook auto-created order (channel_id=%s, code=%s, order_id=%s)",
                    channel_id,
                    code,
                    order.id,
                )
            else:
                skipped_not_found += 1
                cross_channel_candidates = list(
                    Order.objects.filter(deleted_at__isnull=True)
                    .filter(Q(invoice_code=code) | Q(order_code=code))
                    .values_list("id", "kiotviet_channel_id")[:5]
                )
                logger.info(
                    "KiotViet webhook skip: order not found (channel_id=%s, code=%s, cross_channel_candidates=%s)",
                    channel_id,
                    code,
                    cross_channel_candidates,
                )
                continue

        matched_orders += 1
        logger.info(
            "KiotViet webhook matched order (channel_id=%s, code=%s, order_id=%s, current_status=%s)",
            channel_id,
            code,
            order.id,
            order.status,
        )
        invoice_delivery = _first_present(item, ["InvoiceDelivery", "invoiceDelivery"])
        invoice_delivery = invoice_delivery if isinstance(invoice_delivery, dict) else {}

        delivery_code = str(
            _first_present(invoice_delivery, ["DeliveryCode", "deliveryCode"]) or ""
        ).strip()
        status_raw = _first_present(invoice_delivery, ["Status", "status"])
        invoice_status_raw = _first_present(item, ["Status", "status", "InvoiceStatus", "invoiceStatus"])
        modified_at = _parse_modified_datetime(
            _first_present(item, ["ModifiedDate", "modifiedDate"])
        )

        update_values: dict = {}
        previous_status = order.status
        prev_status_for_event = None
        status_changed = False
        shipping_code_changed = False

        if delivery_code and delivery_code != str(order.shipping_code or "").strip():
            update_values["shipping_code"] = delivery_code
            shipping_code_changed = True

        try:
            next_status = int(status_raw) if status_raw is not None else None
        except (TypeError, ValueError):
            next_status = None

        try:
            next_invoice_status = int(invoice_status_raw) if invoice_status_raw is not None else None
        except (TypeError, ValueError):
            next_invoice_status = None

        # POS sale: invoice hoàn thành (status=1) mà không có invoiceDelivery.status
        # → coi như đã giao (status=3) vì không có vận đơn để theo dõi.
        if next_status is None and next_invoice_status == 1:
            next_status = 3

        # Sự kiện hủy/hoàn (invoice status=2 hoặc delivery status returned/cancelled) phải
        # được xác minh lại trên ĐÚNG channel trước khi ghi đè — tránh webhook void của
        # retailer khác bị match nhầm vào đơn cùng mã (mã hóa đơn chỉ unique theo retailer)
        # khiến đơn đang thành công bị lật thành đã hủy.
        cancelled_delivery_statuses = {5, 6}
        is_cancellation_event = (
            next_invoice_status == 2 or next_status in cancelled_delivery_statuses
        )
        if is_cancellation_event:
            # Sự kiện hủy chỉ được áp vào đơn SỞ HỮU đúng hóa đơn này. Đơn match qua
            # order_code (mã va nhau giữa hai namespace) mà bị lật theo hóa đơn hủy
            # của đơn khác = đơn sống tự hủy oan — verify bên dưới không chặn được ca
            # này vì nó xác minh mã webhook chứ không xác minh quan hệ mã ↔ đơn.
            if str(order.invoice_code or "").strip() != code:
                logger.warning(
                    "KiotViet webhook skip cancellation: matched order does not own invoice "
                    "(channel_id=%s, code=%s, order_id=%s, order_invoice_code=%s)",
                    channel_id,
                    code,
                    order.id,
                    order.invoice_code,
                )
                continue
            verified_cancelled = _verify_invoice_cancelled_on_channel(channel=channel, code=code)
            if verified_cancelled is not True:
                logger.warning(
                    "KiotViet webhook skip cancellation: not confirmed on channel "
                    "(channel_id=%s, code=%s, order_id=%s, next_invoice_status=%s, next_status=%s, verified=%s)",
                    channel_id,
                    code,
                    order.id,
                    next_invoice_status,
                    next_status,
                    verified_cancelled,
                )
                continue

        if next_status is not None and next_status != order.status:
            prev_status_for_event = int(order.status)  # capture before overwrite
            update_values["status"] = next_status
            status_changed = True

        if next_invoice_status is not None and next_invoice_status != order.invoice_status:
            update_values["invoice_status"] = next_invoice_status

        price_raw = _first_present(invoice_delivery, ["Price", "price"])
        try:
            next_shipping_fee = Decimal(str(price_raw)) if price_raw is not None else None
        except (InvalidOperation, TypeError, ValueError):
            next_shipping_fee = None
        if next_shipping_fee is not None and next_shipping_fee != order.shipping_fee:
            update_values["shipping_fee"] = next_shipping_fee

        payment_method = _extract_payment_method(item)
        if payment_method and payment_method != str(order.payment_method or "").strip():
            update_values["payment_method"] = payment_method

        if modified_at is not None:
            update_values["updated_at"] = modified_at

        if update_values:
            Order.objects.filter(id=order.id).update(**update_values)
            updated_orders += 1
            logger.info(
                "KiotViet webhook updated order (order_id=%s, channel_id=%s, fields=%s)",
                order.id,
                channel_id,
                sorted(update_values.keys()),
            )

            if "status" in update_values:
                order.status = update_values["status"]
                if prev_status_for_event is not None:
                    from apps.common.events import event_bus
                    from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

                    if prev_status_for_event != int(update_values["status"]):
                        event_bus.publish_on_commit(
                            OrderStatusChanged(
                                order_id=order.id,
                                old_status=prev_status_for_event,
                                new_status=int(update_values["status"]),
                                source=StatusChangeSource.KIOTVIET_WEBHOOK,
                            )
                        )
            if "invoice_status" in update_values:
                order.invoice_status = update_values["invoice_status"]
            if "shipping_code" in update_values:
                order.shipping_code = update_values["shipping_code"]
            if "shipping_fee" in update_values:
                order.shipping_fee = update_values["shipping_fee"]
            if "payment_method" in update_values:
                order.payment_method = update_values["payment_method"]
            if "updated_at" in update_values:
                order.updated_at = update_values["updated_at"]
            _publish_webhook_order_updated_event(
                order=order,
                previous_status=previous_status,
                status_changed=status_changed,
                shipping_code_changed=shipping_code_changed,
            )
        # Đồng bộ shipping_code / giá / trạng thái sang kênh cũ do cron
        # reconcile_paired_channels (21h hằng ngày) đảm nhiệm — không push realtime từ webhook.

    logger.info(
        "KiotViet webhook finished (channel_id=%s, items=%s, matched_orders=%s, created_orders=%s, "
        "reattached_orders=%s, updated_orders=%s, skipped_missing_code=%s, skipped_not_found=%s, "
        "skipped_superseded=%s)",
        channel_id,
        len(items),
        matched_orders,
        created_orders,
        reattached_orders,
        updated_orders,
        skipped_missing_code,
        skipped_not_found,
        skipped_superseded,
    )

    return {
        "ok": True,
        "status_code": 200,
        "detail": "Webhook processed.",
        "stats": {
            "items": len(items),
            "matched_orders": matched_orders,
            "created_orders": created_orders,
            "reattached_orders": reattached_orders,
            "updated_orders": updated_orders,
        },
    }
