from celery import shared_task
from django.conf import settings
from django.db import OperationalError, transaction
from django.utils import timezone
from decimal import Decimal
from pathlib import Path
import json
import threading
import time
import logging

from apps.orders.contracts import OrderEventContract
from apps.orders.contracts.kiotviet_order_flow_port import kiotviet_order_flow_port
from apps.orders.infrastructure.lark_notifier import send_stock_negative_alert
from apps.orders.metrics import (
    landing_ingest_process_latency_seconds,
    landing_ingest_processed_total,
)
from apps.orders.models import (
    LandingIngestTrace,
    LandingIngestTraceStatus,
    Order,
    OrderAssessment,
    OrderJourneyEvent,
    OrderKiotVietJob,
    OrderKiotVietJobStatus,
    OrderStatus,
)
from apps.orders.application.services.lead_filter_service import DuplicateOrderError
from apps.orders.application.services.order_service_impl import OrderService
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)


def _execute_kiotviet_action(order, action, **kwargs) -> dict:
    """Cầu sang KiotViet qua port (Lô 2) — thay cho ``KiotVietService.execute_action``.

    Muốn giả lập trong test thì patch ``KiotVietService.execute_action`` tại nơi
    class thật sự sống (``apps.kiotviet.infrastructure.services.service_actions``):
    patch thuộc tính trên class có hiệu lực toàn cục nên vẫn ăn vào call qua port.
    """
    return kiotviet_order_flow_port().execute_order_action(order, action, **kwargs)


def _build_error_message(exc: Exception) -> str:
    """Build verbose error message including chained causes.

    Bỏ qua cause có nội dung đã nằm trong message cha (kiểu wrap
    ``Exception(f"[stage=x] {exc}") from exc``) — lặp lại body response
    KiotViet 2 lần vừa nhiễu vừa làm FE không parse được JSON lỗi gốc.
    """
    parts = [f"{exc.__class__.__name__}: {exc}"]
    cause = exc.__cause__
    depth = 0
    while cause is not None and depth < 5:
        cause_text = str(cause)
        if cause_text and cause_text not in " | ".join(parts):
            parts.append(f"cause[{depth + 1}] {cause.__class__.__name__}: {cause_text}")
        cause = cause.__cause__
        depth += 1
    return " | ".join(parts)


_KV_FAILURE_STATUS_FALLBACK = {
    "channel_unavailable": "Kênh KiotViet không khả dụng.",
    "validation_failed": "Dữ liệu đơn không hợp lệ với KiotViet.",
    "rate_limited": "KiotViet đang giới hạn tốc độ, vui lòng thử lại sau.",
    "shipping_service_down": "Dịch vụ vận chuyển của KiotViet đang gián đoạn.",
    "order_not_found": "Không tìm thấy đơn trên KiotViet.",
    "permission_denied": "Tài khoản không có quyền thao tác trên KiotViet.",
    "status_sync_timeout": "Đồng bộ trạng thái KiotViet bị timeout.",
    "status_rejected": "KiotViet từ chối cập nhật trạng thái đơn.",
    "upstream_error": "KiotViet trả lỗi hệ thống.",
}


def _resolve_kv_failure_message(payload: dict, effective_status: str) -> str:
    raw = str(payload.get("message") or "").strip()
    if raw:
        return raw
    fallback = _KV_FAILURE_STATUS_FALLBACK.get(effective_status)
    if fallback:
        return fallback
    return "KiotViet trả lỗi nhưng không kèm mô tả."


def _retry_call(fn, *args, retries: int = 3, base_sleep_seconds: float = 1.0, **kwargs):
    """Simple retry helper for unstable upstream KiotViet calls."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries:
                break
            sleep_seconds = base_sleep_seconds * attempt
            logger.warning(
                "Retrying KiotViet call (attempt=%s/%s, fn=%s, sleep=%ss): %s",
                attempt,
                retries,
                getattr(fn, "__name__", str(fn)),
                sleep_seconds,
                exc,
            )
            if sleep_seconds > 0:
                try:
                    time.sleep(sleep_seconds)
                except NotImplementedError as sleep_exc:
                    logger.warning(
                        "Retry sleep interrupted (fn=%s, attempt=%s/%s): %s. Stop retry wait and surface last error.",
                        getattr(fn, "__name__", str(fn)),
                        attempt,
                        retries,
                        sleep_exc,
                    )
                    break
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected retry state")


def _publish_auto_order_updated_event(order: Order, source: str) -> None:
    OrderService.push_event(
        OrderEventContract.build_order_updated(
            order_id=order.id,
            channel_code=order.channel.code,
            status=order.status,
            invoice_status=order.invoice_status,
            shipping_fee=str(order.shipping_fee),
            source=source,
        )
    )

def _update_trace(trace_id, **fields):
    if not trace_id:
        return
    try:
        LandingIngestTrace.objects.filter(id=trace_id).update(**fields)
    except Exception:
        logger.exception("Failed to update LandingIngestTrace (trace_id=%s)", trace_id)


def _mark_trace_failed(trace_id, error_message: str) -> None:
    """Ghi TASK_FAILED, nhưng KHÔNG đè trace đã có bằng chứng thành công.

    Nếu trace đã gắn ``order_id`` (đơn đã được tạo và commit) hoặc đã ở trạng
    thái CREATED thì giữ nguyên: đè xuống TASK_FAILED sẽ làm idempotency guard
    ở đầu task mất tác dụng và lần autoretry kế tiếp tạo đơn thứ hai.
    """
    if not trace_id:
        return
    try:
        (
            LandingIngestTrace.objects.filter(id=trace_id, order__isnull=True)
            .exclude(status=LandingIngestTraceStatus.CREATED)
            .update(
                status=LandingIngestTraceStatus.TASK_FAILED,
                error_message=error_message,
                updated_at=timezone.now(),
            )
        )
    except Exception:
        logger.exception("Failed to mark LandingIngestTrace failed (trace_id=%s)", trace_id)


@shared_task(
    bind=True,
    ignore_result=True,
    name="apps.orders.tasks.process_landing_ingest",
    # Chỉ autoretry lỗi hạ tầng DB (kết nối rớt/timeout) — ValueError/validation
    # không retry vì đơn hỏng thì retry lại vẫn hỏng.
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def process_landing_ingest(self, payload: dict, trace_id: int | None = None):
    task_id = trace_id
    dry_run = bool(sysconfig.get("ORDERS_LANDING_INGEST_DRY_RUN"))
    logger.info(
        "Go task started: process_landing_ingest (task_id=%s, trace_id=%s, dry_run=%s)",
        task_id,
        trace_id,
        dry_run,
    )

    start_ts = time.monotonic()
    try:
        # Idempotency guard (bằng chứng, không dựa status): nếu trace đã gắn
        # order_id thì đơn đã được tạo và commit ở lần chạy trước → return sớm,
        # tránh double-create khi autoretry chạy lại. Guard này bắt trường hợp
        # OperationalError nổ ở một bước SAU khi trace đã ghi CREATED+order_id
        # (metric/log/bước phụ); kết hợp với _mark_trace_failed không đè trace
        # đã có order_id nên bằng chứng không bị xoá trước khi retry đọc.
        # Giới hạn đã biết: nếu lỗi nổ giữa lúc commit đơn và lúc ghi order_id
        # vào trace (kể cả _update_trace nuốt lỗi ghi CREATED), guard không có
        # bằng chứng để chặn — khi đó chỉ còn dedup gate cùng-ngày của
        # LeadFilterService đỡ.
        if trace_id:
            already_created_order_id = (
                LandingIngestTrace.objects.filter(id=trace_id)
                .values_list("order_id", flat=True)
                .first()
            )
            if already_created_order_id:
                logger.info(
                    "Landing ingest already completed (order_id=%s), skip retry re-run (task_id=%s, trace_id=%s)",
                    already_created_order_id,
                    task_id,
                    trace_id,
                )
                return

        if dry_run:
            # Dry-run: keep the full request trail (task accepted, payload valid,
            # queue healthy) without touching the DB or broadcasting events.
            logger.info(
                "DRY_RUN landing ingest: skip DB insert (task_id=%s, trace_id=%s, phone=%s, product_id=%s, channel_id=%s, url_page=%s, name=%s, raw_address=%s)",
                task_id,
                trace_id,
                payload.get("phone", ""),
                payload.get("product_id", ""),
                payload.get("channel_id", ""),
                payload.get("url_page", ""),
                payload.get("name", ""),
                payload.get("raw_address", ""),
            )
            landing_ingest_processed_total.labels(result="dry_run_skipped").inc()
            _update_trace(
                trace_id,
                status=LandingIngestTraceStatus.DRY_RUN_SKIPPED,
                updated_at=timezone.now(),
            )
            return

        order = OrderService.create_order_from_landing(payload)
        # Ghi bằng chứng thành công (CREATED + order_id) NGAY sau khi tạo đơn,
        # trước log/metric: các bước phụ mà nổ OperationalError thì retry vẫn
        # thấy order_id trên trace và bỏ qua thay vì tạo đơn lần nữa.
        _update_trace(
            trace_id,
            status=LandingIngestTraceStatus.CREATED,
            order_id=order.id,
            updated_at=timezone.now(),
        )
        logger.info(
            "Go task finished: process_landing_ingest (task_id=%s, trace_id=%s, order_id=%s, channel_code=%s)",
            task_id,
            trace_id,
            order.id,
            order.channel.code,
        )
        landing_ingest_processed_total.labels(result="created").inc()
    except DuplicateOrderError as exc:
        # Dedup gate nguyên tử phát hiện đơn trùng (TOCTOU) — bỏ qua việc tạo
        # đơn thứ hai. Đây là kết quả mong đợi, không phải lỗi nên không re-raise.
        logger.info(
            "Landing ingest skipped (duplicate gate): task_id=%s trace_id=%s reason=%s",
            task_id,
            trace_id,
            exc,
        )
        landing_ingest_processed_total.labels(result="rejected_duplicate").inc()
        _update_trace(
            trace_id,
            status=LandingIngestTraceStatus.REJECTED_DUPLICATE,
            error_message=str(exc)[:4000],
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.exception(
            "Go task failed: process_landing_ingest (task_id=%s, trace_id=%s)",
            task_id,
            trace_id,
        )
        landing_ingest_processed_total.labels(result="failed").inc()
        # KHÔNG dùng _update_trace thẳng: nếu đơn đã tạo xong (trace đã gắn
        # order_id / CREATED) mà một bước sau đó nổ lỗi, đè TASK_FAILED sẽ xoá
        # bằng chứng và lần retry kế tiếp tạo đơn trùng.
        _mark_trace_failed(trace_id, f"{exc.__class__.__name__}: {exc}"[:4000])
        raise
    finally:
        landing_ingest_process_latency_seconds.observe(time.monotonic() - start_ts)


def _apply_shipping_type_after_job(job: OrderKiotVietJob, order: Order) -> None:
    """Gắn cờ hình thức xuất hàng theo thao tác vừa thành công. KHÔNG ghi sổ tồn.

    Ba mốc gom về một chỗ vì chúng là cùng một câu hỏi: đơn này rời kho bằng đường nào.

    `direct` KHÔNG trừ tồn ở đây. Đơn quick-sale chỉ có hoá đơn KiotViet và cái nhãn
    này; hàng rời kho qua một trong hai cửa của app inventory:
    (1) kho quét MÃ HOÁ ĐƠN ở màn quét xuất (`pending_scan_service`), hoặc
    (2) người dùng bấm nút "Xuất kho" ở chi tiết đơn (`manual_dispatch_service`).
    Tồn phồng trong quãng chờ là HÀNH VI CÓ CHỦ ĐÍCH, không phải job hỏng.

    Hàm này KHÔNG đọc `direct_dispatch_deferred` — tầng API
    (`order_action_view._QUICK_SALE_ACTIONS`) ghi cờ True cho mọi đơn quick-sale nên rẽ
    nhánh theo cờ ở đây chỉ là đường chết. Cột cờ vẫn PHẢI được ghi ở tầng API: nó phân
    biệt đơn direct CŨ (cờ False, tạo trước 28/08/2026, đã bị trừ tồn ngay) với đơn mới,
    và `apps.inventory.subscribers._is_direct_sale` đọc nó để chọn luật huỷ. Xem
    docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
    """
    from apps.orders.models import ShippingType

    if job.action == "create_invoice" and str(order.invoice_code or "").strip():
        if (
            order.shipping_type == ShippingType.SHIPMENT
            or str(order.order_code or "").strip()
            or str(order.shipping_code or "").strip()
        ):
            # Phòng thủ chiều sâu: tầng API (Task 4) đã chặn quick-sale trên đơn có
            # order_code, nhưng nếu vẫn lọt tới đây thì phải đọc SỰ THẬT tươi
            # (order_code/shipping_code), KHÔNG chỉ kiểm cờ shipping_type — mọi đơn tạo
            # trước khi tính năng này deploy đều mang cờ NULL, nên nếu chỉ kiểm cờ thì
            # lớp phòng thủ này không chặn được BẤT KỲ đơn nào trong dữ liệu hiện có.
            # Đơn đã có order_code (đã push sang KiotViet) hoặc shipping_code (đã lên
            # vận đơn KShip) là đã có bằng chứng rời kho qua đường vận chuyển, bất kể cờ
            # đang là gì — lật sang `direct` sẽ cho phép đơn đi đường vận chuyển bị xuất
            # qua kênh sai (quét MÃ HOÁ ĐƠN hoặc nút "Xuất kho" thủ công) thay vì luồng
            # quét mã vận đơn, và nhãn direct còn làm sai cả báo cáo lẫn đường huỷ (đơn
            # direct cũ mang cờ deferred=False được cộng trả tồn NGAY khi huỷ, trong khi
            # hàng thật đang ở đơn vị vận chuyển). Không lật cờ; giữ nguyên giá trị hiện tại.
            logger.warning(
                "Bỏ qua gắn cờ direct cho đơn %s: đã rời kho qua đường vận chuyển "
                "(shipping_type=%s, order_code=%s, shipping_code=%s)",
                order.id, order.shipping_type, order.order_code, order.shipping_code,
            )
            return
        # CHỈ gắn nhãn: không ghi sổ, không gắn `dispatched`. Hàng rời kho qua một
        # trong hai cửa của app inventory (quét mã hoá đơn, hoặc nút "Xuất kho" ở
        # chi tiết đơn). Nhãn direct thì phải gắn ngay, vì nhánh huỷ đơn và bộ lọc
        # trang đơn đọc nhãn này để chọn đường.
        if order.shipping_type != ShippingType.DIRECT:
            order.shipping_type = ShippingType.DIRECT
            order.save(update_fields=["shipping_type", "updated_at"])
        return

    if order.shipping_type is not None:
        # Không ghi đè: đơn đã direct (đã trừ tồn) mà bị push lại sẽ mang nhãn shipment
        # trong khi sổ ghi theo luật direct — nhãn nói dối sổ.
        return
    if job.action == "push" and str(order.order_code or "").strip():
        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type", "updated_at"])
    elif job.action == "create_kship_delivery" and str(order.shipping_code or "").strip():
        # Tạo vận đơn KShip thành công cũng là "rời kho qua đường vận chuyển" — cùng
        # một sự thật với push, chỉ khác cửa vào (KShip thay vì đẩy hoá đơn KiotViet).
        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type", "updated_at"])


_kv_location_lock = threading.Lock()
_KV_LOCATION_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "kiotviet_address_final.json"


def _maybe_update_kiotviet_location_data(order: Order) -> None:
    """Add new kiotviet_location / kiotviet_ward to the static JSON file if not already present."""
    location = (order.kiotviet_location or "").strip()
    ward = (order.kiotviet_ward or "").strip()
    if not location:
        return

    with _kv_location_lock:
        try:
            data: dict = json.loads(_KV_LOCATION_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        wards = data.get(location)
        if wards is None:
            # New location
            data[location] = [ward] if ward else []
            changed = True
        elif ward and ward not in wards:
            wards.append(ward)
            changed = True
        else:
            changed = False

        if changed:
            _KV_LOCATION_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.info("Updated kiotviet_address_final.json: location=%s, ward=%s", location, ward)


@shared_task(
    bind=True,
    ignore_result=True,
    name="apps.orders.tasks.process_kiotviet_job",
    rate_limit=getattr(settings, "KIOTVIET_TASK_RATE_LIMIT", "12/m"),
    # PHẢI override limit toàn cục (25s/30s): task gọi nhiều HTTP KiotViet + retry-sleep.
    # Bị giết ở 25s là chết đúng khoảng "hoá đơn ĐÃ tạo bên KiotViet nhưng CHƯA ghi
    # invoice_code về Order" → job FAILED, user bấm đẩy lại → hoá đơn TRÙNG trên
    # KiotViet (audit 2026-07-14 vòng 5 — nghi là nguồn của vụ 'đơn tự hủy').
    #
    # 2026-08-18: nới 120→180 cho create_kship_delivery. Ngân sách xấu nhất của nhánh
    # này giờ là bước 1 (≤30s) + create-order (60s) + dò lại mã vận đơn sau timeout
    # (≤55s) = 145s. Bị giết giữa bước dò lại là mất đúng thứ bước đó sinh ra để cứu.
    soft_time_limit=180,
    time_limit=210,
)
def process_kiotviet_job(self, job_id: int):
    task_id = job_id
    logger.info("Go task started: process_kiotviet_job (task_id=%s, job_id=%s)", task_id, job_id)

    with transaction.atomic():
        job = (
            OrderKiotVietJob.objects.select_for_update()
            .get(id=job_id)
        )
        logger.debug(
            "Job locked (task_id=%s, job_id=%s, order_id=%s, action=%s, status=%s, attempt_count=%s)",
            task_id,
            job.id,
            job.order_id,
            job.action,
            job.status,
            job.attempt_count,
        )
        if job.status in {OrderKiotVietJobStatus.SUCCESS, OrderKiotVietJobStatus.FAILED}:
            logger.info(
                "Job already terminal, skip processing (task_id=%s, job_id=%s, status=%s)",
                task_id,
                job.id,
                job.status,
            )
            return

        previous_status = job.status
        job.status = OrderKiotVietJobStatus.RUNNING
        job.error_message = ""
        job.started_at = timezone.now()
        job.attempt_count = (job.attempt_count or 0) + 1
        job.save(update_fields=["status", "error_message", "started_at", "attempt_count", "updated_at"])
        order_id = job.order_id
        logger.info(
            "Job moved to RUNNING (task_id=%s, job_id=%s, order_id=%s, action=%s, from_status=%s, to_status=%s, attempt_count=%s)",
            task_id,
            job.id,
            order_id,
            job.action,
            previous_status,
            job.status,
            job.attempt_count,
        )

    started_event = OrderEventContract.build_order_updated(
        order_id=order_id,
        is_kiotviet_processing=True,
        latest_kiotviet_job_status=OrderKiotVietJobStatus.RUNNING,
        latest_kiotviet_job_action=job.action,
        requested_by_id=job.requested_by_id,
        source="orders.tasks.process_kiotviet_job.started",
    )
    logger.debug("Dispatch start event (task_id=%s, job_id=%s, event=%s)", task_id, job_id, started_event)
    OrderService.push_event(started_event)

    order = None
    try:
        order = Order.objects.select_related("channel").get(id=order_id)
        logger.debug(
            "Loaded order for execution (task_id=%s, job_id=%s, order_id=%s, channel_code=%s, status=%s)",
            task_id,
            job_id,
            order.id,
            order.channel.code,
            order.status,
        )

        # Chốt cuối trước khi gọi KiotViet: đơn bán nhanh phải có kho xuất vì nó trừ tồn
        # ngay sau khi hoá đơn được tạo. View đã chặn lúc bấm nút, nhưng kho có thể bị gỡ
        # trong lúc job nằm hàng đợi — đọc DB tươi ở đây mới bịt được khe đó. Task không
        # cấu hình autoretry nên lỗi này không lặp vô hạn.
        if job.action == "create_invoice" and not order.fulfillment_warehouse_id:
            raise ValueError(
                "Đơn bán trực tiếp chưa có kho xuất — không tạo hoá đơn để tránh bán mà không trừ tồn."
            )

        execution = _execute_kiotviet_action(
            order,
            job.action,
            actor_id=job.requested_by_id,
            provider_code=job.kship_provider_code,
            service_code=job.kship_service_code,
            kship_note=job.kship_note or "",
            shipper_note=job.kship_shipper_note or "",
            service_extra=job.kship_service_extra or {},
            sender_address_id=job.kship_sender_address_id,
        )
        logger.debug("KiotViet execution result (task_id=%s, job_id=%s, execution=%s)", task_id, job_id, execution)
        execution_status = str(execution.get("status", "")).lower()
        payload = execution.get("payload", {}) or {}
        if payload.get("old_sync") is not None or payload.get("old_channel_sync") is not None:
            logger.info(
                "KiotViet old sync trace (task_id=%s, job_id=%s, order_id=%s, action=%s, old_sync=%s, old_channel_sync=%s)",
                task_id,
                job_id,
                order.id,
                job.action,
                payload.get("old_sync"),
                payload.get("old_channel_sync"),
            )
        payload_status = str(payload.get("status", "")).lower()
        effective_status = execution_status or payload_status

        is_failed_payload = effective_status in {
            "failed",
            "channel_unavailable",
            "validation_failed",
            "rate_limited",
            "shipping_service_down",
            "order_not_found",
            "permission_denied",
            "status_sync_timeout",
            "status_rejected",
            "upstream_error",
        }

        with transaction.atomic():
            job = OrderKiotVietJob.objects.select_for_update().get(id=job_id)
            job.status = OrderKiotVietJobStatus.FAILED if is_failed_payload else OrderKiotVietJobStatus.SUCCESS
            job.error_message = "" if not is_failed_payload else _resolve_kv_failure_message(payload, effective_status)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
            logger.info(
                "Job finished (task_id=%s, job_id=%s, order_id=%s, action=%s, status=%s, effective_status=%s, error_message=%s)",
                task_id,
                job.id,
                job.order_id,
                job.action,
                job.status,
                effective_status,
                job.error_message,
            )

        # Reload order to get updated values after execute_action
        order.refresh_from_db()

        # Auto-add new kiotviet location/ward to static data after successful push
        if job.status == OrderKiotVietJobStatus.SUCCESS and job.action in ("push", "create_invoice"):
            try:
                _maybe_update_kiotviet_location_data(order)
            except Exception:
                logger.exception("Failed to update kiotviet_address_final.json (job_id=%s, order_id=%s)", job_id, order_id)

        if job.status == OrderKiotVietJobStatus.SUCCESS:
            # KHÔNG để lỗi ở đây bay lên except Exception phía dưới: hoá đơn KiotViet
            # (HD-xxx) đã tạo THẬT và job đã commit SUCCESS ở khối transaction bên trên —
            # nếu một exception ở đây (đơn bị huỷ chen ngang, mất kho, deadlock...) ghi
            # đè job thành FAILED, UI báo "tạo hoá đơn thất bại" trong khi hoá đơn đang
            # SỐNG trên KiotViet → nhân viên bấm lại → hoá đơn TRÙNG (đúng bẫy audit
            # 2026-07-14 mà rate_limit/soft_time_limit ở decorator phía trên đã né).
            # Theo đúng khuôn hai khối hậu xử lý hàng xóm (location data, auto-flip
            # sales_status): nuốt lỗi, log rõ, giữ job SUCCESS.
            try:
                _apply_shipping_type_after_job(job, order)
            except Exception:
                logger.exception(
                    "Failed to apply shipping_type/issue stock after job success — "
                    "STOCK CHƯA ĐƯỢC TRỪ, cần xử lý tay (job_id=%s, order_id=%s, action=%s, invoice_code=%s)",
                    job.id,
                    order.id,
                    job.action,
                    order.invoice_code,
                )
                # Lỗi hậu xử lý này là rò tồn IM LẶNG duy nhất không có lưới hứng nào
                # khác bắt được: RevivedStockAuditService chỉ soi đơn CÓ bút toán
                # SALE_REVERSAL (đơn này không có bút toán nào nên vô hình với nó), job
                # không tự chạy lại được (guard terminal chặn redeliver), và đơn lẫn
                # trong tập shipping_type IS NULL gồm toàn bộ đơn lịch sử nên không lọc
                # ra được. Bọc try/except RIÊNG: lỗi mạng gọi Lark không được phép lật
                # job đang SUCCESS thành FAILED (đúng thứ khối try/except ngoài sinh ra
                # để tránh).
                try:
                    send_stock_negative_alert(
                        f"⚠️ Đơn #{order.id} (hoá đơn {order.invoice_code or '?'}) đã tạo "
                        "hoá đơn KiotViet THÀNH CÔNG nhưng hậu xử lý lỗi nên đơn chưa được "
                        f"gắn nhãn hình thức xuất hàng (job_id={job.id}, xem log để biết "
                        "nguyên nhân). Kiểm tra đơn rồi dùng nút \"Xuất kho\" ở chi tiết "
                        "đơn khi hàng thật rời kho."
                    )
                except Exception:
                    logger.exception(
                        "Failed to send Lark stock alert for post-processing failure "
                        "(job_id=%s, order_id=%s)",
                        job.id,
                        order.id,
                    )
            try:
                # Dù nhánh trên có lỗi hay không: rollback (nếu có) chỉ hoàn tác DB,
                # không hoàn tác instance Python đang cầm trong tay — nạp lại DB để phần
                # code phía dưới (event, auto-flip) không đọc nhầm giá trị dở dang trong
                # bộ nhớ. Bọc try riêng: nếu refresh_from_db() cũng nổ (đơn bị xoá chen
                # ngang), lỗi phải bị nuốt ở ĐÂY chứ không được rơi xuống except ngoài —
                # rơi xuống đó sẽ lật job SUCCESS thành FAILED, đúng thứ khối này (và
                # khối try/except ngay trên) sinh ra để tránh.
                order.refresh_from_db()
            except Exception:
                logger.exception(
                    "Failed to refresh order after shipping_type post-processing "
                    "(job_id=%s, order_id=%s)",
                    job.id,
                    order.id,
                )

        # Auto-flip sales_status to "created_order" when:
        #   - kship delivery succeeded and shipping_code is present, or
        #   - quick-sale (create_invoice) succeeded and invoice_code is present.
        broadcast_sales_status: str | None = None
        auto_flip_source: str | None = None
        if job.status == OrderKiotVietJobStatus.SUCCESS:
            if job.action == "create_kship_delivery" and str(order.shipping_code or "").strip():
                auto_flip_source = "kship_delivery_success"
            elif job.action == "create_invoice" and str(order.invoice_code or "").strip():
                auto_flip_source = "quick_sale_invoice_created"
        if auto_flip_source is not None:
            try:
                with transaction.atomic():
                    assessment, _ = OrderAssessment.objects.select_for_update().get_or_create(order=order)
                    if assessment.sales_status_code != "created_order":
                        previous_code = assessment.sales_status_code
                        assessment.sales_status_code = "created_order"
                        assessment.save(update_fields=["sales_status_code", "updated_at"])
                        OrderJourneyEvent.objects.create(
                            order=order,
                            event_type="sales_status_changed",
                            from_code=previous_code or None,
                            to_code="created_order",
                            actor_id=job.requested_by_id,  # flip là hệ quả thao tác của người bấm job
                            payload_json={
                                "field": "sales_status_code",
                                "source": auto_flip_source,
                            },
                        )
                        logger.info(
                            "Auto set sales_status=created_order (order_id=%s, source=%s, previous=%s)",
                            order.id,
                            auto_flip_source,
                            previous_code,
                        )
                    broadcast_sales_status = "created_order"
            except Exception:
                logger.exception(
                    "Failed to auto set sales_status=created_order (order_id=%s, source=%s)",
                    order.id,
                    auto_flip_source,
                )

        finished_event = OrderEventContract.build_order_updated(
            order_id=order_id,
            channel_code=order.channel.code,
            status=order.status,
            invoice_status=order.invoice_status,
            shipping_fee=str(order.shipping_fee) if order.shipping_fee is not None else None,
            invoice_code=order.invoice_code,
            shipping_code=order.shipping_code,
            is_kiotviet_processing=False,
            latest_kiotviet_job_status=job.status,
            latest_kiotviet_job_action=job.action,
            latest_kiotviet_job_error=job.error_message,
            requested_by_id=job.requested_by_id,
            sales_status_code=broadcast_sales_status,
            source="orders.tasks.process_kiotviet_job.finished",
        )
        logger.debug("Dispatch finished event (task_id=%s, job_id=%s, event=%s)", task_id, job_id, finished_event)
        OrderService.push_event(finished_event)
    except Exception as exc:
        logger.exception(
            "Job execution failed with exception (task_id=%s, job_id=%s, order_id=%s): %s",
            task_id,
            job_id,
            order_id,
            exc,
        )
        with transaction.atomic():
            job = OrderKiotVietJob.objects.select_for_update().get(id=job_id)
            job.status = OrderKiotVietJobStatus.FAILED
            job.error_message = _build_error_message(exc)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
            logger.info(
                "Job moved to FAILED due to exception (task_id=%s, job_id=%s, order_id=%s, action=%s, error_message=%s)",
                task_id,
                job.id,
                job.order_id,
                job.action,
                job.error_message,
            )
        # Reload order to get any updated values even if execution failed.
        # `order` may still be None if the failure happened while loading it
        # (deleted row, or schema/version skew like a dropped column) — degrade
        # to a minimal event instead of masking the real error with an
        # UnboundLocalError.
        if order is not None:
            try:
                order.refresh_from_db()
            except Exception:
                logger.exception(
                    "Failed to refresh order in failure handler (job_id=%s, order_id=%s)",
                    job_id,
                    order_id,
                )
        failed_event = OrderEventContract.build_order_updated(
            order_id=order_id,
            channel_code=order.channel.code if order is not None else None,
            status=order.status if order is not None else None,
            invoice_status=order.invoice_status if order is not None else None,
            shipping_fee=(
                str(order.shipping_fee) if order is not None and order.shipping_fee is not None else None
            ),
            invoice_code=order.invoice_code if order is not None else None,
            shipping_code=order.shipping_code if order is not None else None,
            is_kiotviet_processing=False,
            latest_kiotviet_job_status=OrderKiotVietJobStatus.FAILED,
            latest_kiotviet_job_action=job.action,
            latest_kiotviet_job_error=job.error_message,
            requested_by_id=job.requested_by_id,
            source="orders.tasks.process_kiotviet_job.failed",
        )
        logger.debug("Dispatch failed event (task_id=%s, job_id=%s, event=%s)", task_id, job_id, failed_event)
        OrderService.push_event(failed_event)
        raise
