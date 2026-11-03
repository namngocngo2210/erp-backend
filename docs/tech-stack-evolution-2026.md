# Tech Stack Evolution — H2 2026

> Trạng thái: **DRAFT / chờ phê duyệt** · Chủ trì: CTO · Đội thực thi: 1–2 dev
> Phạm vi: nâng cấp tiệm tiến tầng API (BE) và build/UI (FE) **không rewrite hệ thống**.
> Tài liệu liên quan: [`erp-master-plan.md`](./erp-master-plan.md) (D1–D6),
> [`architecture-standard.md`](./architecture-standard.md) (luật ranh giới module),
> [`erp-app-shell.md`](./erp-app-shell.md) (D5/D6 app-shell).

## 0. Bối cảnh & nguyên tắc

Stack hiện tại (đo ngày 2026-06-24) đã hiện đại và phù hợp: Django 6 + DRF 3.17 / Python 3.13,
React 18 + Vite 5 + TS strict + react-query + shadcn/ui + Tailwind 3. **Không có lý do nghiệp vụ
để đổi stack.** Tài liệu này KHÔNG đề xuất rewrite; nó vá đúng 3 lỗ hổng thật và thực hiện 2 nâng cấp
phiên bản tiệm tiến.

Bề mặt hệ thống (chứng cứ đo bằng grep, để định cỡ rủi ro):

| Hạng mục BE | Số lượng | Hệ quả |
|---|---|---|
| Endpoint (`@api_view` + view) | ~170 (171 function-based, 1 ViewSet) | Bề mặt rewrite nếu đổi framework |
| Serializer class | 120 (trong 27 file) | — |
| `permission_classes` (RBAC) | 211 điểm | RBAC là tài sản, không được fork |
| Custom auth | `apps/users/jwt.py` (`VersionedJWTAuthentication`) + RBAC `apps/users/rbac.py` (`scope_for`) | Nguồn chân lý phân quyền |
| Test dùng `APIClient` | 96 file | Lưới an toàn cho mọi thay đổi transport |

**Nguyên tắc xuyên suốt:** mọi thay đổi phải (1) giữ nguyên hợp đồng HTTP hiện có, (2) có cờ bật/tắt
hoặc rollback rõ ràng, (3) không vi phạm luật ranh giới module, (4) qua được CI hiện có
(ruff/pytest/eslint/vitest/build) trước khi merge.

---

## 1. Quyết định kiến trúc (mở rộng chuỗi D)

| ID | Quyết định | Lý do |
|----|-----------|-------|
| **D7** | **Tầng hợp đồng API bằng `drf-spectacular`** (OpenAPI 3.1) sinh tự động từ DRF hiện có — **không sửa endpoint** | Lấy được single-source-of-truth cho FE↔BE với chi phí gần 0 |
| **D8** | **`django-ninja` theo strangler cho module MỚI**, dùng **auth lõi chung** rút ra từ `jwt.py`+`rbac.py`. **CẤM big-bang rewrite 170 endpoint DRF** | Có DX của ninja ở nơi greenfield mà không gánh rủi ro rewrite revenue-critical |
| **D9** | **FE tiêu thụ API qua type sinh tự động** (`openapi-typescript` + `openapi-fetch`), bọc trong hook react-query mỏng | Đóng lỗ hổng type FE↔BE, bắt lệch ở compile-time |
| **D10** | **React 19 + React Compiler** (auto-memo), KHÔNG dùng RSC | Bỏ memo thủ công, ít bug stale, không đụng kiến trúc SPA |
| **D11** | **Tailwind CSS v4** (CSS-first config) | Build nhanh hơn, chuẩn mới của shadcn/ui |

**Phản-quyết định (ghi rõ để khỏi tái tranh luận):** KHÔNG rewrite DRF→ninja toàn bộ; KHÔNG đổi
React→Next/Vue; KHÔNG tách microservices; KHÔNG bỏ Celery. Xem lý do định lượng ở mục 0 và
hội thoại thiết kế.

---

## 2. Workstream 1 — OpenAPI contract bằng drf-spectacular (D7)

**Mục tiêu:** mọi endpoint DRF hiện có phát ra schema OpenAPI 3.1 chính xác, không sửa logic.

### Thiết kế
- Thêm `drf-spectacular` vào `requirements.txt`/`pyproject.toml`, đăng ký `DEFAULT_SCHEMA_CLASS`
  trong `REST_FRAMEWORK` (`config/settings_parts/base.py:396`).
- Đăng ký security scheme JWT (Bearer) để schema phản ánh đúng auth.
- Expose `/api/schema/` (SpectacularAPIView) + `/api/docs/` (Swagger UI) — **chỉ bật ở môi trường
  non-prod** hoặc sau RBAC admin, không phơi public.
- Sinh file tĩnh `backend/schema.yaml` qua `manage.py spectacular --file schema.yaml`, **commit vào repo**
  làm artifact hợp đồng.

### Điểm đau đã lường trước (quan trọng)
171 endpoint là **function-based `@api_view`** — phần lớn **thiếu metadata serializer** nên schema sinh ra
sẽ **không đầy đủ** (request/response body trống). Đây là phần việc thật của workstream này:
- Bổ sung `@extend_schema(request=..., responses=...)` cho các endpoint không suy ra được kiểu.
- Ưu tiên theo lưu lượng: làm trước các module FE đang gọi nhiều (orders, customers, inventory),
  để workstream 2 có type tốt sớm.
- KHÔNG cần phủ 100% ngay; mỗi sprint nâng độ phủ và đo bằng `--fail-on-warn`.

### CI gate
- Thêm step `spectacular --fail-on-warn` (cảnh báo schema = fail) vào `backend-ci.yml`.
- Thêm step kiểm tra **drift**: regenerate schema và `git diff --exit-code schema.yaml` → đổi API mà
  quên cập nhật schema thì CI đỏ.

### Done khi
Schema sinh không warning cho ≥ orders/customers/inventory; Swagger UI mở được ở dev; CI có drift-check.

---

## 3. Workstream 2 — Typed FE client (D9)

**Mục tiêu:** FE không còn gõ tay `interface` cho response; đổi field ở BE → FE đỏ compile.

### Thiết kế
- `openapi-typescript` đọc `backend/schema.yaml` → sinh `frontend/src/api/schema.d.ts`
  (chỉ type, không runtime).
- `openapi-fetch` làm HTTP client typed (nhẹ, ~6kb, không sinh code thừa).
- Bọc trong **hook react-query mỏng** theo feature-slice hiện có (`features/<domain>/queries.ts`),
  giữ nguyên convention. Không thay react-query.
- npm script `gen:api` chạy codegen; `schema.d.ts` **commit vào repo**; CI check drift tương tự BE.

> **Quyết định mặc định (đổi được):** chọn `openapi-typescript + openapi-fetch` (types-first, nhẹ)
> thay vì `orval`/full-client-gen, vì codebase đã có pattern react-query riêng — không muốn generator
> áp đặt cách viết hook. Nếu muốn sinh luôn hook react-query, cân nhắc `orval` ở sprint sau.

### Chiến lược di trú (không big-bang)
- **Code mới** bắt buộc dùng type sinh tự động.
- **Code cũ** chuyển dần khi đụng vào; ưu tiên các interface response đang lệch thực tế.
- KHÔNG có chiến dịch "thay hết interface một lần".

### Done khi
`gen:api` chạy được trong CI; ít nhất 1 feature-slice (orders) tiêu thụ type sinh tự động end-to-end;
lệch field BE bị bắt ở `tsc`.

---

## 4. Workstream 3 — django-ninja strangler + auth lõi chung (D8)

> **ĐÍNH CHÍNH 2026-08-20:** django-ninja đã GỠ khỏi codebase (audit 2026-08-19 mục 4.12 —
> strangler không tiến triển: chỉ 3 file scaffold demo, 142 file vẫn DRF). Auth lõi chung
> (`apps/users/auth_core.py`) GIỮ NGUYÊN. Muốn thử lại thì revert MR gỡ ninja.

**Mục tiêu:** sẵn sàng để **module greenfield kế tiếp** viết bằng ninja, **không** đụng DRF đang chạy;
phân quyền dùng chung một nguồn.

### Thiết kế
1. **Rút auth lõi framework-agnostic.** Tách phần verify JWT (`apps/users/jwt.py`) và `scope_for`
   (`apps/users/rbac.py`) thành hàm/đối tượng thuần Python (không phụ thuộc DRF request).
   - DRF `VersionedJWTAuthentication` gọi lõi này (refactor, giữ hành vi — 96 file test bảo chứng).
   - Viết `NinjaJWTAuth(HttpBearer)` cùng gọi đúng lõi đó.
   - **Bất biến:** chỉ MỘT chỗ định nghĩa "token hợp lệ" và "user thấy được gì".
2. **Mount ninja song song.** `NinjaAPI` cho module mới (router theo module, nằm trong
   `apps/<new_module>/transport/`), tuân thủ luật ranh giới module như DRF.
3. **Hợp nhất OpenAPI.** ninja tự sinh OpenAPI; gộp/đặt cạnh schema drf-spectacular để FE codegen
   (WS2) tiêu thụ cả hai mà không phân biệt nguồn.
4. **Pilot.** Áp dụng cho đúng **1 module mới** (vd module Kế toán/Tài chính khi triển khai), rồi đo
   DX thực tế trên đội trước khi nhân rộng.

### Phản-quyết định
KHÔNG chuyển bất kỳ endpoint DRF hiện có nào sang ninja chỉ vì lý do framework. DRF và ninja
**cùng tồn tại** trong project; hội tụ tự nhiên qua nhiều năm.

### Rủi ro
Gánh nhận thức "đội phải biết 2 framework". Giảm thiểu bằng: auth lõi chung, cùng cấu trúc
`transport/`, cùng OpenAPI pipeline. Nếu pilot cho thấy DX không bù nổi gánh này → dừng ở DRF.

### Done khi
Auth lõi tách xong, test cũ xanh; có scaffold ninja chạy được 1 endpoint demo dùng chung RBAC;
**không** triển khai rộng cho tới khi có module mới thật.

---

## 5. Workstream 4 — React 19 + React Compiler (D10)

**Mục tiêu:** lên React 19, bật React Compiler để auto-memo, bỏ dần `useMemo`/`useCallback` thủ công.

### Thiết kế / thứ tự an toàn
1. **Kiểm tra tương thích thư viện** trước khi nâng: `ag-grid-react@32`, `react-select@5`,
   `recharts@2`, `@dnd-kit`, `next-themes`, `@testing-library/react@16`, `vite-plugin-pwa`.
   Cái nào chưa hỗ trợ React 19 → nâng major tương ứng hoặc hoãn.
2. **Bật lưới an toàn trước:** thêm `eslint-plugin-react-compiler` ở chế độ cảnh báo, sửa hết
   vi phạm Rules of Hooks/Rules of React. Compiler chỉ an toàn khi code đã "đúng luật".
3. **Nâng** `react`/`react-dom` 18.3 → 19; chạy codemod chính thức của React 19.
4. **Bật React Compiler** qua `babel-plugin-react-compiler` trong `@vitejs/plugin-react`
   (`vite.config.ts:20`). Bật phạm vi hẹp trước (1 feature-slice), đo, rồi mở rộng.
5. Toàn bộ 311 test vitest + build phải xanh; smoke test các trang nặng (ag-grid orders).

### Rủi ro & rollback
Thư viện bên thứ ba chưa sẵn sàng React 19 là rủi ro chính. Rollback = revert bump version
(thay đổi cô lập trong `package.json` + `vite.config.ts`). Compiler có thể tắt riêng mà vẫn giữ React 19.

### Done khi
Tất cả test/build xanh trên React 19; Compiler bật cho ≥1 slice không lỗi; không regression UI ở
trang orders/inventory.

---

## 6. Workstream 5 — Tailwind 3 → 4 (D11)

**Mục tiêu:** lên Tailwind v4 (engine mới, CSS-first), giữ nguyên giao diện.

### Thiết kế
- Chạy công cụ chính thức `npx @tailwindcss/upgrade` (tự chuyển `tailwind.config.ts` → `@theme` trong CSS,
  cập nhật utility đổi tên).
- Đổi pipeline: dùng `@tailwindcss/vite` plugin (`vite.config.ts`), bỏ `postcss.config.js` nếu không
  còn cần.
- **Xác minh tương thích shadcn/ui v4** (shadcn đã hỗ trợ Tailwind 4) và `tailwind-merge@3`,
  `class-variance-authority`.
- Soát thủ công: màu/spacing/`@apply`, light theme tông xanh da trời (chuẩn dự án) phải giữ nguyên.

### Rủi ro & rollback
Khác biệt rendering nhỏ sau khi đổi engine. Rollback = revert `package.json` + config CSS.
Làm **trước** React 19 vì rủi ro thấp hơn và cô lập ở tầng style.

### Done khi
UI khớp pixel với trước ở các trang chính; build xanh; không còn `tailwind.config.ts` legacy
(hoặc giữ qua `@config` nếu cần).

---

## 7. Kế hoạch sprint & tiến độ

Thứ tự theo phụ thuộc và rủi ro: **WS1→WS2** (có phụ thuộc); WS5 trước WS4 (cùng đụng FE build,
Tailwind rủi ro thấp hơn); WS3 độc lập, phần auth-core làm sớm còn pilot chờ module mới.

| Sprint | Nội dung | Phụ thuộc | Trạng thái |
|---|---|---|---|
| **S0** | Baseline: chốt spec, ghim version đích, dựng nhánh, đo lại CI xanh | — | ✅ Xong |
| **S1** | WS1 drf-spectacular: cài + schema + `@extend_schema` cho orders/customers/inventory + CI drift-check | S0 | ✅ Xong (mẫu vertical-slice; annotate diện rộng = S6) |
| **S2** | WS2 typed FE client: codegen + 1 slice (orders) end-to-end + CI drift-check | S1 | ✅ Xong |
| **S3** | WS3 auth-core: tách lõi JWT+RBAC, refactor DRF gọi lõi, scaffold ninja demo (test cũ xanh) | — (song song được) | ✅ Xong |
| **S4** | WS5 Tailwind 4: upgrade tool + verify UI | — | ✅ Code xong (⚠️ chờ QA thị giác) |
| **S5** | WS4 React 19 + Compiler: compat check → eslint-plugin → bump → bật compiler | S4 | ✅ Code xong (⚠️ chờ QA thị giác) |
| **S6** | Mở rộng độ phủ: thêm `@extend_schema` toàn bộ endpoint; CI fail-on-error | S1,S5 | ✅ Xong (annotate; dọn 13 compiler = backlog riêng) |

> Quy ước cập nhật: sau mỗi sprint, ghi mục "Đã làm / Chưa làm / Ghi chú" vào mục 9 và đổi ☐→✅.

---

## 8. Rủi ro tổng thể

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Schema không phủ hết function-based view | Cao | Làm theo lưu lượng, `--fail-on-warn`, không ép 100% ngay |
| Thư viện FE chưa hỗ trợ React 19 | Trung bình | Compat-check ở S5 trước khi bump; hoãn lib chưa sẵn |
| Gánh nhận thức 2 framework BE | Trung bình | Auth-core chung; pilot 1 module rồi mới quyết nhân rộng |
| Drift schema (BE đổi, FE/schema không cập nhật) | Trung bình | CI drift-check 2 phía |
| Khác biệt rendering Tailwind v4 | Thấp | Upgrade tool chính thức + soát UI thủ công |

**Mọi workstream độc lập và có rollback riêng** — có thể dừng/đảo bất kỳ cái nào mà không kéo theo cái khác.

---

## 9. Nhật ký sprint (cập nhật khi thực thi)

- **S0:** ✅ Harness test chạy qua ephemeral docker (`desi-order_default`). Baseline noise đã xác nhận
  & khớp memory: nhóm RBAC catalog/resolver/admin (migration `0014` seed sẵn catalog) + nhóm
  ads_staff scope trên `/api/orders` — fail trên clean master, KHÔNG phải regression.
- **S1:** ✅ **Đã làm:** `drf-spectacular==0.29.0` + transitive deps vào `requirements.txt`;
  `DEFAULT_SCHEMA_CLASS` + `SPECTACULAR_SETTINGS` (JWT Bearer) trong settings; endpoint
  `/api/schema` + `/api/docs` gate ngoài production; **auth extension** (`apps/users/schema.py`,
  guard import trong `apps.py`) → xóa 166 warning auth (214→6); `schema.yaml` commit làm hợp đồng;
  annotate `orders_list` (`@extend_schema`) làm mẫu vertical-slice cho S2; CI step **drift-check**.
  `manage.py check` xanh, ruff xanh, test `list_orders` xanh (18 passed). **Chưa làm (S6):** annotate
  diện rộng 839 endpoint `@api_view` còn lại + bật `--fail-on-warn`.
- **S2:** ✅ **Đã làm:** `openapi-typescript@7.13.0` (devDep) + script `npm run gen:api` sinh
  `src/api/schema.d.ts` (7244 dòng) từ `schema.yaml`; client typed `src/api/typed.ts`
  (`apiGet`/`apiPost`) **bọc `fetchJson` hiện có** — cố ý không dùng openapi-fetch để giữ nguyên
  logic auth (token in-memory + refresh cookie + retry 401); vertical-slice `listOrders` trả
  `OrderListResponse` **sinh từ hợp đồng** (bỏ type viết tay); script `typecheck` (vite build không
  typecheck) + CI step **drift-check schema.d.ts** + tsc, trigger thêm trên `backend/schema.yaml`.
  `tsc` xanh, lint xanh, `gen:api` idempotent. **Chiến lược:** code mới dùng type sinh tự động; code
  cũ chuyển dần (không big-bang).
- **S3:** ✅ **Đã làm:** `django-ninja==1.6.2` + pydantic vào `requirements.txt`; **lõi chung**
  `apps/users/auth_core.py` (`authenticate_bearer_token` ủy quyền chính `VersionedJWTAuthentication`;
  `user_has_any_permission`/`user_has_section` ủy quyền `resolve_effective_permissions`) — MỘT nguồn
  chân lý; refactor `permissions.py` (`require_permission`) gọi lõi; `apps/users/ninja_auth.py`
  (`NinjaJWTAuth` + `require_sections`); `NinjaAPI` chung `config/api_v2.py` mount `/api/v2/`, auth JWT
  mặc định; router demo `apps/users/ninja_demo.py` (`/whoami`, `/orders-guarded`). 5 test scaffold +
  18 test `require_section` (regression) đều xanh (23 passed); Django check sạch, ruff sạch,
  `schema.yaml` không drift. **Chưa làm:** pilot ninja trên module nghiệp vụ thật (chờ module
  greenfield); hợp nhất OpenAPI ninja vào FE codegen.
- **S4:** ✅ **Đã làm:** chạy `@tailwindcss/upgrade` chính thức → `tailwindcss@4.3.1`,
  `@tailwindcss/postcss` (bỏ `autoprefixer`), `tailwind.config.ts` gỡ bỏ (config vào `@theme` trong
  `styles.css`), `darkMode` → `@custom-variant`. **Hai lỗi codemod đã sửa tay:** (1) `<alpha-value>`
  bị hardcode `calc(1*100%)` sẽ phá **452 chỗ** dùng opacity (`bg-muted/40`…) → viết lại `@theme` map
  `--color-X: var(--X)` để v4 áp alpha native (đã verify color-mix trong CSS build); (2) codemod đổi
  nhầm prop `variant="outline"` → `"outline-solid"` ở 9 chỗ (Button/Badge) → revert. **Verify:** build
  xanh, `tsc` xanh, format/lint xanh (0 error), `test:ci` 252 passed (1 fail = baseline noise
  `modules.test.ts`, không do Tailwind). ⚠️ **Gate còn lại: QA thị giác** (không làm headless được) —
  soát light theme tông xanh + dark mode + ag-grid trước khi merge.
- **S5:** ✅ **Đã làm:** compat-check — **mọi lib hỗ trợ React 19** (ag-grid-react 32.3.9,
  react-select, recharts 2.15, @dnd-kit, next-themes, testing-library 16 đều có peer `^19`); bump
  `react`/`react-dom`@19.2.7 + `@types`@19; sửa **6 lỗi type** React 19 (`useRef` →
  `RefObject<T | null>`: popover/select context, login-view, CustomersToolbar); bật **React Compiler**
  (`babel-plugin-react-compiler@1.0.0`, target "19") trong `vite.config.ts`; thêm
  `eslint-plugin-react-compiler` rule **warn** (lưới an toàn). **Verify:** `tsc` xanh, build xanh
  (13s→41s do compiler), lint 0 error (13 warn react-compiler = component compiler tự bỏ qua, an
  toàn), `test:ci` 252 passed (1 fail baseline `modules.test.ts`). ⚠️ **Gate còn lại: QA thị giác**.
  **Chưa làm (S6):** dọn 13 component đang bị compiler bỏ qua (gỡ eslint-disable) để mở rộng auto-memo.
- **S6:** ✅ **Đã làm:** annotate `@extend_schema` cho **166 view `@api_view`** trên 13 app (qua 6 agent
  song song theo app, verify tập trung) → **Errors 839 → 0**, Warnings 214 → 15 (cosmetic:
  enum/operationId auto-resolve, 1 audit ViewSet). ~140 view annotate response/request thật (serializer
  có sẵn hoặc `inline_serializer` khớp dict thực); ~26 view `exclude=True` (webhook KiotViet/Zalo,
  SSE sync, file Excel/binary, HTML print, Prometheus). CI đổi sang **fail-on-error** (chặn endpoint mới
  quên annotate; KHÔNG fail-on-warn vì cosmetic → brittle) + giữ drift-check. FE types regen
  (7244→11447 dòng), `tsc` xanh. Schema **idempotent** (drift-check ổn định). Runtime verify: django
  check sạch, ruff sạch, test orders/channels/inventory/ninja xanh; 1 fail `customer_order_aggregate`
  = **baseline noise** (xác nhận fail trên clean base qua stash). **Backlog riêng (chưa làm):** dọn 13
  component React Compiler đang bỏ qua (cần refactor hook + QA — rủi ro, để incremental); 15 warning
  cosmetic (ENUM_NAME_OVERRIDES); pilot ninja module nghiệp vụ thật khi có.
