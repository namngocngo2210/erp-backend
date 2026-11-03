"""Invoice & delivery slip HTML generation — data fetched from KiotViet API."""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import TYPE_CHECKING

import barcode as python_barcode
from barcode.writer import SVGWriter
import qrcode
import qrcode.image.svg
from django.http import HttpResponse
from django.template.loader import render_to_string

from apps.orders.contracts.kiotviet_invoice_port import kiotviet_invoice_port
from apps.orders.models import Order
from apps.users.phone_visibility import (
    PhoneVisibility,
    is_public_phone_source,
    mask_phone_middle,
)

if TYPE_CHECKING:
    from apps.kiotviet.models import KiotVietChannel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cầu sang KiotViet qua port (BE-2): orders không import service của app kiotviet.
# Giữ nguyên TÊN hàm module-level để test hiện có patch `apps.orders.invoice.<tên>`
# tiếp tục ăn — hành vi và chữ ký y hệt bản import trực tiếp trước đây.
# ---------------------------------------------------------------------------


def get_invoice_by_code(*, invoice_code: str, order, channel) -> dict:
    return kiotviet_invoice_port().get_invoice_by_code(
        invoice_code=invoice_code, order=order, channel=channel
    )


def is_koc_non_cod_order(order) -> bool:
    return kiotviet_invoice_port().is_koc_non_cod_order(order)


def _kiotviet_web_request(*, order, channel, method: str, endpoint: str, params: dict | None = None):
    return kiotviet_invoice_port().web_request(
        order=order, channel=channel, method=method, endpoint=endpoint, params=params
    )


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "orders"
_LOGO_PATH = _TEMPLATES_DIR / "desi-logo.png"
_KIOTVIET_TAG_PATH = _TEMPLATES_DIR / "tag_kiotviet.png"
_logo_data_uri_cache: str | None = None
_kiotviet_tag_data_uri_cache: str | None = None

KIOTVIET_INVOICE_LOOKUP_URL = "https://tracuuhoadon.kiotviet.vn"


def _get_logo_data_uri() -> str:
    global _logo_data_uri_cache
    if _logo_data_uri_cache is None:
        raw = _LOGO_PATH.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        _logo_data_uri_cache = f"data:image/png;base64,{b64}"
    return _logo_data_uri_cache


def _get_kiotviet_tag_data_uri() -> str:
    global _kiotviet_tag_data_uri_cache
    if _kiotviet_tag_data_uri_cache is None:
        raw = _KIOTVIET_TAG_PATH.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        _kiotviet_tag_data_uri_cache = f"data:image/png;base64,{b64}"
    return _kiotviet_tag_data_uri_cache


def _generate_qr_data_uri(data: str) -> str:
    """Generate a QR code as a base64-encoded SVG data URI (no Pillow needed)."""
    factory = qrcode.image.svg.SvgPathImage
    qr = qrcode.QRCode(box_size=6, border=2, image_factory=factory)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _generate_barcode_data_uri(code: str) -> str:
    """Generate a Code128 barcode as SVG data URI."""
    try:
        code128 = python_barcode.get_barcode_class("code128")
        bc = code128(code, writer=SVGWriter())
        buf = io.BytesIO()
        bc.write(buf, options={"write_text": False, "module_height": 12})
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        logger.exception("Failed to generate barcode for code=%s", code)
        return ""


def _fmt(amount) -> str:
    """Format a number as Vietnamese-style integer string: 1,555,000"""
    try:
        rounded = int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        rounded = 0
    return f"{rounded:,}"


def _safe_decimal(value) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _first_non_empty(*values) -> str:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _parse_kiotviet_date(date_str: str | None) -> datetime | None:
    """Parse KiotViet date string like '2026-04-07T09:00:00+07:00'."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _invoice_phone_display(
    phone: str, order: Order | None, visibility: PhoneVisibility | None
) -> str:
    """SĐT khách in trên hoá đơn theo luật docs/customer-phone-masking.md.

    Hoá đơn HTML từng là đường vòng lấy số thật của đơn sale khác (audit
    2026-09-23 M3): gate in chỉ là record-scope, sale scope `all` không có
    `customer-phone:view` vẫn gọi được. FAIL-CLOSED như serializer: thiếu
    `order`/`visibility` thì che — quên truyền ở call site mới phải lộ thành
    "admin thấy số bị che", không được rò.
    """
    if not phone or phone == "-":
        return phone
    if order is None or visibility is None:
        return mask_phone_middle(phone)
    return visibility.apply(
        phone,
        order.staff_id,
        public_source=is_public_phone_source(order.order_source),
    )


def build_invoice_html_from_kiotviet(
    invoice_data: dict,
    *,
    order: Order | None = None,
    visibility: PhoneVisibility | None = None,
) -> str:
    """Render the invoice HTML from KiotViet API response data.

    ``order`` + ``visibility`` quyết định SĐT khách in ra là số thật hay bản che
    (xem ``_invoice_phone_display``); thiếu một trong hai là che.
    """
    # Invoice code & date
    invoice_code = _first_non_empty(
        invoice_data.get("code"),
        invoice_data.get("invoiceCode"),
    ) or "-"

    created_date = _parse_kiotviet_date(
        _first_non_empty(invoice_data.get("createdDate"), invoice_data.get("modifiedDate"))
    )
    if created_date:
        invoice_date = f"{created_date.day:02d} tháng {created_date.month:02d} năm {created_date.year}"
    else:
        invoice_date = "-"

    # Customer info — from invoice root + invoiceDelivery
    delivery = invoice_data.get("invoiceDelivery") or {}
    customer_name = _first_non_empty(
        invoice_data.get("customerName"),
        delivery.get("receiver"),
    ) or "-"
    customer_phone = _invoice_phone_display(
        _first_non_empty(
            invoice_data.get("customerPhone"),
            delivery.get("contactNumber"),
        ) or "-",
        order,
        visibility,
    )

    # Address from invoiceDelivery
    addr_parts = [
        _first_non_empty(delivery.get("address")),
        _first_non_empty(delivery.get("wardName")),
        _first_non_empty(delivery.get("locationName")),
    ]
    customer_address = ", ".join(p for p in addr_parts if p) or "-"

    # Items from invoiceDetails + tax from invoiceDetailTaxs
    details = invoice_data.get("invoiceDetails") or invoice_data.get("InvoiceDetails") or []
    items = []
    items_subtotal = Decimal("0")
    items_vat = Decimal("0")
    items_discount = Decimal("0")
    vat_label = "VAT"
    for detail in details:
        if not isinstance(detail, dict):
            continue
        product_name = _first_non_empty(
            detail.get("productName"),
            detail.get("ProductName"),
            detail.get("productCode"),
        )
        product_code = _first_non_empty(
            detail.get("productCode"),
            detail.get("ProductCode"),
        )
        description = f"{product_name} ({product_code})" if product_code and product_code != product_name else product_name

        quantity = int(detail.get("quantity") or detail.get("Quantity") or 1)

        # Unit price before tax
        unit_price = _safe_decimal(
            _first_non_empty(detail.get("price"), detail.get("Price"), detail.get("basePrice"), "0")
        )
        line_subtotal = _safe_decimal(
            _first_non_empty(detail.get("subTotal"), detail.get("SubTotal"), str(unit_price * quantity))
        )
        items_subtotal += line_subtotal

        # Item-level discount
        item_discount = _safe_decimal(detail.get("discount") or "0")
        items_discount += item_discount

        # Tax from invoiceDetailTaxs
        tax_list = detail.get("invoiceDetailTaxs") or []
        for tax_entry in tax_list:
            if isinstance(tax_entry, dict):
                items_vat += _safe_decimal(tax_entry.get("detailTax") or "0")
                tax_name = _first_non_empty(tax_entry.get("name"))
                if tax_name:
                    vat_label = tax_name

        # Display price = priceAfterTax from tax entry (final price per unit)
        display_price = unit_price
        if tax_list and isinstance(tax_list[0], dict):
            price_after_tax = _safe_decimal(tax_list[0].get("priceAfterTax") or "0")
            if price_after_tax > 0:
                display_price = price_after_tax

        line_total_after_tax = display_price * quantity

        items.append({
            "description": description or "-",
            "unit_price_display": _fmt(display_price),
            "quantity": quantity,
            "line_total_display": _fmt(line_total_after_tax),
        })

    if not items:
        items.append({
            "description": "-",
            "unit_price_display": "0",
            "quantity": 0,
            "line_total_display": "0",
        })

    # Totals
    discount = _safe_decimal(
        _first_non_empty(invoice_data.get("discount"), invoice_data.get("Discount"), "0")
    )
    subtotal = items_subtotal
    vat = items_vat
    grand_total = subtotal - discount + vat

    prepaid = _safe_decimal(
        _first_non_empty(
            invoice_data.get("totalPayment"),
            invoice_data.get("TotalPayment"),
            invoice_data.get("paidAmount"),
            "0",
        )
    )
    remaining = grand_total - prepaid

    context = {
        "logo_data_uri": _get_logo_data_uri(),
        "qr_data_uri": _generate_qr_data_uri(KIOTVIET_INVOICE_LOOKUP_URL),
        "invoice_lookup_url": KIOTVIET_INVOICE_LOOKUP_URL,
        "invoice_code": invoice_code,
        "invoice_date": invoice_date,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_address": customer_address,
        "items": items,
        "subtotal_display": _fmt(subtotal),
        "discount_display": _fmt(discount),
        "vat_label": vat_label,
        "vat_display": _fmt(vat),
        "grand_total_display": _fmt(grand_total),
        "prepaid_display": _fmt(prepaid),
        "remaining_display": _fmt(remaining),
    }

    return render_to_string("orders/invoice.html", context)


def invoice_response(
    order: Order, *, visibility: PhoneVisibility | None = None
) -> HttpResponse | None:
    """Fetch invoice from KiotViet API and return the invoice HTML response.

    ``visibility`` = ``phone_visibility_for(request.user)`` của người in; None → che.
    """
    if not order.invoice_code:
        return None

    if not order.kiotviet_channel:
        return None

    invoice_data = get_invoice_by_code(
        invoice_code=order.invoice_code,
        order=order,
        channel=order.kiotviet_channel,
    )

    html = build_invoice_html_from_kiotviet(invoice_data, order=order, visibility=visibility)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# Delivery slip (Phiếu giao hàng)
# ---------------------------------------------------------------------------

DELIVERY_SLIP_INCLUDES = (
    "TotalQuantity,Order,User,SoldBy,Return,Payments,Customer,"
    "PriceBook,InvoicePromotions,InvoiceOrderSurcharges,InvoiceWarranties,"
    "DeliveryPayments,DeliveryPackageDetails"
)


def _fetch_invoice_detail_web(invoice_id: int, order: Order, channel: KiotVietChannel) -> dict:
    """Fetch full invoice detail from KiotViet Web API."""
    endpoint = f"/api/invoices/{int(invoice_id)}"
    params = {"Includes": DELIVERY_SLIP_INCLUDES}
    response = _kiotviet_web_request(
        order=order,
        channel=channel,
        method="GET",
        endpoint=endpoint,
        params=params,
    )
    response_body = (response.text or "").strip()
    try:
        parsed = response.json()
    except ValueError:
        parsed = None

    if response.status_code == 200 and isinstance(parsed, dict):
        return parsed

    raise Exception(
        f"[stage=fetch_invoice_detail_web] Failed: {response.status_code} - {response_body}"
    )


def _parse_service_notes(service_add_json: str | None) -> list[str]:
    """Parse ServiceAdd JSON to extract shipping notes like 'Cho thử hàng'."""
    if not service_add_json:
        return []
    try:
        items = json.loads(service_add_json)
    except (json.JSONDecodeError, TypeError):
        return []
    notes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = item.get("Code", "")
        name = item.get("Name", "")
        value = item.get("Value", "")
        if code == "ShipperNote" and name:
            notes.append(name)
        elif code == "PaymentBy":
            pass  # skip
        elif code == "GBH":
            pass  # skip khai giá
        elif name and value:
            notes.append(f"{name}: {value}")
    return notes


def _mask_name(name: str) -> str:
    """Mask middle of name: 'Nguyễn Văn Lượng' → 'Ng*************ng'."""
    if not name or name == "-" or len(name) <= 3:
        return name
    stars = max(len(name) - 4, 13)
    return name[:2] + "*" * stars + name[-2:]


def _mask_phone(phone: str) -> str:
    """Mask phone: '0977906710' → '*******6710'."""
    digits = phone.strip()
    if not digits or digits == "-" or len(digits) <= 4:
        return digits
    stars = max(len(digits) - 4, 7)
    return "*" * stars + digits[-4:]


def _resolve_slip_cod(data: dict, order: Order | None) -> Decimal:
    """Số tiền thu hộ in trên phiếu giao.

    Mặc định lấy ``Debt`` (công nợ hoá đơn KiotViet) — đúng cho đơn bán thường.
    Riêng đơn KOC thì luôn 0: KOC không trả tiền hàng, mà đơn "có trả lại" cố ý
    để công nợ trên KiotViet (xem ``_koc_payment_override``) nên ``Debt`` = full
    tiền hàng; nếu in thẳng, shipper sẽ đi thu tiền của KOC.
    """
    if order is not None and is_koc_non_cod_order(order):
        return Decimal("0")
    return _safe_decimal(
        _first_non_empty(
            data.get("Debt"), data.get("debt"),
            data.get("CodNeedPayment"), data.get("codNeedPayment"),
            "0",
        )
    )


def build_delivery_slip_html(data: dict, order: Order | None = None) -> str:
    """Build delivery slip HTML from KiotViet Web API invoice detail response.

    ``order`` dùng để nhận diện đơn KOC — phiếu giao của đơn KOC luôn hiện COD = 0
    (xem ``_resolve_slip_cod``).
    """
    # Invoice code
    invoice_code = _first_non_empty(data.get("Code"), data.get("code")) or "-"

    # Delivery info — prefer DeliveryDetail (Web API PascalCase)
    delivery = data.get("DeliveryDetail") or data.get("InvoiceDelivery") or data.get("deliveryDetail") or {}
    delivery_code = _first_non_empty(delivery.get("DeliveryCode"), delivery.get("deliveryCode")) or ""
    sort_code = _first_non_empty(delivery.get("SortCode"), delivery.get("sortCode")) or ""
    partner_name = _first_non_empty(delivery.get("PartnerName"), delivery.get("partnerName")) or ""
    service_text = _first_non_empty(delivery.get("ServiceCodeText"), delivery.get("serviceCodeText")) or ""

    # Sender — from BranchTakingAddressStr
    sender_address_raw = _first_non_empty(
        delivery.get("BranchTakingAddressStr"),
        delivery.get("branchTakingAddressStr"),
    ) or ""
    sender_name = "desi"
    sender_phone = "0359529280"
    sender_address = sender_address_raw.replace(",", ", ") if sender_address_raw else "-"

    # Receiver
    pkg = delivery.get("DeliveryPackage") or delivery.get("deliveryPackage") or delivery
    receiver_name_raw = _first_non_empty(
        pkg.get("Receiver"), pkg.get("receiver"),
        data.get("CustomerName"), data.get("customerName"),
    ) or "-"
    receiver_phone_raw = _first_non_empty(
        pkg.get("ContactNumber"), pkg.get("contactNumber"),
        data.get("CustomerContactNumber"), data.get("customerContactNumber"),
    ) or "-"
    receiver_name = _mask_name(receiver_name_raw)
    receiver_phone = _mask_phone(receiver_phone_raw)

    recv_addr_parts = [
        _first_non_empty(pkg.get("Address"), pkg.get("address")),
        _first_non_empty(pkg.get("WardName"), pkg.get("wardName")),
    ]
    recv_location = _first_non_empty(pkg.get("LocationName"), pkg.get("locationName"))
    receiver_address_line1 = ", ".join(p for p in recv_addr_parts if p) or "-"
    receiver_address_line2 = recv_location or ""
    receiver_address = f"{receiver_address_line1}\n{receiver_address_line2}".strip()

    cod_amount = _resolve_slip_cod(data, order)

    # Shipping notes
    service_add = _first_non_empty(delivery.get("ServiceAdd"), delivery.get("serviceAdd"))
    comments = _first_non_empty(delivery.get("Comments"), delivery.get("comments"))
    note_parts = _parse_service_notes(service_add)
    if comments:
        note_parts.append(comments)
    shipping_notes = "\n".join(note_parts)

    # Weight
    weight = int(
        _safe_decimal(
            _first_non_empty(
                pkg.get("Weight"), pkg.get("weight"),
                delivery.get("Weight"), delivery.get("weight"),
                "0",
            )
        )
    )

    # Total quantity
    total_quantity = int(
        _safe_decimal(
            _first_non_empty(data.get("TotalQuantity"), data.get("totalQuantity"), "0")
        )
    )

    # Items from InvoiceDetails
    details = data.get("InvoiceDetails") or data.get("invoiceDetails") or []
    items = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        product = detail.get("Product") or detail.get("product") or {}
        code = _first_non_empty(
            detail.get("ProductCode"), detail.get("productCode"),
            product.get("Code"), product.get("code"),
        )
        name = _first_non_empty(
            product.get("FullName"), product.get("fullName"),
            detail.get("ProductName"), detail.get("productName"),
            product.get("Name"), product.get("name"),
        )
        quantity = int(detail.get("Quantity") or detail.get("quantity") or 1)
        items.append({"code": code or "-", "name": name or "-", "quantity": quantity})

    if not items:
        items.append({"code": "-", "name": "-", "quantity": 0})
    if total_quantity == 0:
        total_quantity = sum(i["quantity"] for i in items)

    # Barcodes
    barcode_data_uri = _generate_barcode_data_uri(delivery_code) if delivery_code else ""
    qr_data_uri = _generate_qr_data_uri(delivery_code) if delivery_code else ""

    context = {
        "kiotviet_tag_data_uri": _get_kiotviet_tag_data_uri(),
        "invoice_code": invoice_code,
        "delivery_code": delivery_code,
        "sort_code": sort_code,
        "partner_name": partner_name,
        "service_text": service_text,
        "sender_name": sender_name,
        "sender_phone": sender_phone,
        "sender_address": sender_address,
        "receiver_name": receiver_name,
        "receiver_phone": receiver_phone,
        "receiver_address": receiver_address,
        "cod_display": _fmt(cod_amount),
        "shipping_notes": shipping_notes,
        "weight_display": f"{weight:,}",
        "total_quantity": total_quantity,
        "items": items,
        "barcode_data_uri": barcode_data_uri,
        "qr_data_uri": qr_data_uri,
    }

    return render_to_string("orders/delivery_slip.html", context)


def delivery_slip_response(order: Order) -> HttpResponse:
    """Fetch invoice detail from KiotViet Web API and return delivery slip HTML."""
    data = _fetch_invoice_detail_web(
        invoice_id=order.invoice_id,
        order=order,
        channel=order.kiotviet_channel,
    )
    html = build_delivery_slip_html(data, order)
    Order.objects.filter(id=order.id).update(is_delivery_invoice_printed=True)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _build_bulk_html(fragments: list[str], page_style: str) -> str:
    """Wrap multiple HTML fragments into a single printable document with page breaks."""
    pages = '<div style="page-break-after:always"></div>'.join(fragments)
    return (
        '<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8">'
        f"<style>@page {{ {page_style} }} body {{ margin:0; }}</style>"
        f"</head><body>{pages}</body></html>"
    )


def bulk_invoice_response(
    orders, *, visibility: PhoneVisibility | None = None
) -> HttpResponse:
    """Generate a single HTML document with one invoice per page.

    ``visibility`` tính MỘT lần cho cả lô (không hỏi resolver từng đơn); None → che.
    """
    fragments: list[str] = []
    for order in orders:
        if not order.invoice_code or not order.kiotviet_channel:
            continue
        try:
            invoice_data = get_invoice_by_code(
                invoice_code=order.invoice_code,
                order=order,
                channel=order.kiotviet_channel,
            )
            fragments.append(
                build_invoice_html_from_kiotviet(invoice_data, order=order, visibility=visibility)
            )
        except Exception:
            logger.exception("Bulk invoice: skip order_id=%s", order.id)
    if not fragments:
        return HttpResponse("Không có hóa đơn nào.", status=400, content_type="text/plain; charset=utf-8")
    html = _build_bulk_html(fragments, "margin:15mm 20mm;")
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def bulk_delivery_slip_response(orders) -> HttpResponse:
    """Generate a single HTML document with one delivery slip per page."""
    fragments: list[str] = []
    printed_ids: list[int] = []
    for order in orders:
        if not order.invoice_id or not order.kiotviet_channel:
            continue
        try:
            data = _fetch_invoice_detail_web(
                invoice_id=order.invoice_id,
                order=order,
                channel=order.kiotviet_channel,
            )
            fragments.append(build_delivery_slip_html(data, order))
            printed_ids.append(order.id)
        except Exception:
            logger.exception("Bulk delivery slip: skip order_id=%s", order.id)
    if not fragments:
        return HttpResponse("Không có phiếu giao nào.", status=400, content_type="text/plain; charset=utf-8")
    if printed_ids:
        Order.objects.filter(id__in=printed_ids).update(is_delivery_invoice_printed=True)
    html = _build_bulk_html(fragments, "margin:10mm 15mm;")
    return HttpResponse(html, content_type="text/html; charset=utf-8")
