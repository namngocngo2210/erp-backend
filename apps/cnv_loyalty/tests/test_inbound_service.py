"""CnvOrderIngestService — kéo đơn từ CNV về (spec 2026-08-19).

Ba nhóm test, theo đúng ba thứ có thể hỏng và gây hậu quả không rút lại được:

1. **Chống trùng** với đơn CRM đã đẩy lên CNV (4 tầng ở spec §4).
2. **Chặn echo** — ingest không được đẩy ngược đơn lên CNV.
3. **Ranh giới mirror** — đơn CNV không lọt vào báo cáo/danh sách nội bộ.

Phần còn lại là parser (swagger CNV không khai response body nên hình dạng là
giả định) và idempotency khi chạy lại.
"""

import pytest

from apps.cnv_loyalty.application.services.inbound_service import (
    CnvOrderIngestService,
    extract_order_rows,
)
from apps.cnv_loyalty.models import (
    CnvEntityType,
    CnvOrderLine,
    CnvOrderLink,
    CnvSyncRecord,
    CnvSyncStatus,
)

pytestmark = pytest.mark.django_db


def cnv_order(**overrides) -> dict:
    raw = {
        "id": 9001,
        "name": "#CNV1001",
        "unique_id": "",
        "customer": {
            "id": 555,
            "last_name": "Nguyễn Văn",
            "first_name": "An",
            "phone": "0912345678",
        },
        "line_items": [
            {
                "product_id": 11,
                "variant_id": 12,
                "sku": "SKU-A",
                "name": "Sản phẩm A",
                "quantity": 2,
                "price": 150000,
                "total_discount": 0,
            },
            {
                "product_id": 21,
                "variant_id": 22,
                "sku": "SKU-B",
                "name": "Sản phẩm B",
                "quantity": 1,
                "price": 50000,
                "total_discount": 0,
            },
        ],
        "subtotal_price": 350000,
        "total_discounts": 50000,
        "total_price": 300000,
        "financial_status": "pending",
        "fulfillment_status": "",
        "cancelled_status": "uncancelled",
        "created_at": "2026-08-19T03:00:00Z",
        "updated_at": "2026-08-19T04:00:00Z",
        "shipping_address": {
            "address1": "12 Lê Lợi",
            "ward": "Phường 1",
            "district": "Quận 1",
            "city": "TP HCM",
        },
    }
    raw.update(overrides)
    return raw


class FakeInboundClient:
    """Trả từng trang theo thứ tự đã nạp; ghi lại params để kiểm cửa sổ quét."""

    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.calls: list[dict] = []

    def list_orders(self, params=None):
        self.calls.append(dict(params or {}))
        index = int((params or {}).get("page", 1)) - 1
        rows = self.pages[index] if 0 <= index < len(self.pages) else []
        return {"orders": rows}

    def get_order(self, order_id):  # pragma: no cover - thoả Protocol
        return {}

    def get_order_by_external_id(self, external_id):  # pragma: no cover
        return {}


# ── 1. Chống trùng với đơn CRM đã đẩy lên CNV ────────────────────────────────


def test_bo_qua_don_do_chinh_minh_day_len_theo_unique_id():
    """Tầng 1: `unique_id` dạng `desi-<order_id>` là dấu vân tay của đơn CRM."""
    from apps.orders.models import Order

    stats = CnvOrderIngestService().ingest_rows([cnv_order(unique_id="desi-4321")])

    assert stats.skipped_own == 1
    assert stats.created == 0
    assert Order.all_objects.count() == 0


def test_bo_qua_don_da_co_trong_outbox_du_khong_co_unique_id():
    """Tầng 2: CNV không echo `unique_id` thì `cnv_id` trong outbox gánh.

    Đây là tầng phải hoạt động độc lập — swagger không khai response body nên
    không loại trừ được khả năng CNV nuốt `unique_id`.
    """
    from apps.orders.models import Order

    CnvSyncRecord.objects.create(
        entity_type=CnvEntityType.ORDER,
        local_id=777,
        cnv_id=9001,
        status=CnvSyncStatus.SYNCED,
    )

    stats = CnvOrderIngestService().ingest_rows([cnv_order(unique_id="")])

    assert stats.skipped_own == 1
    assert Order.all_objects.count() == 0


@pytest.mark.parametrize(
    "status", [CnvSyncStatus.SYNCED, CnvSyncStatus.FAILED, CnvSyncStatus.PENDING]
)
def test_tang_2_khong_phu_thuoc_trang_thai_dong_bo(status):
    """Có `cnv_id` là đơn của mình, bất kể lần đồng bộ sau thành hay bại.

    Ca thật hay xảy ra: đơn tạo trên CNV xong, lần PUT cập nhật sau lỗi →
    record `failed` nhưng `cnv_id` vẫn còn. Bản đầu (MR !1254) lọc
    `status=synced` nên mù đúng ca này và đẻ ra một đơn mirror trùng.
    """
    from apps.orders.models import Order

    CnvSyncRecord.objects.create(
        entity_type=CnvEntityType.ORDER,
        local_id=777,
        cnv_id=9001,
        status=status,
    )

    stats = CnvOrderIngestService().ingest_rows([cnv_order(unique_id="")])

    assert stats.skipped_own == 1
    assert Order.all_objects.count() == 0


def test_khong_bo_nham_don_that_cua_cnv():
    """Record outbox của đơn KHÁC không được làm rơi đơn CNV hợp lệ."""
    CnvSyncRecord.objects.create(
        entity_type=CnvEntityType.ORDER,
        local_id=777,
        cnv_id=1234,
        status=CnvSyncStatus.SYNCED,
    )

    stats = CnvOrderIngestService().ingest_rows([cnv_order()])

    assert stats.created == 1


def test_chay_lai_cung_payload_khong_sinh_don_thu_hai():
    """Tầng 3: `cnv_order_id` unique — webhook replay/poll chồng cửa sổ đều vô hại."""
    from apps.orders.models import Order

    service = CnvOrderIngestService()
    first = service.ingest_rows([cnv_order()])
    second = service.ingest_rows([cnv_order(total_price=310000)])

    assert first.created == 1
    assert second.created == 0 and second.updated == 1
    assert Order.all_objects.count() == 1
    assert CnvOrderLink.objects.count() == 1


# ── 2. Chặn echo ─────────────────────────────────────────────────────────────


def test_ingest_khong_day_nguoc_don_len_cnv(monkeypatch, settings, django_capture_on_commit_callbacks):
    """Ingest phải KHÔNG enqueue sync_order — nếu có là vòng lặp echo."""
    from apps.cnv_loyalty import tasks

    settings.CNV_SYNC_ENABLED = True
    enqueued = []
    monkeypatch.setattr(tasks.sync_order, "delay", lambda *args, **kwargs: enqueued.append(args))

    with django_capture_on_commit_callbacks(execute=True):
        CnvOrderIngestService().ingest_rows([cnv_order()])

    assert enqueued == []


def test_guard_subscriber_chan_don_cnv_ke_ca_khi_save(monkeypatch, settings, django_capture_on_commit_callbacks):
    """Lớp chặn thứ nhất: kể cả một đường code tương lai gọi `.save()`.

    `bulk_create` không phát post_save là lớp thứ hai; lớp này phải đứng vững
    một mình vì lớp kia phụ thuộc vào cách viết code, không phải vào quy tắc.
    """
    from apps.cnv_loyalty import tasks
    from apps.cnv_loyalty.application.services import sentinel_service
    from apps.orders.models import Order, OrderSource

    settings.CNV_SYNC_ENABLED = True
    enqueued = []
    monkeypatch.setattr(tasks.sync_order, "delay", lambda *args, **kwargs: enqueued.append(args))

    with django_capture_on_commit_callbacks(execute=True):
        Order.all_objects.create(
            channel=sentinel_service.get_sentinel_channel(),
            customer=sentinel_service.get_sentinel_customer(),
            order_source=OrderSource.CNV,
            name="Khách CNV",
        )

    assert enqueued == []


# ── 3. Ranh giới mirror ──────────────────────────────────────────────────────


def test_don_cnv_bi_loai_khoi_manager_noi_bo():
    from apps.orders.models import Order

    CnvOrderIngestService().ingest_rows([cnv_order()])

    assert Order.objects.count() == 0
    assert Order.all_objects.count() == 1


def test_don_cnv_bi_loai_khoi_sql_tho():
    from apps.common.order_scope import MIRROR_ORDER_SOURCES, sql_exclude_mirror_orders

    assert "cnv" in MIRROR_ORDER_SOURCES
    assert "'cnv'" in sql_exclude_mirror_orders()


def test_mirror_sources_khop_enum():
    """Hai nơi khai nguồn mirror không được trôi khỏi nhau trong im lặng."""
    from apps.common.order_scope import MIRROR_ORDER_SOURCES
    from apps.orders.models import OrderSource

    assert set(MIRROR_ORDER_SOURCES) <= set(OrderSource.values)
    assert OrderSource.CNV in MIRROR_ORDER_SOURCES


# ── 4. Nội dung đơn ──────────────────────────────────────────────────────────


def test_map_du_truong_don_va_dong_san_pham():
    from apps.orders.models import Order, OrderSource

    CnvOrderIngestService().ingest_rows([cnv_order()])

    order = Order.all_objects.get()
    link = CnvOrderLink.objects.get()
    assert order.order_source == OrderSource.CNV
    assert order.order_code == "#CNV1001"
    assert order.name == "Nguyễn Văn An"
    assert order.phone == "0912345678"
    assert "Lê Lợi" in order.address and order.has_address is True
    assert str(order.total_amount) == "350000.00"
    assert str(order.discount_amount) == "50000.00"
    assert str(order.total_after_discount) == "300000.00"
    assert order.product == "Sản phẩm A, Sản phẩm B"
    assert link.cnv_order_id == 9001
    assert link.cnv_customer_id == 555
    assert [line.sku for line in link.lines.order_by("line_index")] == ["SKU-A", "SKU-B"]


def test_khong_map_trang_thai_sang_order_status():
    """Trạng thái CNV lưu NGUYÊN VĂN; `status` giữ NEW vì chưa chốt bảng map."""
    from apps.orders.models import Order, OrderStatus

    CnvOrderIngestService().ingest_rows([cnv_order(cancelled_status="cancelled")])

    order = Order.all_objects.get()
    assert order.status == OrderStatus.NEW
    assert order.partner_delivery_status == "cancelled"
    assert CnvOrderLink.objects.get().cancelled_status == "cancelled"


def test_khop_khach_theo_sdt_khi_co_khach_noi_bo():
    from apps.customers.models import Customer
    from apps.orders.models import Order

    existing = Customer.objects.create(
        code="KH-EXIST",
        full_name="Khách cũ",
        phone_raw="0912345678",
        phone_normalized="0912345678",
    )

    CnvOrderIngestService().ingest_rows([cnv_order()])

    assert Order.all_objects.get().customer_id == existing.pk


def test_khach_la_dung_sentinel_va_khong_de_them_customer():
    """Không tạo Customer mới: vừa tránh echo, vừa không bẩn phép đếm khách mới."""
    from apps.customers.models import Customer
    from apps.orders.models import Order

    CnvOrderIngestService().ingest_rows([cnv_order()])
    CnvOrderIngestService().ingest_rows([cnv_order(id=9002, unique_id="")])

    order = Order.all_objects.first()
    assert order.customer.code == "CNV-UNKNOWN"
    assert Customer.objects.filter(code="CNV-UNKNOWN").count() == 1
    assert Customer.objects.count() == 1


def test_bot_san_pham_thi_dong_thua_bi_xoa():
    """Đơn sửa bớt dòng phía CNV — dòng cũ ở đuôi không được nằm lại."""
    service = CnvOrderIngestService()
    service.ingest_rows([cnv_order()])

    shrunk = cnv_order()
    shrunk["line_items"] = shrunk["line_items"][:1]
    service.ingest_rows([shrunk])

    assert CnvOrderLine.objects.count() == 1
    assert CnvOrderLine.objects.get().sku == "SKU-A"


# ── 5. Parser chịu được payload lạ ───────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"orders": [{"id": 1, "line_items": []}]},
        {"data": [{"id": 1, "line_items": []}]},
        {"order": {"id": 1, "line_items": []}},
        [{"id": 1, "line_items": []}],
        {"id": 1, "line_items": []},
    ],
)
def test_extract_order_rows_chap_nhan_moi_hinh_dang(payload):
    assert extract_order_rows(payload) == [{"id": 1, "line_items": []}]


@pytest.mark.parametrize("payload", [None, {}, "", {"ping": "pong"}, {"orders": "x"}])
def test_extract_order_rows_tra_rong_khi_khong_phai_don(payload):
    assert extract_order_rows(payload) == []


def test_don_thieu_id_bi_bo_qua_khong_lam_chet_ca_lo():
    from apps.orders.models import Order

    stats = CnvOrderIngestService().ingest_rows([{"line_items": []}, cnv_order()])

    assert stats.skipped_invalid == 1
    assert stats.created == 1
    assert Order.all_objects.count() == 1


def test_so_tien_di_dang_khong_lam_vo_lo():
    """Giá trị rác/vượt trần cột DECIMAL(12,2) phải bị kẹp, không được văng."""
    from apps.orders.models import Order

    stats = CnvOrderIngestService().ingest_rows(
        [cnv_order(subtotal_price="abc", total_price=10**15, total_discounts=None)]
    )

    order = Order.all_objects.get()
    assert stats.created == 1
    assert str(order.total_amount) == "0.00"
    assert str(order.total_after_discount) == "9999999999.99"


# ── 6. Poll ──────────────────────────────────────────────────────────────────


def test_poll_lat_trang_toi_khi_het():
    from django.utils import timezone

    from apps.orders.models import Order

    page1 = [cnv_order(id=1000 + index) for index in range(100)]
    page2 = [cnv_order(id=2000 + index) for index in range(5)]
    client = FakeInboundClient([page1, page2])
    now = timezone.now()

    stats = CnvOrderIngestService(client).poll(now - timezone.timedelta(hours=1), now)

    assert stats.created == 105
    assert stats.pages == 2
    assert [call["page"] for call in client.calls] == [1, 2]
    assert Order.all_objects.count() == 105


def test_poll_dung_ngay_khi_trang_dau_rong():
    from django.utils import timezone

    client = FakeInboundClient([[]])
    now = timezone.now()

    stats = CnvOrderIngestService(client).poll(now - timezone.timedelta(hours=1), now)

    assert stats.created == 0
    assert len(client.calls) == 1
