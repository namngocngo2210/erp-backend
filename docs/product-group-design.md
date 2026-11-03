# Thiết kế: Nhóm SKU sản phẩm (Product Group / Variant)

> Trạng thái: **DRAFT — chờ review** · Branch: `feat/product-group-skus` · Ngày: 2026-06-10

## 1. Bối cảnh & Vấn đề

Mỗi sản phẩm gốc hiện được bán dưới nhiều **biến thể** với SKU theo quy ước `<MÃ_GỐC>-<HẬU_TỐ>`:

| SKU | Mã gốc | Biến thể |
|-----|--------|----------|
| `MMK01-THUONG` | `MMK01` | Thường |
| `MMK01-CAOCAP` | `MMK01` | Cao cấp |
| `MSC02-CAOCAP` | `MSC02` | Cao cấp |
| `TMS01-PROMAX` | `TMS01` | Pro Max |

Các biến thể khác nhau về **giá / khối lượng / kích thước**, nên mỗi biến thể bắt buộc là một row `Product` riêng (`sku` đang `unique`). Tuy nhiên hệ thống **chưa có khái niệm "nhóm sản phẩm gốc"**:

- Không gom được biến thể về sản phẩm gốc `MMK01` để quản trị danh mục.
- Khi tạo đơn/hiển thị catalog, không gom các biến thể dưới một sản phẩm để người dùng chọn Thường/Cao cấp.

`ProductCategory` (M2M) là **danh mục ngành hàng** (massage, đệm ghế…), khác hoàn toàn khái niệm nhóm biến thể. `ProductPackage` chỉ xử lý gói số lượng. → Cần bổ sung tầng **`ProductGroup`**.

### Mục tiêu (đã chốt với stakeholder)
- **Quản trị danh mục**: tổ chức lại sản phẩm, gắn tên/ảnh cấp nhóm.
- **Hiển thị catalog / bán hàng**: gom biến thể dưới 1 sản phẩm khi tạo đơn, cho chọn biến thể.
- Quy ước SKU **luôn nhất quán** → cho phép auto-backfill nhóm từ prefix.
- **Làm sạch dữ liệu kèm theo**: soft-delete các SKU rác mang marker `{DEL}` / `.CŨ` (xem §2.1).

### Ngoài phạm vi (lần này)
- Không đổi `Order.sku`, `OrderItem` (vẫn tham chiếu SKU biến thể cụ thể).
- Không gộp giá/kho cấp nhóm (báo cáo theo nhóm có thể làm sau, đã có sẵn `group_id`).

### Kiểm chứng trên data thật (2026-06-10, 2.546 sản phẩm active)
- `split_part(sku, '-', 1)` làm mã nhóm — **đã verify đúng** trên mọi nhóm lớn (`MDD01` 123 biến thể, `MTC06` 72, `BMD01` 63…).
- **Hậu tố KHÔNG phải mô hình 2 bậc** Thường/Cao cấp như ví dụ ban đầu — thực tế là tổ hợp **màu/kích thước/chất liệu** (`MDD01-COMBODENTRANG`, `MTC06-ANHDAOROSEGOLDDAI`, `NCD01-XANH`…). → UI chọn biến thể lấy nhãn từ **toàn bộ hậu tố sau prefix**, không hardcode 2 lựa chọn.
- Sau khi soft-delete 517 SKU rác: còn **342 nhóm / 1.822 sản phẩm** được gán nhóm, **207 sản phẩm** giữ `group = NULL`.

## 2. Giải pháp đã chọn

**Bảng `ProductGroup` riêng + `Product.group` (FK nullable) + auto-assign từ prefix SKU.**

So sánh nhanh các phương án đã cân nhắc:

| Phương án | Ưu | Nhược | Kết luận |
|-----------|-----|-------|----------|
| **A. Bảng `ProductGroup` riêng** | Sạch, lưu metadata cấp nhóm, báo cáo `GROUP BY group_id` không phụ thuộc tên SKU, dễ mở rộng | Thêm 1 bảng + migration backfill | ✅ **Chọn** |
| B. `Product.parent` self-FK | Không thêm bảng | Row "cha" là product giả, lẫn lộn group & product | ❌ |
| C. Suy ra prefix lúc query | Không đổi schema | Không lưu được tên/ảnh nhóm, phụ thuộc 100% chuỗi SKU | ❌ |

### 2.1. Làm sạch dữ liệu trước khi gom (đã chốt)
Soft-delete (set `deleted_at`) các SKU active mang marker "đã xoá/cũ", **chạy trước** bước gom nhóm trong cùng data migration:
- `sku` chứa `{DEL` (441 SKU) — vd `MDD01-DUADENTRANG-3{DEL}`.
- `sku` khớp `\.C[ŨU]` không phân biệt hoa thường (76 SKU) — vd `BXO02-CAOCAP.CŨ`, `BNC02.CU`.
- Tổng **517 SKU**. Reverse migration: chỉ phục hồi `deleted_at = NULL` cho đúng các SKU bị migration này đánh dấu (đánh dấu bằng mốc thời gian/flag để revert chính xác).

### Quy tắc gán nhóm (canonical)
Hàm `derive_group_code(sku) = sku.split("-", 1)[0]` (phần trước dấu `-` đầu tiên).

Để **tránh sinh group rác** cho sản phẩm chỉ có 1 biến thể:
- Nhóm chỉ được tạo & gán khi **prefix xuất hiện ở ≥ 2 SKU active**.
- Sản phẩm prefix duy nhất → `group = NULL` (UI hiển thị trực tiếp, không cần gom).
- Khi tạo product mới mà phát sinh biến thể thứ 2 cùng prefix → tự tạo group và gán cho **cả 2** row.
- Admin có thể override: gán/tách nhóm thủ công, sửa tên/ảnh nhóm.

## 3. Thay đổi Database

### 3.1. Bảng mới `product_groups`
```python
class ProductGroup(models.Model):
    code = models.CharField(max_length=64, unique=True)        # "MMK01" (prefix SKU)
    name = models.CharField(max_length=255)                    # tên hiển thị, mặc định = code
    image_url = models.URLField(max_length=1024, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_groups"
        indexes = [
            models.Index(fields=["is_active"], name="prodgroup_active_idx"),
            models.Index(fields=["deleted_at"], name="prodgroup_deleted_idx"),
            GinIndex(fields=["code"], name="prodgroup_code_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["name"], name="prodgroup_name_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
```

### 3.2. Sửa `Product`
```python
group = models.ForeignKey(
    "ProductGroup",
    on_delete=models.SET_NULL,   # xoá nhóm không xoá biến thể
    related_name="products",
    null=True, blank=True,
)
# + index:
models.Index(fields=["group"], name="product_group_idx")
```

### 3.3. Migrations (đánh số tiếp theo `0077`)
1. **`0078_productgroup_and_product_group_fk`** — schema: tạo `product_groups`, thêm `Product.group` + index. Idempotent, không touch data.
2. **`0079_cleanup_and_backfill_product_groups`** — data migration (RunPython, có `reverse`), chạy theo thứ tự:
   - **Bước A — soft-delete SKU rác (§2.1):** set `deleted_at = now()` cho 517 SKU khớp `{DEL` / `\.C[ŨU]` mà đang active. Đánh dấu để reverse được (vd lưu danh sách id, hoặc reverse theo cùng điều kiện marker + `deleted_at IS NOT NULL`).
   - **Bước B — gom nhóm:** trên tập SKU **còn active sau bước A**, gom theo `derive_group_code`. Với prefix có ≥ 2 SKU: `get_or_create` ProductGroup(code=prefix, name=prefix), set `group` cho các product đó (bulk_update theo batch).
   - Forward an toàn chạy lại nhiều lần (idempotent); reverse: gỡ FK + xoá group auto-tạo, phục hồi `deleted_at` các SKU bước A.

> Backfill chạy theo batch (`iterator()` + `bulk_update`) để không khoá bảng `products` lâu.

## 4. Backend (service / API)

### 4.1. Service auto-assign
`apps/orders/services/product_group.py`:
- `derive_group_code(sku) -> str`
- `ensure_group_for_product(product)` — gọi khi create/update SKU: tìm group theo prefix; nếu chưa có mà tồn tại product khác cùng prefix → tạo group + gán cả hai; tự gắn `product.group`.

Đặt ở tầng service (không nhét vào `Product.save()`) để tránh side-effect ẩn và dễ test.

### 4.2. API
| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET/POST | `/api/orders/product-groups` | list + tìm kiếm (`q` theo code/name) / tạo |
| GET/PUT/DELETE | `/api/orders/product-groups/<id>` | xem (kèm variants) / sửa / soft-delete |
| GET | `/api/orders/products` | thêm filter `group=<id>`, response kèm `group` (id, code, name) |
| GET | `/api/orders/catalog` *(mới, tuỳ chọn)* | list group + variants lồng nhau cho màn tạo đơn |

Serializer:
- `ProductGroupSerializer` (+ `variants` lồng read-only).
- `ProductSerializer` thêm field `group` (nested read, `group_id` để ghi).

## 5. Frontend

- Màn **Quản trị sản phẩm**: thêm cột/bộ lọc "Nhóm", trang quản lý nhóm (CRUD tên/ảnh), gán biến thể vào nhóm.
- Màn **Tạo đơn**: chọn sản phẩm gốc → nếu có nhiều biến thể, hiện dropdown Thường/Cao cấp; nếu 1 biến thể (group NULL) → chọn trực tiếp như cũ.
- Stack: NextJS App Router + shadcn/ui + lucide-react (light theme xanh da trời) theo chuẩn dự án.

## 6. Phân Sprint & Tiến độ

### Sprint 1 — Model, Service & Migration (Backend) ✅ HOÀN TẤT
- [x] **T1.1** Service `apps/orders/services/product_group.py`: `derive_group_code` + `ensure_group_for_product` (bọc `transaction.atomic`, không cướp sibling đã thuộc group khác). 13 unit test.
- [x] **T1.2** `ProductGroup` model + `Product.group` FK (`SET_NULL`) + index. Migration schema `0078`.
- [x] **T1.3** Data migration `0079`: bước A soft-delete SKU rác → bước B backfill nhóm (≥2 biến thể), batch 500 + reverse. Reverse: chỉ undo grouping (xoá toàn bộ group, set group=NULL), **giữ** cleanup (data-hygiene một chiều; rollback dữ liệu dùng backup). 5 migration test (idempotent, reverse).
- [x] **T1.4** Đã chạy migration trên **DB prod** (backup `desi_20260610_080632_pre_product_group.dump`, 206M). Verify khớp: **342 nhóm, 1.822 gán nhóm, 207 active-NULL, 517 soft-deleted, min 2 biến thể/nhóm**.
- **Note sau sprint:** Hậu tố thực tế là tổ hợp màu/size/chất liệu (không phải 2 bậc THUONG/CAOCAP) — ảnh hưởng UI Sprint 3. Service `ensure_group_for_product` **chưa wire** vào luồng save (để Sprint 2 T2.3). Phát hiện 1 test fail **có sẵn từ master** (`test_dashboard_summary_api::test_return_rate_by_product_groups_by_product`, `KeyError 'SKU-A'`) — không liên quan, cần xử lý riêng.

### Sprint 2 — API (Backend) ✅ HOÀN TẤT
- [x] **T2.1** `ProductGroupSerializer` (+ `variants`/`variant_count` lồng, annotate tránh N+1) + `group`/`group_id` trong `ProductSerializer`.
- [x] **T2.2** CRUD `GET/POST /api/orders/product-groups`, `PATCH/DELETE /api/orders/product-groups/<id>` (soft-delete), filter `?group=<id>` ở `/products` (chỉ match group active).
- [x] **T2.3** Hook `ensure_group_for_product` vào `ProductSerializer.create/update` — chỉ auto khi tạo mới hoặc `sku` đổi và không truyền `group_id` tường minh (override thủ công được tôn trọng, `group_id:null` xoá nhóm).
- [x] **T2.4** `GET /api/orders/catalog/products` → `{groups:[{id,code,name,variants}], ungrouped:[]}` (active-only) cho màn tạo đơn.
- **Fix sau review:** etag products-list fold `ProductGroup` count+Max(updated_at) (sửa tên nhóm không còn 304 cũ); read path coi nhóm soft-deleted là `null` (nhất quán 3 đường đọc). 19 API test.
- **Note sau sprint:** Frontend Sprint 3 dùng `GET /catalog/products` cho màn tạo đơn; nhãn biến thể = hậu tố sau prefix.

### Sprint 3 — Frontend ✅ HOÀN TẤT
- [x] **T3.1** Explore: FE thực tế là **React 18 + Vite + React Router + TS + shadcn/ui + React Query + react-select** (KHÔNG phải Next.js — bám codebase). Template gần nhất: `ProductCategoriesPage`.
- [x] **T3.2** ~~Trang `/product-groups` riêng~~ → **đổi hướng theo yêu cầu**: bỏ trang nhóm riêng (route/nav/page xoá), **gộp thẳng vào `ProductsPage`**: hiển thị dạng dòng nhóm mở rộng được (chevron, mặc định thu gọn), click nhóm → xổ danh sách SP con kèm **giá từng SP** + sửa/xoá từng SP; SP không nhóm là dòng lẻ. Nhóm auto theo prefix, không có UI sửa tên/ảnh. Gom + phân trang phía client (tải full active products theo batch 100 như `ProductCategoriesPage`).
- [x] **T3.3** `OrderFormDialog`: picker gom biến thể theo group (react-select grouped options, header `code — name`, nhóm "Khác" cuối), bold hậu tố biến thể; áp cho cả default + async; không đổi hành vi chọn/submit.
- [ ] **T3.4** E2E happy-path — *chưa làm* (FE chỉ có vitest unit; build/lint/tests pass, chưa viết E2E riêng).
- **Note sau sprint:** Build tsc sạch, lint sạch trên file mới. **2 test fail có sẵn từ master, không liên quan**: BE `test_dashboard_summary_api::test_return_rate_by_product_groups_by_product` (KeyError 'SKU-A'); FE `OrderFormDialog.test.tsx` (matcher KiotViet trùng nhiều element). Cả hai đã verify fail trên base.

## 7. Rủi ro & Lưu ý
- **Quy ước SKU**: nếu sau này có SKU phá vỡ quy ước, group sai → service cho phép override thủ công; cân nhắc validation cảnh báo khi tạo SKU không có prefix khớp.
- **Backfill trên bảng lớn**: chạy batch, theo dõi lock; nên chạy ngoài giờ cao điểm.
- **`OrderItem`/`Order.sku` không đổi** → không ảnh hưởng dữ liệu đơn hàng cũ, rollback chỉ là gỡ FK + bảng nhóm.
