import time
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.services import CustomerService, normalize_name, normalize_phone
from apps.orders.address_catalog import map_to_catalog
from apps.orders.address_level2 import (
    resolve_kiotviet_names,
    resolve_level2_edit,
    resolve_level2_fields,
)
from apps.orders.address_parser import format_standard_address, normalize_raw_address, parse_legacy_level3
from apps.orders.contracts import OrderEventContract
from apps.orders.contracts.order_events import StatusChangeSource
from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE, PRE_DISPATCH_DELIVERY_CODES
from apps.orders.domain.line_discount import price_line
from apps.orders.meta_attribution import meta_attribution_fields
from apps.orders.models import Order, OrderItem, OrderRecallEntry, OrderSource, OrderStatus, OrderVat
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS
from apps.orders.product_parser import derive_quantity, derive_sku, derive_total_amount, normalize_landing_product_id
from apps.orders.sheet_rules import STAFF_INGEST_DESIGNATED_EVENT, TRACKED_ORDER_FIELDS
from apps.sysconfig import sysconfig

from . import journey_items_service
from .order_service_helpers import OrderServiceHelpers
from .order_service_shared import logger

# Nguồn đơn có cột `idnguonkhachhang` bên Foxia (trùng `FoxiaLinkKind`, trừ `resale`
# chưa có nguồn đơn). Khai ở đây thay vì import `apps.foxia` (layering: foxia nằm
# TRÊN orders); test_foxia_order_push chốt danh sách này khớp với cột thật.
FOXIA_MANUAL_PUSH_SOURCES = frozenset({"landing", "messenger", "zalo", "hotline"})


def _has_dispatched_delivery_state(order: Order) -> bool:
    """Đơn đang ở trạng thái nghiệp vụ "Đã xuất kho"? (đơn chưa có assessment → False)"""
    assessment = getattr(order, "assessment", None)
    return getattr(assessment, "delivery_business_status_code", None) == DISPATCHED_DELIVERY_CODE


def resolve_landing_total_after_discount(
    total_amount: Decimal,
    discount_amount: Decimal,
    explicit: Decimal | None,
) -> Decimal:
    """Số sau giảm giá của đơn landing (spec 2026-08-28).

    Bên gửi chốt ``total_after_discount`` thì dùng NGUYÊN VĂN, kể cả khi lệch với
    ``total_amount - discount_amount``: payload là nguồn sự thật, CRM không sửa số
    của họ. Thiếu thì tính bù.

    Nhánh kẹp 0 chỉ tới được khi tổng tiền do hệ thống tự suy ra (parse text /
    bảng giá kênh) — payload chốt ``total_amount`` mà giảm giá vượt đã bị
    serializer chặn bằng 400 từ tầng request. Tới đây thì đơn đã nhận (HTTP 200
    đã trả), không còn đường báo lỗi, nên kẹp về 0 + log thay vì để số âm chảy
    vào fct_order_enriched và Customer 360.
    """
    if explicit is not None:
        return explicit
    remaining = total_amount - discount_amount
    if remaining < 0:
        logger.warning(
            "Landing ingest discount exceeds derived total, clamped to 0 "
            "(total_amount=%s, discount_amount=%s)",
            total_amount,
            discount_amount,
        )
        return Decimal("0")
    return remaining


class OrderServiceCommands(OrderServiceHelpers):
    @staticmethod
    @transaction.atomic
    def create_order_from_landing(payload: dict, *, created_via: str = "landing_ingest") -> Order:
        product_raw = payload.get("product", "") or ""
        derived_quantity = derive_quantity(product_raw)
        derived_total_amount = derive_total_amount(product_raw)
        explicit_quantity = payload.get("quantity")
        explicit_total_amount = (
            OrderServiceCommands._decimal(payload["total_amount"])
            if payload.get("total_amount") is not None
            else None
        )
        # Bộ tiền do bên gửi chốt. `explicit_discount is None` (không gửi) và
        # `Decimal("0")` (gửi số 0) dẫn tới cùng một cột DB, nhưng phải giữ tách
        # nhau ở đây để `resolve_landing_total_after_discount` biết có gì để tính bù.
        explicit_discount = (
            OrderServiceCommands._decimal(payload["discount_amount"])
            if payload.get("discount_amount") is not None
            else None
        )
        explicit_total_after_discount = (
            OrderServiceCommands._decimal(payload["total_after_discount"])
            if payload.get("total_after_discount") is not None
            else None
        )
        discount_amount = explicit_discount if explicit_discount is not None else Decimal("0")

        # Resolve kênh SỚM (trước khi tính items_total): items_total cần biết bảng
        # giá của kênh (resolve_price_book(channel)) nên channel phải có mặt trước
        # khối tính bên dưới. Không có gì trước đây phụ thuộc vào việc channel được
        # resolve muộn — dời lên an toàn.
        channel_id_raw = payload.get("channel_id")
        try:
            channel_id = int(channel_id_raw) if channel_id_raw is not None else None
        except (TypeError, ValueError):
            channel_id = None
        channel = (
            Channel.objects.filter(id=channel_id, deleted_at__isnull=True).first()
            if channel_id
            else None
        )
        if channel is None:
            raise ValueError(f"Kênh bán không tồn tại: channel_id={channel_id_raw}")

        # product_id: int (kể cả chuỗi "3681"/"3.681" landing gửi) → product id,
        # str "SKU" → single SKU, "SKU1,SKU2" → multiple SKUs (qty=1 each).
        product_id_raw = normalize_landing_product_id(payload.get("product_id"))
        if explicit_quantity is not None:
            landing_quantity = max(1, int(explicit_quantity))
        else:
            landing_quantity = max(1, int(derived_quantity or 1))
        matched_items: list = []  # list of (Product, quantity) tuples
        if isinstance(product_id_raw, int):
            product = OrderServiceCommands._find_product(product_id=product_id_raw)
            if product is not None:
                matched_items.append((product, landing_quantity))
            else:
                logger.warning("Landing product_id not found in catalog: product_id=%s", product_id_raw)
        elif isinstance(product_id_raw, str) and product_id_raw.strip():
            skus = [chunk.strip() for chunk in product_id_raw.split(",") if chunk.strip()]
            single_sku = len(skus) == 1
            for sku in skus:
                product = OrderServiceCommands._find_product(sku=sku)
                if product is None:
                    logger.warning("Landing product sku not found in catalog: sku=%s", sku)
                    continue
                qty = landing_quantity if single_sku else 1
                matched_items.append((product, qty))

        if matched_items:
            from apps.orders.application.services.price_book_service import (
                package_prices,
                resolve_price_book,
            )

            product_text = ", ".join(f"{p.name} x{qty}" for p, qty in matched_items)
            derived_sku = ", ".join(p.sku for p, _ in matched_items)
            derived_quantity = sum(qty for _, qty in matched_items)
            # Bảng giá của kênh quyết định đơn giá — resolve + tra giá theo LÔ một
            # lần cho toàn bộ sản phẩm (không lặp unit_price_for từng dòng, tránh
            # N+1). `book`/`matched_prices` được tái dùng ở khối tạo OrderItem bên
            # dưới thay vì tra lại lần nữa.
            book = resolve_price_book(channel)
            matched_prices = package_prices([p.id for p, _ in matched_items], book)

            def _matched_unit_price(product) -> Decimal:
                price = matched_prices.get((product.id, 1))
                return price if price is not None else OrderServiceCommands._decimal(product.price)

            # Định giá mọi dòng MỘT lần, tái dùng ở khối tạo OrderItem. Giảm giá dòng tự
            # động (SL 2 → −200k) bỏ qua khi bên gửi đã chốt tiền (payload là nguồn sự thật).
            auto_discount = explicit_total_amount is None and explicit_total_after_discount is None
            priced_lines = [price_line(qty, _matched_unit_price(p), auto_discount=auto_discount) for p, qty in matched_items]
            items_total = sum((line.line_total for line in priced_lines), Decimal("0"))
            if not derived_total_amount or derived_total_amount <= 0:
                derived_total_amount = int(items_total)
            derived_weight, derived_dimensions = OrderServiceCommands._derive_weight_dimensions(matched_items)
        else:
            product_text = ""
            derived_sku = ""
            derived_weight = Decimal("0")
            derived_dimensions = ""

        # Explicit payload fields win over values derived from text/items.
        if explicit_total_amount is not None:
            derived_total_amount = explicit_total_amount
        if explicit_quantity is not None and not matched_items:
            derived_quantity = max(1, int(explicit_quantity))

        # Loại bỏ các placeholder "không có địa chỉ giao hàng" do nguồn đổ đơn
        # gửi sang: lưu rỗng để đơn vào đúng tab tư vấn (không có địa chỉ).
        raw_address = normalize_raw_address(payload.get("raw_address", ""))
        normalized_address = parse_legacy_level3(raw_address)
        mapped_input = {
            "address": normalized_address.get("address", ""),
            "ward": normalized_address.get("ward", ""),
            "district": normalized_address.get("district", ""),
            "province": normalized_address.get("province", ""),
            "ward_code": str(normalized_address.get("ward_code", "")).strip(),
            "district_code": str(normalized_address.get("district_code", "")).strip(),
            "province_code": str(normalized_address.get("province_code", "")).strip(),
        }
        mapped_address = map_to_catalog(mapped_input, raw_input=raw_address)
        logger.debug(
            "Landing address parsed+mapped: parsed=%s mapped_input=%s mapped=%s",
            normalized_address,
            mapped_input,
            mapped_address,
        )
        resolved_ward = mapped_address.get("ward") or normalized_address.get("ward") or ""
        resolved_district = mapped_address.get("district") or normalized_address.get("district") or ""
        resolved_province = mapped_address.get("province") or normalized_address.get("province") or ""
        resolved_ward_code = (
            mapped_address.get("ward_code") or mapped_input.get("ward_code") or ""
        )
        resolved_district_code = (
            mapped_address.get("district_code") or mapped_input.get("district_code") or ""
        )
        resolved_province_code = (
            mapped_address.get("province_code") or mapped_input.get("province_code") or ""
        )
        if not (resolved_province_code or resolved_district_code or resolved_ward_code):
            logger.warning(
                "Landing address mapping returned empty codes: raw_address=%s parsed=%s mapped=%s",
                raw_address,
                normalized_address,
                mapped_address,
            )

        phone_raw = str(payload.get("phone", "")).strip()
        normalized = normalize_phone(phone_raw) if phone_raw else ""
        customer_full_name = normalize_name(payload.get("name", ""))
        if normalized:
            customer = CustomerService.get_or_create_by_phone(
                phone=phone_raw,
                full_name=customer_full_name or "Unknown",
            )
            customer_needs_update = False
            resolved_full_name = customer_full_name or customer.full_name
            if customer.full_name != resolved_full_name:
                customer.full_name = resolved_full_name
                customer_needs_update = True
            if customer.phone_raw != phone_raw:
                customer.phone_raw = phone_raw
                customer_needs_update = True
            if customer_needs_update:
                customer.save(update_fields=["full_name", "phone_raw"])
        else:
            # No (valid) phone — create a standalone customer without dedupe lookup.
            customer = Customer.objects.create(
                full_name=customer_full_name,
                phone_raw=phone_raw,
                phone_normalized="",
            )

        url_page_value = payload.get("url_page", "") or ""
        # channel đã được resolve ở đầu hàm (cần cho items_total ở trên).

        # Cổng dedup nguyên tử: khóa hàng claim của số điện thoại trong chính
        # transaction này rồi kiểm tra lại đơn trùng. Chặn TOCTOU khi hai ingest
        # đồng thời cùng vượt qua evalu() ở tầng view trước khi đơn được tạo.
        # Raise DuplicateOrderError -> task coi như bỏ qua việc tạo đơn.
        from apps.orders.application.services.lead_filter_service import LeadFilterService

        landing_product_ids = [p.id for p, _ in matched_items]
        LeadFilterService.claim_or_reject(
            phone_raw=phone_raw,
            channel=channel,
            product_ids=landing_product_ids,
        )

        # Địa chỉ 2 cấp + Khu vực/Phường xã Kiot (ưu tiên tỉnh mới/phường mới)
        level2_fields = resolve_level2_fields(raw_address, normalized_address)
        kv_resolved = resolve_kiotviet_names(normalized_address, raw_address, level2_fields)

        order = Order.objects.create(
            channel=channel,
            name=normalize_name(payload.get("name", "")),
            phone=phone_raw,
            raw_address=raw_address,
            # raw_address đã normalize (placeholder "không có địa chỉ" → rỗng):
            # rỗng nghĩa là đơn ingest về không có địa chỉ thật.
            has_address=bool(raw_address),
            address=format_standard_address(normalized_address),
            ward=resolved_ward,
            district=resolved_district,
            province=resolved_province,
            ward_code=resolved_ward_code,
            district_code=resolved_district_code,
            province_code=resolved_province_code,
            kiotviet_location=kv_resolved.get("locationName", ""),
            kiotviet_ward=kv_resolved.get("wardName", ""),
            **level2_fields,
            product=product_text,
            product_raw=product_raw,
            sku=derived_sku,
            quantity=derived_quantity,
            channel_owner=channel.owner or "",
            url_page=url_page_value,
            **meta_attribution_fields(url_page_value),
            customer=customer,
            total_amount=derived_total_amount,
            discount_amount=discount_amount,
            # Đơn KHÔNG khớp sản phẩm nào trong catalog không đi qua khối đồng bộ
            # từ dòng hàng bên dưới, nên phải chốt số ngay tại đây — trước 2026-08-28
            # cột này để mặc định 0 dù total_amount có số, làm hụt mọi báo cáo
            # doanh thu đọc total_after_discount.
            total_after_discount=resolve_landing_total_after_discount(
                OrderServiceCommands._decimal(derived_total_amount),
                discount_amount,
                explicit_total_after_discount,
            ),
            # Snapshot bất biến: đúng số tiền ingest tính được (payload tường minh
            # → parse product_raw → số lượng × giá bảng giá kênh). Spec 2026-08-04.
            # Giữ nghĩa cũ = tổng TRƯỚC giảm giá, không trừ discount_amount.
            landing_confirmed_amount=derived_total_amount,
            shipping_fee=payload.get("shipping_fee", 0),
            weight=derived_weight,
            dimensions=derived_dimensions,
            # Tôn trọng order_source từ payload (đã được serializer normalize);
            # chỉ fallback "landing" khi thiếu/không hợp lệ để giữ đúng luồng landing.
            order_source=OrderSource.normalize(
                payload.get("order_source"), default=OrderSource.LANDING
            ),
            # Sale do bên gửi chỉ định (đã resolve ở OrderIngestService). Gán NGAY
            # lúc create, không gán sau: `assign_if_unassigned` bên dưới return sớm
            # khi đã có staff nên đơn không bị chia tự động, và
            # `build_order_created_row` ở cuối hàm mang luôn staff_id/tên sale nên
            # FE nhận realtime thấy đúng người phụ trách ở dòng vừa hiện.
            staff_id=payload.get("staff_id"),
            show_orders=False,
        )
        if matched_items:
            items_to_create: list[OrderItem] = []
            for (product, qty), line in zip(matched_items, priced_lines, strict=True):
                items_to_create.append(
                    OrderItem(
                        order=order,
                        product=product,
                        product_name=product.name,
                        sku=product.sku,
                        quantity=qty,
                        unit_price=line.unit_price,
                        discount=line.discount,
                        line_total=line.line_total,
                    )
                )
            OrderItem.objects.bulk_create(items_to_create)
            sync_total = explicit_total_amount if explicit_total_amount is not None else items_total
            if explicit_total_after_discount is not None:
                # Bên gửi đã chốt số sau giảm giá → chỉ đồng bộ total_amount.
                # `_sync_order_totals_from_items` là helper DÙNG CHUNG và luôn
                # tính lại total_after_discount từ Σ line_total, gọi nó ở đây sẽ
                # xoá số vừa chốt ngay trong cùng transaction.
                order.total_amount = sync_total
                order.total_after_discount = explicit_total_after_discount
            else:
                OrderServiceCommands._sync_order_totals_from_items(order, sync_total)
                # Helper không kẹp âm; đi qua đây một lần nữa để dùng chung đúng
                # một luật với nhánh đơn không có dòng hàng ở trên.
                order.total_after_discount = resolve_landing_total_after_discount(
                    order.total_amount, discount_amount, None
                )
            order.save(update_fields=["total_amount", "total_after_discount", "updated_at"])

        # Chụp mốc "đơn đổ về" SAU khi tổng tiền đã sync từ items — snapshot phải
        # mang đúng số ingest chốt được, cùng nguồn với landing_confirmed_amount.
        from apps.orders.application.services.milestone_snapshot_service import (
            capture_created_snapshot,
        )

        if explicit_total_amount is not None or explicit_total_after_discount is not None:
            landing_amount_source = "payload"
        elif derive_total_amount(product_raw) > 0:
            landing_amount_source = "text"
        elif derived_total_amount and derived_total_amount > 0:
            landing_amount_source = "price_book"
        else:
            landing_amount_source = "none"
        capture_created_snapshot(order, amount_source=landing_amount_source)

        created_at_override = payload.get("created_at_override")
        if created_at_override is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at_override)
            order.created_at = created_at_override
        journey_items_service.log_order_created(order, source=created_via)  # ingest: không có người tạo

        # Sale được bên gửi chỉ định sẵn (staff_id đã resolve từ staff_username ở
        # OrderIngestService) → ghi dấu vết audit trên timeline. Đường ingest dùng
        # token chung, không có actor thao tác nên actor=None; loại event RIÊNG
        # (không phải STAFF_ASSIGNED_EVENT) vì đây không phải bộ chia tự động gán.
        if payload.get("staff_id"):
            OrderServiceHelpers.log_staff_change(
                order=order,
                from_staff_id=None,
                to_staff_id=order.staff_id,
                actor=None,
                event_type=STAFF_INGEST_DESIGNATED_EVENT,
            )

        from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

        LeadAssignmentService.assign_if_unassigned(order)

        from apps.contact_flow.application.services.entry_service import OperationEntryService

        try:
            OperationEntryService.enter_on_new_order(order)
        except Exception:  # noqa: BLE001 — entry flow không được phá ingest
            logger.exception("contact-flow enter_on_new_order failed for order %s", order.id)

        from apps.orders.application.services.order_event_publisher import build_order_created_row

        OrderServiceCommands._push_event_on_commit(
            OrderEventContract.build_order_created(
                order_id=order.id,
                channel_code=channel.code,
                status=order.status,
                source="orders.service.create_order_from_landing",
                row=build_order_created_row(order),
            )
        )
        OrderServiceCommands._schedule_lark_notification(channel_name=channel.name, order=order)
        OrderServiceCommands._schedule_vat_invoice_notification(order)
        # Đẩy khách sang Foxia (spec 2026-09-09). Chỉ luồng landing — đơn tạo tay,
        # sửa đơn, import Excel, KiotViet CỐ Ý không đẩy.
        OrderServiceCommands._schedule_foxia_push(order)
        try:
            from apps.orders.application.services.lead_filter_service import LeadFilterService

            LeadFilterService.register_claim(phone_raw=phone_raw, channel=channel, order=order)
        except Exception:
            logger.exception(
                "Failed to register phone claim after landing ingest (order_id=%s)", order.id
            )
        logger.info(
            "Created order from landing payload: order_id=%s channel_code=%s phone=%s product_id=%s items=%s",
            order.id,
            channel.code,
            normalized,
            product_id_raw,
            [p.sku for p, _ in matched_items],
        )
        return order

    @staticmethod
    @transaction.atomic
    def update_order_inline(order: Order, data: dict, actor, *, verdict=None, occurred_at=None, journey_source=""):
        """``verdict`` (``CancelVerdict`` | None): kết quả xác minh KiotViet mà caller VỪA lấy
        NGOÀI transaction. Chỉ ``CancelVerdict.OK`` / ``UNVERIFIABLE`` mới cho phép bỏ qua
        round-trip HTTP ở guard bên dưới (guard vẫn LUÔN check DB tươi). Bắt buộc cho
        ``dispatch_orders_batch`` + đường PATCH inline: hàm này chạy trong ``transaction.atomic``
        nên một round-trip HTTP ở đây sẽ giữ transaction + lock mở suốt thời gian chờ mạng.
        Truyền verdict thật (không phải cờ bool) để không ai vô hiệu hoá guard bằng một
        ``True`` đặt nhầm — verdict lạ/None → guard tự đi hỏi KiotViet.

        ``occurred_at``: thời điểm hàng thực sự rời kho, chỉ dùng cho nhánh trừ tồn
        (``issue_for_order``). Luồng khớp mã chờ truyền thời điểm QUÉT. Nhánh
        un-dispatch bên dưới KHÔNG nhận: hoàn tồn xảy ra ngay lúc bỏ trạng thái. (``journey_source``: xem helpers.)
        """
        customer = order.customer
        customer_fields_to_update: list[str] = []
        crm_payload = data.pop("crm", None)
        recall_payload = data.pop("recall", None)
        recall_entries_payload = data.pop("recall_entries", None)

        # Trạng thái giao vận TRƯỚC khi upsert assessment — cần cho đường un-dispatch
        # (chỉ hoàn tồn khi rời khỏi "dispatched", không phải mọi thay đổi).
        prev_delivery_code = None
        if crm_payload and "delivery_business_status_code" in crm_payload:
            prev_assessment = getattr(order, "assessment", None)
            prev_delivery_code = getattr(prev_assessment, "delivery_business_status_code", None)

        # Đơn ĐÃ xuất kho thì không đổi được kho xuất: tồn đã trừ ở kho cũ, đổi FK ở đây
        # KHÔNG dời sổ tồn sang kho mới → lệch tồn cả hai kho (kho cũ giữ phần đã trừ,
        # kho mới không trừ gì). Trang chi tiết đã khoá sẵn ô chọn
        # (frontend/src/features/orders/fulfillment-warehouse-lock.ts); đây là chốt chặn
        # thật cho mọi đường PATCH. Muốn đổi kho thì bỏ trạng thái "Đã xuất kho" trước —
        # nhánh un-dispatch bên dưới sẽ hoàn tồn.
        if "fulfillment_warehouse" in data:
            next_warehouse = data["fulfillment_warehouse"]
            next_warehouse_id = getattr(next_warehouse, "id", next_warehouse)
            if next_warehouse_id != order.fulfillment_warehouse_id:
                current_assessment = getattr(order, "assessment", None)
                current_delivery_code = (
                    getattr(current_assessment, "delivery_business_status_code", None) or ""
                )
                if current_delivery_code not in PRE_DISPATCH_DELIVERY_CODES:
                    from rest_framework.exceptions import ValidationError

                    raise ValidationError(
                        {
                            "fulfillment_warehouse": (
                                "Đơn đã xuất kho — không đổi được kho xuất. "
                                "Hãy bỏ trạng thái Đã xuất kho trước."
                            )
                        }
                    )

        next_name = normalize_name(data.get("name", "")) if data.get("name") is not None else None
        if next_name is not None:
            data["name"] = next_name
        next_address = data.get("address")
        next_phone = data.get("phone")

        if next_phone is not None:
            normalized_phone = normalize_phone(str(next_phone))
            if not normalized_phone:
                raise ValueError("Invalid phone payload")

            customer = CustomerService.get_or_create_by_phone(
                phone=str(next_phone),
                full_name=str(next_name or order.name or "Unknown"),
            )
            if order.customer_id != customer.id:
                data["customer"] = customer

            if customer.phone_raw != str(next_phone):
                customer.phone_raw = str(next_phone)
                customer_fields_to_update.append("phone_raw")

        if next_name is not None and customer.full_name != next_name:
            customer.full_name = next_name
            customer_fields_to_update.append("full_name")

        if "customer_gender" in data:
            next_customer_gender = data.pop("customer_gender")
            if customer.gender != next_customer_gender:
                customer.gender = next_customer_gender
                customer_fields_to_update.append("gender")

        if customer_fields_to_update:
            customer.save(update_fields=list(dict.fromkeys(customer_fields_to_update)))

        old_status = order.status
        items_data = data.pop("items", None)
        # Only re-resolve when raw_address itself changed; otherwise respect the
        # user's edits to `address` / kiotviet_* instead of overwriting them.
        raw_address_changed = "raw_address" in data
        # If user explicitly sent kiotviet_location/kiotviet_ward, don't auto-resolve
        has_explicit_kv = "kiotviet_location" in data or "kiotviet_ward" in data

        # Ảnh chụp TRƯỚC setattr — journey cần cả giá trị cũ. Chụp CẢ danh sách theo
        # dõi chứ không chỉ field có trong payload: nhánh `raw_address` bên dưới tự
        # nắn lại `address`/`kiotviet_*`, chụp theo payload sẽ bỏ sót đúng nhánh đó.
        # Field không đổi thì so sánh sau save cũng bằng nhau → không sinh event thừa.
        prev_staff_id = order.staff_id
        staff_in_payload = "staff" in data
        prev_tracked_values = {
            field: getattr(order, field, None) for field in TRACKED_ORDER_FIELDS
        }

        for field, value in data.items():
            setattr(order, field, value)

        # Ô "Địa chỉ 2 cấp" sale sửa tay thắng nhánh suy từ địa chỉ thô bên dưới:
        # tách tỉnh mới/phường mới từ chính chuỗi đó, Khu vực/Phường xã Kiot đổi
        # theo khi tra được danh mục (không tra được thì giữ cũ — gõ sai chính tả
        # mà xoá trống là hoá đơn Kiot fail).
        level2_edited = "address_level2" in data
        if level2_edited:
            edited = resolve_level2_edit(data["address_level2"])
            kv_location = edited.pop("kiotviet_location")
            kv_ward = edited.pop("kiotviet_ward")
            for field, value in edited.items():
                setattr(order, field, value)
                data[field] = value
            if not has_explicit_kv and kv_location and kv_ward:
                order.kiotviet_location = kv_location
                order.kiotviet_ward = kv_ward
                data["kiotviet_location"] = kv_location
                data["kiotviet_ward"] = kv_ward

        if raw_address_changed and (order.raw_address or ""):
            raw_addr = order.raw_address or ""
            parsed = parse_legacy_level3(raw_addr)
            # Địa chỉ 2 cấp suy từ địa chỉ thô → luôn tính lại, kể cả khi sale đã
            # chọn tay Khu vực/Phường xã (trừ khi chính ô 2 cấp cũng được sửa tay).
            level2_fields = resolve_level2_fields(raw_addr, parsed)
            if not level2_edited:
                for field, value in level2_fields.items():
                    setattr(order, field, value)
                    data[field] = value
            if not has_explicit_kv:
                order.address = format_standard_address(parsed)
                data["address"] = order.address
                if level2_edited:
                    level2_fields = {f: getattr(order, f) for f in level2_fields}
                kv_resolved = resolve_kiotviet_names(parsed, raw_addr, level2_fields)
                order.kiotviet_location = kv_resolved.get("locationName", "")
                order.kiotviet_ward = kv_resolved.get("wardName", "")
                data["kiotviet_location"] = order.kiotviet_location
                data["kiotviet_ward"] = order.kiotviet_ward

        order.save(update_fields=[*data.keys(), "updated_at"])

        # Journey cho thay đổi NGOÀI khối CRM: trước đây chỉ `crm` mới sinh event nên
        # đổi sale / mã vận đơn / trạng thái không để lại dấu vết nào truy ngược được.
        if staff_in_payload:
            OrderServiceHelpers.log_staff_change(
                order=order,
                from_staff_id=prev_staff_id,
                to_staff_id=order.staff_id,
                actor=actor,
            )
        if prev_tracked_values:
            OrderServiceHelpers.log_tracked_field_changes(
                order=order, previous_values=prev_tracked_values, actor=actor
            )

        # Đặt cờ VAT (cá nhân/công ty) khi sửa đơn → báo kế toán (idempotent).
        if "vat" in data:
            OrderServiceCommands._schedule_vat_invoice_notification(order)

        stock_warnings: list = []
        if int(old_status) != int(order.status):
            from apps.common.events import event_bus
            from apps.orders.contracts.order_events import OrderStatusChanged

            event_bus.publish_on_commit(
                OrderStatusChanged(
                    order_id=order.id,
                    old_status=int(old_status),
                    new_status=int(order.status),
                    actor_id=getattr(actor, "id", None),
                    source=StatusChangeSource.USER,
                )
            )

        if items_data is not None:
            with journey_items_service.track_items_change(order, actor, StatusChangeSource.USER):
                created_items, items_total = OrderServiceCommands._replace_order_items(order, items_data, actor)
            # Invalidate the prefetch_related("items") cache populated by the view — after
            # delete+bulk_create, the cached list is stale and would leak old values back
            # into the serializer response.
            if hasattr(order, "_prefetched_objects_cache"):
                order._prefetched_objects_cache.pop("items", None)
            created_items = list(order.items.all())
            summary_items = [{"product_name": item.product_name, "sku": item.sku, "quantity": item.quantity} for item in created_items]
            summary_product, summary_sku, summary_quantity = OrderServiceCommands._build_order_product_summary(summary_items)
            OrderServiceCommands._sync_order_totals_from_items(order, items_total)
            order.product = summary_product
            order.sku = summary_sku
            order.quantity = summary_quantity
            order.save(update_fields=["product", "sku", "quantity", "total_amount", "total_after_discount", "updated_at"])
            # Đơn ĐÃ xuất kho mà items bị sửa → đồng bộ lại tồn đã trừ theo items
            # mới (no-op khi đơn chưa xuất — resync tự kiểm tra outstanding).
            if order.fulfillment_warehouse_id:
                from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService

                resync_warnings = StockSalesService.resync_for_order(
                    order_id=order.id,
                    warehouse_id=order.fulfillment_warehouse_id,
                    lines=[
                        SaleLine(product_id=it.product_id, quantity=it.quantity)
                        for it in created_items
                    ],
                    user_id=getattr(actor, "id", None),
                )
                stock_warnings.extend(
                    {"product_id": w.product_id, "balance_after": w.balance_after}
                    for w in resync_warnings
                )
            if old_status != order.status:
                event_payload = OrderEventContract.build_order_updated(
                    order_id=order.id,
                    channel_code=order.channel.code,
                    staff_id=order.staff_id,
                    status=order.status,
                    from_status=old_status,
                    to_status=order.status,
                    source="orders.service.update_order_inline",
                )
            else:
                event_payload = OrderEventContract.build_order_updated(
                    order_id=order.id,
                    channel_code=order.channel.code,
                    staff_id=order.staff_id,
                    status=order.status,
                    source="orders.service.update_order_inline",
                )
            OrderServiceCommands._push_event_on_commit(event_payload)

        assessment, changed_assessment_fields = OrderServiceCommands._upsert_order_assessment(
            order, crm_payload, actor, journey_source=journey_source
        )
        if assessment is not None:
            order.assessment = assessment

        if "delivery_business_status_code" in changed_assessment_fields and (
            assessment is not None
            and assessment.delivery_business_status_code == "dispatched"
        ):
            from rest_framework.exceptions import ValidationError

            # Chốt cuối TRƯỚC khi trừ tồn thật, chạy BÊN TRONG transaction ghi sổ: mọi luồng
            # trừ tồn qua hàm này (PATCH inline từ dropdown "Đã xuất kho", dispatch_orders_batch,
            # cron khớp mã) đều đi qua đây. Các luồng có xác minh từ xa còn chặn sớm hơn ở
            # ngoài transaction (rẻ hơn), nhưng đây là lớp cuối đọc DB TƯƠI → đơn bị hủy
            # trong lúc chờ HTTP vẫn bị chặn.
            OrderServiceCommands._assert_dispatch_allowed(order, verdict=verdict)

            if not order.fulfillment_warehouse_id:
                raise ValidationError(
                    {"fulfillment_warehouse": "Hãy chọn kho xuất trước khi chuyển sang Đã xuất kho."}
                )
            from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService

            lines = [
                SaleLine(product_id=it.product_id, quantity=it.quantity)
                for it in order.items.all()
            ]
            warnings = StockSalesService.issue_for_order(
                order_id=order.id,
                warehouse_id=order.fulfillment_warehouse_id,
                lines=lines,
                user_id=getattr(actor, "id", None),
                occurred_at=occurred_at,
            )
            stock_warnings.extend(
                {"product_id": w.product_id, "balance_after": w.balance_after}
                for w in warnings
            )
        elif (
            "delivery_business_status_code" in changed_assessment_fields
            and assessment is not None
            and prev_delivery_code == DISPATCHED_DELIVERY_CODE
            and (assessment.delivery_business_status_code or "") in PRE_DISPATCH_DELIVERY_CODES
        ):
            # Un-dispatch: bỏ trạng thái "Đã xuất kho" (quét nhầm/kho sửa lại)
            # → hoàn phần tồn đã trừ còn outstanding. Idempotent, đã trừ credit
            # ReturnNote nên không cộng tồn 2 lần.
            from apps.inventory.contracts.stock_sales import StockSalesService

            StockSalesService.release_for_order(
                order_id=order.id,
                user_id=getattr(actor, "id", None),
                note="Hoàn tồn do bỏ trạng thái Đã xuất kho",
            )

        recall = OrderServiceCommands._upsert_order_recall(order, recall_payload, actor)
        if recall is not None:
            order.recall = recall
        OrderServiceCommands._replace_order_recall_entries(order, recall_entries_payload, actor)

        if changed_assessment_fields:
            # Payload chốt TẠI ĐÂY (trước khi đăng ký on_commit) — nhánh dispatch chạy
            # trong lúc đang giữ row-lock Order + advisory lock 61001, không được để
            # round-trip Redis kéo dài thời gian giữ lock.
            # staff_id BẮT BUỘC có: thiếu nó thì consumer phải query DB để phân quyền
            # broadcast, đúng fanout đã gây "too many clients" (83943ff4).
            crm_event_payload = OrderEventContract.build_order_updated(
                order_id=order.id,
                channel_code=order.channel.code,
                staff_id=order.staff_id,
                status=order.status,
                sales_status_code=(
                    getattr(assessment, "sales_status_code", None) or None
                    if "sales_status_code" in changed_assessment_fields
                    else None
                ),
                source="orders.service.update_order_inline.crm",
            )
            OrderServiceCommands._push_event_on_commit(crm_event_payload)

        return stock_warnings

    @staticmethod
    def _is_cancelled_in_db(order: Order) -> bool:
        """Đơn có đang ở trạng thái hủy theo DB (đọc TƯƠI, khoá hàng nếu đang trong transaction)?

        Instance ``order`` trong bộ nhớ được fetch TRƯỚC khi gọi HTTP xác minh (vài trăm ms →
        vài giây). Webhook hủy đơn rơi đúng cửa sổ đó sẽ không hiện lên trên instance cũ →
        phải đọc lại DB, không tin bộ nhớ (TOCTOU). ``select_for_update`` giữ hàng cho tới
        khi transaction ghi sổ commit → không có cửa sổ nào giữa check và trừ tồn.
        """
        qs = Order.objects.filter(id=order.id)
        if transaction.get_connection().in_atomic_block:
            qs = qs.select_for_update()
        fresh = qs.values("status", "invoice_status").first()
        if fresh is None:
            return False
        status_value = int(fresh["status"] or 0)
        invoice_status = fresh["invoice_status"]
        if status_value != int(order.status) or invoice_status != order.invoice_status:
            # Đơn vừa đổi trạng thái dưới chân ta — cập nhật instance để log/serializer
            # không nói dối về trạng thái đã chốt.
            order.status = status_value
            order.invoice_status = invoice_status
        return status_value == int(OrderStatus.CANCELLED) or invoice_status == CANCELLED_INVOICE_STATUS

    @staticmethod
    def _assert_dispatch_allowed(order: Order, *, verdict=None) -> None:
        """Chặn xuất kho cho đơn đã hủy. Raise lỗi để transport dịch sang 409/503.

        DB tươi (rẻ) trước — có thể trễ tới 30' so với KiotViet — rồi hỏi lại nguồn sự thật.
        Không xác minh được → fail-closed (thà chặn nhầm còn hơn xuất hàng đơn hủy).
        Verifier tự heal DB khi phát hiện đơn hủy, ở đây KHÔNG heal lại.

        ``verdict``: kết quả ``CancelVerdict`` caller vừa lấy NGOÀI transaction. OK/UNVERIFIABLE
        → bỏ round-trip HTTP thứ hai (KHÔNG bỏ check DB). CANCELLED → chặn. Bất kỳ giá trị
        nào khác (kể cả None, hay một ``True`` truyền nhầm) → tự đi hỏi KiotViet: guard không
        bao giờ bị vô hiệu chỉ vì caller nói "tôi đã verify rồi".
        """
        from apps.orders.contracts.kiotviet_cancellation_port import (
            CancellationVerificationUnavailable,
            CancelVerdict,
            kiotviet_cancellation_port,
        )
        from apps.orders.exceptions import DispatchVerificationUnavailable, OrderCancelledError

        if OrderServiceCommands._is_cancelled_in_db(order):
            logger.warning(
                "dispatch blocked (order_id=%s, reason=order_cancelled, source=db, "
                "status=%s, invoice_status=%s)",
                order.id,
                order.status,
                order.invoice_status,
            )
            raise OrderCancelledError()

        if verdict is CancelVerdict.OK or verdict is CancelVerdict.UNVERIFIABLE:
            return
        if verdict is CancelVerdict.CANCELLED:
            logger.warning(
                "dispatch blocked (order_id=%s, reason=order_cancelled, source=kiotviet)", order.id
            )
            raise OrderCancelledError()

        try:
            check = kiotviet_cancellation_port().verify_not_cancelled(order)
        except CancellationVerificationUnavailable as exc:
            logger.warning(
                "dispatch blocked (order_id=%s, reason=verify_unavailable): %s", order.id, exc
            )
            raise DispatchVerificationUnavailable() from exc

        if check is CancelVerdict.CANCELLED:
            logger.warning(
                "dispatch blocked (order_id=%s, reason=order_cancelled, source=kiotviet)", order.id
            )
            raise OrderCancelledError()

    @staticmethod
    def verify_dispatch_intent(order: Order, data: dict):
        """Xác minh với KiotViet TRƯỚC khi mở transaction, cho đường PATCH inline.

        ``update_order_inline`` chạy trong ``transaction.atomic``: nếu để guard bên trong gọi
        HTTP thì mỗi lần user bấm "Đã xuất kho" là một transaction mở suốt thời gian chờ mạng
        (refresh token 30s + invoice 3s) → idle-in-transaction. Ta hỏi trước, rồi đưa verdict
        xuống cho guard.

        Trả về ``CancelVerdict`` (để truyền vào ``update_order_inline(verdict=...)``) hoặc None
        khi payload không phải là chuyển sang "Đã xuất kho" (không đụng KiotViet vô cớ).
        Raise ``OrderCancelledError`` / ``DispatchVerificationUnavailable`` → transport dịch 409/503.
        """
        from apps.orders.contracts.kiotviet_cancellation_port import (
            CancellationVerificationUnavailable,
            CancelVerdict,
            kiotviet_cancellation_port,
        )
        from apps.orders.exceptions import DispatchVerificationUnavailable, OrderCancelledError

        crm = data.get("crm") or {}
        if crm.get("delivery_business_status_code") != "dispatched":
            return None
        current = getattr(getattr(order, "assessment", None), "delivery_business_status_code", None)
        if current == "dispatched":
            # Không đổi trạng thái → guard trong service không chạy, khỏi tốn round-trip.
            return None

        if OrderServiceCommands._is_cancelled_in_db(order):
            logger.warning(
                "dispatch blocked (order_id=%s, reason=order_cancelled, source=db, "
                "status=%s, invoice_status=%s)",
                order.id,
                order.status,
                order.invoice_status,
            )
            raise OrderCancelledError()

        try:
            check = kiotviet_cancellation_port().verify_not_cancelled(order)
        except CancellationVerificationUnavailable as exc:
            logger.warning(
                "dispatch blocked (order_id=%s, reason=verify_unavailable): %s", order.id, exc
            )
            raise DispatchVerificationUnavailable() from exc

        if check is CancelVerdict.CANCELLED:
            logger.warning(
                "dispatch blocked (order_id=%s, reason=order_cancelled, source=kiotviet)", order.id
            )
            raise OrderCancelledError()
        return check

    @staticmethod
    def update_order_inline_verified(order: Order, data: dict, actor):
        """Đường PATCH inline: verify NGOÀI transaction rồi mới ghi sổ (xem verify_dispatch_intent)."""
        verdict = OrderServiceCommands.verify_dispatch_intent(order, data)
        return OrderServiceCommands.update_order_inline(order, data, actor, verdict=verdict)

    # Trần số HTTP xác minh KiotViet chạy đồng thời khi prefetch verdict cho một lô
    # xuất kho. Nhỏ để không dội rate-limit KiotViet / chiếm connection pool, đủ lớn
    # để lô 50 cache-miss (timeout 3s/con) gói trong ~25s thay vì 150s tuần tự.
    _DISPATCH_VERIFY_MAX_WORKERS = 6

    @staticmethod
    def _prefetch_dispatch_verdicts(orders: list[Order]) -> dict[int, object]:
        """Hỏi KiotViet cho cả lô TRƯỚC vòng lặp ghi sổ, song song có giới hạn.

        Verifier tự lo cache (hit → trả ngay, không HTTP; miss → HTTP rồi tự đổ verdict
        vào cache như cũ) nên ở đây chỉ cần gọi port cho từng đơn — không đọc/ghi cache
        chéo tầng. Trả về map ``order_id → CancelVerdict | Exception``: exception được
        GIỮ NGUYÊN để vòng lặp chính phân loại per-order (fail-closed từng đơn, không
        fail cả lô — đúng hành vi tuần tự cũ).

        Chạy 1 đơn thì gọi thẳng trên thread hiện tại: không tốn thread cho lô đơn lẻ,
        và các guard đo "verify không nằm trong transaction" tiếp tục quan sát đúng
        connection của request. Song song chỉ an toàn vì side-effect của verifier
        (cache set, heal đơn hủy trong DB, publish event) đều per-order và verifier vốn
        đã chạy đua giữa nhiều web worker trên prod; mỗi worker thread đóng connection
        DB của chính nó khi xong để không rò connection (bài học sự cố 2026-07-31).
        """
        from concurrent.futures import ThreadPoolExecutor

        from django.db import connections

        from apps.orders.contracts.kiotviet_cancellation_port import (
            kiotviet_cancellation_port,
        )

        def _verify_one(order: Order):
            try:
                return kiotviet_cancellation_port().verify_not_cancelled(order)
            except Exception as exc:  # noqa: BLE001 — vòng lặp chính phân loại/raise lại
                return exc

        if not orders:
            return {}
        if len(orders) == 1:
            return {orders[0].id: _verify_one(orders[0])}

        def _verify_one_threaded(order: Order):
            try:
                return _verify_one(order)
            finally:
                # Connection Django là thread-local; thread của pool chết sau batch này
                # nên phải tự đóng, không có request teardown nào dọn hộ.
                connections.close_all()

        max_workers = min(
            OrderServiceCommands._DISPATCH_VERIFY_MAX_WORKERS, len(orders)
        )
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dispatch-verify"
        ) as pool:
            outcomes = list(pool.map(_verify_one_threaded, orders))
        return {order.id: outcome for order, outcome in zip(orders, outcomes)}

    @staticmethod
    def dispatch_orders_batch(
        *, order_ids: list[int], warehouse_id: int, actor, occurred_at=None, journey_source=""
    ) -> list[dict]:
        from django.db import transaction

        from apps.inventory.application.services.stock_sales_service import (
            order_has_outstanding_stock,
        )
        from apps.orders.contracts.kiotviet_cancellation_port import (
            CancellationVerificationUnavailable,
            CancelVerdict,
            kiotviet_cancellation_port,
        )
        from apps.orders.exceptions import DispatchVerificationUnavailable, OrderCancelledError
        from apps.warehouses.models import Warehouse

        _EXPECTED = {2, 11}  # IN_DELIVERY, AWAITING_SHIPMENT
        _MISSING = object()
        warehouse = Warehouse.objects.get(id=warehouse_id)

        # Load cả lô một lần (1 query + 1 prefetch items) thay vì 2 round-trip/đơn.
        # Id trùng trong lô dùng chung một instance; id vắng mặt (không tồn tại hoặc
        # đã xoá mềm) vẫn ra dòng "not_found" như cũ ở vòng lặp dưới.
        orders_by_id: dict[int, Order] = {
            order.id: order
            for order in Order.objects.select_related(
                "channel", "customer", "kiotviet_channel", "assessment"
            )
            .prefetch_related("items")
            .filter(id__in=order_ids, deleted_at__isnull=True)
        }

        def _db_cancelled(order: Order) -> bool:
            return (
                int(order.status) == int(OrderStatus.CANCELLED)
                or order.invoice_status == CANCELLED_INVOICE_STATUS
            )

        # Chọn đơn cần hỏi KiotViet (cùng điều kiện với vòng lặp chính: sống theo DB
        # và chưa xuất kho) rồi prefetch verdict song song. `already_map` giữ kết quả
        # check sổ tồn để vòng lặp chính không query lại lần hai cho cùng lần xử lý.
        already_map: dict[int, bool] = {}
        verify_candidates: list[Order] = []
        for oid in dict.fromkeys(order_ids):  # unique, giữ nguyên thứ tự lô
            candidate = orders_by_id.get(oid)
            if candidate is None or _db_cancelled(candidate):
                continue
            candidate_already = order_has_outstanding_stock(candidate.id)
            already_map[oid] = candidate_already
            if not candidate_already:
                verify_candidates.append(candidate)
        verdict_map = OrderServiceCommands._prefetch_dispatch_verdicts(verify_candidates)

        results: list[dict] = []
        for oid in order_ids:
            order = orders_by_id.get(oid)
            if order is None:
                results.append({"order_id": oid, "ok": False, "error": "not_found"})
                continue

            # Chốt cuối trước khi trừ tồn: đơn hủy TUYỆT ĐỐI không được xuất kho.
            # Guard ở lúc quét (lookup) là chưa đủ — người quét có thể bấm Xác nhận vài phút
            # sau, và cron khớp mã pending ghi sổ KHÔNG đi qua lookup. DB có thể trễ tới 30'
            # so với KiotViet → check DB (rẻ) trước, rồi hỏi lại nguồn sự thật (fail-closed).
            # Per-order: đơn hỏng không được làm hỏng cả lô.
            if _db_cancelled(order):
                logger.warning(
                    "dispatch blocked (order_id=%s, reason=order_cancelled, source=db, "
                    "status=%s, invoice_status=%s)",
                    order.id,
                    order.status,
                    order.invoice_status,
                )
                results.append({"order_id": oid, "ok": False, "error": "order_cancelled"})
                continue

            warnings: list[str] = []
            if int(order.status) not in _EXPECTED:
                warnings.append("unexpected_status")
            # Lọc đơn ĐÃ xuất kho TRƯỚC khi hỏi KiotViet: tồn của đơn này đã trừ rồi
            # (`issue_for_order` là no-op idempotent) nên round-trip HTTP ở đây không bảo vệ
            # được gì mà vẫn kéo dài lô quét. verdict=UNVERIFIABLE → guard trong
            # `update_order_inline` bỏ round-trip thứ hai nhưng VẪN đọc DB tươi (đơn hủy đã
            # biết trong DB vẫn bị chặn). Tính theo NET (SALE - SALE_REVERSAL), không phải
            # exists(SALE): đơn từng xuất rồi bị un-dispatch (hoàn hết) có net = 0 → vẫn phải
            # verify lại KiotViet trước khi xuất lần nữa.
            # `pop` để id trùng trong lô vẫn được check sổ tồn TƯƠI ở lần gặp sau
            # (lần xuất đầu đã trừ tồn → lần sau phải thấy already=True như code cũ).
            already = already_map.pop(oid, _MISSING)
            if already is _MISSING:
                already = order_has_outstanding_stock(order.id)
            # Sổ tồn trống KHÔNG có nghĩa là chưa xuất: đơn không khớp sản phẩm nào xuất
            # kho với `lines=[]` nên không sinh bút toán. Cảnh báo theo cả trạng thái giao
            # hàng để nhân viên kho thấy "đã xuất trước đó" thay vì dấu ✓ như lần đầu.
            # KHÔNG dùng nó để bỏ round-trip xác minh: nhánh đó chỉ an toàn khi tồn đã trừ
            # thật (`issue_for_order` no-op), còn ở đây vẫn phải fail-closed như thường.
            if already or _has_dispatched_delivery_state(order):
                warnings.append("already_dispatched")
            if already:
                check = CancelVerdict.UNVERIFIABLE
            else:
                # Verdict đã prefetch song song ở trên; thiếu (id trùng / sổ tồn đổi
                # dưới chân giữa prefetch và đây) thì hỏi lại tuần tự như code cũ —
                # verifier cache 300s nên thường là cache-hit, không tốn HTTP.
                outcome = verdict_map.pop(oid, _MISSING)
                if outcome is _MISSING:
                    try:
                        outcome = kiotviet_cancellation_port().verify_not_cancelled(order)
                    except CancellationVerificationUnavailable as exc:
                        outcome = exc
                if isinstance(outcome, CancellationVerificationUnavailable):
                    logger.warning(
                        "dispatch blocked (order_id=%s, reason=verify_unavailable): %s",
                        order.id,
                        outcome,
                    )
                    results.append({"order_id": oid, "ok": False, "error": "verify_unavailable"})
                    continue
                if isinstance(outcome, BaseException):
                    # Lỗi lạ (không phải fail-closed của verifier) giữ nguyên semantics
                    # tuần tự cũ: nổ cả request, không nuốt im lặng.
                    raise outcome
                check = outcome
                if check is CancelVerdict.CANCELLED:
                    # verifier đã tự heal DB + publish event — không heal lại ở đây.
                    logger.warning(
                        "dispatch blocked (order_id=%s, reason=order_cancelled, source=kiotviet)",
                        order.id,
                    )
                    results.append({"order_id": oid, "ok": False, "error": "order_cancelled"})
                    continue
            try:
                with transaction.atomic():
                    # verdict: kết quả vừa hỏi KiotViet ở trên (NGOÀI transaction) → guard
                    # trong update_order_inline không đẩy HTTP vào transaction này, nhưng
                    # VẪN đọc lại DB (select_for_update): đơn có thể bị webhook hủy đúng
                    # trong lúc ta chờ mạng → phải chặn ở đây, không trừ tồn.
                    stock_warnings = OrderServiceCommands.update_order_inline(
                        order,
                        {
                            "fulfillment_warehouse": warehouse,
                            "crm": {"delivery_business_status_code": "dispatched"},
                        },
                        actor,
                        verdict=check,
                        occurred_at=occurred_at, journey_source=journey_source,
                    )
            except OrderCancelledError:
                # Guard trong update_order_inline thấy DB đã hủy (webhook chen vào giữa lúc
                # xác minh) — giữ per-order error code, không raise cả lô.
                results.append({"order_id": oid, "ok": False, "error": "order_cancelled"})
                continue
            except DispatchVerificationUnavailable:
                results.append({"order_id": oid, "ok": False, "error": "verify_unavailable"})
                continue
            except Exception as exc:  # ValidationError or others → fail this order only
                results.append({"order_id": oid, "ok": False, "error": str(exc)})
                continue
            results.append({
                "order_id": oid, "ok": True,
                "warnings": warnings, "stock_warnings": stock_warnings,
            })
        return results

    @staticmethod
    @transaction.atomic
    def create_order_manual(payload: dict, actor, *, created_via: str = "manual") -> Order:
        # create-service-timing: bổ dọc tiếp create-timing của view (p95 3.6s,
        # slow-log Postgres trống) — biết 3.6s nằm ở địa chỉ / khách / DB / hậu kỳ.
        _t0 = time.monotonic()
        channel = payload["channel"]
        is_koc = channel.is_koc
        crm_payload = payload.get("crm")
        phone_raw = str(payload.get("phone", "")).strip()
        normalized_phone = normalize_phone(phone_raw)
        if not normalized_phone:
            raise ValueError("Invalid phone payload")

        name = normalize_name(str(payload.get("name", "")))
        raw_address = str(payload.get("raw_address", "")).strip()
        address = str(payload.get("address", "")).strip()
        product_text = str(payload.get("product", "")).strip()
        channel_owner = str(payload.get("channel_owner", "") or channel.owner or "").strip()

        normalized_address = parse_legacy_level3(raw_address)
        mapped_address = map_to_catalog(normalized_address, raw_input=raw_address)
        resolved_ward = payload.get("ward") or mapped_address.get("ward") or normalized_address.get("ward") or ""
        resolved_district = payload.get("district") or mapped_address.get("district") or normalized_address.get("district") or ""
        resolved_province = payload.get("province") or mapped_address.get("province") or normalized_address.get("province") or ""
        resolved_ward_code = payload.get("ward_code") or mapped_address.get("ward_code") or normalized_address.get("ward_code") or ""
        resolved_district_code = (
            payload.get("district_code") or mapped_address.get("district_code") or normalized_address.get("district_code") or ""
        )
        resolved_province_code = (
            payload.get("province_code") or mapped_address.get("province_code") or normalized_address.get("province_code") or ""
        )
        _t_addr = time.monotonic()

        # Ưu tiên khớp KH theo Mã KH (import KOC). Chỉ kích hoạt khi payload có customer_code
        # nên các luồng tạo đơn khác không bị ảnh hưởng. Không tìm thấy thì fallback theo SĐT.
        customer = None
        customer_code = str(payload.get("customer_code", "")).strip()
        if customer_code:
            customer = CustomerService.get_by_code(customer_code)
        if customer is None:
            customer = CustomerService.get_or_create_by_phone(
                phone=phone_raw,
                full_name=name,
                code_prefix="KOC" if is_koc else "KH",
            )

        customer_fields_to_update: list[str] = []
        if name and customer.full_name != name:
            customer.full_name = name
            customer_fields_to_update.append("full_name")
        if customer.phone_raw != phone_raw:
            customer.phone_raw = phone_raw
            customer_fields_to_update.append("phone_raw")

        payload_customer_gender = payload.get("customer_gender")
        if payload_customer_gender is not None and customer.gender is None:
            customer.gender = payload_customer_gender
            customer_fields_to_update.append("gender")

        if customer_fields_to_update:
            customer.save(update_fields=customer_fields_to_update)
        _t_customer = time.monotonic()

        derived_sku = derive_sku(product_text)
        derived_quantity = derive_quantity(product_text)
        derived_total_amount = derive_total_amount(product_text)
        item_payloads = payload.get("items") or []

        payload_sku = str(payload.get("sku", "")).strip()
        payload_quantity = payload.get("quantity", None)
        payload_total_amount = payload.get("total_amount", None)
        payload_discount_amount = payload.get("discount_amount", 0)
        payload_booking_discount = payload.get("booking_voucher_discount", 0)
        payload_total_after_discount = payload.get("total_after_discount", None)
        if payload_total_after_discount is None:
            base_total = payload_total_amount if payload_total_amount is not None else derived_total_amount
            payload_total_after_discount = base_total - payload_discount_amount - payload_booking_discount

        # Đơn KOC loại "không trả lại" coi như đã thanh toán đủ (prepaid = total).
        if is_koc and payload.get("order_type") == "no_return":
            payload_customer_prepaid = payload_total_after_discount
        else:
            payload_customer_prepaid = payload.get("customer_prepaid", 0)

        kiotviet_channel = payload.get("kiotviet_channel")
        branch_id = str(payload.get("branch_id", "")).strip()
        if not branch_id and kiotviet_channel is not None:
            branch_ids = [item.strip() for item in (kiotviet_channel.branch_ids or []) if item and item.strip()]
            branch_id = branch_ids[0] if branch_ids else str(kiotviet_channel.branch_id or "").strip()

        normalized_total_amount = (
            OrderServiceCommands._decimal(payload_total_amount)
            if payload_total_amount is not None
            else OrderServiceCommands._decimal(derived_total_amount)
        )

        # Địa chỉ 2 cấp + Khu vực/Phường xã Kiot (ưu tiên tỉnh mới/phường mới)
        level2_fields = resolve_level2_fields(raw_address, normalized_address)
        kv_resolved = resolve_kiotviet_names(normalized_address, raw_address, level2_fields)
        _t_kv = time.monotonic()

        order = Order.objects.create(
            channel=channel,
            kiotviet_channel=kiotviet_channel,
            staff=payload.get("staff"),
            name=name,
            phone=phone_raw,
            raw_address=raw_address,
            has_address=bool(raw_address or address),  # bất biến: tạo thiếu địa chỉ → dính tab Tư vấn
            address=format_standard_address(normalized_address),
            ward=resolved_ward,
            district=resolved_district,
            province=resolved_province,
            ward_code=resolved_ward_code,
            district_code=resolved_district_code,
            province_code=resolved_province_code,
            kiotviet_location=kv_resolved.get("locationName", ""),
            kiotviet_ward=kv_resolved.get("wardName", ""),
            **level2_fields,
            product=product_text,
            product_raw=str(payload.get("product_raw", "") or "").strip(),
            sku=payload_sku or derived_sku,
            quantity=int(payload_quantity if payload_quantity is not None else derived_quantity),
            channel_owner=channel_owner,
            url_page=str(payload.get("url_page", "")).strip(),
            **meta_attribution_fields(payload.get("url_page")),
            external_order_id=str(payload.get("external_order_id", "")).strip(),
            customer=customer,
            status=payload.get("status", 0),
            discount_amount=payload_discount_amount,
            booking_voucher_discount=payload_booking_discount,
            weight=payload.get("weight", 0),
            dimensions=str(payload.get("dimensions", "")).strip(),
            branch_id=branch_id,
            order_type=payload.get("order_type", "normal"),
            vat=payload.get("vat", OrderVat.NO),
            # show_orders=False is only set by landing-flow creates (manual button on
            # /landing-orders, landing import). When the client omits order_source in
            # those payloads (regression / stale build), default to "landing" so the
            # order remains visible in the landing list and search.
            order_source=(
                "koc"
                if is_koc
                else (
                    payload.get("order_source")
                    or ("landing" if payload.get("show_orders") is False else None)
                )
            ),
            show_orders=payload.get("show_orders", True),
            other_price=payload.get("other_price", 0),
            customer_prepaid=payload_customer_prepaid,
            result=str(payload.get("result", "")).strip(),
            order_code=str(payload.get("order_code", "")).strip(),
            invoice_code=str(payload.get("invoice_code", "")).strip(),
            invoice_id=payload.get("invoice_id"),
            old_invoice_code="",
            old_id=None,
            feedback=str(payload.get("feedback", "")).strip(),
            total_after_discount=payload_total_after_discount,
            total_amount=normalized_total_amount,
            shipping_fee=payload.get("shipping_fee", 0),
            payment_method=str(payload.get("payment_method", "")).strip(),
            shipping_code=str(payload.get("shipping_code", "")).strip(),
            # Chỉ GÁN nguồn xuất, KHÔNG trừ tồn ở đây: tồn chỉ trừ khi đơn sang trạng
            # thái giao vận "dispatched" (quét kho hoặc set tay) — xem `update_order_inline`.
            fulfillment_warehouse=payload.get("fulfillment_warehouse"),
        )
        created_items, items_total = OrderServiceCommands._replace_order_items(order, item_payloads)
        if created_items:
            summary_items = [
                {"product_name": item.product_name, "sku": item.sku, "quantity": item.quantity}
                for item in created_items
            ]
            summary_product, summary_sku, summary_quantity = OrderServiceCommands._build_order_product_summary(summary_items)

            OrderServiceCommands._sync_order_totals_from_items(order, items_total)
            order.product = summary_product
            order.sku = summary_sku
            order.quantity = summary_quantity
            order.save(update_fields=["product", "sku", "quantity", "total_amount", "total_after_discount", "updated_at"])

        created_at_override = payload.get("created_at_override")
        if created_at_override is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at_override)
            order.created_at = created_at_override
        journey_items_service.log_order_created(order, actor_id=getattr(actor, "id", None), source=created_via)
        _t_db = time.monotonic()

        # Chia đơn tự động khi form bật cờ: TÁI SỬ DỤNG đúng bộ chia của luồng
        # landing-ingest (trọng số theo ngày/ca/sheet, largest-remainder, lock).
        # Phải chạy TRƯỚC push event: `build_order_created_row` chụp staff_id +
        # tên sale vào payload realtime, chia sau thì dòng vừa hiện trên bảng đơn
        # của mọi tab đang mở sẽ trống sale cho tới lần refresh kế tiếp.
        # Không chia được (đơn không thuộc sheet nào, ngoài giờ làm việc, ca chưa
        # cấu hình) là no-op có chủ đích: đơn vẫn tạo, sale để trống, sweep vớt sau.
        if payload.get("auto_assign_staff") and order.staff_id is None:
            from apps.orders.application.services.lead_assignment_service import (
                LeadAssignmentService,
            )

            LeadAssignmentService.assign_if_unassigned(order, actor=actor)

        from apps.orders.application.services.order_event_publisher import build_order_created_row

        OrderServiceCommands._push_event_on_commit(
            OrderEventContract.build_order_created(
                order_id=order.id,
                channel_code=channel.code,
                status=order.status,
                source="orders.service.create_order_manual",
                row=build_order_created_row(order),
            )
        )
        OrderServiceCommands._schedule_lark_notification(channel_name=channel.name, order=order)
        OrderServiceCommands._schedule_vat_invoice_notification(order)
        # Đẩy Foxia cho đơn tạo TAY ở trang landing (nút tạo đơn gửi show_orders=False)
        # khi nguồn có cột bên Foxia — quyết định chủ hệ thống 2026-09-17, mở rộng spec
        # 2026-09-09 vốn chỉ đẩy đơn ingest. Đơn tạo ở trang Đơn hàng chính / KOC, sửa
        # đơn, import Excel vẫn KHÔNG đẩy. Nguồn không có cột (shopee, tiktok, koc…)
        # lọc ngay ở đây để khỏi đẻ dòng `skipped_no_mapping` vô nghĩa cho mọi đơn sàn.
        if order.show_orders is False and order.order_source in FOXIA_MANUAL_PUSH_SOURCES:
            OrderServiceCommands._schedule_foxia_push(order)
        try:
            from apps.orders.application.services.lead_filter_service import LeadFilterService

            LeadFilterService.register_claim(phone_raw=phone_raw, channel=channel, order=order)
        except Exception:
            logger.exception(
                "Failed to register phone claim after manual create (order_id=%s)", order.id
            )
        assessment, _changed_assessment_fields = OrderServiceCommands._upsert_order_assessment(
            order, crm_payload, actor
        )
        if assessment is not None:
            order.assessment = assessment
        recall_code = (payload.get("recall_code") or "").strip()
        if recall_code:
            # Task 1 tách mã/ngày thu hồi sang OrderRecallEntry (1-N); luồng tạo đơn
            # chỉ nhận MỘT mã nên tạo đúng một nhóm, không còn gọi _upsert_order_recall
            # (giờ chỉ còn return_slip_code).
            OrderRecallEntry.objects.create(order=order, recall_code=recall_code, updated_by=actor)
        _t_end = time.monotonic()
        logger.info(
            "create-service-timing addr=%.0fms customer=%.0fms kv=%.0fms db=%.0fms "
            "post=%.0fms total=%.0fms order_id=%s",
            (_t_addr - _t0) * 1000,
            (_t_customer - _t_addr) * 1000,
            (_t_kv - _t_customer) * 1000,
            (_t_db - _t_kv) * 1000,
            (_t_end - _t_db) * 1000,
            (_t_end - _t0) * 1000,
            order.id,
        )
        return order

    @staticmethod
    def push_event(payload: dict):
        # Uỷ quyền cho publisher chung: nó làm giàu payload (staff_id/channel_code)
        # để consumer khỏi tra DB cho từng client — sự cố 2026-07-29.
        from apps.orders.application.services.order_event_publisher import publish_order_event

        publish_order_event(payload)

    @staticmethod
    def _push_event_on_commit(payload: dict) -> None:
        """Broadcast websocket SAU commit ngoài cùng, không phải bên trong atomic.

        `group_send` là round-trip Redis — chạy trong atomic sẽ kéo dài thời gian giữ
        transaction/lock (có chỗ đang giữ row-lock Order + advisory lock 61001). Caller
        phải build payload TRƯỚC khi gọi (truyền dict đã chốt, không truyền closure đọc
        state có thể bị mutate sau đó). on_commit trong atomic lồng nhau chạy ở commit
        NGOÀI CÙNG — đúng chủ đích: state rollback thì không bắn event.
        """
        transaction.on_commit(lambda: OrderServiceCommands.push_event(payload))

    @staticmethod
    def _schedule_lark_notification(*, channel_name: str | None = None, order: Order | None = None) -> None:
        # Skip dispatch entirely when the Lark webhook is not configured (dev default).
        if not sysconfig.get("LARK_ORDER_BOT_WEBHOOK_URL"):
            return
        from apps.dispatch import dispatch

        # Chốt tên NV TRƯỚC on_commit (không đọc lại state có thể bị mutate sau đó).
        # order.staff đã được cache ở cả hai luồng tạo đơn (auto-chia lead gán object,
        # manual create nhận object từ payload) nên không phát sinh query FK ở đây.
        staff = order.staff if order is not None and order.staff_id is not None else None
        staff_name = ""
        if staff is not None:
            staff_name = (getattr(staff, "display_name", "") or "").strip() or staff.get_username()

        transaction.on_commit(
            lambda: dispatch(
                "lark.notify_order_created",
                queue="lark_noti",
                channel_name=channel_name,
                staff_name=staff_name or None,
            )
        )

    @staticmethod
    def _schedule_vat_invoice_notification(order: Order) -> None:
        """Báo kế toán (Lark) ngay khi đơn được đặt cờ cần xuất VAT (cá nhân/công ty).

        Best-effort + idempotent: task tự bỏ qua nếu đã gửi (``vat_invoice_notified_at``),
        nên gọi lặp ở cả luồng tạo lẫn sửa đơn đều an toàn.
        """
        if order.vat not in (OrderVat.INDIVIDUAL, OrderVat.COMPANY):
            return
        # Skip dispatch entirely when the VAT bot webhook is not configured (dev default).
        if not sysconfig.get("LARK_VAT_BOT_WEBHOOK_URL"):
            return
        from apps.dispatch import dispatch

        order_id = order.id
        transaction.on_commit(
            lambda: dispatch(
                "lark.notify_vat_invoice_required",
                queue="lark_noti",
                order_id=order_id,
            )
        )

    @staticmethod
    def _schedule_foxia_push(order: Order) -> None:
        """Xếp lịch đẩy khách của đơn landing sang Foxia SAU commit (spec 2026-09-09).

        Đi qua ``apps.dispatch`` thay vì import thẳng ``apps.foxia``: contract phân
        tầng (`.importlinter`) đặt ``foxia`` TRÊN ``orders`` — import ngược là vi
        phạm CI chặn (`lint-imports`, `.gitlab-ci.yml`). Thân việc thật (chọn
        `foxia_source_id`, gọi HTTP, ghi `FoxiaOrderPush`) nằm ở
        ``FoxiaOrderPushService.push``, chạy trong task Celery ``foxia.push_order``.
        """
        # Skip dispatch entirely when the feature flag is off (dev default).
        if not sysconfig.get("FOXIA_PUSH_ENABLED"):
            return

        order_id = order.id

        def _enqueue() -> None:
            # Enqueue hỏng (Redis chớp, tên task registry sai) không được làm hỏng
            # đơn đã lưu — đơn là thật, việc đẩy là phụ trợ.
            try:
                from apps.dispatch import dispatch

                dispatch(
                    "foxia.push_order",
                    queue=settings.CELERY_FOXIA_PUSH_QUEUE,
                    order_id=order_id,
                )
            except Exception:  # noqa: BLE001 — tích hợp đối tác không được phá ingest
                logger.exception("Foxia push: xếp lịch thất bại (order_id=%s)", order_id)

        transaction.on_commit(_enqueue)
