# KiotViet Faithful Field Mirror — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror đơn KiotViet new(A)→old(B) bằng field thật (giá/giảm giá/status/phí ship), bỏ suy luận và bỏ delete-resync loop cho phí ship.

**Architecture:** Sửa builder thuần (`invoice_payload_builder.py`) lấy field thật per-unit + parity-warning; sửa patch status copy mọi mã + conflict guard + read-back; thay delete-resync phí ship bằng PUT giá trực tiếp 1 lần.

**Tech Stack:** Python 3.13, Django, pytest (`SimpleTestCase` cho builder thuần), KiotViet REST API.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-06-23-kiotviet-faithful-field-mirror-design.md`.
- **KHÔNG đổi** cơ chế map id (`map_sale_channel_id`, `map_kiot_user_id`, resolve customer), `partnerDelivery.name` giữ hard-code `"DVVC cho đơn DESI"`.
- **B đã verify**: `invoiceDetails[].discount` là **per-unit**; line subtotal B = `(price − discount) × qty`.
- `discount` cấp dòng từ A: per-unit. Header discount: lấy `discountAfterTax` → `discount` as-is, KHÔNG quy đổi taxRate.
- Branch: `feat/kiotviet-faithful-field-mirror` (đã tạo).
- **Chạy test builder** (SimpleTestCase, không cần DB) qua container ephemeral:
  ```bash
  docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
    --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
    -e DJANGO_SETTINGS_MODULE=config.settings \
    -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
    -v "$(pwd)/backend:/app" -w /app desi-order-web \
    sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q'
  ```
- **Chạy ruff** trên host: `uv run --no-project --with ruff ruff check <files>`.

---

### Task 1: `build_invoice_details` — giá & giảm giá per-unit field thật

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py:86-163` (hàm `build_invoice_details`)
- Test: `backend/apps/kiotviet/tests/test_invoice_payload_builder.py`

**Interfaces:**
- Produces: `build_invoice_details(source_invoice: dict, *, target_is_old: bool) -> list[dict]`, mỗi row `{"productCode","productName","quantity","price","discount"[, "OrderDetailTaxs"]}` với `price`/`discount` per-unit.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `test_invoice_payload_builder.py`:

```python
from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
    build_invoice_details,
)


class BuildInvoiceDetailsRealFieldTests(SimpleTestCase):
    def test_taxed_line_uses_price_after_tax_and_discount_after_tax(self):
        source = {
            "invoiceDetails": [
                {
                    "productCode": "TMS01-PROMAX",
                    "productName": "May massage",
                    "quantity": 2,
                    "price": 2786111.11,  # pre-tax — KHÔNG dùng
                    "discount": 555555.56,  # pre-tax — KHÔNG dùng
                    "invoiceDetailTaxs": [
                        {
                            "priceAfterTax": 3009000.0,
                            "discountAfterTax": 600000.0,
                            "viewDiscountAfterTax": 600000.0,
                            "subTotalAfterTax": 4818000.0,
                            "taxRate": 8.0,
                        }
                    ],
                }
            ]
        }
        rows = build_invoice_details(source, target_is_old=True)
        self.assertEqual(rows[0]["price"], 3009000)
        self.assertEqual(rows[0]["discount"], 600000)
        # B tái lập: (3009000 - 600000) * 2 = 4818000 = subTotalAfterTax
        self.assertEqual((rows[0]["price"] - rows[0]["discount"]) * rows[0]["quantity"], 4818000)

    def test_untaxed_line_uses_raw_price_and_discount_per_unit(self):
        source = {
            "invoiceDetails": [
                {"productCode": "QCT07", "productName": "Quat", "quantity": 3,
                 "price": 300000, "discount": 111000}
            ]
        }
        rows = build_invoice_details(source, target_is_old=True)
        self.assertEqual(rows[0]["price"], 300000)
        self.assertEqual(rows[0]["discount"], 111000)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... pytest apps/kiotviet/tests/test_invoice_payload_builder.py::BuildInvoiceDetailsRealFieldTests -q`
Expected: FAIL (đơn có thuế hiện trả `price = subTotalAfterTax/qty = 2409000`, không phải `3009000`).

- [ ] **Step 3: Sửa `build_invoice_details`**

Thay toàn bộ thân vòng lặp tính `unit_price_dec`/`line_discount` (dòng ~105-158) bằng logic lấy field thật:

```python
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
        if not target_is_old:
            row["OrderDetailTaxs"] = [{"TaxId": 3}]
        result.append(row)
    return result
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... pytest apps/kiotviet/tests/test_invoice_payload_builder.py::BuildInvoiceDetailsRealFieldTests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py backend/apps/kiotviet/tests/test_invoice_payload_builder.py
git commit -m "feat(kiotviet): build invoice details from real per-unit price/discount fields"
```

---

### Task 2: `build_target_invoice_payload` — header discount field thật + parity warning

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py:486-573` (khối discount) và payload dict `:613-626`
- Test: `backend/apps/kiotviet/tests/test_invoice_payload_builder.py`

**Interfaces:**
- Consumes: `build_invoice_details` (Task 1).
- Produces: `build_target_invoice_payload(...) -> {"payload","source_invoice_status","source_delivery_status","source_delivery_code","source_delivery_price"}`. `payload["discount"]==payload["discountAfterTax"]` = header field thật.

- [ ] **Step 1: Viết test thất bại**

```python
import logging
from unittest.mock import patch


class HeaderDiscountRealFieldTests(SimpleTestCase):
    def _build(self, source):
        return build_target_invoice_payload(
            source_invoice=source, target_branch_id=1, target_is_old=True,
            customer_block={"code": "KH"}, customer_code="KH", customer_name="KH",
            source_channel=None, target_channel=None,
        )["payload"]

    def test_header_discount_taken_from_real_field_no_inference(self):
        # Đơn có line discount thật + header discountAfterTax thật; KHÔNG suy luận.
        source = {
            "code": "HD-REAL", "total": 1798999,
            "discountAfterTax": 0, "discount": 0,
            "invoiceDetails": [
                {"productCode": "P1", "productName": "P1", "quantity": 2,
                 "invoiceDetailTaxs": [{"priceAfterTax": 3009000, "discountAfterTax": 600000}]}
            ],
            "invoiceDelivery": {},
        }
        payload = self._build(source)
        self.assertEqual(payload["invoiceDetails"][0]["price"], 3009000)
        self.assertEqual(payload["invoiceDetails"][0]["discount"], 600000)
        self.assertEqual(payload["discount"], 0)
        self.assertEqual(payload["discountAfterTax"], 0)

    def test_header_uses_invoice_level_discount_after_tax(self):
        source = {
            "code": "HD-HDR", "total": 196000, "discountAfterTax": 100000,
            "invoiceDetails": [{"productCode": "P1", "productName": "P1", "quantity": 1, "price": 296000}],
            "invoiceDelivery": {},
        }
        payload = self._build(source)
        self.assertEqual(payload["discount"], 100000)
        self.assertEqual(payload["discountAfterTax"], 100000)

    def test_parity_mismatch_logs_warning_without_forcing(self):
        # Data thiếu giảm giá: line price 296000, total 190999, không field discount nào.
        source = {
            "code": "HD-GAP", "total": 190999, "discountAfterTax": 0,
            "invoiceDetails": [{"productCode": "P1", "productName": "P1", "quantity": 1, "price": 296000}],
            "invoiceDelivery": {},
        }
        with self.assertLogs("apps.kiotviet.infrastructure.services.invoice_payload_builder", level="WARNING") as cm:
            payload = self._build(source)
        self.assertEqual(payload["invoiceDetails"][0]["price"], 296000)
        self.assertEqual(payload["discount"], 0)  # KHÔNG bịa 105001
        self.assertTrue(any("parity" in m.lower() for m in cm.output))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... pytest apps/kiotviet/tests/test_invoice_payload_builder.py::HeaderDiscountRealFieldTests -q`
Expected: FAIL (hiện suy luận → `discount=105001`, không log parity).

- [ ] **Step 3: Sửa khối discount + thêm parity check**

Thay toàn bộ khối từ `source_discount_after_raw = ...` đến hết khối `faithful_ok`/reset (dòng ~486-573) bằng:

```python
    # Header discount: lấy field thật, KHÔNG suy luận Σ(line)−total, KHÔNG quy đổi taxRate.
    header_discount_dec = _to_decimal(_first_non_empty(
        source_invoice.get("discountAfterTax"), source_invoice.get("DiscountAfterTax"),
        source_invoice.get("discount"), source_invoice.get("Discount"), 0,
    ))
    discount_amount = int(round(float(header_discount_dec)))

    # Parity guard: tái lập total từ field thật; lệch thì cảnh báo, KHÔNG ép số.
    line_net_total = Decimal("0")
    for _row in order_details:
        if isinstance(_row, dict):
            line_net_total += (_to_decimal(_row.get("price")) - _to_decimal(_row.get("discount"))) * _to_decimal(_row.get("quantity"), Decimal("1"))
    expected_total = line_net_total - header_discount_dec
    if total_after_tax > 0 and abs(expected_total - total_after_tax) > Decimal("1"):
        logger.warning(
            "parity mismatch %s: expected=%s source_total=%s",
            _first_non_empty(source_invoice.get("code"), source_invoice.get("Code"), "(unknown)"),
            expected_total, total_after_tax,
        )
```

Xóa `_invoice_details_total` nếu không còn caller (giữ nếu còn dùng nơi khác — kiểm tra bằng grep).

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... pytest apps/kiotviet/tests/test_invoice_payload_builder.py -q`
Expected: PASS các test mới. **Lưu ý:** các test cũ assert suy luận (`test_infers_discount_when_item_subtotal_exceeds_source_total`, `test_combines_product_and_invoice_discount_to_preserve_total`, `test_falls_back_when_line_discount_breaks_total_invariant`, `test_invoice_level_discount_only_preserves_total`, `test_preserves_line_discount_when_source_exposes_it`, `test_uses_positive_discount_when_discount_after_tax_is_zero`) sẽ FAIL — **xóa** chúng (hành vi suy luận bị bỏ có chủ đích).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py backend/apps/kiotviet/tests/test_invoice_payload_builder.py
git commit -m "feat(kiotviet): take header discount from real field, add parity warning"
```

---

### Task 3: `patch_invoice_status_after_create` — copy mọi mã + conflict guard + read-back

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py:646-727`
- Test: `backend/apps/kiotviet/tests/test_invoice_status_patch.py` (tạo mới)

**Interfaces:**
- Consumes: `_kiotviet_request`, `get_invoice_by_code` (mock trong test).
- Produces: `patch_invoice_status_after_create(*, target_channel, created_invoice_id, source_invoice_status, source_delivery_status, source_delivery_code="", source_delivery_price=None, order=None) -> dict | None`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/kiotviet/tests/test_invoice_status_patch.py`:

```python
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from apps.kiotviet.infrastructure.services import invoice_payload_builder as B


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = ""
    return r


class StatusPatchConflictGuardTests(SimpleTestCase):
    @patch.object(B, "_kiotviet_request")
    def test_caps_delivery_3_to_2_when_invoice_not_completed(self, mock_req):
        # Tránh import lười bên trong hàm: patch tại module gateway.
        with patch("apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook._kiotviet_request", mock_req):
            mock_req.return_value = _resp(200, {})
            B.patch_invoice_status_after_create(
                target_channel=MagicMock(), created_invoice_id=10,
                source_invoice_status=3, source_delivery_status=3, source_delivery_code="C1",
            )
        # Bước delivery phải gửi status=2 (không phải 3) khi invoice chưa Hoàn thành.
        delivery_calls = [c for c in mock_req.call_args_list if "deliveryDetail" in str(c.kwargs.get("json"))]
        self.assertTrue(delivery_calls)
        self.assertIn("'status': 2", str(delivery_calls[0].kwargs["json"]))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... pytest apps/kiotviet/tests/test_invoice_status_patch.py -q`
Expected: FAIL (hiện gửi delivery `status=3`).

- [ ] **Step 3: Sửa `patch_invoice_status_after_create`**

Đầu hàm, sau khi tính `need_delivery`/`need_invoice`, thêm conflict guard + đổi `source_delivery_status` hiệu dụng:

```python
    # Conflict guard: A.delivery=Giao thành công(3) nhưng A.invoice chưa Hoàn thành(1) →
    # cap delivery=2 để tránh KiotViet auto-complete invoice (không revert được).
    effective_delivery_status = source_delivery_status
    if source_delivery_status == 3 and source_invoice_status is not None and source_invoice_status != 1:
        effective_delivery_status = 2
        logger.warning(
            "capped delivery 3->2 to avoid false-complete (invoice_id=%s, src_invoice=%s)",
            created_invoice_id, source_invoice_status,
        )
    need_delivery = effective_delivery_status is not None and effective_delivery_status != 1
    need_invoice = source_invoice_status is not None
```

Trong bước 1 (delivery), dùng `effective_delivery_status` thay `source_delivery_status`:

```python
        delivery_detail: dict = {
            "status": effective_delivery_status,
            "partnerDelivery": {"name": "DVVC cho đơn DESI"},
        }
```

Sau bước 2 (invoice), thêm read-back cảnh báo:

```python
    # Read-back: xác nhận B khớp A; lệch thì cảnh báo (KiotViet có thể chặn/ignore).
    if need_invoice or need_delivery:
        try:
            from .invoice_sync_queries import get_invoice_by_code  # noqa: PLC0415
            # created_invoice_id là id; đọc lại qua endpoint id để so sánh.
            from .gateway_kship_customer_webhook import _kiotviet_request as _rq  # noqa: PLC0415
            chk = _rq(order=order, channel=target_channel, method="GET", endpoint=f"/invoices/{int(created_invoice_id)}")
            if chk.status_code == 200:
                cur = chk.json() if chk.content else {}
                b_inv = cur.get("status")
                b_del = (cur.get("invoiceDelivery") or {}).get("status")
                if (source_invoice_status is not None and b_inv != source_invoice_status) or (
                    source_delivery_status is not None and b_del != effective_delivery_status
                ):
                    logger.warning(
                        "status read-back mismatch (invoice_id=%s): B(inv=%s,del=%s) vs A(inv=%s,del=%s)",
                        created_invoice_id, b_inv, b_del, source_invoice_status, source_delivery_status,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("status read-back failed (invoice_id=%s): %s", created_invoice_id, exc)
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... pytest apps/kiotviet/tests/test_invoice_status_patch.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py backend/apps/kiotviet/tests/test_invoice_status_patch.py
git commit -m "feat(kiotviet): faithful status copy with delivery-3 conflict guard and read-back"
```

---

### Task 4: `_update_status` mode — cùng conflict guard cho đơn đã tồn tại

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py:157-169` (chỗ tính `effective_del_st`)
- Test: thủ công (mode này khó unit-test thuần; verify bằng đọc code + dry-run log).

**Interfaces:**
- Consumes: `_update_status` đã có `src_del_st`, `src_inv_st`, `tgt_del_st`.

- [ ] **Step 1: Sửa `effective_del_st`**

Thay khối `:157-159`:

```python
            effective_del_st = src_del_st
            if src_del_st is None and src_inv_st == 1 and tgt_del_st is not None and tgt_del_st != 3:
                effective_del_st = 3
            # Conflict guard: A giao thành công(3) nhưng invoice chưa Hoàn thành(1) →
            # cap về 2, tránh auto-complete không revert được (nhất quán với patch khi tạo mới).
            if src_del_st == 3 and src_inv_st is not None and src_inv_st != 1:
                effective_del_st = 2
                self._stdout(f"  CAP delivery 3->2 {src_code}: invoice chưa Hoàn thành, tránh false-complete")
```

- [ ] **Step 2: Verify bằng đọc lại + ruff**

Run: `uv run --no-project --with ruff ruff check backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py`
Expected: no errors. Đọc lại đoạn sửa, xác nhận `effective_del_st` được dùng ở PUT delivery phía dưới (`:165-169`).

- [ ] **Step 3: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py
git commit -m "feat(kiotviet): cap delivery 3->2 in update_status mode to match patch guard"
```

---

### Task 5: Phí ship — PUT giá trực tiếp 1 lần thay delete-resync

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py:592-655` (nhánh `update_delivery_detail`)
- Import: `update_invoice_delivery_price_by_code` từ `invoice_mutations_push`
- Test: `backend/apps/kiotviet/tests/test_sync_delivery_price.py` (tạo mới, test helper tách riêng)

**Interfaces:**
- Consumes: `update_invoice_delivery_price_by_code(*, invoice_code, price, order, channel) -> dict`, `get_invoice_by_code`.
- Produces: helper `_sync_delivery_price_once(*, target_channel, invoice_code, a_price, get_b_price) -> bool` (True nếu khớp sau PUT) để unit-test được không cần network.

- [ ] **Step 1: Viết test thất bại cho helper**

Tạo `backend/apps/kiotviet/tests/test_sync_delivery_price.py`:

```python
from unittest.mock import MagicMock
from django.test import SimpleTestCase

from apps.kiotviet.infrastructure.services.delivery_price_sync import sync_delivery_price_once


class SyncDeliveryPriceOnceTests(SimpleTestCase):
    def test_puts_once_and_reports_match(self):
        put = MagicMock()
        readback = MagicMock(return_value=238455)
        ok = sync_delivery_price_once(
            invoice_code="HD1", a_price=238455, b_price=235755,
            put_price=put, read_b_price=readback,
        )
        put.assert_called_once_with("HD1", 238455)
        self.assertTrue(ok)

    def test_no_put_when_already_match(self):
        put = MagicMock()
        ok = sync_delivery_price_once(
            invoice_code="HD1", a_price=100, b_price=100,
            put_price=put, read_b_price=MagicMock(),
        )
        put.assert_not_called()
        self.assertTrue(ok)

    def test_returns_false_when_still_mismatch_after_put(self):
        ok = sync_delivery_price_once(
            invoice_code="HD1", a_price=238455, b_price=235755,
            put_price=MagicMock(), read_b_price=MagicMock(return_value=235755),
        )
        self.assertFalse(ok)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... pytest apps/kiotviet/tests/test_sync_delivery_price.py -q`
Expected: FAIL (module chưa tồn tại).

- [ ] **Step 3: Tạo helper thuần**

Tạo `backend/apps/kiotviet/infrastructure/services/delivery_price_sync.py`:

```python
"""Đồng bộ phí ship invoiceDelivery.price A->B: PUT trực tiếp 1 lần khi lệch.

B (retailer cũ, không tích hợp Shopee) tự suy giá ship theo deliveryCode và bỏ
ShippingSellerProtectionFeeAmount → delete+resync vô nghĩa. Thay bằng PUT giá 1 lần
+ read-back; vẫn lệch thì B-limited, log, không loop.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


def sync_delivery_price_once(
    *,
    invoice_code: str,
    a_price,
    b_price,
    put_price: Callable[[str, object], object],
    read_b_price: Callable[[], object],
) -> bool:
    """PUT giá ship đúng 1 lần nếu lệch; read-back. True nếu khớp (hoặc đã khớp sẵn)."""
    if a_price == b_price:
        return True
    put_price(invoice_code, a_price)
    new_b = read_b_price()
    if new_b == a_price:
        return True
    logger.warning(
        "ship B-limited %s: A=%s B=%s (after PUT)", invoice_code, a_price, new_b,
    )
    return False
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... pytest apps/kiotviet/tests/test_sync_delivery_price.py -q`
Expected: PASS

- [ ] **Step 5: Nối helper vào `update_delivery_detail` (thay delete-resync khi CHỈ lệch giá)**

Trong `sync_invoices_service.py`, nhánh `update_delivery_detail` (`:640-654`), khi `code_same and status_same and not price_same` → dùng PUT thay `_resync_after_delete`:

```python
                changes_str = ", ".join(changes)
                if dry_run:
                    summary["delivery_detail_updated"] += 1
                    self._stdout(f"  - [dry-run] {source_code}: {changes_str}")
                    return
                # Chỉ lệch GIÁ ship → PUT trực tiếp 1 lần (delete-resync không sửa được giá).
                if code_same and status_same:
                    from apps.kiotviet.infrastructure.services.invoice_mutations_push import (
                        update_invoice_delivery_price_by_code,
                    )
                    from apps.kiotviet.infrastructure.services.delivery_price_sync import (
                        sync_delivery_price_once,
                    )

                    def _put(code, price):
                        update_invoice_delivery_price_by_code(
                            invoice_code=code, price=price, order=None, channel=target_channel,
                        )

                    def _read_b():
                        d = get_invoice_by_code(b_code, order=None, channel=target_channel)
                        return _norm_price((d.get("invoiceDelivery") or {}).get("price"))

                    sync_delivery_price_once(
                        invoice_code=b_code, a_price=src_delivery_price, b_price=b_delivery_price,
                        put_price=_put, read_b_price=_read_b,
                    )
                    summary["delivery_detail_updated"] += 1
                    self._stdout(f"  - PUT ship {source_code}: {changes_str}")
                    return
                _resync_after_delete(b_id, b_code, changes_str, "delivery_detail_updated")
                return
```

- [ ] **Step 6: Chạy ruff + test builder regression**

Run:
```
uv run --no-project --with ruff ruff check backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py backend/apps/kiotviet/infrastructure/services/delivery_price_sync.py
... pytest apps/kiotviet/tests/test_sync_delivery_price.py apps/kiotviet/tests/test_invoice_payload_builder.py -q
```
Expected: no ruff errors; tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/delivery_price_sync.py backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py backend/apps/kiotviet/tests/test_sync_delivery_price.py
git commit -m "feat(kiotviet): sync shipping fee via direct PUT once instead of delete-resync loop"
```

---

### Task 6: Regression — chạy full test suite kiotviet + rà caller builder

**Files:**
- Verify: tất cả file đã sửa.

- [ ] **Step 1: Rà caller dùng chung builder (webhook push)**

Run: `grep -rn "build_target_invoice_payload\|build_invoice_details" backend/apps/kiotviet/ --include="*.py" | grep -v tests`
Đọc từng caller (webhook push, command sync) xác nhận hành vi field-thật mới không phá giả định (đặc biệt webhook push dùng `build_target_invoice_payload`).

- [ ] **Step 2: Chạy full suite kiotviet**

Run: `... pytest apps/kiotviet/tests -q`
Expected: PASS (trừ các test baseline đã biết fail nếu có — đối chiếu [[known-failing-baseline-tests]]).

- [ ] **Step 3: Commit (nếu có chỉnh)**

```bash
git add -A && git commit -m "test(kiotviet): green full suite after faithful mirror changes"
```

---

## Self-Review

- **Spec coverage:** §4.1→Task1, §4.2→Task2, §4.4→Task3, §4.5→Task4, §4.6→Task5, §5 test→Task1-5, §6 regression→Task6. ✓
- **Placeholder scan:** không có TBD/TODO; mọi step có code thật. ✓
- **Type consistency:** `sync_delivery_price_once` chữ ký nhất quán Task5; `build_invoice_details`/`build_target_invoice_payload` giữ nguyên chữ ký công khai. ✓
- **Điểm cần chú ý khi implement:** Task2 xóa 6 test cũ (suy luận) có chủ đích; Task3 read-back gọi `_kiotviet_request` import lười → test phải patch đúng module `gateway_kship_customer_webhook`.
