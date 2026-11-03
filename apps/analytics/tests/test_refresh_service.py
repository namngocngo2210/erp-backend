"""Test `mart_refresh_service`: nhánh refresh, log, mốc boundary.

Chỉ MỘT test dùng `transaction=True`: `test_failed_refresh_is_logged_with_status_failed`
ép REFRESH lỗi thật (query_canceled) — lỗi DB làm transaction Postgres bị abort,
nên dòng log 'failed' chỉ ghi được khi refresh_mart chạy ngoài transaction test
(đúng như prod, autocommit). Mọi test còn lại chạy trong transaction test bình
thường: REFRESH MATERIALIZED VIEW (cả bản CONCURRENTLY) chạy được trong
transaction block, thấy dữ liệu chưa commit của chính transaction đó và rollback
theo test — không cần flush toàn bộ bảng (5–6 s/test) như trước.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import apps.analytics.application.mart_refresh_service as mart_refresh_service
from apps.analytics.application.mart_refresh_service import (
    MART_ORDER,
    MartRefreshFailed,
    is_populated,
    last_refresh_boundary,
    refresh_all_marts,
    refresh_mart,
)


@pytest.fixture(autouse=True)
def _clean_mart_refresh_log(request):
    """`dwh.mart_refresh_log` là bảng SQL thuần tạo bằng RunSQL — không có model
    Django tương ứng, nên bước flush cuối test `transaction=True` KHÔNG dọn nó
    (flush chỉ truncate bảng Django biết qua `django_table_names()`). Dòng log mà
    test `transaction=True` (ở đây là `test_failed_refresh_is_logged_with_status_failed`,
    hoặc ở app khác) commit thật sẽ lọt sang test sau, làm
    `test_boundary_is_the_earliest_start_of_the_last_full_run` tính nhầm mốc
    (MIN lẫn cả `started_at` của một lượt refresh cũ hơn). Với test transaction
    thường, DELETE này rollback theo test — vô hại. Chỉ dọn cho test có mark
    `django_db` — test không đụng DB thì không được cấp quyền DB ở đây.
    """
    if request.node.get_closest_marker("django_db") is None:
        yield
        return
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")
    yield


def test_mart_order_puts_dependencies_first():
    """customer_360 đọc 2 matview kia nên phải refresh sau cùng."""
    assert MART_ORDER.index("dim_customer_profile") < MART_ORDER.index("customer_360")
    assert MART_ORDER.index("fct_order_enriched") < MART_ORDER.index("customer_360")


@pytest.mark.django_db
def test_first_refresh_works_on_unpopulated_matview():
    """Matview tạo WITH NO DATA: lần đầu KHÔNG được dùng CONCURRENTLY."""
    with connection.cursor() as cur:
        for name in MART_ORDER:
            cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name} WITH NO DATA")

    assert is_populated("customer_360") is False
    refresh_all_marts()
    assert is_populated("customer_360") is True


@pytest.mark.django_db
def test_second_refresh_uses_concurrently_and_succeeds():
    refresh_all_marts()
    assert is_populated("customer_360") is True
    refresh_all_marts()
    assert is_populated("customer_360") is True


@pytest.mark.django_db
def test_first_refresh_is_plain_and_second_is_concurrent():
    """Nhánh CONCURRENTLY phải được giữ bằng test: REFRESH thường cũng chạy tốt
    trên view đã nạp, nên nếu chỉ assert is_populated() thì xoá sạch nhánh
    CONCURRENTLY test vẫn xanh."""
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW dwh.dim_customer_profile WITH NO DATA")

    with CaptureQueriesContext(connection) as ctx:
        refresh_mart("dim_customer_profile")
    first = " ".join(q["sql"].upper() for q in ctx.captured_queries)
    assert "REFRESH MATERIALIZED VIEW" in first
    assert "CONCURRENTLY" not in first          # lần đầu: view chưa nạp

    with CaptureQueriesContext(connection) as ctx:
        refresh_mart("dim_customer_profile")
    second = " ".join(q["sql"].upper() for q in ctx.captured_queries)
    assert "CONCURRENTLY" in second             # lần sau: đã nạp


@pytest.mark.django_db
def test_dependent_mart_is_skipped_when_upstream_fails(monkeypatch):
    """dim_customer_profile hỏng -> customer_360 (phụ thuộc trực tiếp) PHẢI bị
    skip, không được refresh trên snapshot dim_customer_profile cũ. Nếu
    không, customer_360 "refresh thành công" nhưng mang chân dung của lần
    refresh trước — một nửa refresh trông giống refresh đầy đủ, không ai biết.
    fct_order_enriched độc lập với dim_customer_profile nên vẫn phải refresh
    bình thường (collect-then-raise cho phần không liên quan).
    """
    real_refresh_mart = mart_refresh_service.refresh_mart
    attempted: list[str] = []

    def fake_refresh_mart(name: str, run_id=None) -> float:
        attempted.append(name)
        if name == "dim_customer_profile":
            raise RuntimeError("boom")
        return real_refresh_mart(name, run_id=run_id)

    monkeypatch.setattr(mart_refresh_service, "refresh_mart", fake_refresh_mart)

    with pytest.raises(MartRefreshFailed):
        mart_refresh_service.refresh_all_marts()

    assert "dim_customer_profile" in attempted
    assert "fct_order_enriched" in attempted       # độc lập -> vẫn chạy
    assert "customer_360" not in attempted         # phụ thuộc hỏng -> SKIP hẳn


@pytest.mark.django_db
def test_refresh_records_a_log_row_per_mart():
    refresh_all_marts()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT mart_name, started_at, finished_at, duration_seconds"
            " FROM dwh.mart_refresh_log ORDER BY id DESC LIMIT %s",
            [len(MART_ORDER)],
        )
        rows = cur.fetchall()
    assert {r[0] for r in rows} == set(MART_ORDER)
    for _, started, finished, duration in rows:
        assert started is not None and finished is not None
        assert finished >= started
        assert duration >= 0


@pytest.mark.django_db
def test_boundary_is_the_earliest_start_of_the_last_full_run():
    refresh_all_marts()
    boundary = last_refresh_boundary()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT min(started_at) FROM dwh.mart_refresh_log"
            " WHERE mart_name = ANY(%s)",
            [list(MART_ORDER)],
        )
        expected = cur.fetchone()[0]
    assert boundary == expected


@pytest.mark.django_db
def test_boundary_is_none_before_any_refresh():
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")
    assert last_refresh_boundary() is None


@pytest.mark.django_db
def test_boundary_uses_latest_run_not_earliest_ever():
    """Test này CẦN lịch sử (>= 2 lượt refresh) mới phân biệt được công thức
    đúng (DISTINCT ON theo mart rồi MIN) với lỗi mà migration 0005 cảnh báo —
    MIN ngây thơ trên toàn bảng, đông cứng mốc ở lần refresh ĐẦU TIÊN mãi mãi.
    Với đúng 1 dòng/mart (như fixture `_clean_mart_refresh_log` để lại cho mỗi
    test) hai công thức trùng nhau y hệt, không bắt được hồi quy — đây chính là
    lỗ hổng mà việc dọn sạch log trước mỗi test vô tình tạo ra cho
    `test_boundary_is_the_earliest_start_of_the_last_full_run`.
    """
    refresh_all_marts()  # lượt 1: sẽ trở thành lịch sử "cũ"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT min(started_at) FROM dwh.mart_refresh_log WHERE mart_name = ANY(%s)",
            [list(MART_ORDER)],
        )
        first_run_earliest_start = cur.fetchone()[0]

    refresh_all_marts()  # lượt 2: đây mới là lượt "gần nhất"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT mart_name, max(started_at) FROM dwh.mart_refresh_log"
            " WHERE mart_name = ANY(%s) GROUP BY mart_name",
            [list(MART_ORDER)],
        )
        second_run_earliest_start = min(started for _, started in cur.fetchall())

    boundary = last_refresh_boundary()

    assert boundary == second_run_earliest_start
    assert boundary > first_run_earliest_start


@pytest.mark.django_db
def test_boundary_is_none_when_any_mart_is_missing_from_the_log():
    """Cold start + 1 mart insert log lỗi -> mart đó KHÔNG có dòng log nào. Nếu
    boundary chỉ lấy MIN trên các mart CÒN LẠI, mốc sẽ TRỄ hơn started_at thật
    của mart bị thiếu -> dwh_reconcile coi dữ liệu đang thiếu là "vẫn ổn", đúng
    lỗi gốc (lệch thời điểm) mặc lớp áo khác. Coverage một phần phải bị coi như
    KHÔNG có coverage: boundary phải là None."""
    refresh_all_marts()
    missing = MART_ORDER[0]
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log WHERE mart_name = %s", [missing])
    assert last_refresh_boundary() is None


# ── FIX 1(a) — run_id: một lượt refresh phải là MỘT lượt CỐ KẾT ──
#
# Lỗ hổng đã sửa: bộ test phía trên (viết trước khi có run_id) chỉ đòi hỏi mỗi
# mart có ÍT NHẤT MỘT dòng log — không đòi các dòng đó đến từ CÙNG một lần
# refresh_all_marts(). Nếu job 04:25 chết 5 đêm liên tiếp, KHÔNG có dòng log
# mới nào được ghi, nhưng mỗi mart vẫn có "dòng mới nhất của riêng nó" từ 5
# đêm trước — set đó vẫn "đủ 3 mart", boundary vẫn ra một mốc "hợp lệ" (chỉ là
# cũ 5 ngày). dwh_reconcile so mart cũ với nguồn-tính-đến-mốc-cũ, khớp tuyệt
# đối, báo "OK" trên kho dữ liệu đã chết. run_id đóng lỗ NÀY (một lần chạy cố
# kết); tuổi của lần chạy cố kết đó được chặn riêng ở FIX 1(b) (dwh_reconcile).


@pytest.mark.django_db
def test_boundary_is_none_when_the_latest_run_is_missing_one_marts_row(monkeypatch):
    """(i) Một lượt refresh_all_marts() bị thiếu dòng log của MỘT mart. Ở đây
    dim_customer_profile crash TRƯỚC khi refresh_mart thật kịp chạy (mô phỏng
    lỗi kết nối/hạ tầng ngoài tầm REFRESH — vẫn là "mất một mart khỏi run",
    một dạng cụ thể hơn của cùng lỗ hổng) nên không có dòng log nào cho nó,
    kể cả 'failed'. customer_360 (phụ thuộc trực tiếp) bị SKIP hẳn nên cũng
    không có dòng log. Cả hai đều thiếu khỏi run_id -> boundary phải là None,
    KHÔNG được lấy MIN trên mart độc lập duy nhất còn refresh được."""
    real_refresh_mart = mart_refresh_service.refresh_mart

    def fake_refresh_mart(name: str, run_id=None) -> float:
        if name == "dim_customer_profile":
            raise RuntimeError("boom")
        return real_refresh_mart(name, run_id=run_id)

    monkeypatch.setattr(mart_refresh_service, "refresh_mart", fake_refresh_mart)

    with pytest.raises(MartRefreshFailed):
        mart_refresh_service.refresh_all_marts()

    with connection.cursor() as cur:
        cur.execute("SELECT mart_name, status FROM dwh.mart_refresh_log")
        rows = dict(cur.fetchall())
    assert "dim_customer_profile" not in rows  # crash trước khi ghi log
    assert rows.get("fct_order_enriched") == "ok"
    assert "customer_360" not in rows  # SKIP -> không log dòng nào

    assert last_refresh_boundary() is None


@pytest.mark.django_db
def test_boundary_is_none_when_the_newest_run_id_covers_only_one_mart():
    """(i), phiên bản dựng trực tiếp bằng SQL để lộ rõ đúng lỗ hổng gốc: một
    run_id CŨ (`run_old`) có đủ 3 mart từ 5 ngày trước; sau đó một run_id MỚI
    HƠN HẲN (`run_new`) chỉ ghi được MỘT mart (mô phỏng job đêm sau chết giữa
    chừng, mới refresh xong dim_customer_profile thì bị kill).

    Thuật toán CŨ (DISTINCT ON theo mart_name trên TOÀN BỘ lịch sử, không xét
    run_id — đúng cái migration 0005 review cảnh báo) vẫn thấy "đủ 3 mart" vì
    2 mart còn lại vẫn có dòng log (dù đã 5 ngày tuổi, đến từ run_old) và trả
    về một mốc trộn lẫn hai lượt chạy: MIN(started_at của run_old) — SAI, và
    đây chính là cách nó "khớp tuyệt đối" với nguồn cũ 5 ngày. Thuật toán ĐÚNG
    phải nhận ra run_id MỚI NHẤT (run_new) không đủ 3 mart và trả None.
    """
    run_old = uuid.uuid4()
    t_old = timezone.now() - timedelta(days=5)
    run_new = uuid.uuid4()
    t_new = timezone.now()

    with connection.cursor() as cur:
        for name in MART_ORDER:
            cur.execute(
                "INSERT INTO dwh.mart_refresh_log"
                " (mart_name, run_id, started_at, finished_at, duration_seconds, status)"
                " VALUES (%s, %s, %s, %s, 1, 'ok')",
                [name, run_old, t_old, t_old],
            )
        cur.execute(
            "INSERT INTO dwh.mart_refresh_log"
            " (mart_name, run_id, started_at, finished_at, duration_seconds, status)"
            " VALUES (%s, %s, %s, %s, 1, 'ok')",
            [MART_ORDER[0], run_new, t_new, t_new],
        )

    assert last_refresh_boundary() is None


@pytest.mark.django_db(transaction=True)
def test_failed_refresh_is_logged_with_status_failed(settings):
    """FIX 5: một REFRESH thật thất bại (không phải mock ở tầng refresh_mart)
    phải để lại dòng log status='failed' — trước đây refresh hỏng biến mất
    khỏi lịch sử hoàn toàn, không có dấu vết gì để chẩn đoán sau này ngoài log
    ứng dụng (hay bị xoay vòng mất). Ép REFRESH thất bại THẬT bằng
    DWH_REFRESH_TIMEOUT_SECONDS cực ngắn (statement_timeout của chính
    refresh_mart(), không lồng thêm timeout ngoài — statement_timeout ngoài sẽ
    bị timeout riêng của refresh_mart() ghi đè trong lúc nó chạy), để đi qua
    đúng nhánh try/except thật bên trong refresh_mart().
    """
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")

    settings.DWH_REFRESH_TIMEOUT_SECONDS = 0.001  # 1ms -> REFRESH chắc chắn bị hủy

    with pytest.raises(Exception):  # noqa: PT011 - lỗi driver DB (query_canceled), không cố định kiểu
        mart_refresh_service.refresh_mart("dim_customer_profile")

    with connection.cursor() as cur:
        cur.execute(
            "SELECT status FROM dwh.mart_refresh_log WHERE mart_name = %s", ["dim_customer_profile"]
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "failed"

    assert last_refresh_boundary() is None


@pytest.mark.django_db
def test_boundary_is_the_second_full_runs_min_not_a_cross_run_mixture():
    """(ii) Hai lượt refresh_all_marts() ĐẦY ĐỦ liên tiếp, mỗi lượt một run_id
    riêng. Boundary phải là MIN(started_at) của LƯỢT THỨ HAI — không được trộn
    dòng log của lượt thứ nhất và lượt thứ hai dù cả hai đều 'đầy đủ 3 mart'."""
    refresh_all_marts()  # run 1
    with connection.cursor() as cur:
        cur.execute("SELECT DISTINCT run_id FROM dwh.mart_refresh_log WHERE status = 'ok'")
        run1_ids = {r[0] for r in cur.fetchall()}
    assert len(run1_ids) == 1
    (run1_id,) = run1_ids

    refresh_all_marts()  # run 2 — run_id MỚI, tách biệt hoàn toàn với run 1
    with connection.cursor() as cur:
        cur.execute(
            "SELECT run_id, started_at FROM dwh.mart_refresh_log"
            " WHERE status = 'ok' AND run_id != %s",
            [run1_id],
        )
        run2_rows = cur.fetchall()
    run2_ids = {r[0] for r in run2_rows}
    assert len(run2_ids) == 1  # cả 3 dòng mới đều cùng một run_id
    expected_boundary = min(r[1] for r in run2_rows)

    with connection.cursor() as cur:
        cur.execute(
            "SELECT max(started_at) FROM dwh.mart_refresh_log WHERE run_id = %s", [run1_id]
        )
        run1_latest_start = cur.fetchone()[0]

    boundary = last_refresh_boundary()
    assert boundary == expected_boundary
    assert boundary > run1_latest_start  # không lẫn dòng cũ của run 1


@pytest.mark.django_db
def test_boundary_is_none_when_a_mart_is_unpopulated_despite_a_healthy_log():
    """FIX 2: migration 0006 DROP + CREATE ... WITH NO DATA cho customer_360,
    nhưng dwh.mart_refresh_log là BẢNG THẬT — nó giữ nguyên các dòng log 'ok'
    từ TRƯỚC migration. Ngay sau migrate, run_id mới nhất trông "đủ 3 mart,
    toàn 'ok'", nhưng customer_360 thực ra đang RỖNG. Nếu last_refresh_boundary
    chỉ xét run_id/status mà bỏ qua relispopulated, nó trả về một mốc "khoẻ
    mạnh" giả — dwh_reconcile sẽ đọc mart rỗng đó và ném OperationalError thô.
    """
    refresh_all_marts()
    assert last_refresh_boundary() is not None  # sanity: khoẻ mạnh trước khi rỗng hoá

    # Test chạy trong transaction thường: REFRESH ... WITH NO DATA rollback theo
    # test, customer_360 không bị bỏ rỗng cho test sau — không cần refresh lại.
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW dwh.customer_360 WITH NO DATA")

    assert is_populated("customer_360") is False
    assert last_refresh_boundary() is None
