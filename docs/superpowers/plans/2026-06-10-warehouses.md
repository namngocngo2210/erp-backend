# Warehouse (Danh mục Kho) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm bảng danh mục kho (`warehouses`) với API CRUD và trang quản lý trên frontend, phân quyền theo section `warehouses`.

**Architecture:** App Django mới `apps.warehouses` (model + repository + transport serializers/views) theo chuẩn `docs/architecture-standard.md`; FE thêm API client + page + route/sidebar gate theo section. Soft-delete + timestamps theo convention chung. Spec: `docs/warehouse-design.md`.

**Tech Stack:** Django 5.2 + DRF, PostgreSQL, React 18 + TypeScript + Vite, shadcn/ui, vitest.

**Branch:** `feat/warehouses` (đã tạo).

**Cách chạy test backend** (container prod không có tests/ruff — dùng container ephemeral cùng image, mount source host, network `desi-order_default`):

```bash
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <paths> --reuse-db -q'
```

(thay `<paths>` bằng đường dẫn test; `mkdir /tmp/prom_multiproc` là BẮT BUỘC). Ruff chạy trên host: `uv run --no-project --with ruff ruff check <files>`.

**Lệnh manage.py** (makemigrations/migrate) chạy qua container ephemeral tương tự:

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  python manage.py makemigrations warehouses
```

**Test FE:** `cd frontend && npx vitest run <file>`.

**Baseline:** 2 test fail sẵn trên master (dashboard return-rate BE, OrderFormDialog FE) — không phải regression.

---

### Task 1: App `warehouses` — skeleton + model + migration

**Files:**
- Create: `backend/apps/warehouses/__init__.py` (rỗng)
- Create: `backend/apps/warehouses/apps.py`
- Create: `backend/apps/warehouses/models.py`
- Create: `backend/apps/warehouses/migrations/__init__.py` (rỗng)
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS, sau `"apps.dispatch",`)

- [ ] **Step 1: Tạo apps.py**

```python
from django.apps import AppConfig


class WarehousesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warehouses"
```

- [ ] **Step 2: Tạo models.py**

```python
from django.db import models


class Warehouse(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "warehouses"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["deleted_at"], name="warehouse_deleted_idx"),
            models.Index(fields=["is_active"], name="warehouse_active_idx"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"
```

- [ ] **Step 3: Thêm `"apps.warehouses",` vào cuối INSTALLED_APPS** trong `backend/config/settings_parts/base.py` (ngay sau dòng `"apps.dispatch",`)

- [ ] **Step 4: Tạo migration**

Run (sau khi đã tạo `/tmp/web.env` như hướng dẫn đầu file):
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  python manage.py makemigrations warehouses
```
Expected: tạo `backend/apps/warehouses/migrations/0001_initial.py` với model Warehouse + 2 index.

- [ ] **Step 5: Kiểm tra Django config hợp lệ**

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py check
```
Expected: `System check identified no issues`.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warehouses backend/config/settings_parts/base.py
git commit -m "feat(warehouses): add warehouse model and migration"
```

---

### Task 2: Section `warehouses` trong phân quyền backend

**Files:**
- Modify: `backend/apps/users/models.py:10-29` (VALID_SECTIONS)

- [ ] **Step 1: Thêm `"warehouses",` vào list `VALID_SECTIONS`** (sau `"product-categories",` — giữ nhóm liên quan kho/sản phẩm gần nhau)

- [ ] **Step 2: Commit**

```bash
git add backend/apps/users/models.py
git commit -m "feat(users): add warehouses section"
```

(Quyền sẽ được test gián tiếp qua API test ở Task 3: staff không có section → 403, staff có section → 200.)

---

### Task 3: API CRUD `/api/warehouses` (TDD)

**Files:**
- Create: `backend/apps/warehouses/tests/__init__.py` (rỗng)
- Create: `backend/apps/warehouses/tests/test_warehouse_api.py`
- Create: `backend/apps/warehouses/repositories.py`
- Create: `backend/apps/warehouses/transport/__init__.py` (rỗng)
- Create: `backend/apps/warehouses/transport/serializers/__init__.py` (rỗng)
- Create: `backend/apps/warehouses/transport/serializers/warehouse_serializers.py`
- Create: `backend/apps/warehouses/transport/views/__init__.py` (rỗng)
- Create: `backend/apps/warehouses/transport/views/warehouse_views.py`
- Create: `backend/apps/warehouses/urls.py`
- Modify: `backend/config/urls.py` (thêm include)

- [ ] **Step 1: Viết test fail trước** — `backend/apps/warehouses/tests/test_warehouse_api.py`

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Department, User, UserRole
from apps.warehouses.models import Warehouse


class WarehouseApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-wh", password="x", role=UserRole.ADMIN)
        self.client.force_authenticate(user=self.admin)

    def test_requires_auth(self):
        anon = APIClient()
        self.assertEqual(anon.get("/api/warehouses").status_code, 401)

    def test_staff_without_section_forbidden(self):
        dept = Department.objects.create(name="Sale", allowed_sections=["orders"])
        staff = User.objects.create_user(
            username="staff-wh", password="x", role=UserRole.STAFF, department=dept
        )
        client = APIClient()
        client.force_authenticate(user=staff)
        self.assertEqual(client.get("/api/warehouses").status_code, 403)

    def test_staff_with_section_allowed(self):
        dept = Department.objects.create(name="Kho", allowed_sections=["warehouses"])
        staff = User.objects.create_user(
            username="staff-wh2", password="x", role=UserRole.STAFF, department=dept
        )
        client = APIClient()
        client.force_authenticate(user=staff)
        self.assertEqual(client.get("/api/warehouses").status_code, 200)

    def test_create_and_list(self):
        res = self.client.post(
            "/api/warehouses", {"code": "HN01", "name": "Kho Hà Nội"}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["code"], "HN01")
        self.assertTrue(res.data["is_active"])

        lst = self.client.get("/api/warehouses")
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(lst.data["total"], 1)
        self.assertEqual(lst.data["items"][0]["name"], "Kho Hà Nội")

    def test_create_strips_and_rejects_blank(self):
        res = self.client.post("/api/warehouses", {"code": "  ", "name": "X"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/warehouses", {"code": " HCM01 ", "name": " Kho HCM "}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["code"], "HCM01")
        self.assertEqual(res.data["name"], "Kho HCM")

    def test_duplicate_code_or_name_rejected(self):
        Warehouse.objects.create(code="HN01", name="Kho Hà Nội")
        dup_code = self.client.post(
            "/api/warehouses", {"code": "HN01", "name": "Kho khác"}, format="json"
        )
        self.assertEqual(dup_code.status_code, 400)
        dup_name = self.client.post(
            "/api/warehouses", {"code": "XX01", "name": "Kho Hà Nội"}, format="json"
        )
        self.assertEqual(dup_name.status_code, 400)

    def test_search_and_filter(self):
        Warehouse.objects.create(code="HN01", name="Kho Hà Nội")
        Warehouse.objects.create(code="HCM01", name="Kho Hồ Chí Minh", is_active=False)

        by_search = self.client.get("/api/warehouses?search=Nội")
        self.assertEqual([w["code"] for w in by_search.data["items"]], ["HN01"])

        by_code = self.client.get("/api/warehouses?search=HCM")
        self.assertEqual([w["code"] for w in by_code.data["items"]], ["HCM01"])

        active_only = self.client.get("/api/warehouses?is_active=true")
        self.assertEqual([w["code"] for w in active_only.data["items"]], ["HN01"])

    def test_pagination(self):
        for i in range(3):
            Warehouse.objects.create(code=f"K{i:02d}", name=f"Kho {i:02d}")
        page = self.client.get("/api/warehouses?limit=2&offset=2")
        self.assertEqual(page.data["total"], 3)
        self.assertEqual(len(page.data["items"]), 1)

    def test_invalid_pagination_rejected(self):
        self.assertEqual(self.client.get("/api/warehouses?limit=0").status_code, 400)
        self.assertEqual(self.client.get("/api/warehouses?limit=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/warehouses?limit=201").status_code, 400)
        self.assertEqual(self.client.get("/api/warehouses?offset=-1").status_code, 400)

    def test_update(self):
        wh = Warehouse.objects.create(code="HN01", name="Kho Hà Nội")
        res = self.client.patch(
            f"/api/warehouses/{wh.id}", {"name": "Kho HN mới", "is_active": False}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["name"], "Kho HN mới")
        self.assertFalse(res.data["is_active"])

    def test_update_not_found(self):
        self.assertEqual(
            self.client.patch("/api/warehouses/9999", {"name": "X"}, format="json").status_code,
            404,
        )

    def test_soft_delete(self):
        wh = Warehouse.objects.create(code="HN01", name="Kho Hà Nội")
        res = self.client.delete(f"/api/warehouses/{wh.id}")
        self.assertEqual(res.status_code, 204)
        wh.refresh_from_db()
        self.assertIsNotNone(wh.deleted_at)
        self.assertFalse(wh.is_active)
        lst = self.client.get("/api/warehouses")
        self.assertEqual(lst.data["total"], 0)
        # Xóa lần 2 → 404 (đã soft-delete thì coi như không tồn tại)
        self.assertEqual(self.client.delete(f"/api/warehouses/{wh.id}").status_code, 404)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run (lệnh docker đầu file, `<paths>` = `apps/warehouses/tests/test_warehouse_api.py`).
Expected: FAIL/ERROR (404 vì chưa có route, import error vì chưa có module).

- [ ] **Step 3: Tạo repositories.py**

```python
from apps.common.repository import BaseRepository
from apps.warehouses.models import Warehouse


class WarehouseRepository(BaseRepository[Warehouse]):
    model = Warehouse
```

- [ ] **Step 4: Tạo serializer** — `transport/serializers/warehouse_serializers.py`

```python
from rest_framework import serializers

from apps.warehouses.models import Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ("id", "code", "name", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_code(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Mã kho không được để trống.")
        return value

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tên kho không được để trống.")
        return value
```

(Unique `code`/`name` do ModelSerializer tự sinh UniqueValidator từ ràng buộc DB → 400.)

- [ ] **Step 5: Tạo views** — `transport/views/warehouse_views.py`

```python
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section
from apps.warehouses.repositories import WarehouseRepository
from apps.warehouses.transport.serializers.warehouse_serializers import WarehouseSerializer

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _parse_pagination(request):
    """Trả ({limit, offset}, None) hoặc (None, Response 400)."""
    try:
        limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        return None, Response(
            {"detail": "limit/offset không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST
        )
    if limit < 1 or limit > MAX_LIMIT or offset < 0:
        return None, Response(
            {"detail": "limit/offset không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST
        )
    return {"limit": limit, "offset": offset}, None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section("warehouses")])
@never_cache
def list_create_warehouses(request):
    if request.method == "GET":
        pagination, error_response = _parse_pagination(request)
        if error_response is not None:
            return error_response

        queryset = WarehouseRepository.active()
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(code__icontains=search))
        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            queryset = queryset.filter(is_active=is_active == "true")

        total = queryset.count()
        items = WarehouseRepository.paginate(
            queryset.order_by("name", "id"),
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
        return Response(
            {
                "items": WarehouseSerializer(items, many=True).data,
                "total": total,
                "limit": pagination["limit"],
                "offset": pagination["offset"],
            },
            status=status.HTTP_200_OK,
        )

    serializer = WarehouseSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    return Response(WarehouseSerializer(instance).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_section("warehouses")])
@never_cache
def update_delete_warehouse(request, warehouse_id: int):
    warehouse = WarehouseRepository.get_or_none(warehouse_id)
    if not warehouse:
        return Response({"detail": "Không tìm thấy kho."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        warehouse.deleted_at = timezone.now()
        warehouse.is_active = False
        warehouse.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = WarehouseSerializer(instance=warehouse, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    return Response(WarehouseSerializer(instance).data, status=status.HTTP_200_OK)
```

- [ ] **Step 6: Tạo urls.py của app**

```python
from django.urls import path

from apps.warehouses.transport.views.warehouse_views import (
    list_create_warehouses,
    update_delete_warehouse,
)

urlpatterns = [
    path("", list_create_warehouses, name="warehouses-list-create"),
    path("<int:warehouse_id>", update_delete_warehouse, name="warehouses-update-delete"),
]
```

- [ ] **Step 7: Include vào root urls** — `backend/config/urls.py`, thêm dòng sau `apps.warranty`:

```python
    re_path(r"^api/warehouses/?", include("apps.warehouses.urls")),
```

LƯU Ý: phải đặt TRƯỚC dòng `re_path(r"^api/", include("apps.audit.urls"))` (dòng catch-all cuối).

- [ ] **Step 8: Chạy test, xác nhận PASS**

Run lệnh docker với `<paths>` = `apps/warehouses/tests/test_warehouse_api.py`.
Expected: tất cả PASS (13 tests).

- [ ] **Step 9: Ruff**

```bash
uv run --no-project --with ruff ruff check backend/apps/warehouses
```
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add backend/apps/warehouses backend/config/urls.py
git commit -m "feat(warehouses): add CRUD API with section permission"
```

---

### Task 4: Django admin

**Files:**
- Create: `backend/apps/warehouses/admin.py`

- [ ] **Step 1: Tạo admin.py**

```python
from django.contrib import admin

from apps.warehouses.models import Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "deleted_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
```

- [ ] **Step 2: Kiểm tra**

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py check
```
Expected: no issues.

- [ ] **Step 3: Commit**

```bash
git add backend/apps/warehouses/admin.py
git commit -m "feat(warehouses): register warehouse admin"
```

---

### Task 5: FE API client

**Files:**
- Create: `frontend/src/lib/api/warehouses.ts`

- [ ] **Step 1: Tạo API client** (theo pattern `product-groups.ts`)

```ts
import { fetchJson } from "@/services/http";

const API_BASE = "/api/warehouses";

export interface Warehouse {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface WarehouseListResponse {
  items: Warehouse[];
  total: number;
  limit: number;
  offset: number;
}

export interface WarehouseUpsert {
  code?: string;
  name?: string;
  is_active?: boolean;
}

export async function getWarehouses(
  params: { search?: string; isActive?: boolean; limit?: number; offset?: number } = {},
): Promise<WarehouseListResponse> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.search?.trim()) query.set("search", params.search.trim());
  if (params.isActive !== undefined) query.set("is_active", String(params.isActive));
  return fetchJson(`${API_BASE}?${query.toString()}`);
}

export async function createWarehouse(data: WarehouseUpsert): Promise<Warehouse> {
  return fetchJson(API_BASE, { method: "POST", body: JSON.stringify(data) });
}

export async function updateWarehouse(id: number, data: WarehouseUpsert): Promise<Warehouse> {
  return fetchJson(`${API_BASE}/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteWarehouse(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/api/warehouses.ts
git commit -m "feat(fe): add warehouses api client"
```

---

### Task 6: FE trang quản lý kho (TDD)

**Files:**
- Create: `frontend/src/pages/__tests__/WarehousesPage.test.tsx`
- Create: `frontend/src/pages/WarehousesPage.tsx`

- [ ] **Step 1: Viết test fail trước** — `frontend/src/pages/__tests__/WarehousesPage.test.tsx`

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, screen, waitFor, fireEvent } from "@testing-library/react";

import { renderWithI18n } from "@/test/render-with-i18n";
import type { Warehouse } from "@/lib/api/warehouses";

vi.mock("@/lib/api/warehouses", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/warehouses")>("@/lib/api/warehouses");
  return {
    ...actual,
    getWarehouses: vi.fn(),
    createWarehouse: vi.fn(),
    updateWarehouse: vi.fn(),
    deleteWarehouse: vi.fn(),
  };
});

import { createWarehouse, getWarehouses } from "@/lib/api/warehouses";
import { WarehousesPage } from "@/pages/WarehousesPage";

const makeWarehouse = (overrides: Partial<Warehouse> = {}): Warehouse => ({
  id: 1,
  code: "HN01",
  name: "Kho Hà Nội",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

describe("WarehousesPage", () => {
  beforeEach(() => {
    vi.mocked(getWarehouses).mockResolvedValue({
      items: [makeWarehouse(), makeWarehouse({ id: 2, code: "HCM01", name: "Kho HCM", is_active: false })],
      total: 2,
      limit: 50,
      offset: 0,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders warehouse list from API", async () => {
    renderWithI18n(<WarehousesPage />);
    await waitFor(() => {
      expect(screen.getByText("Kho Hà Nội")).toBeTruthy();
      expect(screen.getByText("Kho HCM")).toBeTruthy();
    });
    expect(getWarehouses).toHaveBeenCalled();
  });

  it("creates a warehouse via dialog", async () => {
    vi.mocked(createWarehouse).mockResolvedValue(makeWarehouse({ id: 3, code: "DN01", name: "Kho Đà Nẵng" }));
    renderWithI18n(<WarehousesPage />);
    await waitFor(() => expect(screen.getByText("Kho Hà Nội")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /thêm kho/i }));
    fireEvent.change(screen.getByLabelText(/mã kho/i), { target: { value: "DN01" } });
    fireEvent.change(screen.getByLabelText(/tên kho/i), { target: { value: "Kho Đà Nẵng" } });
    fireEvent.click(screen.getByRole("button", { name: /^lưu$/i }));

    await waitFor(() =>
      expect(createWarehouse).toHaveBeenCalledWith({ code: "DN01", name: "Kho Đà Nẵng", is_active: true }),
    );
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd frontend && npx vitest run src/pages/__tests__/WarehousesPage.test.tsx
```
Expected: FAIL (module `@/pages/WarehousesPage` chưa tồn tại).

- [ ] **Step 3: Tạo trang** — `frontend/src/pages/WarehousesPage.tsx`

Theo pattern `ProductCategoriesPage.tsx` (toast sonner, DataTableShell, Dialog form). Code đầy đủ:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Edit, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTableShell } from "@/components/ui/data-table-shell";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Warehouse,
  createWarehouse,
  deleteWarehouse,
  getWarehouses,
  updateWarehouse,
} from "@/lib/api/warehouses";

const PAGE_SIZE = 50;

type WarehouseForm = { code: string; name: string; is_active: boolean };
type StatusFilter = "all" | "active" | "inactive";

const EMPTY_FORM: WarehouseForm = { code: "", name: "", is_active: true };

export function WarehousesPage() {
  const [items, setItems] = useState<Warehouse[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [loading, setLoading] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Warehouse | null>(null);
  const [form, setForm] = useState<WarehouseForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<Warehouse | null>(null);

  const load = useCallback(
    async (opts: { search?: string; status?: StatusFilter; offset?: number } = {}) => {
      const status = opts.status ?? "all";
      setLoading(true);
      try {
        const res = await getWarehouses({
          search: opts.search,
          isActive: status === "all" ? undefined : status === "active",
          limit: PAGE_SIZE,
          offset: opts.offset ?? 0,
        });
        setItems(res.items);
        setTotal(res.total);
        setOffset(res.offset);
      } catch {
        toast.error("Không tải được danh sách kho.");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const timer = window.setTimeout(
      () => void load({ search, status: statusFilter, offset: 0 }),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [search, statusFilter, load]);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setDialogOpen(true);
  };

  const openEdit = (warehouse: Warehouse) => {
    setEditing(warehouse);
    setForm({ code: warehouse.code, name: warehouse.name, is_active: warehouse.is_active });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    const code = form.code.trim();
    const name = form.name.trim();
    if (!code || !name) {
      toast.error("Vui lòng nhập mã kho và tên kho.");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await updateWarehouse(editing.id, { code, name, is_active: form.is_active });
        toast.success("Đã cập nhật kho.");
      } else {
        await createWarehouse({ code, name, is_active: form.is_active });
        toast.success("Đã tạo kho mới.");
      }
      setDialogOpen(false);
      void load({ search, status: statusFilter, offset });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Lưu kho thất bại.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    try {
      await deleteWarehouse(deleting.id);
      toast.success("Đã xóa kho.");
      setDeleting(null);
      void load({ search, status: statusFilter, offset });
    } catch {
      toast.error("Xóa kho thất bại.");
    }
  };

  return (
    <DataTableShell
      title="Quản lý kho"
      toolbar={
        <div className="flex w-full items-center gap-2">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input
              placeholder="Tìm theo tên hoặc mã kho..."
              className="pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Trạng thái" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Tất cả trạng thái</SelectItem>
              <SelectItem value="active">Hoạt động</SelectItem>
              <SelectItem value="inactive">Ngừng hoạt động</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={openCreate}>
            <Plus className="size-4" />
            Thêm kho
          </Button>
        </div>
      }
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Mã kho</TableHead>
            <TableHead>Tên kho</TableHead>
            <TableHead>Trạng thái</TableHead>
            <TableHead className="w-24 text-right">Thao tác</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((warehouse) => (
            <TableRow key={warehouse.id}>
              <TableCell className="font-medium">{warehouse.code}</TableCell>
              <TableCell>{warehouse.name}</TableCell>
              <TableCell>
                {warehouse.is_active ? (
                  <Badge variant="default">Hoạt động</Badge>
                ) : (
                  <Badge variant="secondary">Ngừng hoạt động</Badge>
                )}
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="icon" onClick={() => openEdit(warehouse)}>
                  <Edit className="size-4" />
                </Button>
                <Button variant="ghost" size="icon" onClick={() => setDeleting(warehouse)}>
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {!loading && items.length === 0 && (
            <TableRow>
              <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">
                Chưa có kho nào.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 p-2">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => void load({ search, status: statusFilter, offset: Math.max(0, offset - PAGE_SIZE) })}
          >
            Trang trước
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => void load({ search, status: statusFilter, offset: offset + PAGE_SIZE })}
          >
            Trang sau
          </Button>
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Sửa kho" : "Thêm kho"}</DialogTitle>
            <DialogDescription>
              {editing ? "Cập nhật thông tin kho." : "Tạo kho mới trong hệ thống."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label htmlFor="warehouse-code" className="text-sm font-medium">
                Mã kho
              </label>
              <Input
                id="warehouse-code"
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                placeholder="VD: HN01"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="warehouse-name" className="text-sm font-medium">
                Tên kho
              </label>
              <Input
                id="warehouse-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="VD: Kho Hà Nội"
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                id="warehouse-active"
                checked={form.is_active}
                onCheckedChange={(checked) => setForm({ ...form, is_active: checked })}
              />
              <label htmlFor="warehouse-active" className="text-sm">
                Đang hoạt động
              </label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Hủy
            </Button>
            <Button onClick={() => void handleSave()} disabled={saving}>
              Lưu
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Xóa kho</DialogTitle>
            <DialogDescription>
              Bạn có chắc muốn xóa kho "{deleting?.name}"? Kho sẽ bị ẩn khỏi danh sách.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Hủy
            </Button>
            <Button variant="destructive" onClick={() => void handleDelete()}>
              Xóa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DataTableShell>
  );
}

export default WarehousesPage;
```

LƯU Ý cho người thực hiện: trước khi dùng, mở `frontend/src/components/ui/data-table-shell.tsx` xem props thực tế của `DataTableShell` (title/toolbar có thể tên khác) và chỉnh cho khớp; nếu component không phù hợp thì dựng layout bằng `Card` như các page khác. Tương tự kiểm tra `Badge` variant có `secondary` không. Nguyên tắc: bám pattern `ProductCategoriesPage.tsx`.

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd frontend && npx vitest run src/pages/__tests__/WarehousesPage.test.tsx
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/WarehousesPage.tsx frontend/src/pages/__tests__/WarehousesPage.test.tsx
git commit -m "feat(fe): add warehouses management page"
```

---

### Task 7: FE route + sidebar + i18n + phân quyền phòng ban

**Files:**
- Modify: `frontend/src/lib/routes.ts` (AppSection + SECTION_TO_PATH)
- Modify: `frontend/src/app/routed-pages.tsx` (lazy import + route component)
- Modify: `frontend/src/app/routes.tsx` (route + Guard)
- Modify: `frontend/src/components/Sidebar.tsx` (item trong group `warehouse` sẵn có)
- Modify: `frontend/src/i18n/messages/topnav.ts` (label vi/en)
- Modify: `frontend/src/features/users/DepartmentManager.tsx` (danh sách section cấp cho phòng ban)

- [ ] **Step 1: `lib/routes.ts`** — thêm `| "warehouses"` vào type `AppSection` (sau `"product-categories"`), và vào `SECTION_TO_PATH`:

```ts
  warehouses: "/warehouses",
```

- [ ] **Step 2: `app/routed-pages.tsx`** — thêm lazy import cạnh các page khác:

```tsx
const WarehousesPage = lazy(() => import("@/pages/WarehousesPage"));
```

và route component (cạnh `ProductCategoriesRoute`):

```tsx
export function WarehousesRoute() {
  return <WarehousesPage />;
}
```

- [ ] **Step 3: `app/routes.tsx`** — import `WarehousesRoute` và thêm route sau block `product-categories`:

```tsx
      {
        path: "warehouses",
        element: (
          <Guard section="warehouses">
            <WarehousesRoute />
          </Guard>
        ),
      },
```

- [ ] **Step 4: `components/Sidebar.tsx`** — group `warehouse` đã tồn tại (key `warehouse`, icon Package); thêm item sau `product-categories`:

```tsx
      { key: "warehouses", labelKey: "topnav.tabs.warehouses", icon: Warehouse },
```

và thêm `Warehouse` vào import lucide-react ở đầu file.

- [ ] **Step 5: `i18n/messages/topnav.ts`** — thêm vào cả 2 khối:

```ts
    // vi
    "topnav.tabs.warehouses": "Kho hàng",
    // en
    "topnav.tabs.warehouses": "Warehouses",
```

- [ ] **Step 6: `features/users/DepartmentManager.tsx`** — thêm vào danh sách section (sau dòng `{ key: "product-categories", label: "Danh mục sản phẩm" }`):

```tsx
      { key: "warehouses", label: "Kho hàng" },
```

- [ ] **Step 7: Typecheck + build**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: không lỗi.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/routes.ts frontend/src/app/routed-pages.tsx frontend/src/app/routes.tsx \
  frontend/src/components/Sidebar.tsx frontend/src/i18n/messages/topnav.ts \
  frontend/src/features/users/DepartmentManager.tsx
git commit -m "feat(fe): wire warehouses route, sidebar and section permission"
```

---

### Task 8: Chạy toàn bộ test, migrate, push, MR

- [ ] **Step 1: Full backend test**

Run lệnh docker với `<paths>` = `apps/` (toàn bộ).
Expected: chỉ 1 fail baseline (dashboard return-rate) — không có fail mới.

- [ ] **Step 2: Full FE test**

```bash
cd frontend && npm run test:ci
```
Expected: chỉ fail baseline đã loại trừ (OrderFormDialog đã exclude sẵn trong test:ci) — không có fail mới.

- [ ] **Step 3: Chạy migration trên DB thật**

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py migrate warehouses
```
Expected: `Applying warehouses.0001_initial... OK`.

LƯU Ý: bảng mới rỗng, migration chỉ CREATE TABLE — an toàn, không cần backup. Code prod chỉ dùng bảng sau khi merge + deploy.

- [ ] **Step 4: Cập nhật tiến độ trong `docs/warehouse-design.md`** — tick các mục sprint đã xong, ghi note "đã làm/chưa làm" sau mỗi sprint.

- [ ] **Step 5: Push + tạo MR**

```bash
git push -u origin feat/warehouses
```
Tạo MR `feat/warehouses` → `master`, gửi link cho user.

---

## Self-review notes

- Spec coverage: model/migration (T1), section (T2), API CRUD + test (T3), admin (T4), FE client (T5), FE page + test (T6), route/sidebar/i18n/gate (T7), migrate + MR (T8) — đủ các mục trong `docs/warehouse-design.md`.
- `is_active` filter từ FE chưa có UI filter riêng (spec ghi "filter trạng thái") — API đã hỗ trợ `is_active`; UI chỉ hiển thị badge. Nếu cần filter UI, thêm Select nhỏ ở toolbar sau — không chặn MR.
- DataTableShell props cần xác minh khi thực hiện (đã ghi chú trong Task 6).
