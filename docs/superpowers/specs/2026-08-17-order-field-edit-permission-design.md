# Phân quyền sửa từng field trong đơn hàng

Ngày: 2026-08-17 · Nhánh: `feat/order-field-edit-permission`

## Vấn đề

Quyền `orders:edit` hiện là một công tắc tất-cả-hoặc-không: ai sửa được đơn thì sửa
được cả ~50 field, từ tên khách tới tổng tiền tới trạng thái. Nhu cầu thật là phân
theo vai trò — kế toán sửa tiền, kho sửa mã vận đơn, sale sửa thông tin khách — mà
không ai chạm được phần của người khác.

Hạ tầng cho việc này đã có từ đợt "deep RBAC" (2026-06-14) nhưng **chưa từng được
dùng**:

| Thành phần | Trạng thái |
|---|---|
| `RoleFieldRule(role, section, field, rule)` | có bảng, prod **0 dòng / 10 role** |
| `EffectivePermissions.field_policy` | có, gộp theo "rule ít hạn chế nhất thắng" |
| `FieldPolicyWriteMixin` | đã cắm vào `OrderInlineUpdateSerializer` |
| Tab "Quy tắc field" ở `RolesPage.tsx` | có, nhưng **ô nhập tên field là text tự do** |
| `useFieldPolicy()` (frontend) | **không nơi nào gọi** — FE chưa hề thực thi |

Bốn lỗ của cơ chế cũ khiến nó không dùng được cho mục tiêu này:

1. **Fail-open** — không khai rule = sửa được. Muốn "chỉ kế toán sửa tiền" phải khai
   rule chặn cho *mọi role còn lại*, và role tạo mới mặc định lại mở.
2. **Tên field gõ tay** — gõ `cost_price` thay vì `total_amount` thì rule im lặng
   không chạy, người cấu hình tưởng đã khoá.
3. **Strip im lặng** — payload bị bỏ field, API vẫn trả 200. Nhân viên sửa giá xong
   thấy "thành công" mà giá không đổi, phát hiện lúc đối soát.
4. **Frontend không thực thi** — ô nhập vẫn mở, người dùng vẫn gõ được.

## Quyết định thiết kế

**Allow-list, fail-closed.** Mặc định không sửa được field nào; phải cấp mới mở.
Field mới thêm vào đơn về sau mặc định đóng. Đây là chiều ngược với `RoleFieldRule`
cũ, và là lý do phải có cấu trúc dữ liệu riêng thay vì thêm rule type.

**Bảng mới `RoleFieldGrant` thay vì mở rộng `RoleFieldRule`.** Hai bảng trả lời hai
câu hỏi khác nhau: `RoleFieldRule` = *được xem gì* (ẩn field ở output),
`RoleFieldGrant` = *được sửa gì*. Nhét cả hai ngữ nghĩa ngược nhau vào cùng map
`field → rule` là mời `drop_hidden_fields` hoặc `strip_protected_write_fields` đọc
nhầm — lỗ hổng im lặng, không test nào bắt được.

**Một bộ field chung cho cả 3 trang đơn.** `orders`, `landing-orders`, `koc-orders`
dùng chung `OrderInlineUpdateSerializer` với `field_section = "orders"`; tách riêng
sẽ thành 150 ô cấu hình mỗi role và một trong ba dễ bị quên. Cột `section` vẫn giữ
trong bảng để sau tách được mà không cần migration. Việc ai thấy trang nào vẫn do
quyền section cũ điều tiết.

**Lưu tên field thật, không lưu tên nhóm.** Người cấu hình tick theo nhóm nghiệp vụ,
nhưng DB ghi từng field đã bung ra. Lưu tên nhóm thì hôm nào thêm field vào nhóm là
mọi role có nhóm đó **tự động** sửa được field mới — fail-open lẻn vào cửa sau. Đổi
lại, chia lại nhóm về sau không đụng tới dữ liệu đã cấp.

**Vi phạm thì 403, nhưng so theo GIÁ TRỊ.** Form đơn gửi lại full object nên chặn
theo sự có mặt của field sẽ 403 oan cho mọi cập nhật khác đi kèm. Chỉ chặn khi giá
trị gửi lên **khác thật** giá trị đang lưu — đúng khuôn guard `staff_id` và guard
`invoice_lock` đang chạy.

**`staff_id` không nằm trong catalog.** Nó đã có quyền riêng `sale-assignment:edit`
từ 2026-08-07; kéo vào đây là hai cơ chế cùng gác một cửa.

## Phạm vi

### 1. Tầng RBAC (`apps/users`)

**Model** — `apps/users/models.py`:

```python
class RoleFieldGrant(models.Model):
    """Allow-list: role được SỬA field nào. Vắng mặt = không được sửa."""
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="field_grants")
    section = models.CharField(max_length=50)   # v1 luôn "orders"
    field = models.CharField(max_length=100)

    class Meta:
        db_table = "rbac_role_field_grants"
        unique_together = ("role", "section", "field")
        indexes = [models.Index(fields=["role", "section"], name="rbac_rfg_role_sec_idx")]
```

**Catalog** — `apps/users/rbac_catalog.py`, cạnh `PERMISSION_CATALOG`. Đặt ở `users`
chứ không ở `orders` vì `.importlinter` cấm `apps.users` import `apps.orders` (chiều
ngược lại thì được). Catalog chỉ là dữ liệu: mã nhóm, nhãn tiếng Việt, tuple field.

```python
ORDER_FIELD_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("customer",  "Khách hàng",        ("name", "phone", "customer_gender")),
    ("address",   "Địa chỉ",           ("address", "raw_address", "province", "district", "ward",
                                        "province_code", "district_code", "ward_code",
                                        "kiotviet_location", "kiotviet_ward")),
    ("products",  "Sản phẩm",          ("items", "sku", "quantity")),
    ("money",     "Tiền & chiết khấu", ("total_amount", "total_after_discount", "discount_amount",
                                        "booking_voucher_discount", "other_price",
                                        "customer_prepaid", "shipping_fee")),
    ("shipping",  "Vận chuyển",        ("shipping_code", "weight", "dimensions",
                                        "fulfillment_warehouse", "branch_id")),
    ("status",    "Trạng thái & CSKH", ("status", "result", "feedback", "customer_return",
                                        "return_date", "crm")),
    ("invoice",   "Hoá đơn & VAT",     ("invoice_code", "invoice_id", "vat", "is_export_vat",
                                        "vat_link", "payment_method", "order_code")),
    ("classify",  "Phân loại & KOC",   ("order_type", "koc_order_type", "order_source",
                                        "channel_code", "kiotviet_channel_id",
                                        "recall", "recall_entries", "created_at")),
)

ORDER_FIELD_LABELS: dict[str, str] = { ... }   # field → nhãn tiếng Việt cho message 403 + UI
```

**Resolver** — `apps/users/rbac.py`:

- `EffectivePermissions` thêm `editable_fields: dict[str, frozenset]`.
- Gộp nhiều role = **hợp (union)** — nới lỏng nhất thắng, nhất quán với quy tắc
  `field_policy` hiện có.
- `_compute` đọc `RoleFieldGrant.objects.filter(role_id__in=role_ids)` — một query,
  cùng nhịp với query `RoleFieldRule` sẵn có.
- Superuser / role `is_system` short-circuit như cũ, không đụng.

> **Bẫy cache — bắt buộc:** `from_cache` đang đọc `data["..."]`. Thêm key mới mà đọc
> kiểu đó thì mọi entry cache cũ (TTL 30 phút) ném `KeyError` ngay giây deploy. Phải
> `data.get("editable_fields", {})` **và** gọi `bump_rbac_cache_version()` trong
> migration. Hai lớp, vì lớp nào cũng có thể bị quên khi review.

**API role** — `apps/users/serializers.py` + `services.py`: `RoleSerializer` thêm
`field_grants: list[str]`, ghi theo cùng khuôn `field_rules` đang có (xoá sạch rồi
`bulk_create`). Validate tên field theo catalog, trả 400 nếu lạ — chống hẳn lớp lỗi
"gõ sai tên field" của cơ chế cũ. Đường ghi role đã gọi `bump_rbac_cache_version()`
nên cấp quyền qua UI có hiệu lực ngay.

**Endpoint catalog** — `GET /api/rbac/order-field-catalog` trả nhóm + field + nhãn,
cache dài. Frontend không hard-code danh sách; hai bên lệch nhau chỉ là chuyện thời
gian.

### 2. Chặn ở backend (`apps/orders`)

**Một điểm chặn duy nhất.** `update_order_inline` là đường ghi đơn duy nhất do người
dùng bấm; các POST còn lại là *hành động* (nhân bản / xoá / chuyển / khôi phục / huỷ
KiotViet), có gate section riêng.

Thứ tự trong `order_action_view.update_order_inline`:

```
scoped_order_or_error → can_edit_channel → guard staff_id (sale-assignment)
  → serializer.is_valid()
  → guard invoice_lock          (đã có)
  → guard field_grant           (MỚI)
  → OrderService.update_order_inline_verified
```

Chặn **sau** `is_valid()` để so trên `validated_data` đã coerce type. So ở payload
thô thì `"1000000"` vs `Decimal('1000000.00')` khác nhau và chặn oan — bài học đã
thành comment 20 dòng ở guard `staff_id`, không lặp lại.

**Tái dùng bộ so sánh, không viết lại.** `apps/orders/invoice_lock.py` đã giải xong
phần khó: so `items` theo `product_id`/`sku`, chuẩn hoá Decimal/int, sentinel
`_UNPARSEABLE` cho giá trị không parse nổi. Tách phần thuần đó ra
`apps/orders/field_diff.py` (không import DRF/model, không I/O); `invoice_lock`
import lại — **hành vi khoá hoá đơn không đổi**, đã có test bảo vệ.

Bổ sung 4 kiểu `invoice_lock` chưa gặp:

| Kiểu | Field | Cách so |
|---|---|---|
| Khoá ngoại | `channel_code`, `kiotviet_channel_id`, `fulfillment_warehouse` | `validated_data` trả về *instance* → so `.pk` với `order.<attr>_id` |
| Ngày giờ | `created_at` (`DateTimeField`) | quy cả hai vế về UTC rồi so; **không** dùng getter local (bom múi giờ) |
| Ngày | `return_date` (`DateField`) | so `date` trực tiếp, không dính múi giờ |
| Boolean | `is_export_vat`, `customer_return` | ép `bool` rồi so |
| Lồng nhau | `crm`, `recall`, `recall_entries` | chữ ký chuẩn hoá theo từng field con |

`branch_id` là `CharField` (không phải khoá ngoại, dù tên kết thúc bằng `_id`) → đi
nhánh chuỗi `_norm_str` sẵn có. Đây đúng loại bẫy khiến comparator chặn oan nếu suy
kiểu theo tên field thay vì đọc model.

`crm` là chỗ tốn công nhất — nó ghi sang bản ghi Assessment chứ không phải cột của
`Order`, nên có comparator riêng và test đối chứng riêng, không gộp vào nhánh chung.

**Response khi bị chặn:**

```json
403 { "detail": "Bạn không có quyền sửa: tổng tiền, trạng thái.",
      "blocked_fields": ["total_amount", "status"] }
```

Đúng khuôn `locked_fields` của `invoice_lock` → frontend xử lý bằng cùng một nhánh
code. Kèm `logger.warning` ghi user / đơn / field bị chặn — đủ truy "ai đang cố sửa
gì" mà không phải đẻ bảng audit mới.

**Phòng thủ hai lớp.** Frontend không gửi field chưa được cấp (mục 3); guard backend
là lưới an toàn cho tab mở lâu, gọi API trực tiếp, bản build cũ chưa reload.

### 3. Frontend

| Chỗ | Việc |
|---|---|
| `UserSerializer` + `use-auth-bootstrap.ts` | thêm `editable_order_fields: string[]` vào payload user |
| `hooks/useOrderFieldGrants.ts` (mới) | trả `canEditField(field)`, đặt cạnh `useFieldPolicy` |
| `OrdersTable/OrderCell.tsx` | `canEdit &&= canEditField(field)` — nhánh render text tĩnh đã có sẵn |
| `OrderFormDialog.tsx` | disable input + tooltip "Bạn không có quyền sửa trường này", **và loại field khỏi payload lúc submit** |

Strip lúc submit quan trọng hơn vẻ ngoài của nó: form gửi lại full object, để nguyên
thì mỗi lần comparator backend so sai một field lồng nhau là user ăn 403 vô cớ.
Strip ở FE thu hẹp bề mặt xuống gần bằng không.

Lấy quyền từ hook context, **không đọc ref trong thân render** — React Compiler đóng
băng giá trị (đã cắn ở nút Xuất Excel) và vitest không có compiler nên test không
bắt được.

**Trang Phân quyền** — thêm tab "Quyền sửa field đơn" cạnh 3 tab hiện có:

```
▸ Khách hàng                              [3/3]  ☑
▾ Tiền & chiết khấu                       [2/7]  ⊟
     ☑ Tổng tiền          ☐ Giảm giá      ☐ Voucher booking
     ☑ Phí vận chuyển     ☐ Phụ phí khác  ☐ Khách ứng trước
     ☐ Sau giảm giá
▸ Trạng thái & CSKH                       [0/6]  ☐
```

Checkbox nhóm ba trạng thái (đủ / một phần / trống), badge đếm, nút *Chọn tất cả* /
*Bỏ hết*. Cảnh báo đỏ khi role có `orders:edit` mà chưa cấp field nào: *"Vai trò này
sửa được đơn nhưng chưa được cấp field nào — người dùng sẽ không sửa được gì."*
Thiếu dòng này là chắc chắn có người cấu hình xong rồi mở ticket.

Không nhét vào tab "Quy tắc field" cũ — hai thứ trả lời hai câu hỏi khác nhau, trộn
vào nhau là mời gọi cấu hình nhầm. Đồng thời **bỏ lựa chọn `readonly`** khỏi tab cũ
và đổi nhãn tab thành "Ẩn field khi xem": allow-list đã thay thế hoàn toàn vai trò
của `readonly`, để lại hai cần gạt cùng điều khiển một cửa là nguồn của lỗi cấu
hình. Prod đang 0 dòng nên bỏ không mất gì.

### 4. Test

Bám cấu trúc suite hiện có, không rải test lẻ cho từng chức năng:

- `apps/users/tests/test_rbac_resolver.py` — union grant nhiều role, superuser
  bypass, và **`from_cache` thiếu key `editable_fields`** (case bảo vệ cú deploy).
- `apps/users/tests/test_rbac_sections.py` — validate tên field lạ khi lưu role.
- Một file hành vi ở `apps/orders/tests/` — ma trận (có grant / không grant) × (đổi
  thật / gửi lại giá trị cũ) × 6 kiểu field: scalar, decimal, khoá ngoại, datetime,
  `items`, `crm`.
- Test đối chiếu catalog ↔ `OrderInlineUpdateSerializer.Meta.fields`, đặt ở
  `apps/orders/tests/` (orders được phép import users). Thiếu test này thì 6 tháng
  nữa catalog lệch thực tế mà không ai biết.
- Frontend: `OrderCell` render text tĩnh khi thiếu grant; `OrderFormDialog` loại
  field khỏi payload submit.

Regen `schema.yaml` cho endpoint catalog + field mới của role. Lưu ý file đang cũ
sẵn ~444 dòng — chỉ commit phần diff thuộc MR này.

### 5. Rollout

**Bước 1 — migration seed cấp ĐỦ field cho các role đang có quyền sửa đơn.** Prod
hiện có 7 role / 36 user với `orders:edit` | `landing-orders:edit` |
`koc-orders:edit` | `shipments:edit`:

| Role | User | Quyền sửa đơn hiện có |
|---|---|---|
| Phòng: Sales | 15 | `orders`, `landing-orders` |
| Phòng: Booking | 9 | `koc-orders` |
| Phòng: Kho | 6 | `orders`, `shipments` |
| Trưởng nhóm Sales | 3 | `orders`, `landing-orders` |
| Phòng: Kế toán | 2 | `orders`, `landing-orders` |
| Trưởng nhóm Ads (xem toàn bộ) | 1 | `orders`, `landing-orders` |
| Nhân viên mặc định | 0 | cả ba |

Ngày deploy hành vi giống hệt hôm nay, **không ai mất quyền**. Đây là chỗ cố ý làm
khác đợt `sale-assignment` (07/08): lần đó 1 quyền × vài role nên cấp tay được; lần
này ~50 field × 7 role = ~350 dòng, cấp tay là cầm chắc sai sót và downtime.

Role tạo mới về sau: 0 grant → fail-closed đúng thiết kế.

Cùng migration gọi `bump_rbac_cache_version()`.

**Bước 2 — admin siết dần từng role theo nhu cầu thật.** Không có deadline kỹ thuật
ép; hệ thống chạy bình thường cho tới khi ai đó chủ động bỏ tick.

**Thứ tự deploy: migrate TRƯỚC, roll code SAU.** Code cũ không biết bảng mới nên
chạy trước vô hại; code mới lên là đã có sẵn grant để đọc.

**Thứ tự rollback thì NGƯỢC: revert code trước, drop bảng sau.** Drop bảng khi code
mới còn chạy = guard đọc rỗng = fail-closed = **toàn hệ thống không sửa được đơn**.
Đúng vết migration 0124 đã cắn.

## Ngoài phạm vi

- **Quyền theo hành động cho các nút Huỷ / Khôi phục đơn.** Chúng đổi `status` qua
  endpoint riêng, nên khoá field `status` **không** chặn được các nút này. Phản trực
  giác và sẽ có người báo bug — muốn chặn thật thì phải siết quyền hành động, việc
  của v2.
- **Áp field grant cho luồng tạo đơn và import Excel.** Fail-closed lúc tạo = sale
  không tạo nổi đơn; giữ gate `orders:create` như cũ. Lỗ để lại: người có
  `create` + `delete` sửa gián tiếp bằng cách tạo đơn mới rồi xoá đơn cũ — cần
  **hai** quyền nên chấp nhận được ở v1.
- **Job nền, sync KiotViet, `auto_sync_invoice_full`, quét kho.** Không có
  `request.user` nên không áp — bắt buộc, nếu không sync chết ngay.
- **Tách quyền field riêng cho từng trang đơn.** Cột `section` đã chừa sẵn, làm khi
  có nhu cầu thật.
- **Field grant cho các màn khác** (khách hàng, sản phẩm, kho). Cơ chế đủ tổng quát,
  chỉ cần thêm catalog cho section tương ứng.
