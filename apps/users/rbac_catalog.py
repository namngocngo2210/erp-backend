"""Catalog quyền tĩnh (nguồn sự thật ở code). Sync xuống bảng Permission.

Map từ section → danh sách action. Mọi section đều có "view". Section nghiệp vụ
thêm create/edit/delete; section có duyệt/xuất thêm approve/export. Danh sách bám
theo VALID_SECTIONS và endpoint thực tế (rà lại khi mở rộng).
"""

from collections.abc import Iterator

from django.db import transaction

from .models import Permission

# section -> tuple[action, ...]. Thứ tự action không quan trọng.
PERMISSION_CATALOG: dict[str, tuple[str, ...]] = {
    # Sales / đơn hàng — full CRUD + duyệt + xuất Excel
    "orders": ("view", "create", "edit", "delete", "approve", "export"),
    "landing-orders": ("view", "create", "edit", "delete", "export"),
    "koc-orders": ("view", "create", "edit", "delete", "export"),
    "warranty": ("view", "create", "edit", "delete", "export"),
    "warranty-codes": ("view", "create", "edit", "export"),
    # CRM
    "customers": ("view", "create", "edit", "delete", "export"),
    # Danh sách contact. `export` tách riêng khỏi view: file xuất là toàn bộ danh bạ
    # kèm SĐT khách trong scope của người xuất — "tài sản cạnh tranh giữa sale", không
    # phải ai vào xem được trang cũng nên mang được cả tệp đi.
    "contacts": ("view", "edit", "export"),
    "loyal-customers": ("view", "edit", "export"),
    "resale-tracking": ("view", "edit", "export"),
    # Báo cáo (chỉ xem + xuất)
    "dashboard": ("view", "export"),
    # `edit` KHÔNG phải quyền sửa báo cáo — chỉ dùng cho việc gán chiến dịch quảng
    # cáo về sản phẩm (docs/ads-campaign-product-mapping.md), cùng bản chất với
    # `foxia-report:edit` bên dưới. Không grandfather cho mọi role đang có :view:
    # gán sai một chiến dịch là đổi toàn bộ chi phí theo sản phẩm.
    "ads-report": ("view", "edit", "export"),
    "overview-report": ("view",),  # Báo cáo tổng — chưa có export
    # Trang Hồ sơ khách hàng 360 — đọc mart dwh.*. Chỉ còn "view": hai tab bảng
    # và Excel export đã gỡ khi trang chuyển thành màn hồ sơ từng khách
    # (migration 0041 thu hồi "customer-explorer:export").
    "customer-explorer": ("view",),
    # Chất lượng cuộc gọi: view cho quản lý. Transcript + trích dẫn là PII
    # (SĐT, thông tin bệnh của khách) nên gate trọn sau quyền này.
    # `edit` thêm 2026-08-20 (sweep N2 giai đoạn 2): ghi nhận review "AI chấm
    # sai" (quality_review) là hành vi GHI, không đi chung :view. Migration 0039
    # grandfather :edit cho mọi role đang có :view — hành vi prod không đổi ngày
    # deploy, siết thật = thu hồi từng role trên UI sau.
    # `export` thêm 2026-08-29: file Excel gom SĐT khách + trích dẫn transcript
    # của cả kỳ vào một chỗ mang đi được, rủi ro khác hẳn việc xem trên màn hình
    # nên tách quyền riêng. Migration 0044 seed nhưng CỐ Ý không gán role nào.
    "call-quality": ("view", "edit", "export"),
    # Kênh & tích hợp
    "channels": ("view", "create", "edit", "delete"),
    "kiotviet": ("view", "edit"),
    "facebook-ads": ("view", "edit"),
    "google-ads": ("view", "edit"),
    # Báo cáo hiệu quả marketing Foxia. `edit` chỉ dùng cho việc gán nguồn Foxia
    # vào kênh nội bộ — không có đường ghi nào sang hệ thống đối tác.
    "foxia-report": ("view", "edit"),
    # Đồng bộ sản phẩm lên Pancake POS (docs/pancake-pos-product-sync.md): `view` =
    # xem lượt chạy/liên kết/kiểm tra kết nối; `edit` = bấm đồng bộ ghi ra shop thật,
    # thử lại liên kết. API key/shop vẫn nhập ở Cấu hình hệ thống (superuser).
    # Migration 0053 seed nhưng CỐ Ý không gán role nào.
    "pancake-pos": ("view", "edit"),
    # Catalog
    "products": ("view", "create", "edit", "delete", "export"),
    "product-categories": ("view", "create", "edit", "delete"),
    # Kho
    "warehouses": ("view", "create", "edit", "delete"),
    "stock": ("view", "edit", "export"),
    "stock-documents": ("view", "create", "edit", "delete", "approve", "export"),
    "returns": ("view", "create", "edit", "approve"),
    "shipments": ("view", "edit"),
    # HR / nhân sự
    "employees": ("view", "create", "edit", "delete", "approve", "export"),
    # Lương/hợp đồng — tách khỏi "employees" vì đây là dữ liệu nhạy cảm hơn diện
    # thông thường (nhiều NV có employees:view để xem hồ sơ nhưng không cần thấy lương).
    # KHÔNG tự gán cho role nào ngoài Administrator (is_system, short-circuit full quyền).
    # MỌI đường ra của dữ liệu lương phải nằm trong section này: xem lương/hợp đồng 1
    # người (view), trang Cơ chế lương (view/edit), file bảng lương tháng (export).
    # Gate bằng employees:* là hở — role HR nào có section employees cũng tự động có
    # edit/export của nó (audit 2026-07-14).
    "salaries": ("view", "edit", "export"),
    # Quản trị
    "users": ("view", "create", "edit", "delete"),
    "lead-assignment": ("view", "edit"),
    # Đổi sale phụ trách đơn / khách thân thiết. CHỈ có "edit": không phải một trang
    # nên "view" vô nghĩa và chỉ mời gọi gate nhầm bằng require_section.
    "sale-assignment": ("edit",),
    # Xem ĐẦY ĐỦ SĐT khách trên đơn/khách KHÔNG do mình phụ trách. Không có trang
    # riêng (cùng khuôn sale-assignment): thiếu quyền này thì API trả bản che
    # 091****678 ở mọi màn đơn/danh bạ/khách hàng + file Excel, tìm kiếm theo số
    # vẫn chạy bình thường. Xem apps/users/phone_visibility.py. Migration 0050
    # grandfather cho mọi role đang tồn tại — siết thật = bỏ tick ở role Sale.
    "customer-phone": ("view",),
    "floating-pool": ("view", "edit"),
    "contact_flow": ("view", "edit"),  # Cấu hình tác nghiệp
    "audit": ("view", "export"),
    # Tiện ích: tra cứu địa chỉ 2 cấp / 3 cấp — chỉ xem, không có dữ liệu để sửa.
    "address-lookup": ("view",),
    # Danh sách sđt kéo từ Pancake (view) + nút "tạo lại đơn" từ lead (edit).
    # `edit` thêm 2026-09-23 (audit N1/M1): `retry_order` xếp lịch TẠO ĐƠN THẬT từ
    # lead — là hành vi ghi, không đi chung :view. Migration 0052 grandfather :edit
    # cho mọi role đang có :view (hành vi prod không đổi ngày deploy, siết thật =
    # thu hồi từng role trên UI), cùng chiến lược 0039.
    "pancake-leads": ("view", "edit"),
    # "sync" đã gỡ 2026-07-14: trang Đồng bộ bỏ, không endpoint nào gate section này
    # — quyền cấp ra không có tác dụng (migration 0028 dọn dòng đã seed).
}


def iter_catalog_codes() -> Iterator[str]:
    for section, actions in PERMISSION_CATALOG.items():
        for action in actions:
            yield f"{section}:{action}"


@transaction.atomic
def sync_permissions() -> tuple[int, int]:
    """Đồng bộ bảng Permission khớp catalog. Trả (số tạo mới, số xóa).

    Idempotent: tạo code thiếu, xóa code không còn trong catalog, giữ nguyên code khớp.
    """
    desired: dict[str, tuple[str, str]] = {}
    for section, actions in PERMISSION_CATALOG.items():
        for action in actions:
            desired[f"{section}:{action}"] = (section, action)

    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(code=code, section=section, action=action, label=f"{section} · {action}")
        for code, (section, action) in desired.items()
        if code not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)

    obsolete = existing - set(desired)
    deleted = 0
    if obsolete:
        deleted, _ = Permission.objects.filter(code__in=obsolete).delete()

    return len(to_create), deleted


# ── Catalog field đơn hàng (quyền SỬA theo field) ────────────────────────────
# Đặt ở apps.users vì resolver/serializer role cần đọc, mà .importlinter cấm
# apps.users import apps.orders. Đây thuần dữ liệu: tên field + nhãn tiếng Việt.
# `staff_id` CỐ Ý vắng mặt — đã có quyền riêng `sale-assignment:edit`.
# Quyền sửa trường đánh giá CSKH đi theo hạt TỪNG TRƯỜNG, tên mang tiền tố "crm."
# để guard biết dùng comparator lồng nhau và để không lẫn với cột của Order.
# `crm.needs` là quan hệ riêng (OrderNeed), không phải cột. `crm.__extra__` gom mọi
# trường ĐỘNG khai trong Django admin (bảng assessment_field_defs): chúng không thể
# nằm trong catalog tĩnh này vì apps.users không được import apps.orders.
CRM_FIELD_PREFIX = "crm."
CRM_NEEDS_FIELD = "crm.needs"
CRM_EXTRA_FIELD = "crm.__extra__"

ORDER_FIELD_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("customer", "Khách hàng", ("name", "phone", "customer_gender")),
    (
        "address",
        "Địa chỉ",
        (
            "address", "raw_address", "province", "district", "ward",
            "province_code", "district_code", "ward_code",
            "kiotviet_location", "kiotviet_ward",
            # Chuỗi "Địa chỉ 2 cấp" sale sửa tay (commit 2ce253b0/03219423). Đây là
            # field GHI duy nhất của bộ 2 cấp trên OrderInlineUpdateSerializer; 4 cột
            # ward/province(+mã)_level2 còn lại do service tự suy từ chuỗi này nên
            # KHÔNG phải hạt quyền riêng (test_order_field_catalog khoá catalog ==
            # serializer). Thiếu dòng này thì field nằm ngoài catalog = ai có PATCH
            # đơn cũng sửa được (audit 2026-09-23 N1). Migration 0051 grandfather.
            "address_level2",
        ),
    ),
    ("products", "Sản phẩm", ("items", "sku", "quantity")),
    (
        "money",
        "Tiền & chiết khấu",
        (
            "total_amount", "total_after_discount", "discount_amount",
            "booking_voucher_discount", "other_price", "customer_prepaid",
            "shipping_fee",
        ),
    ),
    (
        "shipping",
        "Vận chuyển",
        ("shipping_code", "weight", "dimensions", "fulfillment_warehouse", "branch_id"),
    ),
    (
        "status",
        "Trạng thái & CSKH",
        ("status", "result", "feedback", "customer_return", "return_date"),
    ),
    (
        "crm",
        "Đánh giá CSKH",
        (
            "crm.sales_status_code",
            "crm.not_closed_reason_code",
            "crm.delivery_business_status_code",
            "crm.customer_classification_code",
            "crm.customer_attitude_raw",
            "crm.potential_level",
            "crm.internal_note",
            "crm.next_followup_at",
            "crm.bomb_reason_group_code",
            "crm.bomb_reason_type_code",
            "crm.bomb_reason_text",
            "crm.age_text",
            "crm.occupation_code",
            "crm.pain_level_code",
            "crm.pain_duration_code",
            "crm.used_massage_before",
            "crm.lifestyle_habit_codes",
            "crm.relax_time_codes",
            "crm.purchase_barrier_codes",
            "crm.delax_source_codes",
            "crm.comparing_brand_text",
            "crm.call_duration_code",
            "crm.post_purchase_feedback_text",
            "crm.post_purchase_rating_code",
            "crm.needs",
            "crm.__extra__",
        ),
    ),
    (
        "invoice",
        "Hoá đơn & VAT",
        (
            "invoice_code", "invoice_id", "vat", "is_export_vat", "vat_link",
            "payment_method", "order_code",
        ),
    ),
    (
        "classify",
        "Phân loại & KOC",
        (
            "order_type", "koc_order_type", "order_source", "channel_code",
            "kiotviet_channel_id", "recall", "recall_entries", "created_at",
        ),
    ),
)

ORDER_FIELD_LABELS: dict[str, str] = {
    "name": "Tên khách", "phone": "Số điện thoại", "customer_gender": "Giới tính",
    "address": "Địa chỉ", "raw_address": "Địa chỉ thô", "province": "Tỉnh/TP",
    "district": "Quận/Huyện", "ward": "Phường/Xã", "province_code": "Mã tỉnh",
    "district_code": "Mã quận", "ward_code": "Mã phường",
    "kiotviet_location": "Khu vực KiotViet", "kiotviet_ward": "Phường KiotViet",
    "address_level2": "Địa chỉ 2 cấp",
    "items": "Danh sách sản phẩm", "sku": "SKU", "quantity": "Số lượng",
    "total_amount": "Tổng tiền", "total_after_discount": "Sau giảm giá",
    "discount_amount": "Giảm giá", "booking_voucher_discount": "Voucher booking",
    "other_price": "Phụ phí khác", "customer_prepaid": "Khách ứng trước",
    "shipping_fee": "Phí vận chuyển",
    "shipping_code": "Mã vận đơn", "weight": "Khối lượng", "dimensions": "Kích thước",
    "fulfillment_warehouse": "Kho xuất", "branch_id": "Chi nhánh",
    "status": "Trạng thái", "result": "Kết quả", "feedback": "Phản hồi",
    "customer_return": "Khách hoàn", "return_date": "Ngày hoàn",
    "invoice_code": "Mã hoá đơn", "invoice_id": "ID hoá đơn", "vat": "Hình thức VAT",
    "is_export_vat": "Xuất VAT", "vat_link": "Link VAT",
    "payment_method": "Hình thức thanh toán", "order_code": "Mã đơn",
    "order_type": "Loại đơn", "koc_order_type": "Loại đơn KOC",
    "order_source": "Nguồn đơn", "channel_code": "Kênh",
    "kiotviet_channel_id": "Kênh KiotViet", "recall": "Thu hồi",
    "recall_entries": "Nhóm thu hồi", "created_at": "Ngày tạo đơn",
    "crm.sales_status_code": "Trạng thái sale",
    "crm.not_closed_reason_code": "Lý do không chốt",
    "crm.delivery_business_status_code": "Trạng thái GH CRM",
    "crm.customer_classification_code": "Phân loại KH",
    "crm.customer_attitude_raw": "Thái độ KH",
    "crm.potential_level": "Tiềm năng",
    "crm.internal_note": "Ghi chú nội bộ",
    "crm.next_followup_at": "Hẹn nhắc lúc",
    "crm.bomb_reason_group_code": "Nhóm lý do bom",
    "crm.bomb_reason_type_code": "Loại lý do bom",
    "crm.bomb_reason_text": "Ghi chú lý do bom",
    "crm.age_text": "Độ tuổi",
    "crm.occupation_code": "Nghề nghiệp",
    "crm.pain_level_code": "Mức độ đau",
    "crm.pain_duration_code": "Thời gian bị đau nhức",
    "crm.used_massage_before": "Đã từng dùng massage",
    "crm.lifestyle_habit_codes": "Thói quen sinh hoạt",
    "crm.relax_time_codes": "Thời điểm thư giãn chính",
    "crm.purchase_barrier_codes": "Rào cản khi mua",
    "crm.delax_source_codes": "Nguồn biết đến Delax",
    "crm.comparing_brand_text": "Hãng khách đang so",
    "crm.call_duration_code": "Thời lượng cuộc gọi",
    "crm.post_purchase_feedback_text": "Cảm nhận về sản phẩm sau mua",
    "crm.post_purchase_rating_code": "Đánh giá về sản phẩm sau mua",
    "crm.needs": "Nhu cầu khách",
    "crm.__extra__": "Trường đánh giá mở rộng (thêm qua admin)",
}

ALL_ORDER_FIELDS: frozenset[str] = frozenset(
    field for _code, _label, fields in ORDER_FIELD_GROUPS for field in fields
)

# Section dùng cho grant field ở v1. Đặt tên thay vì rải chuỗi "orders" khắp nơi.
ORDER_GRANT_SECTION = "orders"
