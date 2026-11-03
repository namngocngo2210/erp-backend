"""Unit test cho build_target_invoice_payload — đảm bảo giảm giá hoá đơn gửi sang B
là giá SAU thuế suy ra từ (Σ dòng sau thuế − total), để B.total khớp A.total tuyệt đối.

Bối cảnh lỗi thật (reconcile 21h, 178 parity mismatch): list API của reconcile KHÔNG trả
``discountAfterTax`` ở header, chỉ có ``discount`` (trước thuế). Builder cũ rơi xuống dùng
``discount`` (trước thuế) áp lên giá dòng sau thuế → B.total thừa đúng phần thuế của khoản giảm.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.kiotviet.infrastructure.services import invoice_payload_builder as B
from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
    build_target_invoice_payload,
)


def _line_net(payload: dict) -> Decimal:
    total = Decimal("0")
    for row in payload["invoiceDetails"]:
        total += (Decimal(str(row["price"])) - Decimal(str(row.get("discount") or 0))) * Decimal(
            str(row["quantity"])
        )
    return total


def _build(source_invoice: dict) -> dict:
    return build_target_invoice_payload(
        source_invoice=source_invoice,
        target_branch_id=1,
        vat_tax_id=None,
        customer_block={"contactNumber": "0900000000"},
        customer_code="C1",
        customer_name="Khach",
        source_channel=None,
        target_channel=None,
    )["payload"]


def test_header_discount_uses_after_tax_so_b_total_matches_a():
    # HD010719: 2 × priceAfterTax 1.599.000 = 3.198.000; discount(trước thuế)=183.333,
    # discountAfterTax thật=198.000; total(khách cần trả)=3.000.000.
    source_invoice = {
        "code": "HD010719",
        "total": 3000000,
        "discount": 183333,  # list API chỉ có field trước thuế
        "status": 3,
        "invoiceDetails": [
            {
                "productCode": "MSC06",
                "productName": "San pham",
                "quantity": 2,
                "price": 1480555.56,
                "invoiceDetailTaxs": [
                    {
                        "priceAfterTax": 1599000,
                        "discountAfterTax": 0,
                        "allocationDiscountAfterTax": 198000,  # giảm giá HĐ phân bổ, sau thuế
                        "subTotalAfterTax": 3198000,
                        "taxRate": 8,
                    }
                ],
            }
        ],
        "invoiceDelivery": {},
    }
    payload = _build(source_invoice)
    line_net = _line_net(payload)  # = 3.198.000
    b_total = line_net - Decimal(str(payload["discount"]))
    assert b_total == Decimal("3000000"), (
        f"B.total {b_total} != A.total 3000000 (payload discount={payload['discount']})"
    )
    # codAmount = khách cần trả = total (đúng với KiotViet này, totalPayment có thể =0)
    assert payload["deliveryDetail"]["codAmount"] == 3000000


def test_header_discount_with_line_discount_present():
    # HDSPE...J1MCCY3: line priceAfterTax 6.589.000 − discountAfterTax 2.590.000 = 3.999.000;
    # header discount(trước thuế)=92.593, discountAfterTax thật=100.000; total=3.899.000.
    source_invoice = {
        "code": "HDSPE_2606264J1MCCY3",
        "total": 3899000,
        "discount": 92593,
        "status": 3,
        "invoiceDetails": [
            {
                "productCode": "MMK01-CAOCAP",
                "productName": "San pham",
                "quantity": 1,
                "price": 6100925.93,
                "discount": 2398148.15,
                "invoiceDetailTaxs": [
                    {
                        "priceAfterTax": 6589000,
                        "discountAfterTax": 2590000,  # giảm giá DÒNG, sau thuế
                        "allocationDiscountAfterTax": 100000,  # giảm giá HĐ phân bổ, sau thuế
                        "subTotalAfterTax": 3999000,
                        "taxRate": 8,
                    }
                ],
            }
        ],
        "invoiceDelivery": {},
    }
    payload = _build(source_invoice)
    b_total = _line_net(payload) - Decimal(str(payload["discount"]))
    assert b_total == Decimal("3899000"), (
        f"B.total {b_total} != 3899000 (payload discount={payload['discount']})"
    )


def test_no_header_discount_keeps_zero():
    source_invoice = {
        "code": "HD000001",
        "total": 1599000,
        "discount": 0,
        "status": 1,
        "invoiceDetails": [
            {
                "productCode": "P1",
                "productName": "P",
                "quantity": 1,
                "price": 1480555.56,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 1599000, "discountAfterTax": 0, "allocationDiscountAfterTax": 0, "subTotalAfterTax": 1599000, "taxRate": 8}
                ],
            }
        ],
        "invoiceDelivery": {},
    }
    payload = _build(source_invoice)
    assert payload["discount"] == 0
    assert _line_net(payload) - Decimal(str(payload["discount"])) == Decimal("1599000")


def test_price_after_tax_zero_falls_back_to_sub_total_after_tax():
    # HD015121 (thật, sync 17/08): KiotViet trả priceAfterTax=0 và discountAfterTax ÂM
    # cho HĐ đã phát hành eInvoice. Giá thật chỉ còn ở subTotalAfterTax — nếu chỉ đọc
    # priceAfterTax thì B được tạo với TOÀN BỘ dòng giá 0.
    source_invoice = {
        "code": "HD015121",
        "total": 2130300,
        "status": 1,
        "invoiceDetails": [
            {
                "productCode": "DV11", "productName": "P1", "quantity": 1, "price": 642500.0,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 0.0, "discountAfterTax": -693900.0, "viewDiscountAfterTax": -693900.0,
                     "allocationDiscountAfterTax": 0.0, "subTotalAfterTax": 693900.0, "taxRate": 8.0},
                ],
            },
            {
                "productCode": "DC30", "productName": "P2", "quantity": 1, "price": 627000.0,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 0.0, "discountAfterTax": -677160.0, "viewDiscountAfterTax": -677160.0,
                     "allocationDiscountAfterTax": 0.0, "subTotalAfterTax": 677160.0, "taxRate": 8.0},
                ],
            },
            {
                "productCode": "MSC06", "productName": "P3", "quantity": 1, "price": 703000.0,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 0.0, "discountAfterTax": -759240.0, "viewDiscountAfterTax": -759240.0,
                     "allocationDiscountAfterTax": 0.0, "subTotalAfterTax": 759240.0, "taxRate": 8.0},
                ],
            },
        ],
        "invoiceDelivery": {},
    }
    payload = _build(source_invoice)
    assert [row["price"] for row in payload["invoiceDetails"]] == [693900, 677160, 759240]
    assert all(row["discount"] == 0 for row in payload["invoiceDetails"])
    assert _line_net(payload) - Decimal(str(payload["discount"])) == Decimal("2130300")


def test_price_after_tax_zero_and_no_sub_total_falls_back_to_pre_tax_price():
    # Không còn field sau thuế nào dùng được → dựng lại đơn giá từ price gốc + taxRate.
    source_invoice = {
        "code": "HD000010",
        "total": 540000,
        "status": 1,
        "invoiceDetails": [
            {
                "productCode": "P1", "productName": "P", "quantity": 1, "price": 500000,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 0, "discountAfterTax": 0, "allocationDiscountAfterTax": 0,
                     "subTotalAfterTax": 0, "taxRate": 8},
                ],
            }
        ],
        "invoiceDelivery": {},
    }
    payload = _build(source_invoice)
    assert payload["invoiceDetails"][0]["price"] == 540000


def test_delivery_created_in_shipping_state_with_code_and_fee():
    source_invoice = {
        "code": "HD000009",
        "total": 500000,
        "discount": 0,
        "status": 1,
        "invoiceDetails": [
            {
                "productCode": "P1", "productName": "P", "quantity": 1, "price": 462963.0,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 500000, "discountAfterTax": 0, "allocationDiscountAfterTax": 0, "subTotalAfterTax": 500000, "taxRate": 8}
                ],
            }
        ],
        "invoiceDelivery": {"deliveryCode": "BEST123", "price": 30000, "status": 3},
    }
    payload = _build(source_invoice)
    assert payload["deliveryDetail"]["status"] == 2, "phải tạo ở Đang giao(2) để neo mã VĐ + phí"
    assert payload["deliveryDetail"]["deliveryCode"] == "BEST123"
    assert payload["deliveryDetail"]["price"] == 30000
    assert payload["deliveryDetail"]["codAmount"] == 500000


# --- patch_invoice_status_after_create ----------------------------------------
# Unit test cho patch_invoice_status_after_create — hội tụ B về expected_b_state,
# thay cho CAP cứng 3->2 cũ. Mock _kiotviet_request tại nguồn import cục bộ
# (.gateway_kship_customer_webhook) vì builder import hàm này bên trong thân hàm.


def _resp(code=200, body=None):
    m = MagicMock()
    m.status_code = code
    m.content = b"{}"
    m.json.return_value = body or {}
    m.text = "{}"
    return m


def _calls_to_put_status(mock_req):
    """Trả list dict json của các call method=PUT."""
    out = []
    for c in mock_req.call_args_list:
        if c.kwargs.get("method") == "PUT":
            out.append(c.kwargs.get("json"))
    return out


def _run_patch(*, exact_mirror, src_inv, src_del, created_del=2):
    from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook as GW

    with patch.object(
        GW,
        "_kiotviet_request",
        return_value=_resp(body={"status": src_inv, "invoiceDelivery": {"status": src_del}}),
    ) as mreq:
        B.patch_invoice_status_after_create(
            target_channel=MagicMock(),
            created_invoice_id=123,
            source_invoice_status=src_inv,
            source_delivery_status=src_del,
            source_delivery_code="BEST1",
            source_delivery_price=30000,
            exact_mirror=exact_mirror,
        )
    return mreq


def test_normal_delivered_completed_sets_del3_then_inv1():
    # A(inv=1, del=3) bình thường (không rơi vào ca ngoại lệ vì invoice != 3):
    # expected = mirror nguyên A -> B phải PUT delivery=3 rồi PUT invoice=1.
    mreq = _run_patch(exact_mirror=False, src_inv=1, src_del=3)
    puts = _calls_to_put_status(mreq)
    # phải có PUT delivery status=3 rồi PUT invoice status=1
    assert any(p.get("deliveryDetail", {}).get("status") == 3 for p in puts)
    assert any(p.get("status") == 1 for p in puts)


def test_exception_default_no_forced_completion():
    # A(inv=3, del=3) default (exact_mirror=False) -> expected (inv=3, del=2).
    # B tạo sẵn del=2, exp_invoice=3 (== 3) -> KHÔNG cần PUT gì.
    # Không được PUT delivery=3, không được PUT invoice=1.
    mreq = _run_patch(exact_mirror=False, src_inv=3, src_del=3)
    puts = _calls_to_put_status(mreq)
    assert all(p.get("deliveryDetail", {}).get("status") != 3 for p in puts)
    assert all(p.get("status") != 1 for p in puts)
    # exp == (3, 2) == B đã tạo sẵn -> không cần patch gì cả, không gọi PUT nào.
    assert puts == []


def test_exception_exact_mirror_true_forces_del3_and_inv1():
    # A(inv=3, del=3) với exact_mirror=True -> expected (inv=1, del=3) (chấp nhận KiotViet
    # tự hoàn thành hoá đơn khi set delivery=3).
    mreq = _run_patch(exact_mirror=True, src_inv=3, src_del=3)
    puts = _calls_to_put_status(mreq)
    assert any(p.get("deliveryDetail", {}).get("status") == 3 for p in puts)
    assert any(p.get("status") == 1 for p in puts)


def test_no_patch_needed_when_expected_matches_created_defaults():
    # A(inv=3, del=2): B được tạo sẵn del=2 và exp_invoice=3 (== 3) -> không PUT gì, không gọi
    # request nào (kể cả read-back), vì (need_delivery, need_invoice) đều False.
    from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook as GW

    with patch.object(GW, "_kiotviet_request", return_value=_resp()) as mreq:
        result = B.patch_invoice_status_after_create(
            target_channel=MagicMock(),
            created_invoice_id=123,
            source_invoice_status=3,
            source_delivery_status=2,
            source_delivery_code="BEST1",
            source_delivery_price=30000,
            exact_mirror=False,
        )
    assert result is None
    mreq.assert_not_called()


def test_missing_created_invoice_id_returns_none():
    from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook as GW

    with patch.object(GW, "_kiotviet_request", return_value=_resp()) as mreq:
        result = B.patch_invoice_status_after_create(
            target_channel=MagicMock(),
            created_invoice_id=None,
            source_invoice_status=1,
            source_delivery_status=3,
            exact_mirror=False,
        )
    assert result is None
    mreq.assert_not_called()
