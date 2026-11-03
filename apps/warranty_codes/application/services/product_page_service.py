"""Nghiệp vụ trang sản phẩm public `/sp/<mã>`.

Ba việc: tra mã, đếm lượt quét (dedupe), và dựng payload đã lọc theo cờ hiển thị.
Xem docs/product-scan-page-design.md §3.3, §4.1.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from apps.sysconfig import sysconfig
from apps.warranty_codes.application.services.activation_service import _log, normalize_keyword
from apps.warranty_codes.models import ScanResult, WarrantyActivation, WarrantyCode

logger = logging.getLogger(__name__)

_SCAN_SESSION_TTL = 24 * 60 * 60


@dataclass(frozen=True)
class ProductPageResult:
    found: bool
    code: WarrantyCode | None = None


def register_scan(code: WarrantyCode, ip: str | None) -> None:
    """Tăng bộ đếm nếu đây là phiên mới (mã + IP + ngày).

    Fail-open khi cache hỏng: trang sản phẩm phải mở được kể cả lúc Redis chết,
    thà đếm thiếu còn hơn chặn khách đang cầm sản phẩm thật.
    """
    ip_hash = hashlib.sha256((ip or "unknown").encode()).hexdigest()[:16]
    key = f"sp:seen:{code.code}:{ip_hash}:{timezone.localdate().isoformat()}"
    try:
        is_new_session = cache.add(key, 1, _SCAN_SESSION_TTL)
    except Exception:  # noqa: BLE001 — cache hỏng không được làm sập trang
        logger.warning("Không đọc được cache khi đếm lượt quét mã %s", code.code)
        return
    if not is_new_session:
        return

    now = timezone.now()
    WarrantyCode.objects.filter(id=code.id).update(scan_count=F("scan_count") + 1)
    WarrantyCode.objects.filter(id=code.id, first_scanned_at__isnull=True).update(
        first_scanned_at=now
    )
    code.refresh_from_db(fields=["scan_count", "first_scanned_at"])


def load(*, keyword: str, ip: str | None, user_agent: str) -> ProductPageResult:
    term = normalize_keyword(keyword)
    # CHỈ khớp `code`. `serial` in công khai trên tem và sinh tuần tự (DX-000123)
    # nên cho tra bằng serial là mở đường dò cả dải mã — quyết định 06/08, xem
    # docs/warranty-portal-design.md.
    code = (
        WarrantyCode.objects.select_related("product", "product__profile")
        .prefetch_related("product__profile__images")
        .filter(code=term)
        .first()
    )
    if code is None:
        _log(keyword=term, code=None, result=ScanResult.NOT_FOUND,
             channel="web", ip=ip, user_agent=user_agent, phone="")
        return ProductPageResult(found=False)

    register_scan(code, ip)
    _log(keyword=term, code=code, result=ScanResult.PAGE_VIEWED,
         channel="web", ip=ip, user_agent=user_agent, phone="")
    return ProductPageResult(found=True, code=code)


def _company() -> dict:
    return {
        "name": sysconfig.get("PORTAL_COMPANY_NAME"),
        "address": sysconfig.get("PORTAL_COMPANY_ADDRESS"),
        "tax_code": sysconfig.get("PORTAL_COMPANY_TAX_CODE"),
        "hotline": sysconfig.get("PORTAL_COMPANY_HOTLINE"),
        "zalo_url": sysconfig.get("PORTAL_COMPANY_ZALO_URL"),
        "facebook_url": sysconfig.get("PORTAL_COMPANY_FACEBOOK_URL"),
    }


def _section(profile, name: str, value):
    """Trả nội dung khối nếu nó được bật, ngược lại trả container rỗng cùng kiểu."""
    if profile is None or not profile.is_published:
        return type(value)()
    return value if name in (profile.visible_sections or []) else type(value)()


def _product_payload(code: WarrantyCode) -> dict | None:
    product = code.product
    if product is None:
        return None
    profile = getattr(product, "profile", None)
    published = profile is not None and profile.is_published

    faqs = [
        {"question": f.get("question", ""), "answer": f.get("answer", "")}
        for f in (_section(profile, "faq", profile.faqs) if published else [])
        if f.get("visible")
    ]
    images = (
        [f"/api/warranty-portal/product-images/{img.id}" for img in profile.images.all()]
        if published else []
    )

    return {
        "sku": product.sku,
        # Tên biên tập viên đặt riêng cho trang public, rỗng thì rơi về tên
        # KiotViet. KHÔNG gác sau `published`: `<h1>` vẫn hiện cho mã hợp lệ
        # chưa bật nội dung, và tên KiotViet thường là cả một chuỗi mô tả kỹ
        # thuật — đã đặt tên đọc được thì đừng quay lại chuỗi đó chỉ vì trang
        # đang ẩn.
        "name": (profile.display_name if profile and profile.display_name else product.name),
        "short_description": profile.short_description if published else "",
        "images": images,
        "highlights": _section(profile, "diem_noi_bat", profile.highlights) if published else [],
        "specs": _section(profile, "thong_so", profile.specs) if published else [],
        "audience": _section(profile, "doi_tuong", profile.audience) if published else {},
        "usage_steps": _section(profile, "huong_dan", profile.usage_steps) if published else [],
        "troubleshooting": _section(profile, "su_co", profile.troubleshooting) if published else [],
        "faqs": faqs,
        "videos": _section(profile, "video", profile.videos) if published else [],
    }


def _warranty_payload(code: WarrantyCode) -> dict | None:
    activation = WarrantyActivation.objects.filter(code=code).first()
    if activation is None:
        return None
    product = code.product
    # Cùng shape với response của /activate để frontend dùng lại đúng WarrantyCard,
    # không đẻ kiểu dữ liệu thứ hai cho cùng một khái niệm.
    return {
        "serial": code.serial,
        "code": code.code,
        "product_sku": product.sku if product else "",
        "product_name": product.name if product else "",
        "activated_at": activation.activated_at,
        "expires_at": activation.expires_at,
        "days_left": (activation.expires_at - timezone.localdate()).days,
        "months": sysconfig.get("WARRANTY_PORTAL_MONTHS"),
    }


def build_payload(code: WarrantyCode) -> dict:
    return {
        "code": code.code,
        "serial": code.serial,
        "authentic": True,
        "scan": {"count": code.scan_count, "first_at": code.first_scanned_at},
        "product": _product_payload(code),
        "warranty": _warranty_payload(code),
        "company": _company(),
    }
