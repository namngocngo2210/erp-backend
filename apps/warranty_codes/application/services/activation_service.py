"""Nghiệp vụ kích hoạt bảo hành từ portal khách hàng."""

from dataclasses import dataclass

from dateutil.relativedelta import relativedelta
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.sysconfig import sysconfig
from apps.warranty_codes.models import (
    PortalChannel,
    ScanResult,
    WarrantyActivation,
    WarrantyCode,
    WarrantyCodeScanLog,
)

_KEYWORD_MAX = 64
_UA_MAX = 255


@dataclass(frozen=True)
class ActivationResult:
    status: str
    activation: WarrantyActivation | None = None
    code: WarrantyCode | None = None


def normalize_keyword(raw: str) -> str:
    return (raw or "").strip().upper()[:_KEYWORD_MAX]


def _log(*, keyword, code, result, channel, ip, user_agent, phone) -> None:
    WarrantyCodeScanLog.objects.create(
        keyword=keyword,
        code=code,
        product_sku=(code.product.sku if code and code.product_id else ""),
        phone=phone,
        result=result,
        channel=channel,
        ip_address=ip,
        user_agent=(user_agent or "")[:_UA_MAX],
    )


def activate(*, keyword: str, phone: str, channel: str, ip: str | None, user_agent: str) -> ActivationResult:
    term = normalize_keyword(keyword)
    if channel not in PortalChannel.values:
        raise ValueError("Kênh không hợp lệ.")

    # Mã ngẫu nhiên (8 ký tự) nằm DƯỚI LỚP CÀO trên tem (cả QR chứa mã cũng bị che) — cào
    # được nghĩa là đang cầm sản phẩm thật, nên bản thân mã đã xác thực quyền sở
    # hữu; `phone` (bản sửa đổi 08/08) KHÔNG dùng để xác thực chủ sở hữu, chỉ là
    # thông tin đăng ký bảo hành điện tử theo SĐT cho CSKH. `serial` in công khai
    # trên tem nên KHÔNG bao giờ dùng để kích hoạt hay xem lại — chỉ khớp `code`,
    # nhập serial luôn rơi vào nhánh not_found bên dưới.
    # Xem docs/warranty-portal-design.md §4.1.
    code = WarrantyCode.objects.select_related("product").filter(code=term).first()

    if code is None:
        _log(keyword=term, code=None, result=ScanResult.NOT_FOUND,
             channel=channel, ip=ip, user_agent=user_agent, phone=phone)
        return ActivationResult(status="not_found")

    existing = WarrantyActivation.objects.filter(code=code).first()
    if existing is not None:
        # Mã đã kích hoạt: không chặn, không ghi đè `phone` — SĐT đăng ký lần đầu
        # (CSKH) là chuẩn. Ai cào được mã cũng coi là chủ sở hữu nên chỉ "xem
        # lại", nhưng SĐT vừa nhập lần này chỉ được GHI VÀO LOG, không cập nhật
        # bản ghi activation.
        _log(keyword=term, code=code, result=ScanResult.VIEWED,
             channel=channel, ip=ip, user_agent=user_agent, phone=phone)
        return ActivationResult(status="viewed", activation=existing, code=code)

    now = timezone.now()
    expires_at = timezone.localdate(now) + relativedelta(months=sysconfig.get("WARRANTY_PORTAL_MONTHS"))
    try:
        with transaction.atomic():
            activation = WarrantyActivation.objects.create(
                code=code,
                phone=phone,
                channel=channel,
                activated_at=now,
                expires_at=expires_at,
                ip_address=ip,
                user_agent=(user_agent or "")[:_UA_MAX],
            )
    except IntegrityError:
        # Hai request cùng lúc — bản ghi kia thắng, xử lý như xem lại (giữ SĐT
        # của bản ghi thắng, không ghi đè bằng SĐT của request thua).
        existing = WarrantyActivation.objects.filter(code=code).first()
        _log(keyword=term, code=code, result=ScanResult.VIEWED,
             channel=channel, ip=ip, user_agent=user_agent, phone=phone)
        return ActivationResult(status="viewed", activation=existing, code=code)

    _log(keyword=term, code=code, result=ScanResult.ACTIVATED,
         channel=channel, ip=ip, user_agent=user_agent, phone=phone)
    return ActivationResult(status="activated", activation=activation, code=code)
