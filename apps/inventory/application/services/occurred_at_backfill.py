"""Nắn `occurred_at` cho bút toán đã ghi trước khi có cột ngày nghiệp vụ.

Tách khỏi file migration để CÓ THỂ TEST được — migration chỉ gọi vào đây. Đánh
đổi đã biết: nếu module này đổi về sau, migration 0027 chạy lại trên DB mới sẽ
chạy phiên bản mới. Chấp nhận được vì đây là thao tác một lần, idempotent, và
mọi câu lệnh đều tự bảo vệ bằng điều kiện `scanned_at < occurred_at`.

Set-based NHƯNG CHIA LÔ theo dải id (`_BATCH`), y hệt migration 0025 trên chính
bảng này. Không câu lệnh nào prune được partition (điều kiện lọc nằm trên
`reference_id`, không phải `created_at` — partition key), nên mỗi UPDATE là một
lượt quét toàn sổ: chạy một phát sẽ giữ khoá hàng và tích dead tuple tới lúc
commit. Chia lô cắt được cả hai. Ngắt giữa chừng vẫn chạy lại được vì điều kiện
`scanned_at < occurred_at` khiến lô đã xong thành no-op.

NGUYÊN TẮC 1 — bút toán ĐIỀU CHỈNH/ĐẢO không bao giờ được nắn ngày. Huỷ hay
điều chỉnh số lượng là một sự kiện nghiệp vụ xảy ra tại lúc nó được ghi, không
phải tại lúc hàng gốc được quét — dời nó về ngày quét là xoá mất một sự kiện có
thật khỏi đúng ngày của nó. CẢ HAI nhánh đều lọc bằng `m.note = ''`: đây là
cách duy nhất tách được bút toán gốc khỏi bút toán hành chính, vì chúng dùng
chung movement_type và chung reference_type/reference_id (xem comment từng câu).

NGUYÊN TẮC 2 — mỗi bút toán phải ghép với ĐÚNG lần quét sinh ra nó. Một mục
tiêu (đơn hàng / nhóm đơn hoàn) có thể có NHIỀU lần quét đã khớp: huỷ rồi quét
lại là chuyện thường ngày. Gộp `MIN(created_at)` theo mục tiêu là tất định
nhưng SAI — nó kéo cả bút toán do lần quét sau sinh ra về lần quét đầu tiên,
tức là gán sai doanh số/lượng hoàn sang ngày khác, đúng thứ mà migration này
sinh ra để sửa. Vì vậy dùng JOIN LATERAL lấy lần quét MỚI NHẤT mà KHÔNG sau
thời điểm ghi sổ của chính dòng đó; `ORDER BY s.created_at DESC, s.id DESC
LIMIT 1` giữ tính tất định khi trùng mốc thời gian.

Cố tình KHÔNG dùng `stock_return_entries.scan_batch_id` để ghép: cột đó do
migration 0018 thêm vào, mọi nhóm đơn trước 0018 đều NULL và sẽ bị bỏ sót —
đúng tập dữ liệu cũ mà migration này cần sửa nhất.

HẠN CHẾ ĐÃ BIẾT, KHÔNG SỬA ĐƯỢC bằng dữ liệu hiện có (nhánh dispatch):
`uniq_pending_scan_code_kind` chỉ là unique index PARTIAL, chặn hai lần quét
PENDING cùng MÃ — không chặn một đơn bị dán lại mã khác rồi phát sinh hai lần
quét PENDING khác mã cho cùng đơn (ngày D và D+1). Một cron chạy ở ngày D+2
khớp cả hai: lần quét D tạo dòng SALE thật, lần quét D+1 là no-op tất định
(hàng đã trừ kho) nhưng vẫn được set `matched`. LATERAL sau đó ghép dòng SALE
đó với lần quét MỚI NHẤT không sau ngày ghi sổ — tức là quét D+1 — trong khi
`MIN(created_at)` kiểu cũ lẽ ra chọn D. Không có cột nào nối một `stock_movement`
với ĐÚNG lần quét đã sinh ra nó (không giống nhánh return còn có `shipping_code`
độc lập), nên không tài nào phân biệt được hai lần quét PENDING nào mới là lần
"thật sự" tạo ra dòng SALE — chỉ có thể suy luận qua thời gian, không phải qua
liên kết dữ liệu. Sai số vì vậy bị CHẶN TRONG khoảng cách giữa hai lần quét của
CHÍNH đơn đó (ở đây là 1 ngày), không lan sang đơn khác hay ngày khác xa hơn.

BACKFILL LÀ TẬP CHA CỦA LUỒNG XUÔI, KHÔNG PHẢI CÙNG MỘT HÀM. Luồng xuôi chỉ set
`occurred_at` cho bút toán do CHÍNH lần quét đó sinh ra. Hai câu lệnh dưới đây
lại ghép theo BẤT KỲ lần quét đã matched nào của mục tiêu (đơn hàng ở nhánh
dispatch, `shipping_code` ở nhánh hoàn), nên còn nắn cả những dòng mà luồng xuôi
không bao giờ đụng tới: đơn xuất tay trong UI Đơn hàng rồi VỀ SAU mới có lần
quét khớp, hay nhóm đơn hoàn tạo tay có `shipping_code` trùng một lần quét
(`_complete_return` short-circuit khi entry đã tồn tại, nên lần quét đó không hề
tạo bút toán). Hướng nắn vẫn bảo vệ được về nghiệp vụ — hàng rời/về kho tại thời
điểm được quét, bất kể ai bấm nút ghi sổ — nhưng phải biết rằng chạy lại
backfill KHÔNG cho ra cùng kết quả với việc để luồng xuôi tự chạy.
"""
from __future__ import annotations

# Cùng cỡ lô với 0025 trên chính bảng này — giữ một chuẩn duy nhất cho ledger.
_BATCH = 50_000

# Nhánh xuất kho: bút toán trỏ thẳng về đơn, không có bảng trung gian để nối.
#
# `m.note = ''` là điều kiện SỐNG CÒN, không phải cho gọn: ba tiến trình hành
# chính (`reissue_after_cancel_release`, `resync_for_order`,
# `transfer_issue_to_sibling`) đều ghi movement_type='sale', reference_type=
# 'order', CÙNG reference_id với lần xuất thật — chỉ `note` khác rỗng phân biệt
# được. `stock_sales_service.ADMIN_SALE_NOTES` cũng nhận diện đúng ba
# note này và ghi rõ chúng KHÔNG phải sự kiện "hàng rời kho". Lần xuất thật
# (`issue_for_order`) không truyền note nên luôn `note = ''`.
# Không lọc note thì đơn quét ngày D, huỷ ngày D+10, hồi sinh ngày D+20 sẽ có
# HAI dòng -1 cùng dồn về ngày D: báo cáo ngày D thấy lượng xuất gấp đôi số
# hàng từng rời kho, còn ngày D+20 mất trắng sự kiện.
#
# Phải tự nối lại `stock_movements` (alias `mm`) trong FROM thay vì LATERAL
# thẳng vào `m`: Postgres không cho FROM-item của UPDATE tham chiếu bảng đích
# ("invalid reference to FROM-clause entry"). Dải id được kẹp ở CẢ hai tầng để
# lô nào cũng chỉ đụng đúng dải đó.
_DISPATCH_SQL = """
UPDATE stock_movements m
SET occurred_at = x.scanned_at
FROM (
    SELECT mm.id AS movement_id, sx.scanned_at
    FROM stock_movements mm
    JOIN LATERAL (
        SELECT s.created_at AS scanned_at
        FROM pending_shipment_scans s
        WHERE s.kind = 'dispatch'
          AND s.status = 'matched'
          AND s.matched_order_id = mm.reference_id
          AND s.created_at <= mm.created_at
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 1
    ) sx ON TRUE
    WHERE mm.reference_type = 'order'
      AND mm.movement_type = 'sale'
      AND mm.note = ''
      AND sx.scanned_at < mm.occurred_at
      AND mm.id >= %(low)s AND mm.id < %(high)s
) x
WHERE m.id = x.movement_id
  AND m.id >= %(low)s AND m.id < %(high)s;
"""

# Nhánh hoàn: bút toán treo ở ReturnEntry, nối về mã quét qua `shipping_code`.
#
# Không có movement_type riêng để phân biệt bút toán GỐC (post_entry) với bút
# toán ĐẢO (cancel_entry) hay ĐIỀU CHỈNH (return_entry_adjust_service) — cả 3
# đều dùng chung movement_type=RETURN_IN, cùng reference_type/reference_id.
# Phải chặn bằng 2 điều kiện độc lập, cùng trỏ về đúng một tập bút toán gốc:
#   - m.note = ''            post_entry gọi record_movement KHÔNG truyền note
#                            (mặc định ''); cancel_entry và adjust_entry đều
#                            truyền note khác rỗng ("Huỷ nhóm đơn #...",
#                            "Điều chỉnh nhóm đơn #..."). Cùng cách dùng note
#                            để nhận diện bút toán hành chính như
#                            `ADMIN_SALE_NOTES` trong stock_sales_service.
#   - m.qty_delta > 0        dòng hoàn gốc luôn dương (ReturnNoteLine.quantity
#                            có ràng buộc >= 1); bút toán đảo/hạ số lượng luôn
#                            âm. Hai điều kiện độc lập, an toàn kép nếu một
#                            trong hai bị phá vỡ do thay đổi sau này.
#
# Một `shipping_code` có thể ứng với NHIỀU nhóm đơn: unique constraint
# `returnentry_shipcode_draft_uniq` chỉ ép trên entry draft không có
# source_order, còn chốt chặn trùng của `_complete_return` bỏ qua entry đã
# CANCELLED — huỷ rồi quét lại chắc chắn sinh ra hai nhóm đơn cùng mã (lệnh
# `find_duplicate_return_notes` ghi nhận đây là tình huống prod có thật). Vì
# thế LATERAL ghép mỗi nhóm đơn với lần quét gần nhất KHÔNG sau ngày tạo của
# chính nó, thay vì lấy lần quét sớm nhất cho mọi nhóm đơn chung mã.
# Ở đây `e` nằm sẵn trong FROM nên LATERAL tham chiếu `e` là hợp lệ, không cần
# tự nối `stock_movements` như nhánh dispatch.
_RETURN_SQL = """
UPDATE stock_movements m
SET occurred_at = x.scanned_at
FROM stock_return_entries e
JOIN LATERAL (
    SELECT s.created_at AS scanned_at
    FROM pending_shipment_scans s
    WHERE s.kind = 'return'
      AND s.status = 'matched'
      AND s.code = e.shipping_code
      AND s.code <> ''
      AND s.created_at <= e.created_at
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1
) x ON TRUE
WHERE m.reference_type = 'return_entry'
  AND m.reference_id = e.id
  AND m.note = ''
  AND m.qty_delta > 0
  AND x.scanned_at < m.occurred_at
  AND m.id >= %(low)s AND m.id < %(high)s;
"""


def _run_batched(cur, sql: str) -> int:
    """Chạy `sql` theo từng dải id của stock_movements, trả tổng số dòng đã nắn.

    Dải id lấy một lần trước vòng lặp: bảng là append-only nên dòng mới chỉ
    xuất hiện ở cuối dải, mà dòng mới thì đã có `occurred_at` đúng ngay từ lúc
    ghi — không cần nắn.
    """
    cur.execute("SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), -1) FROM stock_movements;")
    low, high = cur.fetchone()
    total = 0
    while low <= high:
        cur.execute(sql, {"low": low, "high": low + _BATCH})
        total += cur.rowcount
        low += _BATCH
    return total


def backfill_from_pending_scans(connection) -> dict:
    """Trả số dòng đã nắn theo từng nhánh.

    Bút toán điều chỉnh/đảo/hành chính không bao giờ bị đụng ở cả 2 nhánh: cả
    hai đều lọc `note = ''`, nhánh return chặn thêm bằng `qty_delta > 0`.

    Bất biến `occurred_at <= created_at` được giữ miễn phí: chỉ nắn khi
    `scanned_at < occurred_at`, mà `occurred_at <= created_at` đã đúng từ trước
    ⇒ giá trị mới luôn nhỏ hơn `created_at`.

    `occurred_at` KHÔNG phải partition key nên UPDATE không làm dòng nhảy
    partition — đó chính là lý do ta không sửa `created_at`.
    """
    with connection.cursor() as cur:
        return {
            "dispatch": _run_batched(cur, _DISPATCH_SQL),
            "return": _run_batched(cur, _RETURN_SQL),
        }
