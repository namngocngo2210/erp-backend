# Runbook: quyền sửa field đơn hàng

## Thứ tự deploy — KHÔNG được đảo

1. `python manage.py migrate users` (0035 tạo bảng, 0036 seed + bump cache).
2. Roll code (backend + frontend).

Code cũ không biết bảng mới nên migrate trước là vô hại. Roll code trước khi migrate
= guard đọc bảng chưa tồn tại → 500 toàn bộ đường sửa đơn.

**Cảnh báo:** nhánh này đổi `RBAC_CACHE_SHAPE_VERSION`, nên **mọi** entry cache
quyền sẽ miss một lượt ngay giây roll code — khoảng 6 query mỗi user đang online,
dồn vào cùng thời điểm. Hãy roll vào **giờ thấp điểm**. Hệ thống này đã có tiền sử
bão connection tới PostgreSQL (sự cố 2026-07-31 và 2026-08-03), nên một đợt cache
miss đồng loạt cộng với fanout `order.updated` sẵn có là rủi ro thật, không phải lo
xa.

## Thứ tự rollback — NGƯỢC LẠI

1. Revert code trước.
2. Mới `migrate users 0034` (drop bảng).

Drop bảng khi code mới còn chạy = guard đọc rỗng = fail-closed = **toàn hệ thống
không sửa được đơn**. Đúng vết migration 0124 đã cắn.

## Kiểm tra sau deploy

- Vào Phân quyền → chọn "Phòng: Sales" → tab "Quyền sửa field đơn": phải thấy đủ 8
  nhóm đã tick.
- Sửa thử một đơn bằng tài khoản sale: phải lưu được như trước.
- `SELECT COUNT(*) FROM rbac_role_field_grants;` — kỳ vọng ≈ 49 × số role có BẤT KỲ
  mã nào trong `{orders, landing-orders, koc-orders, shipments} × {view, edit}`.
  Seed theo cả `:view` chứ không riêng `:edit`: endpoint sửa đơn gác bằng
  `require_section` = `{section}:view` và KHÔNG chỗ nào check `:edit`, nên hôm nay
  role view-only vẫn sửa đơn hằng ngày — seed theo `:edit` là cắt quyền của họ ngay
  giây roll code. Đếm số role kỳ vọng TRƯỚC khi migrate:

  ```sql
  SELECT COUNT(DISTINCT rp.role_id)
  FROM rbac_role_permissions rp
  JOIN rbac_permissions p ON p.id = rp.permission_id
  WHERE p.code IN ('orders:view','orders:edit','landing-orders:view',
                   'landing-orders:edit','koc-orders:view','koc-orders:edit',
                   'shipments:view','shipments:edit');
  ```

- Riêng Phòng Kho (`shipments:view`/`shipments:edit`, PATCH đơn qua cùng endpoint):
  thử sửa mã vận đơn bằng tài khoản kho — không seed nhóm này là kho mất quyền sửa
  mã vận đơn ngay ngày deploy dù không đụng gì tới `orders:edit`.

## Siết quyền (làm sau, không gấp)

Bỏ tick nhóm không thuộc phận sự của từng role. Hiệu lực ngay: mọi đường ghi role
đều gọi `bump_rbac_cache_version()`.

### CHẶN — chưa được bắt đầu Bước 2

**Chưa bỏ tick quyền của BẤT KỲ role nào cho tới khi MR tiếp theo (lớp FE cho các
surface còn lại) lên xong.** Các chỗ sau chưa có lớp FE nào đọc
`editable_order_fields`, người dùng sẽ ăn 403 không hiểu vì sao, không có gợi ý nào
trên giao diện:

- Trang chi tiết đơn: `OrderDetailPage` và toàn bộ các `Block*`.
- Ô CRM / thu hồi inline trên bảng đơn: `cell-renderers.tsx` — `renderCrmSelect`,
  `RecallEntryCell`, `renderReturnSlipText`.

Và hai endpoint ghi thẳng `OrderAssessment` **không đi qua guard** (không phải
`PATCH /api/orders/{id}`), nên siết quyền `crm` cũng KHÔNG chặn được chúng — role bị
thu quyền vẫn đổi được trạng thái sale qua hai nút đó:

- `create_contact_log` — `contact_log_view.py`
- `close_case` — `close_case_view.py`

### M1 — grant đổi thì UI phải F5 mới đúng

`editable_order_fields` chỉ được nạp lúc login/bootstrap. Admin đổi grant xong,
backend chuẩn NGAY (mọi đường ghi role đều bump cache-version), nhưng user đang mở
tab vẫn thấy UI cũ: ô đáng lẽ khoá thì vẫn gõ được (lưu sẽ 403), ô vừa được mở thì
vẫn xám. Bảo user F5 sau khi đổi quyền.

### M2 — rollback rồi deploy lại sẽ XOÁ SẠCH công siết quyền

`unseed()` của 0036 xoá **mọi** grant `section="orders"`, kể cả grant admin tự tạo
sau deploy — nó không phân biệt dòng do seed sinh ra với dòng người thật cấu hình.
Chuỗi migrate → siết quyền → rollback (`migrate users 0035`, hoặc `0034` như mục
"Thứ tự rollback" — cả hai đều chạy `unseed`) → deploy lại sẽ âm thầm
cấp lại FULL quyền cho mọi role, xoá sạch công siết mà không báo gì. Trước khi
rollback: dump `rbac_role_field_grants` ra file để còn khôi phục.

## Điều KHÔNG chặn được (cố ý, xem spec)

- Nút Huỷ đơn / Khôi phục đơn đổi `status` qua endpoint hành động riêng → khoá field
  `status` không chặn được. Muốn chặn phải siết quyền hành động.
- Tạo đơn và import Excel không áp quyền field (gate bằng `orders:create`).
- Job nền, sync KiotViet, quét kho không có `request.user` → không áp.

## Cập nhật 2026-08-18 — quyền CSKH xuống từng trường

Trước: cả khối đánh giá CSKH là **một** hạt quyền (`crm`). Nay mỗi trường là một hạt
riêng (`crm.sales_status_code`, `crm.internal_note`, ...), nên cấu hình được kiểu
"sale sửa được ghi chú nhưng không sửa được trạng thái sale".

Migration `0037_expand_crm_field_grants` **bung** mọi grant `crm` hiện có thành đủ
26 hạt con rồi xoá bản ghi `crm`. Không có bước này thì cả công ty mất quyền sửa
CSKH ngay giây roll code. Chiều ngược lại (`collapse`) gộp về `crm`, dùng khi
rollback.

Thứ tự deploy và rollback **giữ nguyên như phần trên**: migrate trước / roll code
sau; rollback thì revert code trước rồi mới lùi migration.

### Điều cần biết khi cấu hình

- **Trường động** (khai qua Django admin, bảng `assessment_field_defs`) không có ô
  riêng — tất cả dùng chung ô **"Trường đánh giá mở rộng"** (`crm.__extra__`).
  Catalog quyền là hằng tĩnh ở `apps/users/rbac_catalog.py`, mà `apps.users` bị cấm
  import `apps.orders` nên không thể đọc bảng đó.
- Thêm **cột đánh giá tĩnh** mới vào `ASSESSMENT_FIELDS` thì phải khai thêm vào
  catalog. Test `apps/orders/tests/test_crm_field_catalog.py` sẽ đỏ nếu quên.
- Ô **"Nhu cầu khách"** (`crm.needs`) là quan hệ riêng, không phải cột — cấp riêng.
