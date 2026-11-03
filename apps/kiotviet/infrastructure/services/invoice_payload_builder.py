"""
Shared builder: từ raw invoice dict (KiotViet new API) → payload create_invoice
cho kênh đích (old hoặc new). Dùng chung bởi sync command và webhook push old.

Không log/track stdout — chỉ build dữ liệu thuần và resolve customer.
"""
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_datetime

from .common_auth_customer import (
    _parse_dimensions,
    _phone_for_kiotviet,
    _sanitize_text,
    _to_kiotviet_gender,
    find_customer_by_phone,
)
from .gateway_kship_customer_webhook import create_customer
from .id_mappers import map_kiot_user_id, map_sale_channel_id
from .location_catalog import is_location_valid

logger = logging.getLogger(__name__)


class CustomerCodeMissingError(Exception):
    """Raised khi source invoice không có customer code (khách lẻ)."""


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _first_non_empty(*values):
    for value in values:
        if value not in (None, ""):
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
            else:
                return value
    return ""


def _customer_from_invoice(source_invoice: dict) -> dict:
    customer = source_invoice.get("customer")
    if not isinstance(customer, dict):
        customer = source_invoice.get("Customer")
    return customer if isinstance(customer, dict) else {}


def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _parse_dt_loose(value) -> "datetime | None":
    text = str(value or "").strip()
    if not text:
        return None
    if "." in text:
        head, _, tail = text.partition(".")
        tail_digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        tail_rest = "".join(ch for ch in tail if not ch.isdigit())
        text = f"{head}.{tail_digits}{tail_rest}" if tail_digits else head
    parsed = parse_datetime(text)
    if parsed is not None:
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    try:
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


# ----------------------------------------------------------------------
# Detail builder
# ----------------------------------------------------------------------
def build_invoice_details(source_invoice: dict, *, vat_tax_id: int | None) -> list[dict]:
    """Dòng hàng cho payload kênh đích. `vat_tax_id` None = kênh đích không áp VAT → không gắn thuế."""
    details = source_invoice.get("invoiceDetails")
    if not isinstance(details, list):
        details = source_invoice.get("InvoiceDetails")
    if not isinstance(details, list):
        return []

    result: list[dict] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        product_code = _first_non_empty(
            detail.get("productCode"), detail.get("ProductCode"),
            detail.get("code"), detail.get("Code"),
        )
        if not product_code:
            continue
        quantity = int(float(_first_non_empty(detail.get("quantity"), detail.get("Quantity"), 1) or 1))

        tax_rows = detail.get("invoiceDetailTaxs") or detail.get("InvoiceDetailTaxs")
        first_tax = tax_rows[0] if isinstance(tax_rows, list) and tax_rows and isinstance(tax_rows[0], dict) else None
        if first_tax:
            # Field thật sau thuế, per-unit. priceAfterTax = đơn giá sau thuế (chưa giảm),
            # discountAfterTax = giảm giá mỗi sản phẩm sau thuế.
            unit_price_dec = _to_decimal(_first_non_empty(
                first_tax.get("priceAfterTax"), first_tax.get("PriceAfterTax"),
            ))
            line_discount_dec = _to_decimal(_first_non_empty(
                first_tax.get("discountAfterTax"), first_tax.get("DiscountAfterTax"),
                first_tax.get("viewDiscountAfterTax"), first_tax.get("ViewDiscountAfterTax"),
            ))
            if unit_price_dec <= 0 and quantity > 0:
                # KiotViet có lúc trả priceAfterTax=0 (gặp ở HĐ đã phát hành eInvoice, vd
                # HD015121) — khi đó discountAfterTax cũng vô nghĩa (âm đúng bằng subtotal).
                # subTotalAfterTax là net SAU thuế của cả dòng (= (priceAfterTax −
                # discountAfterTax) × qty) nên đã trừ giảm giá: dùng nó làm đơn giá thì phải
                # bỏ luôn line discount, tránh trừ hai lần.
                sub_after = _to_decimal(_first_non_empty(
                    first_tax.get("subTotalAfterTax"), first_tax.get("SubTotalAfterTax"),
                ))
                if sub_after > 0:
                    unit_price_dec = sub_after / Decimal(str(quantity))
                    line_discount_dec = Decimal("0")
            if unit_price_dec <= 0:
                # Không còn field sau thuế nào dùng được → dựng lại từ giá trước thuế + taxRate.
                base_price = _to_decimal(_first_non_empty(
                    detail.get("price"), detail.get("Price"),
                    detail.get("basePrice"), detail.get("BasePrice"), 0,
                ))
                if base_price > 0:
                    tax_rate = _to_decimal(_first_non_empty(
                        first_tax.get("taxRate"), first_tax.get("TaxRate"),
                        first_tax.get("value"), first_tax.get("Value"), 0,
                    ))
                    unit_price_dec = base_price * (Decimal("1") + tax_rate / Decimal("100"))
                    line_discount_dec = _to_decimal(_first_non_empty(
                        detail.get("discount"), detail.get("Discount"), 0,
                    ))
        else:
            unit_price_dec = _to_decimal(_first_non_empty(
                detail.get("price"), detail.get("Price"),
                detail.get("basePrice"), detail.get("BasePrice"), 0,
            ))
            line_discount_dec = _to_decimal(_first_non_empty(
                detail.get("discount"), detail.get("Discount"), 0,
            ))

        unit_price = int(round(float(unit_price_dec)))
        line_discount = int(round(float(line_discount_dec)))
        row = {
            "productCode": product_code,
            "productName": _sanitize_text(_first_non_empty(detail.get("productName"), detail.get("ProductName"), product_code)),
            "quantity": quantity,
            "price": unit_price,
            "discount": line_discount if line_discount > 0 else 0,
        }
        if vat_tax_id:
            row["OrderDetailTaxs"] = [{"TaxId": vat_tax_id}]
        result.append(row)
    return result


def _header_discount_after_tax(source_invoice: dict) -> Decimal:
    """Giảm giá hoá đơn SAU thuế, đọc từ FIELD THẬT của KiotViet (không suy ra từ total).

    Ưu tiên Σ ``allocationDiscountAfterTax`` của từng dòng (giảm giá HĐ phân bổ xuống dòng, đã gồm
    thuế) — field này có sẵn trong LIST API mà luồng reconcile dùng. Nếu không có (vd DETAIL API
    không trả allocation per dòng) thì lấy header ``discountAfterTax``. Cuối cùng mới fallback
    ``discount`` (trước thuế) — chỉ khi không còn nguồn sau thuế nào (có thể lệch phần thuế).
    """
    details = source_invoice.get("invoiceDetails")
    if not isinstance(details, list):
        details = source_invoice.get("InvoiceDetails")

    saw_alloc = False
    alloc_total = Decimal("0")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            tax_rows = detail.get("invoiceDetailTaxs") or detail.get("InvoiceDetailTaxs") or []
            if not isinstance(tax_rows, list):
                continue
            for tax in tax_rows:
                if not isinstance(tax, dict):
                    continue
                val = _first_non_empty(
                    tax.get("allocationDiscountAfterTax"),
                    tax.get("AllocationDiscountAfterTax"),
                )
                if val != "":
                    saw_alloc = True
                    alloc_total += _to_decimal(val)
    if saw_alloc:
        return alloc_total

    return _to_decimal(_first_non_empty(
        source_invoice.get("discountAfterTax"), source_invoice.get("DiscountAfterTax"),
        source_invoice.get("discount"), source_invoice.get("Discount"), 0,
    ))


# ----------------------------------------------------------------------
# Customer resolver
# ----------------------------------------------------------------------
def resolve_target_customer(
    *,
    source_invoice: dict,
    delivery: dict,
    target_channel,
    target_branch_id: int,
    order=None,
    create_missing: bool = True,
) -> tuple[dict, str, str]:
    """
    Tìm customer trên target qua phone, nếu không có thì tạo mới với code = "<src_code>.1".
    Raise CustomerCodeMissingError nếu source invoice không có customer code.
    """
    source_customer = _customer_from_invoice(source_invoice)

    full_name = _sanitize_text(_first_non_empty(
        source_invoice.get("customerName"),
        source_invoice.get("CustomerName"),
        source_customer.get("name"),
        source_customer.get("Name"),
        delivery.get("receiver"),
        delivery.get("Receiver"),
        "Khách lẻ",
    )) or "Khách lẻ"

    phone = _phone_for_kiotviet(
        source_invoice.get("customerPhone"),
        source_invoice.get("CustomerPhone"),
        source_customer.get("contactNumber"),
        source_customer.get("ContactNumber"),
        source_customer.get("phoneNumber"),
        source_customer.get("PhoneNumber"),
        delivery.get("contactNumber"),
        delivery.get("ContactNumber"),
    )
    gender = _to_kiotviet_gender(_first_non_empty(source_customer.get("gender"), source_customer.get("Gender")), True)
    address = _sanitize_text(_first_non_empty(
        source_customer.get("address"),
        source_customer.get("Address"),
        delivery.get("address"),
        delivery.get("Address"),
        source_invoice.get("customerAddress"),
        source_invoice.get("CustomerAddress"),
    ))
    location_name = _first_non_empty(
        source_customer.get("locationName"),
        source_customer.get("LocationName"),
        delivery.get("locationName"),
        delivery.get("LocationName"),
    )
    ward_name = _first_non_empty(
        source_customer.get("wardName"),
        source_customer.get("WardName"),
        delivery.get("wardName"),
        delivery.get("WardName"),
    )

    if phone:
        try:
            existing = find_customer_by_phone(phone=phone, order=order, channel=target_channel)
        except Exception as exc:  # noqa: BLE001
            existing = None
            logger.warning("find_customer_by_phone error (%s): %s", phone, exc)
        if isinstance(existing, dict) and existing:
            code = _sanitize_text(_first_non_empty(
                existing.get("code"),
                existing.get("Code"),
                existing.get("customerCode"),
                existing.get("CustomerCode"),
                "",
            ))
            name = _sanitize_text(_first_non_empty(existing.get("name"), existing.get("Name"), full_name)) or full_name
            customer_block = {
                "code": code,
                "name": name,
                "gender": _to_kiotviet_gender(_first_non_empty(existing.get("gender"), existing.get("Gender")), gender),
                "birthDate": _first_non_empty(existing.get("birthDate"), existing.get("BirthDate")),
                "contactNumber": _sanitize_text(_first_non_empty(
                    existing.get("contactNumber"),
                    existing.get("ContactNumber"),
                    existing.get("phoneNumber"),
                    existing.get("PhoneNumber"),
                    phone,
                )),
                "address": _sanitize_text(_first_non_empty(existing.get("address"), existing.get("Address"), address)),
                "email": _sanitize_text(_first_non_empty(existing.get("email"), existing.get("Email"), "")),
                "comment": _sanitize_text(_first_non_empty(
                    existing.get("comments"),
                    existing.get("Comments"),
                    existing.get("comment"),
                    existing.get("Comment"),
                    "",
                )),
            }
            cust_id = _first_non_empty(existing.get("id"), existing.get("Id"))
            try:
                if cust_id is not None:
                    customer_block["id"] = int(cust_id)
            except (TypeError, ValueError):
                pass
            return customer_block, code, name

    source_customer_code = _sanitize_text(
        source_customer.get("code")
        or source_customer.get("Code")
        or source_invoice.get("customerCode")
        or source_invoice.get("CustomerCode")
        or ""
    )
    if not source_customer_code:
        raise CustomerCodeMissingError("source invoice không có customer code (khách lẻ)")
    # Validate location/ward với danh mục LIVE của retailer đích trước khi tạo KH,
    # tránh KvValidateLocationException khi A/B ở phiên bản cấu trúc địa giới khác nhau.
    valid_location = bool(location_name) and is_location_valid(target_channel, location_name)
    new_code_seed = f"{source_customer_code}.1"
    customer_payload = {
        "code": new_code_seed,
        "name": full_name,
        "gender": gender,
        "contactNumber": phone,
        "address": address,
        "locationName": location_name if valid_location else "",
        "wardName": ward_name if (valid_location and ward_name) else "",
        "branchId": int(target_branch_id),
    }
    if not create_missing:
        customer_block = {
            "code": new_code_seed,
            "name": full_name,
            "gender": gender,
            "birthDate": None,
            "contactNumber": phone,
            "address": address,
            "email": "",
            "comment": "",
        }
        return customer_block, new_code_seed, full_name

    try:
        created = create_customer(customer_payload, order=order, channel=target_channel)
    except Exception as exc:  # noqa: BLE001
        raise Exception(f"[stage=create_customer_target] {exc}") from exc

    code = _sanitize_text(_first_non_empty(
        created.get("code"),
        created.get("Code"),
        created.get("customerCode"),
        created.get("CustomerCode"),
        new_code_seed,
    ))
    name = _sanitize_text(_first_non_empty(created.get("name"), created.get("Name"), full_name)) or full_name
    customer_block = {
        "code": code,
        "name": name,
        "gender": _to_kiotviet_gender(_first_non_empty(created.get("gender"), created.get("Gender")), gender),
        "birthDate": _first_non_empty(created.get("birthDate"), created.get("BirthDate")),
        "contactNumber": _sanitize_text(_first_non_empty(
            created.get("contactNumber"),
            created.get("ContactNumber"),
            created.get("phoneNumber"),
            created.get("PhoneNumber"),
            phone,
        )),
        "address": _sanitize_text(_first_non_empty(created.get("address"), created.get("Address"), address)),
        "email": _sanitize_text(_first_non_empty(created.get("email"), created.get("Email"), "")),
        "comment": _sanitize_text(_first_non_empty(
            created.get("comments"),
            created.get("Comments"),
            created.get("comment"),
            created.get("Comment"),
            "",
        )),
    }
    cust_id = _first_non_empty(created.get("id"), created.get("Id"))
    try:
        if cust_id is not None:
            customer_block["id"] = int(cust_id)
    except (TypeError, ValueError):
        pass
    return customer_block, code, name


# ----------------------------------------------------------------------
# Payload builder
# ----------------------------------------------------------------------
def build_target_invoice_payload(
    *,
    source_invoice: dict,
    target_branch_id: int,
    vat_tax_id: int | None,
    customer_block: dict,
    customer_code: str,
    customer_name: str,
    source_channel=None,
    target_channel=None,
) -> dict:
    """
    Build payload cho POST /invoices của KiotViet target từ raw source invoice.

    Returns dict:
      {
        "payload": <body POST /invoices>,
        "source_invoice_status": int|None,
        "source_delivery_status": int|None,
      }
    """
    delivery = source_invoice.get("invoiceDelivery") if isinstance(source_invoice.get("invoiceDelivery"), dict) else {}
    if not delivery:
        delivery = source_invoice.get("InvoiceDelivery") if isinstance(source_invoice.get("InvoiceDelivery"), dict) else {}
    delivery = delivery if isinstance(delivery, dict) else {}

    order_details = build_invoice_details(source_invoice, vat_tax_id=vat_tax_id)

    source_customer = _customer_from_invoice(source_invoice)
    contact_number = customer_block.get("contactNumber") or _phone_for_kiotviet(
        source_invoice.get("customerPhone"),
        source_invoice.get("CustomerPhone"),
        source_customer.get("contactNumber"),
        source_customer.get("ContactNumber"),
        source_customer.get("phoneNumber"),
        source_customer.get("PhoneNumber"),
        delivery.get("contactNumber"),
        delivery.get("ContactNumber"),
    )

    total_after_tax = _to_decimal(_first_non_empty(
        source_invoice.get("total"),
        source_invoice.get("Total"),
        source_invoice.get("totalPayment"),
        source_invoice.get("TotalPayment"),
    ))
    cod_amount = int(float(total_after_tax or 0))

    is_cod = _first_non_empty(
        delivery.get("usingCod"), delivery.get("UsingCod"),
        delivery.get("isCod"), delivery.get("IsCod"),
    )
    is_cod_bool = True
    if isinstance(is_cod, bool):
        is_cod_bool = is_cod
    elif isinstance(is_cod, str) and is_cod:
        is_cod_bool = is_cod.strip().lower() in {"true", "1", "yes"}

    delivery_price = int(float(_to_decimal(_first_non_empty(delivery.get("price"), delivery.get("Price"), 0))))
    order_delivery = {
        "receiver": customer_name or _first_non_empty(delivery.get("receiver"), delivery.get("Receiver"), "Khách lẻ"),
        "contactNumber": contact_number,
        "address": _first_non_empty(delivery.get("address"), delivery.get("Address"), source_invoice.get("customerAddress"), ""),
        "price": delivery_price,
        "codAmount": cod_amount,
        "isCod": is_cod_bool,
        "usingCod": is_cod_bool,
        "usingPriceCod": is_cod_bool,
        "receiveMoney": cod_amount,
        "partnerDelivery": {"name": "DVVC cho đơn DESI"},
    }

    delivery_code = _first_non_empty(delivery.get("deliveryCode"), delivery.get("DeliveryCode"))
    if delivery_code:
        order_delivery["deliveryCode"] = delivery_code

    weight_value = _to_decimal(_first_non_empty(delivery.get("weight"), delivery.get("Weight"), 0))
    if weight_value and weight_value > 0:
        order_delivery["weight"] = int(float(weight_value))

    length_raw = _first_non_empty(delivery.get("length"), delivery.get("Length"))
    width_raw = _first_non_empty(delivery.get("width"), delivery.get("Width"))
    height_raw = _first_non_empty(delivery.get("height"), delivery.get("Height"))
    dims_str = f"{length_raw}x{width_raw}x{height_raw}" if (length_raw and width_raw and height_raw) else ""
    parsed_dimensions = _parse_dimensions(dims_str) if dims_str else None
    if parsed_dimensions:
        order_delivery["length"], order_delivery["width"], order_delivery["height"] = parsed_dimensions

    # Chỉ gửi locationName khi nó thực sự tồn tại trong danh mục khu vực LIVE của
    # retailer đích — nếu không, KiotViet trả 420 KvValidateLocationException và fail
    # cả đơn. Khi không hợp lệ thì bỏ luôn cả wardName (ward không có nghĩa nếu thiếu
    # location hợp lệ); KiotViet vẫn tạo đơn từ address text.
    location_name = _first_non_empty(delivery.get("locationName"), delivery.get("LocationName"))
    ward_name = _first_non_empty(delivery.get("wardName"), delivery.get("WardName"))
    if location_name and is_location_valid(target_channel, location_name):
        order_delivery["locationName"] = location_name
        if ward_name:
            order_delivery["wardName"] = ward_name

    source_invoice_status_raw = source_invoice.get("status") or source_invoice.get("Status")
    try:
        source_invoice_status = int(source_invoice_status_raw) if source_invoice_status_raw not in (None, "") else None
    except (TypeError, ValueError):
        source_invoice_status = None

    delivery_status_raw = _first_non_empty(delivery.get("status"), delivery.get("Status"))
    try:
        source_delivery_status = int(delivery_status_raw) if delivery_status_raw not in (None, "") else None
    except (TypeError, ValueError):
        source_delivery_status = None
    # Tạo ở Đang giao(2): KiotViet chỉ neo mã vận đơn + phí ship khi delivery >= Đang giao.
    # Tạo ở Chờ xử lý(1) khiến code/fee không bám (gốc lỗi lệch phí ship). Trạng thái thật
    # của A được hội tụ ở bước patch_invoice_status_after_create.
    order_delivery["status"] = 2

    purchase_date_raw_src = _first_non_empty(
        source_invoice.get("purchaseDate"),
        source_invoice.get("PurchaseDate"),
        source_invoice.get("createdDate"),
        source_invoice.get("CreatedDate"),
    )
    parsed_purchase = _parse_dt_loose(purchase_date_raw_src) if purchase_date_raw_src else None
    purchase_date = parsed_purchase.strftime("%Y-%m-%d %H:%M:%S") if parsed_purchase else None

    # Giảm giá hoá đơn SAU thuế: đọc FIELD THẬT của KiotViet, KHÔNG suy ra từ total.
    # LIST API (luồng reconcile dùng) KHÔNG trả `discountAfterTax` ở header — chỉ có `discount`
    # (trước thuế). Builder cũ rơi xuống dùng `discount` trước thuế áp lên giá dòng sau thuế →
    # B.total thừa đúng phần thuế của khoản giảm (gốc lỗi 178 đơn parity mismatch).
    # Field sau thuế thật vẫn có trong LIST: line tax row `allocationDiscountAfterTax`
    # (giảm giá HĐ phân bổ xuống dòng, đã gồm thuế) — Σ lại = giảm giá HĐ sau thuế.
    header_discount_dec = _header_discount_after_tax(source_invoice)
    discount_amount = int(round(float(header_discount_dec)))

    # Parity guard: B.total dự kiến = Σ(dòng sau thuế) − giảm giá HĐ sau thuế; lệch thì cảnh báo.
    line_net_total = Decimal("0")
    for _row in order_details:
        if isinstance(_row, dict):
            line_net_total += (
                (_to_decimal(_row.get("price")) - _to_decimal(_row.get("discount")))
                * _to_decimal(_row.get("quantity"), Decimal("1"))
            )
    expected_total = line_net_total - header_discount_dec
    if total_after_tax > 0 and abs(expected_total - total_after_tax) > Decimal("1"):
        logger.warning(
            "parity mismatch %s: expected=%s source_total=%s (line_net=%s header_discount=%s)",
            _first_non_empty(source_invoice.get("code"), source_invoice.get("Code"), "(unknown)"),
            expected_total, total_after_tax, line_net_total, header_discount_dec,
        )

    source_sale_channel_id = _first_non_empty(
        source_invoice.get("saleChannelId"),
        source_invoice.get("SaleChannelId"),
    )
    target_sale_channel_id = (
        map_sale_channel_id(
            source_sale_channel_id,
            source_kv=source_channel,
            target_kv=target_channel,
        )
        if source_sale_channel_id
        else None
    )

    source_sold_by_id = _first_non_empty(
        source_invoice.get("soldById"),
        source_invoice.get("SoldById"),
    )
    target_sold_by_id = (
        map_kiot_user_id(
            source_sold_by_id,
            source_kv=source_channel,
            target_kv=target_channel,
        )
        if source_sold_by_id
        else None
    )

    source_branch_id_for_desc = source_invoice.get("branchId") or source_invoice.get("BranchId") or ""
    source_invoice_code = _first_non_empty(
        source_invoice.get("code"),
        source_invoice.get("Code"),
        source_invoice.get("invoiceCode"),
    )
    description = _sanitize_text(
        f"SYNC từ kênh {source_branch_id_for_desc} - {source_invoice_code}"
    )

    payload = {
        "isApplyVoucher": False,
        "purchaseDate": purchase_date,
        "branchId": int(target_branch_id),
        "customerCode": customer_code,
        "customer": customer_block,
        "description": description,
        "usingCod": is_cod_bool,
        "invoiceDetails": order_details,
        "deliveryDetail": order_delivery,
        "pricingMode": 1 if vat_tax_id else 0,
        "discountAfterTax": discount_amount,
        "discount": discount_amount,
    }
    if target_sale_channel_id is not None:
        payload["saleChannelId"] = target_sale_channel_id
    if target_sold_by_id is not None:
        payload["soldById"] = target_sold_by_id

    delivery_code = _first_non_empty(delivery.get("deliveryCode"), delivery.get("DeliveryCode")) or ""

    return {
        "payload": payload,
        "source_invoice_status": source_invoice_status,
        "source_delivery_status": source_delivery_status,
        "source_delivery_code": str(delivery_code),
        "source_delivery_price": delivery_price,
    }


# ----------------------------------------------------------------------
# Status patcher (sau khi create)
# ----------------------------------------------------------------------
def patch_invoice_status_after_create(
    *,
    target_channel,
    created_invoice_id: int,
    source_invoice_status: int | None,
    source_delivery_status: int | None,
    source_delivery_code: str = "",
    source_delivery_price: int | None = None,
    order=None,
    exact_mirror: bool = False,
) -> dict | None:
    """
    Patch invoice + delivery status của B để hội tụ về expected_b_state(A) thay vì mirror
    thô A với CAP cứng. Tách 2 bước: (1) update delivery trước, (2) update invoice status sau.
    KiotViet yêu cầu delivery phải "Giao thành công" trước khi cho invoice "Hoàn thành".
    Trả về parsed response hoặc None nếu không cần patch / patch fail.
    """
    from ._reconcile_decision import expected_b_state

    exp_invoice_status, exp_delivery_status = expected_b_state(
        source_invoice_status, source_delivery_status, exact_mirror=exact_mirror
    )
    # B được tạo sẵn ở delivery=2 (Đang giao) / invoice=3 (Đang xử lý, mặc định KiotViet).
    # Chỉ PUT khi đích khác giá trị mặc định lúc tạo.
    need_delivery = exp_delivery_status is not None and exp_delivery_status != 2
    need_invoice = exp_invoice_status is not None and exp_invoice_status != 3
    if (not need_delivery and not need_invoice) or not created_invoice_id:
        return None

    from .gateway_kship_customer_webhook import _kiotviet_request

    endpoint = f"/invoices/{int(created_invoice_id)}"
    last_result: dict | None = None

    # Bước 1: Update delivery status trước (nếu cần)
    if need_delivery:
        delivery_detail: dict = {
            "status": exp_delivery_status,
            "partnerDelivery": {"name": "DVVC cho đơn DESI"},
        }
        if source_delivery_code:
            delivery_detail["deliveryCode"] = source_delivery_code
        # Gửi lại price để chắc chắn phí ship không bị reset, kể cả nếu KiotViet
        # đổi behavior PUT deliveryDetail từ merge sang replace.
        if source_delivery_price is not None:
            delivery_detail["price"] = source_delivery_price
        delivery_payload: dict = {"deliveryDetail": delivery_detail}
        resp1 = _kiotviet_request(
            order=order,
            channel=target_channel,
            method="PUT",
            endpoint=endpoint,
            json=delivery_payload,
        )
        if resp1.status_code in {200, 201, 204}:
            try:
                last_result = resp1.json()
            except ValueError:
                pass
        else:
            logger.warning(
                "patch_invoice_status_after_create (delivery) failed: status=%s body=%s",
                resp1.status_code,
                (resp1.text or "").strip()[:300],
            )

    # Bước 2: Update invoice status sau (nếu cần)
    if need_invoice:
        status_payload: dict = {"status": exp_invoice_status}
        resp2 = _kiotviet_request(
            order=order,
            channel=target_channel,
            method="PUT",
            endpoint=endpoint,
            json=status_payload,
        )
        if resp2.status_code in {200, 201, 204}:
            try:
                last_result = resp2.json()
            except ValueError:
                pass
        else:
            logger.warning(
                "patch_invoice_status_after_create (invoice status) failed: status=%s body=%s",
                resp2.status_code,
                (resp2.text or "").strip()[:300],
            )

    # Read-back: xác nhận B khớp A; lệch thì cảnh báo (KiotViet có thể chặn/ignore).
    if need_invoice or need_delivery:
        try:
            from .gateway_kship_customer_webhook import _kiotviet_request as _rq  # noqa: PLC0415
            chk = _rq(order=order, channel=target_channel, method="GET", endpoint=f"/invoices/{int(created_invoice_id)}")
            if chk.status_code == 200:
                # chk.content guard: tránh .json() trên 200 body rỗng
                cur = chk.json() if chk.content else {}
                b_inv = cur.get("status")
                b_del = (cur.get("invoiceDelivery") or {}).get("status")
                if (exp_invoice_status is not None and b_inv != exp_invoice_status) or (
                    exp_delivery_status is not None and b_del != exp_delivery_status
                ):
                    logger.warning(
                        "status read-back mismatch (invoice_id=%s): B(inv=%s,del=%s) vs expected(inv=%s,del=%s)",
                        created_invoice_id, b_inv, b_del, exp_invoice_status, exp_delivery_status,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("status read-back failed (invoice_id=%s): %s", created_invoice_id, exc)

    return last_result
