# Users Page Pagination + Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trang Người dùng phân trang server-side (`limit/offset`, page size 20) và tìm kiếm theo `username`/`display_name` qua `GET /api/users/admin`.

**Architecture:** BE đổi response `GET /api/users/admin` từ mảng sang `{items, total}` + nhận `q/limit/offset` (pattern blocklist/HR). FE chuyển query từ shell (`App.tsx`) về trang `UserManagement` (React Query per-feature, `keepPreviousData`), thêm ô tìm kiếm debounce 300ms + footer phân trang. Regen OpenAPI contract.

**Tech Stack:** Django REST Framework + drf-spectacular, React Query v5, shadcn/ui, openapi-typescript.

**Spec:** `docs/superpowers/specs/2026-07-07-users-page-pagination-search-design.md`

## Global Constraints

- Branch: `feat/users-page-pagination-search` (worktree `.claude/worktrees/users-page-pagination-search`) — KHÔNG tự tạo branch khác, commit thẳng trên branch này.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm footer `Co-Authored-By`.
- Backend test chạy bằng docker image `desi-order-web-test` (image prod thiếu pytest). Lệnh mẫu (network + postgres/redis từ compose chính; nếu chưa chạy: `docker compose -f docker-compose.local.yml up -d postgres redis` ở checkout chính `C:\Users\MYPC\Desktop\workspace\desi-order`):

  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
    -e POSTGRES_DB=desi_userspag \
    -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
    -w /app desi-order-web-test:latest pytest <path> -q
  ```

- FE baseline test có 3 fail sẵn trên master sạch (OrderFormDialog + 2 mobile-viewport) — KHÔNG phải regression, đừng sửa.
- CẤM `react-select` cho code mới; dùng `components/ui`.
- FE: GET data qua React Query + `queries.ts` per-feature; API module typed từ `src/api/schema.d.ts`.

---

### Task 1: Backend — `GET /api/users/admin` nhận `q`/`limit`/`offset`, trả `{items, total}`

**Files:**
- Create: `backend/apps/users/tests/test_admin_users_pagination.py`
- Modify: `backend/apps/users/services.py:16-38` (method `admin_list_users`)
- Modify: `backend/apps/users/serializers.py` (thêm `AdminUserListResponseSerializer` ngay sau `AdminUserUpdateSerializer`, ~dòng 220)
- Modify: `backend/apps/users/views.py:338-369` (`admin_users_collection` + `extend_schema_view`)

**Interfaces:**
- Consumes: `UserSerializer`, `UserService`, `require_section("users")` — đã có sẵn.
- Produces: `UserService.admin_list_users(q: str | None = None)` trả queryset đã lọc + `order_by("id")`; response GET `{"items": [...UserSerializer...], "total": int}`; OpenAPI component `AdminUserListResponse` (Task 2 regen, Task 3 FE dùng).

- [ ] **Step 1: Viết test fail**

Tạo `backend/apps/users/tests/test_admin_users_pagination.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User
from apps.users.rbac_catalog import sync_permissions
from apps.users.test_helpers import make_admin, make_user_with_permissions


class AdminUsersPaginationTests(TestCase):
    """GET /api/users/admin: phân trang limit/offset + lọc q (username/display_name)."""

    @classmethod
    def setUpTestData(cls):
        sync_permissions()
        cls.admin = make_admin("adm")
        User.objects.bulk_create(
            User(username=f"user{i:03d}", display_name=f"Nhân viên {i:03d}")
            for i in range(120)
        )
        User.objects.create(username="alice", display_name="Trần Thị B")
        User.objects.create(username="bob", display_name="Alice Nguyễn")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_response_shape_and_default_page_size(self):
        resp = self.client.get("/api/users/admin")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data.keys()), {"items", "total"})
        self.assertEqual(data["total"], 123)  # admin + 120 + alice + bob
        self.assertEqual(len(data["items"]), 20)

    def test_limit_offset_slice_ordered_by_id(self):
        resp = self.client.get("/api/users/admin", {"limit": 5, "offset": 10})
        data = resp.json()
        self.assertEqual(data["total"], 123)
        self.assertEqual(len(data["items"]), 5)
        ids = [row["id"] for row in data["items"]]
        self.assertEqual(ids, sorted(ids))
        all_ids = list(
            User.objects.filter(deleted_at__isnull=True)
            .order_by("id")
            .values_list("id", flat=True)
        )
        self.assertEqual(ids, all_ids[10:15])

    def test_q_matches_username_and_display_name_case_insensitive(self):
        resp = self.client.get("/api/users/admin", {"q": "ALICE"})
        data = resp.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(
            {row["username"] for row in data["items"]}, {"alice", "bob"}
        )

    def test_q_no_match_returns_empty(self):
        resp = self.client.get("/api/users/admin", {"q": "khong-ton-tai-xyz"})
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])

    def test_limit_clamped_to_100_and_garbage_params_fallback(self):
        resp = self.client.get("/api/users/admin", {"limit": 500})
        self.assertEqual(len(resp.json()["items"]), 100)
        resp = self.client.get("/api/users/admin", {"limit": "abc", "offset": "xyz"})
        data = resp.json()
        self.assertEqual(len(data["items"]), 20)
        self.assertEqual(data["items"][0]["username"], "adm")

    def test_non_admin_still_403(self):
        staff = make_user_with_permissions("staffx", ["users:view"])
        self.client.force_authenticate(user=staff)
        resp = self.client.get("/api/users/admin")
        self.assertEqual(resp.status_code, 403)
```

Lưu ý: nếu `make_user_with_permissions("staffx", ["users:view"])` fail vì catalog không có permission `users:view`, xem `backend/apps/users/rbac_catalog.py` để lấy code hợp lệ của section `users` (test chỉ cần user KHÔNG phải RBAC-superuser nhưng qua được `require_section("users")` — nếu không có code phù hợp, thay bằng user có section users qua role bất kỳ; giữ nguyên assert 403).

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e POSTGRES_DB=desi_userspag \
  -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
  -w /app desi-order-web-test:latest pytest apps/users/tests/test_admin_users_pagination.py -q
```

Expected: FAIL — response hiện là mảng JSON (`data.keys()` nổ vì list, `len(items)` sai).

- [ ] **Step 3: Sửa service**

Trong `backend/apps/users/services.py`, thay method `admin_list_users` (giữ nguyên docstring cũ, thêm phần q):

```python
    @staticmethod
    def admin_list_users(q: str | None = None):
        """Queryset danh sách user cho trang quản trị — prefetch đủ quan hệ mà
        UserSerializer cần để tránh N+1 (audit 2026-07 mục 3.5).

        `q`: lọc icontains trên username/display_name (trang Users tìm kiếm
        server-side). order_by("id") để phân trang limit/offset ổn định.
        """
        from django.db.models import Prefetch, Q

        from apps.users.models import User, UserKiotvietMapping

        queryset = (
            User.objects.filter(deleted_at__isnull=True)
            .select_related("department")
            .prefetch_related(
                "role_assignments",
                "department_links",
                Prefetch(
                    "kiotviet_mappings",
                    queryset=UserKiotvietMapping.objects.select_related(
                        "kiotviet_channel"
                    ),
                ),
            )
            .order_by("id")
        )
        if q:
            queryset = queryset.filter(
                Q(username__icontains=q) | Q(display_name__icontains=q)
            )
        return queryset
```

- [ ] **Step 4: Thêm response serializer**

Trong `backend/apps/users/serializers.py`, ngay sau class `AdminUserUpdateSerializer`:

```python
class AdminUserListResponseSerializer(serializers.Serializer):
    """Response GET /api/users/admin (phân trang) — chỉ dùng khai OpenAPI."""

    items = UserSerializer(many=True)
    total = serializers.IntegerField()
```

- [ ] **Step 5: Sửa view**

Trong `backend/apps/users/views.py`:

1. Thêm `OpenApiParameter` vào import từ `drf_spectacular.utils` (đang có `OpenApiResponse, extend_schema, extend_schema_view, inline_serializer`).
2. Thêm `AdminUserListResponseSerializer` vào import từ `.serializers`.
3. Thêm helper ngay trên `admin_users_collection`:

```python
def _parse_page_param(raw, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value
```

4. Sửa `extend_schema_view` của `admin_users_collection` (nhánh get):

```python
    get=extend_schema(
        operation_id="admin_users_list",
        parameters=[
            OpenApiParameter("q", str, description="Lọc icontains theo username/display_name"),
            OpenApiParameter("limit", int, description="Số dòng mỗi trang (mặc định 20, tối đa 100)"),
            OpenApiParameter("offset", int, description="Vị trí bắt đầu (mặc định 0)"),
        ],
        responses={
            200: AdminUserListResponseSerializer,
            403: OpenApiResponse(description="Không có quyền quản lý user"),
        },
    ),
```

5. Sửa nhánh GET trong `admin_users_collection`:

```python
    if request.method == "GET":
        q = (request.query_params.get("q") or "").strip()
        limit = _parse_page_param(request.query_params.get("limit"), default=20, minimum=1, maximum=100)
        offset = _parse_page_param(request.query_params.get("offset"), default=0, minimum=0)
        users = UserService.admin_list_users(q=q)
        total = users.count()
        items = users[offset : offset + limit]
        return Response(
            {"items": UserSerializer(items, many=True).data, "total": total}
        )
```

- [ ] **Step 6: Chạy test, xác nhận pass + không vỡ test users hiện có**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e POSTGRES_DB=desi_userspag \
  -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
  -w /app desi-order-web-test:latest pytest apps/users -q
```

Expected: PASS toàn bộ (`test_users_list_queries.py` gọi service trực tiếp không truyền `q` — vẫn chạy; nếu có test khác assert response mảng của `GET /api/users/admin`, cập nhật sang `{items, total}`).

- [ ] **Step 7: Ruff + commit**

```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
  -w /app desi-order-web-test:latest ruff check apps/users
git add backend/apps/users
git commit -m "feat: add pagination and search to admin users list API"
```

---

### Task 2: Regen OpenAPI contract (schema.yaml + schema.d.ts)

**Files:**
- Modify (regen): `backend/schema.yaml`
- Modify (regen): `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: BE Task 1 (`AdminUserListResponseSerializer`, `OpenApiParameter`).
- Produces: type `components["schemas"]["AdminUserListResponse"]` cho FE (Task 3).

- [ ] **Step 1: Regen schema.yaml trong docker**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
  -w /app desi-order-web-test:latest python manage.py spectacular --file schema.yaml
```

Expected: schema.yaml cập nhật; `grep -n "AdminUserListResponse" backend/schema.yaml` có kết quả.

- [ ] **Step 2: Regen schema.d.ts**

```bash
cd frontend && npm run gen:api
```

Expected: `grep -n "AdminUserListResponse" src/api/schema.d.ts` có kết quả.

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore: regen openapi contract for paginated users list"
```

---

### Task 3: Frontend — chuyển trang Users sang query phân trang + ô tìm kiếm

**Files:**
- Modify: `frontend/src/lib/api/users.ts:58-62` (`listAdminUsers`)
- Modify: `frontend/src/features/users/queries.ts` (hook + keys)
- Modify: `frontend/src/features/users/components/UserManagement.tsx` (tự fetch, state q/page, footer phân trang)
- Modify: `frontend/src/features/users/UsersTable.tsx` (ô tìm kiếm trong toolbar)
- Modify: `frontend/src/App.tsx:116,123,130,1079,1121` (gỡ adminUsers khỏi shell)
- Modify: `frontend/src/app/AppShellContext.tsx:5,76` (gỡ khỏi context type)
- Modify: `frontend/src/app/routed-pages.tsx:395-408` (`UsersRoute` bỏ prop `users`)
- Modify: `frontend/src/i18n/messages/users.ts` (key mới, cả `vi` và `en`)

**Interfaces:**
- Consumes: `components["schemas"]["AdminUserListResponse"]` (Task 2); hook `useDebounce(callback, delay)` từ `@/hooks/useDebounce`.
- Produces: `listAdminUsers(params: AdminUserListParams): Promise<AdminUserListResponse>`; hook `useAdminUsersQuery(params: AdminUsersQueryParams)`; `userKeys.adminUsers` GIỮ NGUYÊN `["users","admin"]` làm prefix invalidate.

- [ ] **Step 1: Sửa `lib/api/users.ts`**

Thay `listAdminUsers` (và thêm type, đặt cạnh các type payload đầu file):

```typescript
export type AdminUserListParams = { q?: string; limit: number; offset: number };

/**
 * Response GET /api/users/admin. `items` override sang AdminUserRow (superset
 * của schema User: giữ field legacy `role` FE đang dùng).
 */
export type AdminUserListResponse = Omit<
  components["schemas"]["AdminUserListResponse"],
  "items"
> & { items: AdminUserRow[] };

/** GET /api/users/admin — danh sách user phân trang + lọc q. */
export async function listAdminUsers(
  params: AdminUserListParams,
): Promise<AdminUserListResponse> {
  const search = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
  });
  if (params.q) search.set("q", params.q);
  const data = (await fetchJson(
    `/api/users/admin?${search.toString()}`,
  )) as AdminUserListResponse;
  return {
    items: Array.isArray(data?.items) ? data.items : [],
    total: typeof data?.total === "number" ? data.total : 0,
  };
}
```

- [ ] **Step 2: Sửa `features/users/queries.ts`**

Thay toàn bộ phần adminUsers (giữ departments nguyên):

```typescript
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  listAdminUsers,
  listDepartments,
  type AdminUserListParams,
} from "@/lib/api/users";

const SHELL_STALE_TIME_MS = 30_000;

export type AdminUsersQueryParams = Required<Pick<AdminUserListParams, "limit" | "offset">> &
  Pick<AdminUserListParams, "q">;

export const userKeys = {
  adminUsers: ["users", "admin"] as const,
  adminUsersList: (params: AdminUsersQueryParams) => ["users", "admin", "list", params] as const,
  departments: ["users", "departments"] as const,
};

/**
 * GET /api/users/admin — phân trang server-side. Key lồng dưới prefix
 * ["users","admin"] nên các invalidateQueries/removeQueries hiện có ở App.tsx
 * vẫn áp lên mọi trang/bộ lọc. keepPreviousData: giữ trang cũ khi tải trang mới.
 */
export function useAdminUsersQuery(params: AdminUsersQueryParams) {
  return useQuery({
    queryKey: userKeys.adminUsersList(params),
    queryFn: () => listAdminUsers(params),
    staleTime: SHELL_STALE_TIME_MS,
    placeholderData: keepPreviousData,
  });
}
```

(`useDepartmentsQuery` giữ nguyên.)

- [ ] **Step 3: Thêm i18n keys**

Trong `frontend/src/i18n/messages/users.ts`, thêm vào object `vi`:

```typescript
    "users.search.placeholder": "Tìm theo tên đăng nhập, tên hiển thị...",
    "users.pagination.info": "Trang {page}/{pages} · {total} người dùng",
    "users.pagination.prev": "Trước",
    "users.pagination.next": "Sau",
```

và vào object `en`:

```typescript
    "users.search.placeholder": "Search by username or display name...",
    "users.pagination.info": "Page {page}/{pages} · {total} users",
    "users.pagination.prev": "Previous",
    "users.pagination.next": "Next",
```

- [ ] **Step 4: Sửa `UsersTable.tsx` — ô tìm kiếm trong toolbar**

1. Import thêm: `Search` từ `lucide-react`, `Input` từ `@/components/ui/input`.
2. Thêm props:

```typescript
  searchValue: string;
  onSearchChange: (value: string) => void;
```

3. Trong `toolbar` (div `w-full flex flex-wrap ...`), đổi `justify-end` thành `justify-between` và thêm ô tìm kiếm TRƯỚC nhóm nút:

```tsx
        <div className="w-full flex flex-wrap items-center justify-between gap-2">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchValue}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder={t("users.search.placeholder")}
              className="h-8 pl-8"
            />
          </div>
          <div className="flex items-center gap-2">
            {/* 2 Button Xóa đã chọn / Tạo người dùng mới giữ nguyên, chuyển vào đây */}
          </div>
        </div>
```

- [ ] **Step 5: Sửa `UserManagement.tsx` — tự fetch + state tìm kiếm/trang**

1. Gỡ prop `users` khỏi `Props` và khỏi destructuring.
2. Gỡ dòng dead-code `useMemo(() => users.filter((u) => u.role === "admin").length, [users]);` (kết quả không dùng).
3. Import thêm:

```typescript
import { useAdminUsersQuery } from "@/features/users/queries";
import { useDebounce } from "@/hooks/useDebounce";
```

4. Đầu component thêm (trên các state hiện có):

```typescript
const PAGE_SIZE = 20;
const EMPTY_USERS: AdminUserRow[] = [];
```

(2 hằng đặt NGOÀI component, trên `type Props`.)

```typescript
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const applySearch = useDebounce((value: string) => {
    setQuery(value.trim());
    setPage(0);
  }, 300);
  const usersQuery = useAdminUsersQuery({
    q: query || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const users = usersQuery.data?.items ?? EMPTY_USERS;
  const total = usersQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
```

5. Truyền vào `<UsersTable>` thêm:

```tsx
          searchValue={searchInput}
          onSearchChange={(value) => {
            setSearchInput(value);
            applySearch(value);
          }}
```

6. Ngay SAU `<UsersTable ... />` thêm footer phân trang:

```tsx
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {t("users.pagination.info")
              .replace("{page}", String(page + 1))
              .replace("{pages}", String(totalPages))
              .replace("{total}", String(total))}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              ← {t("users.pagination.prev")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={page + 1 >= totalPages}
            >
              {t("users.pagination.next")} →
            </Button>
          </div>
        </div>
```

Ghi chú hành vi: `selectedUserIds` giữ id qua các trang (xóa hàng loạt xóa đủ id đã tick); checkbox "chọn tất cả" chỉ áp cho `selectableUsers` của trang hiện tại — đúng spec.

- [ ] **Step 6: Gỡ adminUsers khỏi shell**

1. `frontend/src/App.tsx`:
   - Xóa dòng `const adminUsersEnabled = isSuperuser && section === "users";` (dòng ~116).
   - Xóa `const adminUsersQuery = useAdminUsersQuery(adminUsersEnabled);` (~123) và `const adminUsers = adminUsersQuery.data ?? EMPTY_LIST;` (~130).
   - Xóa `adminUsers,` khỏi object context value (~1079) và khỏi dependency array (~1121).
   - Cập nhật comment dòng ~210 nếu liệt kê adminUsers.
   - GIỮ import `userKeys` + mọi `invalidateQueries`/`removeQueries({ queryKey: userKeys.adminUsers })` (prefix vẫn đúng). Nếu `useAdminUsersQuery` không còn được import ở App.tsx thì gỡ import.
2. `frontend/src/app/AppShellContext.tsx`: xóa dòng 76 `adminUsers: AdminUserRow[];` và import `AdminUserRow` (dòng 5, không còn chỗ dùng).
3. `frontend/src/app/routed-pages.tsx` (`UsersRoute`): xóa `users={shell.adminUsers}`.

- [ ] **Step 7: Typecheck + lint + test FE**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: typecheck/lint sạch (lint dùng --max-warnings=9999, chỉ cần không error mới); vitest chỉ còn 3 fail baseline đã biết (OrderFormDialog + 2 mobile-viewport). Nếu có test khác fail vì shape context → cập nhật mock theo context mới.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat: server-side pagination and search on users page"
```

---

### Task 4: Verify tổng, cập nhật tiến độ, push + MR

**Files:**
- Modify: `docs/superpowers/specs/2026-07-07-users-page-pagination-search-design.md` (note tiến độ cuối file)

**Interfaces:**
- Consumes: toàn bộ Task 1-3 đã commit.
- Produces: branch đã push + link MR.

- [ ] **Step 1: Chạy lại BE users suite + FE build**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e POSTGRES_DB=desi_userspag \
  -v "/c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/users-page-pagination-search/backend:/app" \
  -w /app desi-order-web-test:latest pytest apps/users -q
cd frontend && npm run build
```

Expected: BE pass; vite build OK.

- [ ] **Step 2: Note tiến độ vào spec**

Thêm cuối file spec:

```markdown
## Tiến độ

- 2026-07-07: Hoàn thành BE (q/limit/offset + {items,total}), regen contract,
  FE chuyển query về trang + ô tìm kiếm debounce + footer phân trang. Đã làm:
  toàn bộ spec. Chưa làm: không.
```

```bash
git add docs/superpowers/specs/2026-07-07-users-page-pagination-search-design.md
git commit -m "docs: note progress on users pagination spec"
```

- [ ] **Step 3: Kiểm tra conflict với master mới nhất**

```bash
git fetch origin master
git merge origin/master --no-edit
```

Expected: "Already up to date" hoặc merge sạch; nếu conflict → resolve, chạy lại Step 1 rồi commit.

- [ ] **Step 4: Push + tạo MR**

```bash
git push -u origin feat/users-page-pagination-search \
  -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat: users page pagination + search"
```

Expected: output chứa link MR — gửi link cho user.
