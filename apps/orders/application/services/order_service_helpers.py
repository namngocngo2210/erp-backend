from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from django.utils import timezone
from django.utils.text import slugify

from apps.channels_core.models import Channel, UserChannel
from apps.orders.contracts import OrderEventContract
from apps.orders.crm_catalog import CLOSED_SALES_STATUS_CODES
from apps.orders.models import (
    NeedType,
    Order,
    OrderAssessment,
    OrderItem,
    OrderJourneyEvent,
    OrderNeed,
    OrderRecall,
    OrderRecallEntry,
    OrderStatus,
)
from apps.catalog.models import Product
from apps.orders.sheet_rules import (
    ORDER_FIELD_CHANGED_EVENT,
    STAFF_CHANGED_EVENT,
    TRACKED_ORDER_FIELDS,
)
from apps.users.models import User
from apps.users.services import UserService

from apps.orders.assessment_fields import (
    ASSESSMENT_FIELDS,
    ASSESSMENT_FIELDS_BY_NAME,
    NON_CLONED_ASSESSMENT_FIELDS,
    AssessmentValidationError,
    coerce_and_validate,
)


def _parse_slip_codes(value) -> list[str]:
    """Chuẩn hoá mã phiếu trả về list: nhận string phẩy (inline edit / import Excel)
    hoặc list (sync KiotViet). Strip, bỏ rỗng, dedupe giữ thứ tự, cắt 64 ký tự."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    codes: list[str] = []
    for item in raw:
        code = str(item).strip()[:64]
        if code and code not in codes:
            codes.append(code)
    return codes


class OrderServiceHelpers:
    @staticmethod
    def push_event(payload: dict):
        # Uỷ quyền cho publisher chung: nó làm giàu payload (staff_id/channel_code)
        # để consumer khỏi tra DB cho từng client — sự cố 2026-07-29.
        from apps.orders.application.services.order_event_publisher import publish_order_event

        publish_order_event(payload)

    def duplicate_order(
        order: Order,
        actor,
        *,
        reset_progress: bool = False,
        inherit_created_at: bool = False,
        default_sales_status: str | None = None,
        show_orders: bool | None = None,
        emit_event: bool = True,
        restored_from: Order | None = None,
        event_source: str = "orders.service.duplicate_order",
    ) -> Order:
        # `reset_progress` đưa đơn về đúng trạng thái "đơn mới ingest": rỗng
        # result/feedback, show_orders=False và bỏ qua bước clone OrderAssessment.
        # `staff` và `branch_id` LUÔN được giữ từ đơn gốc (không phụ thuộc
        # `reset_progress`) — xem lý do ở từng field bên dưới. `kiotviet_channel`,
        # `order_source`, `koc_order_type` cũng luôn copy vô điều kiện, không bị
        # `reset_progress` đụng tới.
        # `show_orders` (không None) override cờ hiển thị; `emit_event=False` để luồng
        # batch tự bắn một event tổng kết thay vì N event order.created; `restored_from`
        # trỏ về đơn hủy gốc khi đơn này sinh ra từ nút "Khôi phục".
        create_kwargs = dict(
            channel=order.channel,
            kiotviet_channel=order.kiotviet_channel,
            name=order.name,
            phone=order.phone,
            raw_address=order.raw_address,
            address=order.address,
            ward=order.ward,
            district=order.district,
            province=order.province,
            ward_code=order.ward_code,
            district_code=order.district_code,
            province_code=order.province_code,
            kiotviet_location=order.kiotviet_location,
            kiotviet_ward=order.kiotviet_ward,
            address_level2=order.address_level2,
            ward_level2=order.ward_level2,
            province_level2=order.province_level2,
            ward_code_level2=order.ward_code_level2,
            province_code_level2=order.province_code_level2,
            product=order.product,
            sku=order.sku,
            quantity=order.quantity,
            channel_owner=order.channel_owner,
            url_page=order.url_page,
            fb_campaign_id=order.fb_campaign_id,
            fb_ad_id=order.fb_ad_id,
            external_order_id="",
            customer=order.customer,
            # Luôn giữ staff của đơn gốc: record scope OWN lọc theo staff_id, nếu
            # rỗng thì sale không còn thấy đơn mình vừa khôi phục.
            staff=order.staff,
            status=OrderStatus.NEW,
            total_amount=order.total_amount,
            discount_amount=order.discount_amount,
            booking_voucher_discount=order.booking_voucher_discount,
            total_after_discount=order.total_after_discount,
            other_price=order.other_price,
            # reset_progress = đơn khôi phục phải "sạch" phí ship như đơn mới ingest
            # để KiotViet tính lại từ đầu khi dispatch lại — cùng nhóm với
            # shipping_code (luôn xoá) và staff/branch_id/result/feedback ở trên.
            shipping_fee=0 if reset_progress else order.shipping_fee,
            shipping_code="",
            weight=order.weight,
            dimensions=order.dimensions,
            # Luôn giữ branch_id của đơn gốc: rỗng khiến đơn của chi nhánh nội bộ
            # (OVERVIEW_EXCLUDED_BRANCH_IDS) lọt ngược vào báo cáo công ty.
            branch_id=order.branch_id,
            order_type=order.order_type,
            # `order_source` LUÔN theo đơn gốc — bản sao của đơn Shopee vẫn là đơn
            # của khách Shopee. Bỏ trống (hành vi cũ của nút Nhân bản) khiến clone
            # rơi khỏi rổ nội sàn của Báo cáo tổng — clone bị xoá `invoice_code` nên
            # `SHOPEE_ORDER_Q`/`TIKTOK_ORDER_Q` mất cả manh mối prefix HDSPE_/HDTTS_
            # — và lọt ngược vào trang Landing/Sheet (vốn loại nguồn sàn/koc).
            order_source=order.order_source,
            # Cùng lý do: nhãn loại đơn KOC là thuộc tính nghiệp vụ của đơn, không
            # phải marker tiến độ. Rỗng thì nhân viên phải gán tay lại từng bản sao.
            koc_order_type=order.koc_order_type,
            customer_prepaid=order.customer_prepaid,
            result="" if reset_progress else order.result,
            order_code="",
            invoice_code="",
            invoice_id=None,
            invoice_status=None,
            old_invoice_code="",
            old_id=None,
            feedback="" if reset_progress else order.feedback,
            # Vẫn copy `vat` (hình thức xuất)
            # nhưng KHÔNG copy `is_export_vat`/`vat_link`: đây là marker "đã xuất hóa
            # đơn VAT rồi" và link trỏ tới hóa đơn của đơn KHÁC (đơn gốc) — copy sang
            # sẽ khiến đơn mới hiện "đã xuất VAT" trong khi chưa hề xuất. Cả hai giữ
            # default của model (False / "").
            payment_method=order.payment_method,
            vat=order.vat,
            has_address=order.has_address,
            product_raw=order.product_raw,
            landing_confirmed_amount=order.landing_confirmed_amount,
            restored_from=restored_from,
        )
        if reset_progress:
            # reset_progress = đơn quay về luồng landing/ingest nên show_orders=False
            # (đơn ingest mới luôn show_orders=False, sau này transfer mới flip True).
            # Nhân bản (reset_progress=False) KHÔNG đụng cờ này: giữ default True của
            # model, nếu copy theo đơn gốc thì clone của đơn landing chưa transfer sẽ
            # biến mất khỏi trang Đơn hàng ngay khi vừa tạo.
            create_kwargs["show_orders"] = False
        if show_orders is not None:
            # Luồng khôi phục quyết định đơn mới hiện ở danh sách nào (cờ
            # `restore_show_orders` từ FE), thắng mặc định của reset_progress.
            create_kwargs["show_orders"] = show_orders
        duplicated_order = Order.objects.create(**create_kwargs)
        # Đơn nhân đôi/khôi phục kế thừa mốc "đơn đổ về" của đơn gốc — cùng ngữ
        # nghĩa với copy landing_confirmed_amount ở create_kwargs.
        from apps.orders.application.services.milestone_snapshot_service import (
            copy_created_snapshot,
        )

        copy_created_snapshot(order, duplicated_order)
        if inherit_created_at and order.created_at is not None:
            # auto_now_add=True khiến mọi gán created_at lúc create đều bị override.
            # Phải UPDATE raw sau khi tạo để giữ nguyên thời điểm đơn gốc.
            Order.objects.filter(pk=duplicated_order.pk).update(created_at=order.created_at)
            duplicated_order.created_at = order.created_at

        source_items = list(order.items.select_related("product").all())
        if source_items:
            # Chỉ luồng KHÔI PHỤC (restored_from != None) mới tính lại giá theo
            # bảng giá HIỆN TẠI của kênh — bản khôi phục là đơn đi bán lại từ hôm
            # nay nên phải theo giá đang bán, không giữ ảnh chụp giá cũ của đơn
            # hủy. Luồng NHÂN ĐÔI (restored_from=None, gọi từ order_action_view)
            # PHẢI giữ nguyên hành vi copy giá y nguyên như trước — sản phẩm chốt
            # chỉ áp dụng cho nút Khôi phục.
            is_restore = restored_from is not None
            book_prices: dict[tuple[int, int], Decimal] = {}
            if is_restore:
                from apps.orders.application.services.price_book_service import (
                    package_prices,
                    resolve_price_book,
                )

                # Resolve bảng giá MỘT LẦN + tra giá theo LÔ cho toàn bộ dòng hàng
                # (giống `_replace_order_items`) — gọi `unit_price_for` cho từng
                # dòng sẽ tốn 2 query/dòng, N+1 theo số dòng hàng.
                price_book = resolve_price_book(order.channel)
                product_ids = [item.product_id for item in source_items if item.product_id]
                book_prices = package_prices(product_ids, price_book)

            duplicated_items = []
            for item in source_items:
                unit_price = item.unit_price
                discount = item.discount
                line_total = item.line_total
                if is_restore and item.product_id:
                    quantity = item.quantity or 1
                    # Ưu tiên đúng bậc gói (quantity=n); không có thì rơi về gói
                    # quantity=1. LƯU Ý: chia cho quantity của GÓI KHỚP (tier_qty),
                    # không phải quantity của dòng hàng — giá gói quantity=1 vốn
                    # ĐÃ LÀ đơn giá/1 sản phẩm, không chia tiếp cho quantity dòng
                    # hàng khi nó là bậc fallback (n != 1).
                    package_price = book_prices.get((item.product_id, quantity))
                    tier_quantity = quantity
                    if package_price is None:
                        package_price = book_prices.get((item.product_id, 1))
                        tier_quantity = 1
                    if package_price is not None:
                        package_price_decimal = Decimal(package_price)
                        unit_price = (package_price_decimal / tier_quantity).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        if tier_quantity == quantity:
                            # Bậc gói khớp CHÍNH XÁC quantity của dòng hàng: line_total
                            # PHẢI bằng đúng giá gói đã khai trong bảng giá, không được
                            # suy ngược từ unit_price đã làm tròn 2 chữ số — giá gói
                            # không chia hết (VD gói quantity=3 giá 100đ → unit_price
                            # làm tròn 33.33) sẽ làm line_total lệch mất tiền lẻ
                            # (33.33*3=99.99, thiếu 1 xu so với giá bán thật là 100).
                            line_total = package_price_decimal
                        else:
                            # Bậc fallback (tier_quantity=1, quantity dòng hàng != 1):
                            # unit_price đã là giá gói quantity=1 nguyên vẹn (không
                            # chia), nhân với quantity không phát sinh sai số.
                            line_total = unit_price * quantity
                        # Giá mới → tính lại giảm giá dòng: nhập theo % thì % bám
                        # giá mới; nhập tiền thì giữ tiền (kẹp không vượt đơn giá).
                        discount, _ = OrderServiceHelpers._line_discount(
                            unit_price,
                            {"discount": item.discount, "discount_percent": item.discount_percent},
                        )
                        line_total -= discount * quantity
                    # Không tra được giá ở bảng nào (kể cả bảng mặc định) → GIỮ
                    # NGUYÊN unit_price/line_total cũ. Ràng buộc cứng: thà giữ giá
                    # cũ còn hơn tạo đơn 0 đồng.
                duplicated_items.append(
                    OrderItem(
                        order=duplicated_order,
                        product=item.product,
                        product_name=item.product_name,
                        sku=item.sku,
                        quantity=item.quantity,
                        unit_price=unit_price,
                        discount=discount,
                        discount_percent=item.discount_percent,
                        line_total=line_total,
                    )
                )
            OrderItem.objects.bulk_create(duplicated_items)

            if is_restore:
                # Tổng đơn PHẢI khớp với dòng hàng vừa tính lại — quên bước này
                # sẽ để lại đơn "dòng hàng một đằng, tổng tiền một nẻo". Dùng
                # `_sync_order_totals_from_items` — helper DÙNG CHUNG cho nhiều
                # luồng khác — KHÔNG được sửa hành vi mặc định của nó, chỉ xử lý
                # tiếp phần kẹp âm bên dưới trong phạm vi riêng của khôi phục.
                items_total = sum((di.line_total for di in duplicated_items), Decimal("0"))
                OrderServiceHelpers._sync_order_totals_from_items(duplicated_order, items_total)
                if duplicated_order.total_after_discount < 0:
                    # Giá bảng mới có thể rẻ hơn giá cũ trong khi discount_amount/
                    # booking_voucher_discount vẫn copy nguyên từ đơn gốc → tổng có
                    # thể âm. Trước đây (copy giá nguyên văn) tình huống này không
                    # xảy ra được qua đường khôi phục; giờ phải kẹp về 0 và LOG CẢNH
                    # BÁO thay vì để âm âm thầm — không tự ý sửa discount, chỉ chặn
                    # số tiền hiển thị đi âm.
                    import logging

                    logging.getLogger(__name__).warning(
                        "Restore reprice produced negative total_after_discount, "
                        "clamped to 0 (order_id=%s, restored_from_id=%s, "
                        "total_amount=%s, discount_amount=%s, "
                        "booking_voucher_discount=%s, total_after_discount_before_clamp=%s)",
                        duplicated_order.id,
                        order.id,
                        duplicated_order.total_amount,
                        duplicated_order.discount_amount,
                        duplicated_order.booking_voucher_discount,
                        duplicated_order.total_after_discount,
                    )
                    duplicated_order.total_after_discount = Decimal("0")
                Order.objects.filter(pk=duplicated_order.pk).update(
                    total_amount=duplicated_order.total_amount,
                    total_after_discount=duplicated_order.total_after_discount,
                )

        if not reset_progress:
            source_assessment = getattr(order, "assessment", None)
            if source_assessment is not None:
                cloned_kwargs = {}
                for spec in ASSESSMENT_FIELDS:
                    if spec.name in NON_CLONED_ASSESSMENT_FIELDS:
                        continue
                    value = getattr(source_assessment, spec.name)
                    if spec.kind == "code_array":
                        value = list(value or [])
                    cloned_kwargs[spec.name] = value
                duplicated_assessment = OrderAssessment.objects.create(
                    order=duplicated_order,
                    updated_by=actor,
                    **cloned_kwargs,
                )
                source_needs = list(source_assessment.needs.select_related("need_type").all())
                if source_needs:
                    OrderNeed.objects.bulk_create(
                        [
                            OrderNeed(order_assessment=duplicated_assessment, need_type=item.need_type)
                            for item in source_needs
                        ]
                    )
                duplicated_order.assessment = duplicated_assessment
                # Đơn nhân bản mang theo trạng thái sale của đơn gốc, nên phải
                # mang theo cả một mốc chốt — nếu không, đơn hiện "Đã chốt" mà ô
                # Ngày chốt trống và không bao giờ lọt vào danh sách "vừa chốt".
                # Mốc là LÚC NHÂN BẢN (không copy mốc cũ): đây là đơn khác, vừa
                # được dựng, cùng tinh thần với created_at.
                OrderServiceHelpers._touch_sales_closed_at(
                    duplicated_order, None, duplicated_assessment.sales_status_code
                )
        elif default_sales_status:
            # Reset progress nhưng vẫn cần preset 1 sales_status (vd "closed" cho
            # luồng khôi phục đơn — đơn coi như đã chốt, tiếp tục push sang KiotViet).
            duplicated_order.assessment = OrderAssessment.objects.create(
                order=duplicated_order,
                updated_by=actor,
                sales_status_code=default_sales_status,
            )
            # Preset này KHÔNG đi qua _upsert_order_assessment nên phải tự đóng
            # mốc chốt, nếu không đơn khôi phục sẽ trống ngày chốt vĩnh viễn.
            OrderServiceHelpers._touch_sales_closed_at(
                duplicated_order, None, default_sales_status
            )

        if emit_event:
            from apps.orders.application.services.order_event_publisher import (
                build_order_created_row,
            )

            OrderServiceHelpers.push_event(
                OrderEventContract.build_order_created(
                    order_id=duplicated_order.id,
                    channel_code=duplicated_order.channel.code,
                    status=duplicated_order.status,
                    source=event_source,
                    row=build_order_created_row(duplicated_order),
                )
            )
        return duplicated_order

    @staticmethod
    def _normalize_url(value: str) -> tuple[str, str]:
        parsed = urlparse((value or "").strip())
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "/").rstrip("/") or "/"
        return host, path

    @staticmethod
    def find_channel_by_link(link: str) -> Channel | None:
        host, path = OrderServiceHelpers._normalize_url(link)
        if not host:
            return None
        best_channel: Channel | None = None
        best_score = -1
        queryset = Channel.objects.filter(deleted_at__isnull=True, is_active=True)
        for channel in queryset:
            sources = channel.landingpage_source or []
            for source in sources:
                source_host, source_path = OrderServiceHelpers._normalize_url(source)
                if not source_host or source_host != host:
                    continue
                if path == source_path:
                    score = 10_000 + len(source_path)
                elif path.startswith(source_path.rstrip("/") + "/") or source_path == "/":
                    score = len(source_path)
                else:
                    continue
                if score > best_score:
                    best_score = score
                    best_channel = channel
        return best_channel

    @staticmethod
    def assigned_channel_codes(user) -> set[str]:
        from apps.users.rbac import resolve_effective_permissions

        if resolve_effective_permissions(user).is_superuser:
            return set(Channel.objects.filter(deleted_at__isnull=True).values_list("code", flat=True))
        return set(
            UserChannel.objects.filter(
                user=user,
                channel__is_active=True,
                channel__deleted_at__isnull=True,
                is_active=True,
            ).values_list("channel__code", flat=True)
        )

    @staticmethod
    def can_edit_channel(user, channel_code: str) -> bool:
        return UserService.can_edit_order(user, channel_code, OrderServiceHelpers.assigned_channel_codes(user)).can_edit

    # XOÁ `order_channel_codes_scope(user)`: hàm này là BẪY bảo mật và đã hết call site.
    # Nó trả `None` cho CẢ HAI ca "không giới hạn (all)" và "scope OWN" (channel code
    # không biểu diễn được "của tôi"), nên mọi gate viết theo khuôn
    # `if scope is not None and code not in scope: 403` đều KHÔNG check gì với user
    # scope OWN — đúng lỗ hổng đọc transcript ở `get_order_journey_events`. Gate record
    # scope dùng `order_record_scope_q` / `scoped_order_or_error`
    # (apps/orders/transport/views/order_views_impl/_view_helpers.py), xử lý đủ 4 mức
    # scope kể cả OWN_SCOPE_SENTINEL.

    @staticmethod
    def channel_code_from_owner(channel_owner: str) -> str:
        normalized = slugify(channel_owner or "")
        if not normalized:
            return "unknown"
        return normalized[:50]

    @staticmethod
    def _decimal(value, *, fallback: Decimal = Decimal("0")) -> Decimal:
        if value in (None, ""):
            return fallback
        try:
            return Decimal(str(value))
        except Exception:
            return fallback

    @staticmethod
    def _line_discount(
        unit_price: Decimal, item_payload: dict
    ) -> tuple[Decimal, Decimal | None]:
        """(discount, discount_percent) cho một dòng hàng từ payload.

        `discount` là tiền giảm cho MỖI sản phẩm. Có `discount_percent` thì quy ra
        tiền từ `unit_price` (làm tròn 2 số lẻ, HALF_UP) và giữ lại %; không thì
        lấy `discount` gửi lên (thiếu = 0) và % = None. Kẹp về [0, unit_price] để
        dòng hàng không bao giờ âm dù payload gửi giảm quá đơn giá.
        """
        percent_raw = item_payload.get("discount_percent")
        if percent_raw not in (None, ""):
            percent = OrderServiceHelpers._decimal(percent_raw)
            discount = (unit_price * percent / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            percent = None
            discount = OrderServiceHelpers._decimal(item_payload.get("discount"))
        discount = min(max(discount, Decimal("0")), max(unit_price, Decimal("0")))
        return discount, percent

    @staticmethod
    def _find_product(*, product_id=None, sku: str = "") -> Product | None:
        queryset = Product.objects.filter(is_active=True)
        if product_id:
            return queryset.filter(id=product_id).first()
        sku_normalized = str(sku or "").strip()
        if not sku_normalized:
            return None
        return queryset.filter(sku__iexact=sku_normalized).first()

    @staticmethod
    def _format_dim(value) -> str:
        try:
            normalized = Decimal(str(value or 0)).normalize()
        except Exception:
            return ""
        text = format(normalized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    @staticmethod
    def _derive_weight_dimensions(matched_items: list) -> tuple[Decimal, str]:
        """Aggregate Order.weight / Order.dimensions from matched (Product, qty) items.

        Per-quantity ProductPackage (weight is total for that pack) takes precedence
        over the Product base fields × qty. Dimensions take the first item's package
        dims since boxing multiple SKUs into one shipment has no canonical shape.
        """
        from apps.catalog.models import ProductPackage

        total_weight = Decimal("0")
        dimensions = ""
        for product, qty in matched_items:
            qty_int = max(1, int(qty or 1))
            package = (
                ProductPackage.objects.filter(product=product, quantity=qty_int).first()
            )
            if package is not None:
                total_weight += OrderServiceHelpers._decimal(package.weight)
                length, width, height = package.length, package.width, package.height
            else:
                total_weight += OrderServiceHelpers._decimal(product.weight) * qty_int
                length, width, height = product.length, product.width, product.height
            if not dimensions and length and width and height:
                dimensions = "x".join(
                    [
                        OrderServiceHelpers._format_dim(length),
                        OrderServiceHelpers._format_dim(width),
                        OrderServiceHelpers._format_dim(height),
                    ]
                )
        return total_weight, dimensions

    @staticmethod
    def _build_order_product_summary(items: list[dict]) -> tuple[str, str, int]:
        if not items:
            return "", "", 0
        product_text = ", ".join(f"{item['product_name']} x{item['quantity']}" for item in items)
        skus = [str(item.get("sku") or "") for item in items]
        total_quantity = sum(int(item.get("quantity") or 0) for item in items)
        return product_text, ", ".join(skus), total_quantity

    @staticmethod
    def _replace_order_items(
        order: Order,
        items_payload: list[dict],
        actor=None,  # kept for backward compatibility with existing call sites
    ) -> tuple[list[OrderItem], Decimal]:
        from apps.orders.application.services.price_book_service import (
            package_prices,
            resolve_price_book,
        )

        # Bảng giá của kênh quyết định đơn giá mặc định khi payload không gửi giá.
        # Payload gửi unit_price tường minh thì vẫn thắng (sale sửa tay / import có
        # cột giá đi qua). Resolve bảng giá MỘT LẦN + tra giá theo LÔ (package_prices)
        # thay vì gọi unit_price_for cho từng dòng — tránh N+1 theo số dòng hàng.
        book = resolve_price_book(order.channel)

        # Pass 1: xác định sản phẩm hợp lệ của từng dòng, gom product_id để tra giá
        # theo lô ở dưới.
        resolved_lines: list[tuple[dict, Product, int]] = []
        for item_payload in items_payload:
            product = OrderServiceHelpers._find_product(
                product_id=item_payload.get("product_id"),
                sku=str(item_payload.get("sku") or ""),
            )
            if product is None:
                continue
            quantity = int(item_payload.get("quantity") or 0)
            if quantity <= 0:
                continue
            resolved_lines.append((item_payload, product, quantity))

        book_prices = package_prices([product.id for _, product, _ in resolved_lines], book)

        built_items: list[dict] = []
        grand_total = Decimal("0")
        for item_payload, product, quantity in resolved_lines:
            price_from_book = book_prices.get((product.id, 1))
            fallback_price = (
                price_from_book if price_from_book is not None else OrderServiceHelpers._decimal(product.price)
            )
            unit_price = OrderServiceHelpers._decimal(item_payload.get("unit_price"), fallback=fallback_price)
            discount, discount_percent = OrderServiceHelpers._line_discount(unit_price, item_payload)
            line_total = (unit_price - discount) * quantity
            grand_total += line_total
            built_items.append(
                {
                    "order": order,
                    "product": product,
                    "product_name": product.name,
                    "sku": product.sku,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "discount": discount,
                    "discount_percent": discount_percent,
                    "line_total": line_total,
                }
            )

        if built_items:
            OrderItem.objects.filter(order=order).delete()
            created_items = OrderItem.objects.bulk_create([OrderItem(**item) for item in built_items])
            return created_items, grand_total

        return [], Decimal("0")

    @staticmethod
    def _sync_order_totals_from_items(order: Order, items_total: Decimal | None = None) -> None:
        """
        Keep order totals consistent with OrderItem unit_price/quantity.
        """
        normalized_items_total = items_total if items_total is not None else (
            OrderItem.objects.filter(order=order).values_list("line_total", flat=True)
        )
        if not isinstance(normalized_items_total, Decimal):
            normalized_items_total = sum((value or Decimal("0")) for value in normalized_items_total)  # type: ignore[arg-type]
        normalized_items_total = OrderServiceHelpers._decimal(normalized_items_total)

        discount_amount = OrderServiceHelpers._decimal(order.discount_amount)
        booking_voucher_discount = OrderServiceHelpers._decimal(order.booking_voucher_discount)
        total_after_discount = normalized_items_total - discount_amount - booking_voucher_discount

        order.total_amount = normalized_items_total
        order.total_after_discount = total_after_discount

    @staticmethod
    def _create_journey_event(
        *,
        order: Order,
        event_type: str,
        actor=None,
        from_code: str | None = None,
        to_code: str | None = None,
        note: str = "",
        payload_json: dict | None = None,
    ) -> OrderJourneyEvent:
        return OrderJourneyEvent.objects.create(
            order=order,
            event_type=event_type,
            from_code=from_code or None,
            to_code=to_code or None,
            note=note or "",
            actor=actor,
            payload_json=payload_json or {},
        )

    @staticmethod
    def _user_display_names(user_ids) -> dict:
        """Map id → tên hiển thị cho ``user_ids`` (bỏ None) trong MỘT query."""
        ids = {uid for uid in user_ids if uid}
        if not ids:
            return {}
        rows = User.objects.filter(id__in=ids).values_list("id", "display_name", "username")
        return {uid: (display or username) for uid, display, username in rows}

    @staticmethod
    def log_staff_change(
        *,
        order: Order,
        from_staff_id: int | None,
        to_staff_id: int | None,
        actor=None,
        event_type: str = STAFF_CHANGED_EVENT,
    ) -> OrderJourneyEvent | None:
        """Ghi journey khi sale phụ trách đổi. No-op nếu không thực sự đổi.

        Tên sale được đóng băng vào ``payload_json`` thay vì chỉ giữ id: user có thể
        bị đổi tên/vô hiệu hoá sau đó, journey vẫn phải đọc được nguyên trạng.
        """
        if from_staff_id == to_staff_id:
            return None
        names = OrderServiceHelpers._user_display_names([from_staff_id, to_staff_id])
        from_name = names.get(from_staff_id)
        to_name = names.get(to_staff_id)
        return OrderServiceHelpers._create_journey_event(
            order=order,
            event_type=event_type,
            actor=actor,
            from_code=str(from_staff_id) if from_staff_id else None,
            to_code=str(to_staff_id) if to_staff_id else None,
            note=f"{from_name or '—'} → {to_name or '—'}",
            payload_json={"from_name": from_name, "to_name": to_name},
        )

    @staticmethod
    def log_staff_change_bulk(
        *,
        order_ids,
        from_staff_id: int | None,
        to_staff_id: int | None,
        actor=None,
        event_type: str = STAFF_CHANGED_EVENT,
    ) -> int:
        """Ghi journey đổi sale cho NHIỀU đơn cùng (from → to) bằng MỘT INSERT.

        Cùng payload với ``log_staff_change`` (tên sale đóng băng vào payload_json,
        note "A → B") — dùng cho đường `.update()` hàng loạt đang giữ lock các dòng
        `orders` (chuyển đơn giữa sale): N INSERT tuần tự trong lock kéo lock dài
        theo N (audit 2026-09-23 T1 §2.6). No-op nếu không thực sự đổi. Trả về số
        event đã ghi.
        """
        ids = [oid for oid in dict.fromkeys(order_ids) if oid is not None]
        if from_staff_id == to_staff_id or not ids:
            return 0
        names = OrderServiceHelpers._user_display_names([from_staff_id, to_staff_id])
        from_name = names.get(from_staff_id)
        to_name = names.get(to_staff_id)
        events = [
            OrderJourneyEvent(
                order_id=order_id,
                event_type=event_type,
                from_code=str(from_staff_id) if from_staff_id else None,
                to_code=str(to_staff_id) if to_staff_id else None,
                note=f"{from_name or '—'} → {to_name or '—'}",
                actor=actor,
                payload_json={"from_name": from_name, "to_name": to_name},
            )
            for order_id in ids
        ]
        OrderJourneyEvent.objects.bulk_create(events)
        return len(events)

    @staticmethod
    def log_tracked_field_changes(
        *, order: Order, previous_values: dict, actor=None
    ) -> list[OrderJourneyEvent]:
        """Ghi journey cho các field trong ``TRACKED_ORDER_FIELDS`` thực sự đổi giá trị.

        ``previous_values`` là ảnh chụp TRƯỚC khi setattr (chỉ chứa field có trong
        payload PATCH). So sánh theo chuỗi để tránh dương tính giả kiểu
        Decimal('0.00') vs '0.00' từ serializer.
        """
        events: list[OrderJourneyEvent] = []
        for field_name, old_value in previous_values.items():
            new_value = getattr(order, field_name, None)
            old_text = "" if old_value is None else str(old_value)
            new_text = "" if new_value is None else str(new_value)
            if old_text == new_text:
                continue
            events.append(
                OrderServiceHelpers._create_journey_event(
                    order=order,
                    event_type=ORDER_FIELD_CHANGED_EVENT,
                    actor=actor,
                    from_code=old_text[:64] or None,
                    to_code=new_text[:64] or None,
                    note=f"{TRACKED_ORDER_FIELDS[field_name]}: {old_text or '—'} → {new_text or '—'}",
                    payload_json={"field": field_name, "from": old_text, "to": new_text},
                )
            )
        return events

    @staticmethod
    def _sync_assessment_needs(assessment: OrderAssessment, needs_payload: list[dict]) -> None:
        # 1) Resolve all submitted codes in a single query (avoid N+1).
        embedded_need_types: list[NeedType] = []
        codes_to_lookup: list[str] = []
        for item in needs_payload:
            need_type = item.get("need_type")
            if need_type is not None:
                embedded_need_types.append(need_type)
                continue
            type_code = str(item.get("type_code") or "").strip()
            if type_code:
                codes_to_lookup.append(type_code)

        resolved_by_code: dict[str, NeedType] = {nt.code: nt for nt in embedded_need_types}
        if codes_to_lookup:
            for nt in NeedType.objects.select_related("group").filter(
                code__in=codes_to_lookup, is_active=True
            ):
                resolved_by_code.setdefault(nt.code, nt)

        normalized_need_types: list[NeedType] = []
        seen_codes: set[str] = set()
        for item in needs_payload:
            need_type = item.get("need_type")
            if need_type is None:
                need_type = resolved_by_code.get(str(item.get("type_code") or "").strip())
            if need_type is None or need_type.code in seen_codes:
                continue
            seen_codes.add(need_type.code)
            normalized_need_types.append(need_type)

        # 2) Diff against existing rows with a single SELECT.
        target_ids = {nt.id for nt in normalized_need_types}
        existing_ids = set(
            OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type_id", flat=True)
        )
        to_delete = existing_ids - target_ids
        to_create = target_ids - existing_ids
        if to_delete:
            OrderNeed.objects.filter(
                order_assessment=assessment, need_type_id__in=to_delete
            ).delete()
        if to_create:
            OrderNeed.objects.bulk_create(
                [
                    OrderNeed(order_assessment=assessment, need_type=nt)
                    for nt in normalized_need_types
                    if nt.id in to_create
                ]
            )

    # --- assessment upsert: split for testability ----------------------------

    _ASSESSMENT_TRACKED_ORIGINALS = (
        "customer_classification_code",
        "sales_status_code",
        "not_closed_reason_code",
        "delivery_business_status_code",
        "bomb_reason_group_code",
        "bomb_reason_type_code",
        "potential_level",
        "customer_attitude_raw",
        "bomb_reason_text",
        "internal_note",
        "next_followup_at",
    )

    @staticmethod
    def _apply_assessment_payload(assessment: OrderAssessment, crm_payload: dict) -> list[str]:
        """Mutate `assessment` in-place from validated payload. Returns changed fields."""
        changed: list[str] = []
        for spec in ASSESSMENT_FIELDS:
            if spec.name not in crm_payload:
                continue
            next_value = coerce_and_validate(spec.name, crm_payload.get(spec.name))
            if getattr(assessment, spec.name) == next_value:
                continue
            setattr(assessment, spec.name, next_value)
            changed.append(spec.name)
        # `bomb_reason_text` là ghi chú chi tiết do sale tự nhập — KHÔNG suy ra từ nhãn
        # loại (nhãn đã có ở bomb_reason_type_label), đổi loại không được ghi đè ghi chú.
        return changed

    # Các key OrderCRMSerializer TRẢ RA nhưng client không ghi được — phải khớp
    # danh sách meta trong product_item_crm_serializers._build_meta_fields.
    # Vì sao phải lờ chúng ở đây: mọi editor inline của bảng đơn gửi
    # `crm: {...row.crm, [field]: value}` — spread NGUYÊN object serializer trả về
    # (OrdersTable/cell-renderers.tsx), nên payload luôn kèm các key echo này.
    # Bản strict đầu tiên (7a2608a2) reject chúng → mọi inline edit 400 ngay sau
    # deploy 2026-08-03 chiều, trong khi form chi tiết (payload allowlist) vẫn chạy.
    # Key lạ THẬT (typo, field chưa khai registry) vẫn bị reject như thiết kế.
    _CRM_SERIALIZER_ECHO_KEYS = frozenset({
        "updated_by_name",
        "next_reminder_at",
        "ai_filled_fields",
        "extra_fields",
        "ai_open_insights",
        "events",
        "created_at",
        "updated_at",
    })

    @staticmethod
    def _is_label_echo_key(key: str) -> bool:
        """Key `*_label` do serializer TỰ SINH cho một field thật (metaclass inject
        SerializerMethodField `{name.removesuffix('_code')}_label` — xem
        product_item_crm_serializers). Payload inline edit echo cả họ key này
        (sales_status_label, age_text_label, ...); chúng là twin hiển thị của một
        cột thật nên lờ đi — còn `*_label` KHÔNG có field gốc tương ứng vẫn bị
        reject như key lạ."""
        if not key.endswith("_label"):
            return False
        base = key.removesuffix("_label")
        return base in ASSESSMENT_FIELDS_BY_NAME or f"{base}_code" in ASSESSMENT_FIELDS_BY_NAME

    @staticmethod
    def _apply_dynamic_assessment_payload(assessment: OrderAssessment, crm_payload: dict) -> list[str]:
        """Validate + merge trường động vào extra_fields. Trả list code đã đổi.

        Key lạ (không phải cột cứng, không phải needs, không phải key echo của
        serializer, không có trong registry active) bị reject — không ghi mù vào
        JSONB để dữ liệu không bẩn dần.
        """
        from apps.orders.dynamic_assessment import coerce_and_validate_dynamic, get_active_defs_by_code

        dynamic_keys = [
            key
            for key in crm_payload
            if key not in ASSESSMENT_FIELDS_BY_NAME
            and key != "needs"
            and key not in OrderServiceHelpers._CRM_SERIALIZER_ECHO_KEYS
            and not OrderServiceHelpers._is_label_echo_key(key)
        ]
        if not dynamic_keys:
            return []

        defs_by_code = get_active_defs_by_code()
        extra = dict(assessment.extra_fields or {})
        changed: list[str] = []
        for key in dynamic_keys:
            spec = defs_by_code.get(key)
            if spec is None:
                raise AssessmentValidationError(f"{key} is not a known assessment field")
            next_value = coerce_and_validate_dynamic(spec, crm_payload[key])
            cleared = next_value is None or next_value == "" or next_value == []
            if cleared:
                if key not in extra:
                    continue
                extra.pop(key)
            else:
                if extra.get(key) == next_value:
                    continue
                extra[key] = next_value
            changed.append(key)
        if changed:
            assessment.extra_fields = extra
        return changed

    @staticmethod
    def _save_assessment(assessment: OrderAssessment, changed: list[str], created: bool, actor) -> None:
        assessment.updated_by = actor
        if not (created or changed):
            return
        if created:
            assessment.save()
        else:
            update_fields = list(dict.fromkeys(changed + ["updated_by", "updated_at"]))
            assessment.save(update_fields=update_fields)

    @staticmethod
    def _touch_sales_closed_at(order: Order, previous_code, new_code) -> None:
        """Ghi `Order.sales_closed_at` khi trạng thái sale VÀO nhóm chốt.

        Chỉ ghi ở lằn ranh NGOÀI → TRONG nhóm `CLOSED_SALES_STATUS_CODES`:

        - `no_answer_2` → `closed`: ghi mốc mới.
        - `closed` → `created_order`: KHÔNG đổi (đi trong nội bộ nhóm) — nếu ghi
          đè ở đây thì mốc sẽ nhảy sang lúc lên đơn/đẩy KiotViet, không còn là
          lúc sale chốt.
        - `closed` → `not_buying`: giữ nguyên mốc cũ, KHÔNG xoá. Cột là dấu vết
          lịch sử; xoá đi sẽ làm đơn biến mất khỏi kết quả sắp xếp theo ngày chốt.
        - `not_buying` → `closed`: ghi đè mốc MỚI. Đơn rơi ra rồi chốt lại phải
          nổi lên đầu danh sách "vừa chốt xong".

        CỐ Ý không đụng `updated_at`: danh sách resale sắp xếp theo `-updated_at`,
        bump ở đây sẽ xáo trộn màn hình đó mỗi lần có người chốt đơn.
        """
        if new_code not in CLOSED_SALES_STATUS_CODES:
            return
        if previous_code in CLOSED_SALES_STATUS_CODES:
            return
        order.sales_closed_at = timezone.now()
        order.save(update_fields=["sales_closed_at"])

    @staticmethod
    def _upsert_order_assessment(
        order: Order, crm_payload: dict | None, actor, *, journey_source: str = ""  # hệ thống ghi → payload.source
    ) -> tuple[OrderAssessment | None, list[str]]:
        if crm_payload is None:
            return getattr(order, "assessment", None), []

        assessment, created = OrderAssessment.objects.get_or_create(
            order=order,
            defaults={"updated_by": actor},
        )
        original_values = {
            name: getattr(assessment, name) for name in OrderServiceHelpers._ASSESSMENT_TRACKED_ORIGINALS
        }

        try:
            changed_fields = OrderServiceHelpers._apply_assessment_payload(assessment, crm_payload)
        except AssessmentValidationError:
            raise

        changed_dynamic = OrderServiceHelpers._apply_dynamic_assessment_payload(assessment, crm_payload)
        if changed_dynamic:
            changed_fields.append("extra_fields")

        OrderServiceHelpers._save_assessment(assessment, changed_fields, created, actor)

        # Sale (hoặc bất kỳ ai) sửa đè field nào thì field đó không còn là "AI điền".
        # Đặt ở đây nên luồng AI cũng bị xoá key ngay sau khi ghi — assessment_ai_fill_service
        # cố ý set lại ai_filled_fields SAU khi gọi helper, không cần cờ phân biệt.
        stale_keys = [
            name for name in changed_fields + changed_dynamic if name in (assessment.ai_filled_fields or {})
        ]
        if "needs" in crm_payload and "needs" in (assessment.ai_filled_fields or {}):
            stale_keys.append("needs")
        if stale_keys:
            for name in stale_keys:
                assessment.ai_filled_fields.pop(name, None)
            assessment.save(update_fields=["ai_filled_fields", "updated_at"])

        if "needs" in crm_payload:
            OrderServiceHelpers._sync_assessment_needs(assessment, crm_payload.get("needs") or [])

        # Auto-add to blocklist when assessment flags spam (either on
        # customer_classification_code or sales_status_code). Safe on repeat
        # assessments — get_or_create-based inside LeadFilterService.
        for field_name in ("customer_classification_code", "sales_status_code"):
            if field_name not in changed_fields:
                continue
            if getattr(assessment, field_name, None) != "spam":
                continue
            try:
                from apps.orders.application.services.lead_filter_service import LeadFilterService

                LeadFilterService.auto_block_from_spam(
                    phone_raw=order.phone,
                    source_field=field_name,
                    order_id=order.id,
                    created_by=actor,
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "Failed to auto-block spam phone (order_id=%s, field=%s)",
                    order.id,
                    field_name,
                )

        note_for_event, source_payload = assessment.internal_note or assessment.bomb_reason_text or "", ({"source": journey_source} if journey_source else {})
        if created:
            OrderServiceHelpers._create_journey_event(
                order=order,
                event_type="assessment_created",
                actor=actor,
                to_code=assessment.sales_status_code or assessment.customer_classification_code or assessment.delivery_business_status_code,
                note=note_for_event,
                payload_json={
                    "sales_status_code": assessment.sales_status_code,
                    "customer_classification_code": assessment.customer_classification_code,
                    "delivery_business_status_code": assessment.delivery_business_status_code,
                    "potential_level": assessment.potential_level, **source_payload,
                },
            )

        event_mappings = {
            "customer_classification_code": "customer_classification_changed",
            "sales_status_code": "sales_status_changed",
            "not_closed_reason_code": "not_closed_reason_changed",
            "delivery_business_status_code": "delivery_business_status_changed",
            "potential_level": "potential_level_changed",
        }
        # Mốc chốt đi CÙNG CHỖ với journey event đổi trạng thái để hai nguồn không
        # bao giờ lệch nhau — command backfill dựng lại cột này chính từ các event đó.
        if created or "sales_status_code" in changed_fields:
            OrderServiceHelpers._touch_sales_closed_at(
                order,
                original_values.get("sales_status_code"),
                assessment.sales_status_code,
            )

        for field_name, event_type in event_mappings.items():
            if field_name not in changed_fields:
                continue
            OrderServiceHelpers._create_journey_event(
                order=order,
                event_type=event_type,
                actor=actor,
                from_code=(str(original_values.get(field_name)) if original_values.get(field_name) is not None else None),
                to_code=(str(getattr(assessment, field_name)) if getattr(assessment, field_name) is not None else None),
                note=note_for_event,
                payload_json={"field": field_name, **source_payload},
            )

        note_fields = {
            "customer_attitude_raw",
            "bomb_reason_group_code",
            "bomb_reason_type_code",
            "bomb_reason_text",
            "internal_note",
        }
        if changed_fields and note_fields.intersection(changed_fields):
            OrderServiceHelpers._create_journey_event(
                order=order,
                event_type="assessment_note_updated",
                actor=actor,
                note=note_for_event,
                payload_json={
                    "customer_attitude_raw": assessment.customer_attitude_raw,
                    "bomb_reason_group_code": assessment.bomb_reason_group_code,
                    "bomb_reason_type_code": assessment.bomb_reason_type_code,
                    "bomb_reason_text": assessment.bomb_reason_text,
                    "internal_note": assessment.internal_note,
                },
            )

        if "sales_status_code" in changed_fields or "next_followup_at" in changed_fields:
            try:
                from apps.orders.application.services.sale_reminder_service import (
                    reschedule_for_assessment,
                )

                reschedule_for_assessment(
                    order=order,
                    assessment=assessment,
                    actor=actor,
                    new_sales_status_code=assessment.sales_status_code,
                    next_followup_at=assessment.next_followup_at,
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "Failed to reschedule sale reminders (order_id=%s)", order.id
                )

        return assessment, changed_fields

    @staticmethod
    def _upsert_order_recall(order: Order, recall_payload: dict | None, actor) -> "OrderRecall | None":
        """Upsert OrderRecall (1-1) từ payload inline. None = không đụng tới recall.

        Lock recall hiện có bằng select_for_update() trước khi ghi: 2 PATCH inline
        đồng thời trên cùng đơn (VD user sửa recall_code + KV returns sync ghi
        return_slip_code gần như cùng lúc) không được phép chồng lên nhau — request
        sau phải đợi request trước commit rồi mới đọc/ghi. Field nào KHÔNG có mặt
        tường minh trong payload thì giữ nguyên (không suy diễn/mặc định) — tránh
        payload full-form cũ đè giá trị mới hơn của field không được sửa.
        """
        if not recall_payload:
            return getattr(order, "recall", None)

        recall = OrderRecall.objects.select_for_update().filter(order=order).first()
        if recall is None:
            recall, _created = OrderRecall.objects.get_or_create(
                order=order,
                defaults={"updated_by": actor},
            )
        # Mã/ngày thu hồi đã tách sang OrderRecallEntry (Task 1) — chỉ còn
        # return_slip_code (mảng, nhận chuỗi phẩy từ inline edit / import và list
        # từ sync KiotViet, `_parse_slip_codes` chuẩn hoá chung một chỗ).
        list_fields = {"return_slip_code"}
        allowed = ("return_slip_code",)
        changed: list[str] = []
        for field_name in allowed:
            if field_name not in recall_payload:
                continue
            value = recall_payload[field_name]
            if field_name in list_fields:
                value = _parse_slip_codes(value)
            setattr(recall, field_name, value)
            changed.append(field_name)
        if changed:
            recall.updated_by = actor
            recall.save(update_fields=[*changed, "updated_by", "updated_at"])
        return recall

    @staticmethod
    def _replace_order_recall_entries(order: Order, entries_payload: list[dict] | None, actor) -> None:
        """Full-replace tập nhóm thu hồi của đơn. None = không đụng tới.

        Xoá-rồi-tạo-lại thay vì đối chiếu theo id: payload luôn đến từ UI gửi trọn
        danh sách, nên diff theo id chỉ thêm nhánh mà không thêm thông tin. Cùng
        khuôn `_replace_order_items`.
        """
        if entries_payload is None:
            return

        # Khoá dòng Order làm điểm nối tiếp: DELETE+bulk_create không tự loại trừ nhau
        # (snapshot READ COMMITTED không thấy dòng transaction kia vừa chèn) nên 2 PATCH
        # chồng nhau sẽ NHÂN ĐÔI nhóm thay vì ghi đè. Lưới bắn 3 PATCH cho mỗi nhóm
        # (blur từng ô) nên đây không phải rủi ro lý thuyết.
        Order.objects.select_for_update().filter(pk=order.pk).exists()

        rows: list[OrderRecallEntry] = []
        for item in entries_payload:
            code = str(item.get("recall_code") or "").strip()[:64]
            created = item.get("recall_created_date")
            received = item.get("recall_received_date")
            # Nhóm trống cả 3 ô = nhóm người dùng vừa xoá trên UI, không lưu.
            if not code and created is None and received is None:
                continue
            rows.append(
                OrderRecallEntry(
                    order=order,
                    recall_code=code,
                    recall_created_date=created,
                    recall_received_date=received,
                    updated_by=actor,
                )
            )

        order.recall_entries.all().delete()
        if rows:
            OrderRecallEntry.objects.bulk_create(rows)
        # Sau delete+bulk_create, cache prefetch do view nạp đã cũ và sẽ rò giá trị
        # cũ ngược vào response — mirror đúng cách `items` xử lý.
        if hasattr(order, "_prefetched_objects_cache"):
            order._prefetched_objects_cache.pop("recall_entries", None)
