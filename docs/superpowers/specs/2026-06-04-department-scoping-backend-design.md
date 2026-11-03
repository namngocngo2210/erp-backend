# Phân quyền page + scope danh sách theo phòng ban ở Backend

> Ngày: 2026-06-04 · Branch: `feat/department-scoping-backend`

## 1. Bối cảnh & vấn đề

Mỗi `User` (ngoài admin) thuộc **một** phòng ban (`User.department`, FK nullable).
Hiện tại backend trả **toàn bộ** dữ liệu rồi để **frontend** tự lọc theo phòng ban:

- **Chặn page chỉ ở FE**: `User.has_section_access()` tồn tại nhưng gần như không được
  enforce — chỉ 2 chỗ dùng (`dashboard`, `ads-report`). Mọi endpoint list còn lại chỉ
  `IsAuthenticated`. Việc ẩn/chặn page nằm hoàn toàn ở FE (`RequireAuth`, Sidebar). ⇒
  **Lỗ hổng**: gọi thẳng API (vd Booking gọi `/api/orders/list`) vẫn ra dữ liệu.
- **Lọc danh sách ở FE**:
  - Kênh bán hàng: `ChannelRepository.list_for_user` trả tất cả kênh cho non-ADS; FE lọc
    theo `Channel.departments` trong form đơn (`features/orders/channel-visibility.ts`).
  - Nhân viên: `staff_collection` trả **toàn bộ** staff; FE lọc theo trang
    (`features/orders/channel-staff.ts`: KOC→Booking, còn lại→Sales).

Mục tiêu: **đẩy việc lọc về backend**, dựa vào phòng ban của user đăng nhập để quyết định
page nào được vào và danh sách nào được thấy.

## 2. Yêu cầu nghiệp vụ (đã chốt)

| Phòng ban | Page được vào | Danh sách kênh | Danh sách nhân viên | Rows đơn |
|-----------|---------------|----------------|---------------------|----------|
| **Sales** | Đơn hàng (`show_orders=True`), Landing (`order_source != "" và != koc`) | Kênh thuộc dept Sales + kênh dùng chung | Chỉ staff Sales | Tất cả đơn của trang (giữ nguyên) |
| **Booking** | Chỉ đơn KOC (`order_source == "koc"`) | (theo dept Booking + dùng chung) | Chỉ staff Booking | Đơn KOC của trang (giữ nguyên) |
| **ADS** | ads-report + đơn của mình | Per-user qua `UserChannel` (giữ nguyên) | — | Chỉ đơn của các kênh mình được gán (giữ nguyên) |
| **Admin** | Tất cả | Tất cả | Tất cả | Tất cả |

Quyết định kèm theo:
- **Khóa lọc danh sách = phòng ban** (`Channel.departments` M2M), **riêng ADS giữ per-user**
  (`UserChannel`) như hiện tại.
- **Rows đơn hàng giữ nguyên**: chỉ lọc theo bộ lọc của từng trang (`show_orders` /
  `order_source`), không thêm lọc theo kênh cho Sales/Booking. Phần thay đổi nằm ở
  **dropdown kênh + dropdown nhân viên** và **chặn page**.
- **Enforcement áp cho TẤT CẢ section** ngay đợt này.
- **Không đổi schema DB** — `Channel.departments` (M2M) và `Department.allowed_sections`
  (JSON) đã có sẵn.

## 3. Kiến trúc tổng thể

Hai mối quan tâm cắt ngang, đều dựa trên `request.user.department`:

| Lớp | Cơ chế | Vị trí |
|-----|--------|--------|
| **A. Chặn page** (authorization) | DRF permission factory `require_section(*sections)` | `apps/users/permissions.py` (mới), gắn lên view |
| **B. Scope danh sách** (data scoping) | Mở rộng repository hiện có | `channel_repositories_impl.py` |

Nguyên tắc: **BE là nguồn sự thật**, FE chỉ render những gì BE trả; FE bỏ phần lọc theo
phòng ban (giữ lại đúng phần "ép buộc kênh đang chọn luôn xuất hiện" để form sửa đơn không
mất giá trị).

## 4. Lớp A — Chặn page

### 4.1. Permission factory (`apps/users/permissions.py` — mới)

```python
from rest_framework.permissions import BasePermission

def require_section(*sections: str):
    """Pass nếu user đăng nhập có quyền với BẤT KỲ section nào trong danh sách.

    Variadic để xử lý endpoint dùng chung giữa nhiều page (vd order CRUD dùng cho
    orders + landing + koc). Nguồn sự thật: User.has_section_access().
    """
    class _HasSectionAccess(BasePermission):
        message = "Bạn không có quyền truy cập chức năng này."

        def has_permission(self, request, view):
            user = request.user
            return bool(
                user
                and user.is_authenticated
                and any(user.has_section_access(s) for s in sections)
            )

    return _HasSectionAccess
```

### 4.2. Mapping section → endpoint

Các view là function-based (`@api_view` + `@permission_classes`). Gắn `require_section(...)`
ngay sau `IsAuthenticated`. Mapping theo app/section (plan sẽ liệt kê chính xác từng view):

| Section | App / endpoint chính |
|---------|----------------------|
| `orders` | `list_orders` |
| `landing-orders` | `list_landing_orders` |
| `koc-orders` | `list_koc_orders` |
| `orders` + `landing-orders` + `koc-orders` (dùng chung) | order CRUD / detail / action / export / contact-log / metrics / import / address... — **bất kỳ section đơn nào cũng vào được** |
| `channels` | **chỉ** các endpoint **quản lý kênh** (tạo/sửa/xoá kênh, gán nhân viên) |
| `customers`, `loyal-customers`, `resale-tracking` | trang/quản lý khách hàng tương ứng |
| `dashboard`, `ads-report` | chuyển 2 check inline sẵn có sang permission class |
| `kiotviet` | `apps/kiotviet` |
| `facebook-ads` | `apps/facebook_ads` |
| `audit` | `apps/audit` |
| `cron-jobs`, `sync` | `apps/cron_jobs` |
| `users` (+ `departments` dùng chung quyền `users`) | `apps/users` |

**Nguyên tắc endpoint "tra cứu/dropdown" trong luồng tạo đơn** (QUAN TRỌNG): endpoint
được tái sử dụng làm nguồn dropdown/tra cứu khi lên đơn **không được** chặn bằng một section
page đơn lẻ — nếu không, vd Booking (chỉ có `koc-orders`) sẽ không tra cứu được khách
hàng/kênh khi tạo đơn. Các endpoint này giữ `IsAuthenticated` thuần và **tự scope** ở Lớp B
(hoặc gắn `require_section` với **union** các section liên quan):
- `GET /api/channels` (danh sách kênh — nguồn dropdown) → `IsAuthenticated`, tự scope (5.1).
- `GET /api/channels/staff` (`staff_collection`) → `IsAuthenticated`, tự scope (5.2).
- Tra cứu / tìm khách hàng dùng khi lên đơn (vd tìm theo SĐT) → `IsAuthenticated` (hoặc union
  với các section đơn). Phân biệt rõ với **trang quản lý khách hàng** (gắn `customers`).

Endpoint **không gắn theo section** khác: `me`, login/refresh, webhook/ingest dùng token kênh.

### 4.3. Endpoint dùng chung (quyết định đã chốt)

Order CRUD/detail/action/export... dùng `require_section("orders", "landing-orders",
"koc-orders")`: Booking sửa được đơn KOC, Sales sửa được đơn thường/landing, không bị 403
chéo.

## 5. Lớp B — Scope danh sách

### 5.1. Danh sách kênh — `ChannelRepository.list_for_user`

```
admin            → tất cả kênh (giữ nguyên)
ADS staff        → kênh được gán per-user qua UserChannel (giữ nguyên)
staff có dept    → Channel.departments chứa dept mình  OR  departments rỗng (dùng chung)
staff không dept → tất cả (giữ parity với FE: currentDepartmentId == null ⇒ all)
```

Query cho nhánh "staff có dept":
```python
queryset.filter(
    Q(departments=user.department) | Q(departments__isnull=True)
).distinct()
```

### 5.2. Danh sách nhân viên — `staff_collection` / `ChannelRepository.list_staff_users`

```
admin         → tất cả staff (form đơn của admin vẫn lọc theo trang ở FE)
staff có dept → chỉ staff cùng department (Sales→Sales, Booking→Booking)
staff không dept → tất cả (parity)
```

`list_staff_users` nhận thêm tham số `department` (optional). View `staff_collection`:
non-admin ⇒ ép `department = request.user.department`; admin ⇒ không lọc. Cache key
`channels:staff:...` thêm thành phần department-scope để không lẫn giữa các user.

## 6. Frontend — dọn dẹp

- **Kênh** (`OrderFormDialog`): bỏ `filterChannelsForDepartment` (BE đã scope). **Giữ** bước
  nhỏ "đảm bảo kênh đang chọn luôn có trong dropdown" (lấy từ channel của chính đơn ở order
  detail) để form **sửa đơn** không mất giá trị khi kênh nằm ngoài scope.
- **Nhân viên** (`buildChannelStaffByCode`): chỉ áp filter theo trang **cho admin**; non-admin
  dùng thẳng list BE trả (đã scope đúng dept).
- Xoá/đơn giản hoá `features/orders/channel-visibility.ts` và test liên quan.
- Không đổi `RequireAuth`/Sidebar (FE vẫn ẩn menu theo `allowed_sections` — UX); BE chỉ bổ
  sung lớp enforcement thật ở API.

## 7. Test

### Backend
- **Permission**: Booking → 403 ở `list_orders` & `list_landing_orders`; Sales → 403 ở
  `list_koc_orders`; user không có section → 403; admin → 200 mọi nơi. (Mẫu:
  `apps/orders/tests/test_order_list_role_scope_api.py`.)
- **Scope kênh**: staff Sales chỉ thấy kênh dept Sales + kênh dùng chung; ADS giữ per-user;
  admin thấy tất cả. (Mẫu: `apps/users/tests/test_department_channels.py`.)
- **Scope nhân viên**: staff Sales chỉ thấy staff Sales; admin thấy tất cả.

### Frontend
- Cập nhật `channel-visibility.test.ts`, `channel-staff.test.ts` theo logic mới (BE-driven).

## 8. Rủi ro & thứ tự triển khai

- **Rủi ro lớn nhất — khoá nhầm user**: bật enforcement khi `allowed_sections` của phòng ban
  cấu hình thiếu sẽ chặn oan. **Tiền đề: user tự audit `allowed_sections` thực tế của
  Sales/Booking/ADS trên prod trước khi bật** (đã chốt — không thêm data migration).
- **Thứ tự an toàn**: triển khai Lớp B (scope danh sách) trước → Lớp A (enforcement) sau,
  kèm test đầy đủ ở mỗi bước.
- Không có migration DB. Cần kiểm tra cache key staff sau khi đổi để tránh phục vụ dữ liệu
  của scope cũ.

## 9. Phân Sprint (sơ bộ — plan chi tiết ở bước sau)

- **Sprint 1 (BE - scope)**: `list_for_user` + `list_staff_users`/`staff_collection` + cache
  key + test scope.
- **Sprint 2 (BE - enforcement)**: `require_section`, gắn lên toàn bộ view theo section,
  chuyển 2 check dashboard/ads-report, test permission.
- **Sprint 3 (FE - dọn dẹp)**: bỏ lọc kênh/nhân viên ở FE, giữ "ép kênh đang chọn", cập nhật
  test FE.
