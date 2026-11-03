# Rà soát data-scope RBAC — khung quyết định

- Ngày: 2026-07-27
- Bối cảnh: `docs/audit-2026-07-26-full-stack.md` (mục "Điều quan trọng nhất về giá trị thực tế")
- Công cụ: `python manage.py audit_rbac_scope` (chỉ đọc)

---

## 1. Vì sao tài liệu này chỉ là khung, không phải danh sách role cụ thể

Role được sinh **động theo phòng ban**: `0015_backfill_roles_from_legacy` tạo
`Phòng: {tên phòng ban}` cho mọi department đang có. Nên **danh sách role thật chỉ tồn tại
trong DB của từng môi trường** — không đọc được từ code, và mỗi lần thêm phòng ban là có
thêm role mới.

Vì vậy quy trình đúng là: **lấy ma trận thật bằng command → đối chiếu với khung dưới đây →
quyết định từng role**. Chạy trên prod:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py audit_rbac_scope --show-users
```

Command in ra từng role: scope mặc định, scope hiệu dụng của từng section, nguồn (mặc định
hay override), và **cảnh báo** cho hai loại cấu hình sai. Nó không sửa gì.

## 2. Sự thật quan trọng nhất: scope chỉ có tác dụng ở 10/31 section

Hệ thống có 31 section trong `VALID_SECTIONS`, nhưng **chỉ 10 section được code thực sự
resolve data-scope**. Với 21 section còn lại (`products`, `stock`, `employees`, `salaries`,
`users`, `warehouses`, `audit`, …), đặt scope gì **cũng không có tác dụng** — quyền ở đó là
nhị phân: có `{section}:view` thì thấy tất cả, không có thì không thấy gì.

| Section có scope | Nơi resolve |
|---|---|
| `orders`, `koc-orders`, `outside-orders`, `landing-orders`, `shipments`, `customers`, `warranty`, `resale-tracking` | `_SECTION_ORDER_IDENTITY_Q` — record-scope đơn hàng |
| `contacts` | `contact_list_service.py` |
| `ads-report` | `_repositories_reports.py` (chỉ chấp nhận `all`, khác đi là từ chối) |

Đừng tốn thời gian tinh chỉnh scope cho 21 section kia. Nếu cần siết chúng, phải siết bằng
**cấp/không cấp permission**, không phải bằng scope. Test `test_scope_aware_sections_match_code_reality`
khoá danh sách này — thêm call site resolve scope mới mà quên cập nhật command thì test đỏ.

## 3. Ba section BẮT BUỘC để `all` — hạ xuống là mất chức năng

Đây không phải khuyến nghị mềm; cả ba đều có bằng chứng từ code/test:

| Section | Vì sao |
|---|---|
| `landing-orders` | Trang Landing là **pool chung**, đơn chưa phân có `staff = NULL`. Scope `own` → user không thấy đơn nào chưa phân. Đây đúng là sự cố mà `0022_landing_orders_scope_all` phải đi sửa. |
| `shipments` | Kho xử lý **mọi** đơn và **không bao giờ** là `Order.staff`. Scope `own` → kho không PATCH được trạng thái giao hàng của bất kỳ đơn nào; sau đợt 2026-07-26 còn mất cả quyền xuất `dispatch_batch`. |
| `warranty` | Khách mang máy tới bảo hành có thể đã mua từ **bất kỳ** sale nào; NV bảo hành không phải `Order.staff`. Scope `own` → không tạo được phiếu bảo hành nào. Đã có test chứng minh bằng code rằng siết sẽ khớp 0 đơn. |

## 4. Sáu section NÊN siết — đây là nơi record-scope có giá trị thật

`orders`, `koc-orders`, `outside-orders`, `customers`, `resale-tracking`, `contacts`.

Hiện tại tất cả đang là `all` (do seed), nghĩa là lớp phân quyền bản ghi vừa dựng **không lọc
gì**. Khuyến nghị theo vai trò:

| Vai trò | `orders` / `koc` / `outside` | `customers` | `resale-tracking` · `contacts` | `landing-orders` · `shipments` · `warranty` | `ads-report` |
|---|---|---|---|---|---|
| **Sale** | `own` | `own` | `own` | *(chỉ cấp nếu thực sự dùng)* | — |
| **Trưởng nhóm sale** | `department` | `department` | `department` | | — |
| **Quản lý kinh doanh** | `all` | `all` | `all` | | `all` |
| **CSKH** | `all`¹ | `all` | `all` | | — |
| **Kho / vận đơn** | *(không cấp)* | *(không cấp)* | *(không cấp)* | `shipments = all` | — |
| **Bảo hành** | *(không cấp)* | `all`² | *(không cấp)* | `warranty = all` | — |
| **Marketing / Ads** | *(không cấp)* | *(không cấp)* | *(không cấp)* | | `all` |
| **Administrator** | *(is_system → bỏ qua scope)* | | | | |

¹ CSKH cần tra cứu đơn của mọi sale để trả lời khách. Nếu muốn siết, dùng `department`.
² Chỉ cấp nếu quy trình bảo hành cần xem hồ sơ khách; không thì bỏ hẳn permission.

**Nguyên tắc chọn scope** — theo thứ tự ưu tiên:
1. Nếu vai trò đó **không dùng** một section, hãy **bỏ permission**, đừng để permission rồi
   siết bằng scope. Không cấp là hàng rào chắc chắn hơn.
2. Còn lại thì chọn scope **hẹp nhất mà công việc vẫn chạy được**.
3. `channel` chỉ dùng khi phân chia theo kênh bán là có thật trong vận hành (đội chạy riêng
   từng kênh). Nó phụ thuộc `UserChannel` được gán đúng — kiểm tra trước khi chọn.
4. `department` phụ thuộc `UserDepartment` (hoặc `User.department` cũ) và các kênh gắn với
   phòng ban đó. Kiểm tra `Department.channels` có dữ liệu trước khi chọn.

## 5. Quy trình thực hiện an toàn

Đổi scope là thay đổi có thể **làm mất chức năng ngay lập tức** với người đang dùng. Đừng đổi
hàng loạt.

1. **Chụp trạng thái hiện tại** — lưu output làm mốc rollback:
   ```bash
   docker compose -f docker-compose.prod.yml exec -T web \
     python manage.py audit_rbac_scope --show-users > rbac-truoc-$(date +%F).txt
   ```
2. **Bắt đầu bằng một role ít người nhất** (cột `--show-users`), tốt nhất là một phòng ban nhỏ.
3. Đổi qua **giao diện Phân quyền** (trang Roles) chứ không sửa thẳng DB — UI đi qua
   `RoleService`, có bump cache RBAC. Sửa SQL trực tiếp sẽ để cache cũ sống tới 30 phút.
4. **Theo dõi 1 ngày làm việc**: hỏi trực tiếp người trong role đó xem có mất chức năng gì
   không. Dấu hiệu cần chú ý: danh sách trống bất thường, nút bị 403, không in được.
5. Xanh thì làm role tiếp theo. Đỏ thì trả lại scope cũ (cache tự bump khi lưu qua UI).
6. Sau mỗi vòng, chạy lại `audit_rbac_scope` để xác nhận không còn cảnh báo ngoài dự kiến.

**Rollback:** đổi lại scope qua UI. Không có migration nào cần revert — scope nằm ở
`Role.scope_default` và `RoleSectionScope`, đều là dữ liệu.

## 6. Kiểm tra trước khi bắt đầu

Ba câu hỏi cần trả lời bằng dữ liệu, không bằng trí nhớ:

- **Có role nào chỉ có `landing-orders` mà không có `orders` không?** Nếu có, họ sẽ mất quyền
  in đơn Landing (endpoint in chốt scope theo section `orders`, xem "nợ có chủ ý" trong báo
  cáo audit). Command sẽ hiện rõ điều này.
- **`UserChannel` và `UserDepartment` đã được gán đầy đủ chưa?** Chọn scope `channel`/
  `department` mà dữ liệu gán còn trống thì người dùng thấy **rỗng**, không phải thấy ít.
- **Role nào đang có `is_system = True`?** Chúng bỏ qua toàn bộ scope. Nếu có role không phải
  Administrator mà lại `is_system`, đó là vấn đề riêng cần xử lý trước.

## 7. Việc còn lại sau khi rà xong

Rà scope xong **chưa phải là hết**. Ba mục dưới đây thuộc tầng code, đã ghi trong báo cáo
audit và cần sprint riêng:

- `can_edit_order` vẫn **fail-open** cho `own`/`all` — lưới chặn cấu trúc
  (`test_record_scope_coverage.py`) đang gánh vai trò phòng thủ. Nên sửa dứt điểm.
- `GET /api/orders/search` vẫn trả `phone`/`address`/số tiền **thô** cho đơn trong scope.
  Có mask hay không là quyết định nghiệp vụ.
- `POST /api/warranty/claims` gate bằng `warranty:view` (quyền đọc) dù nó ghi — nên là
  `warranty:create`. Cần đối chiếu role prod trước khi đổi để không khoá tổ bảo hành.
