"""Application service cho ingest đơn landing (webhook).

Logic nghiệp vụ kéo từ transport view ``ingest_landing_order`` (audit 2026-07
mục 4.5) — view chỉ còn xác thực token, parse body và map kết quả về Response.
"""

import logging
from dataclasses import dataclass

from rest_framework import status

from apps.channels_core.models import Channel
from apps.dispatch import dispatch
from apps.orders.application.ports import validate_landing_payload
from apps.orders.application.services.lead_filter_service import (
    FilterAction,
    LeadFilterService,
)
from apps.orders.import_excel import _normalize_channel_code_or_original
from apps.orders.metrics import (
    landing_ingest_enqueued_total,
    landing_ingest_staff_hint_total,
    landing_ingest_validated_total,
)
from apps.orders.models import IngestSource, LandingIngestTrace, LandingIngestTraceStatus
from apps.orders.product_parser import normalize_landing_product_id
from apps.catalog.models import Product
from apps.sysconfig import sysconfig
from apps.users.models import User

logger = logging.getLogger(__name__)

# Map quyết định lọc lead → trạng thái trace (dùng chung cho landing + lark ingest).
REJECT_STATUS_MAP = {
    FilterAction.REJECT_INVALID_PHONE: LandingIngestTraceStatus.VALIDATION_FAILED,
    FilterAction.REJECT_BLOCKED: LandingIngestTraceStatus.REJECTED_BLOCKED,
    FilterAction.REJECT_DUPLICATE: LandingIngestTraceStatus.REJECTED_DUPLICATE,
}


@dataclass
class LandingIngestResult:
    """Kết quả ingest — body + HTTP status để view trả về nguyên vẹn."""

    body: dict
    status: int


def resolve_landing_product_ids(product_id_raw) -> list[int]:
    """Resolve landing payload product_id (int / "3.681" / SKU / "SKU1,SKU2") to PKs.

    Mirrors lookup rules in OrderServiceCommands.create_order_from_landing so the
    duplicate filter can distinguish ingests for the same phone but different products.
    """
    product_id_raw = normalize_landing_product_id(product_id_raw)
    if product_id_raw is None:
        return []
    if isinstance(product_id_raw, int):
        product = Product.objects.filter(id=product_id_raw, deleted_at__isnull=True).only("id").first()
        return [product.id] if product else []
    if isinstance(product_id_raw, str):
        skus = [chunk.strip() for chunk in product_id_raw.split(",") if chunk.strip()]
        if not skus:
            return []
        return list(
            Product.objects.filter(sku__in=skus, deleted_at__isnull=True).values_list("id", flat=True)
        )
    return []


def resolve_channel_ref(raw):
    """Resolve tham chiếu kênh (int id hoặc text) → Channel.

    int / chuỗi toàn số → tra theo id; text khác → normalize sang code rồi tra theo code.
    Trả None nếu không tìm thấy.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return Channel.objects.filter(id=raw, deleted_at__isnull=True).first()
    text = str(raw).strip()
    if not text:
        return None
    try:
        pk = int(text)
    except ValueError:
        code = _normalize_channel_code_or_original(text)
        return Channel.objects.filter(code=code, deleted_at__isnull=True).first()
    return Channel.objects.filter(id=pk, deleted_at__isnull=True).first()


def resolve_ingest_staff_id(username) -> int | None:
    """Resolve `staff_username` trong payload ingest → id user, hoặc None.

    Chỉ nhận tài khoản CÒN dùng được: `is_active`, chưa xoá mềm, và KHÔNG mang
    role hệ thống (admin). Endpoint này là `AllowAny` + token dùng chung, nên
    "admin" là username dễ đoán nhất — thiếu điều kiện loại role hệ thống thì
    một payload giả có thể gán đơn cho Administrator, và vì đơn đã có staff_id
    nên cả `assign_if_unassigned` lẫn cron sweep đều bỏ qua, đơn nằm ngoài mọi
    lưới đỡ. Cùng luật với `import_excel.py` và `staff_id` field của
    `OrderInlineUpdateSerializer`. Tài khoản sale đã nghỉ coi như không khớp để
    đơn rơi về bộ chia tự động, thay vì nằm chết ở một tài khoản không ai đăng
    nhập.
    """
    text = str(username or "").strip()
    if not text:
        return None
    user = (
        User.objects.filter(username__iexact=text, is_active=True, deleted_at__isnull=True)
        .exclude(role_assignments__role__is_system=True)
        .distinct()
        .only("id")
        .first()
    )
    return user.id if user else None


class OrderIngestService:
    @staticmethod
    def ingest_landing_order(
        raw_body: dict,
        request_data,
        *,
        request_path: str = "",
        content_type: str = "",
    ) -> LandingIngestResult:
        """Xử lý một payload landing ingest đã qua xác thực token.

        Giữ nguyên flow cũ của view: tạo trace → validate → resolve kênh →
        lọc lead → enqueue task. Trả (body, status) để view map về Response.
        """
        dry_run = bool(sysconfig.get("ORDERS_LANDING_INGEST_DRY_RUN"))
        trace = LandingIngestTrace.objects.create(
            status=LandingIngestTraceStatus.RECEIVED,
            source=IngestSource.LANDING,
            is_dry_run=dry_run,
            payload=raw_body,
            phone=str(raw_body.get("phone", ""))[:30],
            name=str(raw_body.get("name", ""))[:255],
            raw_address=str(raw_body.get("address", "") or raw_body.get("raw_address", "")),
            product_code=str(raw_body.get("product_id", ""))[:100],
            url_page=str(raw_body.get("link", "") or raw_body.get("url_page", ""))[:1024],
        )
        logger.info(
            "Landing ingest called: trace_id=%s path=%s content_type=%s dry_run=%s body=%s",
            trace.id,
            request_path,
            content_type,
            dry_run,
            raw_body,
        )

        # Validate qua port (transport cắm LandingOrderSerializer lúc ready()) —
        # application không import serializer transport (audit 2026-09-23 T3/P2-2).
        validation = validate_landing_payload(request_data)
        if not validation.is_valid:
            landing_ingest_validated_total.labels(result="invalid").inc()
            trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
            trace.validation_errors = validation.errors
            trace.save(update_fields=["status", "validation_errors", "updated_at"])
            logger.warning(
                "Landing ingest validation failed: trace_id=%s errors=%s",
                trace.id,
                validation.errors,
            )
            return LandingIngestResult(validation.errors, status.HTTP_400_BAD_REQUEST)
        landing_ingest_validated_total.labels(result="valid").inc()
        payload = validation.validated_data
        trace.phone = str(payload.get("phone", ""))[:30]
        trace.name = str(payload.get("name", ""))[:255]
        trace.raw_address = str(payload.get("raw_address", ""))
        trace.product_code = str(payload.get("product_id", "") or "")[:100]
        trace.url_page = str(payload.get("url_page", ""))[:1024]
        trace.save(update_fields=["phone", "name", "raw_address", "product_code", "url_page", "updated_at"])
        logger.debug(
            "Landing ingest validated payload: trace_id=%s name=%s phone=%s raw_address=%s product_id=%s channel_id=%s url_page=%s",
            trace.id,
            payload.get("name", ""),
            payload.get("phone", ""),
            payload.get("raw_address", ""),
            payload.get("product_id", ""),
            payload.get("channel_id", ""),
            payload.get("url_page", ""),
        )

        channel_id_raw = payload.get("channel_id")
        channel = resolve_channel_ref(channel_id_raw)
        if channel is None:
            trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
            trace.error_message = f"Kênh bán không tồn tại: channel_id={channel_id_raw}"[:4000]
            trace.save(update_fields=["status", "error_message", "updated_at"])
            return LandingIngestResult(
                {"detail": f"Kênh bán không tồn tại: channel_id={channel_id_raw}"},
                status.HTTP_400_BAD_REQUEST,
            )
        # Chuẩn hoá về id để service downstream dùng nguyên logic resolve theo id.
        payload["channel_id"] = channel.id

        product_ids = resolve_landing_product_ids(payload.get("product_id"))
        decision = LeadFilterService.evaluate(payload.get("phone", ""), channel, product_ids=product_ids)
        if not decision.accepted:
            trace.status = REJECT_STATUS_MAP.get(decision.action, LandingIngestTraceStatus.VALIDATION_FAILED)
            trace.error_message = decision.reason[:4000]
            trace.save(update_fields=["status", "error_message", "updated_at"])
            logger.info(
                "Landing ingest rejected: trace_id=%s action=%s reason=%s phone=%s channel_code=%s",
                trace.id,
                decision.action.value,
                decision.reason,
                decision.phone_normalized,
                channel.code,
            )
            http_status = (
                status.HTTP_400_BAD_REQUEST
                if decision.action == FilterAction.REJECT_INVALID_PHONE
                else status.HTTP_409_CONFLICT
            )
            return LandingIngestResult(
                {"detail": decision.reason, **decision.to_response_dict(), "trace_id": trace.id},
                http_status,
            )

        # Chỉ định sale (chỉ có mặt với đơn nguồn khác landing — serializer đã gate).
        # Resolve NGAY ở tầng request thay vì trong task: payload qua queue phải là
        # JSON thuần, và typo tên sale cần hiện trong log request kèm trace_id.
        # Đặt SAU bộ lọc lead (thay vì ngay sau resolve kênh): resolve trước lọc
        # sẽ tính cả những payload sau đó bị 409 (trùng/blocklist)/400 (sđt sai)
        # hoặc dry-run vào metric — mà metric này là đường duy nhất ops nhìn thấy
        # tính năng có chạy hay không, và mỗi lần tra User tốn một query vô ích
        # cho lead sẽ bị dedup ngay sau đó.
        staff_username = payload.pop("staff_username", "")
        if staff_username:
            staff_id = resolve_ingest_staff_id(staff_username)
            if staff_id is None:
                # CỐ Ý lenient (đồng bộ lark-ingest): sai một cái tên không đáng
                # để mất lead, đơn rơi về bộ chia tự động. Metric là đường duy
                # nhất để ops thấy bên gửi đang gõ sai tên hàng loạt.
                landing_ingest_staff_hint_total.labels(result="unknown_username").inc()
                logger.warning(
                    "Landing ingest staff_username không khớp tài khoản nào: trace_id=%s username=%s",
                    trace.id,
                    staff_username,
                )
            else:
                landing_ingest_staff_hint_total.labels(result="assigned").inc()
                payload["staff_id"] = staff_id

        try:
            task = dispatch(
                "orders.landing_ingest",
                queue="orders",
                payload=payload,
                trace_id=trace.id,
            )
        except Exception as exc:
            landing_ingest_enqueued_total.labels(result="failed").inc()
            trace.status = LandingIngestTraceStatus.ENQUEUE_FAILED
            trace.error_message = f"{exc.__class__.__name__}: {exc}"
            trace.save(update_fields=["status", "error_message", "updated_at"])
            logger.exception("Landing ingest enqueue failed: trace_id=%s", trace.id)
            return LandingIngestResult(
                {"detail": "Không thể xếp hàng xử lý đơn hàng lúc này."},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        landing_ingest_enqueued_total.labels(result="success").inc()
        task_id = str(getattr(task, "id", "") or "")[:64]
        trace.task_id = task_id
        trace.save(update_fields=["task_id", "updated_at"])
        logger.info(
            "Landing ingest queued: trace_id=%s task_id=%s dry_run=%s",
            trace.id,
            task_id,
            dry_run,
        )
        return LandingIngestResult(
            {"accepted": True, "task_id": task_id, "trace_id": trace.id, "dry_run": dry_run},
            status.HTTP_200_OK,
        )
