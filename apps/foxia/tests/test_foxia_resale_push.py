"""Job cơ hội resale từ đơn giao thành công (docs/foxia-resale-delivered.md).

Những điều chốt ở đây, sai cái nào cũng thành khách bị gọi trùng hoặc bị bỏ sót:
1. Chỉ đơn HIỆN giao thành công, chuyển trạng thái trong cửa sổ, kênh có ô resale.
2. Mỗi đơn tối đa một lần POST — kể cả khi lượt trước failed hay kẹt pending.
3. Mỗi SĐT tối đa một cơ hội mỗi ngày giao.
4. Hai cờ tắt → không ghi, không gọi.
5. Sale resale do bộ chia (cột `resale`) chọn, gửi bằng `foxia_id`; ngoài ca hoặc
   ca chưa cấu hình → chờ, không ghi gì.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.foxia.application.services.order_push_service import PushResult
from apps.foxia.application.services.resale_push_service import (
    CHANCE_RESALE,
    FoxiaResalePushService,
    build_message,
)
from apps.foxia.domain.datetime_utils import to_foxia_utc
from apps.foxia.models import (
    FoxiaLinkCell,
    FoxiaLinkChannel,
    FoxiaResalePush,
    FoxiaResalePushStatus,
)
from apps.foxia.tasks import push_resale_delivered
from apps.orders.models import LeadAssignmentShare, Order, OrderItem, OrderJourneyEvent
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

VN = ZoneInfo("Asia/Bangkok")
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=VN)

RESALE_ON = override_settings(
    FOXIA_PUSH_ENABLED=True,
    FOXIA_RESALE_PUSH_ENABLED=True,
    FOXIA_RESALE_PUSH_SINCE="2026-10-01",
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang",
)


class FakePush:
    def __init__(self, ok=True, cohoi=True):
        self.calls = []
        self._ok = ok
        self._cohoi = cohoi
        self._next_cohoi = 500

    def __call__(self, source_id, payload):
        self.calls.append((source_id, payload))
        self._next_cohoi += 1
        return PushResult(
            url=f"https://foxia.test/duongdankhachhang/{source_id}",
            http_status=200 if self._ok else None,
            response_body="{}" if self._ok else "",
            error="" if self._ok else "ReadTimeout: x",
            duration_ms=12,
            cohoi_id=self._next_cohoi if self._ok and self._cohoi else None,
            khachhang_id=9 if self._ok else None,
        )


TODAY = NOW.date()
SHIFT = ("morning", TODAY)


def _service(fake, shift=SHIFT):
    return FoxiaResalePushService(push=fake, now=lambda: NOW, shift_context=lambda: shift)


def _resale_staff(username="resale1", foxia_id="77", weight=100, shift="morning", day=TODAY):
    Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
    user = User.objects.create(
        username=username, department_id=DepartmentID.SALES, is_active=True, foxia_id=foxia_id
    )
    LeadAssignmentShare.objects.create(date=day, shift=shift, sheet="resale", user=user, weight=weight)
    return user


@pytest.fixture(autouse=True)
def resale_config(request):
    """Mặc định mọi test có 1 sale resale nhận 100% ca sáng; test cần cấu hình riêng
    đánh dấu `no_resale_staff`."""
    if request.node.get_closest_marker("no_resale_staff"):
        return None
    return _resale_staff()


def _channel(code="facebook", resale_id=115):
    channel = Channel.objects.create(code=code, name=f"Kênh {code}")
    if resale_id is not None:
        FoxiaLinkCell.objects.create(
            link_channel=FoxiaLinkChannel.objects.create(channel=channel),
            kind="resale",
            foxia_source_id=resale_id,
        )
    return channel


def _delivered(channel, at, status=3, created_at=None, journey=True, **overrides):
    """Đơn giao thành công lúc `at`; `created_at` mặc định = `at` (job lọc theo NGÀY TẠO)."""
    values = {
        "channel": channel,
        "order_source": "landing",
        "customer": Customer.objects.create(full_name="Khach test"),
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn",
        "province": "Hà Nội",
        "order_code": "DH001",
        "status": status,
    }
    values.update(overrides)
    order = Order.objects.create(**values)
    # auto_now/auto_now_add bỏ qua giá trị truyền vào → ghi đè bằng update().
    Order.all_objects.filter(id=order.id).update(created_at=created_at or at, updated_at=at)
    order.refresh_from_db()
    if journey:
        event = OrderJourneyEvent.objects.create(
            order=order, event_type="order_status_changed", from_code="2", to_code="3"
        )
        OrderJourneyEvent.objects.filter(id=event.id).update(created_at=at)
    return order


@RESALE_ON
def test_don_giao_thanh_cong_trong_cua_so_duoc_day_dung_payload(resale_config):
    channel = _channel()
    at = NOW - timedelta(hours=2)
    order = _delivered(channel, at, product_raw="Combo 2 thảm massage", total_after_discount=Decimal("3199000"))
    fake = FakePush()

    result = _service(fake).run()

    assert (result.ok, result.failed, result.skipped) == (1, 0, 0)
    source_id, payload = fake.calls[0]
    assert source_id == 115
    assert payload == {
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn, Hà Nội",
        "chance_id": CHANCE_RESALE,
        "created_at": to_foxia_utc(at),
        # Đơn không có dòng hàng → chỉ còn giá sau giảm.
        "message": "Giá sau giảm: 3.199.000đ",
        "staff_id": 77,
    }
    row = FoxiaResalePush.objects.get(order=order)
    assert row.status == FoxiaResalePushStatus.OK
    assert row.staff == resale_config
    assert row.cohoi_id == 501
    assert row.delivered_on == timezone.localdate(at)
    assert row.request_body == payload


@RESALE_ON
def test_chay_lai_khong_day_trung_ke_ca_khi_lan_truoc_that_bai():
    channel = _channel()
    _delivered(channel, NOW - timedelta(hours=1))
    _service(FakePush(ok=False)).run()

    fake = FakePush()
    result = _service(fake).run()

    assert fake.calls == []
    assert result.candidates == 0
    assert FoxiaResalePush.objects.get().status == FoxiaResalePushStatus.FAILED


@RESALE_ON
def test_loc_ung_vien():
    channel = _channel()
    no_resale_cell = _channel(code="shopee", resale_id=None)
    _delivered(channel, NOW - timedelta(hours=1), phone="0900000001")                    # hợp lệ
    _delivered(channel, NOW - timedelta(hours=1), status=5, phone="0900000002")          # đã lật sang hoàn
    _delivered(channel, NOW - timedelta(hours=1), invoice_status=2, phone="0900000003")  # hoá đơn huỷ
    _delivered(channel, NOW - timedelta(hours=1), deleted_at=NOW, phone="0900000004")    # xoá mềm
    _delivered(no_resale_cell, NOW - timedelta(hours=1), phone="0900000005")             # kênh không có ô
    _delivered(channel, datetime(2026, 9, 30, 23, 0, tzinfo=VN), phone="0900000006")  # tạo trước SINCE
    fake = FakePush()

    result = _service(fake).run()

    assert [p["phone"] for _, p in fake.calls] == ["0900000001"]
    assert result.candidates == 1
    assert FoxiaResalePush.objects.count() == 1


@RESALE_ON
def test_loc_theo_ngay_tao_don_khong_theo_ngay_giao():
    """Chốt 2026-10-06: đơn TẠO từ SINCE mà đã giao thành công. Đơn tạo trước SINCE
    giao sau SINCE thì loại; đơn tạo sau SINCE thì lấy dù giao lúc nào."""
    channel = _channel()
    _delivered(channel, NOW - timedelta(hours=1), phone="0900000001",
               created_at=datetime(2026, 9, 28, 10, 0, tzinfo=VN))                        # tạo trước SINCE
    kept = _delivered(channel, NOW - timedelta(hours=1), phone="0900000002",
                      created_at=datetime(2026, 10, 1, 0, 5, tzinfo=VN))                  # tạo sau SINCE
    fake = FakePush()

    _service(fake).run()

    assert [p["phone"] for _, p in fake.calls] == ["0900000002"]
    assert FoxiaResalePush.objects.get().order == kept


@RESALE_ON
def test_don_khong_co_journey_lay_moc_giao_tu_updated_at():
    """Đơn chuyển trạng thái qua đường không phát journey vẫn được lấy, mốc giao = updated_at."""
    at = NOW - timedelta(hours=5)
    order = _delivered(_channel(), at, journey=False)
    fake = FakePush()

    _service(fake).run()

    assert fake.calls[0][1]["created_at"] == to_foxia_utc(at)
    assert FoxiaResalePush.objects.get(order=order).delivered_on == timezone.localdate(at)


@RESALE_ON
def test_khong_gioi_han_so_ngay_ke_tu_since():
    """Bật cờ muộn vẫn nhặt đủ đơn tạo từ SINCE (01/10) — không có lookback cắt ngày."""
    channel = _channel()
    late_now = datetime(2026, 11, 20, 15, 0, tzinfo=VN)
    _delivered(channel, datetime(2026, 10, 1, 0, 5, tzinfo=VN), phone="0900000001")
    _delivered(channel, datetime(2026, 9, 30, 23, 55, tzinfo=VN), phone="0900000002")
    fake = FakePush()

    service = FoxiaResalePushService(push=fake, now=lambda: late_now, shift_context=lambda: SHIFT)
    assert service.waiting_count() == 1
    service.run()

    assert [p["phone"] for _, p in fake.calls] == ["0900000001"]


@RESALE_ON
def test_cung_sdt_cung_ngay_chi_mot_co_hoi():
    channel = _channel()
    first = _delivered(channel, NOW - timedelta(hours=3), order_code="DH1")
    second = _delivered(channel, NOW - timedelta(hours=1), order_code="DH2")
    fake = FakePush()

    result = _service(fake).run()

    assert len(fake.calls) == 1
    assert (result.ok, result.skipped) == (1, 1)
    assert FoxiaResalePush.objects.get(order=first).status == FoxiaResalePushStatus.OK
    assert FoxiaResalePush.objects.get(order=second).status == FoxiaResalePushStatus.SKIPPED_DUPLICATE

    # Lượt sau: đơn thứ ba cùng SĐT cùng ngày vẫn bị chặn nhờ dòng đã ghi trong DB.
    third = _delivered(channel, NOW - timedelta(minutes=30), order_code="DH3")
    _service(fake).run()
    assert len(fake.calls) == 1
    assert FoxiaResalePush.objects.get(order=third).status == FoxiaResalePushStatus.SKIPPED_DUPLICATE


@RESALE_ON
def test_don_khong_sdt_ghi_bo_qua():
    channel = _channel()
    order = _delivered(channel, NOW - timedelta(hours=1), phone="")
    fake = FakePush()

    _service(fake).run()

    assert fake.calls == []
    assert FoxiaResalePush.objects.get(order=order).status == FoxiaResalePushStatus.SKIPPED_NO_PHONE


@pytest.mark.parametrize(
    "flags",
    [
        {"FOXIA_PUSH_ENABLED": False, "FOXIA_RESALE_PUSH_ENABLED": True},
        {"FOXIA_PUSH_ENABLED": True, "FOXIA_RESALE_PUSH_ENABLED": False},
    ],
)
def test_co_tat_thi_khong_ghi_khong_goi(flags):
    with override_settings(FOXIA_RESALE_PUSH_SINCE="2026-10-01", **flags):
        _delivered(_channel(), NOW - timedelta(hours=1))
        fake = FakePush()
        assert _service(fake).run().status == "disabled"
    assert fake.calls == []
    assert not FoxiaResalePush.objects.exists()


@override_settings(FOXIA_PUSH_ENABLED=False, FOXIA_RESALE_PUSH_ENABLED=False, FOXIA_RESALE_PUSH_SINCE="2026-10-01")
def test_dry_run_dem_ung_vien_khi_co_tat_ma_khong_ghi():
    order = _delivered(_channel(), NOW - timedelta(hours=1))
    fake = FakePush()

    result = _service(fake).run(dry_run=True)

    assert result.order_ids == [order.id]
    assert fake.calls == []
    assert not FoxiaResalePush.objects.exists()


@RESALE_ON
@override_settings(FOXIA_RESALE_PUSH_SINCE="hom-qua")
def test_since_sai_dinh_dang_bo_qua_luot():
    assert _service(FakePush()).run().status == "bad_since"


@RESALE_ON
@override_settings(IS_STAGING=True)
def test_staging_khong_bao_gio_day():
    _delivered(_channel(), NOW - timedelta(hours=1))
    assert _service(FakePush()).run().status == "disabled"


def test_message_la_sku_va_gia_sau_giam_cua_don():
    """Chốt 2026-10-07: ghi chú = SKU x SL các dòng + giá SAU GIẢM của ĐƠN (không
    cộng line_total — dòng hàng không mang giảm cấp đơn), bỏ product_raw/mã đơn."""
    order = _delivered(
        _channel(), NOW, product_raw="Combo thảm", total_amount=Decimal("2499000"),
        total_after_discount=Decimal("2450000"),
    )
    for sku, qty in (("DT11", 1), ("MSC06", 2)):
        product = Product.objects.create(sku=sku, name=f"SP {sku}")
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name, sku=sku,
            quantity=qty, unit_price=Decimal("1"), line_total=Decimal("2499000"),
        )

    assert build_message(order, NOW) == "DT11 x1, MSC06 x2 - Giá sau giảm: 2.450.000đ"


def test_task_tu_no_op_khi_co_tat():
    assert push_resale_delivered() == "disabled"


@RESALE_ON
@pytest.mark.no_resale_staff
def test_chia_deu_theo_trong_so_va_bo_qua_sale_chot_don_goc():
    a = _resale_staff("ra", foxia_id="11", weight=2)
    b = _resale_staff("rb", foxia_id="22", weight=1)
    channel = _channel()
    for i in range(6):
        # Đơn gốc mang sale `a`: bộ chia vẫn chọn theo tỉ lệ, không giữ sale chốt đơn.
        _delivered(channel, NOW - timedelta(minutes=60 - i), phone=f"09000000{i:02d}", staff=a)
    fake = FakePush()

    _service(fake).run()

    sent = sorted(p["staff_id"] for _, p in fake.calls)
    assert sent == [11, 11, 11, 11, 22, 22]
    shares = {s.user_id: s.assigned_count for s in LeadAssignmentShare.objects.filter(sheet="resale")}
    assert shares == {a.id: 4, b.id: 2}


@RESALE_ON
def test_ngoai_ca_khong_ghi_cho_luot_sau():
    order = _delivered(_channel(), NOW - timedelta(hours=1))
    fake = FakePush()

    assert _service(fake, shift=(None, NOW.date())).run().status == "outside_shift"
    assert fake.calls == []
    assert not FoxiaResalePush.objects.exists()

    # Vào ca → lượt sau nhặt lại đúng đơn đó.
    assert _service(fake).run().ok == 1
    assert FoxiaResalePush.objects.get().order == order


@RESALE_ON
def test_ca_chua_cau_hinh_cot_resale_thi_cho():
    _delivered(_channel(), NOW - timedelta(hours=1))
    fake = FakePush()

    result = _service(fake, shift=("afternoon", NOW.date())).run()

    assert result.status == "no_resale_config"
    assert fake.calls == []
    assert not FoxiaResalePush.objects.exists()


@RESALE_ON
@pytest.mark.no_resale_staff
def test_sale_mat_foxia_id_sau_khi_cau_hinh_van_day_khong_staff_id():
    _resale_staff("rx", foxia_id="")
    _delivered(_channel(), NOW - timedelta(hours=1))
    fake = FakePush()

    _service(fake).run()

    assert "staff_id" not in fake.calls[0][1]


# ── thống kê cho trang Chia đơn ──────────────────────────────────────────────


@RESALE_ON
def test_day_summary_dem_theo_trang_thai_va_sale(resale_config):
    channel = _channel()
    _delivered(channel, NOW - timedelta(hours=3), order_code="A", phone="0900000001")
    _delivered(channel, NOW - timedelta(hours=2), order_code="B", phone="0900000001")  # trùng SĐT
    _delivered(channel, NOW - timedelta(hours=1), order_code="C", phone="0900000002")
    with patch("django.utils.timezone.now", return_value=NOW):
        _service(FakePush()).run()

    summary = FoxiaResalePushService.day_summary(TODAY)

    assert summary["by_status"]["ok"] == 2
    assert summary["by_status"]["skipped_duplicate"] == 1
    assert summary["by_status"]["failed"] == 0
    assert summary["by_staff"] == [
        {"user_id": resale_config.id, "username": "resale1", "display_name": "resale1",
         "total": 2, "ok": 2, "failed": 0, "pending": 0},
    ]
    assert FoxiaResalePushService.day_summary(TODAY - timedelta(days=1))["by_staff"] == []


@RESALE_ON
def test_state_va_waiting():
    _delivered(_channel(), NOW - timedelta(hours=1))
    fake = FakePush()

    assert _service(fake).state() == "ready"
    assert _service(fake, shift=(None, TODAY)).state() == "outside_shift"
    assert _service(fake, shift=("evening", TODAY)).state() == "no_resale_config"
    assert _service(fake).waiting_count() == 1

    _service(fake).run()
    assert _service(fake).waiting_count() == 0


def test_state_disabled_khi_co_tat():
    assert _service(FakePush()).state() == "disabled"


@RESALE_ON
def test_api_resale_stats():
    client = APIClient()
    client.force_authenticate(make_user_with_permissions("la_viewer", ["lead-assignment:view"]))

    body = client.get(f"/api/foxia/resale-stats?date={TODAY.isoformat()}").json()

    assert body["date"] == TODAY.isoformat()
    assert body["state"] in {"ready", "outside_shift", "no_resale_config"}
    assert set(body["by_status"]) == set(FoxiaResalePushStatus.values)
    assert body["by_staff"] == []
    assert client.get("/api/foxia/resale-stats?date=hom-nay").status_code == 400


def test_api_resale_stats_can_quyen_chia_don():
    client = APIClient()
    client.force_authenticate(make_user_with_permissions("outsider", ["orders:view"]))
    assert client.get("/api/foxia/resale-stats").status_code == 403


def test_beat_quet_moi_phut():
    """User chốt 2026-10-06: job resale chạy mỗi phút."""
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["foxia-push-resale-delivered"]
    assert entry["task"] == "apps.foxia.tasks.push_resale_delivered"
    assert sorted(entry["schedule"].minute) == list(range(60))
    assert entry["options"]["expires"] < 60  # không dồn task khi worker-foxia bận


def test_day_tuan_tu_va_lo_vua_soft_time_limit():
    """07/10: 4 luồng song song làm Foxia timeout hàng loạt → đẩy tuần tự; lô phải
    xong trước soft_time_limit ngay cả khi mọi request đều chạm timeout 10s."""
    from apps.foxia.application.services import resale_push_service as svc

    assert svc.PARALLELISM == 1
    assert push_resale_delivered.soft_time_limit > svc.MAX_BATCH * 10



@RESALE_ON
def test_foxia_khong_tao_co_hoi_ghi_trang_thai_rieng():
    """HTTP 200 nhưng `cohoi_id` rỗng (Foxia trả false): KHÔNG tính là thành công."""
    order = _delivered(_channel(), NOW - timedelta(hours=1))

    result = _service(FakePush(cohoi=False)).run()

    assert (result.ok, result.no_opportunity, result.failed) == (0, 1, 0)
    assert "no_opp:1" in result.summary()
    row = FoxiaResalePush.objects.get(order=order)
    assert row.status == FoxiaResalePushStatus.NO_OPPORTUNITY
    assert row.cohoi_id is None
    # Không tự đẩy lại: POST không idempotent.
    fake = FakePush()
    _service(fake).run()
    assert fake.calls == []
