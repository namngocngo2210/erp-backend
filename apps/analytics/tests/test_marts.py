"""Test các matview `dwh` — dựng dữ liệu qua ORM, REFRESH, soi từng cột.

KHÔNG dùng `transaction=True`: `REFRESH MATERIALIZED VIEW` (bản thường) là
lệnh transactional — chạy được trong transaction block, thấy dữ liệu chưa commit
của CHÍNH transaction đó, và rollback theo test. Bản cũ khai `transaction=True`
cho cả 33 test vì tưởng REFRESH cần dữ liệu đã commit; cái giá là mỗi test flush
TOÀN BỘ bảng (5–6 s/test, 19/30 mục chậm nhất của suite). Chạy trong transaction
test bình thường thì matview sau REFRESH cũng tự quay về trạng thái cũ khi test
rollback — không cần dọn tay, không rò dữ liệu sang test sau.

Lưu ý còn lại: Postgres `now()` cố định theo mốc bắt đầu transaction, nên các test
neo mốc thời gian từ `timezone.now()` phía Python (cùng giây với `now()` của
transaction) — vẫn đúng như trước.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone

from apps.analytics.sql.eras import (
    RECENCY_ACTIVE_MAX_DAYS,
    RECENCY_AT_RISK_MAX_DAYS,
    RECENCY_COOLING_MAX_DAYS,
)
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.analytics.tests.helpers import make_assessment, make_channel, make_customer, make_order
from apps.callcenter.models import CallQualityScore, CallRecord
from apps.orders.models import NeedGroup, NeedType, Order, OrderItem, OrderNeed, Product

_BUSINESS_TZ = ZoneInfo(BUSINESS_TZ)

pytestmark = pytest.mark.django_db


def refresh(name: str) -> None:
    with connection.cursor() as cur:
        cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name}")


def fetch_profile(customer_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT age_text, age_text_src, occupation_code, occupation_code_src,"
            " filled_field_count, ai_field_count"
            " FROM dwh.dim_customer_profile WHERE customer_id = %s",
            [customer_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "age_text": row[0],
        "age_text_src": row[1],
        "occupation_code": row[2],
        "occupation_code_src": row[3],
        "filled_field_count": row[4],
        "ai_field_count": row[5],
    }


def test_empty_string_is_not_treated_as_a_value():
    """Bẫy chính: order_assessments dùng '' chứ không phải NULL."""
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="", occupation_code="")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile is not None
    assert profile["age_text"] is None
    assert profile["filled_field_count"] == 0


def test_takes_latest_non_empty_value():
    customer = make_customer()
    old = make_order(customer=customer)
    make_assessment(old, age_text="45_54")
    new = make_order(customer=customer)
    make_assessment(new, age_text="65_plus")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id)["age_text"] == "65_plus"


def test_latest_empty_does_not_override_older_value():
    """Phiếu mới để trống thì vẫn giữ giá trị cũ, không xoá mất."""
    customer = make_customer()
    old = make_order(customer=customer)
    make_assessment(old, age_text="65_plus")
    new = make_order(customer=customer)
    make_assessment(new, age_text="")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id)["age_text"] == "65_plus"


def test_provenance_marks_ai_filled_fields():
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="65_plus", ai_filled_fields={"age_text": True})

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["age_text_src"] == "ai"
    assert profile["ai_field_count"] == 1
    assert profile["filled_field_count"] == 1


def test_declared_value_is_marked_declared():
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["age_text_src"] == "declared"
    assert profile["ai_field_count"] == 0


def test_cancelled_order_still_contributes_portrait():
    """Matview này KHÔNG lọc GTC: khách khai tuổi ở đơn bị huỷ thì tuổi đó vẫn đúng.

    Đơn duy nhất của khách có status=6 (huỷ) vẫn phải đóng góp chân dung —
    nếu ai đó thêm GTC filter vào view này, test này phải đỏ trong khi 5 test
    age_text ở trên vẫn xanh (chúng không hề đụng tới status).
    """
    customer = make_customer()
    order = make_order(customer=customer, status=6)
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile is not None
    assert profile["age_text"] == "65_plus"


def test_mirror_orders_are_excluded():
    """Đơn mirror (order_source='foxia') không được đóng góp chân dung."""
    customer = make_customer()
    order = make_order(customer=customer, order_source="foxia")
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id) is None


def test_null_order_source_is_included():
    """order_source NULL (phần lớn đơn cũ) phải được tính, không bị `<>` nuốt mất."""
    customer = make_customer()
    order = make_order(customer=customer, order_source=None)
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile is not None
    assert profile["age_text"] == "65_plus"


def test_soft_deleted_orders_are_excluded():
    """Đơn đã xoá mềm (deleted_at IS NOT NULL) không được đóng góp chân dung."""
    customer = make_customer()
    order = make_order(customer=customer, deleted_at=timezone.now())
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id) is None


def test_provenance_works_for_a_second_field():
    """Lặp lại test provenance cho occupation_code (không phải age_text).

    Bắt lỗi copy-paste ai_key sai trong 4/5 tuple còn lại của _SCALARS — nếu chỉ
    test age_text thì một ai_key sai ở occupation_code sẽ vô hình.
    """
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(
        order, occupation_code="office_worker", ai_filled_fields={"occupation_code": True}
    )

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["occupation_code"] == "office_worker"
    assert profile["occupation_code_src"] == "ai"
    assert profile["ai_field_count"] == 1
    assert profile["filled_field_count"] == 1


def test_field_counts_describe_the_merged_row():
    """filled_field_count/ai_field_count phải mô tả CHÍNH dòng đã merge — tức
    khớp 1-1 với số cột non-null thực sự trả về trên dòng đó, dù các trường
    được góp từ nhiều phiếu khác nhau của cùng một khách.

    Đơn A chỉ góp age_text (1 trường), đơn B góp occupation_code +
    pain_level_code + pain_duration_code (3 trường). Dòng merge cuối cùng có
    4 cột non-null -> filled_field_count PHẢI là 4, không phải 3 (số trường
    của "phiếu giàu nhất" — bán kính đếm cũ đã bị loại bỏ vì tự mâu thuẫn với
    chính dòng dữ liệu nó mô tả).
    """
    customer = make_customer()
    order_a = make_order(customer=customer)
    make_assessment(order_a, age_text="65_plus")
    order_b = make_order(customer=customer)
    make_assessment(
        order_b,
        occupation_code="office_worker",
        pain_level_code="severe",
        pain_duration_code="over_1y",
    )

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["filled_field_count"] == 4


def test_ai_field_count_is_a_subset_of_filled_field_count():
    """ai_field_count phải là tập con nguồn AI của filled_field_count — không
    phải một max() độc lập tính trên phiếu khác. Đơn A góp age_text do AI điền,
    đơn B góp occupation_code do sale khai tay -> filled_field_count=2 (cả hai
    trường đều có giá trị trên dòng merge), ai_field_count=1 (chỉ age_text).
    """
    customer = make_customer()
    order_a = make_order(customer=customer)
    make_assessment(order_a, age_text="65_plus", ai_filled_fields={"age_text": True})
    order_b = make_order(customer=customer)
    make_assessment(order_b, occupation_code="office_worker")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["filled_field_count"] == 2
    assert profile["ai_field_count"] == 1


def fetch_order_row(order_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT is_gtc, item_count, primary_sku, call_count, has_transcript"
            " FROM dwh.fct_order_enriched WHERE order_id = %s",
            [order_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "is_gtc": row[0],
        "item_count": row[1],
        "primary_sku": row[2],
        "call_count": row[3],
        "has_transcript": row[4],
    }


def test_gtc_flag_true_for_delivered_order():
    order = make_order(status=3)
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id)["is_gtc"] is True


def test_gtc_flag_false_for_cancelled_order():
    order = make_order(status=6)
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id)["is_gtc"] is False


def test_mirror_orders_are_excluded_entirely():
    order = make_order(order_source="foxia")
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id) is None


def test_null_order_source_is_kept():
    """Bẫy NULL: viết `order_source <> 'foxia'` sẽ làm mất đơn này."""
    order = make_order(order_source=None)
    refresh("fct_order_enriched")
    with connection.cursor() as cur:
        cur.execute(
            "SELECT order_source FROM dwh.fct_order_enriched WHERE order_id = %s", [order.id]
        )
        rows = cur.fetchall()
    # Đúng 1 dòng và order_source được giữ NGUYÊN là NULL (không bị fill mặc định).
    assert rows == [(None,)]


def test_rescored_call_does_not_inflate_call_count_or_totals():
    """`CallQualityScore.call_record` là FK, không phải OneToOne — cột `unique`
    thật là (call_record, rubric_version), nghĩa là 1 cuộc gọi có thể được chấm
    lại dưới rubric mới, sinh N dòng điểm cho 1 dòng call record.

    Nếu CTE `calls` JOIN thẳng bảng điểm rồi COUNT/SUM trên tập hàng sau JOIN,
    mỗi lần chấm lại sẽ nhân đôi call_count và call_total_seconds cho đơn đó dù
    thực tế chỉ có 1 cuộc gọi. Bẫy im lặng: vẫn ra đúng 1 dòng/đơn (UNIQUE index
    trên order_id không bắt được), REFRESH không lỗi — số liệu chỉ âm thầm sai.

    Quyết định nghiệp vụ: khi có nhiều lần chấm, lấy CHẤM GẦN NHẤT
    (created_at DESC, id DESC) làm quality_total_score, không phải điểm cao
    nhất — chấm lại dưới rubric mới thay thế kết quả cũ, không phải để chọn
    điểm đẹp nhất.
    """
    order = make_order()
    record = CallRecord.objects.create(
        call_id=f"call-{order.id}",
        order=order,
        acd_time=180,
        transcript="noi dung",
    )
    # Điểm cũ CAO HƠN (90), điểm mới THẤP HƠN (60) một cách CỐ Ý: nếu ai đó
    # "dọn" fixture cho hai luật trùng đáp số, test sẽ hết khả năng phân biệt
    # "chấm gần nhất thắng" (đúng, trả 60) với "điểm cao nhất thắng" (sai, trả 90).
    older = CallQualityScore.objects.create(
        call_record=record,
        rubric_version="v1",
        call_date=timezone.now().date(),
        total_score=90,
    )
    CallQualityScore.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(days=1)
    )
    CallQualityScore.objects.create(
        call_record=record,
        rubric_version="v2",
        call_date=timezone.now().date(),
        total_score=60,
    )

    refresh("fct_order_enriched")

    with connection.cursor() as cur:
        cur.execute(
            "SELECT call_count, call_total_seconds, quality_total_score"
            " FROM dwh.fct_order_enriched WHERE order_id = %s",
            [order.id],
        )
        row = cur.fetchone()

    assert row is not None
    call_count, call_total_seconds, quality_total_score = row
    assert call_count == 1
    assert call_total_seconds == 180
    assert float(quality_total_score) == 60.0


def test_quality_total_score_uses_latest_call_not_highest_score():
    """Một đơn có HAI cuộc gọi (2 call record khác nhau, không phải 2 lần chấm
    lại của cùng 1 cuộc gọi — đó là test ở trên). Cuộc gọi CŨ được 90 điểm,
    cuộc gọi MỚI (start_time gần đây hơn) chỉ 30 điểm. quality_total_score
    phải lấy điểm của cuộc gọi GẦN NHẤT (30), không phải max() (90) — max()
    thổi điểm đẹp cho agent bằng cách phớt lờ cuộc gọi tệ gần đây nhất.
    """
    order = make_order()
    older_call = CallRecord.objects.create(
        call_id=f"call-{order.id}-1",
        order=order,
        acd_time=120,
        start_time=timezone.now() - timedelta(days=1),
    )
    newer_call = CallRecord.objects.create(
        call_id=f"call-{order.id}-2",
        order=order,
        acd_time=90,
        start_time=timezone.now(),
    )
    CallQualityScore.objects.create(
        call_record=older_call,
        rubric_version="v1",
        call_date=timezone.now().date(),
        total_score=90,
    )
    CallQualityScore.objects.create(
        call_record=newer_call,
        rubric_version="v1",
        call_date=timezone.now().date(),
        total_score=30,
    )

    refresh("fct_order_enriched")

    with connection.cursor() as cur:
        cur.execute(
            "SELECT quality_total_score FROM dwh.fct_order_enriched WHERE order_id = %s",
            [order.id],
        )
        (quality_total_score,) = cur.fetchone()

    assert float(quality_total_score) == 30.0


def fetch_360(customer_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT orders_gtc, orders_all, total_spent, avg_order_value,"
            " days_since_last_purchase, repurchase_gap_median_days"
            " FROM dwh.customer_360 WHERE customer_id = %s",
            [customer_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "orders_gtc": row[0],
        "orders_all": row[1],
        "total_spent": row[2],
        "avg_order_value": row[3],
        "days_since_last_purchase": row[4],
        "repurchase_gap_median_days": row[5],
    }


def refresh_all() -> None:
    for name in ("dim_customer_profile", "fct_order_enriched", "customer_360"):
        refresh(name)


def test_cancelled_orders_counted_but_not_spent():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=6, total_after_discount=9_000_000)

    refresh_all()

    row = fetch_360(customer.id)
    assert row["orders_gtc"] == 1
    assert row["orders_all"] == 2
    assert int(row["total_spent"]) == 1_000_000


def test_avg_order_value_uses_gtc_only():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=3, total_after_discount=3_000_000)
    make_order(customer=customer, status=6, total_after_discount=9_000_000)

    refresh_all()

    assert int(fetch_360(customer.id)["avg_order_value"]) == 2_000_000


def test_customer_without_gtc_order_still_present():
    """Khách chỉ có đơn huỷ vẫn phải có mặt — nếu không sẽ mất khỏi tệp remarketing."""
    customer = make_customer()
    make_order(customer=customer, status=6, total_after_discount=1_000_000)

    refresh_all()

    row = fetch_360(customer.id)
    assert row is not None
    assert row["orders_gtc"] == 0
    assert int(row["total_spent"]) == 0


def test_days_since_last_purchase_uses_business_timezone_not_utc():
    """USE_TZ=True ghim session ở UTC. Nếu days_since_last_purchase trừ theo
    ngày UTC trần (now()::date - last_purchase_at::date), một đơn mua 05:00
    sáng giờ VN (UTC+7) rơi vào NGÀY UTC HÔM TRƯỚC, ra
    days_since_last_purchase=1 dù created_date của chính đơn đó (quy đổi qua
    BUSINESS_TZ ở fct_order_enriched) vẫn là "hôm nay". Đơn mua sáng nay giờ
    VN phải cho kết quả 0, không phải 1.
    """
    today_hcm = timezone.localtime(timezone.now(), _BUSINESS_TZ).date()
    early_morning = datetime.combine(today_hcm, time(5, 0), tzinfo=_BUSINESS_TZ)

    customer = make_customer()
    order = make_order(customer=customer, status=3)
    # created_at có auto_now_add=True: create(created_at=...) bị Django ghi đè
    # bằng now() lúc save(). Phải sửa bằng .update() (bulk update bỏ qua
    # auto_now_add) để ép giờ tạo đơn về đúng 05:00 sáng giờ VN hôm nay.
    Order.objects.filter(pk=order.pk).update(created_at=early_morning)

    refresh_all()

    assert fetch_360(customer.id)["days_since_last_purchase"] == 0


def test_repurchase_gap_median_days_for_two_orders():
    """Khách có ĐÚNG 2 đơn GTC cách nhau 20 ngày -> median chính là khoảng
    cách đó (chỉ có một gap duy nhất để tính median)."""
    base = timezone.now()
    customer = make_customer()
    older = make_order(customer=customer, status=3)
    newer = make_order(customer=customer, status=3)
    # created_at có auto_now_add=True — dùng .update() để ép ngày tạo, xem
    # comment ở test days_since_last_purchase phía trên.
    Order.objects.filter(pk=older.pk).update(created_at=base - timedelta(days=20))
    Order.objects.filter(pk=newer.pk).update(created_at=base)

    refresh_all()

    gap = fetch_360(customer.id)["repurchase_gap_median_days"]
    assert gap == pytest.approx(20.0, abs=0.01)


def test_repurchase_gap_median_days_is_none_for_a_single_order():
    """Khách mới có ĐÚNG 1 đơn GTC — chưa từng mua lại lần nào, nên phải là
    None, KHÔNG PHẢI 0. 0 nghĩa là "mua lại tức thì" (gap 0 ngày), một trạng
    thái nghiệp vụ hoàn toàn khác với "chưa từng mua lại" — gộp hai trạng
    thái này vào cùng giá trị 0 là sai.
    """
    customer = make_customer()
    make_order(customer=customer, status=3)

    refresh_all()

    assert fetch_360(customer.id)["repurchase_gap_median_days"] is None


def fetch_360_col(customer_id: int, col: str):
    with connection.cursor() as cur:
        cur.execute(f"SELECT {col} FROM dwh.customer_360 WHERE customer_id = %s", [customer_id])  # noqa: S608
        row = cur.fetchone()
    if row is None:
        return None
    return row[0]


def test_era_legacy_current_and_both():
    old = make_customer()
    o1 = make_order(customer=old, status=3, total_after_discount=50_000)
    Order.objects.filter(pk=o1.pk).update(created_at=timezone.make_aware(datetime(2023, 5, 1, 10, 0)))

    new = make_customer()
    make_order(customer=new, status=3, total_after_discount=1_500_000)

    both = make_customer()
    o2 = make_order(customer=both, status=3, total_after_discount=50_000)
    Order.objects.filter(pk=o2.pk).update(created_at=timezone.make_aware(datetime(2023, 6, 1, 10, 0)))
    make_order(customer=both, status=3, total_after_discount=1_500_000)

    refresh_all()

    assert fetch_360_col(old.id, "era") == "legacy"
    assert fetch_360_col(new.id, "era") == "current"
    assert fetch_360_col(both.id, "era") == "both"


def test_price_tier_lifetime_uses_fixed_thresholds():
    """Tên cột có hậu tố `_lifetime`: gộp TOÀN BỘ đơn GTC bất kể era, khác với
    hành vi CHỈ thuộc kỳ hiện tại mà tên cũ `price_tier` dễ đọc nhầm thành."""
    low = make_customer()
    make_order(customer=low, status=3, total_after_discount=600_000)
    mid = make_customer()
    make_order(customer=mid, status=3, total_after_discount=1_500_000)
    high = make_customer()
    make_order(customer=high, status=3, total_after_discount=2_500_000)
    refresh_all()
    assert fetch_360_col(low.id, "price_tier_lifetime") == "low"
    assert fetch_360_col(mid.id, "price_tier_lifetime") == "mid"
    assert fetch_360_col(high.id, "price_tier_lifetime") == "high"


def test_purchase_rhythm_lifetime_and_recency():
    once = make_customer()
    make_order(customer=once, status=3, total_after_discount=1_500_000)
    twice = make_customer()
    make_order(customer=twice, status=3, total_after_discount=1_500_000)
    make_order(customer=twice, status=3, total_after_discount=1_500_000)
    refresh_all()
    assert fetch_360_col(once.id, "purchase_rhythm_lifetime") == "one_time"
    assert fetch_360_col(twice.id, "purchase_rhythm_lifetime") == "repeat"
    assert fetch_360_col(once.id, "recency_bucket") == "active"


def test_cancelled_only_customer_has_null_behavior_not_wrong_labels():
    """Khách chỉ có đơn huỷ: era/price_tier_lifetime/rhythm phải NULL, KHÔNG được
    gán nhãn sai."""
    customer = make_customer()
    make_order(customer=customer, status=6, total_after_discount=1_500_000)
    refresh_all()
    assert fetch_360_col(customer.id, "era") is None
    assert fetch_360_col(customer.id, "price_tier_lifetime") is None
    assert fetch_360_col(customer.id, "purchase_rhythm_lifetime") is None


def test_recency_bucket_covers_all_four_bands():
    """Bốn khách rơi vào bốn dải recency khác nhau. Offset ngày SUY từ chính các
    hằng số ngưỡng (điểm giữa mỗi khoảng), không hard-code số ngày cứng — nếu
    ngưỡng đổi, test vẫn nằm chắc trong dải mới, không đứng ngay biên.
    """
    now = timezone.now()
    active_days = RECENCY_ACTIVE_MAX_DAYS // 2
    cooling_days = (RECENCY_ACTIVE_MAX_DAYS + RECENCY_COOLING_MAX_DAYS) // 2
    at_risk_days = (RECENCY_COOLING_MAX_DAYS + RECENCY_AT_RISK_MAX_DAYS) // 2
    dormant_days = RECENCY_AT_RISK_MAX_DAYS + 100

    active = make_customer()
    o_active = make_order(customer=active, status=3)
    Order.objects.filter(pk=o_active.pk).update(created_at=now - timedelta(days=active_days))

    cooling = make_customer()
    o_cooling = make_order(customer=cooling, status=3)
    Order.objects.filter(pk=o_cooling.pk).update(created_at=now - timedelta(days=cooling_days))

    at_risk = make_customer()
    o_at_risk = make_order(customer=at_risk, status=3)
    Order.objects.filter(pk=o_at_risk.pk).update(created_at=now - timedelta(days=at_risk_days))

    dormant = make_customer()
    o_dormant = make_order(customer=dormant, status=3)
    Order.objects.filter(pk=o_dormant.pk).update(created_at=now - timedelta(days=dormant_days))

    refresh_all()

    assert fetch_360_col(active.id, "recency_bucket") == "active"
    assert fetch_360_col(cooling.id, "recency_bucket") == "cooling"
    assert fetch_360_col(at_risk.id, "recency_bucket") == "at_risk"
    assert fetch_360_col(dormant.id, "recency_bucket") == "dormant"


def test_recency_bucket_agrees_with_days_since_last_purchase_at_band_edge():
    """`test_recency_bucket_covers_all_four_bands` dùng ĐIỂM GIỮA mỗi dải nên
    không thể bắt được lỗi lệch 1 NGÀY: 0006 (trước fix) tính `recency_bucket`
    bằng `now()::date` trần trong khi `days_since_last_purchase` dùng
    `AT TIME ZONE BUSINESS_TZ` — job refresh chạy 04:25 giờ VN = 21:25 UTC hôm
    trước, nên `now()::date` gần như LUÔN là một ngày trễ hơn ngày kinh doanh.
    Hệ quả: khách đúng 91 ngày (phải là 'cooling') bị tính ra 90 ngày và gắn
    nhãn 'active'. Test này đứng NGAY biên 90/91 để bắt đúng lỗi lệch 1 ngày.

    Mốc dựng từ "hôm nay" theo BUSINESS_TZ tại THỜI ĐIỂM CHẠY TEST (không
    hard-code ngày cứng), neo ở 12:00 trưa business-tz — tự nó không phải bom
    hẹn giờ theo lịch: chạy suite lúc 00:00-07:00 giờ VN không đẩy biên đi đâu
    vì cả "hôm nay" lẫn last_purchase_at đều suy ra CÙNG một cách, CÙNG múi
    giờ, không phụ thuộc giờ đồng hồ hệ thống lúc chạy.
    """

    def business_noon(d):
        return datetime.combine(d, time(12, 0), tzinfo=_BUSINESS_TZ)

    today_business = timezone.now().astimezone(_BUSINESS_TZ).date()

    at_boundary = make_customer()  # đúng 90 ngày -> active (biên trong)
    o_at = make_order(customer=at_boundary, status=3)
    Order.objects.filter(pk=o_at.pk).update(
        created_at=business_noon(today_business - timedelta(days=RECENCY_ACTIVE_MAX_DAYS))
    )

    past_boundary = make_customer()  # 91 ngày -> cooling (biên ngoài)
    o_past = make_order(customer=past_boundary, status=3)
    Order.objects.filter(pk=o_past.pk).update(
        created_at=business_noon(
            today_business - timedelta(days=RECENCY_ACTIVE_MAX_DAYS + 1)
        )
    )

    refresh_all()

    at_row = fetch_360(at_boundary.id)
    past_row = fetch_360(past_boundary.id)
    assert at_row["days_since_last_purchase"] == RECENCY_ACTIVE_MAX_DAYS
    assert past_row["days_since_last_purchase"] == RECENCY_ACTIVE_MAX_DAYS + 1
    assert fetch_360_col(at_boundary.id, "recency_bucket") == "active"
    assert fetch_360_col(past_boundary.id, "recency_bucket") == "cooling"


def test_buyer_role_prefers_mixed_over_single_intent():
    """Khách có nhu cầu thuộc CẢ HAI nhóm gift + buy_for_self phải là 'mixed',
    không phải 'gift' — nếu nhánh 'gift' được kiểm tra trước nhánh 'mixed' trong
    CASE, một khách có ý định kép sẽ bị gán nhãn sai và mất đi tín hiệu đó.
    """
    # Không dựa vào NeedGroup/NeedType seed bởi migration 0026/0052: test
    # `transaction=True` ở app khác (ai_mart, coverage_api) chạy trước có thể đã
    # FLUSH sạch bảng catalog. Dựng trực tiếp bằng get_or_create, mã group PHẢI
    # đúng 'gift'/'buy_for_self' vì đó là literal mà CASE trong migration so khớp.
    gift_group, _ = NeedGroup.objects.get_or_create(code="gift", defaults={"name_vi": "Quà tặng"})
    self_group, _ = NeedGroup.objects.get_or_create(
        code="buy_for_self", defaults={"name_vi": "Mua cho bản thân"}
    )
    gift_type, _ = NeedType.objects.get_or_create(
        code="test-gift-need", defaults={"group": gift_group, "name_vi": "Quà tặng test"}
    )
    self_type, _ = NeedType.objects.get_or_create(
        code="test-self-need", defaults={"group": self_group, "name_vi": "Bản thân test"}
    )

    mixed = make_customer()
    order_mixed = make_order(customer=mixed, status=3)
    assessment_mixed = make_assessment(order_mixed)
    OrderNeed.objects.create(order_assessment=assessment_mixed, need_type=gift_type)
    OrderNeed.objects.create(order_assessment=assessment_mixed, need_type=self_type)

    gift_only = make_customer()
    order_gift = make_order(customer=gift_only, status=3)
    assessment_gift = make_assessment(order_gift)
    OrderNeed.objects.create(order_assessment=assessment_gift, need_type=gift_type)

    self_only = make_customer()
    order_self = make_order(customer=self_only, status=3)
    assessment_self = make_assessment(order_self)
    OrderNeed.objects.create(order_assessment=assessment_self, need_type=self_type)

    refresh_all()

    assert fetch_360_col(mixed.id, "buyer_role") == "mixed"
    assert fetch_360_col(gift_only.id, "buyer_role") == "gift"
    assert fetch_360_col(self_only.id, "buyer_role") == "self"


def test_channel_and_product_affinity_take_the_latest_order():
    """channel_affinity/product_affinity phải lấy từ đơn GTC GẦN NHẤT, không
    phải đơn đầu tiên. Khách có 2 đơn GTC trên 2 kênh khác nhau với 2 SKU khác
    nhau; đơn sau (mới hơn) phải thắng.
    """
    customer = make_customer()
    channel_old = make_channel(name="Kenh cu")
    channel_new = make_channel(name="Kenh moi")
    product_old = Product.objects.create(name="SP cu", sku="SKU-AFFINITY-OLD")
    product_new = Product.objects.create(name="SP moi", sku="SKU-AFFINITY-NEW")

    order_old = make_order(customer=customer, channel=channel_old, status=3)
    OrderItem.objects.create(
        order=order_old, product=product_old, sku=product_old.sku, quantity=1, line_total=500_000
    )
    Order.objects.filter(pk=order_old.pk).update(
        created_at=timezone.now() - timedelta(days=10)
    )

    order_new = make_order(customer=customer, channel=channel_new, status=3)
    OrderItem.objects.create(
        order=order_new, product=product_new, sku=product_new.sku, quantity=1, line_total=500_000
    )
    Order.objects.filter(pk=order_new.pk).update(created_at=timezone.now())

    refresh_all()

    assert fetch_360_col(customer.id, "channel_affinity") == channel_new.name
    assert fetch_360_col(customer.id, "product_affinity") == product_new.sku


def test_channel_and_product_affinity_tie_break_by_order_id():
    """Hai đơn GTC CÙNG created_at (tie) không được chọn ngẫu nhiên giữa các
    lần refresh — `ORDER BY created_at DESC, order_id DESC` đảm bảo đơn có
    order_id LỚN HƠN (tạo sau, dù cùng created_at) luôn thắng, ổn định."""
    customer = make_customer()
    channel_a = make_channel(name="Kenh A")
    channel_b = make_channel(name="Kenh B")
    product_a = Product.objects.create(name="SP A", sku="SKU-TIE-A")
    product_b = Product.objects.create(name="SP B", sku="SKU-TIE-B")

    order_a = make_order(customer=customer, channel=channel_a, status=3)
    OrderItem.objects.create(
        order=order_a, product=product_a, sku=product_a.sku, quantity=1, line_total=500_000
    )
    order_b = make_order(customer=customer, channel=channel_b, status=3)  # tạo SAU -> order_id lớn hơn
    OrderItem.objects.create(
        order=order_b, product=product_b, sku=product_b.sku, quantity=1, line_total=500_000
    )
    assert order_b.pk > order_a.pk

    tie_at = timezone.now()
    Order.objects.filter(pk__in=[order_a.pk, order_b.pk]).update(created_at=tie_at)

    refresh_all()

    assert fetch_360_col(customer.id, "channel_affinity") == channel_b.name
    assert fetch_360_col(customer.id, "product_affinity") == product_b.sku


def test_recency_bucket_uses_business_timezone():
    """Test CẤU TRÚC, không phải hành vi — pin dạng SQL, không phụ thuộc đồng
    hồ thật. `test_recency_bucket_agrees_with_days_since_last_purchase_at_band_edge`
    ở trên chỉ phát hiện được lệch giờ khi thời điểm CHẠY TEST rơi đúng vào
    khung UTC/VN khác ngày (00:00-07:00 giờ VN) — vì now() được Postgres tính
    thật, pytest không "đóng băng" được nó. Ngoài khung đó, một bản revert về
    now()::date trần vẫn xanh gần 17/24 giờ trong ngày — đúng kiểu "test xanh
    nhưng không chứng minh được gì" mà dự án này đã dính nhiều lần. Test này
    đọc thẳng SQL đã ghép của migration và assert CẤU TRÚC của biểu thức, nên
    pin được 100% thời gian, không phụ thuộc giờ chạy.
    """
    import importlib

    migration = importlib.import_module(
        "apps.analytics.migrations.0006_customer_360_behavior"
    )
    forward_sql = migration.FORWARD_SQL

    end_marker = "END AS recency_bucket"
    end_idx = forward_sql.index(end_marker)
    start_idx = forward_sql.rindex("CASE", 0, end_idx)
    recency_case_sql = forward_sql[start_idx : end_idx + len(end_marker)]

    now_wrapped = recency_case_sql.count("now() AT TIME ZONE")
    last_purchase_wrapped = recency_case_sql.count("a.last_purchase_at AT TIME ZONE")

    assert now_wrapped > 0, (
        "recency_bucket phải dùng AT TIME ZONE ở CẢ HAI vế, nếu không nó lệch"
        " 1 ngày so với days_since_last_purchase ngay bên cạnh — job chạy"
        " 04:25 VN = 21:25 UTC hôm trước"
    )
    assert last_purchase_wrapped > 0, (
        "recency_bucket phải dùng AT TIME ZONE ở CẢ HAI vế, nếu không nó lệch"
        " 1 ngày so với days_since_last_purchase ngay bên cạnh — job chạy"
        " 04:25 VN = 21:25 UTC hôm trước"
    )
    assert now_wrapped == last_purchase_wrapped, (
        "hai vế phải được bọc AT TIME ZONE ĐỀU NHAU ở mọi nhánh WHEN — một vế"
        " bọc, một vế trần vẫn cho kết quả sai y hệt như cả hai đều trần"
    )
    # Bắt cả trường hợp revert MỘT PHẦN (chỉ 1 trong 2 vế bị trả về dạng trần):
    # dạng bọc là "(now() AT TIME ZONE '...')::date" nên KHÔNG chứa chuỗi con
    # "now()::date" liền nhau — substring check này an toàn, không báo sai.
    assert "now()::date" not in recency_case_sql, (
        "recency_bucket phải dùng AT TIME ZONE ở CẢ HAI vế, nếu không nó lệch"
        " 1 ngày so với days_since_last_purchase ngay bên cạnh — job chạy"
        " 04:25 VN = 21:25 UTC hôm trước"
    )
    assert "a.last_purchase_at::date" not in recency_case_sql, (
        "recency_bucket phải dùng AT TIME ZONE ở CẢ HAI vế, nếu không nó lệch"
        " 1 ngày so với days_since_last_purchase ngay bên cạnh — job chạy"
        " 04:25 VN = 21:25 UTC hôm trước"
    )
