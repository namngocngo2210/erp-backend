# Đơn KOC: nhiều mã thu hồi + phân loại đơn (tặng mẫu / gia hạn / thu hồi)

Ngày: 2026-08-13
Nhánh: `feat/koc-order-type-multi-recall`

## 1. Bối cảnh & vấn đề

Trang đơn hàng KOC (`order_source = koc`) hiện có khối "Thu hồi" (`OrderRecall`, quan
hệ 1-1 với `Order`, bảng `order_recalls`) gồm 4 trường nhập tay:

| Trường | Kiểu hiện tại | Ghi chú |
|---|---|---|
| `recall_code` | `CharField(64)` | **Chỉ 1 mã** |
| `recall_created_date` | `DateField` | |
| `recall_received_date` | `DateField` | |
| `return_slip_code` | `ArrayField(CharField(64))` | Đã là nhiều mã (migration `0101`) |

Hai vấn đề nghiệp vụ:

1. **Một đơn KOC có thể phát sinh nhiều đơn thu hồi** (thu hồi nhiều đợt, hoặc mã bị
   huỷ-tạo lại). `recall_code` chỉ chứa được 1 mã nên nhân viên phải nhét mã thứ hai
   vào ô ghi chú — mất khả năng tra cứu.
2. **Không lọc được đơn theo tính chất nghiệp vụ.** Đơn "tặng mẫu", "gia hạn",
   "thu hồi" hiện chỉ được đánh dấu bằng chữ tự do trong ô ghi chú — không có field,
   không có filter, không thống kê được. Ghi chú vì thế bị dùng lẫn lộn giữa "phân
   loại đơn" và "ghi chú vấn đề phát sinh".

Mục tiêu: `recall_code` chứa được N mã; thêm một trường phân loại đơn KOC lọc được;
trả ô ghi chú về đúng chức năng ghi chú vấn đề khác.

## 2. Quyết định nghiệp vụ đã chốt

| Câu hỏi | Chốt |
|---|---|
| Một đơn có thể vừa tặng mẫu vừa gia hạn không? | **Không** — chọn 1 loại duy nhất (enum), để trống = đơn KOC thường |
| Dữ liệu cũ trong ghi chú có backfill không? | **Không** — đơn cũ để trống, nhân viên gán dần. Không đoán từ text ghi chú |
| Chọn loại = "Thu hồi" có bắt buộc nhập mã thu hồi không? | **Không bắt buộc** — đơn mới lên kế hoạch thu hồi vẫn phải lọc ra được trước khi có mã |
| Ô ghi chú | Giữ nguyên, từ nay chỉ dùng cho vấn đề khác. Không migrate, không xoá chữ |

## 3. Thiết kế

### 3.1. `recall_code` → nhiều mã

Codebase đã có tiền lệ **giống hệt**: `return_slip_code` từng là `CharField` và được
chuyển sang `ArrayField` ở
`backend/apps/orders/migrations/0101_orderrecall_return_slip_code_array.py`. Thiết kế
này sao chép nguyên khuôn đó, không phát minh cách mới.

**Giữ nguyên tên field `recall_code`** (số ít) dù đã là mảng — đúng như
`return_slip_code`. Đổi tên sẽ kéo theo: đổi column key đang lưu trong localStorage của
client (người dùng mất cấu hình cột), đổi key export, đổi contract API, đổi header
import. Cái giá đó không đổi lại được gì ngoài tên đẹp hơn.

**Contract API giữ nguyên dạng chuỗi phẩy.** Request/response vẫn là
`"recall_code": "RC-01, RC-02"`, backend parse bằng `_parse_slip_codes` (đã có sẵn,
dùng chung với `return_slip_code`). Lý do: đồng nhất với ô "Phiếu trả hàng" ngay bên
cạnh trong UI, FE không phải xử lý hai kiểu dữ liệu cho hai ô nhìn giống nhau.

**DB & index.**

```python
recall_code = ArrayField(models.CharField(max_length=64), default=list, blank=True)
```

Index `order_recall_code_idx` hiện là btree trên `varchar` — **vô dụng với kiểu mảng**.
Phải đổi sang **GIN**. Đây là điểm bắt buộc, không phải tối ưu: màn tra cứu mã vận đơn
(`backend/apps/orders/transport/views/shipping_lookup_view.py:133`) đang filter
`recall__recall_code=code`, sẽ thành `recall_code__contains=[code]`; không có GIN thì
mỗi lần quét mã là seq scan toàn bảng `order_recalls`.

**Migration** dùng `SeparateDatabaseAndState` + SQL function split/trim/bỏ-rỗng/cắt-64,
copy nguyên `0101` (Postgres cấm subquery trong `USING`, nên phải bọc trong function
tạm rồi drop). Giữ `SET LOCAL lock_timeout = '5s'`.

**Các điểm phải sửa kèm** (đây là toàn bộ chỗ đọc/ghi `recall_code`, đã rà hết):

| File | Sửa gì |
|---|---|
| `apps/orders/models.py` | Kiểu field + index GIN |
| `apps/orders/migrations/01xx_…` | Migration mới |
| `…/serializers/…/order_core_serializers.py` `get_recall` | `", ".join(recall.recall_code or [])` |
| `…/application/services/order_service_helpers.py` `_upsert_order_recall` | Chuyển `recall_code` từ `char_fields` sang `list_fields` (đi qua `_parse_slip_codes`) |
| `…/serializers/…/kiotviet_landing_serializers.py` `OrderRecallInputSerializer` | `max_length` 64 → 1024 (nhiều mã trên 1 chuỗi) |
| `…/serializers/…/order_action_serializers.py:26` | `recall_code` lúc tạo đơn — cùng ngữ nghĩa nhiều mã |
| `apps/orders/export_excel.py:118` | `", ".join(...)` thay cho giá trị đơn |
| `…/views/shipping_lookup_view.py:133` | `recall__recall_code__contains=[code]` |
| `apps/orders/repositories.py:856` | Filter `has_recall_code`: `recall_code__len__gt=0` thay cho `.exclude(recall_code="")` |
| FE `BlockRecall.tsx` | Không đổi kiểu input — vẫn là text nhập nhiều mã cách nhau dấu phẩy, giống ô "Phiếu trả hàng" ngay dưới |

### 3.2. Trường phân loại đơn KOC

**Field mới đặt trên `Order`, không phải `OrderRecall`.** Đơn tặng mẫu và đơn gia hạn
không có bản ghi `OrderRecall`; ép chúng phải tạo một dòng `order_recalls` rỗng chỉ để
lưu nhãn là bóp méo ý nghĩa bảng đó và làm hỏng filter `has_recall_code`.

```python
# apps/orders/models.py
class KocOrderType(models.TextChoices):
    SAMPLE_GIFT = "sample_gift", "Tặng mẫu"
    RENEWAL = "renewal", "Gia hạn"
    RECALL = "recall", "Thu hồi"


# … trong Order
koc_order_type = models.CharField(
    max_length=32, choices=KocOrderType.choices, blank=True, default=""
)
```

`TextChoices` ngay trên model, **không** đi qua `crm_catalog.py`. Lý do: `crm_catalog`
phục vụ các trường của `OrderAssessment` (dữ liệu CRM do sale nhập, thay đổi thường
xuyên theo nghiệp vụ tư vấn); còn đây là thuộc tính của chính `Order`, cùng loại với
`order_type` và `shipping_type` — cả hai đều đang là `TextChoices` trên model. Bám theo
láng giềng gần nhất, và được `ModelSerializer` validate sẵn không phải viết tay.

Rỗng `""` = đơn KOC thường, không phải một option (không có nhãn "Khác" giả).

**Index partial:**

```sql
CREATE INDEX order_koc_type_idx ON orders (koc_order_type) WHERE koc_order_type <> '';
```

Toàn bảng `orders` chỉ một phần nhỏ là đơn KOC, và trong đó chỉ đơn được gán nhãn mới
có giá trị. Index đầy đủ sẽ là low-selectivity trên hàng triệu dòng rỗng — chỉ tốn
write overhead (đúng bài học đã ghi ở comment `models.py:725-728`). Partial index nhỏ,
và mọi query lọc đều có điều kiện `koc_order_type IN (...)` nên dùng được index này.

**Validate** ở tầng serializer: giá trị phải nằm trong catalog hoặc là chuỗi rỗng.

### 3.3. Bộ lọc

Tham số mới `koc_order_type` — **multi-select, ngữ nghĩa OR** (chuỗi phẩy, mirror cách
các filter multi khác của trang đơn đang làm). Chọn một loại là ra ngay danh sách; vẫn
xem được "tặng mẫu + gia hạn" cùng lúc khi cần.

Phải khai báo ở **cả hai** nơi parse filter:

- `…/views/order_views_impl/_view_helpers.py` (list + export)
- `…/views/order_views_impl/sheet3_view.py` — file này có sẵn comment cảnh báo
  `has_recall_code` từng bị bỏ sót ở đây; không lặp lại lỗi đó.

Áp filter ở `apps/orders/repositories.py`, ngay cạnh khối `has_recall_code`.

Filter `has_recall_code` cũ **giữ nguyên**, không thay thế: hai thứ trả lời hai câu hỏi
khác nhau — "đơn được đánh dấu là đơn thu hồi" (nghiệp vụ, có từ lúc lên kế hoạch) và
"đơn đã có mã thu hồi" (tác nghiệp, có sau).

### 3.4. Hiển thị

- **Cột bảng** `koc_order_type` (nhãn "Loại đơn KOC"), thêm vào `ORDER_COLUMN_OPTIONS`
  và vào `RECALL_COLUMN_KEYS` (`frontend/src/features/orders/order-import-format.ts`) —
  nhóm cột này đã được `OrdersPage.tsx:392` và `OrdersColumnPicker.tsx:60` tự động ẩn ở
  trang không phải KOC. Không cần thêm cơ chế ẩn/hiện mới.
- **Inline edit**: select trong khối `BlockRecall.tsx`, đặt trên cùng — nó phân loại cả
  đơn chứ không chỉ phần thu hồi.
- **Bộ lọc**: multi-select trong `OrdersFilterPanel.tsx`, chỉ render khi `isKoc`.
- **Export Excel**: cột xuất do client gửi lên (không có danh sách cột tĩnh ở backend),
  nên chỉ cần thêm một nhánh trong `order_row_values` (`export_excel.py`) xuất **nhãn
  tiếng Việt** — đúng cách `shipping_type` đang làm ở `export_excel.py:86-88`. File
  Excel là để người đọc, không xuất code.
- **i18n**: `frontend/src/i18n/messages/orders.ts`, cả `vi` và `en`.

### 3.5. Không làm (YAGNI)

- Không backfill nhãn từ ghi chú, không đụng vào text ghi chú cũ.
- Không thêm báo cáo/thống kê theo loại đơn KOC ở sprint này — lọc được trước, có số
  liệu thật rồi mới bàn báo cáo.
- Không đổi `recall_code` sang mảng thật trong JSON contract.
- Không thêm ràng buộc "loại = Thu hồi thì bắt buộc có mã".

## 4. Rủi ro & triển khai

**`ALTER COLUMN TYPE` là thay đổi breaking.** Code cũ đọc cột đã thành mảng sẽ vỡ
(`recall_code` trả về list thay vì str) → **migrate và roll code phải đi liền nhau**,
không tách hai nhịp như migration cộng thêm cột. Chạy giờ thấp điểm. Bảng
`order_recalls` nhỏ nên ALTER nhanh; vẫn giữ `lock_timeout = '5s'` để không treo hàng
đợi khoá nếu gặp transaction dài.

**Merge migration leaf**: master vừa có sự cố trùng leaf migration (commit `93c59be9`).
Trước khi push phải `makemigrations --check` và xác nhận chỉ có 1 leaf trong app
`orders`.

**`schema.yaml`**: sửa tay tối thiểu phần bị ảnh hưởng thay vì regen toàn file — file
này đang stale sẵn, regen sẽ kéo nhiễu của feature khác vào MR.

### Runbook triển khai

**`0124` (`ALTER COLUMN TYPE` trên `order_recalls.recall_code`) là breaking, không chỉ
"hiển thị sai".** Code CŨ chạy trên schema MỚI (cột đã thành `varchar(64)[]`) sẽ
**500 ở hot path**, không phải chỉ trả sai dữ liệu:
- Bộ lọc `has_recall_code` (`repositories.py`) trước nhánh này dùng
  `.exclude(recall_code="")` — so sánh mảng với chuỗi rỗng ném `ProgrammingError`
  ngay khi ai bật bộ lọc "Mã đơn thu hồi" trên trang Đơn hàng/KOC.
- `shipping_lookup_view.py` (màn quét mã của kho) trước nhánh này dùng
  `filter(recall__recall_code=code)` — so sánh mảng với chuỗi cũng ném
  `ProgrammingError` → 500 ngay lần quét đầu tiên sau khi DB đã migrate nhưng code
  chưa roll.

⇒ **migrate DB và roll code phải cùng một nhịp**, chạy giờ thấp điểm, **không rolling
deploy để hai version code cùng sống song song** (không có bước "migrate trước, code
sau" an toàn như các migration chỉ cộng thêm cột).

**Rollback**: revert code **và** chạy `migrate orders 0123` (không lùi một mình migration
mà giữ code mới — code mới đọc `recall_code` như list, chạy trên cột đã lùi về
`varchar(64)` sẽ vỡ ngược lại).

**Cảnh báo mất dữ liệu khi rollback**: `REVERSE_SQL` của `0124` gộp mảng lại bằng
`array_to_string(recall_code, ', ')` rồi cắt còn 64 ký tự
(`left(..., 64)`). Một đơn đã nhập 2-3 mã thu hồi (mỗi mã dạng `RC-xxxxxxxx` dài
~11 ký tự, đã gần sát 64 ký tự với 2 mã) sẽ bị cắt cụt **vĩnh viễn** — phần mã bị cắt
không khôi phục lại được từ chuỗi đã cắt. Nếu tại thời điểm cần rollback đã có đơn
nhập nhiều hơn 1 mã, **dump bảng `order_recalls` trước khi lùi migration**.

**`0126` (`CREATE INDEX CONCURRENTLY` cho `koc_order_type`)** không chạy trong
transaction (`atomic = False`). Hỏng giữa chừng (mất kết nối, huỷ query,...) để lại
index ở trạng thái `INVALID` — Postgres không tự dọn. Khắc phục:
```sql
DROP INDEX IF EXISTS order_koc_type_created_idx;
```
rồi chạy lại `migrate orders 0126`.

## 5. Kiểm thử

| Tầng | Nội dung |
|---|---|
| BE — `tests/test_order_recall.py` | Ghi/đọc nhiều mã thu hồi; chuỗi có khoảng trắng thừa và phần tử rỗng bị chuẩn hoá; PATCH không kèm `recall_code` không xoá dữ liệu cũ |
| BE — `tests/test_shipping_lookup.py` | Tra cứu trúng mã thu hồi thứ 2, thứ 3 trong mảng |
| BE — `tests/test_order_list_filters_api.py` | Lọc `koc_order_type` 1 giá trị, nhiều giá trị (OR), giá trị rác bị bỏ qua; `has_recall_code` vẫn đúng sau khi đổi kiểu |
| BE — `tests/test_orders_export_definition.py` | Cột mã thu hồi xuất đủ N mã; cột "Loại đơn KOC" xuất nhãn tiếng Việt |
| FE | Payload gửi lên đúng; filter lưu/nạp theo scope KOC; cột chỉ hiện ở trang KOC |

Không tạo file test rời rạc cho từng thay đổi nhỏ — bám vào các file test theo cấu trúc
sẵn có ở trên.

## 6. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 | BE: `recall_code` → ArrayField + migration + GIN index + sửa 8 điểm đọc/ghi + test | ✅ Xong |
| 2 | BE: `koc_order_type` + catalog + migration partial index + filter (2 nơi parse) + export + test | ✅ Xong |
| 3 | FE: types, cột bảng, inline select, bộ lọc, i18n, test | ✅ Xong |
| 4 | `schema.yaml`, rà migration leaf, chạy lint + test, mở MR | ✅ Xong (schema + suite; còn nợ push + mở MR — controller làm sau khi review toàn nhánh) |

Sprint 1 và 2 độc lập (khác field, khác file trọng tâm, chỉ chạm chung `models.py` và
`repositories.py`) nên chạy song song được. Sprint 3 phụ thuộc contract của cả hai.

**Đã làm ở Sprint 4:** xác nhận migration `orders` chỉ có một leaf nối tiếp
(`0124_orderrecall_recall_code_array` → `0125_order_koc_order_type`), `makemigrations
--check` sạch; sửa tay `backend/schema.yaml` (thêm `KocOrderTypeEnum` + field
`koc_order_type` vào `Order`/`OrderList`/`PatchedOrderInlineUpdate`, nới `recall_code`
từ maxLength 64 lên 1024 ở `OrderCreate`/`OrderRecallInput`); chạy đủ suite backend
`apps/orders` (1303 passed, 25 subtests) và suite frontend `test:ci` (148 file, 1139
test) — cả hai xanh 100%, không có test đỏ để phân tích baseline.

**Còn nợ:** `git push` + mở MR (Step 6-7 của task-7, cố ý để controller làm sau khi
review toàn nhánh); ghi chú vận hành migrate/roll-code liền nhau trong mô tả MR khi mở.
