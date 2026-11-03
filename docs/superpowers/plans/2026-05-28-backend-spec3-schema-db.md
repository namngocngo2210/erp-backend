# Backend Spec 3 — Schema & DB Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đo trước, thêm index/đổi schema có bằng chứng và reversible. Các thay đổi đụng ngữ nghĩa (unique constraint, on_delete) chỉ thực hiện sau khi anh xác nhận business rule + audit dữ liệu.

**Architecture:** Mỗi thay đổi là một migration Django reversible. Index dùng `AddIndexConcurrently` cho bảng lớn để không khóa ghi. Trước mỗi index: `EXPLAIN (ANALYZE)` query thật + ghi vào file `evidence`. Sau mỗi migration: chạy `migrate` thuận + nghịch trên DB tạm.

**Tech Stack:** Django migrations, PostgreSQL, `psql` cho EXPLAIN, `pg_stat_statements` (nếu bật).

**Spec gốc:** [docs/superpowers/specs/2026-05-28-backend-spec3-schema-db-design.md](../specs/2026-05-28-backend-spec3-schema-db-design.md)

**Phụ thuộc:** Spec 1 + Spec 2 đã merge.

---

## Nguyên tắc

- **Đo trước, sửa sau.** Không thêm index "phòng hờ".
- Migration phải `migrate` thuận + nghịch chạy sạch.
- Mục B (unique, on_delete) gated bằng cổng xác nhận business — nếu chưa được phê duyệt, task chỉ lập "phiếu xác nhận" và DỪNG, không tự đổi.
- Mỗi migration một commit.

---

## Phase A — Đo & thu thập bằng chứng

### Task A1: Thiết lập file evidence

**Files:**
- Create: `docs/superpowers/notes/2026-05-28-spec3-query-evidence.md`

- [ ] **Step 1: Tạo template**

```markdown
# Spec 3 — Query Evidence

Mỗi mục dưới đây ghi:
1. Query (Django ORM hoặc SQL).
2. `EXPLAIN (ANALYZE, BUFFERS)` trước khi thêm index.
3. Quyết định: thêm/không thêm index. Nếu thêm, dán plan.
4. `EXPLAIN (ANALYZE, BUFFERS)` sau khi thêm index.

## A1.1 OrderAssessment filter combo
(query thực + plan trước/sau)

## A1.2 SaleReminder pending due
...

## A1.3 OrderKiotVietJob unfinished
...
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/notes/2026-05-28-spec3-query-evidence.md
git commit -m "docs(spec3): scaffold evidence notes for index decisions"
```

---

### Task A2: Thu thập query thật

- [ ] **Step 1: Tìm query thật trong code dùng các tổ hợp filter cần xét**

```bash
grep -rn "sales_status_code\|customer_classification_code" backend/apps --include="*.py" | grep -v migrations | grep -v tests
grep -rn "SaleReminder.objects.filter" backend/apps --include="*.py" | grep -v migrations | grep -v tests
grep -rn "OrderKiotVietJob.objects" backend/apps --include="*.py" | grep -v migrations | grep -v tests
```

- [ ] **Step 2: Dán query thật vào evidence note**

Mỗi mục: chép đoạn ORM thực + sinh SQL tương đương (dùng `qs.query` trong Django shell).

- [ ] **Step 3: Chạy EXPLAIN trên DB local (clone/sample)**

```bash
cd backend && python manage.py shell -c "from apps.orders.models import SaleReminder; print(SaleReminder.objects.filter(status='pending', due_at__lte='2026-12-31').query)"
# Sau đó psql -d <db> -c "EXPLAIN (ANALYZE, BUFFERS) <sql>;"
```

- [ ] **Step 4: Quyết định cho từng mục: ADD INDEX / KEEP / SKIP**

Ghi rõ trong evidence: nếu cost thấp + ít row, SKIP.

- [ ] **Step 5: Commit evidence**

```bash
git add docs/superpowers/notes/2026-05-28-spec3-query-evidence.md
git commit -m "docs(spec3): collect EXPLAIN evidence for candidate indexes"
```

> ⚠️ Sau task này, các task A3+ chỉ làm với các mục được đánh dấu ADD INDEX trong evidence. Mục SKIP bỏ qua.

---

### Task A3: Thêm index theo từng quyết định (lặp pattern)

**Mẫu cho mỗi index (lặp):**

**Files:**
- Modify: `backend/apps/orders/models.py` (hoặc app tương ứng) — thêm `Index` vào `Meta.indexes`
- Generated: `backend/apps/orders/migrations/XXXX_add_<name>.py`

- [ ] **Step 1: Thêm `models.Index(...)` vào `Meta.indexes` (hoặc partial index có `condition=Q(...)`)**

Ví dụ với `SaleReminder`:
```python
models.Index(
    fields=["due_at"],
    condition=Q(status="pending"),
    name="sale_reminder_pending_due_idx",
),
```

- [ ] **Step 2: Sinh migration**

```bash
cd backend && python manage.py makemigrations orders
```

- [ ] **Step 3: Sửa migration thành `CONCURRENTLY` (nếu bảng lớn)**

Trong file migration mới, đổi `AddIndex` → `AddIndexConcurrently` và set:
```python
class Migration(migrations.Migration):
    atomic = False
    ...
    operations = [
        AddIndexConcurrently(...),
    ]
```
*(Cần `from django.contrib.postgres.operations import AddIndexConcurrently`.)*

> Bảng nhỏ (< 10k row) thì `AddIndex` thường nhanh — không cần concurrent. Chọn theo size thật.

- [ ] **Step 4: Chạy migrate thuận + nghịch trên DB tạm**

```bash
cd backend && python manage.py migrate orders
cd backend && python manage.py migrate orders <previous_number>
cd backend && python manage.py migrate orders
```

- [ ] **Step 5: EXPLAIN sau khi index có**

Dán plan sau vào evidence.

- [ ] **Step 6: Commit**

```bash
git commit -am "perf(orders): add partial index sale_reminder_pending_due_idx"
```

(Lặp cho từng index được duyệt.)

---

## Phase B — Job dispatch: TextField → CharField

### Task B1: Migration AlterField

**Files:**
- Modify: `backend/apps/dispatch/models.py` (fields `queue`, `task_type`)
- Generated: `backend/apps/dispatch/migrations/XXXX_alter_job_queue_task_type.py`

- [ ] **Step 1: Đo độ dài hiện tại của dữ liệu**

```bash
cd backend && python manage.py shell -c "from apps.dispatch.models import Job; from django.db.models import Max, Length; print(Job.objects.aggregate(q=Max(Length('queue')), t=Max(Length('task_type'))))"
```
Ghi vào evidence.

- [ ] **Step 2: Chọn `max_length` an toàn (kết quả * 2, tối thiểu 64/128)**

- [ ] **Step 3: Sửa model**

```python
queue = models.CharField(max_length=64)
task_type = models.CharField(max_length=128)
```

- [ ] **Step 4: Sinh migration & inspect SQL**

```bash
cd backend && python manage.py makemigrations dispatch
cd backend && python manage.py sqlmigrate dispatch XXXX | head -30
```
PostgreSQL: chuyển `text` → `varchar(N)` thường rewrite bảng. **Nếu bảng lớn**, dùng raw SQL `ALTER TABLE ... ALTER COLUMN ... TYPE varchar(N) USING ...` không rewrite (nếu N đủ lớn, PG có thể skip), hoặc giữ là `TextField` và chỉ thêm index — đánh giá lại.

- [ ] **Step 5: Test thuận/nghịch trên DB tạm**

- [ ] **Step 6: Commit**

```bash
git commit -am "refactor(dispatch): use CharField for queue/task_type to enable indexable lookups"
```

---

## Phase C — Mục gated (CẦN XÁC NHẬN BUSINESS)

> ⚠️ Các task dưới đây KHÔNG được thực hiện cho tới khi anh trả lời các câu hỏi trong "phiếu xác nhận".

### Task C1: Phiếu xác nhận business rule

**Files:**
- Create: `docs/superpowers/notes/2026-05-28-spec3-business-confirmation.md`

- [ ] **Step 1: Tạo phiếu**

```markdown
# Spec 3 — Phiếu xác nhận business rule

Vui lòng trả lời để mở khóa các task ngữ nghĩa.

## Q1. `Order.order_code`
- (a) Unique toàn cục (mọi channel)?
- (b) Unique trong từng channel?
- (c) Có thể trùng hợp lệ (skip constraint)?

## Q2. `Order.external_order_id`
- (a) Phải unique theo (channel, external_order_id) cho bản ghi active (chưa deleted)?
- (b) Có thể trùng hợp lệ (vd platform retry sinh cùng id)?

## Q3. Hard-delete Order
- (a) Hệ thống có lúc nào hard-delete Order trên production không?
- (b) Nếu KHÔNG → giữ CASCADE cho assessment/needs ok, hoãn thay đổi on_delete.
- (c) Nếu CÓ → đổi sang SET_NULL hoặc soft-delete cascading.

## Q4. JSONField tách cột (`CustomerResaleFollowUp.contact_logs`, ...)
- Có nhu cầu report/aggregate field nào trong JSON không? Liệt kê.
```

- [ ] **Step 2: Commit phiếu, DỪNG ở đây**

```bash
git add docs/superpowers/notes/2026-05-28-spec3-business-confirmation.md
git commit -m "docs(spec3): business confirmation form (gates C2/C3/C4)"
```

> Sau khi anh điền phiếu, executor tiếp tục các task tương ứng dưới đây. Nếu phiếu không được điền, **dừng** ở Phase B.

---

### Task C2: Unique constraint cho `Order.external_order_id` (gated bởi Q2)

**Chỉ thực hiện nếu Q2 = (a).**

- [ ] **Step 1: Audit dữ liệu trùng hiện có**

```bash
cd backend && python manage.py shell -c "
from django.db.models import Count
from apps.orders.models import Order
dup = (Order.objects.filter(deleted_at__isnull=True)
    .exclude(external_order_id='')
    .values('channel_id','external_order_id')
    .annotate(c=Count('id')).filter(c__gt=1))
print('duplicates:', list(dup[:20]), 'total groups:', dup.count())
"
```

- [ ] **Step 2: Nếu có trùng → tạo script dedup hoặc data migration làm sạch TRƯỚC khi thêm constraint**

(Phạm vi script tùy số lượng — không bịa, cần xem dữ liệu thật.)

- [ ] **Step 3: Thêm constraint vào `Order.Meta`**

```python
models.UniqueConstraint(
    fields=["channel", "external_order_id"],
    condition=Q(external_order_id__gt="", deleted_at__isnull=True),
    name="uniq_order_ch_ext_order_id_active",
)
```

- [ ] **Step 4: Sinh migration + test thuận/nghịch**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(orders): unique(channel, external_order_id) for active orders"
```

---

### Task C3: Unique constraint `Order.order_code` (gated bởi Q1)

Tương tự C2 — chỉ làm theo phương án Q1 đã chọn.

---

### Task C4: Đổi on_delete CASCADE (gated bởi Q3)

**Chỉ thực hiện nếu Q3 = (c).**

- [ ] **Step 1: Đổi `OrderAssessment.order.on_delete = SET_NULL`** (nếu phương án SET_NULL được chọn)

```python
order = models.OneToOneField(
    Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="assessment"
)
```

- [ ] **Step 2: Cập nhật code đang giả định `order.assessment` luôn tồn tại** (nếu có)

Grep: `grep -rn "\.assessment\." backend/apps --include="*.py" | grep -v migrations | grep -v tests`

- [ ] **Step 3: Migration + test thuận/nghịch + test hành vi delete**

- [ ] **Step 4: Commit**

```bash
git commit -am "refactor(orders): keep OrderAssessment when Order is hard-deleted"
```

---

### Task C5: Tách JSONField → cột query-friendly (gated bởi Q4)

Cho mỗi field anh chỉ ra:
- Thêm cột riêng (vd `last_contact_at`, `contact_count`) với index nếu cần.
- Data migration: tính giá trị từ JSON hiện có sang cột mới (forwards + reverse).
- Cập nhật service ghi để duy trì cả 2 (single-source via signals/managers).

(Spec chi tiết hơn sẽ phụ thuộc trả lời Q4.)

---

## Phase D — Verify cuối

### Task D1: `makemigrations --check`

- [ ] **Step 1:**

```bash
cd backend && python manage.py makemigrations --check --dry-run
```
Expected: "No changes detected".

- [ ] **Step 2: Migrate thuận + nghịch toàn bộ trên DB tạm**

```bash
cd backend && python manage.py migrate
cd backend && python manage.py migrate orders zero --fake-initial  # rồi forward lại
```

(Hoặc dùng strategy DB tạm theo memory dự án.)

- [ ] **Step 3: Test bộ liên quan**

```bash
cd backend && python manage.py test apps.orders apps.dispatch -v 1
```

- [ ] **Step 4: Push**

```bash
git branch --show-current
git push 2>&1 | tail -5
```

---

## Self-Review

Spec 3 mục:
- A. Index theo bằng chứng → Phase A ✓
- B. Job TextField→CharField → Phase B ✓
- C1 unique external_order_id → C2 (gated) ✓
- C1 unique order_code → C3 (gated) ✓
- C2 on_delete CASCADE → C4 (gated) ✓
- C3 JSONField tách cột → C5 (gated) ✓

Mục gated được đánh dấu rõ và không tự ý chạy — đúng nguyên tắc đo-trước-sửa-sau và cần xác nhận business.
