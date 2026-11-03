# Audit toàn hệ thống — 2026-07-23

> Phạm vi: backend, frontend, database, security, CI/CD, infra.
> Phương pháp: đo trực tiếp trên code + chạy đối chứng thật bằng Docker. Không suy đoán.
> Baseline: master `a3cf0453`. Các đợt audit trước (07-03 → 07-18) đã đóng phần lớn lỗ
> hổng kinh điển — tài liệu này chỉ ghi những gì **còn hở**, không lặp lại việc đã xong.

## Đánh giá tổng quan

Hệ thống đã trưởng thành hơn mặt bằng công ty 50 người rất nhiều: modular monolith có
contract phân tầng cưỡng chế bằng import-linter, OpenAPI drift-check 2 chiều FE↔BE,
Sentry + Prometheus + Loki + Alertmanager, backup có metric sống, Postgres tuning thủ công,
index GIN trigram + partial index có đo đạc, JWT versioned, throttle keyed theo CF-Connecting-IP,
Docker multi-stage non-root. **Không có lý do kỹ thuật để đổi stack hay tách microservices.**

Nhưng có **một lỗ hổng nghiêm trọng làm vô hiệu hoá phần lớn lưới an toàn**, và một
quyết định bảo mật bị chính file compose ghi đè ngược.

| Mảng | Trạng thái |
|---|---|
| Security (cấu hình) | Tốt — trừ 1 điểm bị ghi đè ở prod |
| Security (bề mặt API) | Tốt — DRF default `IsAuthenticated`, mọi `AllowAny` đều là webhook token-auth hoặc form public có throttle |
| Database (schema/index) | Rất tốt |
| Database (vận hành/quan sát) | Thiếu tầng quan sát query |
| Backend (kiến trúc) | Khá — nợ phân tầng còn tồn |
| Frontend | Tốt về hạ tầng — nợ ở kích thước file |
| CI/CD | **Hỏng ở mắt xích quan trọng nhất** |

---

## 🔴 NGHIÊM TRỌNG

### N0. Không có CI nào chạy — workflow viết cho GitHub trong repo host trên GitLab

> Phát hiện khi bắt tay sửa N1 (2026-07-24). Nghiêm trọng hơn N1 và bao trùm N1.

Remote của repo là `git@gitlab.com:desi3512912/desi-order.git`, nhưng toàn bộ định
nghĩa CI nằm ở `.github/workflows/{backend,frontend}-ci.yml` — **cú pháp GitHub
Actions**. GitLab chỉ đọc `.gitlab-ci.yml`; repo **không có** file đó:

```
$ ls .gitlab-ci.yml       → NO .gitlab-ci.yml
$ git remote -v           → origin git@gitlab.com:desi3512912/desi-order.git
$ ls .github/workflows/   → backend-ci.yml  frontend-ci.yml
```

Kể cả khi đổi đường dẫn CI config trong Settings, GitLab vẫn không hiểu cú pháp
`on:` / `runs-on:` / `uses:` của GitHub Actions. Nghĩa là **không một kiểm tra tự động
nào từng chạy** khi push hay mở MR: không lint, không test, không migration check,
không OpenAPI drift-check, không build.

Bằng chứng gián tiếp củng cố: workflow GitHub thiếu hẳn biến `ENCRYPTION_KEY` mà
migration `kiotviet/0009` bắt buộc phải có — nếu nó từng chạy thật, nó đã đỏ ngay ở
bước dựng test DB và ai đó đã phải sửa.

Hệ quả: mọi cổng chất lượng mô tả trong `system-audit-hardening-2026-07.md` (mục 7.6,
D7, D9) tồn tại trên giấy chứ chưa từng bảo vệ nhánh nào.

**Đã sửa:** thêm `.gitlab-ci.yml` chạy thật trên GitLab (lint · test · OpenAPI contract
· build), dùng pytest ngay từ đầu nên đóng luôn N1.
**⚠️ Còn cần người vận hành:** file CI chỉ có tác dụng khi project đã gắn GitLab Runner.
Kiểm tra Settings → CI/CD → Runners; chưa có runner thì pipeline nằm "pending" vĩnh viễn.

### N1. CI chạy sai test runner — 817/2135 test (38%) chưa từng được chạy trên CI

`.github/workflows/backend-ci.yml:61` chạy:

```yaml
- name: Run tests
  run: uv run python manage.py test -v1
```

Nhưng repo cấu hình pytest (`backend/pytest.ini`) và **103/268 file test viết theo phong cách
pytest function** (`def test_x():` ở module level, không bọc trong `TestCase`). Test runner
unittest của Django **chỉ thu thập subclass của `TestCase`** — các file kia vẫn được *import*
(nên không báo lỗi) nhưng sinh ra **0 test**. CI xanh trong khi test không chạy.

**Bằng chứng đo thật** (image `desi-backend-test`, Postgres/Redis container thật):

```
DJANGO RUNNER COLLECTED: 1318      ← thứ CI đang chạy
pytest apps --collect-only: 2135   ← thứ thực sự tồn tại
```

→ **817 test (38,3%) là vùng mù của CI.** Phân bố đúng vào các module rủi ro nhất:

| App | Test bị CI bỏ qua | Ghi chú |
|---|---:|---|
| `inventory` | 249 | 22k LOC, luồng kho/scan/hoàn hàng |
| `warranty` | 117 | có endpoint public |
| `orders` | 70 | doanh thu |
| `best_express` | 57 | webhook vận chuyển |
| `kiotviet` | 54 | **luồng tiền / đồng bộ hoá đơn** |
| `contact_flow` | 24 | |
| `hr` | 16 | |
| `customers` | 14 | |
| `users` | 7 | **RBAC / auth** |

Nguy hiểm kép: mọi kết luận "0 regression" ở các audit trước đều dựa trên chạy pytest **local**,
còn cổng chặn merge thì không. Test viết đúng, chỉ là không ai gác cửa.

**Sửa** (1 dòng, rủi ro thấp — chạy pytest local trước để chốt baseline fail):

```yaml
- name: Run tests
  run: uv run pytest apps -q
```

### N2. Prod ghi đè hardening JWT: access token sống **1 năm**

`config/settings_parts/base.py:661-663` cố tình hạ vòng đời token, kèm comment giải thích
đúng lý do:

```python
# Trước đây mặc định là 1 năm / 365 ngày — token lộ sẽ dùng được rất lâu.
JWT_ACCESS_MINUTES = int(os.getenv("JWT_ACCESS_MINUTES", "60"))
JWT_REFRESH_DAYS = int(os.getenv("JWT_REFRESH_DAYS", "14"))
```

`docker-compose.prod.yml:61-62` đưa nguyên giá trị cũ trở lại:

```yaml
JWT_ACCESS_MINUTES: ${JWT_ACCESS_MINUTES:-525600}   # = 365 ngày
JWT_REFRESH_DAYS:   ${JWT_REFRESH_DAYS:-365}
```

Access token là JWT tự chứa — **không blacklist được**. Blacklist chỉ áp cho refresh token.
Giảm nhẹ duy nhất là `VersionedJWTAuthentication` (claim `token_version`), nhưng nó chỉ tăng
khi **đổi mật khẩu**. Hệ quả: một access token rò rỉ (log, devtools, proxy, máy nhân viên
nghỉ việc chưa reset pass) dùng được **tới 1 năm**, và cách thu hồi duy nhất là ép user đổi mật khẩu.

**Điểm mấu chốt: frontend đã sẵn sàng cho token ngắn.** `frontend/src/services/http.ts` giữ
access token **in-memory**, refresh token ở **httpOnly cookie**, và có refresh **chủ động**
(`shouldRefreshAccessToken` refresh trước khi hết hạn, `refreshInFlight` chống refresh storm).
Giá trị 1 năm là di sản từ thời chưa có cơ chế này — giữ lại không đổi lấy được gì.

**Sửa:** đặt `JWT_ACCESS_MINUTES=60`, `JWT_REFRESH_DAYS=14` (hoặc 30) trong `.env` prod, rồi
bỏ default 525600 khỏi compose. Deploy vào giờ thấp điểm; user đang đăng nhập sẽ tự refresh,
không bị đá ra.

---

## 🟠 CAO

### C1. Không có `pg_stat_statements` — mù hoàn toàn ở tầng query

Toàn repo không nơi nào bật extension này (chỉ được **nhắc tới** trong docs như "nếu bật").
Đang có Prometheus + Grafana + `postgres-exporter`, tức thấy được connection/lock/cache-hit,
nhưng **không biết query nào tốn thời gian nhất**. Trong khi lịch sử sự cố toàn là query:
`report-precompute-timeout-2026-07-22`, `resale-tracking-performance`, `REPORT_QUERY_TIMEOUT_SECONDS`,
`statement_timeout=30s` toàn cục. Đang chữa triệu chứng bằng timeout mà không có công cụ tìm nguyên nhân.

**Sửa** — thêm vào `docker-compose.prod.yml` service postgres:

```yaml
-c shared_preload_libraries=pg_stat_statements
-c pg_stat_statements.max=5000
-c pg_stat_statements.track=top
```

Cần restart Postgres (không phải reload). Sau đó `CREATE EXTENSION pg_stat_statements;` và bật
collector tương ứng ở `postgres-exporter`. Chi phí ~vài MB RAM, đổi lấy câu trả lời cho
"top 10 query theo tổng thời gian" — thứ hiện phải đoán.

### C2. `max_connections=100` không có connection pooler, `CONN_MAX_AGE=0`

Cấu hình hiện tại là hệ quả trực tiếp của sự cố 2026-07-23 (~96 tab reconnect → cạn slot).
Cách chữa đã chọn: tắt persistent connection (`DJANGO_CONN_MAX_AGE=0`) + chặn đồng thời ở tầng
ứng dụng (`WS_HANDSHAKE_DB_CONCURRENCY=8`). Đúng về mặt dập lửa, nhưng là **đánh đổi lâu dài**:
mỗi request giờ tự bắt tay TCP + auth Postgres, và không tận dụng được prepared statement.

Với lộ trình 100–150 nhân sự, đây là trần cứng đang tới gần. PgBouncer (transaction pooling)
giải quyết cả hai vế: app mở/đóng connection thoải mái, Postgres chỉ thấy ~20–30 connection thật.

**Sửa:** thêm service `pgbouncer` (`edoburu/pgbouncer` hoặc `bitnami/pgbouncer`), `pool_mode=transaction`,
`default_pool_size=25`, `max_client_conn=500`; trỏ `POSTGRES_HOST=pgbouncer`. Sau đó bật lại
`DJANGO_CONN_MAX_AGE=600` an toàn.
⚠️ `pool_mode=transaction` **không tương thích** với prepared statement phía server —
psycopg3 dùng chúng mặc định, phải đặt `OPTIONS={"prepare_threshold": None}` trong `DATABASES`.
Đây là bẫy phổ biến nhất khi gắn PgBouncer vào Django + psycopg3, cần test kỹ.

---

## 🟡 TRUNG BÌNH

### T1. Gần như không có lưới chống N+1
Chỉ **5/268** file test dùng `assertNumQueries`/`CaptureQueriesContext`, trong khi có 177 điểm
`select_related`/`prefetch_related`. Nghĩa là: tối ưu đã làm, nhưng không có gì ngăn nó bị
phá âm thầm — thêm một field vào serializer là đủ. Đề xuất: bắt buộc `assertNumQueries` cho
mọi list-endpoint chính (orders, customers, inventory flow-history, contacts).

### T2. Ruff trên CI gần như không lint gì
`uvx ruff check --select E9,F63,F7,F82 .` — chỉ 4 nhóm rule (syntax error + undefined name).
Không có `pyproject.toml` cấu hình ruff ở backend. Comment ghi "mở rộng rule dần" nhưng chưa
mở rộng. Đề xuất: thêm `[tool.ruff]` với `E,F,I,UP,B,SIM`, dùng `--statistics` để đo nợ, rồi
bật theo từng app qua `per-file-ignores` thay vì bật cả loạt.

### T3. Nợ phân tầng kiến trúc
7 app còn `views.py` phẳng (`audit`, `callcenter`, `customers`, `google_ads`, `users`, `warranty`,
`kiotviet`); riêng `orders` có **cả hai** (`views.py` phẳng lẫn `transport/`) — trạng thái nửa
vời khó nhất để bảo trì. `.importlinter` đang treo **25 entry nợ**, nặng nhất là
`orders → kiotviet` (FK model + gọi thẳng service sync hoá đơn). Không cần sửa gấp, nhưng
`orders` nên dứt điểm chuyển hết sang `transport/` để hết cảnh "tìm view ở hai chỗ".

### T4. `orders` có 108 migration
Migrate từ đầu (CI, môi trường mới, staging refresh) phải replay 108 file. Nên `squashmigrations`
mốc trước migration mới nhất đã deploy prod, giữ file cũ tới khi mọi môi trường qua mốc.

### T5. Frontend — vài file quá lớn
Hạ tầng FE rất tốt (36 route lazy, `manualChunks`, React 19 + Compiler, Tailwind 4, typecheck +
lint:strict 0 warning + OpenAPI drift-check). Nợ nằm ở kích thước:
`use-orders-data.ts` **2786 LOC**, `OrdersPage.tsx` 2087, `OrderFormDialog.tsx` 2076,
`DashboardSections.tsx` 1443. Một hook 2786 dòng nghĩa là mọi thay đổi trang Đơn hàng đều
đụng cùng một file — điểm nghẽn khi đội tăng lên. Tách theo trục nghiệp vụ (filter / bảng /
bulk-action / export), không tách theo dòng.

### T6. `dev` và `master` đã phân kỳ
`dev` có 16 commit chưa vào `master`; `master` có 138 commit chưa vào `dev`. CLAUDE.md quy định
làm việc trên `dev` mới nhất, nhưng thực tế merge đang đổ vào `master`. Cần chốt lại **một**
nhánh tích hợp rồi sửa quy ước cho khớp — hiện tại quy ước và thực tế đang nói hai điều khác nhau.

### T7. Comment CI frontend đã lỗi thời
`.github/workflows/frontend-ci.yml` ghi *"test:ci excludes pre-existing failures (websocket,
OrderFormDialog, *.mobile)"*, nhưng `package.json` có `"test:ci": "vitest run"` — **giống hệt**
`test`, không loại trừ gì. Hoặc lỗi đã sửa mà quên xoá comment, hoặc phần loại trừ đã mất.
Comment sai về vùng phủ test còn nguy hiểm hơn không có comment.

---

## Đề xuất tech stack

**Không đổi gì lớn.** Django 6 + DRF + Celery + Postgres 16 + React 19/Vite 7 là lựa chọn đúng
cho quy mô này, và quyết định "không tách microservices, không rewrite DRF→ninja" ở
`tech-stack-evolution-2026.md` vẫn còn nguyên giá trị. Với 1–2 dev, microservices sẽ đổi một
vấn đề dễ (query chậm) lấy nhiều vấn đề khó (distributed transaction, tracing, deploy).

Chỉ nên thêm **hai thành phần hạ tầng**, cả hai đều là bổ sung thuần, không đụng code nghiệp vụ:

| Thành phần | Giải quyết | Chi phí |
|---|---|---|
| **PgBouncer** | Trần `max_connections=100`, cho phép bật lại `CONN_MAX_AGE` | 1 container ~20MB + bẫy prepared statement (C2) |
| **pg_stat_statements** | Mù tầng query (C1) | vài MB RAM + 1 lần restart Postgres |

**Không khuyến nghị lúc này:**
- *Kafka / RabbitMQ* — Celery + Redis đang phục vụ tốt; thêm broker thứ hai là thêm thứ để vận hành, không giải quyết vấn đề nào đang có.
- *Read replica* — đúng hướng nhưng chưa tới lúc; DB 7GB, và chưa có `pg_stat_statements` thì chưa biết đọc/ghi lệch ra sao. Làm C1 trước, đo, rồi mới quyết.
- *Next.js / RSC* — SPA sau login không hưởng lợi từ SSR; chi phí rewrite không đổi lấy gì.
- *Golang cho service mới* — chỉ khi có nhu cầu throughput cụ thể (`go-offload-plan.md`); đội 1–2 dev nuôi hai ngôn ngữ backend là chia đôi năng lực.

---

## Thứ tự thực hiện đề xuất

| # | Việc | Rủi ro | Ước lượng |
|---|---|---|---|
| 1 | **N1** — đổi CI sang pytest, chốt danh sách baseline fail | Thấp | 1–2h (+ thời gian xử lý test đỏ lộ ra) |
| 2 | **N2** — hạ vòng đời JWT ở prod | Thấp | 30' + theo dõi 1 ngày |
| 3 | **C1** — bật `pg_stat_statements` | Thấp (cần restart PG) | 1h |
| 4 | **T7** — làm rõ comment CI frontend | Thấp | 15' |
| 5 | **T1** — `assertNumQueries` cho list-endpoint chính | Thấp | 1 ngày |
| 6 | **C2** — PgBouncer | **Trung bình** — cần test prepared statement | 1 ngày + staging |
| 7 | **T2** — mở rộng ruff | Thấp | 0,5 ngày |
| 8 | **T4/T5/T3** — squash migration, tách file FE, dứt điểm `transport/` cho orders | Trung bình | theo sprint |

> Lưu ý thứ tự: **N1 phải làm trước mọi việc khác.** Chừng nào CI còn bỏ qua 38% test thì mọi
> thay đổi ở dưới đều đang được verify bằng một cái lưới thủng.

---

# Nhật ký thực hiện — 2026-07-24

Nhánh `fix/audit-2026-07-23-followups`.

## Đã xong

| Mục | Thay đổi | Bằng chứng |
|---|---|---|
| **N0** | Thêm `.gitlab-ci.yml` — CI thật chạy trên GitLab (lint · test · OpenAPI contract · build). Docker build để `when: manual` để không đốt CI minutes. | `docker compose config` hợp lệ; cú pháp theo GitLab CI |
| **N1** | CI dùng `pytest apps`, bỏ `manage.py test`. Áp cho cả `.gitlab-ci.yml` lẫn `.github/workflows` (phòng khi mirror sang GitHub). Bổ sung `ENCRYPTION_KEY` còn thiếu ở workflow GitHub. | full suite: **2135 test chạy** (trước: 1318) |
| **N1b** | Sửa 2 test RBAC đỏ sẵn trên master: `Role.objects.create(name="Administrator")` trùng `rbac_roles_name_key` vì migration 0015 đã seed role đó vào test DB. Đổi tên role trong test — `rbac._compute` short-circuit theo cờ `is_system`, không theo tên, nên ý nghĩa test giữ nguyên. | 2 test chuyển từ đỏ sang xanh |
| **N2** | `JWT_ACCESS_MINUTES` 525600 → **60**, `JWT_REFRESH_DAYS` 365 → **14** ở prod compose. Local compose đặt bằng prod để luồng refresh được chạy khi dev (trước đây token 1 năm nên lỗi refresh chỉ lộ ở prod). Ghi vào `.env.example`. | `docker compose config` in ra `JWT_ACCESS_MINUTES: "60"` |
| **C1** | Bật `shared_preload_libraries=pg_stat_statements` + `--collector.stat_statements` cho postgres-exporter. | runbook `2026-07-24-enable-pg-stat-statements.md` |
| **C2** | PgBouncer **opt-in** (`profile: pgbouncer`, `DB_PGBOUNCER=0` mặc định). Settings tự đặt `prepare_threshold=None` và **ngừng gửi `statement_timeout` qua startup options** khi bật pooler. | runbook `2026-07-24-pgbouncer-cutover.md` |
| **T1** | Test chống N+1 cho `/api/orders`: so số query giữa 3 đơn và 15 đơn thay vì chốt trần cứng dễ vỡ. | đo thật: **8 query cho cả 3 lẫn 15 đơn** — thêm 1 query/đơn là đỏ ngay |
| **CI-bug** | `uv run` + `pyproject.toml` mới thêm = venv rỗng, mất sạch package đã cài. Đổi sang gọi `python`/`uvx` trực tiếp ở cả 2 hệ CI. | tái hiện trong image uv: `ModuleNotFoundError: No module named 'django'` |
| **T2** | `backend/pyproject.toml` với ruleset `E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP` + danh sách nợ hiện hữu (405 vi phạm) ghi rõ từng mã kèm số lượng. | `ruff check .` → **All checks passed** |
| **T7** | Xoá comment sai về `test:ci`; ghim `engines.node` `>=22 <24`. | Node 22: **704/704 pass**; Node 24 làm 2 test RequireAuth đỏ giả (undici siết `AbortSignal`) |

**Chủ ý không auto-fix hàng loạt.** `ruff --fix` dọn được 194 vi phạm nhưng sẽ chạm hàng
trăm file và conflict với ~20 nhánh đang mở; riêng F401 trong các module facade
(`from .x_impl import *`) là re-export cố ý — "fix" sẽ xoá đúng thứ đang được import nơi khác.

## Nợ lộ ra khi bật CI thật — đã dọn

Dựng CI xong mới thấy: **mọi cổng chất lượng đều đang đỏ trên master**. Đây là bằng chứng
trực tiếp cho N0 — audit 2026-07-03 ghi nhận "lint:strict 0 warning ✅, format ✅",
và từ đó tới nay nợ tích lại vì không có gì chặn.

| Cổng | Trạng thái trên master | Đã xử lý |
|---|---|---|
| `lint:strict` | **6 warning** (ngưỡng 0) | Gỡ import thừa, đổi tên tham số không dùng, tách `PAGE_SIZE` khỏi file component (Fast Refresh), tắt đúng dòng 3 cảnh báo react-compiler |
| `format:check` | **4 file** lệch prettier | `prettier --write` |
| OpenAPI completeness | **3 endpoint** không có `@extend_schema` dùng được | `request=None` cho `complete_warranty_claim` + `add_journey` (POST không đọc body); `exclude=True` cho webhook BEST Express (hợp đồng của vendor, không thuộc API nội bộ) |
| OpenAPI drift | `schema.yaml` thiếu field `occurred_at` | regen + commit |
| `gen:api` drift-check | **không bao giờ pass được** — `openapi-typescript` xuất thụt 4 space còn file trong repo đã qua prettier (2 space) | `gen:api` chạy prettier ngay sau khi sinh |
| `lint-imports` | **23 vi phạm** contract phân tầng tích trên master | khai vào `ignore_imports` có phân loại (xem dưới) |

### Contract phân tầng — 23 vi phạm tích trong 3 tuần

`.importlinter` được viết 2026-07-03 nhưng **chưa từng được thực thi** (đúng hệ quả của N0).
Chạy lần đầu trên master sạch: **23 vi phạm** ngoài danh sách nợ đã khai. Đã khai vào
`ignore_imports` để contract chặn được nợ MỚI, chia 3 nhóm theo mức độ:

| Nhóm | Số | Bản chất |
|---|---:|---|
| (a) `staging_neutralize` | 8 | Command xoá dữ liệu nhạy cảm của MỌI app → chạm tầng trên là đúng bản chất, **không phải nợ** |
| (b) Chỉ là test | 11 | Dựng fixture bằng model tầng trên — ưu tiên trả thấp |
| (c) **Code prod vượt tầng** | 4 | **Nợ kiến trúc thật**, cần refactor qua `contracts`/events |

Bốn entry nhóm (c) đáng chú ý — đây là code chạy thật, không phải test:
`inventory.return_order_sync` và `orders.invoice` gọi thẳng
`kiotviet.infrastructure.services.invoice_mutations_push`; `orders.order_service_commands`
và `orders.shipping_lookup_view` gọi thẳng `kiotviet...cancellation_verifier`.

Về 3 cảnh báo `react-compiler`: chúng phát sinh vì `react-hooks/exhaustive-deps` bị tắt có
chủ đích ở 3 `useEffect`. Đọc comment tại chỗ thì thấy deps hẹp là **có tải trọng nghiệp vụ**
— chúng bảo vệ bản nháp chưa lưu của người dùng khỏi bị wipe khi refetch realtime. Viết lại
để chiều lint là đổi một cảnh báo cosmetic lấy rủi ro mất dữ liệu người dùng, nên tắt đúng
dòng kèm giải thích thay vì refactor.

Một chi tiết đáng ghi: `react-compiler` báo lỗi tại **dòng chứa comment `eslint-disable`**,
không phải dòng `useEffect` — đặt disable trước `useEffect` không có tác dụng.

## Cần người vận hành (không tự làm được từ code)

1. **Gắn GitLab Runner** — `.gitlab-ci.yml` vô nghĩa nếu chưa có runner (Settings → CI/CD → Runners).
2. **C1** — restart Postgres + `CREATE EXTENSION pg_stat_statements` theo runbook.
3. **C2** — validate staging rồi mới cutover prod theo runbook. Chưa làm thì không có gì đổi.
4. **N2** — deploy giờ thấp điểm, theo dõi 1 ngày.

## Chưa làm — lý do

| Mục | Vì sao tách sprint riêng |
|---|---|
| **T3** — dứt điểm `transport/` cho `orders` | Đụng 311 file / 43k LOC của app doanh thu. Cần CI xanh chạy thật vài vòng trước khi dám refactor ở quy mô này — mà CI mới vừa được dựng hôm nay. |
| **T4** — squash 108 migration của `orders` | Squash an toàn đòi **mọi môi trường** (prod, staging, máy dev) đã qua mốc squash. Không xác minh được trạng thái migration prod từ đây; đoán sai là hỏng deploy. Cần người vận hành xác nhận `showmigrations` trên prod trước. |
| **T5** — tách `use-orders-data.ts` (2786 LOC) | Refactor nhiều ngày, blast radius là toàn trang Đơn hàng. Gộp chung với 9 thay đổi hạ tầng trong một MR là không review nổi. |
| **T6** — phân kỳ `dev`/`master` | **Quyết định của con người, không phải của code.** Dữ liệu: `master` đi trước `dev` 138 commit; 16 commit riêng của `dev` đều là overview-report, và tính năng đó **đã có mặt trên master** (`test_overview_report_product_filter.py`, `ProvinceRevenueCard.test.tsx`…) tức đã được làm lại. Nên `master` là nhánh tích hợp thật, còn `dev` là nhánh chết. Việc cần làm — reset `dev` về `master` hay xoá hẳn, rồi sửa CLAUDE.md cho khớp — là thao tác phá huỷ trên nhánh chung, cần anh chốt. |
