from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.analytics.tests.helpers import make_customer, make_order
from apps.orders.models import Order

# Không cần `transaction=True`: REFRESH MATERIALIZED VIEW thấy dữ liệu chưa commit
# của chính transaction test và rollback theo test (xem test_marts.py).
pytestmark = pytest.mark.django_db


def test_reconcile_passes_when_mart_matches_source():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=6, total_after_discount=5_000_000)
    refresh_all_marts()

    call_command("dwh_reconcile")  # không raise = khớp


def test_order_created_after_refresh_does_not_trip_reconcile():
    """Ca đã xảy ra trên prod: đơn sinh ra SAU khi refresh khởi động không được
    coi là lệch — mart đúng, chỉ là ảnh chụp cũ hơn nguồn."""
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    later = make_order(status=3, total_after_discount=2_000_000)
    Order.objects.filter(pk=later.pk).update(
        created_at=timezone.now() + timedelta(minutes=5)
    )

    call_command("dwh_reconcile")  # không raise


def test_stale_mart_still_trips_reconcile():
    """Đơn sinh ra TRƯỚC mốc refresh mà mart không có = mart hỏng thật, phải raise."""
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    missed = make_order(status=3, total_after_discount=2_000_000)
    Order.objects.filter(pk=missed.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )

    with pytest.raises(CommandError):
        call_command("dwh_reconcile")


def test_reconcile_errors_clearly_when_never_refreshed():
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")
    with pytest.raises(CommandError, match="chưa refresh"):
        call_command("dwh_reconcile")


def test_reconcile_errors_cleanly_instead_of_crashing_on_unpopulated_mart():
    """FIX 2: đúng ca migration 0006 tạo ra — customer_360 bị DROP + CREATE
    WITH NO DATA nhưng dwh.mart_refresh_log (bảng thật) vẫn giữ dòng log 'ok'
    từ trước migration. Nếu dwh_reconcile không có guard này, nó sẽ đọc thẳng
    customer_360 rỗng và ném OperationalError thô (stack trace) thay vì một
    CommandError rõ ràng bảo phải refresh trước.
    """
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    # Rollback theo test — customer_360 không bị bỏ rỗng cho test sau.
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW dwh.customer_360 WITH NO DATA")

    with pytest.raises(CommandError, match="chưa refresh"):
        call_command("dwh_reconcile")


def test_reconcile_rejects_a_stale_boundary_past_the_age_limit(settings):
    """FIX 1(b): lượt refresh cố kết gần nhất VẪN đủ 3 mart (run_id hợp lệ),
    nhưng đã quá cũ (job 04:25 chết nhiều đêm liên tiếp, không có lượt chạy
    mới nào để so sánh) — dwh_reconcile phải TỪ CHỐI thay vì so mart cũ với
    nguồn-tính-đến-mốc-cũ rồi báo khớp trên một kho dữ liệu đã chết.
    """
    settings.DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS = 36
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    # Lùi cả lượt refresh (mọi mart, cùng run_id) về 40h trước — vượt trần 36h.
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE dwh.mart_refresh_log"
            " SET started_at = started_at - interval '40 hours',"
            "     finished_at = finished_at - interval '40 hours'"
            " WHERE status = 'ok'"
        )

    with pytest.raises(CommandError, match="cũ"):
        call_command("dwh_reconcile")


def test_reconcile_accepts_a_boundary_within_the_age_limit(settings):
    """Đối chứng cho test trên: cùng cấu hình, mốc trong hạn (10h < 36h) vẫn
    phải cho reconcile chạy bình thường.

    Đơn được lùi created_at về 15h trước (SÂU hơn mức boundary sẽ bị lùi, 10h)
    để việc lùi boundary không vô tình biến đơn thật thành "sinh ra sau mốc" —
    nếu không, chính thao tác backdate cho test này sẽ tự tạo ra một lệch số
    (nguồn=0, mart=1) không liên quan gì đến điều đang được kiểm ở đây.
    """
    settings.DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS = 36
    customer = make_customer()
    order = make_order(customer=customer, status=3, total_after_discount=1_000_000)
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=15))
    refresh_all_marts()

    with connection.cursor() as cur:
        cur.execute(
            "UPDATE dwh.mart_refresh_log"
            " SET started_at = started_at - interval '10 hours',"
            "     finished_at = finished_at - interval '10 hours'"
            " WHERE status = 'ok'"
        )

    call_command("dwh_reconcile")  # không raise
