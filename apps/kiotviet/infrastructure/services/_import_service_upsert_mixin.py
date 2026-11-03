"""Customer/order/item upsert for ImportInvoicesService (mixin moved verbatim from
management/commands/_import_invoices_upsert_mixin.py - WP8.1)."""

from decimal import Decimal

from django.db import transaction

from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.customers.services import CustomerService, normalize_phone
from apps.kiotviet.models import KiotVietChannel
from apps.orders.application.services import journey_items_service
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order, OrderItem
from apps.catalog.models import Product


class ImportPageCache:
    """Product/Customer nạp trước cho MỘT trang hoá đơn (audit 2026-09-23 T1 §2.4).

    Trước: mỗi hoá đơn 3-5 query Customer (code, phone, exists) + 1-3 query Product
    mỗi dòng (`get_or_create`) → page 100 hoá đơn ≈ 1000-1500 query tuần tự ở worker
    `kiotviet`. Giờ: 1 query `sku__in` + 1 `code__in` + 1 `phone_normalized__in` cho
    cả trang; hoá đơn chỉ chạm DB cho phần THIẾU (tạo mới) hoặc phần đổi (save).

    Ngữ nghĩa giữ nguyên `filter(...).first()` cũ: `first()` trên queryset không
    order → theo pk, nên trùng mã (prod có 7.069 mã `customers.code` trùng) lấy
    bản ghi id NHỎ NHẤT, y hệt trước.

    An toàn với rollback: mỗi hoá đơn chạy trong `transaction.atomic()` riêng; bản
    ghi TẠO MỚI hoặc SỬA trong hoá đơn được ghi vào `_touched` và chỉ "chốt" vào
    cache khi hoá đơn commit (`commit_invoice`). Hoá đơn lỗi → `discard_invoice`
    gỡ các khoá đã chạm khỏi cache để hoá đơn sau tra lại DB thay vì dùng instance
    có state đã rollback.
    """

    def __init__(self):
        self.products_by_sku: dict[str, Product] = {}
        self.known_skus: set[str] = set()
        self.customers_by_code: dict[str, Customer] = {}
        self.known_codes: set[str] = set()
        self.customers_by_phone: dict[str, Customer] = {}
        self.known_phones: set[str] = set()
        self._touched_skus: set[str] = set()
        self._touched_codes: set[str] = set()
        self._touched_phones: set[str] = set()

    @classmethod
    def build(cls, invoices: list, upsert) -> "ImportPageCache":
        """`upsert` = class có `_extract_item_rows`/`_first_non_empty`/`_to_int`."""
        cache = cls()
        skus: set[str] = set()
        codes: set[str] = set()
        phones: set[str] = set()
        for invoice in invoices:
            if not isinstance(invoice, dict):
                continue
            for row in upsert._extract_item_rows(invoice):
                skus.add(row["sku"])
            customer_obj = invoice.get("customer") if isinstance(invoice.get("customer"), dict) else {}
            customer_id = upsert._to_int(
                upsert._first_non_empty(invoice.get("customerId"), customer_obj.get("id"))
            )
            code = upsert._first_non_empty(
                invoice.get("customerCode"),
                customer_obj.get("code"),
                f"KV-{customer_id}" if customer_id is not None else "",
            )
            if code:
                codes.add(code)
            delivery = invoice.get("invoiceDelivery") if isinstance(invoice.get("invoiceDelivery"), dict) else {}
            if not delivery:
                delivery = invoice.get("InvoiceDelivery") if isinstance(invoice.get("InvoiceDelivery"), dict) else {}
            phone_raw = upsert._first_non_empty(
                invoice.get("customerPhone"),
                customer_obj.get("contactNumber"),
                customer_obj.get("phoneNumber"),
                delivery.get("contactNumber"),
                delivery.get("ContactNumber"),
            )
            phone = normalize_phone(phone_raw)
            if phone:
                phones.add(phone)

        cache.known_skus = set(skus)
        if skus:
            cache.products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=skus)}
        cache.known_codes = set(codes)
        if codes:
            for customer in Customer.objects.filter(
                code__in=codes, deleted_at__isnull=True
            ).order_by("id"):
                cache.customers_by_code.setdefault(customer.code, customer)
        cache.known_phones = set(phones)
        if phones:
            for customer in Customer.objects.filter(
                phone_normalized__in=phones, deleted_at__isnull=True
            ).order_by("id"):
                cache.customers_by_phone.setdefault(customer.phone_normalized, customer)
        return cache

    # --- Product ---
    def has_sku(self, sku: str) -> bool:
        return sku in self.known_skus

    def product(self, sku: str) -> Product | None:
        return self.products_by_sku.get(sku)

    def touch_product(self, product: Product) -> None:
        self.products_by_sku[product.sku] = product
        self.known_skus.add(product.sku)
        self._touched_skus.add(product.sku)

    # --- Customer ---
    def has_code(self, code: str) -> bool:
        return code in self.known_codes

    def customer_by_code(self, code: str) -> Customer | None:
        return self.customers_by_code.get(code)

    def has_phone(self, phone: str) -> bool:
        return phone in self.known_phones

    def customer_by_phone(self, phone: str) -> Customer | None:
        return self.customers_by_phone.get(phone)

    def touch_customer(self, customer: Customer) -> None:
        if customer.code:
            self.customers_by_code[customer.code] = customer
            self.known_codes.add(customer.code)
            self._touched_codes.add(customer.code)
        if customer.phone_normalized:
            self.customers_by_phone[customer.phone_normalized] = customer
            self.known_phones.add(customer.phone_normalized)
            self._touched_phones.add(customer.phone_normalized)

    # --- ranh giới hoá đơn ---
    def commit_invoice(self) -> None:
        self._touched_skus.clear()
        self._touched_codes.clear()
        self._touched_phones.clear()

    def discard_invoice(self) -> None:
        for sku in self._touched_skus:
            self.products_by_sku.pop(sku, None)
            self.known_skus.discard(sku)
        for code in self._touched_codes:
            self.customers_by_code.pop(code, None)
            self.known_codes.discard(code)
        for phone in self._touched_phones:
            self.customers_by_phone.pop(phone, None)
            self.known_phones.discard(phone)
        self.commit_invoice()


class ImportUpsertMixin:
    @classmethod
    def _upsert_customer(cls, invoice: dict, delivery: dict, page_cache: ImportPageCache | None = None) -> Customer:
        customer_obj = invoice.get("customer") if isinstance(invoice.get("customer"), dict) else {}

        customer_id = cls._to_int(
            cls._first_non_empty(invoice.get("customerId"), customer_obj.get("id"))
        )
        customer_code = cls._first_non_empty(
            invoice.get("customerCode"),
            customer_obj.get("code"),
            f"KV-{customer_id}" if customer_id is not None else "",
        )
        full_name = cls._first_non_empty(
            invoice.get("customerName"),
            customer_obj.get("name"),
            delivery.get("receiver"),
            delivery.get("Receiver"),
            "Unknown",
        )
        phone_raw = cls._first_non_empty(
            invoice.get("customerPhone"),
            customer_obj.get("contactNumber"),
            customer_obj.get("phoneNumber"),
            delivery.get("contactNumber"),
            delivery.get("ContactNumber"),
        )

        customer = None
        if customer_code:
            if page_cache is not None and page_cache.has_code(customer_code):
                customer = page_cache.customer_by_code(customer_code)
            else:
                customer = Customer.objects.filter(code=customer_code, deleted_at__isnull=True).first()

        phone_normalized = normalize_phone(phone_raw)
        if customer is None and phone_normalized:
            if page_cache is not None and page_cache.has_phone(phone_normalized):
                customer = page_cache.customer_by_phone(phone_normalized)
            else:
                customer = Customer.objects.filter(phone_normalized=phone_normalized, deleted_at__isnull=True).first()

        created_here = customer is None
        if customer is None:
            if phone_normalized:
                customer = CustomerService.get_or_create_by_phone(phone=phone_raw, full_name=full_name)
                if customer_code and customer.code != customer_code and not Customer.objects.filter(code=customer_code).exists():
                    customer.code = customer_code
                    customer.save(update_fields=["code", "updated_at"])
            else:
                # Phone rỗng, bị che (vd TikTok Shop mask "(+84)037*****54") hoặc không
                # hợp lệ → normalize_phone trả "". Vẫn tạo customer để không làm fail cả
                # invoice; giữ phone_raw (kể cả chuỗi masked) để tham chiếu.
                customer = Customer.objects.create(
                    full_name=full_name,
                    phone_raw=phone_raw,
                    phone_normalized="",
                )
                if customer_code and customer.code != customer_code and not Customer.objects.filter(code=customer_code).exclude(id=customer.id).exists():
                    customer.code = customer_code
                    customer.save(update_fields=["code", "updated_at"])

        update_fields = []
        if full_name and customer.full_name != full_name:
            customer.full_name = full_name
            update_fields.append("full_name")
        if phone_raw and customer.phone_raw != phone_raw:
            customer.phone_raw = phone_raw
            update_fields.append("phone_raw")
        if phone_normalized and customer.phone_normalized != phone_normalized:
            # SĐT có thể đã thuộc về một customer active KHÁC (vd đơn cùng người mua
            # nhưng KiotViet tách/gộp mã KH khác nhau). Ghi đè sẽ vi phạm partial unique
            # index cust_phone_norm_active_uniq và làm cả invoice fail. Chỉ nhận SĐT khi
            # chưa ai giữ — nếu đã có chủ, giữ nguyên, dedup xử lý sau.
            phone_taken = (
                Customer.objects.filter(phone_normalized=phone_normalized, deleted_at__isnull=True)
                .exclude(id=customer.id)
                .exists()
            )
            if not phone_taken:
                customer.phone_normalized = phone_normalized
                update_fields.append("phone_normalized")
        if customer_code and customer.code != customer_code and not Customer.objects.filter(code=customer_code).exclude(id=customer.id).exists():
            customer.code = customer_code
            update_fields.append("code")

        if update_fields:
            customer.save(update_fields=[*sorted(set(update_fields)), "updated_at"])

        if page_cache is not None and (created_here or update_fields):
            # Ghi nhận theo hoá đơn — chỉ chốt khi hoá đơn commit (xem ImportPageCache).
            page_cache.touch_customer(customer)

        return customer

    @classmethod
    def _extract_item_rows(cls, invoice: dict) -> list[dict]:
        details = invoice.get("invoiceDetails")
        if not isinstance(details, list):
            details = invoice.get("InvoiceDetails")
        if not isinstance(details, list):
            return []

        rows = []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            product_code = cls._first_non_empty(
                detail.get("productCode"),
                detail.get("ProductCode"),
                detail.get("code"),
                detail.get("Code"),
            )
            if not product_code:
                continue

            quantity = cls._to_int(cls._first_non_empty(detail.get("quantity"), detail.get("Quantity"), 1)) or 1
            unit_price = cls._to_decimal(
                cls._first_non_empty(detail.get("price"), detail.get("Price"), detail.get("basePrice"), detail.get("BasePrice"), 0)
            )
            # `discount` của KV là giảm cho MỖI sản phẩm (subTotal = (price − discount) × quantity).
            discount = cls._to_decimal(cls._first_non_empty(detail.get("discount"), detail.get("Discount"), 0))
            line_total = cls._to_decimal(
                cls._first_non_empty(
                    detail.get("subTotal"),
                    detail.get("SubTotal"),
                    detail.get("total"),
                    detail.get("Total"),
                    (unit_price - discount) * quantity,
                )
            )
            rows.append(
                {
                    "sku": product_code,
                    "name": cls._first_non_empty(detail.get("productName"), detail.get("ProductName"), product_code),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "discount": discount,
                    "line_total": line_total,
                }
            )

        return rows

    @classmethod
    def _extract_payment_method(cls, invoice: dict) -> str:
        payments = invoice.get("payments")
        if not isinstance(payments, list):
            payments = invoice.get("Payments")
        methods: list[str] = []
        if isinstance(payments, list):
            for payment in payments:
                if not isinstance(payment, dict):
                    continue
                method = cls._first_non_empty(payment.get("method"), payment.get("Method"))
                if method and method not in methods:
                    methods.append(method)
        if not methods:
            method = cls._first_non_empty(invoice.get("method"), invoice.get("Method"))
            if method:
                methods.append(method)
        return ", ".join(methods)[:100]

    @classmethod
    def _upsert_products_and_items(
        cls, order: Order, invoice: dict, page_cache: ImportPageCache | None = None
    ) -> tuple[int, str, str]:
        rows = cls._extract_item_rows(invoice)
        # Chụp dòng hàng cũ để journey ghi "đổi số lượng" khi hoá đơn KiotViet bị sửa.
        # Đơn mới tạo (chưa có dòng) thì bỏ qua — đó là lần ghi đầu, không phải đổi.
        items_before = journey_items_service.snapshot_items(order.id)
        if not rows:
            OrderItem.objects.filter(order=order).delete()
            cls._log_items_synced(order, items_before)
            return 0, "", ""

        aggregated: dict[str, dict] = {}
        for row in rows:
            sku = row["sku"]
            current = aggregated.get(sku)
            if current is None:
                aggregated[sku] = dict(row)
                continue
            current["quantity"] += row["quantity"]
            current["line_total"] += row["line_total"]
            current["unit_price"] = row["unit_price"]
            current["discount"] = row["discount"]

        items_payload = []
        total_qty = 0
        total_amount = Decimal("0")
        names: list[str] = []
        skus: list[str] = []

        for sku, row in aggregated.items():
            product = page_cache.product(sku) if page_cache is not None and page_cache.has_sku(sku) else None
            created = False
            if product is None:
                product, created = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "name": row["name"],
                        "price": row["unit_price"],
                    },
                )
                if page_cache is not None:
                    page_cache.touch_product(product)
            if not created:
                update_fields = []
                if row["name"] and product.name != row["name"]:
                    product.name = row["name"]
                    update_fields.append("name")
                if update_fields:
                    product.save(update_fields=[*update_fields, "updated_at"])
                    if page_cache is not None:
                        page_cache.touch_product(product)

            items_payload.append(
                OrderItem(
                    order=order,
                    product=product,
                    product_name=row["name"],
                    sku=sku,
                    quantity=row["quantity"],
                    unit_price=row["unit_price"],
                    discount=row["discount"],
                    line_total=row["line_total"],
                )
            )
            total_qty += row["quantity"]
            total_amount += row["line_total"]
            names.append(row["name"])
            skus.append(sku)

        OrderItem.objects.filter(order=order).delete()
        OrderItem.objects.bulk_create(items_payload)
        cls._log_items_synced(order, items_before)

        return total_qty, ", ".join(names), ", ".join(skus)

    @staticmethod
    def _log_items_synced(order: Order, items_before: dict) -> None:
        if not items_before:
            return
        journey_items_service.log_items_changed(
            order,
            items_before,
            journey_items_service.snapshot_items(order.id),
            source=StatusChangeSource.KIOTVIET_SYNC,
        )

    @classmethod
    def _upsert_invoice(
        cls,
        *,
        invoice: dict,
        kv_channel: KiotVietChannel,
        dry_run: bool,
        create_only: bool = False,
        page_cache: ImportPageCache | None = None,
    ) -> str:
        """``page_cache``: vòng lặp theo trang truyền cache Product/Customer đã nạp
        trước (ImportPageCache); webhook/1 hoá đơn để trống → tra DB như cũ."""
        if page_cache is None:
            return cls._upsert_invoice_impl(
                invoice=invoice, kv_channel=kv_channel, dry_run=dry_run, create_only=create_only
            )
        try:
            outcome = cls._upsert_invoice_impl(
                invoice=invoice,
                kv_channel=kv_channel,
                dry_run=dry_run,
                create_only=create_only,
                page_cache=page_cache,
            )
        except BaseException:
            page_cache.discard_invoice()
            raise
        page_cache.commit_invoice()
        return outcome

    @classmethod
    def _upsert_invoice_impl(
        cls,
        *,
        invoice: dict,
        kv_channel: KiotVietChannel,
        dry_run: bool,
        create_only: bool = False,
        page_cache: ImportPageCache | None = None,
    ) -> str:
        delivery = invoice.get("invoiceDelivery") if isinstance(invoice.get("invoiceDelivery"), dict) else {}
        if not delivery:
            delivery = invoice.get("InvoiceDelivery") if isinstance(invoice.get("InvoiceDelivery"), dict) else {}

        existing = cls._find_existing_order(invoice, kv_channel)
        if create_only and existing is not None:
            return "skipped"
        # Đơn đã bị user XOÁ (soft-delete) cũng tính là "đã tồn tại" với create_only —
        # không thì lookback/deep-sweep resurrect đơn đã xoá mỗi đêm (audit 2026-07-18).
        if create_only and cls._soft_deleted_order_exists(invoice, kv_channel):
            return "skipped"

        # Hoá đơn đã bị KiotViet huỷ-xuất lại (`X` → `X.01`): webhook reattach đã chuyển
        # đơn sang mã mới và ghi mã cũ vào `reattached_invoice_codes`. Import lại payload
        # của mã CŨ không được tạo dòng đơn huỷ trùng, cũng không được lật đơn kế thừa
        # (đang sống) sang huỷ — bỏ qua hẳn (audit 2026-07-14).
        if existing is None and cls._has_reattached_successor(invoice, kv_channel):
            return "skipped"

        # Đơn đã huỷ (status=2): vẫn import — chưa có trong DB thì rơi xuống nhánh
        # tạo mới bên dưới (giữ invoice_status=2); nếu đã có thì chỉ sync
        # invoice_status, không ghi đè các field khác từ payload huỷ.
        if cls._resolve_invoice_own_status(invoice=invoice) == 2 and existing is not None:
            # Guard: hóa đơn kênh CŨ chỉ là bản mirror, được match về đơn của kênh MỚI
            # qua shipping_code toàn cục (_find_existing_order). KHÔNG cho hóa đơn cũ đã huỷ
            # ghi đè invoice_status=2 lên đơn thật của kênh mới — đây là nguyên nhân đơn
            # thành công bị tự lật thành đã huỷ sau mỗi đợt đối soát.
            if bool(kv_channel.old) and existing.kiotviet_channel_id != kv_channel.id:
                return "skipped"
            if not dry_run and existing.invoice_status != 2:
                existing.invoice_status = 2
                existing.save(update_fields=["invoice_status", "updated_at"])
            return "updated"

        outcome = "updated" if existing else "created"

        if dry_run:
            # parse/map for validation even in dry-run
            cls._extract_address_fields(delivery)
            cls._extract_item_rows(invoice)
            return outcome

        sale_channel_id = cls._extract_sale_channel_id(invoice)
        order_channel = cls._resolve_order_channel_from_sale_channel_id(
            kv_channel=kv_channel,
            sale_channel_id=sale_channel_id,
        )
        if order_channel is None and existing is not None and existing.channel_id:
            order_channel = existing.channel
        if order_channel is None:
            order_channel = cls._get_or_create_order_channel(kv_channel)

        with transaction.atomic():
            customer = cls._upsert_customer(invoice, delivery, page_cache)
            address = cls._extract_address_fields(delivery)
            sold_by_id = cls._first_non_empty(invoice.get("soldById"), invoice.get("SoldById"))
            staff = cls._resolve_staff(sold_by_id, kv_channel)

            shipping_fee = cls._to_decimal(cls._first_non_empty(delivery.get("price"), delivery.get("Price"), 0))
            shipping_code = cls._first_non_empty(delivery.get("deliveryCode"), delivery.get("DeliveryCode"))
            status_val = cls._resolve_invoice_status(invoice=invoice, delivery=delivery)
            invoice_status_val = cls._resolve_invoice_own_status(invoice=invoice)
            weight_val = cls._to_decimal(cls._first_non_empty(delivery.get("weight"), delivery.get("Weight"), 0))

            invoice_code = cls._first_non_empty(invoice.get("code"), invoice.get("invoiceCode"))
            invoice_id = cls._to_int(cls._first_non_empty(invoice.get("id"), invoice.get("invoiceId")))
            order_code = cls._first_non_empty(invoice.get("orderCode"), invoice.get("OrderCode"))
            branch_id = cls._first_non_empty(invoice.get("branchId"), invoice.get("BranchId"))
            payment_method = cls._extract_payment_method(invoice)
            created_dt = cls._parse_dt(cls._first_non_empty(invoice.get("createdDate"), invoice.get("CreatedDate")))
            modified_dt = cls._parse_dt(cls._first_non_empty(invoice.get("modifiedDate"), invoice.get("ModifiedDate")))
            purchase_dt = cls._parse_dt(cls._first_non_empty(invoice.get("purchaseDate"), invoice.get("PurchaseDate")))
            dimensions = cls._build_dimensions(delivery, invoice)

            name = cls._first_non_empty(invoice.get("customerName"), delivery.get("receiver"), "Unknown")
            phone = cls._first_non_empty(invoice.get("customerPhone"), delivery.get("contactNumber"))

            matched_by_shipping_on_old_channel = False
            if existing is None:
                order = Order.objects.create(
                    channel=order_channel,
                    kiotviet_channel=kv_channel,
                    customer=customer,
                    staff=staff,
                    name=name,
                    phone=phone,
                    raw_address=address["address"],
                    address=address["address"],
                    ward=address["ward"],
                    district=address["district"],
                    province=address["province"],
                    ward_code=address["ward_code"],
                    district_code=address["district_code"],
                    province_code=address["province_code"],
                    kiotviet_location=address.get("kiotviet_location", ""),
                    kiotviet_ward=address.get("kiotviet_ward", ""),
                    address_level2=address.get("address_level2", ""),
                    ward_level2=address.get("ward_level2", ""),
                    province_level2=address.get("province_level2", ""),
                    ward_code_level2=address.get("ward_code_level2", ""),
                    province_code_level2=address.get("province_code_level2", ""),
                    shipping_fee=shipping_fee,
                    shipping_code=shipping_code,
                    status=status_val if status_val is not None else 0,
                    invoice_status=invoice_status_val,
                    weight=weight_val,
                    dimensions=dimensions,
                    branch_id=branch_id,
                    payment_method=payment_method,
                    order_code=order_code,
                    invoice_code=invoice_code,
                    invoice_id=invoice_id,
                    kiotviet_purchase_date=purchase_dt,
                    old_invoice_code=invoice_code if bool(kv_channel.old) else "",
                    old_id=invoice_id if bool(kv_channel.old) else None,
                    external_order_id=invoice_code or str(invoice_id or ""),
                    channel_owner="kiotviet",
                )
            else:
                order = existing
                previous_status = int(order.status)
                matched_by_shipping_on_old_channel = bool(
                    bool(kv_channel.old)
                    and shipping_code
                    and str(order.shipping_code or "").strip() == str(shipping_code).strip()
                )
                if matched_by_shipping_on_old_channel:
                    # KV cũ chỉ là bản mirror của KV mới (A → B). Khi sync/webhook từ B
                    # match về order của A theo shipping_code, chỉ ghi old_invoice_code/
                    # old_id để trace; KHÔNG cho data của B ghi đè field nào của A.
                    order.old_invoice_code = invoice_code
                    order.old_id = invoice_id
                    order.save(update_fields=["old_invoice_code", "old_id", "updated_at"])
                else:
                    order.channel = order_channel
                    order.kiotviet_channel = kv_channel
                    order.customer = customer
                    order.staff = staff
                    order.name = name
                    order.phone = phone
                    order.raw_address = address["address"]
                    order.address = address["address"]
                    order.ward = address["ward"]
                    order.district = address["district"]
                    order.province = address["province"]
                    order.ward_code = address["ward_code"]
                    order.district_code = address["district_code"]
                    order.province_code = address["province_code"]
                    order.kiotviet_location = address.get("kiotviet_location", "")
                    order.kiotviet_ward = address.get("kiotviet_ward", "")
                    order.address_level2 = address.get("address_level2", "")
                    order.ward_level2 = address.get("ward_level2", "")
                    order.province_level2 = address.get("province_level2", "")
                    order.ward_code_level2 = address.get("ward_code_level2", "")
                    order.province_code_level2 = address.get("province_code_level2", "")
                    order.shipping_fee = shipping_fee
                    order.shipping_code = shipping_code
                    if status_val is not None:
                        order.status = status_val
                    if invoice_status_val is not None:
                        order.invoice_status = invoice_status_val
                    order.weight = weight_val
                    if dimensions:
                        order.dimensions = dimensions
                    order.branch_id = branch_id
                    if payment_method:
                        order.payment_method = payment_method
                    had_invoice_code = bool(str(order.invoice_code or "").strip())
                    order.order_code = order_code
                    order.invoice_code = invoice_code
                    order.invoice_id = invoice_id
                    if purchase_dt is not None:
                        order.kiotviet_purchase_date = purchase_dt
                    if bool(kv_channel.old):
                        order.old_invoice_code = invoice_code
                        order.old_id = invoice_id
                    order.external_order_id = invoice_code or str(invoice_id or "")
                    order.channel_owner = "kiotviet"
                    # `save()` trần ghi TẤT CẢ field theo bản đọc đầu hàm. Nếu một webhook
                    # reattach chạy song song vừa ghi thêm mã chết vào mảng, bản ghi này sẽ
                    # xoá mất mã đó → sự kiện huỷ của mã chết lại sinh dòng đơn trùng. Đọc
                    # lại mảng ngay trước khi ghi (audit 2026-07-14 vòng 3).
                    order.reattached_invoice_codes = (
                        Order.objects.filter(pk=order.pk)
                        .values_list("reattached_invoice_codes", flat=True)
                        .first()
                        or []
                    )
                    order.save()
                    if not had_invoice_code and invoice_code:
                        # Mốc "đã tạo hóa đơn" — chỉ lần gán invoice_code ĐẦU TIÊN
                        # (spec 2026-08-04-order-milestone-snapshots).
                        from apps.orders.application.services.milestone_snapshot_service import (
                            capture_invoice_snapshot,
                        )

                        capture_invoice_snapshot(order)

                    # Import/webhook-reattach có thể ĐỔI trạng thái đơn đã tồn tại — kể cả
                    # hồi sinh đơn đang huỷ (KiotViet huỷ-xuất lại hoá đơn: X huỷ trước,
                    # X.01 reattach về chính đơn này). `save()` trần KHÔNG phát sự kiện nên
                    # subscriber kho không biết: tồn đã được CỘNG LẠI lúc đơn lật huỷ, còn
                    # hàng thì vẫn đang trên đường giao → tồn phồng vĩnh viễn. Phát
                    # OrderStatusChanged đúng như đường webhook để kho tự cân lại.
                    if status_val is not None and int(status_val) != previous_status:
                        event_bus.publish_on_commit(
                            OrderStatusChanged(
                                order_id=order.id,
                                old_status=previous_status,
                                new_status=int(status_val),
                                source=StatusChangeSource.KIOTVIET_SYNC,
                            )
                        )

            if not matched_by_shipping_on_old_channel:
                # Lấy total_amount từ field "Total" của invoice, không tính từ invoice details
                items_total = cls._to_decimal(cls._first_non_empty(invoice.get("total"), invoice.get("Total"), 0))
                total_qty, product_summary, sku_summary = cls._upsert_products_and_items(
                    order, invoice, page_cache
                )

                order.quantity = total_qty
                order.total_amount = items_total
                order.total_after_discount = items_total
                order.product = product_summary
                order.sku = sku_summary
                order.save(
                    update_fields=[
                        "quantity",
                        "total_amount",
                        "total_after_discount",
                        "product",
                        "sku",
                        "updated_at",
                    ]
                )
                dt_updates = {}
                if created_dt is not None:
                    dt_updates["created_at"] = created_dt
                if modified_dt is not None:
                    dt_updates["updated_at"] = modified_dt
                if dt_updates:
                    Order.objects.filter(id=order.id).update(**dt_updates)

        return outcome
