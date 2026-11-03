"""Điều phối ETL. Watermark là MỘT mốc chung '__hot__' cho cả nhóm nóng —
đơn giản hơn mốc/bảng và đúng: mọi bảng nóng đọc cùng cutoff, upsert cùng nhịp.

Thứ tự nạp là RÀNG BUỘC, không phải thẩm mỹ — hai luật:

1. khach_hang PHẢI đứng sau don_hang: transform của nó đọc ai.don_hang, không
   đọc lại orders.
2. EXTERNAL_MARTS (hai bảng chat) PHẢI đứng CUỐI trong MỌI vòng lặp. Chúng là
   nhóm duy nhất phụ thuộc một DB NGOÀI (cầu postgres_fdw, spec 2026-09-04 C1).
   Đặt chúng ở giữa vòng thì một stack crawler đang tắt sẽ giết luôn phần còn
   lại của lượt chạy — với rebuild_all() điều đó nghĩa là
   san_pham/ton_kho/lich_su_kho/chi_phi_quang_cao KHÔNG được nạp, mà bốn bảng đó
   KHÔNG có đường nạp tăng dần nào khác (xem AiMartRebuildSilent trong
   monitoring/alerts.yml). Vì thế rebuild_all() đi theo REBUILD_MARTS = local
   nóng → nhóm đêm → external, đúng spec §5.3 ("full rebuild cả hai bảng, SAU
   ai_mart nhóm đêm"), chứ không phải HOT_MARTS + NIGHTLY_MARTS.

   HAI HÀM XỬ LÝ LỖI EXTERNAL KHÁC NHAU, CÓ CHỦ ĐÍCH (đổi 22/09/2026):
   - sync_hot()    : raise, đúng C2. Nhịp 30' → AiMartSyncSilent bắt trong 90'.
   - rebuild_all() : ghi nhận vào khoá "ngoai_hong" của kết quả rồi đi tiếp.
   Nhịp 1 lần/đêm chưa bao giờ là cảm biến tốt cho cầu FDW; bắt nó chết theo chỉ
   biến AiMartRebuildSilent thành alert nổ vĩnh viễn với mô tả sai, trong khi
   nhóm đêm thực ra đã nạp xong. C2 vẫn được sync_hot() giữ nguyên."""

import logging
import uuid
from datetime import timedelta

from django.db import connections
from django.utils import timezone

from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql import (
    chat_hoi_thoai,
    chat_tin_nhan,
    chi_phi_quang_cao,
    cuoc_goi,
    dong_don_hang,
    don_hang,
    khach_hang,
    lich_su_kho,
    san_pham,
    ton_kho,
)
from apps.ai_mart.infrastructure.sql.base import MartTable

logger = logging.getLogger(__name__)

HOT_KEY = "__hot__"
OVERLAP = timedelta(minutes=5)  # biên chống transaction commit lệch mốc (spec §5.2)

# Nhóm nóng đọc nguồn NỘI BỘ (bảng của chính DB desi). khach_hang PHẢI đứng sau
# don_hang: transform đọc ai.don_hang, không đọc lại orders. cuoc_goi đọc thẳng
# callcenter_call_records + orders, không phụ thuộc mart nào ở trên — đặt cuối
# chỉ để giữ thứ tự thêm mới, không phải ràng buộc dữ liệu.
LOCAL_HOT_MARTS: tuple[MartTable, ...] = (
    don_hang.MART,
    dong_don_hang.MART,
    khach_hang.MART,
    cuoc_goi.MART,
)
# lich_su_kho nạp tăng dần riêng theo moc_id (sync_hot(), sau vòng HOT_MARTS) —
# vẫn liệt kê ở đây vì rebuild_all() TRUNCATE+INSERT toàn bộ như mọi bảng đêm.
NIGHTLY_MARTS: tuple[MartTable, ...] = (
    san_pham.MART,
    ton_kho.MART,
    lich_su_kho.MART,
    chi_phi_quang_cao.MART,
)
# Hai bảng chat đọc qua cầu postgres_fdw sang DB `crawler` (spec 2026-09-04 C1).
# Tách thành nhóm RIÊNG chứ không chỉ "xếp cuối HOT_MARTS": chúng phải đứng cuối
# ở CẢ sync_hot() lẫn rebuild_all(), mà thứ tự của rebuild_all() không suy ra
# được từ vị trí trong HOT_MARTS (xem docstring module, luật 2).
# hoi_thoai đứng sau tin_nhan chỉ để giữ thứ tự đọc, hai bảng không phụ thuộc nhau.
EXTERNAL_MARTS: tuple[MartTable, ...] = (
    chat_tin_nhan.MART,
    chat_hoi_thoai.MART,
)

# sync_hot() chỉ đi HOT_MARTS: external vẫn nằm cuối, đúng như trước.
HOT_MARTS: tuple[MartTable, ...] = LOCAL_HOT_MARTS + EXTERNAL_MARTS
# rebuild_all() đi theo thứ tự NÀY — KHÔNG phải HOT_MARTS + NIGHTLY_MARTS.
REBUILD_MARTS: tuple[MartTable, ...] = LOCAL_HOT_MARTS + NIGHTLY_MARTS + EXTERNAL_MARTS


class EtlFailed(RuntimeError):
    pass


def sync_hot() -> dict:
    run_id = uuid.uuid4()
    cutoff_new = timezone.now()          # chốt mốc TRƯỚC khi đọc
    moc = loaders.read_watermark(HOT_KEY)
    if moc is None:
        # Chưa từng rebuild — sync tăng dần không có nền để đắp lên.
        raise EtlFailed("chưa có watermark __hot__ — chạy rebuild_all trước")
    cutoff = moc - OVERLAP
    results: dict[str, int] = {}
    for mart in HOT_MARTS:
        started = timezone.now()
        try:
            ids = loaders.candidate_ids(mart, cutoff)
            rows = loaders.upsert_by_ids(mart, ids)
        except Exception as exc:
            loaders.log_run(run_id, mart.name, started, timezone.now(), None, "failed", str(exc))
            raise EtlFailed(f"sync {mart.name} hỏng: {exc}") from exc
        loaders.log_run(run_id, mart.name, started, timezone.now(), rows, "ok")
        results[mart.name] = rows

    # lich_su_kho là bảng append-only (spec Q8) — nạp tăng dần theo moc_id ngay
    # trong nhịp hot, không đợi rebuild đêm mới thấy bút toán mới.
    started = timezone.now()
    try:
        rows = lich_su_kho.sync_incremental()
    except Exception as exc:
        loaders.log_run(
            run_id, lich_su_kho.MART.name, started, timezone.now(), None, "failed", str(exc)
        )
        raise EtlFailed(f"sync {lich_su_kho.MART.name} hỏng: {exc}") from exc
    loaders.log_run(run_id, lich_su_kho.MART.name, started, timezone.now(), rows, "ok")
    results[lich_su_kho.MART.name] = rows

    loaders.write_watermark(HOT_KEY, cutoff_new)
    return {"tables": results, "cutoff": cutoff_new.isoformat()}


def rebuild_all() -> dict:
    run_id = uuid.uuid4()
    cutoff_new = timezone.now()
    results: dict[str, int] = {}

    # Từ điển nhu cầu bám theo bảng need_types — admin thêm mã mới lúc nào cũng
    # được, rebuild đêm là chỗ rẻ nhất để mart không sinh mã mồ côi.
    with connections[loaders.TARGET_ALIAS].cursor() as cur:
        refresh_need_dictionary(cur)

    ngoai_hong: dict[str, str] = {}
    ten_ngoai = {m.name for m in EXTERNAL_MARTS}

    for mart in REBUILD_MARTS:
        started = timezone.now()
        try:
            rows = loaders.full_rebuild(mart)
        except Exception as exc:
            loaders.log_run(run_id, mart.name, started, timezone.now(), None, "failed", str(exc))
            # Bảng chat (cầu FDW sang DB crawler) hỏng thì GHI NHẬN rồi đi tiếp,
            # KHÔNG kéo cả lượt rebuild xuống theo. Đây là chỗ khác sync_hot().
            #
            # Vì sao: trước đây mọi lỗi đều raise, mà external xếp CUỐI, nên cầu
            # chat chết = task Celery FAILED dù san_pham/ton_kho/lich_su_kho/
            # chi_phi_quang_cao đã nạp xong đầy đủ ngay trước đó. Không có event
            # task-succeeded thì AiMartRebuildSilent nổ VĨNH VIỄN và mô tả của nó
            # ("3 bảng đó đóng băng") là SAI — chúng vẫn tươi. Đo thật đêm
            # 22/09/2026: nhóm đêm nạp đủ (2.152/243/22.827/9.219 dòng) rồi
            # ai.tin_nhan chết, alert nổ suốt.
            #
            # Bỏ raise ở đây KHÔNG làm mất khả năng phát hiện nguồn chat chết:
            # sync_hot() chạy mỗi 30' vẫn raise đúng theo C2, và AiMartSyncSilent
            # bắt trong vòng 90'. Nhịp đêm 1 lần/ngày chưa bao giờ là cảm biến
            # tốt cho cầu FDW; để nó chết theo chỉ làm hỏng tín hiệu nhóm đêm.
            if mart.name in ten_ngoai:
                ngoai_hong[mart.name] = str(exc)
                logger.error("rebuild %s hỏng (cầu chat), vẫn tính lượt: %s", mart.name, exc)
                continue
            raise EtlFailed(f"rebuild {mart.name} hỏng: {exc}") from exc
        loaders.log_run(run_id, mart.name, started, timezone.now(), rows, "ok")
        results[mart.name] = rows

    # lich_su_kho vừa được TRUNCATE+INSERT toàn bộ ở trên — chốt lại moc_id =
    # max(id) để sync_incremental() kế tiếp không cố INSERT lại các dòng vừa
    # rebuild (PK but_toan_id sẽ chặn, nhưng đừng dựa vào lỗi đó).
    lich_su_kho.reset_watermark_to_max()

    loaders.write_watermark(HOT_KEY, cutoff_new)
    return {"tables": results, "cutoff": cutoff_new.isoformat(), "ngoai_hong": ngoai_hong}
