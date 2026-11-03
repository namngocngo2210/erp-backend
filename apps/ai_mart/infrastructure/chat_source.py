"""Mô tả 6 bảng nguồn bên DB `crawler` — MỘT nguồn sinh ra CẢ HAI dạng DDL.

Prod: foreign table qua postgres_fdw (spec C1). Test/dev: bảng local cùng tên
cùng cột, vì foreign table không dựng được trong DB test (cần superuser + một
DB crawler thật). Cột khai ở đây phải khớp DDL bên desi-crawl-conversations —
đo bằng information_schema ngày 04/09/2026.

Bảng messages bên nguồn partition theo tháng với PK (id, inserted_at); ở đây
chỉ cần đọc nên khai id là bigint thường, KHÔNG khai PRIMARY KEY (foreign
table không có ràng buộc, bảng local phải giống để test không dễ dãi hơn prod).
"""

SERVER_NAME = "chat_crawler"
STG_SCHEMA = "ai_stg"

# tên local (ai_stg) -> tên thật (public bên DB crawler)
SOURCE_NAMES: dict[str, str] = {
    "chat_pages": "pages",
    "chat_conversations": "conversations",
    "chat_customers": "customers",
    "chat_messages": "messages",
    "chat_tags": "tags",
    "chat_conversation_tags": "conversation_tags",
}

# Cột `raw jsonb` của nguồn CỐ Ý bị bỏ: đo được 0/751.350 dòng có dữ liệu (spec
# F1). Kéo một cột toàn NULL qua FDW chỉ tốn băng thông.
CHAT_TABLES: dict[str, str] = {
    "chat_pages": """
    id bigint,
    pancake_page_id text,
    platform text,
    name text,
    status text,
    last_synced_at timestamptz""",
    "chat_conversations": """
    id bigint,
    page_id bigint,
    pancake_conversation_id text,
    customer_id bigint,
    type text,
    snippet text,
    message_count integer,
    last_message_at timestamptz,
    updated_at timestamptz,
    created_at timestamptz""",
    "chat_customers": """
    id bigint,
    page_id bigint,
    pancake_customer_id text,
    name text,
    phone text,
    fb_id text,
    gender text""",
    "chat_messages": """
    id bigint,
    page_id bigint,
    conversation_id bigint,
    pancake_message_id text,
    sender_id text,
    direction text,
    type text,
    content text,
    attachments jsonb,
    inserted_at timestamptz""",
    "chat_tags": """
    id bigint,
    page_id bigint,
    pancake_tag_id text,
    name text,
    color text""",
    "chat_conversation_tags": """
    conversation_id bigint,
    tag_id bigint""",
}

# customers.email CỐ Ý không kéo qua: schema ai cấm cột email (luật 6), và
# ai_stg là nơi dữ liệu dễ vô tình chảy tiếp vào mart.


def create_local_tables_sql() -> str:
    return "\n".join(
        f"CREATE TABLE IF NOT EXISTS {STG_SCHEMA}.{table} ({body}\n);"
        for table, body in CHAT_TABLES.items()
    )


# postgres_fdw mặc định fetch_size = 100 dòng/vòng: một lượt quét 769k tin
# nhắn qua cầu là ~7.700 round trip mạng, và rebuild đêm (_select("TRUE")) quét
# TOÀN BỘ nguồn. 10.000 cắt con số đó xuống ~77 round trip, đổi lại vài MB bộ
# nhớ mỗi cursor phía desi-postgres — rẻ hơn nhiều so với ngân sách 1500s của
# ai_mart_rebuild (soft_time_limit trong infrastructure/tasks.py).
# CHỈ áp cho foreign table: bảng local (test/dev) không có OPTIONS, và OPTIONS
# nằm SAU danh sách cột nên không đụng vào hợp đồng "hai DDL cùng cột" mà
# tests/test_chat_source.py canh.
FETCH_SIZE = "10000"


def create_foreign_tables_sql(server: str = SERVER_NAME) -> str:
    return "\n".join(
        f"CREATE FOREIGN TABLE {STG_SCHEMA}.{table} ({body}\n)"
        f" SERVER {server} OPTIONS (schema_name 'public',"
        f" table_name '{SOURCE_NAMES[table]}', fetch_size '{FETCH_SIZE}');"
        for table, body in CHAT_TABLES.items()
    )


def drop_tables_sql() -> str:
    """DROP bảng nguồn dù nó đang là bảng local hay foreign table.

    KHÔNG dùng `DROP FOREIGN TABLE IF EXISTS` rồi `DROP TABLE IF EXISTS`: `IF
    EXISTS` chỉ bỏ qua trường hợp KHÔNG TỒN TẠI; gặp đúng tên mà SAI KIỂU object
    thì Postgres vẫn nổ ("chat_pages" is not a foreign table). Mà đó CHÍNH LÀ
    trạng thái sau migration 0015 (bảng local), nên `ai_mart_chat_fdw` chết ngay ở
    lần chạy đầu trên môi trường mới — đo thật trên prod 05/09/2026.

    Đọc relkind rồi phát đúng câu DROP là cách chạy được cho CẢ HAI chiều: lần đầu
    (local -> foreign) và các lần sau (foreign -> foreign).
    """
    names = ", ".join(f"'{table}'" for table in CHAT_TABLES)
    return f"""
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{STG_SCHEMA}' AND c.relname IN ({names})
    LOOP
        IF r.relkind = 'f' THEN
            EXECUTE format('DROP FOREIGN TABLE %I.%I', '{STG_SCHEMA}', r.relname);
        ELSE
            EXECUTE format('DROP TABLE %I.%I', '{STG_SCHEMA}', r.relname);
        END IF;
    END LOOP;
END $$;"""
