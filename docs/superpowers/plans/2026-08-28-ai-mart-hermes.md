# AI Mart (schema `ai`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng schema `ai` trong Postgres `desi` — bảng vật lý chuẩn hoá nhãn tiếng Việt, ETL tăng dần 30 phút + full rebuild đêm, role chỉ-đọc `hermes_ro` — để agent Hermes đọc qua MCP mà không thấy mã thô.

**Architecture:** App Django mới `apps/ai_mart` (clean architecture, tầng TRÊN `analytics`). Tầng RAW là bảng OLTP hiện có; MART là bảng vật lý trong schema `ai`; META là `ai_meta` (watermark + nhật ký). Watermark bằng cột `dw_updated_at` do trigger duy trì trên 5 bảng nguồn; upsert = DELETE-by-key + INSERT-select trong 1 transaction; rebuild đêm là cơ chế tự lành. Từ điển mã `ai.tu_dien_ma` seed từ code, tra trong SQL bằng hàm `ai.nhan(nhom, ma)`.

**Tech Stack:** Django 5 / Python 3.12, Postgres 16, Celery (queue `slow`), pytest, raw SQL migrations theo tiền lệ `apps/analytics`.

**Spec:** `docs/superpowers/specs/2026-08-28-ai-mart-hermes-design.md` — plan này lập luận từ spec, executor đọc cả hai.

## Global Constraints

- Mọi bảng/cột trong schema `ai` PHẢI có `COMMENT` tiếng Việt (spec §4.7) — `test_contract.py` chặn.
- Chuỗi rỗng → `NULL` bằng `NULLIF(btrim(x), '')` (spec §4.2).
- Tiền `numeric` VND, thời gian `timestamptz` + cột `ngay_*` kiểu `date` quy về `Asia/Ho_Chi_Minh` — import `BUSINESS_TZ` từ `apps.analytics.sql.tz`, KHÔNG gõ lại chuỗi timezone.
- Mệnh đề GTC PHẢI gọi `apps.analytics.sql.gtc.gtc_where()` — nguồn chân lý duy nhất, cấm chép 5 mệnh đề.
- Loại đơn mirror bằng `apps.common.order_scope.sql_exclude_mirror_orders()`.
- PII: SĐT che `0912***678` (4 đầu + `***` + 3 cuối); KHÔNG email, KHÔNG địa chỉ chi tiết; giữ tỉnh/huyện; tên khách giữ nguyên.
- Job Celery: bọc `single_flight` + `statement_timeout` từ `apps.common.db_guards`; **lỗi phải thoát ra ngoài**, cấm nuốt (sự cố 2026-07-22).
- Migration kiểu `SeparateDatabaseAndState` + `RunSQL` theo tiền lệ `apps/analytics/migrations/0001_dwh_schema.py`.
- Thứ tự deploy prod: **migrate trước, roll code sau**.
- KHÔNG có `.gitlab-ci.yml` trên master — mọi lint/test chạy tay: `ruff check apps/ai_mart`, `lint-imports` (từ `backend/`), pytest.
- Commit message tiếng Anh, ngắn, KHÔNG kèm Co-Authored-By.
- Mỗi sprint = 1 branch + 1 MR riêng (`feat/ai-mart-s1` … `feat/ai-mart-s4`) tạo từ master mới nhất — user merge rất nhanh, đã gửi link MR thì đóng băng branch.
- Sau mỗi sprint: cập nhật cột Trạng thái §8 của spec + note đã làm/chưa làm, commit cùng MR sprint đó.
- Test chạy bằng Docker theo memory `backend-test-run-command` (container test phải dựng lại nếu chưa có). Các test đỏ SẴN trên master đã biết: `report_trends`, `realtime-upsert-filters`, N+1 export đơn (9 vs 10 query), `audit-logs-api.test.ts` (tsc) — KHÔNG phải lỗi của nhánh này.
- Bẫy Windows: file mới tạo bằng script phải LF, không CRLF (bài học `write_text` CRLF).

## Quy ước đặt tên dùng chung (mọi task tuân theo)

- Schema: `ai` (mart, Hermes đọc), `ai_stg` (staging), `ai_meta` (watermark + nhật ký).
- Hàm SQL: `ai.nhan(nhom text, ma text) → text` (tra nhãn), `ai.che_sdt(p text) → text` (che SĐT).
- Trigger fn: `public.ai_mart_touch_dw()`; trigger tên `<bảng>_dw_touch`.
- Module SQL mỗi bảng mart: `apps/ai_mart/infrastructure/sql/<ten_bang>.py` expose hằng `MART` kiểu `MartTable`.
- `MartTable` (định nghĩa Task 5): `name` (vd `"ai.don_hang"`), `key_column` (vd `"don_id"`), `group` (`"hot"` | `"nightly"`), `select_sql(where: str) -> str`, `candidate_ids_sql: str | None`.
- Loader (Task 5): `read_watermark(table_name) -> datetime | None`, `write_watermark(table_name, moc)`, `upsert_by_ids(mart, ids) -> int`, `full_rebuild(mart) -> int`, `log_run(run_id, table_name, started_at, finished_at, rows, status, error="")`.
- Service (Task 8): `sync_hot() -> dict`, `rebuild_all() -> dict`, exception `EtlFailed(RuntimeError)`.
- Task Celery: `apps.ai_mart.tasks.sync_hot_marts`, `apps.ai_mart.tasks.rebuild_all_marts`.

---

# SPRINT 1 — Nền móng: schema, trigger, từ điển, đơn hàng

Branch: `git worktree add .claude/worktrees/ai-mart-s1 -b feat/ai-mart-s1 origin/master` (sau khi `git fetch origin master`). Mọi lệnh dưới chạy trong worktree đó, thư mục `backend/`.

### Task 1: Scaffold app + 3 schema + khai tầng import-linter

**Files:**
- Create: `backend/apps/ai_mart/__init__.py`, `apps.py`, `domain/__init__.py`, `application/__init__.py`, `infrastructure/__init__.py`, `infrastructure/sql/__init__.py`, `management/__init__.py`, `management/commands/__init__.py`, `migrations/__init__.py`, `tests/__init__.py`
- Create: `backend/apps/ai_mart/migrations/0001_schemas.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS, cạnh dòng 82 `"apps.analytics"`)
- Modify: `backend/.importlinter` (layers — thêm `ai_mart` TRÊN `analytics`)
- Test: `backend/apps/ai_mart/tests/test_app_registration.py`

**Interfaces:**
- Produces: app label `ai_mart`; 3 schema `ai`, `ai_stg`, `ai_meta` tồn tại trong mọi DB (kể cả test DB) sau migrate.

- [ ] **Step 1: Viết test đăng ký app (fail trước)**

```python
# backend/apps/ai_mart/tests/test_app_registration.py
"""App phải được đăng ký và migration 0001 phải tạo đủ 3 schema.

Test schema chạy trên test DB — migration chạy trong quá trình dựng DB test,
nên chỉ cần soi pg_namespace, không cần gọi migrate thủ công.
"""

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase


class AiMartRegistrationTests(TestCase):
    def test_app_installed(self):
        self.assertTrue(django_apps.is_installed("apps.ai_mart"))

    def test_schemas_exist(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nspname FROM pg_namespace"
                " WHERE nspname IN ('ai', 'ai_stg', 'ai_meta')"
            )
            names = {row[0] for row in cur.fetchall()}
        self.assertEqual(names, {"ai", "ai_stg", "ai_meta"})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_app_registration.py -v`
Expected: FAIL/ERROR — app chưa tồn tại (`ModuleNotFoundError`).

- [ ] **Step 3: Tạo skeleton app**

`backend/apps/ai_mart/apps.py`:

```python
from django.apps import AppConfig


class AiMartConfig(AppConfig):
    name = "apps.ai_mart"
    label = "ai_mart"
    verbose_name = "AI Mart — dữ liệu chuẩn hoá cho agent"
```

`backend/apps/ai_mart/__init__.py` để trống. Tạo đủ các `__init__.py` còn lại (trống).

Thêm vào `INSTALLED_APPS` trong `backend/config/settings_parts/base.py`, ngay sau `"apps.analytics",`:

```python
    "apps.ai_mart",
```

- [ ] **Step 4: Migration 0001 tạo 3 schema**

`backend/apps/ai_mart/migrations/0001_schemas.py`:

```python
"""Tạo 3 schema của AI mart. Tách riêng để CREATE SCHEMA chạy đúng một lần,
các migration bảng sau chỉ phụ thuộc vào nó (tiền lệ: analytics/0001_dwh_schema).

`ai` — bảng contract Hermes đọc. `ai_stg` — staging. `ai_meta` — watermark + nhật ký.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE SCHEMA IF NOT EXISTS ai;"
                        " CREATE SCHEMA IF NOT EXISTS ai_stg;"
                        " CREATE SCHEMA IF NOT EXISTS ai_meta;"
                    ),
                    reverse_sql=(
                        "DROP SCHEMA IF EXISTS ai CASCADE;"
                        " DROP SCHEMA IF EXISTS ai_stg CASCADE;"
                        " DROP SCHEMA IF EXISTS ai_meta CASCADE;"
                    ),
                )
            ]
        )
    ]
```

- [ ] **Step 5: Khai tầng trong `.importlinter`**

Trong `backend/.importlinter`, khối `layers =`, thêm `ai_mart` thành tầng TRÊN CÙNG (trên `analytics` — vì `ai_mart` import `apps.analytics.sql.gtc`/`tz`):

```
layers =
    ai_mart
    analytics
    callcenter | contact_flow | warranty_codes
    ...phần còn lại giữ nguyên...
```

⚠ `exhaustive = true`: quên khai là `lint-imports` FAIL ngay — đó là chủ đích.

- [ ] **Step 6: Chạy test + lint, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_app_registration.py -v && ruff check apps/ai_mart && lint-imports`
Expected: PASS cả ba. (Test DB được dựng lại nên migration 0001 chạy.)

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ai_mart backend/config/settings_parts/base.py backend/.importlinter
git commit -m "feat(ai_mart): scaffold app with ai/ai_stg/ai_meta schemas"
```

### Task 2: Trigger watermark `dw_updated_at` trên 5 bảng nguồn

**Files:**
- Create: `backend/apps/ai_mart/migrations/0002_dw_updated_at.py`
- Test: `backend/apps/ai_mart/tests/test_triggers.py`

**Interfaces:**
- Produces: cột `dw_updated_at timestamptz NULL` + trigger `<bảng>_dw_touch` trên `orders`, `order_items`, `customers`, `order_assessments`, `callcenter_call_records`; hàm `public.ai_mart_touch_dw()`.
- Ghi chú thiết kế: KHÔNG backfill. Dòng cũ mang NULL vĩnh viễn cho tới lần sửa kế tiếp — vô hại vì initial load + rebuild đêm là full SELECT, watermark chỉ phục vụ upsert tăng dần cho dòng bị chạm SAU khi trigger cài. Nhờ vậy né hẳn rủi ro khoá bảng 547k dòng.

- [ ] **Step 1: Viết test trigger (fail trước)**

```python
# backend/apps/ai_mart/tests/test_triggers.py
"""Trigger dw_updated_at phải bắt MỌI đường ghi — kể cả .update()/.bulk_update()
vốn bỏ qua auto_now (chính lý do không dùng được updated_at làm watermark:
webhook KiotViet webhook_impl.py:380, sync Foxia order_sync_service.py:253)."""

from django.db import connection
from django.test import TestCase

from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory  # nếu không có factory, xem Step 1b


def _dw(order_id: int):
    with connection.cursor() as cur:
        cur.execute("SELECT dw_updated_at FROM orders WHERE id = %s", [order_id])
        return cur.fetchone()[0]


class DwUpdatedAtTriggerTests(TestCase):
    def test_insert_sets_dw_updated_at(self):
        order = OrderFactory()
        self.assertIsNotNone(_dw(order.id))

    def test_queryset_update_bumps(self):
        order = OrderFactory()
        before = _dw(order.id)
        Order.objects.filter(id=order.id).update(status=2)
        self.assertGreater(_dw(order.id), before)

    def test_bulk_update_bumps(self):
        order = OrderFactory()
        before = _dw(order.id)
        order.status = 3
        Order.objects.bulk_update([order], ["status"])
        self.assertGreater(_dw(order.id), before)

    def test_all_five_tables_have_trigger(self):
        expected = {
            "orders", "order_items", "customers",
            "order_assessments", "callcenter_call_records",
        }
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT event_object_table FROM information_schema.triggers"
                " WHERE trigger_name LIKE '%%_dw_touch'"
            )
            tables = {row[0] for row in cur.fetchall()}
        self.assertEqual(tables, expected)
```

**Step 1b — nếu `apps.orders.tests.factories.OrderFactory` không tồn tại:** grep `grep -rn "class OrderFactory" apps/orders/tests/` — dùng đúng đường dẫn tìm thấy; nếu repo dựng order bằng helper khác (vd `apps/orders/tests/helpers.py`), dùng helper đó thay vì tự chế. Bám codebase, không phát minh fixture mới.

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_triggers.py -v`
Expected: FAIL — cột `dw_updated_at` chưa tồn tại.

- [ ] **Step 3: Migration 0002**

```python
# backend/apps/ai_mart/migrations/0002_dw_updated_at.py
"""Cột + trigger watermark cho ETL tăng dần (spec §5.1, quyết định Q6/Q8).

KHÔNG backfill, KHÔNG DEFAULT now() (DEFAULT sẽ rewrite cả bảng 547k dòng).
NULL = "chưa bị chạm từ khi cài trigger" — hợp lệ, initial load là full SELECT.

5 bảng — thiếu order_items là ai.dong_don_hang trễ tới rebuild đêm khi sync
KiotViet sửa item mà không chạm orders (spec §5.1).
"""

from django.db import migrations

TABLES = (
    "orders",
    "order_items",
    "customers",
    "order_assessments",
    "callcenter_call_records",
)

FORWARD = ["""
CREATE OR REPLACE FUNCTION public.ai_mart_touch_dw() RETURNS trigger AS $$
BEGIN
    NEW.dw_updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""]
REVERSE = []

for t in TABLES:
    FORWARD.append(
        f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS dw_updated_at timestamptz;"
    )
    FORWARD.append(f"""
DROP TRIGGER IF EXISTS {t}_dw_touch ON {t};
CREATE TRIGGER {t}_dw_touch BEFORE INSERT OR UPDATE ON {t}
    FOR EACH ROW EXECUTE FUNCTION public.ai_mart_touch_dw();
""")
    FORWARD.append(
        f"CREATE INDEX IF NOT EXISTS {t}_dw_updated_idx ON {t} (dw_updated_at)"
        " WHERE dw_updated_at IS NOT NULL;"
    )
    REVERSE.append(f"DROP TRIGGER IF EXISTS {t}_dw_touch ON {t};")
    REVERSE.append(f"DROP INDEX IF EXISTS {t}_dw_updated_idx;")
    REVERSE.append(f"ALTER TABLE {t} DROP COLUMN IF EXISTS dw_updated_at;")

REVERSE.append("DROP FUNCTION IF EXISTS public.ai_mart_touch_dw();")


class Migration(migrations.Migration):
    dependencies = [
        ("ai_mart", "0001_schemas"),
        ("orders", "__latest__"),
        ("customers", "__latest__"),
        ("callcenter", "__latest__"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql="\n".join(FORWARD), reverse_sql="\n".join(REVERSE))
            ]
        )
    ]
```

⚠ `"__latest__"` không phải cú pháp Django hợp lệ — thay bằng migration MỚI NHẤT thực tế của từng app tại thời điểm code: chạy `ls apps/orders/migrations | tail -3` (tương tự customers, callcenter) và điền đúng tên. Partial index `WHERE dw_updated_at IS NOT NULL` giữ index nhỏ (đa số dòng cũ NULL).

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_triggers.py -v`
Expected: PASS 4/4.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/ai_mart/migrations/0002_dw_updated_at.py backend/apps/ai_mart/tests/test_triggers.py
git commit -m "feat(ai_mart): dw_updated_at watermark column and triggers on 5 source tables"
```
### Task 3: Từ điển mã trong domain

**Files:**
- Create: `backend/apps/ai_mart/domain/dictionary.py`
- Test: `backend/apps/ai_mart/tests/test_dictionary_domain.py`

**Interfaces:**
- Produces: `DICTIONARY: dict[str, list[tuple[str, str]]]` — nhóm mã → list `(ma, nhan)`; `dictionary_rows() -> list[tuple[str, str, str, int]]` trả `(nhom_ma, ma, nhan, thu_tu)` phẳng để seed. Task 4 seed từ đây; `test_dictionary.py` (Task 6) đối chiếu dữ liệu với đây.

- [ ] **Step 1: Viết test cấu trúc (fail trước)**

```python
# backend/apps/ai_mart/tests/test_dictionary_domain.py
"""Từ điển là NGUỒN SỰ THẬT — trùng mã trong 1 nhóm hay nhãn rỗng là hỏng cả mart."""

from apps.ai_mart.domain.dictionary import DICTIONARY, dictionary_rows


def test_no_duplicate_codes_within_group():
    for nhom, items in DICTIONARY.items():
        codes = [ma for ma, _ in items]
        assert len(codes) == len(set(codes)), f"trùng mã trong nhóm {nhom}"


def test_no_empty_labels():
    for nhom, ma, nhan, _ in dictionary_rows():
        assert nhan.strip(), f"nhãn rỗng: {nhom}/{ma}"


def test_core_groups_present():
    # Các nhóm mà transform Sprint 1-3 tra cứu — thiếu 1 nhóm là cột _text ra NULL hàng loạt.
    expected = {
        "trang_thai_don", "trang_thai_hoa_don", "loai_don", "loai_don_koc",
        "nguon_don", "hinh_thuc_giao", "trang_thai_sale", "ly_do_khong_chot",
        "trang_thai_giao_nghiep_vu", "nhom_ly_do_bom", "phan_loai_khach",
        "nghe_nghiep", "muc_do_dau", "nhom_tuoi", "gioi_tinh",
        "trang_thai_ton", "loai_bien_dong_kho", "loai_chung_tu_kho",
        "trang_thai_boc_bang",
    }
    assert expected <= set(DICTIONARY.keys())


def test_order_status_covers_all_enum_values():
    from apps.orders.models import OrderStatus

    codes = {ma for ma, _ in DICTIONARY["trang_thai_don"]}
    assert codes == {str(s.value) for s in OrderStatus}
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_dictionary_domain.py -v`
Expected: FAIL — module chưa tồn tại.

- [ ] **Step 3: Viết `domain/dictionary.py`**

Nguồn dữ liệu: import từ `apps.orders.crm_catalog` (hợp lệ theo tầng: ai_mart trên orders). Nhãn `Order.status` là bản chép từ `frontend/src/i18n/messages/status.ts` — từ giờ DB là nguồn sự thật (spec §3.3).

```python
# backend/apps/ai_mart/domain/dictionary.py
"""Từ điển mã → nhãn tiếng Việt của schema ai — NGUỒN SỰ THẬT DUY NHẤT.

Lấy tối đa từ crm_catalog + enum model (một chỗ sửa, mart tự phủ theo).
Riêng nhãn Order.status chép từ frontend/src/i18n/messages/status.ts vì Python
không import được TS — từ thời điểm seed, DB là nguồn sự thật, file TS là bản
trình bày (spec §3.3).
"""

from apps.orders import crm_catalog as cat
from apps.orders.models import KocOrderType, OrderSource, OrderType, ShippingType

# Chép từ frontend/src/i18n/messages/status.ts (khối "vi") — giữ khớp từng chữ.
_ORDER_STATUS_VI = {
    0: "Mới", 1: "Chờ xử lý", 2: "Đang giao hàng", 3: "Giao thành công",
    4: "Đang chuyển hoàn", 5: "Đã chuyển hoàn", 6: "Đã hủy", 7: "Đang lấy hàng",
    8: "Chờ lấy lại", 9: "Đã lấy hàng", 10: "Chờ giao lại", 11: "Chờ chuyển hàng",
    12: "Chờ chuyển hoàn lại", 13: "Chờ xác nhận hoàn",
}

# Nhãn tiếng Việt cho enum có label tiếng Anh trong model — model là nguồn MÃ,
# đây là nguồn NHÃN hiển thị.
_ORDER_TYPE_VI = {
    OrderType.NORMAL.value: "Đơn thường",
    OrderType.NO_RETURN.value: "Không thu hồi",
    OrderType.HAS_RETURN.value: "Có thu hồi",
}
_ORDER_SOURCE_VI = {
    OrderSource.LANDING.value: "Landing", OrderSource.KOL.value: "KOL",
    OrderSource.ZALO.value: "Zalo", OrderSource.MESSENGER.value: "Messenger",
    OrderSource.HOTLINE.value: "Hotline", OrderSource.KOC.value: "KOC",
    OrderSource.SHOPEE.value: "Shopee", OrderSource.TIKTOK.value: "TikTok",
}
_SHIPPING_TYPE_VI = {
    ShippingType.SHIPMENT.value: "Gửi vận chuyển",
    ShippingType.DIRECT.value: "Bán trực tiếp tại quầy",
}

# KiotViet invoice status — chỉ seed mã mà CODE của mình đã dùng và hiểu rõ
# (gtc.py: 2 = huỷ; service_actions.py:226: 1 = hoàn thành, 3 = đang xử lý).
# Mã lạ trên prod sẽ bị `ai_mart_verify` (Task 9) liệt kê — HỎI user, không đoán.
_INVOICE_STATUS_VI = {1: "Hoàn thành", 2: "Đã hủy", 3: "Đang xử lý"}

_GENDER_VI = {0: "Nữ", 1: "Nam"}

# Kho — chép nhãn từ chính TextChoices của apps/inventory/models.py (đã là tiếng Việt).
_STOCK_STATE_VI = {"sellable": "Bán được", "warranty": "Bảo hành", "defect": "Lỗi"}
_MOVEMENT_TYPE_VI = {
    "receipt": "Nhập kho", "issue": "Xuất kho", "adjustment": "Điều chỉnh",
    "transfer_in": "Chuyển đến", "transfer_out": "Chuyển đi", "sale": "Bán hàng",
    "sale_reversal": "Hoàn bán", "return_in": "Nhập trả hàng",
    "reclass_in": "Chuyển trạng thái (vào)", "reclass_out": "Chuyển trạng thái (ra)",
    "disposal": "Xuất huỷ/gửi lại",
}
_MOVEMENT_REF_VI = {
    "order": "Đơn hàng", "document": "Phiếu kho", "return": "Phiếu trả hàng (lịch sử)",
    "return_entry": "Nhóm đơn hoàn", "reclass": "Chuyển trạng thái tồn",
}
_TRANSCRIBE_STATUS_VI = {
    0: "Chờ transcribe", 1: "Đang transcribe", 2: "Hoàn thành",
    3: "Thất bại", 4: "Bỏ qua",
}


def _from_catalog(options) -> list[tuple[str, str]]:
    return [(o.code, o.label) for o in options]


def _from_map(mapping) -> list[tuple[str, str]]:
    return [(str(k), v) for k, v in mapping.items()]


DICTIONARY: dict[str, list[tuple[str, str]]] = {
    "trang_thai_don": _from_map(_ORDER_STATUS_VI),
    "trang_thai_hoa_don": _from_map(_INVOICE_STATUS_VI),
    "loai_don": _from_map(_ORDER_TYPE_VI),
    "loai_don_koc": [
        (KocOrderType.SAMPLE_GIFT.value, "Tặng mẫu"),
        (KocOrderType.RENEWAL.value, "Gia hạn"),
        (KocOrderType.RECALL.value, "Thu hồi"),
    ],
    "nguon_don": _from_map(_ORDER_SOURCE_VI),
    "hinh_thuc_giao": _from_map(_SHIPPING_TYPE_VI),
    "trang_thai_sale": _from_catalog(cat.SALES_STATUS_OPTIONS),
    "ly_do_khong_chot": _from_catalog(cat.NOT_CLOSED_REASON_OPTIONS),
    "trang_thai_giao_nghiep_vu": _from_catalog(cat.DELIVERY_BUSINESS_STATUS_OPTIONS),
    "nhom_ly_do_bom": [
        (item["code"], item["name_vi"]) for item in cat.BOMB_REASON_GROUP_SEED
    ],
    "phan_loai_khach": _from_catalog(cat.CUSTOMER_CLASSIFICATION_OPTIONS),
    "nghe_nghiep": _from_catalog(cat.OCCUPATION_OPTIONS),
    "muc_do_dau": _from_catalog(cat.PAIN_LEVEL_OPTIONS),
    "nhom_tuoi": _from_catalog(cat.AGE_GROUP_OPTIONS),
    "gioi_tinh": _from_map(_GENDER_VI),
    "trang_thai_ton": _from_map(_STOCK_STATE_VI),
    "loai_bien_dong_kho": _from_map(_MOVEMENT_TYPE_VI),
    "loai_chung_tu_kho": _from_map(_MOVEMENT_REF_VI),
    "trang_thai_boc_bang": _from_map(_TRANSCRIBE_STATUS_VI),
}


def dictionary_rows() -> list[tuple[str, str, str, int]]:
    """Phẳng hoá để seed: (nhom_ma, ma, nhan, thu_tu). thu_tu = vị trí khai báo × 10."""
    rows: list[tuple[str, str, str, int]] = []
    for nhom, items in DICTIONARY.items():
        for i, (ma, nhan) in enumerate(items):
            rows.append((nhom, ma, nhan, (i + 1) * 10))
    return rows
```

Ghi chú chủ đích (đối chiếu spec §3.1): `phuong_thuc_tt` KHÔNG có nhóm từ điển — `orders.payment_method` là CharField tự do, không phải closed set; transform sẽ passthrough giá trị thô (Task 6). `loai_cuoc_goi` cũng không cần nhóm — quy tắc `"1" = Gọi ra, còn lại = Gọi vào` chép từ `export_call_transcript.py:76`, nằm thẳng trong SQL (Task 11).

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_dictionary_domain.py -v`
Expected: PASS 4/4.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/ai_mart/domain backend/apps/ai_mart/tests/test_dictionary_domain.py
git commit -m "feat(ai_mart): code-to-label dictionary as single source of truth"
```

### Task 4: Bảng `ai.tu_dien_ma` + hàm `ai.nhan()` + `ai.che_sdt()` + seed

**Files:**
- Create: `backend/apps/ai_mart/migrations/0003_tu_dien_ma.py`
- Test: `backend/apps/ai_mart/tests/test_dictionary_table.py`

**Interfaces:**
- Consumes: `dictionary_rows()` từ Task 3.
- Produces: bảng `ai.tu_dien_ma(nhom_ma, ma, nhan, mo_ta, thu_tu)` PK `(nhom_ma, ma)`; hàm SQL `ai.nhan(nhom text, ma text) → text` (STABLE); hàm `ai.che_sdt(p text) → text` (IMMUTABLE). Mọi transform từ Task 6 trở đi gọi 2 hàm này.

- [ ] **Step 1: Viết test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_dictionary_table.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.dictionary import dictionary_rows


def _sql_one(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchone()[0]


class TuDienMaTests(TestCase):
    def test_seed_matches_domain(self):
        n = _sql_one("SELECT count(*) FROM ai.tu_dien_ma")
        self.assertEqual(n, len(dictionary_rows()))

    def test_nhan_function(self):
        self.assertEqual(_sql_one("SELECT ai.nhan('trang_thai_don', '3')"), "Giao thành công")
        self.assertEqual(
            _sql_one("SELECT ai.nhan('trang_thai_sale', 'no_answer_5')"),
            "Không nghe máy lần 5",
        )
        self.assertIsNone(_sql_one("SELECT ai.nhan('trang_thai_don', '999')"))
        self.assertIsNone(_sql_one("SELECT ai.nhan('trang_thai_don', NULL)"))

    def test_che_sdt(self):
        self.assertEqual(_sql_one("SELECT ai.che_sdt('0912345678')"), "0912***678")
        self.assertIsNone(_sql_one("SELECT ai.che_sdt('')"))
        self.assertIsNone(_sql_one("SELECT ai.che_sdt(NULL)"))
        # SĐT ngắn bất thường: không được lộ chữ số nào.
        self.assertEqual(_sql_one("SELECT ai.che_sdt('12345')"), "***")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_dictionary_table.py -v`
Expected: FAIL — bảng/hàm chưa tồn tại.

- [ ] **Step 3: Migration 0003**

```python
# backend/apps/ai_mart/migrations/0003_tu_dien_ma.py
"""Bảng từ điển + 2 hàm SQL nền của schema ai.

Seed bằng RunPython đọc domain/dictionary.py — một nguồn, migration không chép tay.
Migration này chỉ seed lần đầu; đổi nhãn về sau đi bằng data migration mới
(INSERT ... ON CONFLICT DO UPDATE) để giữ lịch sử migrate tuyến tính.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.tu_dien_ma (
    nhom_ma text NOT NULL,
    ma      text NOT NULL,
    nhan    text NOT NULL,
    mo_ta   text,
    thu_tu  int  NOT NULL DEFAULT 0,
    PRIMARY KEY (nhom_ma, ma)
);
COMMENT ON TABLE ai.tu_dien_ma IS
    'Từ điển mã → nhãn tiếng Việt. Muốn biết một cột *_ma có những giá trị nào, SELECT theo nhom_ma.';
COMMENT ON COLUMN ai.tu_dien_ma.nhom_ma IS 'Nhóm mã, vd trang_thai_don, trang_thai_sale';
COMMENT ON COLUMN ai.tu_dien_ma.ma IS 'Mã gốc trong dữ liệu vận hành';
COMMENT ON COLUMN ai.tu_dien_ma.nhan IS 'Nhãn tiếng Việt hiển thị cho người đọc';
COMMENT ON COLUMN ai.tu_dien_ma.mo_ta IS 'Giải thích thêm ngữ nghĩa/bẫy của mã, có thể NULL';
COMMENT ON COLUMN ai.tu_dien_ma.thu_tu IS 'Thứ tự trình bày trong nhóm';

CREATE OR REPLACE FUNCTION ai.nhan(p_nhom text, p_ma text) RETURNS text AS $$
    SELECT t.nhan FROM ai.tu_dien_ma t WHERE t.nhom_ma = p_nhom AND t.ma = p_ma;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION ai.che_sdt(p text) RETURNS text AS $$
    SELECT CASE
        WHEN NULLIF(btrim(coalesce(p, '')), '') IS NULL THEN NULL
        WHEN length(btrim(p)) >= 8 THEN left(btrim(p), 4) || '***' || right(btrim(p), 3)
        ELSE '***'
    END;
$$ LANGUAGE sql IMMUTABLE;
"""

DROP = """
DROP FUNCTION IF EXISTS ai.che_sdt(text);
DROP FUNCTION IF EXISTS ai.nhan(text, text);
DROP TABLE IF EXISTS ai.tu_dien_ma;
"""


def seed(apps, schema_editor):
    from apps.ai_mart.domain.dictionary import dictionary_rows

    with schema_editor.connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (nhom_ma, ma) DO UPDATE SET nhan = EXCLUDED.nhan,"
            " thu_tu = EXCLUDED.thu_tu",
            dictionary_rows(),
        )
        # mo_ta cho các mã có bẫy ngữ nghĩa đã biết
        cur.execute(
            "UPDATE ai.tu_dien_ma SET mo_ta ="
            " 'Đã hỏi nhưng khách không nói — KHÁC với NULL nghĩa là sale chưa hỏi'"
            " WHERE nhom_ma = 'nhom_tuoi' AND ma = 'not_disclosed'"
        )
        cur.execute(
            "UPDATE ai.tu_dien_ma SET mo_ta ="
            " 'Giá trị lịch sử trước khi tách phiếu ngày — movement mới không dùng'"
            " WHERE nhom_ma = 'loai_chung_tu_kho' AND ma = 'return'"
        )


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0002_dw_updated_at")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=DDL, reverse_sql=DROP),
                migrations.RunPython(seed, migrations.RunPython.noop),
            ]
        )
    ]
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_dictionary_table.py apps/ai_mart/tests/test_dictionary_domain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/ai_mart/migrations/0003_tu_dien_ma.py backend/apps/ai_mart/tests/test_dictionary_table.py
git commit -m "feat(ai_mart): tu_dien_ma table with nhan() and che_sdt() helpers"
```

### Task 5: Hạ tầng ETL — `MartTable`, meta tables, loaders

**Files:**
- Create: `backend/apps/ai_mart/migrations/0004_meta_tables.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/base.py`
- Create: `backend/apps/ai_mart/infrastructure/loaders.py`
- Test: `backend/apps/ai_mart/tests/test_loaders.py`

**Interfaces:**
- Produces:
  - `MartTable` (frozen dataclass): `name: str` (`"ai.don_hang"`), `key_column: str`, `group: str` (`"hot"`/`"nightly"`), `select_sql: Callable[[str], str]` (nhận mệnh đề WHERE, trả câu SELECT đầy đủ), `candidate_ids_sql: str | None` (SQL trả 1 cột id, param `%(cutoff)s`).
  - `read_watermark(table_name: str) -> datetime | None`
  - `write_watermark(table_name: str, moc: datetime) -> None`
  - `upsert_by_ids(mart: MartTable, ids: list[int]) -> int` — DELETE theo key + INSERT-select `WHERE <key nguồn> = ANY(ids)`, 1 transaction, trả số dòng insert.
  - `full_rebuild(mart: MartTable) -> int` — TRUNCATE + INSERT full, 1 transaction.
  - `log_run(run_id, table_name, started_at, finished_at, rows, status, error="") -> None` — ghi `ai_meta.nhat_ky_chay`, nuốt lỗi ghi log (không đánh hỏng lượt chạy — tiền lệ `_record_refresh` của analytics).

- [ ] **Step 1: Migration 0004 meta tables**

```python
# backend/apps/ai_mart/migrations/0004_meta_tables.py
"""Watermark + nhật ký chạy của ETL. Nằm ở ai_meta — hermes_ro KHÔNG thấy."""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai_meta.moc_dong_bo (
    ten_bang text PRIMARY KEY,
    moc      timestamptz,
    moc_id   bigint
);
COMMENT ON TABLE ai_meta.moc_dong_bo IS
    'Watermark ETL: moc cho bảng theo dw_updated_at, moc_id cho bảng append-only (stock_movements)';

CREATE TABLE IF NOT EXISTS ai_meta.nhat_ky_chay (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL,
    ten_bang text NOT NULL,
    bat_dau timestamptz NOT NULL,
    ket_thuc timestamptz,
    so_dong int,
    trang_thai text NOT NULL,
    loi text NOT NULL DEFAULT ''
);
COMMENT ON TABLE ai_meta.nhat_ky_chay IS 'Nhật ký từng lượt nạp một bảng mart. trang_thai: ok | failed';
CREATE INDEX IF NOT EXISTS nhat_ky_chay_bang_thoi_gian_idx
    ON ai_meta.nhat_ky_chay (ten_bang, bat_dau DESC);
"""

DROP = """
DROP TABLE IF EXISTS ai_meta.nhat_ky_chay;
DROP TABLE IF EXISTS ai_meta.moc_dong_bo;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0003_tu_dien_ma")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
```

- [ ] **Step 2: Viết test loaders (fail trước)**

```python
# backend/apps/ai_mart/tests/test_loaders.py
"""Kiểm hạ tầng nạp bằng một mart giả trỏ vào bảng tạm — không phụ thuộc mart thật."""

import uuid
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.base import MartTable

FAKE = MartTable(
    name="ai_stg.test_fake_mart",
    key_column="don_id",
    group="hot",
    select_sql=lambda where: (
        f"SELECT s.id AS don_id, s.val AS gia_tri FROM ai_stg.test_fake_src s WHERE {where}"
    ),
    candidate_ids_sql="SELECT id FROM ai_stg.test_fake_src WHERE changed_at > %(cutoff)s",
)


class LoaderTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS ai_stg.test_fake_src"
                " (id bigint PRIMARY KEY, val text, changed_at timestamptz);"
                "CREATE TABLE IF NOT EXISTS ai_stg.test_fake_mart"
                " (don_id bigint PRIMARY KEY, gia_tri text);"
            )

    def _seed(self, rows):
        with connection.cursor() as cur:
            cur.executemany(
                "INSERT INTO ai_stg.test_fake_src VALUES (%s, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET val=EXCLUDED.val, changed_at=EXCLUDED.changed_at",
                rows,
            )

    def test_watermark_roundtrip(self):
        self.assertIsNone(loaders.read_watermark("x"))
        now = timezone.now()
        loaders.write_watermark("x", now)
        self.assertEqual(loaders.read_watermark("x"), now)
        loaders.write_watermark("x", now + timedelta(minutes=1))
        self.assertEqual(loaders.read_watermark("x"), now + timedelta(minutes=1))

    def test_full_rebuild_then_upsert(self):
        now = timezone.now()
        self._seed([(1, "a", now), (2, "b", now)])
        n = loaders.full_rebuild(FAKE)
        self.assertEqual(n, 2)
        # Sửa 1 dòng + xoá 1 dòng nguồn → upsert theo ids phải phản ánh cả hai
        self._seed([(1, "a2", now)])
        with connection.cursor() as cur:
            cur.execute("DELETE FROM ai_stg.test_fake_src WHERE id = 2")
        loaders.upsert_by_ids(FAKE, [1, 2])
        with connection.cursor() as cur:
            cur.execute("SELECT don_id, gia_tri FROM ai_stg.test_fake_mart ORDER BY don_id")
            self.assertEqual(cur.fetchall(), [(1, "a2")])

    def test_upsert_empty_ids_is_noop(self):
        self.assertEqual(loaders.upsert_by_ids(FAKE, []), 0)

    def test_log_run_never_raises(self):
        # ten_bang dài bất thường vẫn không được ném lỗi ra ngoài
        loaders.log_run(uuid.uuid4(), "x" * 10, timezone.now(), timezone.now(), 1, "ok")
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_loaders.py -v`
Expected: FAIL — module chưa tồn tại.

- [ ] **Step 4: Viết `sql/base.py` + `loaders.py`**

```python
# backend/apps/ai_mart/infrastructure/sql/base.py
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MartTable:
    """Mô tả một bảng mart: transform là hàm sinh SELECT, loader lo phần còn lại.

    select_sql nhận mệnh đề WHERE (không kèm từ khoá WHERE) — full_rebuild truyền
    scope mặc định, upsert truyền scope + lọc id. Nhờ đó CHỈ CÓ MỘT câu SELECT
    cho cả hai đường nạp: hết loại bug "hai nhánh code lệch nhau".
    """

    name: str
    key_column: str
    group: str  # "hot" | "nightly"
    select_sql: Callable[[str], str]
    candidate_ids_sql: str | None = None
```

```python
# backend/apps/ai_mart/infrastructure/loaders.py
"""Nạp dữ liệu vào schema ai. Target đi qua alias connection Django — đổi target
là đổi env, không phải viết lại transform (spec §2, giữ Q1/Q3 đảo ngược được)."""

import logging

from django.db import connections, transaction

from apps.ai_mart.infrastructure.sql.base import MartTable

logger = logging.getLogger(__name__)

TARGET_ALIAS = "default"


def _conn():
    return connections[TARGET_ALIAS]


def read_watermark(table_name: str):
    with _conn().cursor() as cur:
        cur.execute("SELECT moc FROM ai_meta.moc_dong_bo WHERE ten_bang = %s", [table_name])
        row = cur.fetchone()
    return row[0] if row else None


def write_watermark(table_name: str, moc) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "INSERT INTO ai_meta.moc_dong_bo (ten_bang, moc) VALUES (%s, %s)"
            " ON CONFLICT (ten_bang) DO UPDATE SET moc = EXCLUDED.moc",
            [table_name, moc],
        )


def read_watermark_id(table_name: str) -> int:
    with _conn().cursor() as cur:
        cur.execute("SELECT moc_id FROM ai_meta.moc_dong_bo WHERE ten_bang = %s", [table_name])
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else 0


def write_watermark_id(table_name: str, moc_id: int) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "INSERT INTO ai_meta.moc_dong_bo (ten_bang, moc_id) VALUES (%s, %s)"
            " ON CONFLICT (ten_bang) DO UPDATE SET moc_id = EXCLUDED.moc_id",
            [table_name, moc_id],
        )


def upsert_by_ids(mart: MartTable, ids: list[int]) -> int:
    """DELETE theo key rồi INSERT-select — một transaction.

    DELETE+INSERT thay vì ON CONFLICT: (1) không phải liệt kê 40 cột hai lần,
    (2) dòng RỜI KHỎI scope (xoá mềm, thành đơn mirror) được dọn đúng — ON
    CONFLICT không xoá được dòng đã mất khỏi SELECT.
    """
    if not ids:
        return 0
    select = mart.select_sql(f"{_source_key(mart)} = ANY(%(ids)s)")
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            cur.execute(
                f"DELETE FROM {mart.name} WHERE {mart.key_column} = ANY(%(ids)s)",
                {"ids": ids},
            )
            cur.execute(f"INSERT INTO {mart.name} {select}", {"ids": ids})
            return cur.rowcount


def full_rebuild(mart: MartTable) -> int:
    select = mart.select_sql("TRUE")
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            cur.execute(f"TRUNCATE {mart.name}")
            cur.execute(f"INSERT INTO {mart.name} {select}")
            return cur.rowcount


def candidate_ids(mart: MartTable, cutoff) -> list[int]:
    if mart.candidate_ids_sql is None:
        return []
    with _conn().cursor() as cur:
        cur.execute(mart.candidate_ids_sql, {"cutoff": cutoff})
        return [row[0] for row in cur.fetchall()]


def _source_key(mart: MartTable) -> str:
    """Cột id NGUỒN mà select_sql hiểu trong mệnh đề WHERE.

    Quy ước: mỗi module sql đặt alias bảng gốc sao cho `<alias>.id` là khoá —
    don_hang: o.id, cuoc_goi: r.id... Module khai báo qua thuộc tính động
    `source_key` gắn lên MART (xem sql/don_hang.py); mặc định 'o.id'.
    """
    return getattr(mart, "source_key", "o.id")


def log_run(run_id, table_name, started_at, finished_at, rows, status, error="") -> None:
    try:
        with _conn().cursor() as cur:
            cur.execute(
                "INSERT INTO ai_meta.nhat_ky_chay"
                " (run_id, ten_bang, bat_dau, ket_thuc, so_dong, trang_thai, loi)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [run_id, table_name, started_at, finished_at, rows, status, error[:2000]],
            )
    except Exception:
        logger.warning("ghi ai_meta.nhat_ky_chay cho %s thất bại", table_name, exc_info=True)
```

⚠ `MartTable` là frozen dataclass — `source_key` không gắn động được lên instance. Sửa: thêm field `source_key: str = "o.id"` vào dataclass (Task 5 này, ngay từ đầu) và `_source_key` đọc `mart.source_key`. Đây là hợp đồng cho MỌI module sql sau.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_loaders.py -v`
Expected: PASS 4/4.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/ai_mart/migrations/0004_meta_tables.py backend/apps/ai_mart/infrastructure backend/apps/ai_mart/tests/test_loaders.py
git commit -m "feat(ai_mart): meta tables and generic mart loaders"
```
### Task 6: Bảng `ai.don_hang` — DDL, transform, test nội dung + test contract

**Files:**
- Create: `backend/apps/ai_mart/migrations/0005_don_hang.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/don_hang.py`
- Test: `backend/apps/ai_mart/tests/test_don_hang.py`, `backend/apps/ai_mart/tests/test_contract.py`

**Interfaces:**
- Consumes: `MartTable`, loaders (Task 5); `ai.nhan`/`ai.che_sdt` (Task 4); `gtc_where`, `BUSINESS_TZ`, `sql_exclude_mirror_orders`.
- Produces: `apps.ai_mart.infrastructure.sql.don_hang.MART` (`name="ai.don_hang"`, `key_column="don_id"`, `source_key="o.id"`, `group="hot"`); scope chuẩn `_SCOPE = "o.deleted_at IS NULL AND <mirror>"`.

- [ ] **Step 1: Viết test nội dung (fail trước)**

```python
# backend/apps/ai_mart/tests/test_don_hang.py
"""Kiểm bản dịch: dựng đơn thật qua ORM, full_rebuild, soi từng cột ra nhãn đúng.

Dùng chung fixture đơn của apps/orders (bám codebase — xem Task 2 Step 1b).
"""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.don_hang import MART
from apps.orders.models import Order, OrderAssessment
from apps.orders.tests.factories import OrderFactory


def _row(order_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT trang_thai_don_ma, trang_thai_don_text, trang_thai_sale_text,"
            " sdt_che, ngay_tao_ngay, la_don_gtc, tuoi_text, tuoi_nguon_text"
            " FROM ai.don_hang WHERE don_id = %s",
            [order_id],
        )
        cols = [c[0] for c in cur.description]
        r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


class DonHangTransformTests(TestCase):
    def test_status_and_sales_status_translated(self):
        order = OrderFactory(status=3)
        OrderAssessment.objects.update_or_create(
            order=order, defaults={"sales_status_code": "no_answer_5", "age_text": "45_54"}
        )
        loaders.full_rebuild(MART)
        row = _row(order.id)
        self.assertEqual(row["trang_thai_don_ma"], 3)
        self.assertEqual(row["trang_thai_don_text"], "Giao thành công")
        self.assertEqual(row["trang_thai_sale_text"], "Không nghe máy lần 5")
        self.assertEqual(row["tuoi_text"], "45-54")
        self.assertEqual(row["tuoi_nguon_text"], "Sale nhập")

    def test_empty_age_becomes_null(self):
        order = OrderFactory(status=0)
        OrderAssessment.objects.update_or_create(order=order, defaults={"age_text": "   "})
        loaders.full_rebuild(MART)
        row = _row(order.id)
        self.assertIsNone(row["tuoi_text"])
        self.assertIsNone(row["tuoi_nguon_text"])

    def test_mirror_and_soft_deleted_excluded(self):
        kept = OrderFactory(status=0)
        mirror = OrderFactory(status=0, order_source="foxia")
        gone = OrderFactory(status=0)
        Order.objects.filter(id=gone.id).update(deleted_at="2026-01-01T00:00:00Z")
        loaders.full_rebuild(MART)
        self.assertIsNotNone(_row(kept.id))
        self.assertIsNone(_row(mirror.id))
        self.assertIsNone(_row(gone.id))

    def test_incremental_upsert_reflects_status_change_via_queryset_update(self):
        order = OrderFactory(status=2)
        loaders.full_rebuild(MART)
        cutoff = loaders.read_watermark("ai.don_hang")  # None lần đầu — dùng mốc giả
        from django.utils import timezone
        cutoff = timezone.now()
        Order.objects.filter(id=order.id).update(status=3)  # đường webhook KiotViet
        ids = loaders.candidate_ids(MART, cutoff)
        self.assertIn(order.id, ids)
        loaders.upsert_by_ids(MART, ids)
        self.assertEqual(_row(order.id)["trang_thai_don_text"], "Giao thành công")
```

- [ ] **Step 2: Viết test contract (fail trước) — tự phủ MỌI bảng `ai` về sau**

```python
# backend/apps/ai_mart/tests/test_contract.py
"""Hợp đồng schema ai: mọi bảng, mọi cột PHẢI có COMMENT tiếng Việt (spec §4.7).

Introspect pg_catalog nên bảng thêm ở sprint sau TỰ ĐỘNG bị kiểm — không phải
sửa test. Đây là 'một bộ test theo cấu trúc hệ thống' mà CLAUDE.md yêu cầu.
"""

from django.db import connection
from django.test import TestCase


class AiSchemaContractTests(TestCase):
    def _tables(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai' AND c.relkind IN ('r', 'v')"
            )
            return [r[0] for r in cur.fetchall()]

    def test_every_table_and_column_has_comment(self):
        missing = []
        with connection.cursor() as cur:
            for table in self._tables():
                cur.execute(
                    "SELECT obj_description(('ai.' || quote_ident(%s))::regclass)", [table]
                )
                if not (cur.fetchone()[0] or "").strip():
                    missing.append(f"ai.{table} (bảng)")
                cur.execute(
                    "SELECT a.attname, col_description(a.attrelid, a.attnum)"
                    " FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attnum > 0 AND NOT a.attisdropped",
                    [table],
                )
                for col, comment in cur.fetchall():
                    if not (comment or "").strip():
                        missing.append(f"ai.{table}.{col}")
        self.assertEqual(missing, [], f"thiếu COMMENT: {missing}")

    def test_no_forbidden_pii_columns(self):
        # spec §4.6 — không cột nào mang email/địa chỉ chi tiết/SĐT thô
        forbidden = ("email", "dia_chi", "address", "phone_raw", "sdt_goc")
        bad = []
        with connection.cursor() as cur:
            for table in self._tables():
                cur.execute(
                    "SELECT a.attname FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attnum > 0 AND NOT a.attisdropped",
                    [table],
                )
                for (col,) in cur.fetchall():
                    if any(f in col for f in forbidden):
                        bad.append(f"ai.{table}.{col}")
        self.assertEqual(bad, [])
```

- [ ] **Step 3: Chạy 2 test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_don_hang.py apps/ai_mart/tests/test_contract.py -v`
Expected: FAIL — bảng/module chưa có.

- [ ] **Step 4: Viết `sql/don_hang.py`**

```python
# backend/apps/ai_mart/infrastructure/sql/don_hang.py
"""Transform ai.don_hang — 1 dòng/đơn nội bộ. Cấu trúc CTE mượn từ
analytics/0003_fct_order_enriched (call_scores/calls/items/needs đã được
soi kỹ ở đó — giữ nguyên các quyết định: điểm cuộc gọi GẦN NHẤT chứ không
max; DISTINCT ON trước khi JOIN để không nhân dòng theo lần chấm lại)."""

from apps.analytics.sql.gtc import gtc_where
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.common.order_scope import sql_exclude_mirror_orders

_SCOPE = f"o.deleted_at IS NULL AND {sql_exclude_mirror_orders('o')}"

_CANDIDATES = """
SELECT o.id FROM orders o WHERE o.dw_updated_at > %(cutoff)s
UNION
SELECT oa.order_id FROM order_assessments oa WHERE oa.dw_updated_at > %(cutoff)s
UNION
SELECT oi.order_id FROM order_items oi WHERE oi.dw_updated_at > %(cutoff)s
UNION
SELECT r.order_id FROM callcenter_call_records r
 WHERE r.dw_updated_at > %(cutoff)s AND r.order_id IS NOT NULL
"""


def _select(where: str) -> str:
    return f"""
WITH items AS (
    SELECT oi.order_id,
           count(*)::int AS so_dong_sp,
           array_remove(array_agg(oi.sku), NULL) AS skus,
           (array_agg(oi.sku ORDER BY oi.line_total DESC NULLS LAST))[1] AS sku_chinh
    FROM order_items oi GROUP BY oi.order_id
),
call_scores AS (
    SELECT DISTINCT ON (s.call_record_id) s.call_record_id, s.total_score
    FROM callcenter_call_quality_scores s
    ORDER BY s.call_record_id, s.created_at DESC, s.id DESC
),
calls AS (
    SELECT r.order_id,
           count(*)::int AS so_cuoc_goi,
           coalesce(sum(r.acd_time), 0)::int AS tong_giay_goi,
           bool_or(NULLIF(btrim(coalesce(r.transcript, '')), '') IS NOT NULL) AS co_transcript,
           bool_or(r.extract_payload IS NOT NULL
                   AND r.extract_payload <> '{{}}'::jsonb) AS da_boc_ai,
           (array_agg(cs.total_score ORDER BY r.start_time DESC NULLS LAST, r.id DESC)
               FILTER (WHERE cs.total_score IS NOT NULL))[1] AS diem_chat_luong
    FROM callcenter_call_records r
    LEFT JOIN call_scores cs ON cs.call_record_id = r.id
    WHERE r.order_id IS NOT NULL
    GROUP BY r.order_id
),
needs AS (
    SELECT oa.order_id,
           array_agg(DISTINCT nt.code) AS nhu_cau_ma,
           string_agg(DISTINCT nt.name_vi, ', ') AS nhu_cau_text
    FROM order_assessments oa
    JOIN order_needs onn ON onn.order_assessment_id = oa.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    GROUP BY oa.order_id
)
SELECT
    o.id AS don_id,
    o.order_code AS ma_don,
    o.created_at AS ngay_tao,
    (o.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_tao_ngay,
    o.sales_closed_at AS ngay_chot,
    (o.sales_closed_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_chot_ngay,
    o.status AS trang_thai_don_ma,
    ai.nhan('trang_thai_don', o.status::text) AS trang_thai_don_text,
    o.invoice_status AS trang_thai_hoa_don_ma,
    ai.nhan('trang_thai_hoa_don', o.invoice_status::text) AS trang_thai_hoa_don_text,
    ({gtc_where("o")}) AS la_don_gtc,
    NULLIF(btrim(coalesce(o.order_type, '')), '') AS loai_don_ma,
    ai.nhan('loai_don', NULLIF(btrim(coalesce(o.order_type, '')), '')) AS loai_don_text,
    NULLIF(btrim(coalesce(o.koc_order_type, '')), '') AS loai_don_koc_ma,
    ai.nhan('loai_don_koc', NULLIF(btrim(coalesce(o.koc_order_type, '')), '')) AS loai_don_koc_text,
    NULLIF(btrim(coalesce(o.order_source, '')), '') AS nguon_don_ma,
    coalesce(ai.nhan('nguon_don', NULLIF(btrim(coalesce(o.order_source, '')), '')),
             NULLIF(btrim(coalesce(o.order_source, '')), '')) AS nguon_don_text,
    o.shipping_type AS hinh_thuc_giao_ma,
    ai.nhan('hinh_thuc_giao', o.shipping_type) AS hinh_thuc_giao_text,
    NULLIF(btrim(coalesce(o.payment_method, '')), '') AS phuong_thuc_tt,
    o.channel_id AS kenh_id,
    ch.name AS kenh_ten,
    ct.name AS loai_kenh_text,
    o.staff_id AS nhan_vien_id,
    NULLIF(btrim(concat_ws(' ', u.last_name, u.first_name)), '') AS nhan_vien_ten,
    o.customer_id AS khach_id,
    c.code AS ma_khach,
    NULLIF(btrim(coalesce(c.full_name, '')), '') AS ten_khach,
    ai.che_sdt(c.phone_normalized) AS sdt_che,
    NULLIF(btrim(coalesce(o.province, '')), '') AS tinh_thanh,
    NULLIF(btrim(coalesce(o.district, '')), '') AS quan_huyen,
    c.is_new_customer AS la_khach_moi,
    (o.total_after_discount + coalesce(o.discount_amount, 0)) AS tien_hang,
    o.discount_amount AS giam_gia,
    o.shipping_fee AS phi_ship,
    o.total_after_discount AS tong_sau_giam,
    o.quantity AS so_luong,
    coalesce(i.so_dong_sp, 0) AS so_dong_sp,
    coalesce(i.skus, '{{}}') AS danh_sach_sku,
    NULLIF(array_to_string(i.skus, ', '), '') AS danh_sach_sku_text,
    i.sku_chinh,
    oa.sales_status_code AS trang_thai_sale_ma,
    ai.nhan('trang_thai_sale', oa.sales_status_code) AS trang_thai_sale_text,
    oa.not_closed_reason_code AS ly_do_khong_chot_ma,
    ai.nhan('ly_do_khong_chot', oa.not_closed_reason_code) AS ly_do_khong_chot_text,
    oa.delivery_business_status_code AS trang_thai_giao_nghiep_vu_ma,
    ai.nhan('trang_thai_giao_nghiep_vu', oa.delivery_business_status_code)
        AS trang_thai_giao_nghiep_vu_text,
    oa.bomb_reason_group_code AS nhom_ly_do_bom_ma,
    ai.nhan('nhom_ly_do_bom', oa.bomb_reason_group_code) AS nhom_ly_do_bom_text,
    oa.customer_classification_code AS phan_loai_khach_ma,
    ai.nhan('phan_loai_khach', oa.customer_classification_code) AS phan_loai_khach_text,
    coalesce(n.nhu_cau_ma, '{{}}') AS nhu_cau_ma,
    n.nhu_cau_text,
    ai.nhan('nhom_tuoi', NULLIF(btrim(coalesce(oa.age_text, '')), '')) AS tuoi_text,
    CASE
        WHEN NULLIF(btrim(coalesce(oa.age_text, '')), '') IS NULL THEN NULL
        WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? 'age_text'
            THEN 'AI bóc từ ghi âm'
        ELSE 'Sale nhập'
    END AS tuoi_nguon_text,
    oa.occupation_code AS nghe_nghiep_ma,
    ai.nhan('nghe_nghiep', oa.occupation_code) AS nghe_nghiep_text,
    oa.pain_level_code AS muc_do_dau_ma,
    ai.nhan('muc_do_dau', oa.pain_level_code) AS muc_do_dau_text,
    coalesce(cl.so_cuoc_goi, 0) AS so_cuoc_goi,
    coalesce(cl.tong_giay_goi, 0) AS tong_giay_goi,
    coalesce(cl.co_transcript, false) AS co_transcript,
    coalesce(cl.da_boc_ai, false) AS da_boc_ai,
    cl.diem_chat_luong,
    now() AS dong_bo_luc
FROM orders o
LEFT JOIN customers c ON c.id = o.customer_id
LEFT JOIN channels ch ON ch.id = o.channel_id
LEFT JOIN channel_types ct ON ct.id = ch.channel_type_id
LEFT JOIN users u ON u.id = o.staff_id
LEFT JOIN order_assessments oa ON oa.order_id = o.id
LEFT JOIN items i ON i.order_id = o.id
LEFT JOIN calls cl ON cl.order_id = o.id
LEFT JOIN needs n ON n.order_id = o.id
WHERE {_SCOPE} AND ({where})
"""


MART = MartTable(
    name="ai.don_hang",
    key_column="don_id",
    source_key="o.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
```

⚠ Kiểm tra 2 điểm trước khi chạy: (1) tên bảng users là `users` — xác nhận bằng `grep -n "db_table" apps/users/models.py`; nếu khác (vd `auth_user`), sửa JOIN. (2) `channels.channel_type_id` — xác nhận bằng `grep -n "channel_type" apps/channels_core/models.py`; FK nullable nên LEFT JOIN là đúng. (3) `age_text` chứa MÃ nhóm tuổi (models.py:601 ghi rõ "Mã nhóm tuổi (crm_catalog.AGE_GROUP_OPTIONS), KHÔNG phải text tự do") — vì vậy `tuoi_text` đi qua `ai.nhan('nhom_tuoi', ...)`, không passthrough.

Ghi chú chủ đích: `phuong_thuc_tt` là MỘT cột passthrough (không cặp `_ma`/`_text`) vì `payment_method` là text tự do — lệch spec §3.1 có chủ đích, đã ghi ở Task 3.

- [ ] **Step 5: Migration 0005 — DDL + COMMENT đầy đủ**

Viết `backend/apps/ai_mart/migrations/0005_don_hang.py` theo đúng khuôn 0004 (`SeparateDatabaseAndState` + `RunSQL`), `dependencies = [("ai_mart", "0004_meta_tables")]`. DDL:

```sql
CREATE TABLE IF NOT EXISTS ai.don_hang (
    don_id bigint PRIMARY KEY,
    ma_don text,
    ngay_tao timestamptz,
    ngay_tao_ngay date,
    ngay_chot timestamptz,
    ngay_chot_ngay date,
    trang_thai_don_ma smallint,
    trang_thai_don_text text,
    trang_thai_hoa_don_ma smallint,
    trang_thai_hoa_don_text text,
    la_don_gtc boolean NOT NULL DEFAULT false,
    loai_don_ma text, loai_don_text text,
    loai_don_koc_ma text, loai_don_koc_text text,
    nguon_don_ma text, nguon_don_text text,
    hinh_thuc_giao_ma text, hinh_thuc_giao_text text,
    phuong_thuc_tt text,
    kenh_id bigint, kenh_ten text, loai_kenh_text text,
    nhan_vien_id bigint, nhan_vien_ten text,
    khach_id bigint, ma_khach text, ten_khach text, sdt_che text,
    tinh_thanh text, quan_huyen text, la_khach_moi boolean,
    tien_hang numeric, giam_gia numeric, phi_ship numeric,
    tong_sau_giam numeric, so_luong int,
    so_dong_sp int, danh_sach_sku text[], danh_sach_sku_text text, sku_chinh text,
    trang_thai_sale_ma text, trang_thai_sale_text text,
    ly_do_khong_chot_ma text, ly_do_khong_chot_text text,
    trang_thai_giao_nghiep_vu_ma text, trang_thai_giao_nghiep_vu_text text,
    nhom_ly_do_bom_ma text, nhom_ly_do_bom_text text,
    phan_loai_khach_ma text, phan_loai_khach_text text,
    nhu_cau_ma text[], nhu_cau_text text,
    tuoi_text text, tuoi_nguon_text text,
    nghe_nghiep_ma text, nghe_nghiep_text text,
    muc_do_dau_ma text, muc_do_dau_text text,
    so_cuoc_goi int, tong_giay_goi int,
    co_transcript boolean, da_boc_ai boolean, diem_chat_luong numeric,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS don_hang_ngay_tao_ngay_idx ON ai.don_hang (ngay_tao_ngay);
CREATE INDEX IF NOT EXISTS don_hang_khach_idx ON ai.don_hang (khach_id);
CREATE INDEX IF NOT EXISTS don_hang_gtc_ngay_idx ON ai.don_hang (ngay_tao_ngay) WHERE la_don_gtc;
CREATE INDEX IF NOT EXISTS don_hang_trang_thai_idx ON ai.don_hang (trang_thai_don_text);
```

COMMENT — BẮT BUỘC đủ 63 cột + bảng, mẫu (viết đủ trong migration, mỗi cột một câu ngữ nghĩa, không lặp tên cột):

```sql
COMMENT ON TABLE ai.don_hang IS
 'Mỗi dòng = 1 đơn hàng nội bộ (đã loại bản sao từ đối tác foxia/cnv). Doanh thu chuẩn: SUM(tong_sau_giam) WHERE la_don_gtc.';
COMMENT ON COLUMN ai.don_hang.don_id IS 'Id đơn trong hệ vận hành — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.don_hang.ma_don IS 'Mã đơn hiển thị trên CRM, vd DL12345';
COMMENT ON COLUMN ai.don_hang.ngay_chot IS 'Thời điểm sale chốt đơn. Cột nguồn mới có từ 08/2026 và CHƯA backfill — NULL ở đơn cũ KHÔNG có nghĩa là chưa chốt';
COMMENT ON COLUMN ai.don_hang.la_don_gtc IS 'true = đơn giao thành công hợp lệ để tính doanh thu (đã loại hóa đơn hủy, chi nhánh loại trừ)';
COMMENT ON COLUMN ai.don_hang.tien_hang IS 'VND, trước giảm giá — suy từ tong_sau_giam + giam_gia';
COMMENT ON COLUMN ai.don_hang.tong_sau_giam IS 'VND, sau giảm giá, CHƯA gồm phí ship';
COMMENT ON COLUMN ai.don_hang.sdt_che IS 'SĐT khách đã che giữa (0912***678) — không có bản đầy đủ trong schema này';
COMMENT ON COLUMN ai.don_hang.tuoi_nguon_text IS 'Nguồn của tuoi_text: "Sale nhập" hoặc "AI bóc từ ghi âm" — AI không đè số sale nhập';
COMMENT ON COLUMN ai.don_hang.phuong_thuc_tt IS 'Text tự do từ hệ vận hành, không phải danh mục đóng';
-- ... (viết đủ cho TẤT CẢ cột còn lại — test_contract sẽ FAIL nếu thiếu bất kỳ cột nào)
```

- [ ] **Step 6: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_don_hang.py apps/ai_mart/tests/test_contract.py apps/ai_mart/tests/ -v`
Expected: PASS toàn bộ (test_contract quét cả `tu_dien_ma` — bảng đó đã có COMMENT từ Task 4).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): don_hang mart table with full Vietnamese labels"
```

### Task 7: Bảng `ai.dong_don_hang` + test từ điển theo dữ liệu

**Files:**
- Create: `backend/apps/ai_mart/migrations/0006_dong_don_hang.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/dong_don_hang.py`
- Test: `backend/apps/ai_mart/tests/test_dong_don_hang.py`, `backend/apps/ai_mart/tests/test_dictionary.py`

**Interfaces:**
- Produces: `sql/dong_don_hang.py` expose `MART` (`name="ai.dong_don_hang"`, `key_column="don_id"` — XOÁ THEO ĐƠN rồi insert lại mọi dòng của đơn đó, xử lý được item bị xoá; `source_key="o.id"`; candidate dùng chung `_CANDIDATES` của don_hang — import từ `don_hang`).

- [ ] **Step 1: Viết test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_dong_don_hang.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.dong_don_hang import MART
from apps.orders.models import OrderItem
from apps.orders.tests.factories import OrderFactory


class DongDonHangTests(TestCase):
    def test_lines_per_order(self):
        order = OrderFactory(status=0)
        # OrderFactory có thể đã tạo item — đếm delta thay vì số tuyệt đối
        # (bài học seed migration 0113: test đếm tuyệt đối đỏ hàng loạt).
        existing = OrderItem.objects.filter(order=order).count()
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id])
            self.assertEqual(cur.fetchone()[0], existing)

    def test_deleted_item_removed_on_upsert(self):
        order = OrderFactory(status=0)
        loaders.full_rebuild(MART)
        OrderItem.objects.filter(order=order).delete()
        loaders.upsert_by_ids(MART, [order.id])
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id])
            self.assertEqual(cur.fetchone()[0], 0)
```

```python
# backend/apps/ai_mart/tests/test_dictionary.py
"""Mọi mã closed-set xuất hiện trong ai.don_hang phải có nhãn — cột _ma có giá trị
mà _text NULL nghĩa là từ điển thiếu mã: chính vấn đề spec sinh ra để giải."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.don_hang import MART

# (cột mã, cột nhãn) closed-set trong ai.don_hang — phuong_thuc_tt là text tự do, không kiểm.
PAIRS = [
    ("trang_thai_don_ma::text", "trang_thai_don_text"),
    ("trang_thai_sale_ma", "trang_thai_sale_text"),
    ("ly_do_khong_chot_ma", "ly_do_khong_chot_text"),
    ("trang_thai_giao_nghiep_vu_ma", "trang_thai_giao_nghiep_vu_text"),
    ("nhom_ly_do_bom_ma", "nhom_ly_do_bom_text"),
    ("phan_loai_khach_ma", "phan_loai_khach_text"),
    ("nghe_nghiep_ma", "nghe_nghiep_text"),
    ("muc_do_dau_ma", "muc_do_dau_text"),
    ("loai_don_ma", "loai_don_text"),
    ("loai_don_koc_ma", "loai_don_koc_text"),
    ("hinh_thuc_giao_ma", "hinh_thuc_giao_text"),
]


class DictionaryCoverageTests(TestCase):
    def test_no_untranslated_codes(self):
        # Dựng dữ liệu qua fixture chung rồi rebuild — dữ liệu test nào lọt vào
        # mart cũng phải dịch được hết.
        from apps.orders.tests.factories import OrderFactory

        OrderFactory(status=3)
        loaders.full_rebuild(MART)
        gaps = []
        with connection.cursor() as cur:
            for ma, text in PAIRS:
                cur.execute(
                    f"SELECT DISTINCT {ma} FROM ai.don_hang"
                    f" WHERE {ma} IS NOT NULL AND {text} IS NULL"
                )
                gaps.extend((ma, row[0]) for row in cur.fetchall())
        self.assertEqual(gaps, [], f"mã chưa có nhãn: {gaps}")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_dong_don_hang.py apps/ai_mart/tests/test_dictionary.py -v`

- [ ] **Step 3: Viết transform + migration**

```python
# backend/apps/ai_mart/infrastructure/sql/dong_don_hang.py
"""1 dòng/sản phẩm trong đơn. key_column = don_id (không phải dong_id): upsert
xoá TOÀN BỘ dòng của đơn rồi insert lại — item bị xoá khỏi đơn được dọn đúng."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.ai_mart.infrastructure.sql.don_hang import _CANDIDATES, _SCOPE


def _select(where: str) -> str:
    return f"""
SELECT
    oi.id AS dong_id,
    o.id AS don_id,
    o.order_code AS ma_don,
    (o.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_tao_ngay,
    NULLIF(btrim(coalesce(oi.sku, '')), '') AS sku,
    NULLIF(btrim(coalesce(oi.product_name, '')), '') AS ten_sp,
    pg.name AS nhom_sp_text,
    oi.quantity AS so_luong,
    oi.unit_price AS don_gia,
    oi.line_total AS thanh_tien
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
LEFT JOIN products p ON p.id = oi.product_id
LEFT JOIN product_groups pg ON pg.id = p.group_id
WHERE {_SCOPE} AND ({where})
"""


MART = MartTable(
    name="ai.dong_don_hang",
    key_column="don_id",
    source_key="o.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
```

Migration `0006_dong_don_hang.py` (`dependencies = [("ai_mart", "0005_don_hang")]`): bảng `ai.dong_don_hang` (`dong_id bigint PRIMARY KEY, don_id bigint, ma_don text, ngay_tao_ngay date, sku text, ten_sp text, nhom_sp_text text, so_luong int, don_gia numeric, thanh_tien numeric`), index `(don_id)`, `(sku)`, `(ngay_tao_ngay)`, COMMENT đủ 10 cột + bảng (mẫu: `COMMENT ON TABLE ai.dong_don_hang IS 'Mỗi dòng = 1 sản phẩm trong 1 đơn. JOIN ai.don_hang qua don_id để lọc theo trạng thái/GTC.'`).

⚠ `upsert_by_ids` DELETE theo `key_column` = `don_id` nhưng PK là `dong_id` — đúng chủ đích (xem docstring). `loaders.upsert_by_ids` không phụ thuộc PK nên không cần sửa.

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/ -v`
Expected: PASS toàn bộ app.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): dong_don_hang line-level mart and dictionary coverage test"
```
### Task 8: `etl_service` + Celery task + beat + routing

**Files:**
- Create: `backend/apps/ai_mart/application/etl_service.py`
- Create: `backend/apps/ai_mart/infrastructure/tasks.py`, `backend/apps/ai_mart/tasks.py` (re-export như analytics)
- Modify: `backend/config/settings_parts/celery.py` (routing ~dòng 111, beat schedule cuối dict)
- Test: `backend/apps/ai_mart/tests/test_etl_service.py`

**Interfaces:**
- Consumes: `MART` của don_hang, dong_don_hang (Sprint 2-3 tự thêm vào registry).
- Produces:
  - `HOT_MARTS: tuple[MartTable, ...]`, `NIGHTLY_MARTS: tuple[MartTable, ...]` — registry, sprint sau append.
  - `sync_hot() -> dict` — trả `{"tables": {ten: so_dong}, "cutoff": iso}`.
  - `rebuild_all() -> dict`.
  - `EtlFailed(RuntimeError)`.
  - Celery: `apps.ai_mart.tasks.sync_hot_marts` (soft 240 / hard 300), `apps.ai_mart.tasks.rebuild_all_marts` (soft 1500 / hard 1800), cả hai queue `slow`, single-flight.

- [ ] **Step 1: Viết test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_etl_service.py
"""Hợp đồng của điều phối: mốc lấy TRƯỚC khi đọc (không mất dòng ghi trong lúc
chạy), biên lùi 5 phút, thứ tự bảng, và LỖI PHẢI THOÁT RA (sự cố 2026-07-22)."""

from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.application import etl_service
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory


class EtlServiceTests(TestCase):
    def test_sync_hot_picks_up_queryset_update(self):
        order = OrderFactory(status=2)
        etl_service.rebuild_all()
        Order.objects.filter(id=order.id).update(status=3)
        result = etl_service.sync_hot()
        self.assertGreaterEqual(result["tables"]["ai.don_hang"], 1)

    def test_sync_hot_advances_watermark(self):
        from apps.ai_mart.infrastructure import loaders

        etl_service.rebuild_all()
        before = timezone.now()
        etl_service.sync_hot()
        moc = loaders.read_watermark("__hot__")
        self.assertIsNotNone(moc)
        self.assertGreaterEqual(moc, before)

    def test_failure_propagates_and_watermark_not_advanced(self):
        from apps.ai_mart.infrastructure import loaders

        etl_service.rebuild_all()
        etl_service.sync_hot()
        moc_truoc = loaders.read_watermark("__hot__")
        with mock.patch(
            "apps.ai_mart.infrastructure.loaders.upsert_by_ids",
            side_effect=RuntimeError("boom"),
        ):
            OrderFactory(status=0)
            with self.assertRaises(etl_service.EtlFailed):
                etl_service.sync_hot()
        self.assertEqual(loaders.read_watermark("__hot__"), moc_truoc)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_etl_service.py -v`

- [ ] **Step 3: Viết service**

```python
# backend/apps/ai_mart/application/etl_service.py
"""Điều phối ETL. Watermark là MỘT mốc chung '__hot__' cho cả nhóm nóng —
đơn giản hơn mốc/bảng và đúng: mọi bảng nóng đọc cùng cutoff, upsert cùng nhịp.

Thứ tự trong HOT_MARTS là bắt buộc: khach_hang (Sprint 2) đọc ai.don_hang."""

import logging
import uuid
from datetime import timedelta

from django.utils import timezone

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql import dong_don_hang, don_hang

logger = logging.getLogger(__name__)

HOT_KEY = "__hot__"
OVERLAP = timedelta(minutes=5)  # biên chống transaction commit lệch mốc (spec §5.2)

HOT_MARTS = (don_hang.MART, dong_don_hang.MART)
NIGHTLY_MARTS: tuple = ()  # Sprint 3 append


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
    loaders.write_watermark(HOT_KEY, cutoff_new)
    return {"tables": results, "cutoff": cutoff_new.isoformat()}


def rebuild_all() -> dict:
    run_id = uuid.uuid4()
    cutoff_new = timezone.now()
    results: dict[str, int] = {}
    for mart in HOT_MARTS + NIGHTLY_MARTS:
        started = timezone.now()
        try:
            rows = loaders.full_rebuild(mart)
        except Exception as exc:
            loaders.log_run(run_id, mart.name, started, timezone.now(), None, "failed", str(exc))
            raise EtlFailed(f"rebuild {mart.name} hỏng: {exc}") from exc
        loaders.log_run(run_id, mart.name, started, timezone.now(), rows, "ok")
        results[mart.name] = rows
    loaders.write_watermark(HOT_KEY, cutoff_new)
    return {"tables": results, "cutoff": cutoff_new.isoformat()}
```

```python
# backend/apps/ai_mart/infrastructure/tasks.py
"""Celery tasks — khuôn y hệt analytics/infrastructure/tasks.py: single_flight,
statement_timeout, LỖI THOÁT RA NGOÀI."""

import logging

from celery import shared_task

from apps.ai_mart.application.etl_service import rebuild_all, sync_hot
from apps.common.db_guards import single_flight, statement_timeout

logger = logging.getLogger(__name__)

_SYNC_LOCK = "lock:ai_mart_sync_hot"
_REBUILD_LOCK = "lock:ai_mart_rebuild_all"


@shared_task(name="apps.ai_mart.tasks.sync_hot_marts", soft_time_limit=240, time_limit=300)
def sync_hot_marts() -> dict:
    with single_flight(_SYNC_LOCK, 360) as acquired:
        if not acquired:
            logger.warning("sync_hot_marts already running — skip this cycle")
            return {"skipped": True}
        with statement_timeout(180):
            return sync_hot()


@shared_task(name="apps.ai_mart.tasks.rebuild_all_marts", soft_time_limit=1500, time_limit=1800)
def rebuild_all_marts() -> dict:
    with single_flight(_REBUILD_LOCK, 2400) as acquired:
        if not acquired:
            logger.warning("rebuild_all_marts already running — skip this cycle")
            return {"skipped": True}
        with statement_timeout(1200):
            return rebuild_all()
```

`backend/apps/ai_mart/tasks.py` (re-export, khuôn analytics):

```python
from apps.ai_mart.infrastructure.tasks import rebuild_all_marts, sync_hot_marts

__all__ = ["rebuild_all_marts", "sync_hot_marts"]
```

- [ ] **Step 4: Routing + beat trong `config/settings_parts/celery.py`**

Cạnh dòng ~111 (`"apps.analytics.tasks.refresh_dwh_marts": {"queue": CELERY_SLOW_QUEUE},`):

```python
    "apps.ai_mart.tasks.sync_hot_marts": {"queue": CELERY_SLOW_QUEUE},
    "apps.ai_mart.tasks.rebuild_all_marts": {"queue": CELERY_SLOW_QUEUE},
```

Cuối dict beat, sau entry `analytics-refresh-dwh-marts` (04:25):

```python
    # Nhịp 30' lệch pha (phút 7/37 — nguyên tắc lệch pha ở đầu file) nạp tăng dần
    # schema ai cho agent Hermes. Rebuild đêm 04:50: SAU refresh dwh 04:25 để
    # không giẫm I/O, và là cơ chế tự lành cho mọi thứ watermark bỏ sót.
    "ai-mart-sync-hot": {
        "task": "apps.ai_mart.tasks.sync_hot_marts",
        "schedule": crontab(minute="7,37"),
    },
    "ai-mart-rebuild-all": {
        "task": "apps.ai_mart.tasks.rebuild_all_marts",
        "schedule": crontab(hour=4, minute=50),
    },
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest apps/ai_mart/tests/test_etl_service.py -v && ruff check apps/ai_mart && lint-imports`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/ai_mart backend/config/settings_parts/celery.py
git commit -m "feat(ai_mart): etl orchestration with celery sync and nightly rebuild"
```

### Task 9: Management commands + role `hermes_ro`

**Files:**
- Create: `backend/apps/ai_mart/management/commands/ai_mart_sync.py`, `ai_mart_rebuild.py`, `ai_mart_verify.py`, `ai_mart_grant_role.py`
- Test: `backend/apps/ai_mart/tests/test_commands.py`

**Interfaces:**
- Produces: 4 lệnh manage.py. `ai_mart_verify` in danh sách mã chưa có nhãn (chạy trên prod TRƯỚC khi trỏ MCP — mã lạ thì HỎI user, không đoán, theo CLAUDE.md). `ai_mart_grant_role` tạo/cập nhật role từ env `HERMES_DB_PASSWORD`.

- [ ] **Step 1: Viết test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_commands.py
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.orders.tests.factories import OrderFactory


class CommandTests(TestCase):
    def test_rebuild_then_sync(self):
        OrderFactory(status=3)
        out = StringIO()
        call_command("ai_mart_rebuild", stdout=out)
        self.assertIn("ai.don_hang", out.getvalue())
        out = StringIO()
        call_command("ai_mart_sync", stdout=out)
        self.assertIn("cutoff", out.getvalue())

    def test_verify_reports_clean_on_test_data(self):
        OrderFactory(status=3)
        call_command("ai_mart_rebuild")
        out = StringIO()
        call_command("ai_mart_verify", stdout=out)
        self.assertIn("0 mã chưa có nhãn", out.getvalue())
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/ai_mart/tests/test_commands.py -v`

- [ ] **Step 3: Viết 4 command**

`ai_mart_rebuild.py` / `ai_mart_sync.py` — mỏng, gọi service, in kết quả:

```python
# backend/apps/ai_mart/management/commands/ai_mart_rebuild.py
from django.core.management.base import BaseCommand

from apps.ai_mart.application.etl_service import rebuild_all


class Command(BaseCommand):
    help = "Full rebuild toàn bộ bảng trong schema ai (initial load + tự lành)"

    def handle(self, *args, **options):
        result = rebuild_all()
        for table, rows in result["tables"].items():
            self.stdout.write(f"{table}: {rows} dòng")
        self.stdout.write(f"cutoff: {result['cutoff']}")
```

```python
# backend/apps/ai_mart/management/commands/ai_mart_sync.py
from django.core.management.base import BaseCommand

from apps.ai_mart.application.etl_service import sync_hot


class Command(BaseCommand):
    help = "Chạy một nhịp upsert tăng dần nhóm nóng"

    def handle(self, *args, **options):
        result = sync_hot()
        for table, rows in result["tables"].items():
            self.stdout.write(f"{table}: {rows} dòng")
        self.stdout.write(f"cutoff: {result['cutoff']}")
```

```python
# backend/apps/ai_mart/management/commands/ai_mart_verify.py
"""Liệt kê mã closed-set có trong dữ liệu nhưng thiếu nhãn — chạy trên PROD
trước khi trỏ MCP vào schema ai. Mã lạ (vd invoice_status = 4/5): HỎI user
nghĩa là gì rồi thêm vào domain/dictionary.py + data migration, KHÔNG đoán."""

from django.core.management.base import BaseCommand
from django.db import connection

from apps.ai_mart.tests.test_dictionary import PAIRS  # một nguồn cặp cột duy nhất


class Command(BaseCommand):
    help = "Soi mã chưa có nhãn trong ai.don_hang"

    def handle(self, *args, **options):
        gaps = []
        with connection.cursor() as cur:
            for ma, text in PAIRS:
                cur.execute(
                    f"SELECT DISTINCT {ma} FROM ai.don_hang"
                    f" WHERE {ma} IS NOT NULL AND {text} IS NULL"
                )
                gaps.extend((ma, row[0]) for row in cur.fetchall())
        for ma, val in gaps:
            self.stdout.write(self.style.WARNING(f"{ma}: {val}"))
        self.stdout.write(f"{len(gaps)} mã chưa có nhãn")
```

⚠ Import từ `tests/` vào command là mùi xấu — chuyển `PAIRS` sang `apps/ai_mart/domain/coverage.py` và cả test lẫn command import từ đó (sửa luôn Task 7 file test khi làm task này).

```python
# backend/apps/ai_mart/management/commands/ai_mart_grant_role.py
"""Tạo/cập nhật role hermes_ro (spec §6). Chạy TAY trên prod sau migrate:
  docker compose exec -e HERMES_DB_PASSWORD=... web python manage.py ai_mart_grant_role
Role là đối tượng cluster-wide — KHÔNG đặt trong migration (test DB không được
tạo role, và migrate chạy lại là ALTER ROLE vô nghĩa)."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Tạo/cập nhật role chỉ-đọc hermes_ro cho schema ai"

    def handle(self, *args, **options):
        password = os.environ.get("HERMES_DB_PASSWORD", "")
        if not password:
            raise CommandError("thiếu env HERMES_DB_PASSWORD")
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'hermes_ro'")
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute("CREATE ROLE hermes_ro LOGIN CONNECTION LIMIT 3")
            # đổi mật khẩu qua tham số — KHÔNG format chuỗi SQL bằng tay
            cur.execute("ALTER ROLE hermes_ro WITH LOGIN PASSWORD %s CONNECTION LIMIT 3", [password])
            cur.execute("ALTER ROLE hermes_ro SET statement_timeout = '30s'")
            cur.execute("ALTER ROLE hermes_ro SET default_transaction_read_only = on")
            cur.execute("GRANT USAGE ON SCHEMA ai TO hermes_ro")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ai TO hermes_ro")
            cur.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO hermes_ro"
            )
        self.stdout.write(self.style.SUCCESS("hermes_ro sẵn sàng — chỉ đọc schema ai"))
```

⚠ `ALTER ROLE ... PASSWORD %s`: psycopg2 không tham số hoá được utility statement — nếu lỗi syntax, dùng `cur.execute(sql.SQL("ALTER ROLE hermes_ro WITH LOGIN PASSWORD {} CONNECTION LIMIT 3").format(sql.Literal(password)))` với `from psycopg2 import sql`. Test không phủ command này (cần quyền superuser); kiểm chứng nằm ở runbook S4.

- [ ] **Step 4: Chạy test + toàn suite app, xác nhận PASS**

Run: `pytest apps/ai_mart -v && ruff check apps/ai_mart && lint-imports`

- [ ] **Step 5: Commit + kết sổ Sprint 1**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): management commands and hermes_ro role bootstrap"
```

Cập nhật spec §8: S1 → ✅ kèm 2 dòng note (đã làm: scaffold→don_hang/dong_don_hang→ETL→commands; chưa làm: các bảng S2/S3, deploy). Commit `docs(ai-mart): mark sprint 1 done`. Chạy suite hồi quy liên quan: `pytest apps/orders apps/analytics -x -q` (trigger mới đụng bảng orders — phải xanh như master). Push:

```bash
git push -o merge_request.create -o merge_request.title="feat(ai_mart): sprint 1 - schema, dictionary, don_hang" -u origin feat/ai-mart-s1
```

Gửi link MR cho user. Đóng băng branch.

---

# SPRINT 2 — Khách hàng, cuộc gọi, sản phẩm, siêu dữ liệu

Branch mới từ master mới nhất (sau khi S1 merge): `feat/ai-mart-s2`. Số migration bắt đầu từ 0007 — nếu master đã có thêm migration ai_mart thì đánh số tiếp theo thực tế.

### Task 10: Bảng `ai.khach_hang`

**Files:**
- Create: `backend/apps/ai_mart/migrations/0007_khach_hang.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/khach_hang.py`
- Modify: `backend/apps/ai_mart/application/etl_service.py` (append vào `HOT_MARTS`, SAU don_hang)
- Test: `backend/apps/ai_mart/tests/test_khach_hang.py`

**Interfaces:**
- Produces: `sql/khach_hang.py` expose `MART` (`name="ai.khach_hang"`, `key_column="khach_id"`, `source_key="c.id"`, `group="hot"`). Transform ĐỌC `ai.don_hang` (không đọc lại orders) — thứ tự trong HOT_MARTS bắt buộc sau don_hang, khuôn `customer_360` đọc `fct_order_enriched`.

- [ ] **Step 1: Viết test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_khach_hang.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.orders.tests.factories import OrderFactory


class KhachHangTests(TestCase):
    def test_aggregates_from_don_hang(self):
        o1 = OrderFactory(status=3)
        OrderFactory(status=3, customer=o1.customer)
        OrderFactory(status=6, customer=o1.customer)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT so_don_tat_ca, so_don_huy, gioi_tinh_text, sdt_che"
                " FROM ai.khach_hang WHERE khach_id = %s",
                [o1.customer_id],
            )
            so_don, so_huy, gioi_tinh, sdt = cur.fetchone()
        self.assertEqual(so_don, 3)
        self.assertEqual(so_huy, 1)
        self.assertIn(gioi_tinh, (None, "Nam", "Nữ"))
        if sdt is not None:
            self.assertIn("***", sdt)

    def test_customer_only_edit_flows_into_mart(self):
        from django.utils import timezone

        from apps.customers.models import Customer

        o = OrderFactory(status=3)
        etl_service.rebuild_all()
        cutoff = timezone.now()
        Customer.objects.filter(id=o.customer_id).update(full_name="Tên Mới")
        etl_service.sync_hot()
        with connection.cursor() as cur:
            cur.execute("SELECT ten_khach FROM ai.khach_hang WHERE khach_id = %s", [o.customer_id])
            self.assertEqual(cur.fetchone()[0], "Tên Mới")
```

- [ ] **Step 2: FAIL rồi viết transform**

Run: `pytest apps/ai_mart/tests/test_khach_hang.py -v` → FAIL.

```python
# backend/apps/ai_mart/infrastructure/sql/khach_hang.py
"""1 dòng/khách. Aggregate từ ai.don_hang — một tầng, không đọc lại orders
(khuôn customer_360 ← fct_order_enriched). Candidate: khách bị sửa TRỰC TIẾP
hoặc khách của đơn vừa đổi — cả hai đều qua dw_updated_at."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable

_CANDIDATES = """
SELECT c.id FROM customers c WHERE c.dw_updated_at > %(cutoff)s
UNION
SELECT o.customer_id FROM orders o
 WHERE o.dw_updated_at > %(cutoff)s AND o.customer_id IS NOT NULL
UNION
SELECT o2.customer_id FROM order_assessments oa
 JOIN orders o2 ON o2.id = oa.order_id
 WHERE oa.dw_updated_at > %(cutoff)s AND o2.customer_id IS NOT NULL
"""


def _select(where: str) -> str:
    return f"""
WITH agg AS (
    SELECT d.khach_id,
           count(*) FILTER (WHERE d.la_don_gtc)::int AS so_don_gtc,
           count(*)::int AS so_don_tat_ca,
           count(*) FILTER (WHERE d.trang_thai_don_ma = 6)::int AS so_don_huy,
           coalesce(sum(d.tong_sau_giam) FILTER (WHERE d.la_don_gtc), 0) AS tong_chi_tieu,
           avg(d.tong_sau_giam) FILTER (WHERE d.la_don_gtc) AS gia_tri_don_tb,
           min(d.ngay_tao) FILTER (WHERE d.la_don_gtc) AS lan_mua_dau,
           max(d.ngay_tao) FILTER (WHERE d.la_don_gtc) AS lan_mua_cuoi,
           array_remove(array_agg(DISTINCT d.kenh_ten), NULL) AS kenh_da_mua,
           array_remove(array_agg(DISTINCT d.tinh_thanh), NULL) AS tinh_da_mua,
           array_remove(array_agg(DISTINCT d.sku_chinh), NULL) AS sku_hay_mua,
           coalesce(sum(d.so_cuoc_goi), 0)::int AS so_cuoc_goi,
           avg(d.diem_chat_luong) AS diem_chat_luong_tb
    FROM ai.don_hang d
    WHERE d.khach_id IS NOT NULL
    GROUP BY d.khach_id
),
gaps AS (
    SELECT khach_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_days)
               AS khoang_cach_mua_lai_trung_vi
    FROM (
        SELECT d.khach_id,
               EXTRACT(EPOCH FROM (d.ngay_tao - lag(d.ngay_tao) OVER (
                   PARTITION BY d.khach_id ORDER BY d.ngay_tao))) / 86400.0 AS gap_days
        FROM ai.don_hang d WHERE d.la_don_gtc AND d.khach_id IS NOT NULL
    ) t WHERE gap_days IS NOT NULL GROUP BY khach_id
)
SELECT
    c.id AS khach_id,
    c.code AS ma_khach,
    NULLIF(btrim(coalesce(c.full_name, '')), '') AS ten_khach,
    ai.che_sdt(c.phone_normalized) AS sdt_che,
    ai.nhan('gioi_tinh', c.gender::text) AS gioi_tinh_text,
    css.segment_code AS phan_khuc_ma,
    cat.name AS phan_khuc_text,
    c.is_new_customer AS la_khach_moi,
    coalesce(a.so_don_gtc, 0) AS so_don_gtc,
    coalesce(a.so_don_tat_ca, 0) AS so_don_tat_ca,
    coalesce(a.so_don_huy, 0) AS so_don_huy,
    coalesce(a.tong_chi_tieu, 0) AS tong_chi_tieu,
    a.gia_tri_don_tb,
    a.lan_mua_dau,
    a.lan_mua_cuoi,
    CASE WHEN a.lan_mua_cuoi IS NULL THEN NULL
         ELSE floor(EXTRACT(EPOCH FROM (now() - a.lan_mua_cuoi)) / 86400)::int
    END AS so_ngay_tu_lan_mua_cuoi,
    g.khoang_cach_mua_lai_trung_vi,
    coalesce(a.kenh_da_mua, '{{}}') AS kenh_da_mua,
    coalesce(a.tinh_da_mua, '{{}}') AS tinh_da_mua,
    coalesce(a.sku_hay_mua, '{{}}') AS sku_hay_mua,
    coalesce(a.so_cuoc_goi, 0) AS so_cuoc_goi,
    a.diem_chat_luong_tb,
    now() AS dong_bo_luc
FROM customers c
LEFT JOIN customer_segment_snapshot css ON css.customer_id = c.id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN agg a ON a.khach_id = c.id
LEFT JOIN gaps g ON g.khach_id = c.id
WHERE c.deleted_at IS NULL AND ({where})
"""


MART = MartTable(
    name="ai.khach_hang",
    key_column="khach_id",
    source_key="c.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
```

⚠ Phân khúc: `phan_khuc_text` JOIN thẳng `customer_segment_categories.name` (bảng động, KHÔNG qua tu_dien_ma). 46% khách segment_code rỗng — NULL là đúng hiện trạng. `BUSINESS_TZ` import không dùng thì bỏ import (ruff F401 — nhớ bài học star re-export, nhưng đây là import thường, xoá an toàn).

Migration `0007_khach_hang.py`: DDL 22 cột đúng tên/kiểu như SELECT, PK `khach_id`, index `(ma_khach)`, `(lan_mua_cuoi)`, COMMENT đủ. Mẫu COMMENT bảng: `'Mỗi dòng = 1 khách hàng, số liệu mua tổng hợp từ ai.don_hang (chỉ đơn GTC trừ khi cột nói khác). SĐT đã che.'`. Append `khach_hang.MART` vào `HOT_MARTS` (sau `dong_don_hang.MART`).

- [ ] **Step 3: PASS + commit**

Run: `pytest apps/ai_mart -v` → PASS.

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): khach_hang customer mart aggregated from don_hang"
```
### Task 11: Bảng `ai.cuoc_goi`

**Files:**
- Create: `backend/apps/ai_mart/migrations/0008_cuoc_goi.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/cuoc_goi.py`
- Modify: `etl_service.py` (append `HOT_MARTS`)
- Test: `backend/apps/ai_mart/tests/test_cuoc_goi.py`

**Interfaces:**
- Produces: `MART` (`name="ai.cuoc_goi"`, `key_column="cuoc_goi_id"`, `source_key="r.id"`, `group="hot"`).

- [ ] **Step 1: Test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_cuoc_goi.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.cuoc_goi import MART
from apps.callcenter.models import CallRecord


class CuocGoiTests(TestCase):
    def _mk(self, **kw):
        base = dict(call_id="c-1", call_type="1", caller="0912345678", transcript="alo")
        base.update(kw)
        return CallRecord.objects.create(**base)

    def test_direction_and_masking(self):
        rec = self._mk()
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT loai_cuoc_goi_text, sdt_che, agent_ten, tom_tat_ai"
                " FROM ai.cuoc_goi WHERE cuoc_goi_id = %s", [rec.id],
            )
            direction, sdt, agent, tomtat = cur.fetchone()
        self.assertEqual(direction, "Gọi ra")
        self.assertEqual(sdt, "0912***678")
        self.assertIsNone(agent)   # agent_name rỗng → NULL, KHÔNG bịa "Không xác định"
        self.assertIsNone(tomtat)

    def test_inbound_and_summary(self):
        rec = self._mk(
            call_id="c-2", call_type="2",
            extract_payload={"clean": {"call_summary": "khách hỏi giá"}},
        )
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT loai_cuoc_goi_text, tom_tat_ai FROM ai.cuoc_goi WHERE cuoc_goi_id = %s",
                [rec.id],
            )
            direction, tomtat = cur.fetchone()
        self.assertEqual(direction, "Gọi vào")
        self.assertEqual(tomtat, "khách hỏi giá")
```

- [ ] **Step 2: FAIL → viết transform + migration**

```python
# backend/apps/ai_mart/infrastructure/sql/cuoc_goi.py
"""1 dòng/cuộc gọi. Quy tắc chiều gọi chép từ export_call_transcript.py:76:
'1' = Gọi ra, còn lại (không rỗng) = Gọi vào. Tóm tắt/nhu cầu AI lấy từ
extract_payload->'clean' (đường dẫn JSON đã đo trong spec dwh §0.4)."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable

_CANDIDATES = "SELECT r.id FROM callcenter_call_records r WHERE r.dw_updated_at > %(cutoff)s"


def _select(where: str) -> str:
    return f"""
WITH call_scores AS (
    SELECT DISTINCT ON (s.call_record_id) s.call_record_id, s.total_score
    FROM callcenter_call_quality_scores s
    ORDER BY s.call_record_id, s.created_at DESC, s.id DESC
)
SELECT
    r.id AS cuoc_goi_id,
    r.order_id AS don_id,
    o.order_code AS ma_don,
    o.customer_id AS khach_id,
    r.start_time AS bat_dau,
    (r.start_time AT TIME ZONE '{BUSINESS_TZ}')::date AS bat_dau_ngay,
    r.acd_time AS thoi_luong_giay,
    NULLIF(btrim(coalesce(r.call_type, '')), '') AS loai_cuoc_goi_ma,
    CASE
        WHEN NULLIF(btrim(coalesce(r.call_type, '')), '') IS NULL THEN NULL
        WHEN r.call_type = '1' THEN 'Gọi ra'
        ELSE 'Gọi vào'
    END AS loai_cuoc_goi_text,
    NULLIF(btrim(coalesce(r.agent_name, '')), '') AS agent_ten,
    ai.nhan('trang_thai_boc_bang', r.transcribe_status::text) AS trang_thai_boc_bang_text,
    NULLIF(btrim(coalesce(r.transcript, '')), '') AS transcript,
    NULLIF(btrim(coalesce(r.extract_payload #>> '{{clean,call_summary}}', '')), '')
        AS tom_tat_ai,
    coalesce(
        ARRAY(SELECT jsonb_array_elements_text(
            CASE jsonb_typeof(r.extract_payload #> '{{clean,need_type_codes}}')
                WHEN 'array' THEN r.extract_payload #> '{{clean,need_type_codes}}'
                ELSE '[]'::jsonb END)),
        '{{}}') AS nhu_cau_ai,
    cs.total_score AS diem_chat_luong,
    ai.che_sdt(coalesce(NULLIF(btrim(r.caller_normalized), ''), r.caller)) AS sdt_che,
    now() AS dong_bo_luc
FROM callcenter_call_records r
LEFT JOIN orders o ON o.id = r.order_id
LEFT JOIN call_scores cs ON cs.call_record_id = r.id
WHERE {where}
"""


MART = MartTable(
    name="ai.cuoc_goi",
    key_column="cuoc_goi_id",
    source_key="r.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
```

Migration `0008_cuoc_goi.py`: DDL 16 cột khớp SELECT, PK `cuoc_goi_id`, index `(don_id)`, `(bat_dau_ngay)`, COMMENT đủ — chú ý COMMENT cột `agent_ten`: `'Tên agent tổng đài — phần lớn RỖNG trên prod (hạ tầng Zenify chỉ trả 4 agent_id), NULL không có nghĩa là thiếu sót của đơn'`; COMMENT bảng nêu: mọi cuộc ở đây đều là cuộc bán hàng (tên hàng đợi HAPPYCALL của Zenify không lưu trong DB — spec §3.1). Append `cuoc_goi.MART` vào `HOT_MARTS`.

- [ ] **Step 3: PASS + commit**

Run: `pytest apps/ai_mart -v` → PASS.

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): cuoc_goi call mart with transcript and AI summary"
```

### Task 12: Bảng `ai.san_pham`

**Files:**
- Create: `backend/apps/ai_mart/migrations/0009_san_pham.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/san_pham.py`
- Modify: `etl_service.py` (append `NIGHTLY_MARTS`)
- Test: `backend/apps/ai_mart/tests/test_san_pham.py`

**Interfaces:**
- Produces: `MART` (`name="ai.san_pham"`, `key_column="san_pham_id"`, `source_key="p.id"`, `group="nightly"`, `candidate_ids_sql=None` — chỉ rebuild đêm).

- [ ] **Step 1: Test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_san_pham.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.san_pham import MART
from apps.catalog.models import Product, ProductComponent


class SanPhamTests(TestCase):
    def test_combo_flag_from_components(self):
        combo = Product.objects.create(name="Bộ ghế", sku="COMBO1", price=1000)
        child = Product.objects.create(name="Ghế", sku="GHE1", price=800)
        ProductComponent.objects.create(parent=combo, component=child, quantity=1)
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT la_combo, thanh_phan_combo FROM ai.san_pham WHERE sku = 'COMBO1'"
            )
            la_combo, thanh_phan = cur.fetchone()
            cur.execute("SELECT la_combo FROM ai.san_pham WHERE sku = 'GHE1'")
            child_flag = cur.fetchone()[0]
        self.assertTrue(la_combo)
        self.assertEqual(thanh_phan, ["GHE1"])
        self.assertFalse(child_flag)
```

- [ ] **Step 2: FAIL → transform + migration**

```python
# backend/apps/ai_mart/infrastructure/sql/san_pham.py
"""1 dòng/SP. la_combo suy từ product_components (catalog CỐ Ý không có cờ
is_combo — mart vật chất hoá luật đó, spec §3.2). ten_hien_thi từ
product_profiles.display_name — rỗng nghĩa là dùng ten_sp."""

from apps.ai_mart.infrastructure.sql.base import MartTable


def _select(where: str) -> str:
    return f"""
WITH combo AS (
    SELECT pc.parent_id,
           array_agg(child.sku ORDER BY child.sku) AS thanh_phan
    FROM product_components pc
    JOIN products child ON child.id = pc.component_id
    GROUP BY pc.parent_id
)
SELECT
    p.id AS san_pham_id,
    p.sku,
    p.name AS ten_sp,
    NULLIF(btrim(coalesce(pp.display_name, '')), '') AS ten_hien_thi,
    pg.name AS nhom_sp_text,
    p.price AS gia_ban,
    p.is_active AS dang_kinh_doanh,
    (cb.parent_id IS NOT NULL) AS la_combo,
    coalesce(cb.thanh_phan, '{{}}') AS thanh_phan_combo,
    now() AS dong_bo_luc
FROM products p
LEFT JOIN product_groups pg ON pg.id = p.group_id
LEFT JOIN product_profiles pp ON pp.product_id = p.id
LEFT JOIN combo cb ON cb.parent_id = p.id
WHERE p.deleted_at IS NULL AND ({where})
"""


MART = MartTable(
    name="ai.san_pham",
    key_column="san_pham_id",
    source_key="p.id",
    group="nightly",
    select_sql=_select,
    candidate_ids_sql=None,
)
```

⚠ Xác minh trước khi chạy: `product_profiles.product_id` là tên FK thật — `grep -n "product = \|OneToOne" apps/catalog/models.py` quanh class ProductProfile; nếu là OneToOne thì cột vẫn là `product_id`. Migration `0009_san_pham.py`: 10 cột, PK `san_pham_id`, unique index `(sku)`, COMMENT đủ. Append vào `NIGHTLY_MARTS`.

- [ ] **Step 3: PASS + commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): san_pham product mart with combo expansion"
```

### Task 13: View `ai.huong_dan_bang` + `ai.tinh_trang_du_lieu` + test freshness

**Files:**
- Create: `backend/apps/ai_mart/migrations/0010_metadata_views.py`
- Test: `backend/apps/ai_mart/tests/test_freshness.py`

**Interfaces:**
- Produces: 2 view trong schema `ai`. `huong_dan_bang` đọc pg_catalog; `tinh_trang_du_lieu` đọc `ai_meta.nhat_ky_chay` (view thường → chạy quyền owner, hermes_ro đọc được nội dung mà không cần quyền `ai_meta` — spec §6).

- [ ] **Step 1: Test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_freshness.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.orders.tests.factories import OrderFactory


class MetadataViewTests(TestCase):
    def test_huong_dan_bang_lists_don_hang_columns(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.huong_dan_bang"
                " WHERE ten_bang = 'don_hang' AND mo_ta_cot IS NOT NULL"
            )
            self.assertGreater(cur.fetchone()[0], 50)

    def test_tinh_trang_du_lieu_reflects_run(self):
        OrderFactory(status=3)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT trang_thai_text, so_dong FROM ai.tinh_trang_du_lieu"
                " WHERE ten_bang = 'ai.don_hang'"
            )
            trang_thai, so_dong = cur.fetchone()
        self.assertEqual(trang_thai, "Bình thường")
        self.assertGreaterEqual(so_dong, 1)
```

- [ ] **Step 2: FAIL → migration 0010**

```python
# backend/apps/ai_mart/migrations/0010_metadata_views.py
"""2 view siêu dữ liệu — thuốc giải cho 'ETL trễ âm thầm' (spec §3.3): agent đọc
được độ trễ nên nói 'số liệu tính đến 14:30' thay vì tưởng là số hiện tại."""

from django.db import migrations

DDL = """
CREATE OR REPLACE VIEW ai.huong_dan_bang AS
SELECT c.relname AS ten_bang,
       obj_description(c.oid) AS mo_ta_bang,
       a.attname AS ten_cot,
       format_type(a.atttypid, a.atttypmod) AS kieu_du_lieu,
       col_description(c.oid, a.attnum) AS mo_ta_cot
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'ai' AND c.relkind IN ('r', 'v')
ORDER BY c.relname, a.attnum;
COMMENT ON VIEW ai.huong_dan_bang IS
 'Tự tra cứu: mỗi dòng = 1 cột của 1 bảng trong schema ai kèm kiểu và mô tả tiếng Việt. Đọc bảng này TRƯỚC khi viết SQL.';

CREATE OR REPLACE VIEW ai.tinh_trang_du_lieu AS
SELECT DISTINCT ON (nk.ten_bang)
       nk.ten_bang,
       nk.ket_thuc AS chay_lan_cuoi,
       nk.so_dong,
       floor(EXTRACT(EPOCH FROM (now() - nk.ket_thuc)) / 60)::int AS do_tre_phut,
       CASE
           WHEN nk.trang_thai <> 'ok' THEN 'Lỗi ở lần chạy gần nhất'
           WHEN now() - nk.ket_thuc > interval '90 minutes' THEN 'Trễ — số liệu có thể cũ'
           ELSE 'Bình thường'
       END AS trang_thai_text
FROM ai_meta.nhat_ky_chay nk
WHERE nk.ket_thuc IS NOT NULL
ORDER BY nk.ten_bang, nk.ket_thuc DESC;
COMMENT ON VIEW ai.tinh_trang_du_lieu IS
 'Độ tươi của từng bảng. LUÔN kiểm tra trước khi trả lời câu hỏi về số liệu hôm nay — nếu do_tre_phut lớn, nói rõ mốc dữ liệu.';
"""

DROP = """
DROP VIEW IF EXISTS ai.tinh_trang_du_lieu;
DROP VIEW IF EXISTS ai.huong_dan_bang;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0009_san_pham")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
```

⚠ `test_contract` quét cả view (`relkind IN ('r','v')`) — 2 view này phải có COMMENT cột? `col_description` trên view có hoạt động nhưng migration không COMMENT từng cột view. Sửa test_contract: với view, chỉ bắt buộc COMMENT bảng (relkind='v' bỏ kiểm cột) — hoặc COMMENT đủ cột view trong migration này. CHỌN: COMMENT đủ cột (nhất quán, view chỉ có 5+5 cột) — thêm 10 câu `COMMENT ON COLUMN ai.huong_dan_bang.* / ai.tinh_trang_du_lieu.*` vào DDL.

- [ ] **Step 3: PASS + commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): self-describing metadata views for the agent"
```

### Task 14: Test masking toàn schema

**Files:**
- Test: `backend/apps/ai_mart/tests/test_masking.py`

**Interfaces:** introspect — tự phủ bảng mới về sau, không cần sửa khi S3 thêm bảng.

- [ ] **Step 1: Viết test (chạy được ngay — RED nếu có lỗi masking thật)**

```python
# backend/apps/ai_mart/tests/test_masking.py
"""Không một ô nào trong schema ai được chứa SĐT 10 số trần. Quét MỌI cột text
của MỌI bảng — bảng thêm ở sprint sau tự bị quét."""

import re

from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.orders.tests.factories import OrderFactory

PHONE = re.compile(r"(?<!\d)0\d{9}(?!\d)")
# transcript là ngoại lệ CÓ CHỦ ĐÍCH: nội dung hội thoại thô, Hermes cần nguyên văn
# (API transcript MR !1377 cũng trả nguyên văn sau token). Không quét cột này.
SKIP_COLUMNS = {("cuoc_goi", "transcript"), ("cuoc_goi", "tom_tat_ai")}


class MaskingTests(TestCase):
    def test_no_raw_phone_numbers_anywhere(self):
        OrderFactory(status=3)
        etl_service.rebuild_all()
        offenders = []
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname, a.attname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0"
                " WHERE n.nspname = 'ai' AND c.relkind = 'r'"
                " AND format_type(a.atttypid, a.atttypmod) = 'text'"
            )
            targets = [t for t in cur.fetchall() if t not in SKIP_COLUMNS]
            for table, col in targets:
                cur.execute(f'SELECT "{col}" FROM ai."{table}" WHERE "{col}" IS NOT NULL LIMIT 500')
                for (val,) in cur.fetchall():
                    if PHONE.search(val or ""):
                        offenders.append(f"ai.{table}.{col}: {val[:30]}")
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: Chạy — PASS (hoặc sửa transform nếu bắt được lỗi thật)**

Run: `pytest apps/ai_mart/tests/test_masking.py -v`

- [ ] **Step 3: Kết sổ Sprint 2**

Chạy `pytest apps/ai_mart -v && ruff check apps/ai_mart && lint-imports`. Cập nhật spec §8 (S2 ✅ + note). Commit, push MR mới:

```bash
git add backend/apps/ai_mart docs/superpowers/specs/2026-08-28-ai-mart-hermes-design.md
git commit -m "feat(ai_mart): sprint 2 - customers, calls, products, metadata views"
git push -o merge_request.create -o merge_request.title="feat(ai_mart): sprint 2 - customer/call/product marts" -u origin feat/ai-mart-s2
```

---

# SPRINT 3 — Kho + chi phí quảng cáo + equivalence

Branch mới `feat/ai-mart-s3` từ master mới nhất.

### Task 15: `ai.ton_kho` + `ai.lich_su_kho`

**Files:**
- Create: `backend/apps/ai_mart/migrations/0011_kho.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/ton_kho.py`, `backend/apps/ai_mart/infrastructure/sql/lich_su_kho.py`
- Modify: `etl_service.py` — append cả hai vào `NIGHTLY_MARTS`; riêng `lich_su_kho` thêm nạp tăng dần theo `moc_id` trong `sync_hot()` (bảng append-only, spec Q8)
- Test: `backend/apps/ai_mart/tests/test_kho.py`

**Interfaces:**
- Produces: `ton_kho.MART` (`key_column="ton_kho_id"`, `source_key="si.id"`, nightly). `lich_su_kho.MART` (`key_column="but_toan_id"`, `source_key="sm.id"`, nightly) + hàm riêng `lich_su_kho.sync_incremental() -> int` (INSERT dòng `sm.id > moc_id`, append-only nên KHÔNG cần DELETE) — `etl_service.sync_hot()` gọi sau vòng HOT_MARTS.

- [ ] **Step 1: Test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_kho.py
from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.ai_mart.infrastructure.sql import lich_su_kho
from apps.catalog.models import Product
from apps.inventory.models import StockItem, StockMovement
from apps.warehouses.models import Warehouse


class KhoTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="HN", name="Kho Hà Nội")
        self.p = Product.objects.create(name="Ghế", sku="GHE1", price=1)

    def test_ton_kho_translated(self):
        StockItem.objects.create(warehouse=self.wh, product=self.p, state="defect", quantity=3)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT kho_ten, trang_thai_ton_text, so_luong FROM ai.ton_kho WHERE sku = 'GHE1'"
            )
            self.assertEqual(cur.fetchone(), ("Kho Hà Nội", "Lỗi", 3))

    def test_lich_su_kho_incremental_append(self):
        etl_service.rebuild_all()
        StockMovement.objects.create(
            warehouse=self.wh, product=self.p, movement_type="receipt",
            qty_delta=5, balance_after=5, reference_type="document", reference_id=1,
        )
        n = lich_su_kho.sync_incremental()
        self.assertEqual(n, 1)
        with connection.cursor() as cur:
            cur.execute("SELECT loai_bien_dong_text FROM ai.lich_su_kho WHERE sku = 'GHE1'")
            self.assertEqual(cur.fetchone()[0], "Nhập kho")
```

⚠ Field thật của `StockMovement` (qty_delta/balance_after/reference_*, cột thời gian) xác minh bằng `sed -n 48,110p apps/inventory/models.py` trước khi viết — test trên dùng tên từ docstring model đã đọc; nếu lệch (vd cột thời gian là `created_at` hay `moved_at`), sửa test + transform theo model thật.

- [ ] **Step 2: FAIL → viết 2 transform + migration**

```python
# backend/apps/ai_mart/infrastructure/sql/ton_kho.py
from apps.ai_mart.infrastructure.sql.base import MartTable


def _select(where: str) -> str:
    return f"""
SELECT
    si.id AS ton_kho_id,
    w.name AS kho_ten,
    p.sku,
    p.name AS ten_sp,
    ai.nhan('trang_thai_ton', si.state) AS trang_thai_ton_text,
    si.quantity AS so_luong,
    si.updated_at AS cap_nhat_luc,
    now() AS dong_bo_luc
FROM stock_items si
JOIN warehouses w ON w.id = si.warehouse_id
JOIN products p ON p.id = si.product_id
WHERE {where}
"""


MART = MartTable(
    name="ai.ton_kho", key_column="ton_kho_id", source_key="si.id",
    group="nightly", select_sql=_select, candidate_ids_sql=None,
)
```

```python
# backend/apps/ai_mart/infrastructure/sql/lich_su_kho.py
"""Sổ cái kho — bất biến (append-only). Nạp tăng dần theo moc_id (spec Q8):
CHỈ INSERT id mới, không DELETE. Rebuild đêm vẫn TRUNCATE+INSERT như mọi bảng.

MỘT nguồn duy nhất stock_movements — bài học 'hai query dựng SL song song'
(MR !1345): mọi chế độ xem đều phải đi từ cùng một câu SELECT này."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.base import MartTable

_WM_KEY = "ai.lich_su_kho"


def _select(where: str) -> str:
    return f"""
SELECT
    sm.id AS but_toan_id,
    (sm.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay,
    w.name AS kho_ten,
    p.sku,
    p.name AS ten_sp,
    ai.nhan('loai_bien_dong_kho', sm.movement_type) AS loai_bien_dong_text,
    sm.qty_delta AS so_luong_thay_doi,
    sm.balance_after AS ton_sau,
    ai.nhan('loai_chung_tu_kho', sm.reference_type) AS loai_chung_tu_text,
    sm.reference_id AS chung_tu_id,
    o.order_code AS ma_don,
    now() AS dong_bo_luc
FROM stock_movements sm
JOIN warehouses w ON w.id = sm.warehouse_id
JOIN products p ON p.id = sm.product_id
LEFT JOIN orders o ON sm.reference_type = 'order' AND o.id = sm.reference_id
WHERE {where}
"""


MART = MartTable(
    name="ai.lich_su_kho", key_column="but_toan_id", source_key="sm.id",
    group="nightly", select_sql=_select, candidate_ids_sql=None,
)


def sync_incremental() -> int:
    """INSERT bút toán mới hơn moc_id. Append-only nên không cần DELETE."""
    moc_id = loaders.read_watermark_id(_WM_KEY)
    from django.db import connections, transaction

    conn = connections[loaders.TARGET_ALIAS]
    with transaction.atomic(using=loaders.TARGET_ALIAS):
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {MART.name} {_select('sm.id > %(moc_id)s')}",
                        {"moc_id": moc_id})
            inserted = cur.rowcount
            cur.execute("SELECT coalesce(max(id), %s) FROM stock_movements", [moc_id])
            loaders.write_watermark_id(_WM_KEY, cur.fetchone()[0])
    return inserted
```

⚠ Cột thời gian của movement: nếu model dùng tên khác `created_at`, sửa `_select`. ⚠ `ma_don` chỉ join khi `reference_type='order'` — mã quét khác shipping_code là chuyện đã biết (MR !1151), COMMENT cột phải nói rõ `'Chỉ có khi chứng từ là đơn hàng'`. Trong `etl_service.sync_hot()`: sau vòng `HOT_MARTS`, gọi `lich_su_kho.sync_incremental()` và ghi log_run riêng; trong `rebuild_all()`: sau khi rebuild `lich_su_kho`, cập nhật `moc_id = max(id)` để nhịp tăng dần kế tiếp không insert trùng (PK `but_toan_id` sẽ chặn nhưng đừng dựa vào lỗi).

Migration `0011_kho.py`: 2 bảng + index (`ton_kho`: unique `(ton_kho_id)`, index `(sku)`; `lich_su_kho`: PK `but_toan_id`, index `(ngay)`, `(sku)`, `(ma_don)`), COMMENT đủ.

- [ ] **Step 3: PASS + commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): warehouse stock and immutable ledger marts"
```
### Task 16: `ai.chi_phi_quang_cao`

**Files:**
- Create: `backend/apps/ai_mart/migrations/0012_chi_phi_quang_cao.py`
- Create: `backend/apps/ai_mart/infrastructure/sql/chi_phi_quang_cao.py`
- Modify: `etl_service.py` (append `NIGHTLY_MARTS`)
- Test: `backend/apps/ai_mart/tests/test_chi_phi_quang_cao.py`

**Interfaces:**
- Consumes: hằng số hệ số từ `apps.orders.reporting.shared`: `ADS_DAILY_COST_MULTIPLIER` (1.1), `ADS_DAILY_COST_MULTIPLIER_FROM_2026_08` (1.08), `FACEBOOK_ADS_MULTIPLIER_EFFECTIVE_DATE` (2026-08-01), `GOOGLE_ADS_DAILY_COST_MULTIPLIER` (1.08) — import, KHÔNG chép số.
- Produces: `MART` (`name="ai.chi_phi_quang_cao"`, `key_column="dong_id"`, nightly, không incremental).

**QUYẾT ĐỊNH NGHIỆP VỤ ghi vào COMMENT bảng (lệch màn hình CÓ CHỦ ĐÍCH):** màn hình báo cáo chọn hệ số FB **theo kỳ lọc** (`facebook_ads_cost_multiplier(date_from)` — kỳ bắc mốc ăn nguyên 1.1). Bảng mart grain NGÀY không có "kỳ", nên áp hệ số **theo ngày**: `ngay >= 2026-08-01 → 1.08, trước đó → 1.1`. Hệ quả: tổng mart ≠ màn hình cho kỳ bắc qua 01/08/2026. COMMENT bảng phải nói thẳng điều này để Hermes giải thích được chênh lệch — nếu không sẽ tái diễn kịch bản "hai màn không cộng khớp" (MR !1374).

**2 BẪY ĐÃ BIẾT phải né trong SQL:** (1) TKQC Facebook nhân dòng theo `FacebookApp` (MR !1256 — chi phí khống ×4): dedupe TKQC theo id NGOÀI trước khi join spend. Đo lại grain thật trước khi viết: `facebook_ad_daily_spends` treo theo `ad` (FacebookAd) chứ không theo account — kiểm tra FacebookAd có nhân bản theo app không bằng cách đọc `apps/facebook_ads/models.py` class FacebookAd + memory `ads-digital-product-breakdown` ("bẫy đo grain ad/account phải trải bản sao TKQC"). (2) Hai id kênh hard-code KOC=15, SALE_FB=24 không liên quan bảng này — không đụng.

- [ ] **Step 1: Test (fail trước)**

```python
# backend/apps/ai_mart/tests/test_chi_phi_quang_cao.py
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chi_phi_quang_cao import MART


def _rows(**filters):
    where = " AND ".join(f"{k} = %s" for k in filters)
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT nen_tang_text, ngay, chi_phi_goc, he_so_vat, chi_phi_da_ap_he_so"
            f" FROM ai.chi_phi_quang_cao WHERE {where}", list(filters.values()),
        )
        return cur.fetchall()


class ChiPhiQuangCaoTests(TestCase):
    def _mk_fb_spend(self, day, spend):
        # Dựng chuỗi FacebookApp→Account→Campaign→Ad→DailySpend tối thiểu.
        # Đọc apps/facebook_ads/models.py để điền đúng field bắt buộc — KHÔNG đoán;
        # nếu đã có factory trong apps/facebook_ads/tests thì dùng factory đó.
        from apps.facebook_ads.tests.factories import FacebookAdDailySpendFactory

        return FacebookAdDailySpendFactory(date=day, spend=spend)

    def test_fb_multiplier_per_day(self):
        self._mk_fb_spend(date(2026, 7, 31), Decimal("100"))
        self._mk_fb_spend(date(2026, 8, 1), Decimal("100"))
        loaders.full_rebuild(MART)
        rows = {r[1]: r for r in _rows(nen_tang_text="Facebook")}
        self.assertEqual(rows[date(2026, 7, 31)][3], Decimal("1.1"))
        self.assertEqual(rows[date(2026, 7, 31)][4], Decimal("110.0"))
        self.assertEqual(rows[date(2026, 8, 1)][3], Decimal("1.08"))
        self.assertEqual(rows[date(2026, 8, 1)][4], Decimal("108.0"))

    def test_google_flat_multiplier(self):
        from apps.google_ads.tests.factories import GoogleAdsCampaignDailySpendFactory

        GoogleAdsCampaignDailySpendFactory(date=date(2026, 8, 2), spend=Decimal("200"))
        loaders.full_rebuild(MART)
        rows = _rows(nen_tang_text="Google")
        self.assertEqual(rows[0][3], Decimal("1.08"))
        self.assertEqual(rows[0][4], Decimal("216.0"))
```

⚠ Nếu factory không tồn tại ở 1 trong 2 app ads: tìm cách các test hiện có của app đó dựng dữ liệu (`grep -rn "DailySpend" apps/facebook_ads/tests | head`) và dùng đúng khuôn đó.

- [ ] **Step 2: FAIL → transform + migration**

```python
# backend/apps/ai_mart/infrastructure/sql/chi_phi_quang_cao.py
"""Grain: (ngày × nền tảng × TKQC × chiến dịch). Hệ số VAT theo NGÀY — lệch màn
hình báo cáo (chọn theo kỳ) CÓ CHỦ ĐÍCH, xem COMMENT bảng + MR !1374.

FB: spend treo theo ad → gộp về campaign. TKQC nhân dòng theo FacebookApp
(MR !1256) — lấy min(account.id) mỗi external id làm đại diện trước khi join."""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.orders.reporting.shared import (
    ADS_DAILY_COST_MULTIPLIER,
    ADS_DAILY_COST_MULTIPLIER_FROM_2026_08,
    FACEBOOK_ADS_MULTIPLIER_EFFECTIVE_DATE,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
)

_FB_OLD = str(ADS_DAILY_COST_MULTIPLIER)
_FB_NEW = str(ADS_DAILY_COST_MULTIPLIER_FROM_2026_08)
_FB_DATE = FACEBOOK_ADS_MULTIPLIER_EFFECTIVE_DATE.isoformat()
_G = str(GOOGLE_ADS_DAILY_COST_MULTIPLIER)


def _select(where: str) -> str:
    return f"""
WITH fb AS (
    SELECT s.date AS ngay,
           'Facebook'::text AS nen_tang_text,
           acc.name AS tkqc_ten,
           camp.name AS chien_dich_ten,
           p.sku AS sku_gan,
           sum(s.spend) AS chi_phi_goc,
           sum(s.impressions) AS hien_thi,
           sum(s.clicks) AS luot_nhap
    FROM facebook_ad_daily_spends s
    JOIN facebook_ads fa ON fa.id = s.ad_id
    JOIN facebook_ad_campaigns camp ON camp.id = fa.campaign_id
    JOIN facebook_ad_accounts acc ON acc.id = camp.ad_account_id
    LEFT JOIN products p ON p.id = camp.product_id
    GROUP BY s.date, acc.name, camp.name, p.sku
),
gg AS (
    SELECT s.date AS ngay,
           'Google'::text AS nen_tang_text,
           acc.name AS tkqc_ten,
           camp.name AS chien_dich_ten,
           p.sku AS sku_gan,
           sum(s.spend) AS chi_phi_goc,
           sum(s.impressions) AS hien_thi,
           sum(s.clicks) AS luot_nhap
    FROM google_ads_campaign_daily_spends s
    JOIN google_ads_campaigns camp ON camp.id = s.campaign_id
    JOIN google_ads_accounts acc ON acc.id = camp.account_id
    LEFT JOIN products p ON p.id = camp.product_id
    GROUP BY s.date, acc.name, camp.name, p.sku
),
hop AS (SELECT * FROM fb UNION ALL SELECT * FROM gg)
SELECT
    row_number() OVER (ORDER BY ngay, nen_tang_text, tkqc_ten, chien_dich_ten) AS dong_id,
    ngay, nen_tang_text, tkqc_ten, chien_dich_ten, sku_gan,
    chi_phi_goc,
    CASE WHEN nen_tang_text = 'Google' THEN {_G}::numeric
         WHEN ngay >= DATE '{_FB_DATE}' THEN {_FB_NEW}::numeric
         ELSE {_FB_OLD}::numeric
    END AS he_so_vat,
    round(chi_phi_goc * CASE WHEN nen_tang_text = 'Google' THEN {_G}::numeric
         WHEN ngay >= DATE '{_FB_DATE}' THEN {_FB_NEW}::numeric
         ELSE {_FB_OLD}::numeric END, 0) AS chi_phi_da_ap_he_so,
    hien_thi, luot_nhap,
    now() AS dong_bo_luc
FROM hop
WHERE {where}
"""


MART = MartTable(
    name="ai.chi_phi_quang_cao", key_column="dong_id", source_key="dong_id",
    group="nightly", select_sql=_select, candidate_ids_sql=None,
)
```

⚠ 3 điểm PHẢI xác minh trước khi chạy (tên FK là phỏng theo convention, chưa đọc trực tiếp): `fa.campaign_id`, `camp.ad_account_id`, `camp.product_id` (FB) và `camp.account_id`, `camp.product_id` (Google) — đọc `apps/facebook_ads/models.py` + `apps/google_ads/models.py`, sửa theo tên thật. ⚠ Kiểm bẫy nhân dòng: chạy trên test data 1 app × 2 FacebookApp cùng external account id nếu model cho phép — nếu `facebook_ad_accounts` nhân bản theo app thì thêm CTE dedupe `SELECT DISTINCT ON (external_id) ...` trước khi join (memory `ads-report-duplicate-ad-accounts`). ⚠ `sku_gan` qua `camp.product_id` là kết quả MR !1353 (gán chiến dịch↔SP) — SKU rác TMS01 đã xử ở đó, mart chỉ đọc.

Migration `0012_chi_phi_quang_cao.py`: 12 cột, PK `dong_id`, index `(ngay)`, `(nen_tang_text, ngay)`, COMMENT bảng ghi rõ quyết định hệ số-theo-ngày (đoạn đầu Task này) + COMMENT đủ cột. Append `NIGHTLY_MARTS`.

- [ ] **Step 3: PASS + commit**

```bash
git add backend/apps/ai_mart
git commit -m "feat(ai_mart): ads cost mart with per-day VAT multiplier"
```

### Task 17: Test tương đương upsert ≡ rebuild + kết sổ Sprint 3

**Files:**
- Test: `backend/apps/ai_mart/tests/test_etl_equivalence.py`

- [ ] **Step 1: Viết test**

```python
# backend/apps/ai_mart/tests/test_etl_equivalence.py
"""Bảo hiểm lõi của kiến trúc ETL: trạng thái mart sau (rebuild → sửa → sync)
phải Y HỆT trạng thái sau (sửa → rebuild). Lệch = watermark/candidate sót đường ghi."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.orders.models import Order, OrderItem
from apps.orders.tests.factories import OrderFactory


def _snapshot(table, key):
    with connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY {key}")
        cols = [c[0] for c in cur.description]
        skip = {"dong_bo_luc"}  # timestamp lượt chạy — khác nhau là đương nhiên
        return [
            {c: v for c, v in zip(cols, row) if c not in skip} for row in cur.fetchall()
        ]


class EquivalenceTests(TestCase):
    def test_sync_equals_rebuild(self):
        o1 = OrderFactory(status=2)
        OrderFactory(status=3)
        etl_service.rebuild_all()
        # 3 kiểu đột biến qua 3 đường ghi khác nhau
        Order.objects.filter(id=o1.id).update(status=3)          # queryset.update
        OrderItem.objects.filter(order=o1).delete()               # xoá item
        OrderFactory(status=0)                                    # đơn mới
        etl_service.sync_hot()
        after_sync = {
            t: _snapshot(t, k)
            for t, k in [("ai.don_hang", "don_id"), ("ai.dong_don_hang", "dong_id"),
                         ("ai.khach_hang", "khach_id")]
        }
        etl_service.rebuild_all()
        after_rebuild = {
            t: _snapshot(t, k)
            for t, k in [("ai.don_hang", "don_id"), ("ai.dong_don_hang", "dong_id"),
                         ("ai.khach_hang", "khach_id")]
        }
        self.assertEqual(after_sync, after_rebuild)
```

- [ ] **Step 2: Chạy — PASS (nếu FAIL: đây là bug thật của candidate SQL, sửa ở sql/*.py chứ không nới test)**

Run: `pytest apps/ai_mart/tests/test_etl_equivalence.py -v`

- [ ] **Step 3: Kết sổ Sprint 3**

`pytest apps/ai_mart -v && ruff check apps/ai_mart && lint-imports` → cập nhật spec §8 (S3 ✅ + note) → commit + push:

```bash
git add backend/apps/ai_mart docs/superpowers/specs/2026-08-28-ai-mart-hermes-design.md
git commit -m "feat(ai_mart): sprint 3 - inventory and ads marts with equivalence test"
git push -o merge_request.create -o merge_request.title="feat(ai_mart): sprint 3 - inventory/ads marts" -u origin feat/ai-mart-s3
```

---

# SPRINT 4 — Deploy, giám sát, chuyển MCP

Branch mới `feat/ai-mart-s4` từ master mới nhất. Sprint này phần lớn là cấu hình + tài liệu; phần chạy trên prod là RUNBOOK — người vận hành (user) thực hiện, plan chỉ chuẩn bị đủ đồ.

### Task 18: Env compose + alert Prometheus

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.local.yml`, `docker-compose.staging.yml`, `docker-compose.prod.yml` — thêm `HERMES_DB_PASSWORD` vào khối env của backend (cùng chỗ khối `MINIO_*`):

```yaml
  # Role chỉ-đọc schema ai cho agent Hermes — chỉ lệnh ai_mart_grant_role dùng.
  HERMES_DB_PASSWORD: ${HERMES_DB_PASSWORD:-}
```

- Modify: `monitoring/alerts.yml` — thêm group `ai_mart` (đặt cạnh group `celery`, ~dòng 224). TRƯỚC KHI VIẾT: đọc `sed -n 224,300p monitoring/alerts.yml` để chép đúng tên metric + label mà celery-exporter của repo này expose; rule mẫu (điều chỉnh tên metric theo file thật):

```yaml
  - name: ai_mart
    rules:
      # ETL trễ âm thầm là chế độ hỏng nguy hiểm nhất (spec §9) — task fail thì
      # celery-exporter đếm được; task KHÔNG CHẠY (beat chết, queue kẹt) thì chỉ
      # có luật "vắng thành công quá lâu" mới bắt được.
      - alert: AiMartSyncFailed
        expr: increase(celery_task_failed_total{name="apps.ai_mart.tasks.sync_hot_marts"}[1h]) > 0
        for: 0m
        labels: {severity: warning}
        annotations:
          summary: "ETL schema ai lỗi"
          description: "sync_hot_marts fail trong 1h qua — Hermes đang trả số cũ. Xem ai_meta.nhat_ky_chay."
      - alert: AiMartSyncSilent
        expr: increase(celery_task_succeeded_total{name="apps.ai_mart.tasks.sync_hot_marts"}[90m]) == 0
        for: 0m
        labels: {severity: warning}
        annotations:
          summary: "ETL schema ai không chạy >90 phút"
          description: "Không có lượt sync_hot_marts thành công nào trong 90 phút (nhịp chuẩn 30'). Kiểm beat + queue slow."
```

- [ ] **Step 1: Sửa 4 file compose + alerts.yml theo trên** (đọc file thật trước, chép đúng anchor/format hiện có)
- [ ] **Step 2: Validate**

Run: `docker compose -f docker-compose.yml config -q && docker compose -f docker-compose.prod.yml config -q` (và 2 file còn lại)
Expected: exit 0, không lỗi YAML. Với alerts: `docker run --rm -v "$PWD/monitoring:/m" --entrypoint promtool prom/prometheus:v3.13.0 check rules /m/alerts.yml` — hoặc nếu máy không kéo được image, ghi chú lại để kiểm trên prod trước khi reload.

⚠ Bẫy bind-mount inode (memory `prometheus-alerts-bind-mount-inode-trap`): sau khi pull trên prod, PHẢI `docker compose up -d --force-recreate --no-deps prometheus` — reload/restart thường không ăn rule mới.

- [ ] **Step 3: Commit**

```bash
git add docker-compose*.yml monitoring/alerts.yml
git commit -m "chore(ai_mart): hermes env passthrough and etl freshness alerts"
```

### Task 19: Runbook deploy

**Files:**
- Create: `docs/ai-mart-deploy-runbook.md`

- [ ] **Step 1: Viết runbook** — nội dung đầy đủ:

```markdown
# Runbook deploy AI Mart (schema `ai`) + chuyển MCP Hermes

Thứ tự BẮT BUỘC: migrate trước, roll code sau (spec §9). Làm giờ thấp điểm.

## 0. Chuẩn bị
- [ ] Đo baseline write-path TRƯỚC deploy (so sánh ở bước 5):
      SELECT mean_exec_time, calls FROM pg_stat_statements
       WHERE query ILIKE 'UPDATE "orders"%' ORDER BY calls DESC LIMIT 5;
      Ghi số ra giấy. (pg_stat_statements TÍCH LUỸ — chỉ so mean trước/sau cùng
      khoảng giờ, bài học đo MR !1205.)
- [ ] `git pull` trên prod. KHÔNG `docker compose pull` cả cụm (bẫy kéo image core
      — memory prod-compose-recreate-gotchas).

## 1. Migrate (code cũ vẫn chạy — cột/trigger mới vô hại với code cũ)
- [ ] docker compose -f docker-compose.prod.yml exec web python manage.py migrate ai_mart
- [ ] Kiểm: SELECT count(*) FROM ai.tu_dien_ma;  -- phải > 100

## 2. Roll code + beat
- [ ] Build/restart web, worker-slow, beat — TỪNG service một, không recreate
      hàng loạt (bẫy trùng tên container). Image worker phải cùng digest với web
      (sự cố image lệch per-service 2026-08-03).

## 3. Initial load + kiểm chất lượng
- [ ] docker compose ... exec web python manage.py ai_mart_rebuild
      (ghi lại thời lượng — nếu > 10 phút là chạm ngưỡng Bậc 1 của spec §9 sớm)
- [ ] docker compose ... exec web python manage.py ai_mart_verify
      → Nếu có mã chưa nhãn (vd invoice_status lạ): HỎI user nghĩa của mã,
      thêm vào domain/dictionary.py + data migration ở MR sau. KHÔNG đoán.
- [ ] SELECT * FROM ai.tinh_trang_du_lieu;  -- mọi bảng "Bình thường"

## 4. Role + MCP
- [ ] docker compose ... exec -e HERMES_DB_PASSWORD='<sinh mật khẩu mạnh>' web \
        python manage.py ai_mart_grant_role
- [ ] Thử bằng psql: đăng nhập hermes_ro, xác nhận:
      SELECT count(*) FROM ai.don_hang;                  -- OK
      SELECT count(*) FROM orders;                        -- PHẢI permission denied
      SELECT count(*) FROM ai_meta.nhat_ky_chay;          -- PHẢI permission denied
      INSERT INTO ai.tu_dien_ma VALUES ('x','x','x');     -- PHẢI read-only
- [ ] Đổi connection string của MCP `prod-db` phía Hermes sang user hermes_ro
      (database desi, search_path=ai). Nhắc user cập nhật cấu hình MCP.

## 5. Hậu kiểm
- [ ] Đo lại write-path (câu ở bước 0) sau ≥1 giờ chạy — mean tăng >5% thì báo.
- [ ] Xem 2-3 nhịp sync: SELECT * FROM ai_meta.nhat_ky_chay ORDER BY id DESC LIMIT 12;
- [ ] Prometheus: force-recreate --no-deps prometheus để ăn rule mới (bẫy inode).
- [ ] Đối chiếu số: SUM(tong_sau_giam) WHERE la_don_gtc AND ngay_tao_ngay trong
      tháng hiện tại vs Báo cáo tổng trên CRM — lệch phải giải thích được
      (mốc thời gian refresh, đơn mirror).

## Rollback
- Trigger/cột dw_updated_at vô hại với code cũ — rollback code là đủ, KHÔNG cần
  migrate ngược. Muốn gỡ hẳn: python manage.py migrate ai_mart zero (schema ai
  bị DROP CASCADE — Hermes mất dữ liệu đọc, gỡ MCP trước).
```

- [ ] **Step 2: Commit + kết sổ Sprint 4**

```bash
git add docs/ai-mart-deploy-runbook.md
git commit -m "docs(ai_mart): production deploy runbook"
```

Cập nhật spec §8 (S4 ✅ phần chuẩn bị; các mục chạy-trên-prod đánh dấu ⏳ chờ user thực hiện theo runbook). Push MR:

```bash
git push -o merge_request.create -o merge_request.title="feat(ai_mart): sprint 4 - deploy config, alerts, runbook" -u origin feat/ai-mart-s4
```

Gửi link MR + nhắc user: (1) deploy theo runbook, (2) đặt env `HERMES_DB_PASSWORD` trước khi chạy grant_role (bài học MR !1377 — env prod TRƯỚC roll code), (3) sau deploy Hermes đọc schema `ai` — câu trả lời về trạng thái sẽ ra chữ, không ra số.

---

## Self-review đã chạy (giữ lại làm ghi chú cho executor)

1. **Spec coverage:** §0-§2 → Task 1-5; §3.1 → Task 6,7,10,11; §3.2 → Task 12,15,16; §3.3 → Task 4,13; §4 → nằm trong transform + test_contract/test_masking/test_dictionary; §5 → Task 2,5,8,15; §6 → Task 9,18,19; §7 → Task 6,7,8,13,14,17; §8 → cấu trúc 4 sprint của plan; §9 → Task 19 (đo write-path, runbook) + Task 18 (alert). Không còn mục spec nào thiếu task.
2. **Placeholder:** các chỗ "viết đủ COMMENT" là yêu cầu liệt kê đầy đủ có mẫu kèm theo + test_contract chặn bằng máy — không phải placeholder bỏ ngỏ. Các ⚠ "xác minh tên cột trước khi chạy" là bước verify có lệnh cụ thể, executor phải chạy lệnh đó.
3. **Type consistency:** `MartTable` có field `source_key` từ Task 5 (đã sửa mâu thuẫn frozen-dataclass); `key_column` của dong_don_hang = `don_id` có docstring giải thích; `PAIRS` chuyển về `domain/coverage.py` ở Task 9 (test Task 7 sửa import cùng lúc); watermark key `__hot__` dùng thống nhất Task 5/8; `read_watermark_id`/`write_watermark_id` khai ở Task 5, dùng ở Task 15.
