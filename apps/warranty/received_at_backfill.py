"""Rà (và chỉ khi người vận hành đồng ý mới nắn) `warehouse_received_at` cho các
hành trình bị cron đóng dấu ngày sai.

`warehouse_received_at` là trường NGƯỜI DÙNG SỬA ĐƯỢC (xem
`JOURNEY_EDITABLE_FIELDS`) nên không được nắn mù. Ứng viên duy nhất có thể suy
ra từ dữ liệu là: giá trị hiện tại vẫn ĐÚNG BẰNG thứ cron đã ghi — tức bằng ngày
(giờ VN) mà bản ghi quét chuyển sang `matched`, xấp xỉ bằng
`pending_shipment_scans.updated_at`.

TIỀN ĐỀ THẬT SỰ, và nó KHÔNG chắc chắn: `updated_at` là `auto_now`, nên nó chỉ
là *lần cuối bản ghi quét bị ghi*, không phải *lúc nó được khớp*. Bản ghi đã
`matched` vẫn còn bị đụng: `PendingScanService.update_note` KHÔNG lọc trạng
thái nào cả (docstring của nó ghi thẳng "mọi trạng thái") và lưu bằng
`update_fields=["note", "updated_at"]` ⇒ `auto_now` dời `updated_at` sang ngày
sửa ghi chú. Đường đi có thật qua HTTP:
`apps/inventory/transport/views/flow_history_views.py` (endpoint sửa ghi chú).
Hai chiều lệch, KHÔNG đối xứng về mức nguy hiểm:

  - THIẾU NẮN (lành tính): ghi chú được sửa SAU khi khớp ⇒ `updated_at` nhảy
    sang ngày khác, vế chặn không còn khớp ⇒ một hành trình cron ghi sai ngày
    bị bỏ qua. Dữ liệu giữ nguyên như trước migration, sửa tay được.
  - NẮN QUÁ TAY (nguy hiểm): giá trị do NGƯỜI gõ lại tình cờ bằng ngày
    `updated_at` ⇒ bị coi là do cron ghi và bị đè. Ca kinh điển không dính tới
    `update_note`: hàng quét ngày D, tới D+2 nhân viên mới lập hành trình và tự
    gõ `warehouse_received_at = D+2`; vài phút sau cron khớp — nó KHÔNG đè vì
    trường đã có giá trị (xem `mark_returned_by_tracking`) — nhưng `updated_at`
    lúc đó vẫn rơi vào D+2, đúng bằng giá trị người gõ. Rà soát sau đó thấy
    "khớp" và sẽ đè về D.

TẠI SAO KHÔNG CÒN TÍN HIỆU NÀO KHÁC ĐỂ PHÂN BIỆT HAI CA TRÊN:
  - `pending_shipment_scans` không có cột `matched_at` riêng.
  - `apps/audit` chỉ dò được thực thể cho đơn/khách/sản phẩm/kênh/người dùng;
    một hành trình bảo hành ghi log với `entity_type='OTHER'`, `entity_id=None`
    ⇒ không lần ra được hành trình nào đã bị sửa tay.
  - `warranty_journeys.updated_at` cũng vô dụng: `tracking_sync_service` ghi lại
    hành trình đều đặn.

⇒ HAI CA KHÔNG PHÂN BIỆT ĐƯỢC BẰNG DỮ LIỆU. Vì vậy việc nắn KHÔNG chạy tự động
trong migration nữa (migration 0016 chỉ CÒN BÁO CÁO). Mặc định `apply=False` chỉ
SELECT ra đúng tập dòng mà lệnh UPDATE sẽ đụng và KHÔNG ghi gì; người vận hành
duyệt danh sách đó rồi tự chạy `manage.py backfill_warranty_received_at --apply`.
Đổi một trường người dùng sửa được là quyết định nghiệp vụ, không phải hệ quả
của việc deploy.

Chiều ngược của migration là `noop` và không có bản sao nào của giá trị cũ, nên
cả hai chế độ đều trả `(journey_id, ngày_cũ, ngày_mới)`: với `apply=True` đó là
**đường về duy nhất** cho một dòng bị nắn oan. Giữ log của lần chạy thật.

Không chia lô (khác `occurred_at_backfill._BATCH = 50_000`): `warranty_journeys`
là bảng nghiệp vụ cỡ nghìn dòng, không phân mảnh, nên một UPDATE duy nhất chạy
xong trong mili-giây — chia lô ở đây chỉ thêm phức tạp mà không cắt được khoá
hay dead tuple nào đáng kể.

Tách khỏi migration để test được; xem ghi chú đánh đổi ở
`apps/inventory/application/services/occurred_at_backfill.py`.
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# MẢNH FROM/WHERE DÙNG CHUNG cho cả hai chế độ.
#
# BẮT BUỘC: chế độ rà (SELECT) và chế độ nắn (UPDATE) phải LUÔN LUÔN cho ra
# CÙNG MỘT tập ứng viên — nếu không, thứ người vận hành duyệt sẽ khác thứ thực
# sự bị đè, và đó đúng là rủi ro mà việc tách chế độ sinh ra để chặn. Vì thế
# mệnh đề FROM/WHERE chỉ được viết MỘT LẦN ở đây và nội suy vào cả hai câu lệnh;
# đừng bao giờ sửa riêng một bên.
#
# TIME_ZONE = "Asia/Bangkok" (config/settings_parts/base.py) — phải khớp với
# `timezone.localdate()` bên Python, nếu không sẽ lệch đúng 1 ngày quanh nửa
# đêm: Django ghim session DB ở UTC khi USE_TZ=True, `::date` trần sẽ cắt theo
# UTC và phân loại sai mọi lần khớp từ 00:00 tới 07:00 giờ VN.
#
# JOIN LATERAL thay vì `UPDATE ... FROM` trần: một hành trình có thể có NHIỀU
# bản ghi quét đã khớp cùng `result_ref`. `uniq_pending_scan_code_kind` chỉ là
# unique index PARTIAL (`condition=Q(status="pending")`), nên quét lại một mã
# sau khi dòng đầu đã sang `matched` sinh thêm một dòng matched nữa cùng mã.
# Nếu cả hai cùng được khớp trong một ngày (cùng thoả vế chặn) thì câu lệnh
# trần để Postgres nhặt dòng nào tuỳ ý ⇒ ngày nắn ra không tất định.
#
# Lấy lần quét SỚM NHẤT — ngược với `occurred_at_backfill` (lấy MỚI NHẤT không
# sau thời điểm ghi sổ). Ở đây ngược lại là ĐÚNG vì cron chỉ ghi
# `warehouse_received_at` khi trường còn TRỐNG (`mark_returned_by_tracking`):
# lần khớp ĐẦU TIÊN là lần duy nhất thực sự ghi ngày, mọi lần quét lại sau đó
# đều là no-op với trường này. `ORDER BY s.created_at ASC, s.id ASC` giữ tính
# tất định khi hai bản ghi trùng mốc thời gian.
#
# Tự nối lại `warranty_journeys` (alias `jj`) thay vì LATERAL thẳng vào `j`:
# Postgres không cho FROM-item của UPDATE tham chiếu bảng đích.
# ---------------------------------------------------------------------------
_CANDIDATES_FROM_WHERE = """
    FROM warranty_journeys jj
    JOIN LATERAL (
        SELECT (s.created_at AT TIME ZONE 'Asia/Bangkok')::date AS scan_date,
               (s.updated_at AT TIME ZONE 'Asia/Bangkok')::date AS matched_date
        FROM pending_shipment_scans s
        WHERE s.kind = 'return'
          AND s.status = 'matched'
          AND s.result_ref = 'warranty-journey:' || jj.id
        ORDER BY s.created_at ASC, s.id ASC
        LIMIT 1
    ) sx ON TRUE
    WHERE jj.warehouse_received_at = sx.matched_date
      AND jj.warehouse_received_at <> sx.scan_date
"""

# Chế độ RÀ: đúng những dòng mà `_UPDATE_SQL` sẽ đụng, cùng giá trị đề xuất, và
# KHÔNG ghi gì. `ORDER BY jj.id` chỉ để bản in ra cho người đọc được tất định.
_SELECT_SQL = f"""
SELECT jj.id,
       jj.warehouse_received_at AS old_received_at,
       sx.scan_date AS new_received_at
{_CANDIDATES_FROM_WHERE}
ORDER BY jj.id;
"""

# Chế độ NẮN. RETURNING id + giá trị CŨ + giá trị MỚI: đây là lần ghi đè dữ liệu
# người dùng sửa được, mà chiều ngược của migration là noop ⇒ không còn dấu vết
# nào khác.
_UPDATE_SQL = f"""
UPDATE warranty_journeys j
SET warehouse_received_at = x.scan_date
FROM (
    SELECT jj.id AS journey_id,
           jj.warehouse_received_at AS old_received_at,
           sx.scan_date
    {_CANDIDATES_FROM_WHERE}
) x
WHERE j.id = x.journey_id
RETURNING j.id, x.old_received_at, j.warehouse_received_at;
"""


def backfill_warehouse_received_at(
    connection, *, apply: bool = False
) -> list[tuple[int, date, date]]:
    """Rà (mặc định) hoặc nắn ngày kho nhận, trả về NHẬT KÝ từng dòng.

    Mỗi phần tử là `(journey_id, ngày_cũ, ngày_mới)`; `len(...)` chính là số
    hành trình liên quan.

    `apply=False` (mặc định): chỉ SELECT, KHÔNG ghi một dòng nào — dùng cho
    migration 0016 và cho lần chạy rà của người vận hành. Tập dòng trả về đúng
    bằng tập dòng mà `apply=True` sẽ đè (chung `_CANDIDATES_FROM_WHERE`).

    `apply=True`: thực sự UPDATE. Trả cả giá trị cũ chứ không chỉ đếm là có chủ
    đích: xem docstring module — log này là đường về duy nhất cho một dòng bị
    nắn oan.

    Chạy `apply=True` lần hai không nắn gì thêm: sau lần đầu
    `warehouse_received_at = scan_date`, mà vế chặn đòi đồng thời
    `= matched_date` và `<> scan_date` ⇒ không dòng nào thoả.
    """
    with connection.cursor() as cur:
        cur.execute(_UPDATE_SQL if apply else _SELECT_SQL)
        return cur.fetchall()
