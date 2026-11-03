# Kho hội thoại chat vào schema `ai` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đưa 769.234 tin nhắn / 94.902 hội thoại Pancake mà `desi-crawl-conversations` đã crawl vào schema `ai` của DB `desi`, gán nhãn **nhu cầu** và **lý do khách không mua** bằng Gemini theo đúng bộ mã sale đang nhập tay, để trả lời hai câu hỏi đó bằng SQL thuần.

**Architecture:** Giữ 2 DB tách rời, bắc cầu bằng `postgres_fdw` — foreign table nằm trong `ai_stg` (`ai_stg.chat_*`), ETL đọc qua cầu rồi ghi vào hai bảng mart `ai.hoi_thoai` (grain hội thoại) và `ai.tin_nhan` (grain tin). Tái dùng nguyên khung `apps/ai_mart` đang chạy: `MartTable` + một câu SELECT dùng chung cho cả upsert tăng dần lẫn full rebuild, watermark `__hot__`, `ai_meta.nhat_ky_chay`, test contract/masking tự quét. Nhãn LLM sống ở `ai_stg.hoi_thoai_nhan` (FDW chỉ-đọc nên nhãn không thể ở phía crawler), giữ nguyên hình dạng `extract_payload = {raw, clean, rejected}` của `apps/callcenter`.

**Tech Stack:** Django 5 + psycopg 3, PostgreSQL 16 (`postgres_fdw`), Celery + Redis (`single_flight`, `statement_timeout`), google-genai (`settings.GEMINI_EXTRACT_MODEL`), `manage.py test` / `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-04-ai-chat-mart-design.md` — đọc kèm plan này. Quyết định C1–C8, luật chuẩn hoá §4, bộ test §8.

## Global Constraints

- **Nhánh:** `feat/ai-chat-mart`, cắt từ `master`.
- **Tuyệt đối không chạy test bằng `.env` gốc của repo** (`APP_ENV=production` → Postgres PROD). Mọi lệnh test trong plan này dùng khối env dưới đây, gọi tắt là **`$TESTENV`**:

  ```bash
  cd /home/desi/desi-order/backend
  docker start desi-test-pg desi-test-redis   # container hay bị Exited
  export POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
         POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
         REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
         APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text \
         ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
         ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
  ```

  Chạy test: `/home/desi/desi-order/.venv/bin/python manage.py test <path> -v2 --noinput`.
  **Sau mỗi migration mới phải dùng `--noinput` và xoá DB test cũ** (`manage.py test` tự tạo lại; với `pytest` thì `--create-db`), nếu không cột mới sẽ thiếu.
- **Lỗi phải thoát ra ngoài.** Bài học sự cố `statement timeout` 2026-07-22 và quyết định C2: nguồn FDW không với tới được thì job **raise**, không `try/except` nuốt rồi ghi `succeeded`. Khung `etl_service` hiện có đã đúng — không được thêm `except` mới quanh nó.
- **PII (luật 6):** chỉ `ai.hoi_thoai.noi_dung`, `ai.hoi_thoai.tom_tat_ai`, `ai.tin_nhan.noi_dung` là văn bản thô chưa che — ngoại lệ có chủ đích, `COMMENT` của ba cột này **bắt buộc** chứa câu "KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời". Mọi cột khác che SĐT bằng `ai.che_sdt()`. **Không** cột `email`, `dia_chi`, `address`, `phone_raw`, `sdt_goc` nào trong schema `ai` (`test_contract.test_no_forbidden_pii_columns` chặn).
- **COMMENT (luật 7):** bắt buộc cho **mọi bảng và mọi cột** trong schema `ai`; `test_contract` quét `pg_description` nên bảng mới tự động bị kiểm.
- **Múi giờ (luật 4):** mọi cột `*_ngay` là `(<ts> AT TIME ZONE 'Asia/Ho_Chi_Minh')::date`, hằng số lấy từ `apps.analytics.sql.tz.BUSINESS_TZ`.
- **Bộ mã đóng (C4):** nhu cầu ← `need_types`/`need_groups`; lý do không mua ← nhóm `ly_do_khong_chot` **đã có sẵn** trong `ai.tu_dien_ma` (14 mã) — không đẻ mã mới.
- **Không có cột nhân viên (C5).** Đo được 596k/596k tin outbound đều mang `sender_id = pancake_page_id`; bịa cột luôn NULL tệ hơn không có cột.
- **Mật khẩu chỉ qua env**, không hằng số trong code, không log. Env mới: `CHAT_CRAWLER_DB_*`.
- **Migration đánh số tiếp từ `0014`** (mới nhất trên `master` là `0013_don_hang_chi_nhanh_nhom_kenh`). DDL đi bằng `migrations.SeparateDatabaseAndState(database_operations=[migrations.RunSQL(...)])` — schema `ai` không có model Django.
- **Không sửa `ai.cuoc_goi`, `ai.don_hang`** hay bất kỳ mart đang chạy nào.
- **Commit sau mỗi task**, message tiếng Việt theo mẫu repo, kết thúc bằng:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01CTBEV7ixrYU2Jn9CJN17Gc
  ```

## Số đo nguồn (đo 2026-09-04 trên `deploy-postgres-1`, DB `crawler`)

Dùng các số này khi viết `COMMENT` và khi kiểm chứng kết quả:

| Bảng | Số dòng / phân bố |
|---|---|
| `messages` | 769.234 (id **duy nhất tuyệt đối** — 769.234 distinct, tuy PK là `(id, inserted_at)` vì bảng partition theo tháng) |
| `messages.type × direction` | `INBOX/out` 581.636 · `INBOX/in` 128.564 · `COMMENT/in` 30.799 · `COMMENT/out` 28.227 · `RATING/in` 8 |
| `messages` có đính kèm | 255.080 |
| `conversations` | 94.902 — `INBOX` 65.263 · `COMMENT` 28.693 · `RATING` 946 |
| `pages` | 37 — `facebook` 19 · `personal_zalo` 8 · `shopee_official` 6 · `tiktok_shop` 2 · `youtube` 1 · `zalo` 1 |
| `customers.phone` | chỉ 113/66.277 dòng có giá trị |
| `need_types` (DB `desi`) | 36 mã (29 `is_active`) — **nhiều hơn** `crm_catalog.NEED_TYPE_SEED` (29) |
| `need_groups` (DB `desi`) | 8 nhóm — nhiều hơn `NEED_GROUP_SEED` (5) |

**Khoá:** `conversations.pancake_conversation_id` chỉ duy nhất theo `(page_id, pancake_conversation_id)`, `pancake_message_id` chỉ duy nhất theo `(pancake_message_id, inserted_at)`. **Không dùng id Pancake làm PK của mart** — dùng `conversations.id` / `messages.id` (bigint, duy nhất toàn cục), giữ id Pancake ở cột phụ để tra ngược lên SPA.

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `backend/apps/ai_mart/domain/chat_dictionary.py` | 6 nhóm mã tĩnh của chat (không đụng DB) | 1 |
| `backend/apps/ai_mart/domain/need_dictionary.py` | Đồng bộ `nhu_cau`/`nhom_nhu_cau` từ bảng `need_types`/`need_groups` | 1 |
| `backend/apps/ai_mart/migrations/0014_chat_tu_dien.py` | Seed từ điển | 1 |
| `backend/apps/ai_mart/infrastructure/chat_source.py` | Một nguồn duy nhất mô tả 6 bảng nguồn; sinh DDL foreign **và** local | 2 |
| `backend/apps/ai_mart/management/commands/ai_mart_chat_fdw.py` | Dựng/kiểm cầu FDW trên prod (cần superuser → không nằm trong migration) | 2 |
| `backend/apps/ai_mart/migrations/0015_chat_stg_nguon.py` | Tạo bảng local `ai_stg.chat_*` (test/dev; prod bị command thay bằng foreign table) | 2 |
| `backend/apps/ai_mart/infrastructure/sql/chat_tin_nhan.py` | Transform `ai.tin_nhan` | 3 |
| `backend/apps/ai_mart/migrations/0016_tin_nhan.py` | DDL + COMMENT `ai.tin_nhan` | 3 |
| `backend/apps/ai_mart/migrations/0017_hoi_thoai.py` | DDL + COMMENT `ai.hoi_thoai` và `ai_stg.hoi_thoai_nhan` | 4 |
| `backend/apps/ai_mart/infrastructure/sql/chat_hoi_thoai.py` | Transform `ai.hoi_thoai` (gồm nhãn AI + nối đơn) | 4 |
| `backend/apps/ai_mart/application/etl_service.py` (sửa) | Thêm 2 mart vào `HOT_MARTS`, refresh từ điển nhu cầu trong `rebuild_all` | 5 |
| `backend/apps/ai_mart/domain/chat_extract.py` | Hợp đồng LLM: response schema, prompt catalog, `normalize_chat_extracted` | 6 |
| `backend/apps/ai_mart/infrastructure/chat_extract_runner.py` | Xếp hàng + gọi Gemini + ghi `ai_stg.hoi_thoai_nhan` | 7 |
| `backend/apps/ai_mart/management/commands/ai_mart_chat_extract.py` | Chạy tay / backfill theo lô | 7 |
| `backend/apps/ai_mart/infrastructure/tasks.py` (sửa) | Task Celery `extract_chat_labels` | 7 |
| `docs/runbooks/2026-09-04-ai-chat-mart-deploy.md` | Runbook triển khai | 8 |

---

### Task 1: Từ điển mã cho chat + nhu cầu

Spec §3.3. Không có từ điển thì mọi cột `*_text` của hai bảng mart sẽ NULL — đây là task nền, làm trước.

**Files:**
- Create: `backend/apps/ai_mart/domain/chat_dictionary.py`
- Create: `backend/apps/ai_mart/domain/need_dictionary.py`
- Create: `backend/apps/ai_mart/migrations/0014_chat_tu_dien.py`
- Modify: `backend/apps/ai_mart/domain/dictionary.py` (thêm 6 nhóm vào `DICTIONARY`)
- Test: `backend/apps/ai_mart/tests/test_chat_dictionary.py`

**Interfaces:**
- Consumes: `apps.ai_mart.domain.dictionary.DICTIONARY`, `dictionary_rows() -> list[tuple[str, str, str, int]]` (đã có).
- Produces:
  - `chat_dictionary.CHAT_DICTIONARY: dict[str, list[tuple[str, str]]]` — 6 nhóm: `nen_tang_chat`, `loai_tin_chat`, `chieu_tin_chat`, `giai_doan_chat`, `muc_do_quan_tam`, `trang_thai_gan_nhan`.
  - `need_dictionary.refresh_need_dictionary(cursor) -> int` — upsert `nhu_cau` + `nhom_nhu_cau` từ bảng `need_types`/`need_groups`, trả tổng số dòng ghi.
  - Task 4 tra nhãn bằng `ai.nhan('nen_tang_chat', …)`, `ai.nhan('giai_doan_chat', …)`, `ai.nhan('nhu_cau', …)`, `ai.nhan('trang_thai_gan_nhan', …)`; Task 6 dựng enum prompt từ `CHAT_DICTIONARY`.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_dictionary.py`:

```python
"""Từ điển chat: nhóm tĩnh seed đủ, nhóm nhu cầu bám theo bảng need_types.

need_types trên prod có 36 mã trong khi crm_catalog.NEED_TYPE_SEED chỉ có 29 —
seed tĩnh sẽ để 7 mã thành mồ côi (cột _ma có giá trị, _text NULL). Vì vậy
nhu_cau/nhom_nhu_cau PHẢI đọc từ bảng, không chép hằng số.
"""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.chat_dictionary import CHAT_DICTIONARY
from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary
from apps.orders.models import NeedGroup, NeedType


class ChatDictionaryTests(TestCase):
    def _labels(self, nhom):
        with connection.cursor() as cur:
            cur.execute("SELECT ma, nhan FROM ai.tu_dien_ma WHERE nhom_ma = %s", [nhom])
            return dict(cur.fetchall())

    def test_static_groups_seeded_by_migration(self):
        for nhom, items in CHAT_DICTIONARY.items():
            seeded = self._labels(nhom)
            for ma, nhan in items:
                self.assertEqual(seeded.get(ma), nhan, f"{nhom}.{ma} chưa seed")

    def test_platform_codes_cover_measured_source_values(self):
        # 6 platform đo được trên DB crawler ngày 04/09/2026
        seeded = self._labels("nen_tang_chat")
        for code in ("facebook", "zalo", "personal_zalo", "tiktok_shop",
                     "shopee_official", "youtube"):
            self.assertIn(code, seeded)

    def test_refresh_picks_up_need_type_added_after_migration(self):
        group = NeedGroup.objects.create(code="grp_moi", name_vi="Nhóm mới", sort_order=900)
        NeedType.objects.create(
            code="nhu_cau_moi", group=group, name_vi="Nhu cầu mới",
            sort_order=910, is_active=True,
        )
        with connection.cursor() as cur:
            refresh_need_dictionary(cur)
        self.assertEqual(self._labels("nhu_cau").get("nhu_cau_moi"), "Nhu cầu mới")
        self.assertEqual(self._labels("nhom_nhu_cau").get("grp_moi"), "Nhóm mới")

    def test_inactive_need_type_seeded_with_deprecation_note(self):
        group = NeedGroup.objects.create(code="grp_cu", name_vi="Nhóm cũ", sort_order=901)
        NeedType.objects.create(
            code="nhu_cau_cu", group=group, name_vi="Nhu cầu cũ",
            sort_order=911, is_active=False,
        )
        with connection.cursor() as cur:
            refresh_need_dictionary(cur)
            cur.execute(
                "SELECT mo_ta FROM ai.tu_dien_ma WHERE nhom_ma = 'nhu_cau' AND ma = 'nhu_cau_cu'"
            )
            (mo_ta,) = cur.fetchone()
        # mã ngừng dùng VẪN phải có nhãn (nhãn cũ trên mart không được thành mồ côi),
        # nhưng phải nói rõ là đã ngừng — nếu không người đọc tưởng vẫn dùng được.
        self.assertIn("ngừng dùng", (mo_ta or ""))
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
# $TESTENV (xem Global Constraints)
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_dictionary -v2 --noinput
```
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.ai_mart.domain.chat_dictionary'`.

- [ ] **Step 3: Viết `domain/chat_dictionary.py`**

```python
"""Nhóm mã TĨNH cho mart hội thoại chat — không nhóm nào lấy được từ crm_catalog
hay enum model, nên khai ở đây và đây là nguồn sự thật duy nhất.

nhu_cau / nhom_nhu_cau KHÔNG nằm ở đây: chúng có bảng thật (need_types,
need_groups) và phải bám theo bảng — xem need_dictionary.py.
"""

# Giá trị mã = ĐÚNG chuỗi thô của nguồn (pages.platform, conversations.type,
# messages.direction) để ai.nhan() tra thẳng, không cần bảng ánh xạ trung gian.
NEN_TANG_CHAT = [
    ("facebook", "Facebook"),
    ("zalo", "Zalo OA"),
    ("personal_zalo", "Zalo cá nhân"),
    ("tiktok_shop", "TikTok Shop"),
    ("shopee_official", "Shopee"),
    ("youtube", "YouTube"),
]

LOAI_TIN_CHAT = [
    ("INBOX", "Tin nhắn"),
    ("COMMENT", "Bình luận"),
    ("RATING", "Đánh giá"),
]

CHIEU_TIN_CHAT = [
    ("in", "Khách gửi"),
    ("out", "Page gửi"),
]

# Giai đoạn hội thoại — danh mục ĐÓNG, LLM chỉ được chọn trong đây (spec §3.1).
GIAI_DOAN_CHAT = [
    ("hoi_gia", "Hỏi giá"),
    ("hoi_thong_tin", "Hỏi thông tin sản phẩm"),
    ("dang_can_nhac", "Đang cân nhắc"),
    ("tu_choi", "Từ chối"),
    ("da_chot", "Đã chốt"),
    ("sau_ban", "Sau bán / bảo hành"),
    ("khong_xac_dinh", "Không xác định"),
]

MUC_DO_QUAN_TAM = [
    ("cao", "Cao"),
    ("trung_binh", "Trung bình"),
    ("thap", "Thấp"),
]

# Trạng thái hàng đợi gán nhãn (ai_stg.hoi_thoai_nhan.extract_status).
# "chua_xep_hang" = chưa có dòng nào trong hàng đợi — KHÁC "pending" (đã xếp
# hàng, đang chờ tới lượt). Không có mã này thì trang_thai_gan_nhan_text NULL
# và người đọc không phân biệt được hai tình huống.
TRANG_THAI_GAN_NHAN = [
    ("chua_xep_hang", "Chưa xếp hàng"),
    ("pending", "Chờ gán nhãn"),
    ("processing", "Đang gán nhãn"),
    ("done", "Đã gán nhãn"),
    ("failed", "Gán nhãn lỗi"),
    ("skipped", "Bỏ qua (quá ngắn)"),
]

CHAT_DICTIONARY: dict[str, list[tuple[str, str]]] = {
    "nen_tang_chat": NEN_TANG_CHAT,
    "loai_tin_chat": LOAI_TIN_CHAT,
    "chieu_tin_chat": CHIEU_TIN_CHAT,
    "giai_doan_chat": GIAI_DOAN_CHAT,
    "muc_do_quan_tam": MUC_DO_QUAN_TAM,
    "trang_thai_gan_nhan": TRANG_THAI_GAN_NHAN,
}
```

- [ ] **Step 4: Nối vào `DICTIONARY`**

Trong `backend/apps/ai_mart/domain/dictionary.py`, thêm import ở đầu file:

```python
from apps.ai_mart.domain.chat_dictionary import CHAT_DICTIONARY
```

và ngay sau khai báo `DICTIONARY` (dòng cuối của dict, trước `def dictionary_rows`):

```python
# Nhóm mã của mart hội thoại chat (spec 2026-09-04) — gộp vào đây để
# dictionary_rows() là MỘT nguồn seed duy nhất cho mọi migration từ điển.
DICTIONARY.update(CHAT_DICTIONARY)
```

- [ ] **Step 5: Viết `domain/need_dictionary.py`**

```python
"""nhu_cau / nhom_nhu_cau đọc THẲNG từ bảng need_types / need_groups.

Không chép crm_catalog.NEED_TYPE_SEED: đo 04/09/2026 thấy bảng có 36 mã còn
seed chỉ 29 — 7 mã kia do admin thêm sau, chép hằng số là để chúng thành mã
mồ côi trên mart (cột _ma có giá trị mà _text NULL, đúng cái bệnh spec ai_mart
sinh ra để chữa).
"""

_UPSERT_NHOM = """
INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu)
SELECT 'nhom_nhu_cau', g.code, g.name_vi, coalesce(g.sort_order, 0)
FROM need_groups g
ON CONFLICT (nhom_ma, ma) DO UPDATE
SET nhan = EXCLUDED.nhan, thu_tu = EXCLUDED.thu_tu
"""

# mo_ta: mã ngừng dùng VẪN được seed (nhãn lịch sử trên mart phải tra được),
# nhưng phải nói rõ đã ngừng. NULL cho mã còn hiệu lực — không nhồi chữ thừa.
_UPSERT_NHU_CAU = """
INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, mo_ta, thu_tu)
SELECT 'nhu_cau', t.code, t.name_vi,
       CASE WHEN t.is_active THEN NULL
            ELSE 'Mã đã ngừng dùng — chỉ còn xuất hiện ở dữ liệu lịch sử' END,
       coalesce(t.sort_order, 0)
FROM need_types t
ON CONFLICT (nhom_ma, ma) DO UPDATE
SET nhan = EXCLUDED.nhan, mo_ta = EXCLUDED.mo_ta, thu_tu = EXCLUDED.thu_tu
"""


def refresh_need_dictionary(cursor) -> int:
    """Đồng bộ 2 nhóm mã nhu cầu vào ai.tu_dien_ma. Idempotent."""
    total = 0
    for sql in (_UPSERT_NHOM, _UPSERT_NHU_CAU):
        cursor.execute(sql)
        total += cursor.rowcount
    return total
```

- [ ] **Step 6: Viết migration `0014_chat_tu_dien.py`**

```python
"""Seed từ điển cho mart hội thoại chat (spec 2026-09-04 §3.3).

Chạy lại dictionary_rows() nguyên bộ thay vì chỉ 6 nhóm mới: upsert nên vô
hại, và nhân tiện làm tươi nhãn của các nhóm cũ nếu domain đã đổi.
"""

from django.db import migrations


def seed(apps, schema_editor):
    from apps.ai_mart.domain.dictionary import dictionary_rows
    from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary

    with schema_editor.connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (nhom_ma, ma) DO UPDATE SET nhan = EXCLUDED.nhan,"
            " thu_tu = EXCLUDED.thu_tu",
            dictionary_rows(),
        )
        refresh_need_dictionary(cur)


class Migration(migrations.Migration):
    # need_types/need_groups phải tồn tại trước khi seed đọc chúng.
    dependencies = [("ai_mart", "0013_don_hang_chi_nhanh_nhom_kenh"), ("orders", "0001_initial")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(seed, migrations.RunPython.noop)]
        )
    ]
```

> Nếu `orders` không có migration tên `0001_initial` (đã squash), thay bằng tên migration **mới nhất** của app `orders`: `ls backend/apps/orders/migrations | tail -3`.

- [ ] **Step 7: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_dictionary -v2 --noinput
```
Expected: 4 tests PASS.

- [ ] **Step 8: Kiểm không vỡ test từ điển cũ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_dictionary apps.ai_mart.tests.test_dictionary_domain apps.ai_mart.tests.test_dictionary_table -v2 --noinput
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/ai_mart/domain/chat_dictionary.py \
        backend/apps/ai_mart/domain/need_dictionary.py \
        backend/apps/ai_mart/domain/dictionary.py \
        backend/apps/ai_mart/migrations/0014_chat_tu_dien.py \
        backend/apps/ai_mart/tests/test_chat_dictionary.py
git commit -m "feat(ai_mart): từ điển mã cho mart hội thoại chat + đồng bộ nhu cầu từ need_types"
```

---

### Task 2: Cầu FDW + bảng nguồn `ai_stg.chat_*`

Spec §5.1 (C1). `CREATE EXTENSION` / `CREATE SERVER` / `CREATE USER MAPPING` cần **superuser** → không được nằm trong migration (DB test không có superuser, và migrate chạy lại sẽ vô nghĩa). Vì vậy: migration tạo bảng **local rỗng** cùng tên/cùng cột (để test và dev chạy được), còn command `ai_mart_chat_fdw` trên prod DROP bảng local đó và thay bằng foreign table. Cột do **một** hằng số sinh ra cho cả hai đường → không lệch nhau.

Đổi `IMPORT FOREIGN SCHEMA` của spec thành `CREATE FOREIGN TABLE` liệt kê cột: `IMPORT` không kiểm soát được tên/kiểu và không tạo được bản sao local giống hệt cho test.

**Files:**
- Create: `backend/apps/ai_mart/infrastructure/chat_source.py`
- Create: `backend/apps/ai_mart/management/commands/ai_mart_chat_fdw.py`
- Create: `backend/apps/ai_mart/migrations/0015_chat_stg_nguon.py`
- Modify: `backend/config/settings_parts/base.py` (env `CHAT_CRAWLER_DB_*`, thêm `CHAT_CRAWLER_DB_PASSWORD` vào `STAGING_FORBIDDEN_OUTBOUND_ENVS`)
- Modify: `compose.yml`, `compose.prod.yml` (truyền env vào service backend/worker — kiểm bằng `ls /home/desi/desi-order/compose*.yml`)
- Test: `backend/apps/ai_mart/tests/test_chat_source.py`

**Interfaces:**
- Produces:
  - `chat_source.CHAT_TABLES: dict[str, str]` — tên bảng local (`chat_pages`, `chat_conversations`, `chat_customers`, `chat_messages`, `chat_tags`, `chat_conversation_tags`) → thân danh sách cột SQL.
  - `chat_source.SOURCE_NAMES: dict[str, str]` — tên local → tên bảng thật bên `crawler` (`chat_pages` → `pages`, …).
  - `chat_source.create_local_tables_sql() -> str`
  - `chat_source.create_foreign_tables_sql(server: str = "chat_crawler") -> str`
  - `chat_source.drop_tables_sql() -> str`
- Task 3 và 4 đọc `ai_stg.chat_*` theo đúng tên cột khai ở đây.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_source.py`:

```python
"""Bảng local (test/dev) và foreign table (prod) PHẢI cùng tên cột, cùng kiểu.

Lệch một cột là transform chạy xanh trên CI rồi chết trên prod — đúng loại bug
mà 'một nguồn sinh cả hai DDL' sinh ra để chặn.
"""

import re

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import chat_source


def _columns(ddl: str, table: str) -> list[str]:
    body = re.search(rf"{table} \((.*?)\n\)", ddl, re.S).group(1)
    return [line.strip().split()[0] for line in body.strip().splitlines() if line.strip()]


class ChatSourceTests(TestCase):
    def test_local_and_foreign_ddl_have_identical_columns(self):
        local = chat_source.create_local_tables_sql()
        foreign = chat_source.create_foreign_tables_sql()
        for table in chat_source.CHAT_TABLES:
            self.assertEqual(
                _columns(local, table), _columns(foreign, table), f"lệch cột ở {table}"
            )

    def test_foreign_ddl_maps_to_real_source_table_names(self):
        foreign = chat_source.create_foreign_tables_sql()
        # ai_stg.chat_conversations phải trỏ tới public.conversations bên crawler
        self.assertIn("table_name 'conversations'", foreign)
        self.assertIn("SERVER chat_crawler", foreign)
        self.assertNotIn("password", foreign.lower())  # mật khẩu KHÔNG nằm trong DDL bảng

    def test_migration_created_local_tables(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai_stg' AND c.relname LIKE 'chat\\_%'"
            )
            found = {r[0] for r in cur.fetchall()}
        self.assertEqual(found, set(chat_source.CHAT_TABLES))

    def test_messages_table_has_columns_transform_depends_on(self):
        # Task 3/4 đọc đúng các cột này; đổi tên ở nguồn phải làm test đỏ ở đây trước.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_attribute a"
                " WHERE a.attrelid = 'ai_stg.chat_messages'::regclass AND a.attnum > 0"
            )
            cols = {r[0] for r in cur.fetchall()}
        self.assertLessEqual(
            {"id", "page_id", "conversation_id", "direction", "type", "content",
             "attachments", "inserted_at"},
            cols,
        )
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_source -v2 --noinput
```
Expected: FAIL — `No module named 'apps.ai_mart.infrastructure.chat_source'`.

- [ ] **Step 3: Viết `infrastructure/chat_source.py`**

```python
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


def create_foreign_tables_sql(server: str = SERVER_NAME) -> str:
    return "\n".join(
        f"CREATE FOREIGN TABLE {STG_SCHEMA}.{table} ({body}\n)"
        f" SERVER {server} OPTIONS (schema_name 'public', table_name '{SOURCE_NAMES[table]}');"
        for table, body in CHAT_TABLES.items()
    )


def drop_tables_sql() -> str:
    # DROP cả hai dạng: bảng local do migration tạo, hoặc foreign table lần chạy trước.
    return "\n".join(
        f"DROP FOREIGN TABLE IF EXISTS {STG_SCHEMA}.{table};"
        f" DROP TABLE IF EXISTS {STG_SCHEMA}.{table};"
        for table in CHAT_TABLES
    )
```

- [ ] **Step 4: Viết migration `0015_chat_stg_nguon.py`**

```python
"""Bảng nguồn chat trong ai_stg — dạng LOCAL (test/dev).

Trên prod, `manage.py ai_mart_chat_fdw` DROP các bảng này rồi tạo lại thành
FOREIGN TABLE trỏ sang DB crawler. Migration dùng IF NOT EXISTS nên lần migrate
sau thấy foreign table cùng tên là bỏ qua — không giẫm lên cầu FDW đang chạy.
"""

from django.db import migrations

from apps.ai_mart.infrastructure.chat_source import create_local_tables_sql, drop_tables_sql


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0014_chat_tu_dien")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=create_local_tables_sql(), reverse_sql=drop_tables_sql())
            ]
        )
    ]
```

- [ ] **Step 5: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_source -v2 --noinput
```
Expected: 4 tests PASS.

- [ ] **Step 6: Thêm settings**

`backend/config/settings_parts/base.py` — cạnh khối `GEMINI_*` (~dòng 418):

```python
# Cầu postgres_fdw sang DB của desi-crawl-conversations (spec 2026-09-04 C1).
# Host mặc định là tên container trong mạng deploy_default — chính tiến trình
# Postgres của desi-postgres mở kết nối, KHÔNG phải Django, nên phải là tên mà
# container postgres phân giải được.
CHAT_CRAWLER_DB_HOST = os.getenv("CHAT_CRAWLER_DB_HOST", "deploy-postgres-1").strip()
CHAT_CRAWLER_DB_PORT = os.getenv("CHAT_CRAWLER_DB_PORT", "5432").strip()
CHAT_CRAWLER_DB_NAME = os.getenv("CHAT_CRAWLER_DB_NAME", "crawler").strip()
# KHÔNG dùng superuser `crawler` cho user mapping — role này chỉ cần SELECT.
CHAT_CRAWLER_DB_USER = os.getenv("CHAT_CRAWLER_DB_USER", "chat_ro").strip()
CHAT_CRAWLER_DB_PASSWORD = os.getenv("CHAT_CRAWLER_DB_PASSWORD", "").strip()
```

và thêm vào tuple `STAGING_FORBIDDEN_OUTBOUND_ENVS` (~dòng 295):

```python
    # Staging cầm mật khẩu này là đọc được toàn bộ hội thoại khách thật.
    "CHAT_CRAWLER_DB_PASSWORD",
```

- [ ] **Step 7: Viết command `ai_mart_chat_fdw`**

`backend/apps/ai_mart/management/commands/ai_mart_chat_fdw.py`:

```python
"""Dựng cầu postgres_fdw sang DB crawler. Chạy TAY trên prod, cần SUPERUSER:

  docker compose exec -e CHAT_CRAWLER_DB_PASSWORD=... web \\
      python manage.py ai_mart_chat_fdw

CREATE EXTENSION/SERVER/USER MAPPING là đối tượng cấp cluster — không đặt trong
migration (DB test không có superuser). `--check` chỉ đo, không sửa gì.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg import sql as pg_sql

from apps.ai_mart.infrastructure import chat_source


class Command(BaseCommand):
    help = "Tạo/cập nhật foreign table ai_stg.chat_* trỏ sang DB crawler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true",
            help="chỉ kiểm tra cầu có đọc được không, không thay đổi gì",
        )

    def handle(self, *args, **options):
        if options["check"]:
            return self._check()

        password = settings.CHAT_CRAWLER_DB_PASSWORD
        if not password:
            raise CommandError("thiếu env CHAT_CRAWLER_DB_PASSWORD")

        with connection.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
            cur.execute(
                "SELECT 1 FROM pg_foreign_server WHERE srvname = %s",
                [chat_source.SERVER_NAME],
            )
            verb = "ALTER" if cur.fetchone() else "CREATE"
            options_kw = "OPTIONS (SET host %s, SET port %s, SET dbname %s)" if verb == "ALTER" \
                else "FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host %s, port %s, dbname %s)"
            cur.execute(
                pg_sql.SQL("{} SERVER {} " + options_kw.replace("%s", "{}")).format(
                    pg_sql.SQL(verb),
                    pg_sql.Identifier(chat_source.SERVER_NAME),
                    pg_sql.Literal(settings.CHAT_CRAWLER_DB_HOST),
                    pg_sql.Literal(settings.CHAT_CRAWLER_DB_PORT),
                    pg_sql.Literal(settings.CHAT_CRAWLER_DB_NAME),
                )
            )
            cur.execute(
                "SELECT 1 FROM pg_user_mappings WHERE srvname = %s AND usename = current_user",
                [chat_source.SERVER_NAME],
            )
            map_verb = "ALTER" if cur.fetchone() else "CREATE"
            cur.execute(
                pg_sql.SQL(
                    "{} USER MAPPING FOR CURRENT_USER SERVER {} OPTIONS ({} user {}, {} password {})"
                ).format(
                    pg_sql.SQL(map_verb),
                    pg_sql.Identifier(chat_source.SERVER_NAME),
                    pg_sql.SQL("SET" if map_verb == "ALTER" else ""),
                    pg_sql.Literal(settings.CHAT_CRAWLER_DB_USER),
                    pg_sql.SQL("SET" if map_verb == "ALTER" else ""),
                    pg_sql.Literal(password),
                )
            )
            # Bảng local do migration 0015 tạo bị thay bằng foreign table.
            cur.execute(chat_source.drop_tables_sql())
            cur.execute(chat_source.create_foreign_tables_sql())
        self.stdout.write(self.style.SUCCESS("cầu FDW sẵn sàng — kiểm bằng --check"))

    def _check(self):
        with connection.cursor() as cur:
            for table in chat_source.CHAT_TABLES:
                cur.execute(f"SELECT count(*) FROM ai_stg.{table}")  # noqa: S608 — tên do code sở hữu
                (n,) = cur.fetchone()
                self.stdout.write(f"ai_stg.{table}: {n} dòng")
```

- [ ] **Step 8: Truyền env vào compose**

Với mỗi file compose có service backend/worker (`compose.yml`, `compose.prod.yml` — kiểm `grep -ln "GEMINI_API_KEY" /home/desi/desi-order/compose*.yml`), thêm vào khối `environment` của service web **và** worker:

```yaml
      CHAT_CRAWLER_DB_HOST: ${CHAT_CRAWLER_DB_HOST:-deploy-postgres-1}
      CHAT_CRAWLER_DB_PORT: ${CHAT_CRAWLER_DB_PORT:-5432}
      CHAT_CRAWLER_DB_NAME: ${CHAT_CRAWLER_DB_NAME:-crawler}
      CHAT_CRAWLER_DB_USER: ${CHAT_CRAWLER_DB_USER:-chat_ro}
      CHAT_CRAWLER_DB_PASSWORD: ${CHAT_CRAWLER_DB_PASSWORD:-}
```

Và service `postgres` (container `desi-postgres`) phải nối thêm mạng `deploy_default` — đo được hai container ở hai mạng rời (`desi-order_default` 172.19.0.20 vs `deploy_default` 172.18.0.2) và `deploy-postgres-1` chỉ bind `127.0.0.1:15432` nên không đi vòng qua host được:

```yaml
  postgres:
    networks:
      - default
      - deploy_default
networks:
  deploy_default:
    external: true
```

- [ ] **Step 9: Xác minh `updated_at` dùng được làm watermark (spec §5.2, việc còn treo của S1)**

Rủi ro: nếu crawler ghi đè `conversations.updated_at = now()` lúc upsert thay vì lấy từ payload Pancake, watermark vẫn chạy nhưng ý nghĩa khác hẳn. Kiểm bằng **hai** cách, không đoán:

```bash
# 1. Đọc code crawler — câu UPSERT phải gán updated_at từ payload, KHÔNG phải now()/CURRENT_TIMESTAMP
grep -rn "updated_at" ~/desi-crawl-conversations/internal --include=*.go | grep -i "insert\|update\|excluded"

# 2. Đo phân bố: updated_at trải rộng theo lịch sử = của Pancake;
#    dồn cục quanh giờ crawl = của crawler
docker exec deploy-postgres-1 psql -U crawler -d crawler -Atc \
  "SELECT min(updated_at), max(updated_at), count(DISTINCT date_trunc('day', updated_at)) FROM conversations;"
```

Kết luận (dù ra sao) ghi một dòng vào `docs/runbooks/2026-09-04-ai-chat-mart-deploy.md` mục "Đã biết / bẫy" ở Task 8. Nếu hoá ra là `now()` của crawler: **không đổi thiết kế** — watermark vẫn chạy đúng (dữ liệu mới vẫn được quét), và rebuild đêm là lưới an toàn; chỉ cần ghi rõ để người sau không hiểu nhầm cột này là thời điểm nghiệp vụ.

- [ ] **Step 10: Chạy lại toàn bộ test ai_mart**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart -v2 --noinput
```
Expected: PASS (bảng `ai_stg.chat_*` rỗng chưa ảnh hưởng mart nào).

- [ ] **Step 11: Commit**

```bash
git add backend/apps/ai_mart/infrastructure/chat_source.py \
        backend/apps/ai_mart/management/commands/ai_mart_chat_fdw.py \
        backend/apps/ai_mart/migrations/0015_chat_stg_nguon.py \
        backend/apps/ai_mart/tests/test_chat_source.py \
        backend/config/settings_parts/base.py compose.yml compose.prod.yml
git commit -m "feat(ai_mart): cầu postgres_fdw + bảng nguồn ai_stg.chat_* cho hội thoại Pancake"
```

---

### Task 3: Bảng `ai.tin_nhan`

Spec §3.2. Grain 1 tin, để agent trích **nguyên văn** làm dẫn chứng thay vì chỉ đưa nhãn (C3).

**Files:**
- Create: `backend/apps/ai_mart/migrations/0016_tin_nhan.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/chat_tin_nhan.py`
- Create: `backend/apps/ai_mart/tests/chat_helpers.py`
- Test: `backend/apps/ai_mart/tests/test_chat_tin_nhan.py`

**Interfaces:**
- Consumes: `chat_source.CHAT_TABLES` (Task 2); `MartTable` từ `apps.ai_mart.infrastructure.sql.base`; `loaders.full_rebuild`, `loaders.upsert_by_ids`, `loaders.candidate_ids`.
- Produces:
  - `chat_tin_nhan.MART: MartTable` — `name="ai.tin_nhan"`, `key_column="tin_nhan_id"`, `source_key="m.id"`, `group="hot"`.
  - `chat_helpers.mk_page(**kw) -> int`, `mk_conversation(**kw) -> int`, `mk_message(**kw) -> int`, `mk_tag(**kw) -> int`, `tag_conversation(conversation_id, tag_id)` — chèn thẳng vào `ai_stg.chat_*`, trả id. Task 4 dùng lại.

- [ ] **Step 1: Viết helper dựng dữ liệu nguồn**

`backend/apps/ai_mart/tests/chat_helpers.py`:

```python
"""Dựng dữ liệu trong ai_stg.chat_* bằng SQL trần.

Không có model Django cho các bảng này (chúng là foreign table trên prod), nên
test phải INSERT tay. id tự tăng bằng bộ đếm — bảng nguồn không có sequence.
"""

import itertools

from django.db import connection

_ids = itertools.count(1)


def _insert(table: str, **values) -> int:
    cols = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    with connection.cursor() as cur:
        cur.execute(f"INSERT INTO ai_stg.{table} ({cols}) VALUES ({marks})", list(values.values()))
    return values.get("id", 0)


def mk_page(**kw) -> int:
    values = {
        "id": next(_ids),
        "pancake_page_id": f"pp-{next(_ids)}",
        "platform": "facebook",
        "name": "Delax Fanpage",
        "status": "active",
    }
    values.update(kw)
    return _insert("chat_pages", **values)


def mk_customer(page_id: int, **kw) -> int:
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_customer_id": f"pc-{next(_ids)}",
        "name": "Khách Test",
        "phone": "",
        "fb_id": "",
        "gender": "",
    }
    values.update(kw)
    return _insert("chat_customers", **values)


def mk_conversation(page_id: int, **kw) -> int:
    from django.utils import timezone

    now = timezone.now()
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_conversation_id": f"conv-{next(_ids)}",
        "customer_id": None,
        "type": "INBOX",
        "snippet": "",
        "message_count": 0,
        "last_message_at": now,
        "updated_at": now,
        "created_at": now,
    }
    values.update(kw)
    return _insert("chat_conversations", **values)


def mk_message(conversation_id: int, page_id: int, **kw) -> int:
    from django.utils import timezone

    values = {
        "id": next(_ids),
        "page_id": page_id,
        "conversation_id": conversation_id,
        "pancake_message_id": f"msg-{next(_ids)}",
        "sender_id": "",
        "direction": "in",
        "type": "INBOX",
        "content": "xin chào",
        "attachments": None,
        "inserted_at": timezone.now(),
    }
    values.update(kw)
    return _insert("chat_messages", **values)


def mk_tag(page_id: int, name: str, **kw) -> int:
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_tag_id": f"tag-{next(_ids)}",
        "name": name,
        "color": "",
    }
    values.update(kw)
    return _insert("chat_tags", **values)


def tag_conversation(conversation_id: int, tag_id: int) -> None:
    _insert("chat_conversation_tags", conversation_id=conversation_id, tag_id=tag_id)
```

- [ ] **Step 2: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_tin_nhan.py`:

```python
import json
from datetime import UTC, datetime, timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_tin_nhan import MART
from apps.ai_mart.tests import chat_helpers as ch


class TinNhanTests(TestCase):
    def _row(self, tin_nhan_id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT hoi_thoai_id, page_ten, chieu_text, loai_tin_text, noi_dung,"
                " co_dinh_kem, gui_luc_ngay FROM ai.tin_nhan WHERE tin_nhan_id = %s",
                [tin_nhan_id],
            )
            return cur.fetchone()

    def test_labels_and_attachment_flag(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        mid = ch.mk_message(
            conv, page, direction="out", type="COMMENT", content="  chào bạn  ",
            attachments=json.dumps([{"type": "photo"}]),
        )
        loaders.full_rebuild(MART)
        hoi_thoai_id, page_ten, chieu, loai, noi_dung, dinh_kem, _ = self._row(mid)
        self.assertEqual(hoi_thoai_id, conv)
        self.assertEqual(page_ten, "Delax Fanpage")
        self.assertEqual(chieu, "Page gửi")
        self.assertEqual(loai, "Bình luận")
        self.assertEqual(noi_dung, "chào bạn")   # btrim, không giữ khoảng trắng thừa
        self.assertTrue(dinh_kem)

    def test_empty_content_is_excluded(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        empty = ch.mk_message(conv, page, content="   ")
        kept = ch.mk_message(conv, page, content="có nội dung")
        loaders.full_rebuild(MART)
        self.assertIsNone(self._row(empty))
        self.assertIsNotNone(self._row(kept))

    def test_date_uses_business_timezone(self):
        # 2026-08-31T18:30Z = 01/09/2026 01:30 giờ VN → phải rơi vào ngày 01/09
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        mid = ch.mk_message(
            conv, page,
            inserted_at=datetime(2026, 8, 31, 18, 30, tzinfo=UTC),
        )
        loaders.full_rebuild(MART)
        self.assertEqual(str(self._row(mid)[6]), "2026-09-01")

    def test_incremental_upsert_matches_full_rebuild(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="tin 1")
        ch.mk_message(conv, page, content="tin 2")
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.tin_nhan")
            (before,) = cur.fetchone()

        long_ago = timezone.now() - timedelta(days=1)
        ids = loaders.candidate_ids(MART, long_ago)
        loaders.upsert_by_ids(MART, ids)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.tin_nhan")
            (after,) = cur.fetchone()
        # upsert chạy lại trên cùng dữ liệu KHÔNG được nhân đôi dòng
        self.assertEqual(before, after)
        self.assertEqual(before, 2)
```

- [ ] **Step 3: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_tin_nhan -v2 --noinput
```
Expected: FAIL — `No module named 'apps.ai_mart.infrastructure.sql.chat_tin_nhan'`.

- [ ] **Step 4: Viết migration `0016_tin_nhan.py`**

```python
"""Bảng ai.tin_nhan — grain 1 tin nhắn chat (spec 2026-09-04 §3.2).

10 cột, ĐÚNG thứ tự SELECT của infrastructure/sql/chat_tin_nhan.py (loader
INSERT theo vị trí, không liệt kê tên cột).

Chưa partition: 769k dòng còn xa ngưỡng ~10 triệu ghi ở spec §9.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.tin_nhan (
    tin_nhan_id bigint PRIMARY KEY,
    hoi_thoai_id bigint,
    page_ten text,
    gui_luc timestamptz,
    gui_luc_ngay date,
    chieu_text text,
    loai_tin_text text,
    noi_dung text,
    co_dinh_kem boolean,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS tin_nhan_hoi_thoai_idx ON ai.tin_nhan (hoi_thoai_id);
CREATE INDEX IF NOT EXISTS tin_nhan_gui_luc_ngay_idx ON ai.tin_nhan (gui_luc_ngay);

COMMENT ON TABLE ai.tin_nhan IS
 'Mỗi dòng = 1 tin nhắn chat khách hàng (nguồn Pancake/pages.fm qua crawler desi-crawl-conversations). CHỈ chứa tin CÓ nội dung chữ — tin đính kèm thuần (ảnh/sticker) bị loại. Không có cột nhân viên: nguồn không lưu admin_id, mọi tin gửi ra đều mang id của page (spec §1 F2). RIÊNG cột noi_dung là văn bản thô CHƯA che PII — xem COMMENT cột.';
COMMENT ON COLUMN ai.tin_nhan.tin_nhan_id IS 'Id tin nhắn trong DB crawler (messages.id) — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.tin_nhan.hoi_thoai_id IS 'Id hội thoại chứa tin này — nối với ai.hoi_thoai.hoi_thoai_id';
COMMENT ON COLUMN ai.tin_nhan.page_ten IS 'Tên page/gian hàng nhận tin (Facebook page, Zalo OA, gian TikTok Shop...) — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.tin_nhan.gui_luc IS 'Thời điểm Pancake ghi nhận tin (giờ UTC) — là thời điểm NGHIỆP VỤ, không phải thời điểm crawl';
COMMENT ON COLUMN ai.tin_nhan.gui_luc_ngay IS 'Ngày gửi tin theo giờ Việt Nam (Asia/Ho_Chi_Minh) — dùng để lọc/group theo ngày';
COMMENT ON COLUMN ai.tin_nhan.chieu_text IS 'Nhãn tiếng Việt chiều tin: "Khách gửi" (direction=in) hoặc "Page gửi" (direction=out). Tra ai.tu_dien_ma nhóm chieu_tin_chat. LƯU Ý: "Page gửi" gộp cả nhân viên lẫn chatbot — dùng ai.hoi_thoai.do_bot_xu_ly để lọc.';
COMMENT ON COLUMN ai.tin_nhan.loai_tin_text IS 'Nhãn tiếng Việt loại tin: Tin nhắn (INBOX) / Bình luận (COMMENT) / Đánh giá (RATING). Tra ai.tu_dien_ma nhóm loai_tin_chat.';
COMMENT ON COLUMN ai.tin_nhan.noi_dung IS 'Nguyên văn nội dung tin nhắn — CHƯA CHE PII: khách gõ thẳng SĐT và địa chỉ vào chat. Ngoại lệ có chủ đích của luật che PII (spec §4.6), cùng chuẩn với ai.cuoc_goi.transcript, vì agent cần nguyên văn để trả lời về nội dung tư vấn. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời.';
COMMENT ON COLUMN ai.tin_nhan.co_dinh_kem IS 'Tin có đính kèm (ảnh/video/file) hay không — đo 04/09/2026: 255.080/769.234 tin có đính kèm. Nội dung đính kèm KHÔNG được lưu.';
COMMENT ON COLUMN ai.tin_nhan.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.tin_nhan;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0015_chat_stg_nguon")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
```

- [ ] **Step 5: Viết `infrastructure/sql/chat_tin_nhan.py`**

```python
"""1 dòng/tin nhắn chat. Nguồn là foreign table ai_stg.chat_* (spec C1).

Bộ lọc "chỉ tin có nội dung" hiện là no-op (đo 04/09/2026: 0/769.234 tin có
content rỗng) nhưng phải giữ: lần crawl sau tin đính-kèm-thuần sẽ có content
rỗng và không được lọt vào bảng chữ.
"""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.tz import BUSINESS_TZ

# Tin nhắn append-only nên không có updated_at riêng — lấy theo hội thoại vừa đổi.
_CANDIDATES = """
SELECT m.id
FROM ai_stg.chat_messages m
JOIN ai_stg.chat_conversations c ON c.id = m.conversation_id
WHERE c.updated_at > %(cutoff)s
"""

_HAS_CONTENT = "NULLIF(btrim(coalesce(m.content, '')), '') IS NOT NULL"


def _select(where: str) -> str:
    return f"""
SELECT
    m.id AS tin_nhan_id,
    m.conversation_id AS hoi_thoai_id,
    NULLIF(btrim(coalesce(p.name, '')), '') AS page_ten,
    m.inserted_at AS gui_luc,
    (m.inserted_at AT TIME ZONE '{BUSINESS_TZ}')::date AS gui_luc_ngay,
    ai.nhan('chieu_tin_chat', m.direction) AS chieu_text,
    ai.nhan('loai_tin_chat', m.type) AS loai_tin_text,
    btrim(m.content) AS noi_dung,
    CASE jsonb_typeof(m.attachments)
        WHEN 'array' THEN jsonb_array_length(m.attachments) > 0
        ELSE m.attachments IS NOT NULL
    END AS co_dinh_kem,
    now() AS dong_bo_luc
FROM ai_stg.chat_messages m
LEFT JOIN ai_stg.chat_pages p ON p.id = m.page_id
WHERE {_HAS_CONTENT} AND ({where})
"""


MART = MartTable(
    name="ai.tin_nhan",
    key_column="tin_nhan_id",
    source_key="m.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
```

- [ ] **Step 6: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_tin_nhan -v2 --noinput
```
Expected: 4 tests PASS.

- [ ] **Step 7: Kiểm test contract tự bắt bảng mới**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_contract -v2 --noinput
```
Expected: PASS (mọi cột của `ai.tin_nhan` đã có COMMENT). Nếu FAIL thì bổ sung `COMMENT ON COLUMN` còn thiếu vào migration rồi tạo lại DB test.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/ai_mart/migrations/0016_tin_nhan.py \
        backend/apps/ai_mart/infrastructure/sql/chat_tin_nhan.py \
        backend/apps/ai_mart/tests/chat_helpers.py \
        backend/apps/ai_mart/tests/test_chat_tin_nhan.py
git commit -m "feat(ai_mart): bảng ai.tin_nhan — grain 1 tin nhắn chat"
```

---

### Task 4: `ai_stg.hoi_thoai_nhan` + bảng `ai.hoi_thoai`

Spec §3.1, C3, C7, C8. Đây là bảng trả lời hai câu hỏi kinh doanh. Cột nhãn AI đọc từ `ai_stg.hoi_thoai_nhan` — task này **không** gọi LLM; test chèn tay vào bảng nhãn để chứng minh đường dữ liệu chạy. Task 6/7 mới sinh nhãn thật.

**Files:**
- Create: `backend/apps/ai_mart/migrations/0017_hoi_thoai.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/chat_hoi_thoai.py`
- Test: `backend/apps/ai_mart/tests/test_chat_hoi_thoai.py`

**Interfaces:**
- Consumes: `chat_helpers` (Task 3), `chat_source` (Task 2), từ điển `nen_tang_chat`/`loai_tin_chat`/`giai_doan_chat`/`muc_do_quan_tam`/`nhu_cau`/`ly_do_khong_chot`/`trang_thai_gan_nhan` (Task 1).
- Produces:
  - `chat_hoi_thoai.MART: MartTable` — `name="ai.hoi_thoai"`, `key_column="hoi_thoai_id"`, `source_key="c.id"`, `group="hot"`.
  - Bảng `ai_stg.hoi_thoai_nhan(hoi_thoai_id bigint PK, extract_payload jsonb, extract_status text, extract_error text, extract_attempts int, model text, noi_dung_hash text, created_at, updated_at)` — Task 7 ghi vào đây.
  - **Hợp đồng khoá `extract_payload->'clean'`** (Task 6 phải sinh đúng các khoá này): `tom_tat` (text), `nhu_cau` (array mã `need_types`), `ly_do_khong_mua` (array mã `ly_do_khong_chot`), `san_pham_quan_tam` (array text tự do), `giai_doan` (mã `giai_doan_chat`), `muc_do_quan_tam` (mã `muc_do_quan_tam`), `sdt_khach` (SĐT đã chuẩn hoá dạng `0…`).

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_hoi_thoai.py`:

```python
import json

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch
from apps.ai_mart.tests.helpers import mk_order


def _label(conv_id, *cols):
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(cols)} FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [conv_id]
        )
        return cur.fetchone()


def _set_labels(conv_id, clean, status="done"):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_payload, extract_status)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (hoi_thoai_id) DO UPDATE SET extract_payload = EXCLUDED.extract_payload,"
            " extract_status = EXCLUDED.extract_status, updated_at = now()",
            [conv_id, json.dumps({"clean": clean}), status],
        )


class HoiThoaiShapeTests(TestCase):
    def test_counts_and_transcript_roles(self):
        page = ch.mk_page(platform="personal_zalo", name="Zalo Delax")
        cust = ch.mk_customer(page, name="Chị Lan", phone="0912345678")
        conv = ch.mk_conversation(page, customer_id=cust, type="INBOX")
        ch.mk_message(conv, page, direction="in", content="ghế này bao nhiêu")
        ch.mk_message(conv, page, direction="out", content="dạ 20 triệu ạ")
        ch.mk_message(conv, page, direction="in", content="đắt quá")
        loaders.full_rebuild(MART)

        row = _label(
            conv, "so_tin", "so_tin_khach", "so_tin_page", "co_khach_tra_loi",
            "nen_tang_text", "loai_kenh_text", "khach_ten", "sdt_che", "noi_dung",
        )
        so_tin, khach, page_msg, co_tra_loi, nen_tang, kenh, ten, sdt, noi_dung = row
        self.assertEqual((so_tin, khach, page_msg), (3, 2, 1))
        self.assertTrue(co_tra_loi)
        self.assertEqual(nen_tang, "Zalo cá nhân")
        self.assertEqual(kenh, "Tin nhắn")
        self.assertEqual(ten, "Chị Lan")
        self.assertEqual(sdt, "0912***678")          # luật che PII
        self.assertEqual(
            noi_dung,
            "Khách: ghế này bao nhiêu\nPage: dạ 20 triệu ạ\nKhách: đắt quá",
        )

    def test_conversation_without_messages_survives_with_zero_counts(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        loaders.full_rebuild(MART)
        so_tin, khach, co_tra_loi, noi_dung = _label(
            conv, "so_tin", "so_tin_khach", "co_khach_tra_loi", "noi_dung"
        )
        # 465 hội thoại rỗng trên nguồn — phải là 0, KHÔNG phải NULL, và không bị rơi khỏi bảng
        self.assertEqual((so_tin, khach), (0, 0))
        self.assertFalse(co_tra_loi)
        self.assertIsNone(noi_dung)

    def test_bot_tag_flag(self):
        page = ch.mk_page()
        bot_conv = ch.mk_conversation(page)
        human_conv = ch.mk_conversation(page)
        ch.tag_conversation(bot_conv, ch.mk_tag(page, "BOT-MSC06"))
        ch.tag_conversation(human_conv, ch.mk_tag(page, "Đã tư vấn"))
        loaders.full_rebuild(MART)
        self.assertTrue(_label(bot_conv, "do_bot_xu_ly")[0])
        self.assertFalse(_label(human_conv, "do_bot_xu_ly")[0])
        self.assertEqual(_label(human_conv, "nhan_tag_text")[0], "Đã tư vấn")


class HoiThoaiLabelTests(TestCase):
    def test_ai_labels_are_translated_and_grouped(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="đau vai gáy")
        _set_labels(conv, {
            "tom_tat": "khách đau vai gáy, chê đắt",
            "nhu_cau": ["neck_shoulder_nape"],
            "ly_do_khong_mua": ["price_high"],
            "san_pham_quan_tam": ["Ghế massage X10"],
            "giai_doan": "dang_can_nhac",
            "muc_do_quan_tam": "cao",
        })
        loaders.full_rebuild(MART)
        row = _label(
            conv, "tom_tat_ai", "nhu_cau_ai", "nhu_cau_ai_text", "nhom_nhu_cau_ai",
            "ly_do_khong_mua_ma", "san_pham_quan_tam", "giai_doan_text",
            "muc_do_quan_tam_text", "trang_thai_gan_nhan_text",
        )
        tom_tat, nhu_cau, nhu_cau_text, nhom, ly_do, san_pham, giai_doan, muc_do, tt = row
        self.assertEqual(tom_tat, "khách đau vai gáy, chê đắt")
        self.assertEqual(nhu_cau, ["neck_shoulder_nape"])
        self.assertEqual(nhu_cau_text, "Cổ - vai - gáy")
        self.assertEqual(nhom, ["pain_relief"])
        self.assertEqual(ly_do, ["price_high"])
        self.assertEqual(san_pham, ["Ghế massage X10"])
        self.assertEqual(giai_doan, "Đang cân nhắc")
        self.assertEqual(muc_do, "Cao")
        self.assertEqual(tt, "Đã gán nhãn")

    def test_unlabelled_conversation_reads_chua_xep_hang(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        loaders.full_rebuild(MART)
        tt, nhu_cau, ly_do = _label(
            conv, "trang_thai_gan_nhan_text", "nhu_cau_ai", "ly_do_khong_mua_ma"
        )
        self.assertEqual(tt, "Chưa xếp hàng")
        # mảng RỖNG chứ không NULL: "chưa gán nhãn" khác "gán nhãn xong, không có nhu cầu nào"
        self.assertEqual(nhu_cau, [])
        self.assertEqual(ly_do, [])

    def test_order_link_by_extracted_phone(self):
        order = mk_order(status=3, order_code="DH-CHAT-1")
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="số em là ...")
        _set_labels(conv, {"sdt_khach": order.customer.phone_normalized})
        loaders.full_rebuild(MART)
        don_id, ma_don, khach_id = _label(conv, "don_id", "ma_don", "khach_id")
        self.assertEqual(don_id, order.id)
        self.assertEqual(ma_don, "DH-CHAT-1")
        self.assertEqual(khach_id, order.customer_id)

    def test_no_order_link_when_phone_absent(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        _set_labels(conv, {"tom_tat": "không nói số"})
        loaders.full_rebuild(MART)
        self.assertEqual(_label(conv, "don_id", "ma_don"), (None, None))
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_hoi_thoai -v2 --noinput
```
Expected: FAIL — `No module named 'apps.ai_mart.infrastructure.sql.chat_hoi_thoai'`.

- [ ] **Step 3: Viết migration `0017_hoi_thoai.py`**

```python
"""ai.hoi_thoai (mart) + ai_stg.hoi_thoai_nhan (hàng đợi & kho nhãn LLM).

34 cột, ĐÚNG thứ tự SELECT của infrastructure/sql/chat_hoi_thoai.py.

ai_stg.hoi_thoai_nhan nằm ở ai_stg vì (1) FDW chỉ đọc được nguồn nên nhãn buộc
phải sống bên desi, (2) hermes_ro KHÔNG có quyền trên ai_stg — extract_payload
thô chứa cả phần rejected, không phải hợp đồng công bố (spec C8, §7).
"""

from django.db import migrations

STG_DDL = """
CREATE TABLE IF NOT EXISTS ai_stg.hoi_thoai_nhan (
    hoi_thoai_id bigint PRIMARY KEY,
    extract_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    extract_status text NOT NULL DEFAULT 'pending',
    extract_error text NOT NULL DEFAULT '',
    extract_attempts int NOT NULL DEFAULT 0,
    model text NOT NULL DEFAULT '',
    noi_dung_hash text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hoi_thoai_nhan_status_idx
    ON ai_stg.hoi_thoai_nhan (extract_status);
COMMENT ON TABLE ai_stg.hoi_thoai_nhan IS
 'Hàng đợi + kết quả gán nhãn LLM cho hội thoại chat. extract_payload giữ nguyên hình dạng {raw, clean, rejected} của callcenter_call_records: chỉ phần clean được đổ lên ai.hoi_thoai, rejected giữ lại để biết từ điển thiếu mã nào. KHÔNG cấp quyền cho hermes_ro.';
"""

DDL = """
CREATE TABLE IF NOT EXISTS ai.hoi_thoai (
    hoi_thoai_id bigint PRIMARY KEY,
    pancake_hoi_thoai_id text,
    page_ten text,
    nen_tang_text text,
    bat_dau timestamptz,
    bat_dau_ngay date,
    ket_thuc timestamptz,
    ket_thuc_ngay date,
    so_tin int,
    so_tin_khach int,
    so_tin_page int,
    co_khach_tra_loi boolean,
    loai_kenh_text text,
    do_bot_xu_ly boolean,
    nhan_tag text[],
    nhan_tag_text text,
    noi_dung text,
    khach_ten text,
    sdt_che text,
    tom_tat_ai text,
    nhu_cau_ai text[],
    nhu_cau_ai_text text,
    nhom_nhu_cau_ai text[],
    ly_do_khong_mua_ma text[],
    ly_do_khong_mua_text text,
    san_pham_quan_tam text[],
    giai_doan_text text,
    muc_do_quan_tam_text text,
    khach_id bigint,
    don_id bigint,
    ma_don text,
    trang_thai_gan_nhan_text text,
    gan_nhan_luc timestamptz,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS hoi_thoai_bat_dau_ngay_idx ON ai.hoi_thoai (bat_dau_ngay);
CREATE INDEX IF NOT EXISTS hoi_thoai_don_id_idx ON ai.hoi_thoai (don_id);
CREATE INDEX IF NOT EXISTS hoi_thoai_nhu_cau_idx ON ai.hoi_thoai USING gin (nhu_cau_ai);

COMMENT ON TABLE ai.hoi_thoai IS
 'Mỗi dòng = 1 hội thoại chat với khách (nguồn Pancake/pages.fm, 6 nền tảng: Facebook, Zalo OA, Zalo cá nhân, TikTok Shop, Shopee, YouTube). Đây là bảng trả lời "khách CẦN gì" và "vì sao khách KHÔNG mua" theo lời khách nói trong chat — so trực tiếp được với ai.don_hang.ly_do_khong_chot_ma (sale ghi trên phiếu) vì dùng CHUNG bộ mã. KHÔNG quy được về nhân viên: nguồn không lưu admin_id (spec §1 F2). Phần lớn lưu lượng gửi ra là chatbot — luôn lọc do_bot_xu_ly khi đọc số. RIÊNG noi_dung và tom_tat_ai là văn bản thô CHƯA che PII — xem COMMENT từng cột.';
COMMENT ON COLUMN ai.hoi_thoai.hoi_thoai_id IS 'Id hội thoại trong DB crawler (conversations.id) — nối với ai.tin_nhan.hoi_thoai_id. Chỉ dùng đối chiếu, không hiển thị cho người dùng cuối.';
COMMENT ON COLUMN ai.hoi_thoai.pancake_hoi_thoai_id IS 'Id hội thoại phía Pancake/pages.fm — dùng để mở đúng hội thoại trên giao diện Pancake. CHỈ duy nhất trong phạm vi 1 page, KHÔNG duy nhất toàn hệ thống.';
COMMENT ON COLUMN ai.hoi_thoai.page_ten IS 'Tên page/gian hàng diễn ra hội thoại — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.hoi_thoai.nen_tang_text IS 'Nhãn tiếng Việt nền tảng chat (tra ai.tu_dien_ma nhóm nen_tang_chat): Facebook / Zalo OA / Zalo cá nhân / TikTok Shop / Shopee / YouTube — NULL nếu nền tảng lạ chưa có trong từ điển';
COMMENT ON COLUMN ai.hoi_thoai.bat_dau IS 'Thời điểm tin ĐẦU TIÊN của hội thoại (giờ UTC) — NULL nếu hội thoại chưa có tin nào có nội dung chữ';
COMMENT ON COLUMN ai.hoi_thoai.bat_dau_ngay IS 'Ngày bắt đầu hội thoại theo giờ Việt Nam (Asia/Ho_Chi_Minh) — dùng để lọc/group theo ngày';
COMMENT ON COLUMN ai.hoi_thoai.ket_thuc IS 'Thời điểm tin CUỐI CÙNG (giờ UTC) — hội thoại vẫn có thể còn tiếp diễn, đây không phải mốc "đã đóng"';
COMMENT ON COLUMN ai.hoi_thoai.ket_thuc_ngay IS 'Ngày tin cuối theo giờ Việt Nam (Asia/Ho_Chi_Minh)';
COMMENT ON COLUMN ai.hoi_thoai.so_tin IS 'Tổng số tin CÓ NỘI DUNG CHỮ trong hội thoại (tin đính kèm thuần không tính) — 0 nếu hội thoại rỗng';
COMMENT ON COLUMN ai.hoi_thoai.so_tin_khach IS 'Số tin do KHÁCH gửi — chỉ báo quan trọng nhất về "hội thoại thật": đo 04/09/2026 chỉ 18.516/94.902 hội thoại có từ 2 lượt khách nói trở lên';
COMMENT ON COLUMN ai.hoi_thoai.so_tin_page IS 'Số tin gửi ra từ page — GỘP cả nhân viên lẫn chatbot, không tách được (spec §1 F2)';
COMMENT ON COLUMN ai.hoi_thoai.co_khach_tra_loi IS 'Khách có gửi ít nhất 1 tin hay không — false nghĩa là page nhắn một chiều, KHÔNG có dữ liệu nhu cầu để đọc';
COMMENT ON COLUMN ai.hoi_thoai.loai_kenh_text IS 'Nhãn tiếng Việt loại kênh hội thoại (tra ai.tu_dien_ma nhóm loai_tin_chat): Tin nhắn (INBOX, 65.263) / Bình luận (COMMENT, 28.693) / Đánh giá (RATING, 946) — số đo 04/09/2026';
COMMENT ON COLUMN ai.hoi_thoai.do_bot_xu_ly IS 'Hội thoại được gắn thẻ chatbot (thẻ bắt đầu bằng "BOT-" hoặc "TEST ") — LUÔN lọc cột này khi thống kê nhu cầu: lưu lượng bot là lý do tin gửi ra gấp ~3,8 lần tin khách gửi';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag IS 'Mảng tên thẻ nhân viên/hệ thống gắn cho hội thoại trên Pancake — mảng rỗng nếu chưa gắn thẻ nào';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag_text IS 'Các thẻ nối bằng dấu phẩy, tiện đọc/tìm — NULL nếu chưa gắn thẻ nào';
COMMENT ON COLUMN ai.hoi_thoai.noi_dung IS 'Toàn văn hội thoại ghép theo thứ tự thời gian, mỗi dòng có tiền tố vai trò "Khách: " hoặc "Page: ". CHƯA CHE PII: khách gõ thẳng SĐT và địa chỉ vào chat. Ngoại lệ có chủ đích của luật che PII (spec §4.6), cùng chuẩn với ai.cuoc_goi.transcript. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời. NULL nếu hội thoại chưa có tin nào có nội dung chữ.';
COMMENT ON COLUMN ai.hoi_thoai.khach_ten IS 'Tên khách theo hồ sơ Pancake (thường là tên tài khoản mạng xã hội, KHÔNG phải tên trên đơn) — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.hoi_thoai.sdt_che IS 'SĐT trong hồ sơ Pancake đã che giữa (0912***678) — CỰC KỲ THƯA: đo 04/09/2026 chỉ 113/66.277 hồ sơ khách có SĐT. NULL không có nghĩa là khách không để lại số; số thường nằm trong nội dung tin.';
COMMENT ON COLUMN ai.hoi_thoai.tom_tat_ai IS 'Tóm tắt hội thoại do AI trích (extract_payload->clean->tom_tat) — NULL nếu chưa gán nhãn. CHƯA CHE PII: bản tóm tắt có thể lặp lại SĐT/địa chỉ khách gõ trong chat — cùng ngoại lệ với noi_dung, KHÔNG trích PII từ cột này ra câu trả lời.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai IS 'Mảng MÃ loại nhu cầu do AI đọc từ hội thoại (bộ mã need_types — CÙNG bộ mã sale tick trên phiếu, tra nhãn ở ai.tu_dien_ma nhóm nhu_cau). Mảng rỗng = chưa gán nhãn HOẶC khách không nói nhu cầu nào — phân biệt bằng trang_thai_gan_nhan_text.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai_text IS 'Các nhãn nhu cầu tiếng Việt nối bằng dấu phẩy — NULL nếu không có nhu cầu nào';
COMMENT ON COLUMN ai.hoi_thoai.nhom_nhu_cau_ai IS 'Mảng MÃ nhóm nhu cầu (need_groups) suy ra từ nhu_cau_ai — tra nhãn ở ai.tu_dien_ma nhóm nhom_nhu_cau. Dùng để gom nhóm khi báo cáo cấp cao.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_ma IS 'Mảng MÃ lý do khách không mua theo lời khách nói trong chat — DÙNG CHUNG bộ mã với ai.don_hang.ly_do_khong_chot_ma (sale ghi trên phiếu), nên so trực tiếp được "khách nói gì" với "sale ghi gì". Tra nhãn ở ai.tu_dien_ma nhóm ly_do_khong_chot.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_text IS 'Các nhãn lý do không mua tiếng Việt nối bằng dấu phẩy — NULL nếu không có lý do nào';
COMMENT ON COLUMN ai.hoi_thoai.san_pham_quan_tam IS 'Mảng tên sản phẩm khách nhắc tới, TEXT TỰ DO do AI trích (không phải mã sản phẩm, KHÔNG nối được với ai.san_pham) — dùng để đọc, không dùng để join';
COMMENT ON COLUMN ai.hoi_thoai.giai_doan_text IS 'Nhãn tiếng Việt giai đoạn hội thoại do AI phân loại (tra ai.tu_dien_ma nhóm giai_doan_chat): Hỏi giá / Hỏi thông tin sản phẩm / Đang cân nhắc / Từ chối / Đã chốt / Sau bán - bảo hành / Không xác định. "Đã chốt" là NHẬN ĐỊNH CỦA AI TỪ LỜI KHÁCH, không phải đơn hàng thật — muốn biết đơn thật thì xem don_id.';
COMMENT ON COLUMN ai.hoi_thoai.muc_do_quan_tam_text IS 'Nhãn tiếng Việt mức độ quan tâm do AI đánh giá (Cao / Trung bình / Thấp) — NHẬN ĐỊNH chủ quan của mô hình, không phải chỉ số đo được';
COMMENT ON COLUMN ai.hoi_thoai.khach_id IS 'Id khách hàng trong CRM, khớp qua SĐT mà AI bóc được từ nội dung chat — NULL khi không khớp được (xem COMMENT don_id)';
COMMENT ON COLUMN ai.hoi_thoai.don_id IS 'Id đơn hàng GẦN NHẤT của khách khớp theo SĐT AI bóc từ nội dung chat (đơn mới nhất theo ngày tạo). ĐỘ PHỦ RẤT THẤP và là best-effort: nguồn không có khoá nối chat↔đơn, chỉ 113/66.277 hồ sơ khách có SĐT nên gần như toàn bộ phụ thuộc việc khách gõ số vào chat (spec §1 F3, C7). don_id IS NULL TUYỆT ĐỐI KHÔNG có nghĩa là "khách không mua" — chỉ có nghĩa là không nối được.';
COMMENT ON COLUMN ai.hoi_thoai.ma_don IS 'Mã đơn hiển thị trên CRM của đơn ở cột don_id — NULL khi không khớp được đơn';
COMMENT ON COLUMN ai.hoi_thoai.trang_thai_gan_nhan_text IS 'Nhãn tiếng Việt trạng thái gán nhãn AI (tra ai.tu_dien_ma nhóm trang_thai_gan_nhan): Chưa xếp hàng / Chờ gán nhãn / Đang gán nhãn / Đã gán nhãn / Gán nhãn lỗi / Bỏ qua (quá ngắn). Chỉ dòng "Đã gán nhãn" mới được coi là đã đọc hết hội thoại — mọi cột nhãn AI khác trạng thái này đều rỗng vì CHƯA CHẠY, không phải vì khách không nói.';
COMMENT ON COLUMN ai.hoi_thoai.gan_nhan_luc IS 'Thời điểm gán nhãn AI thành công gần nhất — NULL nếu chưa gán nhãn xong';
COMMENT ON COLUMN ai.hoi_thoai.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.hoi_thoai;
DROP TABLE IF EXISTS ai_stg.hoi_thoai_nhan;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0016_tin_nhan")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=STG_DDL + DDL, reverse_sql=DROP)]
        )
    ]
```

- [ ] **Step 4: Viết `infrastructure/sql/chat_hoi_thoai.py`**

```python
"""1 dòng/hội thoại chat. Nguồn: foreign table ai_stg.chat_* + nhãn LLM ở
ai_stg.hoi_thoai_nhan (spec C3, C8).

Dùng LATERAL thay vì CTE gộp: upsert tăng dần chỉ chạm đúng hội thoại trong
danh sách id, đi bằng index idx_messages_conv_inserted của nguồn. CTE gộp sẽ
quét toàn bộ 769k tin mỗi nhịp 30' — đúng cái nợ spec §9 ghi là ngưỡng đổi ý.

Aggregate-only LATERAL luôn trả đúng 1 dòng, nên hội thoại rỗng vẫn còn trong
mart với so_tin = 0 (coalesce), KHÔNG bị rơi mất.
"""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.common.order_scope import sql_exclude_mirror_orders

_CANDIDATES = """
SELECT c.id FROM ai_stg.chat_conversations c WHERE c.updated_at > %(cutoff)s
UNION
SELECT n.hoi_thoai_id FROM ai_stg.hoi_thoai_nhan n WHERE n.updated_at > %(cutoff)s
"""

# Cùng scope đơn với ai.don_hang: đơn xoá mềm / đơn mirror / đơn gốc đã có bản
# khôi phục đều không được coi là đơn thật.
_ORDER_SCOPE = (
    f"o.deleted_at IS NULL AND {sql_exclude_mirror_orders('o')}"
    " AND NOT EXISTS (SELECT 1 FROM orders r"
    "                 WHERE r.restored_from_id = o.id AND r.deleted_at IS NULL)"
)


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
    NULLIF(btrim(coalesce(cu.name, '')), '') AS khach_ten,
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
        array_agg(t.name ORDER BY t.name) AS nhan_tag,
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
```

- [ ] **Step 5: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_hoi_thoai -v2 --noinput
```
Expected: 7 tests PASS. Nếu `nhu_cau_ai_text` NULL → từ điển Task 1 chưa seed; nếu `nhom_nhu_cau_ai` rỗng → `need_types.group_id` chưa có dữ liệu trong DB test, kiểm bằng `SELECT count(*) FROM need_types`.

- [ ] **Step 6: Chạy contract + masking**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_contract apps.ai_mart.tests.test_masking -v2 --noinput
```
Expected: `test_contract` PASS. `test_masking` **có thể FAIL** ở `hoi_thoai.noi_dung` / `tin_nhan.noi_dung` — Task 8 khai báo ngoại lệ; nếu đỏ đúng ba cột đó thì ghi lại và đi tiếp.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ai_mart/migrations/0017_hoi_thoai.py \
        backend/apps/ai_mart/infrastructure/sql/chat_hoi_thoai.py \
        backend/apps/ai_mart/tests/test_chat_hoi_thoai.py
git commit -m "feat(ai_mart): bảng ai.hoi_thoai + hàng đợi nhãn ai_stg.hoi_thoai_nhan"
```

---

### Task 5: Nối hai mart vào ETL + chứng minh lỗi thoát ra ngoài

Spec §5.2, §5.3, C2. Sau task này, hai bảng tự nạp theo nhịp 30' và rebuild đêm, và **nguồn chết là job đỏ** chứ không xanh giả.

**Files:**
- Modify: `backend/apps/ai_mart/application/etl_service.py`
- Test: `backend/apps/ai_mart/tests/test_chat_etl.py`

**Interfaces:**
- Consumes: `chat_tin_nhan.MART` (Task 3), `chat_hoi_thoai.MART` (Task 4), `need_dictionary.refresh_need_dictionary` (Task 1).
- Produces: `HOT_MARTS` có thêm 2 phần tử ở **cuối**; `rebuild_all()` gọi `refresh_need_dictionary` trước vòng lặp.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_etl.py`:

```python
"""Nhịp ETL của hai bảng chat.

Bài học sự cố statement timeout 2026-07-22 + quyết định C2: nguồn không đọc
được thì job PHẢI raise. FDW chết trên prod và bảng nguồn biến mất trong test
cùng biểu hiện: relation/connection lỗi ngay ở câu SELECT đầu tiên.
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.application import etl_service
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.tests import chat_helpers as ch


class ChatEtlTests(TestCase):
    def _counts(self):
        with connection.cursor() as cur:
            cur.execute("SELECT (SELECT count(*) FROM ai.hoi_thoai),"
                        " (SELECT count(*) FROM ai.tin_nhan)")
            return cur.fetchone()

    def test_chat_marts_are_in_hot_group(self):
        names = [m.name for m in etl_service.HOT_MARTS]
        self.assertIn("ai.hoi_thoai", names)
        self.assertIn("ai.tin_nhan", names)

    def test_incremental_sync_equals_full_rebuild(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="tin 1")
        ch.mk_message(conv, page, direction="out", content="tin 2")

        etl_service.rebuild_all()
        full = self._counts()

        # Đẩy watermark về quá khứ để mọi hội thoại thành "vừa đổi"
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now() - timedelta(days=2))
        etl_service.sync_hot()
        self.assertEqual(self._counts(), full)

    def test_sync_raises_when_source_unreachable(self):
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now())
        with connection.cursor() as cur:
            cur.execute("DROP TABLE ai_stg.chat_conversations CASCADE")
        with self.assertRaises(etl_service.EtlFailed):
            etl_service.sync_hot()

    def test_failed_sync_is_logged_as_failed_not_ok(self):
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now())
        with connection.cursor() as cur:
            cur.execute("DROP TABLE ai_stg.chat_conversations CASCADE")
        with self.assertRaises(etl_service.EtlFailed):
            etl_service.sync_hot()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT trang_thai FROM ai_meta.nhat_ky_chay"
                " WHERE ten_bang IN ('ai.hoi_thoai', 'ai.tin_nhan')"
                " ORDER BY bat_dau DESC LIMIT 1"
            )
            (status,) = cur.fetchone()
        self.assertEqual(status, "failed")

    def test_rebuild_refreshes_need_dictionary(self):
        from apps.orders.models import NeedGroup, NeedType

        group = NeedGroup.objects.create(code="grp_rb", name_vi="Nhóm rebuild", sort_order=902)
        NeedType.objects.create(code="nc_rb", group=group, name_vi="Nhu cầu rebuild",
                                sort_order=912, is_active=True)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhan FROM ai.tu_dien_ma WHERE nhom_ma = 'nhu_cau' AND ma = 'nc_rb'"
            )
            row = cur.fetchone()
        self.assertEqual(row and row[0], "Nhu cầu rebuild")
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_etl -v2 --noinput
```
Expected: FAIL — `ai.hoi_thoai` không có trong `HOT_MARTS`.

- [ ] **Step 3: Sửa `etl_service.py`**

Thêm vào khối import:

```python
from apps.ai_mart.infrastructure.sql import (
    chat_hoi_thoai,
    chat_tin_nhan,
    chi_phi_quang_cao,
    cuoc_goi,
    ...
)
```

Thêm hai mart vào cuối `HOT_MARTS`:

```python
HOT_MARTS: tuple[MartTable, ...] = (
    don_hang.MART,
    dong_don_hang.MART,
    khach_hang.MART,
    cuoc_goi.MART,
    # Hai bảng chat đọc qua cầu postgres_fdw (spec 2026-09-04 C1). Đặt CUỐI vì
    # chúng là bảng duy nhất phụ thuộc một DB NGOÀI: nguồn chết thì 4 bảng trên
    # đã nạp xong, không kéo nhau đổ. hoi_thoai đứng sau tin_nhan chỉ để giữ
    # thứ tự đọc, hai bảng không phụ thuộc nhau.
    chat_tin_nhan.MART,
    chat_hoi_thoai.MART,
)
```

Trong `rebuild_all()`, ngay sau `results: dict[str, int] = {}`:

```python
    # Từ điển nhu cầu bám theo bảng need_types — admin thêm mã mới lúc nào cũng
    # được, rebuild đêm là chỗ rẻ nhất để mart không sinh mã mồ côi.
    with connections[loaders.TARGET_ALIAS].cursor() as cur:
        refresh_need_dictionary(cur)
```

kèm import `from django.db import connections` và
`from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary`.

- [ ] **Step 4: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_etl -v2 --noinput
```
Expected: 5 tests PASS.

- [ ] **Step 5: Chạy toàn bộ `apps.ai_mart`**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart -v2 --noinput
```
Expected: PASS trừ `test_masking` (Task 8 xử lý).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/ai_mart/application/etl_service.py \
        backend/apps/ai_mart/tests/test_chat_etl.py
git commit -m "feat(ai_mart): nạp ai.hoi_thoai/ai.tin_nhan theo nhịp nóng + rebuild đêm"
```

---

### Task 6: Hợp đồng gán nhãn LLM

Spec §6. Tách hợp đồng (mã nào hợp lệ, lọc ra sao) khỏi phần gọi API — đúng cách `apps/orders/ai_extract_contract.py` tách khỏi `apps/callcenter/extract.py`. Task này **không** gọi mạng, test chạy offline.

**Files:**
- Create: `backend/apps/ai_mart/domain/chat_extract.py`
- Test: `backend/apps/ai_mart/tests/test_chat_extract_contract.py`

**Interfaces:**
- Consumes: `chat_dictionary.GIAI_DOAN_CHAT`, `MUC_DO_QUAN_TAM` (Task 1); `apps.orders.crm_catalog.NOT_CLOSED_REASON_OPTIONS`; bảng `need_types`; `apps.customers.phone_utils.normalize_phone`.
- Produces:
  - `chat_extract.load_need_labels() -> dict[str, str]` — mã→nhãn đọc từ `need_types` (chỉ `is_active`).
  - `chat_extract.load_reason_labels() -> dict[str, str]`
  - `chat_extract.build_chat_response_schema() -> dict`
  - `chat_extract.build_chat_prompt(noi_dung: str) -> str`
  - `chat_extract.normalize_chat_extracted(raw: dict) -> tuple[dict, dict]` — `(clean, rejected)` theo đúng hợp đồng khoá đã chốt ở Task 4.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_extract_contract.py`:

```python
from django.test import TestCase

from apps.ai_mart.domain import chat_extract


class ChatExtractContractTests(TestCase):
    def test_invented_codes_go_to_rejected_not_clean(self):
        clean, rejected = chat_extract.normalize_chat_extracted({
            "tom_tat": "khách chê đắt",
            "nhu_cau": ["neck_shoulder_nape", "mã_tự_chế"],
            "ly_do_khong_mua": ["price_high", "lý_do_bịa"],
            "giai_doan": "giai_doan_bia",
            "muc_do_quan_tam": "cao",
        })
        self.assertEqual(clean["nhu_cau"], ["neck_shoulder_nape"])
        self.assertEqual(rejected["nhu_cau"], ["mã_tự_chế"])
        self.assertEqual(clean["ly_do_khong_mua"], ["price_high"])
        self.assertEqual(rejected["ly_do_khong_mua"], ["lý_do_bịa"])
        self.assertNotIn("giai_doan", clean)
        self.assertEqual(rejected["giai_doan"], "giai_doan_bia")
        self.assertEqual(clean["muc_do_quan_tam"], "cao")

    def test_phone_is_normalized_before_storing(self):
        clean, _ = chat_extract.normalize_chat_extracted({"sdt_khach": "+84 912 345 678"})
        self.assertEqual(clean["sdt_khach"], "0912345678")

    def test_unparseable_phone_is_dropped_not_stored_raw(self):
        # Nối đơn join thẳng customers.phone_normalized — rác lọt vào là join sai.
        clean, rejected = chat_extract.normalize_chat_extracted({"sdt_khach": "gọi em nhé"})
        self.assertNotIn("sdt_khach", clean)
        self.assertEqual(rejected["sdt_khach"], "gọi em nhé")

    def test_product_names_are_trimmed_and_capped(self):
        clean, _ = chat_extract.normalize_chat_extracted(
            {"san_pham_quan_tam": ["  Ghế X10  ", "", "Ghế X10", "A" * 300]}
        )
        self.assertEqual(clean["san_pham_quan_tam"][0], "Ghế X10")
        self.assertNotIn("", clean["san_pham_quan_tam"])
        self.assertEqual(len(clean["san_pham_quan_tam"]), 3)       # khử trùng lặp
        self.assertLessEqual(len(clean["san_pham_quan_tam"][-1]), 200)

    def test_schema_enums_come_from_the_dictionary(self):
        schema = chat_extract.build_chat_response_schema()
        self.assertIn("hoi_gia", schema["properties"]["giai_doan"]["enum"])
        self.assertIn(
            "neck_shoulder_nape", schema["properties"]["nhu_cau"]["items"]["enum"]
        )

    def test_prompt_forbids_guessing_and_lists_codes(self):
        prompt = chat_extract.build_chat_prompt("Khách: đau vai\nPage: dạ")
        self.assertIn("TUYỆT ĐỐI không suy đoán", prompt)
        self.assertIn("neck_shoulder_nape", prompt)
        self.assertIn("Khách: đau vai", prompt)
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_extract_contract -v2 --noinput
```
Expected: FAIL — `No module named 'apps.ai_mart.domain.chat_extract'`.

- [ ] **Step 3: Viết `domain/chat_extract.py`**

```python
"""Hợp đồng dữ liệu giữa Gemini và mart hội thoại.

KHÔNG TIN enum của LLM: response_schema chỉ là gợi ý, model vẫn trả mã lạ.
Mọi giá trị đi qua normalize_chat_extracted; phần bị loại giữ ở `rejected` để
biết từ điển đang thiếu khái niệm nào — đó là tín hiệu nghiệp vụ, không phải
rác (spec §6, §9).

Bộ mã lấy TỪ BẢNG need_types và từ crm_catalog, không chép tay: nhãn chat phải
so được với nhãn sale nhập trên phiếu, chép tay là hai kho số lệch nhau (C4).
"""

from __future__ import annotations

from apps.ai_mart.domain.chat_dictionary import GIAI_DOAN_CHAT, MUC_DO_QUAN_TAM
from apps.customers.phone_utils import normalize_phone
from apps.orders.crm_catalog import NOT_CLOSED_REASON_OPTIONS

TEXT_MAX_LEN = 200
MAX_PRODUCTS = 10

GIAI_DOAN_LABELS = dict(GIAI_DOAN_CHAT)
MUC_DO_LABELS = dict(MUC_DO_QUAN_TAM)


def load_need_labels() -> dict[str, str]:
    """Mã nhu cầu CÒN HIỆU LỰC. Mã đã tắt vẫn có nhãn trong từ điển (để đọc dữ
    liệu cũ) nhưng KHÔNG được đưa vào prompt — không mời model gán nhãn chết."""
    from apps.orders.models import NeedType

    return dict(
        NeedType.objects.filter(is_active=True)
        .order_by("sort_order", "code")
        .values_list("code", "name_vi")
    )


def load_reason_labels() -> dict[str, str]:
    return {o.code: o.label for o in NOT_CLOSED_REASON_OPTIONS}


_PROMPT_HEADER = (
    "Đây là một hội thoại chat giữa page bán hàng và khách hàng, bằng tiếng Việt, "
    "về sản phẩm ghế/máy massage. Mỗi dòng có tiền tố vai trò: 'Khách:' là lời "
    "khách, 'Page:' là lời nhân viên hoặc chatbot.\n\n"
    "Hãy trích xuất NHU CẦU của khách và LÝ DO khách chưa/không mua.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- CHỈ căn cứ vào dòng bắt đầu bằng 'Khách:'. Lời của Page là câu hỏi gợi ý, "
    "KHÔNG phải nhu cầu của khách.\n"
    "- CHỈ điền khi khách nói rõ. TUYỆT ĐỐI không suy đoán từ ngữ cảnh.\n"
    "- Không chắc chắn thì để null (với mảng thì để mảng rỗng).\n"
    "- Mỗi field có điền giá trị PHẢI kèm một mục trong `evidence` với trích dẫn "
    "NGUYÊN VĂN câu nói của khách làm căn cứ.\n"
    "- Chỉ dùng đúng các mã dưới đây, không tự chế mã mới.\n"
    "- sdt_khach: chỉ điền khi khách tự gõ số điện thoại trong chat.\n\n"
    "DANH MỤC MÃ HỢP LỆ (mã = ý nghĩa):\n"
)


def _catalog_block(name: str, labels: dict[str, str], note: str = "") -> str:
    options = "; ".join(f"{code} = {label}" for code, label in labels.items())
    header = f"{name} ({note})" if note else name
    return f"- {header}: {options}"


def build_prompt_catalog_text() -> str:
    return "\n".join([
        _catalog_block("nhu_cau", load_need_labels(), "nhu cầu khách nói ra, chọn nhiều được"),
        _catalog_block("ly_do_khong_mua", load_reason_labels(),
                       "chỉ điền khi khách NÊU lý do chưa mua"),
        _catalog_block("giai_doan", GIAI_DOAN_LABELS, "chọn đúng MỘT"),
        _catalog_block("muc_do_quan_tam", MUC_DO_LABELS, "chọn đúng MỘT"),
        "- tom_tat: 1-3 câu tiếng Việt tóm tắt hội thoại theo góc nhìn khách cần gì.",
        "- san_pham_quan_tam: tên sản phẩm khách nhắc tới, nguyên văn, tối đa 10 mục.",
        "- sdt_khach: số điện thoại khách tự gõ trong chat, dạng số, không kèm chữ.",
    ])


def build_chat_prompt(noi_dung: str) -> str:
    return f"{_PROMPT_HEADER}{build_prompt_catalog_text()}\n\nHỘI THOẠI:\n{noi_dung}"


def build_chat_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "tom_tat": {"type": "string", "nullable": True},
            "nhu_cau": {"type": "array",
                        "items": {"type": "string", "enum": sorted(load_need_labels())}},
            "ly_do_khong_mua": {"type": "array",
                                "items": {"type": "string", "enum": sorted(load_reason_labels())}},
            "san_pham_quan_tam": {"type": "array", "items": {"type": "string"}},
            "giai_doan": {"type": "string", "enum": sorted(GIAI_DOAN_LABELS), "nullable": True},
            "muc_do_quan_tam": {"type": "string", "enum": sorted(MUC_DO_LABELS),
                                "nullable": True},
            "sdt_khach": {"type": "string", "nullable": True},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string",
                                  "enum": ["nhu_cau", "ly_do_khong_mua", "giai_doan",
                                           "muc_do_quan_tam", "san_pham_quan_tam"]},
                        "quote": {"type": "string"},
                    },
                    "required": ["field", "quote"],
                },
            },
        },
    }


def _clean_code_array(value, labels, clean, rejected, key) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        rejected[key] = value
        return
    kept: list[str] = []
    dropped: list = []
    for code in value:
        if isinstance(code, str) and code in labels and code not in kept:
            kept.append(code)
        elif code not in kept and code not in dropped:
            dropped.append(code)
    if kept:
        clean[key] = kept
    if dropped:
        rejected[key] = dropped


def _clean_scalar(value, labels, clean, rejected, key) -> None:
    if value is None:
        return
    if isinstance(value, str) and value in labels:
        clean[key] = value
    else:
        rejected[key] = value


def normalize_chat_extracted(raw: dict) -> tuple[dict, dict]:
    """Lọc output LLM về đúng hợp đồng khoá của ai.hoi_thoai. Trả ``(clean, rejected)``."""
    clean: dict = {}
    rejected: dict = {}

    tom_tat = raw.get("tom_tat")
    if isinstance(tom_tat, str) and tom_tat.strip():
        clean["tom_tat"] = tom_tat.strip()[:2000]
    elif tom_tat is not None:
        rejected["tom_tat"] = tom_tat

    _clean_code_array(raw.get("nhu_cau"), load_need_labels(), clean, rejected, "nhu_cau")
    _clean_code_array(raw.get("ly_do_khong_mua"), load_reason_labels(), clean, rejected,
                      "ly_do_khong_mua")
    _clean_scalar(raw.get("giai_doan"), GIAI_DOAN_LABELS, clean, rejected, "giai_doan")
    _clean_scalar(raw.get("muc_do_quan_tam"), MUC_DO_LABELS, clean, rejected, "muc_do_quan_tam")

    products = raw.get("san_pham_quan_tam")
    if isinstance(products, list):
        kept: list[str] = []
        for item in products:
            if not isinstance(item, str) or not item.strip():
                continue
            text = item.strip()[:TEXT_MAX_LEN]
            if text not in kept:
                kept.append(text)
        if kept:
            clean["san_pham_quan_tam"] = kept[:MAX_PRODUCTS]
    elif products is not None:
        rejected["san_pham_quan_tam"] = products

    # Chuẩn hoá NGAY tại đây: cột don_id join thẳng customers.phone_normalized,
    # để nguyên "+84 912..." là join trượt 100% mà không ai thấy lỗi.
    phone = raw.get("sdt_khach")
    if isinstance(phone, str) and phone.strip():
        normalized = normalize_phone(phone)
        if normalized:
            clean["sdt_khach"] = normalized
        else:
            rejected["sdt_khach"] = phone
    elif phone is not None:
        rejected["sdt_khach"] = phone

    evidence = raw.get("evidence")
    if isinstance(evidence, list):
        clean["evidence"] = [
            {"field": item["field"], "quote": item["quote"].strip()[:TEXT_MAX_LEN]}
            for item in evidence
            if isinstance(item, dict)
            and isinstance(item.get("field"), str)
            and isinstance(item.get("quote"), str)
            and item["quote"].strip()
        ]
    return clean, rejected
```

- [ ] **Step 4: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_extract_contract -v2 --noinput
```
Expected: 6 tests PASS. Nếu `test_schema_enums_come_from_the_dictionary` đỏ vì `need_types` rỗng trong DB test → thêm `NeedType.objects.create(...)` vào test đó thay vì nới hợp đồng.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/ai_mart/domain/chat_extract.py \
        backend/apps/ai_mart/tests/test_chat_extract_contract.py
git commit -m "feat(ai_mart): hợp đồng gán nhãn LLM cho hội thoại chat"
```

---

### Task 7: Chạy gán nhãn — xếp hàng, gọi Gemini, ghi nhãn

Spec §6. Hàng đợi + vòng lặp lô, khuôn bê từ `apps/callcenter/extract.py` (cooldown khi 429, lỗi 1 dòng không chặn dòng khác, không log nội dung chat).

**Files:**
- Create: `backend/apps/ai_mart/infrastructure/chat_extract_runner.py`
- Create: `backend/apps/ai_mart/management/commands/ai_mart_chat_extract.py`
- Modify: `backend/apps/ai_mart/infrastructure/tasks.py`
- Modify: `backend/config/settings_parts/base.py` (4 env `AI_CHAT_EXTRACT_*`)
- Modify: `backend/config/settings_parts/celery.py` (route + beat)
- Test: `backend/apps/ai_mart/tests/test_chat_extract_runner.py`

**Interfaces:**
- Consumes: `chat_extract.build_chat_prompt`, `build_chat_response_schema`, `normalize_chat_extracted` (Task 6); bảng `ai.hoi_thoai` (Task 4).
- Produces:
  - `chat_extract_runner.enqueue_pending() -> int` — chèn/đánh thức dòng trong `ai_stg.hoi_thoai_nhan` cho hội thoại đủ điều kiện; `noi_dung_hash` đổi thì gán nhãn lại, không đổi thì thôi.
  - `chat_extract_runner.run_batch(limit: int | None = None) -> dict` — khoá lô, gọi Gemini, ghi `extract_payload`. Trả `{"claimed", "done", "errors", "skipped", "quota_stopped"}`.
  - `chat_extract_runner.ChatExtractError` (kế thừa khuôn `transient` / `quota_exhausted`).
  - Task Celery `apps.ai_mart.tasks.extract_chat_labels`.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/ai_mart/tests/test_chat_extract_runner.py`:

```python
"""Vòng chạy gán nhãn — mock Gemini, không gọi mạng."""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings

from apps.ai_mart.infrastructure import chat_extract_runner as runner
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch

_GOOD = '{"tom_tat": "khách đau vai", "nhu_cau": ["neck_shoulder_nape"], "giai_doan": "hoi_gia"}'


def _queue_row(conv_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT extract_status, extract_payload, noi_dung_hash, model"
            " FROM ai_stg.hoi_thoai_nhan WHERE hoi_thoai_id = %s", [conv_id]
        )
        return cur.fetchone()


@override_settings(AI_CHAT_EXTRACT_ENABLED=True, GEMINI_API_KEY="test-key",
                   AI_CHAT_EXTRACT_MIN_CHARS=1)
class ChatExtractRunnerTests(TestCase):
    def _conversation(self, content="đau vai gáy quá"):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, direction="in", content=content)
        loaders.full_rebuild(MART)
        return conv

    def test_enqueue_only_conversations_with_customer_messages(self):
        with_customer = self._conversation()
        page = ch.mk_page()
        silent = ch.mk_conversation(page)
        ch.mk_message(silent, page, direction="out", content="chào bạn")
        loaders.full_rebuild(MART)

        runner.enqueue_pending()
        self.assertIsNotNone(_queue_row(with_customer))
        # page nhắn một chiều: không có gì để đọc, không được đốt token
        self.assertIsNone(_queue_row(silent))

    def test_run_batch_stores_clean_payload_and_marks_done(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["done"], 1)
        status, payload, _, model = _queue_row(conv)
        self.assertEqual(status, "done")
        self.assertEqual(payload["clean"]["nhu_cau"], ["neck_shoulder_nape"])
        self.assertIn("raw", payload)
        self.assertIn("rejected", payload)
        self.assertTrue(model)

    def test_labels_reach_the_mart_after_rebuild(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            runner.run_batch(limit=10)
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhu_cau_ai_text, giai_doan_text, trang_thai_gan_nhan_text"
                " FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [conv]
            )
            nhu_cau, giai_doan, trang_thai = cur.fetchone()
        self.assertEqual(nhu_cau, "Cổ - vai - gáy")
        self.assertEqual(giai_doan, "Hỏi giá")
        self.assertEqual(trang_thai, "Đã gán nhãn")

    def test_unchanged_conversation_is_not_relabelled(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            runner.run_batch(limit=10)
            runner.enqueue_pending()          # chạy lại: nội dung không đổi
            runner.run_batch(limit=10)
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(_queue_row(conv)[0], "done")

    def test_changed_conversation_is_relabelled(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            runner.run_batch(limit=10)
            page_id = ch.mk_page()
            ch.mk_message(conv, page_id, direction="in", content="thêm câu mới")
            loaders.full_rebuild(MART)
            runner.enqueue_pending()
            runner.run_batch(limit=10)
        self.assertEqual(gen.call_count, 2)

    def test_api_error_marks_row_failed_without_blocking_others(self):
        first = self._conversation("câu một")
        second = self._conversation("câu hai")
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", side_effect=[RuntimeError("bùm"), _GOOD]):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["done"], 1)
        statuses = {_queue_row(first)[0], _queue_row(second)[0]}
        self.assertEqual(statuses, {"pending", "done"})   # lỗi 1 lần → về pending, còn lượt thử

    def test_disabled_flag_stops_everything(self):
        self._conversation()
        with override_settings(AI_CHAT_EXTRACT_ENABLED=False):
            self.assertEqual(runner.run_batch(limit=10)["claimed"], 0)
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_extract_runner -v2 --noinput
```
Expected: FAIL — `No module named 'apps.ai_mart.infrastructure.chat_extract_runner'`.

- [ ] **Step 3: Thêm settings**

`backend/config/settings_parts/base.py`, cạnh khối `CHAT_CRAWLER_DB_*`:

```python
# Gán nhãn hội thoại chat bằng Gemini (spec 2026-09-04 §6). Dùng chung
# GEMINI_API_KEY / GEMINI_EXTRACT_MODEL / GEMINI_QUOTA_COOLDOWN_MINUTES với
# luồng cuộc gọi — cùng nhà cung cấp, cùng hạn mức.
AI_CHAT_EXTRACT_ENABLED = os.getenv("AI_CHAT_EXTRACT_ENABLED", "0") in ("1", "true", "True")
AI_CHAT_EXTRACT_BATCH = int(os.getenv("AI_CHAT_EXTRACT_BATCH", "20"))
AI_CHAT_EXTRACT_MAX_ATTEMPTS = int(os.getenv("AI_CHAT_EXTRACT_MAX_ATTEMPTS", "3"))
# Dưới ngưỡng này thì hội thoại không đủ chữ để đọc ra nhu cầu — bỏ qua, đừng đốt token.
AI_CHAT_EXTRACT_MIN_CHARS = int(os.getenv("AI_CHAT_EXTRACT_MIN_CHARS", "80"))
```

- [ ] **Step 4: Viết `infrastructure/chat_extract_runner.py`**

```python
"""Xếp hàng + gọi Gemini + ghi nhãn cho hội thoại chat.

Khuôn bê từ apps/callcenter/extract.py: cooldown riêng khi 429, lỗi hạ tầng
nhất thời KHÔNG đốt lượt attempt, lỗi 1 dòng không chặn dòng khác. KHÔNG log
nội dung hội thoại (PII) — chỉ log id và tên field.

Hàng đợi nằm ở ai_stg.hoi_thoai_nhan; nguồn xếp hàng là ai.hoi_thoai (đã ETL),
nên vòng này KHÔNG chạm foreign table: FDW chết vẫn gán nhãn tiếp được phần
tồn đọng.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connections, transaction

from apps.ai_mart.domain.chat_extract import (
    build_chat_prompt,
    build_chat_response_schema,
    normalize_chat_extracted,
)
from apps.ai_mart.infrastructure.loaders import TARGET_ALIAS

logger = logging.getLogger(__name__)

COOLDOWN_KEY = "gemini:chat_extract:cooldown"

_TRANSIENT_MARKERS = ("429", "500", "503", "resource_exhausted", "unavailable",
                      "deadline", "timeout", "timed out", "connection")
_QUOTA_MARKERS = ("429", "resource_exhausted")


class ChatExtractError(Exception):
    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted


def _conn():
    return connections[TARGET_ALIAS]


_ENQUEUE_SQL = """
INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, noi_dung_hash, extract_status)
SELECT h.hoi_thoai_id, md5(h.noi_dung), 'pending'
FROM ai.hoi_thoai h
WHERE h.so_tin_khach > 0
  AND h.noi_dung IS NOT NULL
  AND length(h.noi_dung) >= %(min_chars)s
ON CONFLICT (hoi_thoai_id) DO UPDATE
SET extract_status = 'pending',
    extract_attempts = 0,
    noi_dung_hash = EXCLUDED.noi_dung_hash,
    updated_at = now()
WHERE hoi_thoai_nhan.noi_dung_hash IS DISTINCT FROM EXCLUDED.noi_dung_hash
"""


def enqueue_pending() -> int:
    """Xếp hàng hội thoại đủ điều kiện. Nội dung không đổi thì KHÔNG gán nhãn lại."""
    with _conn().cursor() as cur:
        cur.execute(_ENQUEUE_SQL, {"min_chars": settings.AI_CHAT_EXTRACT_MIN_CHARS})
        return cur.rowcount


# Ưu tiên hội thoại khách nói nhiều nhất — backfill theo lô giảm dần (spec §6).
_CLAIM_SQL = """
WITH lo AS (
    SELECT n.hoi_thoai_id
    FROM ai_stg.hoi_thoai_nhan n
    JOIN ai.hoi_thoai h ON h.hoi_thoai_id = n.hoi_thoai_id
    WHERE n.extract_status = 'pending'
    ORDER BY h.so_tin_khach DESC, h.ket_thuc DESC NULLS LAST
    LIMIT %(limit)s
    FOR UPDATE OF n SKIP LOCKED
)
UPDATE ai_stg.hoi_thoai_nhan n
SET extract_status = 'processing', updated_at = now()
FROM lo
WHERE n.hoi_thoai_id = lo.hoi_thoai_id
RETURNING n.hoi_thoai_id
"""


def _claim(limit: int) -> list[int]:
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            cur.execute(_CLAIM_SQL, {"limit": limit})
            return [row[0] for row in cur.fetchall()]


def _generate_json(prompt: str) -> str:
    """Gọi Gemini với structured output. Hàm riêng để test mock được."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise ChatExtractError(f"Thiếu thư viện google-genai: {exc}") from exc

    client = genai.Client(
        api_key=settings.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=120_000)
    )
    response = client.models.generate_content(
        model=settings.GEMINI_EXTRACT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=build_chat_response_schema(),
            temperature=0,   # đọc hiểu, không cần sáng tạo
        ),
    )
    return getattr(response, "text", None) or ""


def _extract_one(noi_dung: str) -> tuple[dict, dict, dict]:
    try:
        text = _generate_json(build_chat_prompt(noi_dung))
    except ChatExtractError:
        raise
    except Exception as exc:  # noqa: BLE001 — giữ nguyên văn lỗi SDK cho dòng hàng đợi
        lowered = str(exc).lower()
        raise ChatExtractError(
            f"Gemini lỗi: {exc}",
            transient=any(m in lowered for m in _TRANSIENT_MARKERS),
            quota_exhausted=any(m in lowered for m in _QUOTA_MARKERS),
        ) from exc

    text = (text or "").strip()
    if not text:
        raise ChatExtractError("Gemini trả về response rỗng.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChatExtractError(f"Gemini trả về JSON không hợp lệ: {exc}") from exc
    if not isinstance(raw, dict):
        raise ChatExtractError("Gemini trả về JSON không phải object.")
    clean, rejected = normalize_chat_extracted(raw)
    return raw, clean, rejected


def _save_done(hoi_thoai_id: int, raw, clean, rejected) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan SET extract_payload = %s, extract_status = 'done',"
            " extract_error = '', model = %s, updated_at = now() WHERE hoi_thoai_id = %s",
            [json.dumps({"raw": raw, "clean": clean, "rejected": rejected}),
             settings.GEMINI_EXTRACT_MODEL, hoi_thoai_id],
        )


def _save_error(hoi_thoai_id: int, message: str, *, transient: bool) -> None:
    # Lỗi hạ tầng nhất thời KHÔNG đốt lượt attempt (bài học audit transcribe
    # 2026-07-18: Gemini chập chờn 15 phút mà đốt attempt là hàng loạt dòng
    # FAILED oan).
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan"
            " SET extract_attempts = extract_attempts + %s,"
            "     extract_error = %s,"
            "     extract_status = CASE WHEN extract_attempts + %s >= %s"
            "                           THEN 'failed' ELSE 'pending' END,"
            "     updated_at = now()"
            " WHERE hoi_thoai_id = %s",
            [0 if transient else 1, message[:2000], 0 if transient else 1,
             settings.AI_CHAT_EXTRACT_MAX_ATTEMPTS, hoi_thoai_id],
        )


def _requeue(ids: list[int]) -> None:
    if not ids:
        return
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan SET extract_status = 'pending', updated_at = now()"
            " WHERE hoi_thoai_id = ANY(%s) AND extract_status = 'processing'",
            [ids],
        )


def _load_noi_dung(hoi_thoai_id: int) -> str:
    with _conn().cursor() as cur:
        cur.execute("SELECT noi_dung FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [hoi_thoai_id])
        row = cur.fetchone()
    return (row[0] if row else "") or ""


def run_batch(limit: int | None = None) -> dict:
    summary = {"claimed": 0, "done": 0, "errors": 0, "skipped": 0, "quota_stopped": False}
    if not settings.AI_CHAT_EXTRACT_ENABLED or not settings.GEMINI_API_KEY:
        return summary
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt
    # UPDATE cả lô để rồi vứt (sự cố transcribe 2026-07-22).
    if cache.get(COOLDOWN_KEY) is not None:
        summary["quota_stopped"] = True
        return summary

    batch = _claim(settings.AI_CHAT_EXTRACT_BATCH if limit is None else limit)
    summary["claimed"] = len(batch)

    for index, hoi_thoai_id in enumerate(batch):
        noi_dung = _load_noi_dung(hoi_thoai_id)
        if len(noi_dung) < settings.AI_CHAT_EXTRACT_MIN_CHARS:
            with _conn().cursor() as cur:
                cur.execute(
                    "UPDATE ai_stg.hoi_thoai_nhan SET extract_status = 'skipped',"
                    " updated_at = now() WHERE hoi_thoai_id = %s", [hoi_thoai_id]
                )
            summary["skipped"] += 1
            continue
        try:
            raw, clean, rejected = _extract_one(noi_dung)
        except ChatExtractError as exc:
            if exc.quota_exhausted:
                cache.set(COOLDOWN_KEY, 1,
                          timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60)
                _requeue(batch[index:])
                summary["quota_stopped"] = True
                logger.warning("gán nhãn chat nghỉ %s phút vì Gemini hết quota: %s",
                               settings.GEMINI_QUOTA_COOLDOWN_MINUTES, exc)
                break
            _save_error(hoi_thoai_id, str(exc), transient=exc.transient)
            summary["errors"] += 1
            logger.warning("gán nhãn chat lỗi hoi_thoai_id=%s: %s", hoi_thoai_id, exc)
            continue
        _save_done(hoi_thoai_id, raw, clean, rejected)
        summary["done"] += 1
        # KHÔNG log nội dung hội thoại/giá trị nhãn (PII) — chỉ id và tên field.
        logger.info("gán nhãn chat xong hoi_thoai_id=%s fields=%s",
                    hoi_thoai_id, sorted(clean))
    return summary
```

- [ ] **Step 5: Viết command + task Celery + beat**

`backend/apps/ai_mart/management/commands/ai_mart_chat_extract.py`:

```python
"""Gán nhãn hội thoại chat bằng Gemini. Dùng để backfill theo lô:

  python manage.py ai_mart_chat_extract --limit 200
"""

from django.core.management.base import BaseCommand

from apps.ai_mart.infrastructure import chat_extract_runner


class Command(BaseCommand):
    help = "Xếp hàng và gán nhãn hội thoại chat"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--enqueue-only", action="store_true")

    def handle(self, *args, **options):
        queued = chat_extract_runner.enqueue_pending()
        self.stdout.write(f"xếp hàng: {queued}")
        if options["enqueue_only"]:
            return
        summary = chat_extract_runner.run_batch(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(str(summary)))
```

Thêm vào cuối `backend/apps/ai_mart/infrastructure/tasks.py`:

```python
_EXTRACT_LOCK = "lock:ai_mart_chat_extract"


@shared_task(name="apps.ai_mart.tasks.extract_chat_labels", soft_time_limit=540, time_limit=600)
def extract_chat_labels() -> dict:
    """Gán nhãn hội thoại chat. KHÔNG chạm foreign table — nguồn FDW chết vẫn
    tiêu hoá được phần tồn đọng trong hàng đợi."""
    from apps.ai_mart.infrastructure import chat_extract_runner

    with single_flight(_EXTRACT_LOCK, 900) as acquired:
        if not acquired:
            logger.warning("extract_chat_labels already running — skip this cycle")
            return {"skipped": True}
        with statement_timeout(120):
            queued = chat_extract_runner.enqueue_pending()
        summary = chat_extract_runner.run_batch()
        return {"queued": queued, **summary}
```

`backend/config/settings_parts/celery.py` — route (cạnh 2 dòng `ai_mart` sẵn có, ~dòng 113):

```python
    "apps.ai_mart.tasks.extract_chat_labels": {"queue": CELERY_SLOW_QUEUE},
```

và beat (cạnh `ai-mart-rebuild-all`, ~dòng 531):

```python
    # Gán nhãn chat: nhịp 10' lệch pha khỏi sync (phút 7/37) để hai job không
    # giành cùng lúc connection của queue slow. Lô nhỏ, chạy đều còn hơn dồn cục.
    "ai-mart-chat-extract": {
        "task": "apps.ai_mart.tasks.extract_chat_labels",
        "schedule": crontab(minute="2,12,22,32,42,52"),
    },
```

- [ ] **Step 6: Chạy test cho xanh**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_chat_extract_runner -v2 --noinput
```
Expected: 7 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ai_mart/infrastructure/chat_extract_runner.py \
        backend/apps/ai_mart/management/commands/ai_mart_chat_extract.py \
        backend/apps/ai_mart/infrastructure/tasks.py \
        backend/apps/ai_mart/tests/test_chat_extract_runner.py \
        backend/config/settings_parts/base.py backend/config/settings_parts/celery.py
git commit -m "feat(ai_mart): pipeline gán nhãn nhu cầu/lý do không mua cho hội thoại chat"
```

---

### Task 8: Ngoại lệ che PII, quyền `hermes_ro`, alert và runbook

Spec §4.6, §7, §8, §10 (S4). Task khoá sổ: bộ test chốt hợp đồng PII, quyền đọc cho agent, và tài liệu triển khai.

**Files:**
- Modify: `backend/apps/ai_mart/tests/test_masking.py`
- Create: `backend/apps/ai_mart/tests/test_chat_contract.py`
- Modify: `monitoring/alerts.yml`
- Create: `docs/runbooks/2026-09-04-ai-chat-mart-deploy.md`

**Interfaces:**
- Consumes: mọi thứ từ Task 1–7.
- Produces: không có API mới.

- [ ] **Step 1: Khai báo ngoại lệ che PII**

`backend/apps/ai_mart/tests/test_masking.py`, sửa `SKIP_COLUMNS`:

```python
# Ngoại lệ CÓ CHỦ ĐÍCH của luật che PII (spec ai-mart §4.6 + spec chat §4):
# nội dung hội thoại thô, Hermes cần nguyên văn để trả lời về nội dung tư vấn.
# Ba cột chat cùng chuẩn với cuoc_goi.transcript — COMMENT của chúng CẤM agent
# trích SĐT/địa chỉ ra câu trả lời. Mọi cột khác vẫn bị quét.
SKIP_COLUMNS = {
    ("cuoc_goi", "transcript"),
    ("cuoc_goi", "tom_tat_ai"),
    ("hoi_thoai", "noi_dung"),
    ("hoi_thoai", "tom_tat_ai"),
    ("tin_nhan", "noi_dung"),
}
```

- [ ] **Step 2: Viết test chốt hợp đồng chat**

`backend/apps/ai_mart/tests/test_chat_contract.py`:

```python
"""Hai điều test_contract chung không bắt được:
1. Ngoại lệ PII phải được TUYÊN BỐ trong COMMENT, không im lặng.
2. Mọi mã LLM có thể sinh ra đều phải dịch được — cột _ma có giá trị mà _text
   NULL nghĩa là từ điển thiếu, đúng bệnh spec ai_mart sinh ra để chữa.
"""

import json

from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.chat_dictionary import GIAI_DOAN_CHAT, MUC_DO_QUAN_TAM
from apps.ai_mart.domain.chat_extract import load_need_labels, load_reason_labels
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch

PII_EXCEPTIONS = [
    ("hoi_thoai", "noi_dung"),
    ("hoi_thoai", "tom_tat_ai"),
    ("tin_nhan", "noi_dung"),
]


class ChatContractTests(TestCase):
    def test_pii_exception_columns_declare_the_ban_in_their_comment(self):
        missing = []
        with connection.cursor() as cur:
            for table, col in PII_EXCEPTIONS:
                cur.execute(
                    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attname = %s",
                    [table, col],
                )
                comment = (cur.fetchone() or [""])[0] or ""
                if "KHÔNG trích SĐT/địa chỉ từ cột này" not in comment:
                    missing.append(f"ai.{table}.{col}")
        self.assertEqual(missing, [], f"ngoại lệ PII chưa tuyên bố trong COMMENT: {missing}")

    def test_don_id_comment_warns_about_low_coverage(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
                " WHERE a.attrelid = 'ai.hoi_thoai'::regclass AND a.attname = 'don_id'"
            )
            (comment,) = cur.fetchone()
        # C7: agent KHÔNG được đọc don_id IS NULL thành "khách không mua"
        self.assertIn("không có nghĩa là", comment)

    def test_every_label_code_resolves(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="test")
        clean = {
            "nhu_cau": sorted(load_need_labels()),
            "ly_do_khong_mua": sorted(load_reason_labels()),
            "giai_doan": GIAI_DOAN_CHAT[0][0],
            "muc_do_quan_tam": MUC_DO_QUAN_TAM[0][0],
        }
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_payload, extract_status)"
                " VALUES (%s, %s, 'done')",
                [conv, json.dumps({"clean": clean})],
            )
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhu_cau_ai, nhu_cau_ai_text, ly_do_khong_mua_ma, ly_do_khong_mua_text,"
                " giai_doan_text, muc_do_quan_tam_text FROM ai.hoi_thoai WHERE hoi_thoai_id = %s",
                [conv],
            )
            nhu_cau, nhu_cau_text, ly_do, ly_do_text, giai_doan, muc_do = cur.fetchone()
        # Số nhãn phải bằng số mã: thiếu 1 nhãn là 1 mã mồ côi
        self.assertEqual(len(nhu_cau_text.split(", ")), len(nhu_cau))
        self.assertEqual(len(ly_do_text.split(", ")), len(ly_do))
        self.assertIsNotNone(giai_doan)
        self.assertIsNotNone(muc_do)
```

- [ ] **Step 3: Chạy hai bộ test**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart.tests.test_masking apps.ai_mart.tests.test_chat_contract -v2 --noinput
```
Expected: PASS. `test_every_label_code_resolves` đỏ nghĩa là có mã trong `need_types`/`ly_do_khong_chot` chưa vào `ai.tu_dien_ma` → sửa Task 1, **không** nới test.

- [ ] **Step 4: Thêm alert cho luồng gán nhãn**

`monitoring/alerts.yml`, ngay sau block `AiMartRebuildSilent`:

```yaml
      # sync_hot_marts đã được AiMartSyncSilent canh — cầu FDW chết là job raise,
      # không có lượt succeeded nào và alert đó nổ trong 90'. Đây là lỗ hổng
      # CÒN LẠI: gán nhãn chết âm thầm thì hai bảng chat vẫn tươi, chỉ có cột
      # nhãn AI đứng im — nhìn dashboard không thấy gì bất thường.
      - alert: AiChatExtractSilent
        expr: increase(celery_task_succeeded_total{name="apps.ai_mart.tasks.extract_chat_labels"}[3h]) == 0 or absent(celery_task_succeeded_total{name="apps.ai_mart.tasks.extract_chat_labels"})
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "Gán nhãn hội thoại chat không chạy >3 giờ"
          description: "Không có lượt extract_chat_labels thành công nào trong 3 giờ (nhịp chuẩn 10'). ai.hoi_thoai vẫn nạp bình thường nhưng nhu_cau_ai/ly_do_khong_mua_ma đóng băng. Kiểm GEMINI_API_KEY, hạn mức Gemini, queue slow."
```

- [ ] **Step 5: Viết runbook**

`docs/runbooks/2026-09-04-ai-chat-mart-deploy.md` — nội dung bắt buộc có, theo đúng thứ tự thao tác:

```markdown
# Runbook — Triển khai mart hội thoại chat (spec 2026-09-04)

## 0. Điều kiện tiên quyết
- Stack `desi-crawl-conversations` đang chạy (`docker ps | grep deploy-postgres-1`).
- Đã có mật khẩu cho role chỉ-đọc phía DB `crawler`.

## 1. Tạo role chỉ-đọc phía crawler (KHÔNG dùng superuser `crawler`)
```bash
docker exec -it deploy-postgres-1 psql -U crawler -d crawler
CREATE ROLE chat_ro LOGIN PASSWORD '<mật khẩu mạnh>' CONNECTION LIMIT 5;
ALTER ROLE chat_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE crawler TO chat_ro;
GRANT USAGE ON SCHEMA public TO chat_ro;
GRANT SELECT ON pages, conversations, customers, messages, tags, conversation_tags TO chat_ro;
```

## 2. Nối mạng docker
`desi-postgres` (mạng `desi-order_default`) và `deploy-postgres-1` (mạng `deploy_default`)
nằm ở hai mạng rời; `deploy-postgres-1` chỉ bind `127.0.0.1:15432` nên KHÔNG đi vòng qua host được.
Chính tiến trình Postgres của `desi-postgres` mở kết nối FDW, nên phải nối container đó:
```bash
docker network connect deploy_default desi-postgres
docker exec desi-postgres getent hosts deploy-postgres-1   # phải ra IP
```
Khai báo cố định trong compose (Task 2 Step 8) để không mất sau `docker compose up`.

## 3. Đặt env và migrate
Thêm vào `.env` prod: `CHAT_CRAWLER_DB_PASSWORD`, `AI_CHAT_EXTRACT_ENABLED=1`.
```bash
docker compose exec web python manage.py migrate ai_mart
```

## 4. Dựng cầu FDW (cần SUPERUSER)
```bash
docker compose exec web python manage.py ai_mart_chat_fdw
docker compose exec web python manage.py ai_mart_chat_fdw --check
```
`--check` phải in ra số dòng thật (~94.902 hội thoại / ~769.234 tin).

## 5. Nạp lần đầu
```bash
docker compose exec web python manage.py ai_mart_rebuild
```

## 6. Cấp quyền cho agent
`ALTER DEFAULT PRIVILEGES` không phủ bảng do owner khác tạo — PHẢI chạy lại:
```bash
docker compose exec -e HERMES_DB_PASSWORD=... web python manage.py ai_mart_grant_role
```
Kiểm: `hermes_ro` SELECT được `ai.hoi_thoai`, `ai.tin_nhan`, và **bị từ chối** trên `ai_stg.hoi_thoai_nhan`.

## 7. Backfill nhãn theo lô
```bash
docker compose exec web python manage.py ai_mart_chat_extract --limit 500
```
Lặp lại, theo dõi `SELECT extract_status, count(*) FROM ai_stg.hoi_thoai_nhan GROUP BY 1`.

## 8. Đo độ phủ thật rồi CẬP NHẬT COMMENT (spec §4.7, S4)
```sql
SELECT count(*) FILTER (WHERE don_id IS NOT NULL)::numeric / count(*) FROM ai.hoi_thoai;
```
Ghi con số đo được vào `COMMENT ON COLUMN ai.hoi_thoai.don_id` bằng một data migration mới —
không để agent tự suy độ phủ.

## 9. Rollback
```bash
docker compose exec web python manage.py migrate ai_mart 0013_don_hang_chi_nhanh_nhom_kenh
docker exec desi-postgres psql -U desi -d desi -c "DROP SERVER IF EXISTS chat_crawler CASCADE"
```

## Đã biết / bẫy
- Nguồn trễ ~3 ngày so với thực tế lúc viết spec — kiểm `max(last_message_at)` trước khi kết luận "không có hội thoại".
- Crawler log nội dung tin ra stdout (PII) — nợ riêng, không thuộc phạm vi spec này.
- FDW chết → `sync_hot_marts` **đỏ** (đúng thiết kế C2), `AiMartSyncSilent` nổ sau 90'. Không được thêm try/except để "cho nó xanh".
```

- [ ] **Step 6: Chạy toàn bộ test app**

```bash
/home/desi/desi-order/.venv/bin/python manage.py test apps.ai_mart -v2 --noinput
```
Expected: toàn bộ PASS.

- [ ] **Step 7: Kiểm import-linter không vỡ tầng**

```bash
cd /home/desi/desi-order/backend && /home/desi/desi-order/.venv/bin/lint-imports
```
Expected: PASS. `domain/` không được import `infrastructure/`; `chat_extract.py` chỉ đọc model qua import trong hàm.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/ai_mart/tests/test_masking.py \
        backend/apps/ai_mart/tests/test_chat_contract.py \
        monitoring/alerts.yml \
        docs/runbooks/2026-09-04-ai-chat-mart-deploy.md
git commit -m "feat(ai_mart): chốt hợp đồng PII/từ điển cho mart chat + alert + runbook triển khai"
```

---

## Kiểm chứng cuối (sau Task 8)

Câu SQL mà spec §10 đòi phải trả lời được sau S2 — chạy trên prod sau bước 5 của runbook:

```sql
SELECT nen_tang_text,
       count(*) AS so_hoi_thoai,
       count(*) FILTER (WHERE so_tin_khach >= 2) AS hoi_thoai_that
FROM ai.hoi_thoai
WHERE bat_dau_ngay >= date '2026-08-01' AND bat_dau_ngay < date '2026-09-01'
  AND NOT do_bot_xu_ly
GROUP BY nen_tang_text
ORDER BY so_hoi_thoai DESC;
```

Và câu trả lời cho hai câu hỏi kinh doanh (sau bước 7):

```sql
-- Khách cần gì
SELECT ai.nhan('nhu_cau', nc) AS nhu_cau, count(*) AS so_hoi_thoai
FROM ai.hoi_thoai h, unnest(h.nhu_cau_ai) AS nc
WHERE h.bat_dau_ngay >= date '2026-08-01' AND NOT h.do_bot_xu_ly
GROUP BY 1 ORDER BY 2 DESC;

-- Khách nói lý do gì vs sale ghi lý do gì (cùng bộ mã — đây là giá trị của C4)
SELECT ld AS ly_do,
       count(*) FILTER (WHERE nguon = 'chat') AS khach_noi_trong_chat,
       count(*) FILTER (WHERE nguon = 'don')  AS sale_ghi_tren_phieu
FROM (
    SELECT unnest(ly_do_khong_mua_ma) AS ld, 'chat' AS nguon FROM ai.hoi_thoai
    WHERE bat_dau_ngay >= date '2026-08-01' AND NOT do_bot_xu_ly
    UNION ALL
    SELECT ly_do_khong_chot_ma, 'don' FROM ai.don_hang
    WHERE ngay_tao_ngay >= date '2026-08-01' AND ly_do_khong_chot_ma IS NOT NULL
) t GROUP BY ld ORDER BY 2 DESC NULLS LAST;
```

> Tên cột ngày của `ai.don_hang` có thể khác `ngay_tao_ngay` — kiểm bằng
> `\d ai.don_hang` trước khi chạy, đừng đoán.

## Việc CỐ Ý không làm

- **Không chấm điểm sale/CSKH** (C5) — nguồn không có `admin_id`, mọi tin gửi ra đều mang id page.
- **Không crawl lại** (C6) — chỉ cần cho mục tiêu đã loại ở C5.
- **Không partition `ai.tin_nhan`** — 769k dòng còn xa ngưỡng ~10 triệu (spec §9).
- **Không sửa crawler** (log PII ra stdout, độ trễ 3 ngày) — nợ riêng, ghi ở runbook.
- **Không thêm cột `email`/địa chỉ** — luật 6 cấm, `chat_source` cố ý bỏ `customers.email`.
