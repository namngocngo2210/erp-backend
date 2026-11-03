import logging
from celery import shared_task
from django.db import OperationalError

from apps.common.db_guards import single_flight
from .customer_type import reconcile_customer_type
from .segment_snapshot_service import SegmentSnapshotService

logger = logging.getLogger(__name__)

# Khóa single-flight: chu kỳ beat sau bị bỏ qua nếu chu kỳ trước chưa xong. Chặn nhiều
# instance ghi đè segment snapshot / đối soát loại khách chạy chồng, thrash work_mem →
# death-spiral. TTL là van an toàn (> time_limit) để tự nhả nếu worker chết.
_SEGMENT_LOCK_KEY = "lock:rebuild_segment_snapshot"
_SEGMENT_LOCK_TTL = 1200  # > time_limit=900


class PrecomputeStepsFailed(Exception):
    """Ít nhất một bước precompute hỏng trong chu kỳ.

    Ném ra SAU khi đã chạy hết các bước, nên vẫn giữ nguyên tính chất "một report
    chậm không chặn các report còn lại" — chỉ khác là chu kỳ hỏng không còn được
    Celery ghi nhận là thành công.
    """


def _rebuild_report(label: str, fn) -> bool:
    """Chạy một precompute report, nuốt lỗi (kể cả statement_timeout) và log — để một
    report chậm/timeout không chặn các report còn lại trong cùng chu kỳ.

    Trả về True nếu chạy xong, False nếu đã nuốt lỗi. Người gọi PHẢI tổng hợp lại các
    bước hỏng và báo ra ngoài; nếu chỉ log rồi thôi thì chu kỳ hỏng sẽ không ai biết.
    """
    try:
        fn()
        logger.info("%s rebuilt", label)
        return True
    except Exception:
        logger.exception("%s rebuild failed (skipped this cycle)", label)
        return False


@shared_task(
    name="apps.customers.tasks.rebuild_segment_snapshot",
    soft_time_limit=600,
    time_limit=900,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def rebuild_segment_snapshot(full: bool = False) -> dict:
    """Ghi segment snapshot khách hàng và đối soát loại khách (customer_type) cho chu kỳ 2h.

    ``full=False`` (mặc định, nhịp 2h): chỉ tính lại snapshot của khách CÓ THAY ĐỔI
    kể từ chu kỳ trước. ``full=True`` (1 lần/ngày, giờ thấp điểm): quét lại toàn bộ.

    Vì sao tách (đo pg_stat_statements 2026-08-11): rebuild toàn bộ mỗi 2h là
    workload nặng thứ hai của cả database — 5 câu × 80.741 lượt, một câu đọc ra
    100,5 triệu dòng, tổng ~23% `total_exec_time`. Gần như toàn bộ khối lượng đó
    là tính lại snapshot cho những khách không hề phát sinh gì. Lần full hằng ngày
    giữ lại lưới an toàn cho mọi đường sửa dữ liệu không chạm `updated_at`
    (thao tác tay trên DB, xoá cứng) — cùng tinh thần với `reconcile_customer_type`.

    Ném ``PrecomputeStepsFailed`` nếu có bước hỏng. Trước đây task nuốt hết lỗi rồi
    vẫn trả về số dòng snapshot, nên Celery ghi nhận ``succeeded`` kể cả khi bước
    đối soát loại khách chết vì statement timeout (sự cố 2026-07-22, khi đó task còn
    gồm nhiều bước precompute report hơn) — dashboard đứng số và drift loại khách âm
    thầm tích tụ mà không alert nào nổ. Để task fail đúng lúc nó fail thì
    celery-exporter mới đếm được, và rule CeleryTaskFailing mới bắt được.
    """
    with single_flight(_SEGMENT_LOCK_KEY, _SEGMENT_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("rebuild_segment_snapshot already running — skip this cycle")
            return {"skipped": True, "rows": 0, "failed": []}

        if full:
            # `skip_unchanged` chỉ bật ở đây: lần full ghi đè cả bảng để đổi vài
            # trăm dòng (đo prod 2026-08-17: 755.219 dòng ghi / ~300 dòng đổi
            # thật). Đường tăng dần phải giữ mặc định tắt để mốc nước còn tiến —
            # xem docstring `SegmentSnapshotService.rebuild`.
            n = SegmentSnapshotService.rebuild(skip_unchanged=True)
            logger.info("segment snapshot rebuilt (full): %s rows written", n)
        else:
            n, fell_back = SegmentSnapshotService.rebuild_incremental()
            logger.info(
                "segment snapshot rebuilt (incremental%s): %s rows",
                " → full: chưa có mốc nước" if fell_back else "",
                n,
            )

        steps = []
        if full:
            # Lưới an toàn cho loại khách: sửa drift từ các đường không qua signal
            # (đổi customer của đơn, xóa cứng, thao tác tay DB). Chỉ chạy ở lần
            # full nightly — đo pg_stat_statements 2026-08-19: UPDATE full-scan
            # customers×orders 4,2s/lần, chạy 12 lần/ngày trong giờ làm việc chỉ
            # để vớt drift mà bản chất là hiếm; cùng tinh thần với skip_unchanged.
            steps.append(("customer type reconcile", reconcile_customer_type))
        failed = [label for label, fn in steps if not _rebuild_report(label, fn)]

        if failed:
            raise PrecomputeStepsFailed(
                f"{len(failed)}/{len(steps)} bước precompute hỏng: {', '.join(failed)}"
            )
        return {"skipped": False, "rows": n, "failed": []}
