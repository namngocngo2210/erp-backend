"""1 dòng/hội thoại chat. Nguồn: foreign table ai_stg.chat_* + nhãn LLM ở
ai_stg.hoi_thoai_nhan (spec C3, C8).

Dùng LATERAL thay vì CTE gộp: upsert tăng dần chỉ chạm đúng hội thoại trong
danh sách id, đi bằng index idx_messages_conv_inserted của nguồn. CTE gộp sẽ
quét toàn bộ 769k tin mỗi nhịp 30' — đúng cái nợ spec §9 ghi là ngưỡng đổi ý.

Aggregate-only LATERAL luôn trả đúng 1 dòng, nên hội thoại rỗng vẫn còn trong
mart với so_tin = 0 (coalesce), KHÔNG bị rơi mất.

`ai_mart_verify` KHÔNG phủ được bảng này, và không thể phủ. Phép dò chuẩn của
lệnh đó là "<x>_ma IS NOT NULL AND <x>_text IS NULL" (có mã mà không tra được
nhãn = từ điển thiếu mã). Ở đây mọi cột *_text đều dựng bằng
`coalesce(ai.nhan(...), x.ma)`: mã mồ côi hiện NGUYÊN VĂN mã chứ không bao giờ
ra NULL, nên phép dò luôn trả 0 dòng dù từ điển thiếu bao nhiêu mã. Việc canh mã
mồ côi của hai bảng chat nằm ở tests/test_chat_contract.py
(test_every_need_and_reason_code_resolves_in_the_dictionary), hỏi thẳng
`ai.nhan()` cho từng mã một. ĐỪNG suy ra "ai_mart_verify xanh = từ điển đủ mã".
"""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.common.order_scope import sql_exclude_mirror_orders

# Nhánh hoi_thoai_nhan CỐ Ý loại `pending`. `enqueue_pending()` ghi
# `updated_at = now()` lên MỌI dòng nó xếp hàng — lượt backfill đầu là 60.435 dòng
# cùng lúc — nên nếu không lọc, lượt sync kế tiếp phải upsert lại TOÀN BỘ mart.
# Đo thật trên prod 05/09/2026: 60.435 dòng, 587s. Beat chỉ có statement_timeout(180)
# → bị giết, watermark KHÔNG tiến, lượt sau lặp lại y hệt: kẹt vĩnh viễn.
# Đây đúng ngưỡng đổi ý spec §9 đã dự liệu.
#
# Điều kiện loại đúng dòng VỪA XẾP HÀNG LẦN ĐẦU (`pending` + chưa từng có lỗi):
# nó không mang gì mới cho mart ngoài trang_thai_gan_nhan_text đổi "Chưa xếp hàng"
# -> "Chờ gán nhãn"; rebuild đêm nhặt nốt. Dòng `pending` do _save_error trả về sau
# một lượt hỏng thì extract_error KHÁC rỗng nên VẪN lên mart — không được để mất,
# đó là tín hiệu vận hành. done/failed/skipped cũng vẫn lên ngay ở nhịp 30 phút.
_CANDIDATES = """
SELECT c.id FROM ai_stg.chat_conversations c WHERE c.updated_at > %(cutoff)s
UNION
SELECT n.hoi_thoai_id FROM ai_stg.hoi_thoai_nhan n
 WHERE n.updated_at > %(cutoff)s
   AND NOT (n.extract_status = 'pending' AND n.extract_error = '')
"""

# Cùng scope đơn với ai.don_hang: đơn xoá mềm / đơn mirror / đơn gốc đã có bản
# khôi phục đều không được coi là đơn thật.
_ORDER_SCOPE = (
    f"o.deleted_at IS NULL AND {sql_exclude_mirror_orders('o')}"
    " AND NOT EXISTS (SELECT 1 FROM orders r"
    "                 WHERE r.restored_from_id = o.id AND r.deleted_at IS NULL)"
)


# Luật 6 (che PII): ba cột noi_dung/tom_tat_ai/tin_nhan.noi_dung là ngoại lệ ĐÃ
# KHAI BÁO duy nhất. Mọi cột khác phải che SĐT — kể cả cột "text tự do" tưởng vô
# hại: nhân viên Pancake thường đổi TÊN hồ sơ khách thành chính số điện thoại,
# và đặt tên thẻ theo số.
#
# KHÔNG dùng ai.che_sdt() ở đây: hàm đó che TOÀN BỘ chuỗi (left 4 + '***' +
# right 3) — đúng cho cột chỉ chứa SĐT (sdt_che) nhưng sẽ bôi nát một cái tên
# thật. Thay TẠI CHỖ, giữ nguyên phần còn lại. Dạng đầu ra khớp đúng ai.che_sdt:
# '0912345678' -> '0912***678'.
_SDT_PATTERN = r"(0[0-9]{3})[0-9]{3}([0-9]{3})"


def _che_sdt_nhung(expr: str) -> str:
    return f"regexp_replace({expr}, '{_SDT_PATTERN}', '\\1***\\2', 'g')"


_KHACH_TEN = _che_sdt_nhung("NULLIF(btrim(coalesce(cu.name, '')), '')")
_TAG_TEN = _che_sdt_nhung("t.name")


def _json_array(path: str) -> str:
    """jsonb -> mảng text an toàn: payload hỏng/không phải array thì thành []."""
    return (
        f"CASE jsonb_typeof(n.extract_payload #> '{{clean,{path}}}')"
        f" WHEN 'array' THEN n.extract_payload #> '{{clean,{path}}}'"
        f" ELSE '[]'::jsonb END"
    )


def _json_text(path: str) -> str:
    return f"NULLIF(btrim(coalesce(n.extract_payload #>> '{{clean,{path}}}', '')), '')"


def _select(where: str) -> str:
    return f"""
SELECT
    c.id AS hoi_thoai_id,
    NULLIF(btrim(coalesce(c.pancake_conversation_id, '')), '') AS pancake_hoi_thoai_id,
    NULLIF(btrim(coalesce(p.name, '')), '') AS page_ten,
    ai.nhan('nen_tang_chat', p.platform) AS nen_tang_text,
    a.bat_dau,
    (a.bat_dau AT TIME ZONE '{BUSINESS_TZ}')::date AS bat_dau_ngay,
    a.ket_thuc,
    (a.ket_thuc AT TIME ZONE '{BUSINESS_TZ}')::date AS ket_thuc_ngay,
    coalesce(a.so_tin, 0) AS so_tin,
    coalesce(a.so_tin_khach, 0) AS so_tin_khach,
    coalesce(a.so_tin_page, 0) AS so_tin_page,
    coalesce(a.so_tin_khach, 0) > 0 AS co_khach_tra_loi,
    ai.nhan('loai_tin_chat', c.type) AS loai_kenh_text,
    coalesce(g.do_bot_xu_ly, false) AS do_bot_xu_ly,
    coalesce(g.nhan_tag, '{{}}') AS nhan_tag,
    NULLIF(array_to_string(coalesce(g.nhan_tag, '{{}}'), ', '), '') AS nhan_tag_text,
    a.noi_dung,
    {_KHACH_TEN} AS khach_ten,
    ai.che_sdt(NULLIF(btrim(coalesce(cu.phone, '')), '')) AS sdt_che,
    {_json_text('tom_tat')} AS tom_tat_ai,
    nc.ma AS nhu_cau_ai,
    nc.nhan AS nhu_cau_ai_text,
    nc.nhom AS nhom_nhu_cau_ai,
    ld.ma AS ly_do_khong_mua_ma,
    ld.nhan AS ly_do_khong_mua_text,
    coalesce(ARRAY(SELECT jsonb_array_elements_text({_json_array('san_pham_quan_tam')})),
             '{{}}') AS san_pham_quan_tam,
    ai.nhan('giai_doan_chat', {_json_text('giai_doan')}) AS giai_doan_text,
    ai.nhan('muc_do_quan_tam', {_json_text('muc_do_quan_tam')}) AS muc_do_quan_tam_text,
    don.khach_id,
    don.don_id,
    don.ma_don,
    ai.nhan('trang_thai_gan_nhan', coalesce(n.extract_status, 'chua_xep_hang'))
        AS trang_thai_gan_nhan_text,
    CASE WHEN n.extract_status = 'done' THEN n.updated_at END AS gan_nhan_luc,
    now() AS dong_bo_luc
FROM ai_stg.chat_conversations c
LEFT JOIN ai_stg.chat_pages p ON p.id = c.page_id
LEFT JOIN ai_stg.chat_customers cu ON cu.id = c.customer_id
LEFT JOIN ai_stg.hoi_thoai_nhan n ON n.hoi_thoai_id = c.id
LEFT JOIN LATERAL (
    SELECT
        min(m.inserted_at) AS bat_dau,
        max(m.inserted_at) AS ket_thuc,
        count(*)::int AS so_tin,
        count(*) FILTER (WHERE m.direction = 'in')::int AS so_tin_khach,
        count(*) FILTER (WHERE m.direction = 'out')::int AS so_tin_page,
        string_agg(
            CASE WHEN m.direction = 'in' THEN 'Khách: ' ELSE 'Page: ' END || btrim(m.content),
            E'\\n' ORDER BY m.inserted_at, m.id
        ) AS noi_dung
    FROM ai_stg.chat_messages m
    WHERE m.conversation_id = c.id
      AND NULLIF(btrim(coalesce(m.content, '')), '') IS NOT NULL
) a ON TRUE
LEFT JOIN LATERAL (
    SELECT
        -- Che ngay tại đây: nhan_tag_text ở ngoài dựng từ chính mảng này,
        -- nên một chỗ che là cả hai cột đều sạch. ORDER BY và bool_or vẫn
        -- đọc t.name THÔ — thứ tự thẻ và cờ bot không đổi theo việc che số.
        array_agg({_TAG_TEN} ORDER BY t.name) AS nhan_tag,
        bool_or(t.name ~ '^(BOT-|TEST )') AS do_bot_xu_ly
    FROM ai_stg.chat_conversation_tags ct
    JOIN ai_stg.chat_tags t ON t.id = ct.tag_id
    WHERE ct.conversation_id = c.id
) g ON TRUE
LEFT JOIN LATERAL (
    SELECT
        coalesce(array_agg(x.ma ORDER BY x.ma), '{{}}') AS ma,
        NULLIF(string_agg(coalesce(ai.nhan('nhu_cau', x.ma), x.ma), ', ' ORDER BY x.ma), '')
            AS nhan,
        coalesce(array_agg(DISTINCT nt.group_code)
                 FILTER (WHERE nt.group_code IS NOT NULL), '{{}}') AS nhom
    FROM jsonb_array_elements_text({_json_array('nhu_cau')}) AS x(ma)
    LEFT JOIN (
        SELECT t2.code, ng.code AS group_code
        FROM need_types t2 JOIN need_groups ng ON ng.id = t2.group_id
    ) nt ON nt.code = x.ma
) nc ON TRUE
LEFT JOIN LATERAL (
    SELECT
        coalesce(array_agg(y.ma ORDER BY y.ma), '{{}}') AS ma,
        NULLIF(string_agg(coalesce(ai.nhan('ly_do_khong_chot', y.ma), y.ma), ', '
                          ORDER BY y.ma), '') AS nhan
    FROM jsonb_array_elements_text({_json_array('ly_do_khong_mua')}) AS y(ma)
) ld ON TRUE
LEFT JOIN LATERAL (
    SELECT o.id AS don_id,
           NULLIF(btrim(coalesce(o.order_code, '')), '') AS ma_don,
           o.customer_id AS khach_id
    FROM orders o
    JOIN customers kh ON kh.id = o.customer_id
    WHERE kh.phone_normalized = {_json_text('sdt_khach')}
      AND {_ORDER_SCOPE}
    ORDER BY o.created_at DESC, o.id DESC
    LIMIT 1
) don ON TRUE
WHERE {where}
"""


MART = MartTable(
    name="ai.hoi_thoai",
    key_column="hoi_thoai_id",
    source_key="c.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
