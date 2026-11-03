# Khôi phục đơn đã hủy: chuyển từ UPDATE in-place sang tạo đơn mới (clone)

- **Ngày:** 2026-07-28
- **Nhánh:** `feat/restore-order-clone`
- **Phạm vi:** backend `apps/orders` (task + helper + model), frontend chỉ sửa comment/docstring

## 1. Bối cảnh

Hiện tại `process_restore_orders`
(`backend/apps/orders/infrastructure/tasks/order_tasks_impl/restore_tasks.py`)
khôi phục đơn bằng cách **UPDATE in-place** chính bản ghi đã hủy: reset
`status`, `invoice_*`, `old_*`, `order_code`, `shipping_*` rồi upsert
`OrderAssessment` về `sales_status_code="closed"`. Đơn giữ nguyên `id`, và mọi
dấu vết hóa đơn KiotViet đã hủy bị xóa sạch — không truy vết ngược được.

Luồng clone từng tồn tại (commit `38bb5a77`, view đồng bộ) và bị thay bằng
in-place khi chuyển sang chạy nền (commit `5e949ccb`). Helper
`OrderService.duplicate_order` vẫn còn nguyên trong
`backend/apps/orders/application/services/order_service_helpers.py:30` với đầy
đủ cờ phục vụ khôi phục (`preserve_source`, `reset_progress`,
`inherit_created_at`, `default_sales_status`).

**Yêu cầu:** khôi phục phải tạo một đơn **mới**, phần logic còn lại giữ nguyên.

## 2. Quyết định thiết kế

| Vấn đề | Quyết định |
|---|---|
| Đơn cũ sau khi khôi phục | **Giữ nguyên**, không sửa một field nào. Vẫn `invoice_status=2`, vẫn nằm tab "Đã hủy" làm lịch sử |
| `created_at` đơn mới | **Kế thừa** đơn gốc (`inherit_created_at=True`) — báo cáo theo ngày không xê dịch |
| Khôi phục 2 lần | **Chặn** bằng field mới `Order.restored_from`; đơn gốc đã có bản khôi phục còn sống thì vào `already_restored_ids` (tách riêng khỏi `skipped_ids`, xem §5) |
| Realtime | **Không** bắn `order.created` cho từng đơn clone; chỉ giữ 1 event `restore.completed` cuối batch |
| Nơi đặt logic clone | **Tái dùng** `OrderService.duplicate_order`, mở rộng tham số — không viết bản clone thứ hai |
| Kiến trúc chạy nền | Giữ nguyên: view enqueue → Celery task → event cuối batch |

### Quyết định bổ sung (chốt sau final review ngày 2026-07-29)

Sau khi review bản đầu, chủ dự án chốt thêm 4 điểm — đã áp dụng, không còn là đề xuất:

| Vấn đề | Quyết định |
|---|---|
| `is_export_vat` / `vat_link` khi clone | **Không copy.** Đây là marker "đã xuất hóa đơn VAT rồi" và link trỏ tới hóa đơn của đơn KHÁC (đơn gốc) — copy sang khiến kế toán tưởng đơn mới đã xuất VAT trong khi chưa hề xuất. Vẫn copy `vat` (hình thức xuất) |
| `staff` khi `reset_progress=True` | **Luôn giữ** đơn gốc, không còn đặt `None`. Record scope OWN lọc theo `staff_id`; đặt rỗng khiến sale không còn thấy đơn mình vừa khôi phục |
| `branch_id` khi `reset_progress=True` | **Luôn giữ** đơn gốc, không còn đặt `""`. Đặt rỗng khiến đơn của chi nhánh nội bộ (vốn bị `OVERVIEW_EXCLUDED_BRANCH_IDS` loại khỏi báo cáo) lọt ngược vào báo cáo công ty sau khi khôi phục |
| Đơn hủy có bản khôi phục còn sống, trong `summary_overview` | **Loại khỏi base.** Clone kế thừa `created_at` đơn gốc nên rơi vào cùng kỳ báo cáo với đơn gốc → đếm hai lần vào `gross_revenue` nếu không loại. Chỉ áp cho `summary_overview`, không đụng chip đếm tab |

## 3. Thay đổi model

`backend/apps/orders/models.py` — thêm vào `Order`:

```python
restored_from = models.ForeignKey(
    "self", on_delete=models.SET_NULL, null=True, blank=True,
    related_name="restored_orders", db_index=False,
)
```

kèm partial index tường minh trong `Meta.indexes`:

```python
models.Index(
    fields=["restored_from"],
    condition=Q(restored_from__isnull=False),
    name="ord_restored_from_idx",
)
```

`db_index=False` + index tự đặt tên: tập đơn có `restored_from IS NOT NULL` rất
nhỏ so với toàn bảng `orders`, partial index gọn hơn index đầy đủ mà Django sinh
tự động, và tên index nằm trong tầm kiểm soát khi cần thao tác trên prod.

Migration mới `0111_order_restored_from` (migration mới nhất hiện tại là
`0110_backfill_age_group`), theo pattern bảng lớn của repo: `atomic = False`,
`SeparateDatabaseAndState`, `SET LOCAL lock_timeout`, `CREATE INDEX CONCURRENTLY`,
FK thêm dạng `NOT VALID` rồi `VALIDATE CONSTRAINT` riêng.

Bước `ADD CONSTRAINT` được bọc trong `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM
pg_constraint WHERE conname = 'orders_restored_from_id_fk') THEN ... END IF;
END $$;` (bổ sung sau final review) để migration **re-runnable** nếu lần chạy
trước lỗi giữa chừng: Postgres không có cú pháp `ADD CONSTRAINT IF NOT EXISTS`,
và migration này `atomic = False` nên một lỗi ở bước `VALIDATE CONSTRAINT`
(bước sau) để lại `ADD CONSTRAINT` (bước trước) đã áp dụng thật trên DB — chạy
lại migration từ đầu mà không có DO-block sẽ ăn lỗi "constraint already exists".

`SET_NULL` chứ không `CASCADE`: xóa đơn hủy cũ không được kéo theo đơn đang bán.

## 4. Thay đổi helper `duplicate_order`

Ba tham số mới, default giữ **nguyên** hành vi cho endpoint
`POST /api/orders/<id>/duplicate`:

| Tham số | Kiểu | Default | Ý nghĩa |
|---|---|---|---|
| `show_orders` | `bool \| None` | `None` | `None` = giữ logic cũ (`reset_progress` ⇒ `False`, ngược lại theo đơn gốc). Có giá trị = override thẳng |
| `emit_event` | `bool` | `True` | `False` = không push `order.created` |
| `restored_from` | `Order \| None` | `None` | Gán vào `create_kwargs["restored_from"]` |

Bổ sung vào `create_kwargs` (copy **vô điều kiện**, áp dụng cho cả endpoint
duplicate vì giữ các thuộc tính này khi nhân bản đơn cũng là hành vi đúng):

`payment_method`, `vat`, `shipping_form`, `outside`, `has_address`,
`product_raw`.

Lý do: bản in-place hiện tại **giữ** các field này (UPDATE chỉ reset marker
hủy), còn bản clone cũ làm mất chúng. Nghiêm trọng nhất là `outside` — đơn gửi
ngoài khôi phục xong sẽ biến mất khỏi trang "Đơn gửi ngoài", chính là nơi có
nút khôi phục.

**Không** copy `is_export_vat` / `vat_link` (quyết định bổ sung sau final
review, xem mục ở §2) — giữ default model (`False` / `""`). Bản đầu của spec
này từng liệt kê hai field trên vào nhóm copy vô điều kiện; đã sửa lại vì
đây là marker "đã xuất hóa đơn VAT của đơn KHÁC", copy sang là sai nghiệp vụ.

`staff` và `branch_id` **luôn** copy từ đơn gốc, kể cả khi `reset_progress=True`
— không còn là field bị `reset_progress` xoá nữa (xem quyết định bổ sung ở §2).
`shipping_fee` thì ngược lại: reset về `0` khi `reset_progress=True`, gate nằm
trong helper (`shipping_fee=0 if reset_progress else order.shipping_fee`),
không phải một UPDATE riêng sau khi tạo đơn trong task như cách làm ban đầu —
đơn khôi phục cần "sạch" phí ship để KiotViet tính lại từ đầu khi dispatch,
cùng nhóm lý do với `shipping_code` (luôn xoá).

**Không** copy: `fulfillment_warehouse`, `external_order_id`, `shipping_code`,
`order_code`, `invoice_code`, `invoice_id`, `invoice_status`,
`old_invoice_code`, `old_id`, `reattached_invoice_codes`, `is_export_vat`,
`vat_link`. Đơn mới bắt đầu sạch để KiotViet push lại từ đầu; kho xuất được
gán lại khi dispatch.

## 5. Thay đổi task `process_restore_orders`

Chữ ký, tên task (`apps.orders.tasks.process_restore_orders`), `soft_time_limit=300`,
`time_limit=360` giữ nguyên → không đụng `apps/dispatch/registry.py`, không đụng
view, không đụng frontend.

```
với mỗi order_id:
  transaction.atomic:
    order = Order.objects.select_for_update()
                .select_related("channel")
                .filter(pk=order_id, deleted_at__isnull=True).first()
    skip (→ skipped_ids) nếu order is None
    skip (→ skipped_ids) nếu order.invoice_status != 2
    skip (→ already_restored_ids) nếu order.restored_orders.filter(deleted_at__isnull=True).exists()

    new_order = OrderService.duplicate_order(
        order, actor,
        preserve_source=True,
        reset_progress=True,
        inherit_created_at=True,
        default_sales_status="closed",
        show_orders=bool(restore_show_orders),
        emit_event=False,
        restored_from=order,
    )
  restored_ids.append(new_order.id)

cuối batch: _broadcast_restore_completed(
    user_id=user_id, restored_ids=restored_ids,
    skipped_ids=skipped_ids, already_restored_ids=already_restored_ids,
)
```

Bỏ hoàn toàn: khối `Order.objects.filter(pk=order_id).update(...)`, khối upsert
`OrderAssessment` (helper đã lo qua `default_sales_status`), và event
`order.updated` từng đơn. Tham số `event_source` không truyền nữa (chỉ
`emit_event=False` nên helper không dùng tới; giá trị mặc định của helper là
đủ, không cần override cho luồng restore).

Giữ nguyên: điều kiện `invoice_status == 2`, `select_for_update`, try/except
từng đơn (lỗi một đơn → `skipped_ids`, không giết cả batch), log start/finish.

`restored_ids` (trong event và trong return value) đổi ý nghĩa: **id đơn mới**
thay vì id đơn cũ.

**Thay đổi so với bản đầu:** đơn hủy "đã có bản khôi phục còn sống" (bấm nút
khôi phục lần hai) giờ vào `already_restored_ids` / `already_restored_count`
riêng, KHÔNG còn gộp chung vào `skipped_ids`. Lý do: đây là thao tác người
dùng thường gặp (đơn hủy vẫn nằm tab "Đã hủy" và vẫn chọn được), khác về bản
chất với "đơn không tồn tại/chưa hủy" — payload `restore.completed` và giá trị
trả về của task đều có thêm 2 khóa này. Frontend (`use-crm-websocket.ts`) hiện
đọc riêng `already_restored_count` để hiện một dòng toast tách biệt ("N đơn đã
khôi phục trước đó") thay vì gộp vào "N đơn không hợp lệ".

**Chống race:** `select_for_update` giữ lock trên đơn gốc suốt transaction, và
điều kiện chống trùng đọc `restored_orders` bên trong cùng transaction đó → hai
request đồng thời không thể cùng tạo hai bản khôi phục.

**Chi phí:** mỗi đơn thêm ~4 query (đọc items, bulk_create items, create
assessment, update `created_at`). Batch 200 đơn ≈ 1.400 query trong task nền —
chấp nhận được trong giới hạn 300s.

## 6. Frontend

Ban đầu dự kiến chỉ sửa 2 chỗ chú thích đã sai; sau final review có thêm một
thay đổi hành vi thật ở `use-crm-websocket.ts` (đi cùng payload
`already_restored_ids` mới ở §5):

- `frontend/src/hooks/use-crm-websocket.ts:138` — comment "row đã cập nhật biến
  mất khỏi tab Đã hủy (vì invoice_status giờ là NULL)" không còn đúng: đơn cũ
  vẫn nằm nguyên ở tab "Đã hủy", đơn mới xuất hiện ở danh sách đơn. Lệnh
  `loadOrders(force)` vẫn cần giữ.
- Cùng file, handler `restore.completed` đọc thêm `already_restored_count` từ
  payload và hiện riêng một dòng toast ("N đơn đã khôi phục trước đó") thay vì
  gộp chung với "N đơn không hợp lệ (bỏ qua)" như trước — khớp với việc backend
  tách `already_restored_ids` khỏi `skipped_ids`.
- Docstring view `restore_cancelled_orders`
  (`order_action_view.py:294`) — đang ghi "UPDATE in-place", sửa thành tạo đơn
  mới; kéo theo `frontend/src/api/schema.d.ts` khi regenerate schema.

Ngữ nghĩa `restore_show_orders` giữ nguyên: `true` khi khôi phục từ trang đơn
thường / KOC / gửi ngoài, `false` từ tab "Đã hủy" của trang Landing
(`frontend/src/app/routed-pages.tsx:256,350`).

## 7. Test

File mới `backend/apps/orders/tests/test_restore_cancelled_orders.py`, gọi
thẳng `process_restore_orders.run(...)`:

1. Clone tạo đơn mới; đơn cũ giữ nguyên `invoice_status=2`, `invoice_code`, `order_code`
2. Items sao chép đủ (số lượng, giá, product); assessment đơn mới có `sales_status_code="closed"`
3. `created_at` đơn mới bằng `created_at` đơn gốc
4. `show_orders` bám theo `restore_show_orders` — kiểm cả `True` lẫn `False`
5. `restored_from` trỏ đúng đơn gốc
6. Chạy task 2 lần trên cùng đơn → lần 2 vào `already_restored_ids` (không phải
   `skipped_ids`), tổng số đơn không tăng
7. Đơn `invoice_status != 2` → `skipped_ids`, không tạo gì
8. Không có event `order.created` nào được phát (patch `OrderServiceHelpers.push_event`)
9. Các field mới copy đúng: `outside`, `vat`, `payment_method`, `shipping_form`,
   `has_address`, `staff`, `branch_id`; và ngược lại `is_export_vat`/`vat_link`
   **không** copy (về default model)
10. Đơn không tồn tại / đã soft-delete → `skipped_ids`
11. `_broadcast_restore_completed` nhận đúng id đơn mới, không phải id đơn gốc

Thêm test cho `duplicate_order` ở default (luồng endpoint `/duplicate`) để chốt
rằng nhóm field mới copy không phá hành vi cũ.

Test hiện có `test_order_bulk_action_permissions.py::RestoreCancelledOrdersScopeTests`
mock `dispatch` ở tầng view nên không bị ảnh hưởng.

Lệnh chạy: pytest trong container `desi-backend-test:latest` trên network
`desi-test-net`, `POSTGRES_DB` riêng cho worktree này, giới hạn ở
`apps/orders`. Lint bắt buộc: `ruff check .` theo `backend/pyproject.toml`.

## 8. Rủi ro

| Rủi ro | Mức | Xử lý |
|---|---|---|
| Thêm field copy làm đổi hành vi endpoint `/duplicate` | Thấp | Có chủ ý; khóa bằng test mới. Endpoint hiện không có test assert nội dung clone |
| Batch lớn chạy lâu hơn in-place (nhiều query hơn) | Thấp | Vẫn trong `soft_time_limit=300`; batch thực tế vài chục đơn |
| Người dùng khôi phục nhầm rồi khôi phục lại | Thấp | `restored_from` chặn; muốn tạo bản thứ hai phải xóa bản khôi phục trước |

**Đã xử lý, không còn là rủi ro chấp nhận:** bản đầu của spec này liệt kê "đơn
cũ và đơn mới cùng tồn tại → báo cáo đếm 2 lần" ở mức Trung bình và coi là hành
vi chấp nhận được. Sau final review phát hiện đây thực sự là bug ở
`summary_overview` (clone kế thừa `created_at` đơn gốc nên rơi vào cùng kỳ báo
cáo, cộng dồn `gross_revenue` — mẫu số của mọi ô %) — đã sửa bằng cách loại đơn
hủy có bản khôi phục còn sống khỏi base của `summary_overview` (xem quyết định
bổ sung ở §2, chi tiết implement ở §4/§5 tương ứng file
`backend/apps/orders/_repositories_reports.py`). Chip đếm tab (`repositories.py`
`status_counts_for`) không đổi — đơn hủy vẫn hiển thị bình thường ở tab "Đã hủy".

## 9. Ghi chú vận hành khi deploy migration 0111

- Chạy off-peak: `CREATE INDEX CONCURRENTLY` và `VALIDATE CONSTRAINT` quét toàn
  bảng `orders` (~7GB prod), tuy không khóa DML nhưng tốn I/O đáng kể.
- Sau khi chạy, kiểm tra `pg_index.indisvalid` cho index `ord_restored_from_idx`.
  Nếu `CREATE INDEX CONCURRENTLY` bị ngắt giữa chừng (mất kết nối, deploy bị
  huỷ), Postgres để lại index ở trạng thái **invalid** nhưng vẫn tồn tại — lần
  chạy migration sau đó, mệnh đề `IF NOT EXISTS` trong SQL sẽ bỏ qua âm thầm
  (không báo lỗi, không tự sửa), và query vẫn planner-visible nhưng không được
  dùng. Phải `DROP INDEX` rồi tạo lại thủ công nếu phát hiện `indisvalid = false`.
- `VALIDATE CONSTRAINT orders_restored_from_id_fk` giữ lock `SHARE UPDATE
  EXCLUSIVE` trên `orders` trong suốt thời gian quét: DML (SELECT/INSERT/UPDATE/
  DELETE) không bị chặn, nhưng không được chạy song song các thao tác DDL khác
  hoặc `VACUUM FULL` trên bảng `orders` trong lúc migration đang chạy bước này.

## 10. Ngoài phạm vi

- Không thêm UI hiển thị liên kết đơn mới ↔ đơn hủy (field `restored_from` mới
  chỉ phục vụ chống trùng; đưa lên UI là việc sau nếu cần).
- Không backfill `restored_from` cho các đơn đã khôi phục bằng in-place trước
  đây — dữ liệu cũ đã bị ghi đè, không dựng lại được.

## 8. Cập nhật 2026-08-03 — tách `failed_ids` + nhận đơn hủy POS

Rút kinh nghiệm sự cố 2026-08-03 (worker chạy image cũ, `NotNullViolation` khi
clone bị toast thành "2 đơn không hợp lệ" làm chẩn đoán lạc hướng):

- **Điều kiện nhận đơn** mở rộng theo đúng `CANCELLED_ORDER_Q` của tab "Đã hủy":
  `invoice_status=2` HOẶC `status=6` (đơn hủy POS/CRM không có hóa đơn). Trước
  đây chỉ nhận `invoice_status=2` nên đơn hủy POS chọn được trên tab nhưng luôn
  bị báo "không hợp lệ".
- **Payload/return tách 3 loại**: `skipped_ids` (dữ liệu không đủ điều kiện),
  `already_restored_ids` (đã có bản khôi phục), `failed_ids` (exception khi
  clone — lỗi hệ thống, cần xem log worker). FE toast đọc `failed_count` và
  hiện "N đơn gặp lỗi hệ thống, vui lòng thử lại" (toast.error khi không đơn
  nào khôi phục được).
