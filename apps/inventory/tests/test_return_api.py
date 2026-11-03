from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import ReturnEntry, ReturnNote, ReturnStatus
from apps.orders.models import Order, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin, make_user_with_sections
from apps.warehouses.models import Warehouse


class ReturnEntryApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_ret")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WR1", name="Kho Trả")
        self.product = Product.objects.create(name="SP Trả", sku="RET-1")

    def _create_warranty_return(self, qty=3, auto_post=False):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "customer_name": "Nguyễn Văn A",
                "customer_phone": "0901234567",
                "auto_post": auto_post,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": qty,
                        "return_type": "warranty",
                    }
                ],
            },
            format="json",
        )

    def test_create_warranty_return_returns_201_draft(self):
        resp = self._create_warranty_return(qty=3)
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["status"] == "draft"
        assert len(body["lines"]) == 1
        assert body["lines"][0]["quantity"] == 3
        assert body["lines"][0]["return_type"] == "warranty"
        # Phiếu ngày lồng dưới key return_note ("note" là ghi chú của nhóm đơn).
        assert body["return_note"]["code"].startswith("PTH-")
        assert body["return_note"]["warehouse_name"] == "Kho Trả"
        assert body["return_note"]["status"] == "open"

    def test_post_return_increases_warranty_stock(self):
        resp = self._create_warranty_return(qty=5, auto_post=True)
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "posted"

        stock_resp = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": "warranty"}
        )
        assert stock_resp.status_code == 200
        body = stock_resp.json()
        matching = [i for i in body["items"] if i["product_sku"] == "RET-1"]
        assert len(matching) == 1
        assert matching[0]["quantity"] == 5

    def test_cancel_return_reverts_warranty_stock(self):
        resp = self._create_warranty_return(qty=4, auto_post=True)
        assert resp.status_code == 201
        entry_id = resp.json()["id"]

        cancel_resp = self.client.post(
            f"/api/inventory/returns/{entry_id}/cancel", format="json"
        )
        assert cancel_resp.status_code == 200, cancel_resp.json()
        assert cancel_resp.json()["status"] == "cancelled"

        stock_resp = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": "warranty"}
        )
        assert stock_resp.status_code == 200
        items = stock_resp.json()["items"]
        matching = [i for i in items if i["product_sku"] == "RET-1"]
        qty = matching[0]["quantity"] if matching else 0
        assert qty == 0

    def test_create_return_with_empty_lines_returns_400(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={"warehouse_id": self.wh.id, "lines": []},
            format="json",
        )
        assert resp.status_code == 400

    # Endpoint GET list/detail + POST /post đã gỡ cùng UI phiếu trả (2026-07-14):
    # tạo chỉ còn qua luồng quét, ghi sổ lại nhóm nháp đi qua service/command.
    def test_manual_endpoints_are_gone(self):
        resp = self._create_warranty_return()
        entry_id = resp.json()["id"]
        assert self.client.get("/api/inventory/returns").status_code == 405
        assert self.client.get(f"/api/inventory/returns/{entry_id}").status_code == 404
        assert (
            self.client.post(f"/api/inventory/returns/{entry_id}/post").status_code
            == 404
        )

    def test_post_already_posted_entry_raises(self):
        resp = self._create_warranty_return(auto_post=True)
        entry_id = resp.json()["id"]
        with self.assertRaises(ValueError):
            ReturnEntryService.post_entry(entry_id=entry_id, user_id=self.user.id)

    def test_cancel_draft_return_allowed(self):
        """Nháp huỷ được (chỉ set CANCELLED, không có movement để đảo).

        Trước 14/07 nháp bị chặn huỷ, nhưng MR remove-return-note-ui đã xoá mọi cửa ghi
        sổ nháp trong khi guard chống-trùng vẫn giữ mã vận đơn của nó → mã bị khoá chết.
        Phải có đường dọn (audit 2026-07-14 vòng 3).
        """
        resp = self._create_warranty_return()
        assert resp.status_code == 201
        entry_id = resp.json()["id"]

        cancel_resp = self.client.post(
            f"/api/inventory/returns/{entry_id}/cancel", format="json"
        )
        assert cancel_resp.status_code == 200, cancel_resp.json()
        assert cancel_resp.json()["status"] == "cancelled"


class ReturnSourceInvoiceCodeTests(TestCase):
    """Body trả về khi tạo nhóm đơn hoàn expose mã hóa đơn (invoice_code) của đơn nguồn."""

    def setUp(self):
        self.user = make_admin("admin_ret_inv")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WR-INV", name="Kho Inv")
        self.product = Product.objects.create(name="SP Inv", sku="INV-1")
        self.channel = Channel.objects.create(code="ch-inv", name="Inv Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Inv",
            phone_raw="0912345699",
            phone_normalized="0912345699",
            code="CUST-INV",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Inv",
            phone="0912345699",
            total_amount="100000",
            invoice_code="HDINV-001",
            fulfillment_warehouse=self.wh,
        )

    def _create(self, source_order_id):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "source_order_id": source_order_id,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "delivery_returned",
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        return resp.json()

    def test_create_exposes_source_order_invoice_code(self):
        body = self._create(self.order.id)
        assert body["source_order_invoice_code"] == "HDINV-001"

    def test_invoice_code_empty_when_no_source_order(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "shipping_code": "VN-NO-ORDER",
                # Nhóm 0 dòng không còn được tạo (audit 14/07 vòng 3) — vẫn giữ ca "không
                # gắn đơn nguồn" bằng nhóm có dòng.
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "delivery_returned",
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["source_order_invoice_code"] == ""


class ReturnTypeStateMappingTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_ret_map")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WR-MAP", name="Kho Map")
        self.product = Product.objects.create(name="SP Map", sku="MAP-1")

    def _create_and_post(self, return_type):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "auto_post": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 7,
                        "return_type": return_type,
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "posted"

    def _qty_in_state(self, state):
        resp = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": state}
        )
        items = [i for i in resp.json()["items"] if i["product_sku"] == "MAP-1"]
        return items[0]["quantity"] if items else 0

    RETURN_TYPE_TO_STATE = (
        ("delivery_returned", "sellable"),
        ("external_return", "sellable"),
        ("warranty", "warranty"),
        ("defect", "defect"),
    )

    def test_return_type_maps_to_stock_state(self):
        """Mỗi return_type ghi sổ đúng trạng thái tồn — bảng ánh xạ là hợp đồng
        với FE (tab tồn theo trạng thái)."""
        for return_type, state in self.RETURN_TYPE_TO_STATE:
            with self.subTest(return_type=return_type, state=state):
                before = self._qty_in_state(state)
                self._create_and_post(return_type)
                assert self._qty_in_state(state) == before + 7


@pytest.mark.django_db
@pytest.mark.parametrize(
    "line_extra",
    [
        pytest.param({}, id="missing-return-type"),
        pytest.param({"return_type": "bogus"}, id="invalid-return-type"),
    ],
)
def test_line_return_type_missing_or_invalid_returns_400(line_extra):
    # Loại hoàn là BẮT BUỘC ở dòng — phiếu ngày không còn loại để kế thừa,
    # và giá trị ngoài tập choices cũng bị serializer chặn.
    # (Gộp parametrize từ test_create_line_without_return_type_returns_400 +
    # test_missing_line_type_rejected + test_invalid_line_type_rejected.)
    user = make_admin("admin_ret_line400")
    client = APIClient()
    client.force_authenticate(user)
    wh = Warehouse.objects.create(code="WR-L400", name="Kho L400")
    product = Product.objects.create(name="SP L400", sku="RET-L400")
    resp = client.post(
        "/api/inventory/returns",
        data={
            "warehouse_id": wh.id,
            "lines": [{"product_id": product.id, "quantity": 1, **line_extra}],
        },
        format="json",
    )
    assert resp.status_code == 400


class ReturnApiLineTypeTests(TestCase):
    """API phiếu trả: nhận/trả return_type per line + created_by_name."""

    def setUp(self):
        # is_superuser=True (Django flag) KHÔNG bypass được require_section — RBAC ở
        # đây là role-based (is_system role), không dựa vào is_superuser. Dùng
        # make_admin như các test API khác trong apps/inventory/tests/.
        self.user = make_admin("kho-lt")
        self.user.display_name = "Kho Một"
        self.user.save(update_fields=["display_name"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.warehouse = Warehouse.objects.create(code="WH-API-LT", name="Kho API LT")
        self.product = Product.objects.create(name="SP API", sku="SKU-API-LT")

    def _create_payload(self, **over):
        payload = {
            "warehouse_id": self.warehouse.id,
            "lines": [
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "warranty",
                }
            ],
        }
        payload.update(over)
        return payload

    def test_create_returns_line_type_and_creator(self):
        res = self.client.post(
            "/api/inventory/returns", self._create_payload(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["created_by_name"], "Kho Một")
        self.assertEqual(res.data["lines"][0]["return_type"], "warranty")


class ReturnAutoPostTests(TestCase):
    """Endpoint tạo phiếu trả với auto_post=true: ghi sổ ngay + set đơn RETURNED,
    lỗi thì rollback (không tạo phiếu). auto_post=false giữ hành vi nháp cũ."""

    def setUp(self):
        self.user = make_admin("admin_auto_post")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WAP1", name="Kho AutoPost")
        self.product = Product.objects.create(name="SP AP", sku="AP-1")
        self.channel = Channel.objects.create(code="ch-ap", name="AP Channel")
        self.customer = Customer.objects.create(
            full_name="Khach AP",
            phone_raw="0912345672",
            phone_normalized="0912345672",
            code="CUST-AP",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach AP",
            phone="0912345672",
            total_amount="100000",
            status=OrderStatus.AWAITING_SHIPMENT,
            shipping_code="VNAP001",
            fulfillment_warehouse=self.wh,
        )

    def _post(self, extra, *, return_type="delivery_returned"):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "source_order_id": self.order.id,
                "shipping_code": "VNAP001",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": return_type,
                    }
                ],
                **extra,
            },
            format="json",
        )

    def test_auto_post_true_posts_and_marks_order_returned(self):
        resp = self._post({"auto_post": True})
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "posted"
        self.order.refresh_from_db()
        assert int(self.order.status) == int(OrderStatus.RETURNED)

    def test_auto_post_failure_rolls_back_creates_nothing(self):
        before = int(self.order.status)
        # Ghi sổ lỗi (lỗi nghiệp vụ bất kỳ) → cả create + post rollback: không nhóm
        # đơn, không phiếu ngày, đơn giữ nguyên trạng thái.
        with patch.object(
            ReturnEntryService, "post_entry", side_effect=ValueError("ghi sổ lỗi")
        ):
            resp = self._post({"auto_post": True})
        assert resp.status_code == 400, resp.json()
        assert resp.json()["detail"] == "ghi sổ lỗi"
        assert ReturnEntry.objects.count() == 0
        assert ReturnNote.objects.count() == 0
        self.order.refresh_from_db()
        assert int(self.order.status) == before

    def test_default_no_auto_post_creates_draft(self):
        resp = self._post({})
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "draft"
        self.order.refresh_from_db()
        assert int(self.order.status) != int(OrderStatus.RETURNED)


class UnmatchedReturnCreateTests(TestCase):
    """Nhóm đơn hoàn 0 DÒNG: không còn được tạo, và nháp rỗng di sản phải gỡ được.

    Trước 14/07 quét mã 404 sinh nhóm nháp 0 dòng rồi khớp tay qua PATCH. MR
    remove-return-note-ui đã xoá list/PATCH/post → nháp 0 dòng không còn cửa nào ghi sổ,
    trong khi guard chống-trùng vẫn giữ mã vận đơn của nó: quét lại luôn bị chặn, hàng hoàn
    không bao giờ được cộng tồn (audit 2026-07-14 vòng 3). Nay:
      - tạo nhóm 0 dòng → 400 (mã chưa khớp đi đường PendingShipmentScan);
      - nháp rỗng cũ đang giữ mã → tự huỷ khi có lần quét thật;
      - nháp huỷ được qua API (dọn rác).
    """

    def setUp(self):
        self.user = make_admin("admin_unmatched")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WU1", name="Kho U")
        self.product = Product.objects.create(name="SP U", sku="UNM-1")

    def _post(self, payload):
        return self.client.post("/api/inventory/returns", data=payload, format="json")

    def _lines(self, quantity=2):
        return [
            {
                "product_id": self.product.id,
                "quantity": quantity,
                "return_type": "delivery_returned",
            }
        ]

    def test_zero_line_entry_rejected_even_with_shipping_code(self):
        resp = self._post(
            {"warehouse_id": self.wh.id, "shipping_code": "VD-LOST-001", "lines": []}
        )
        assert resp.status_code == 400, resp.json()

    def test_empty_lines_without_code_still_400(self):
        resp = self._post({"warehouse_id": self.wh.id, "lines": []})
        assert resp.status_code == 400

    def test_matched_entry_with_lines_and_code_ok(self):
        resp = self._post(
            {
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-OK-01",
                "lines": self._lines(),
            }
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["shipping_code"] == "VD-OK-01"

    def test_duplicate_code_with_lines_returns_400(self):
        resp1 = self._post(
            {
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-DUP-01",
                "lines": self._lines(),
            }
        )
        assert resp1.status_code == 201
        resp2 = self._post(
            {
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-DUP-01",
                "lines": self._lines(),
            }
        )
        assert resp2.status_code == 400
        # Thông báo trùng nêu MÃ PHIẾU NGÀY chứa nhóm đơn đã có.
        assert resp1.json()["return_note"]["code"] in resp2.json()["detail"]

    def test_legacy_empty_draft_does_not_lock_the_shipping_code(self):
        """Nháp rỗng di sản giữ mã → lần quét thật vẫn tạo được nhóm đơn (tự huỷ nháp)."""
        legacy = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VD-LEGACY-01",
            user_id=self.user.id,
        )
        assert legacy.status == ReturnStatus.DRAFT

        resp = self._post(
            {
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-LEGACY-01",
                "lines": self._lines(),
            }
        )
        assert resp.status_code == 201, resp.json()

        legacy.refresh_from_db()
        assert legacy.status == ReturnStatus.CANCELLED
        created_id = resp.json()["id"]
        assert created_id != legacy.id
        assert ReturnEntry.objects.get(id=created_id).lines.count() == 1

    def test_non_empty_draft_still_blocks_duplicate(self):
        """Nháp CÓ dòng vẫn là dữ liệu thật (command post_matched_return_drafts ghi sổ
        được) → không tự huỷ, vẫn chặn trùng."""
        draft = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=self._lines(),
            shipping_code="VD-DRAFT-LINES",
            user_id=self.user.id,
        )
        resp = self._post(
            {
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-DRAFT-LINES",
                "lines": self._lines(),
            }
        )
        assert resp.status_code == 400
        draft.refresh_from_db()
        assert draft.status == ReturnStatus.DRAFT

    def test_cancel_draft_entry_allowed(self):
        draft = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VD-CANCEL-DRAFT",
            user_id=self.user.id,
        )
        resp = self.client.post(f"/api/inventory/returns/{draft.id}/cancel")
        assert resp.status_code == 200, resp.json()
        draft.refresh_from_db()
        assert draft.status == ReturnStatus.CANCELLED

    def test_cancel_twice_rejected(self):
        draft = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VD-CANCEL-TWICE",
            user_id=self.user.id,
        )
        ReturnEntryService.cancel_entry(entry_id=draft.id, user_id=self.user.id)
        with self.assertRaisesMessage(ValueError, "đã huỷ"):
            ReturnEntryService.cancel_entry(entry_id=draft.id, user_id=self.user.id)

    def test_post_zero_line_entry_rejected(self):
        entry = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=[],
            shipping_code="VD-NOLINE-01",
            user_id=self.user.id,
        )
        with self.assertRaisesMessage(ValueError, "dòng hàng"):
            ReturnEntryService.post_entry(entry_id=entry.id, user_id=self.user.id)

    def test_create_without_returns_permission_403(self):
        # User có quyền section khác ("stock") nhưng không có "returns" → 403.
        plain = make_user_with_sections("plain_create_user", ["stock"])
        client = APIClient()
        client.force_authenticate(plain)
        resp = client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-RBAC",
                "lines": self._lines(),
            },
            format="json",
        )
        assert resp.status_code == 403
