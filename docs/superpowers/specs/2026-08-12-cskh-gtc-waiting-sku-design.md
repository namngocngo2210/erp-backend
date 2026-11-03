# Danh sách contact CSKH: bộ đếm chờ theo mốc GTC + cột SKU/giá đơn + lọc sản phẩm

Ngày: 2026-08-12
Trang liên quan: `Danh sách contact` (CSKH / Sale) — `GET /api/sale/contacts`
Thiết kế nền: `docs/sale-contacts-page-design.md`

## 1. Vấn đề

Cột **Thời gian chờ** hiện đếm từ `max(order.created_at, last_activity_at)`
([contact_list_service.py:305-307](../../../backend/apps/orders/application/services/contact_list_service.py)).
Với nhóm **Chăm sóc sau bán** (đơn đã Giao thành công — GTC), con số này là "bao lâu kể từ
ngày data về", không phải "bao lâu kể từ khi khách nhận hàng" — đúng thứ CSKH cần để biết
đã trễ chăm sóc bao lâu.

Ngoài ra CSKH gọi khách mà không thấy khách đã mua **SKU nào** với **giá bao nhiêu**, và
không lọc được danh sách theo sản phẩm.

## 2. Phạm vi

| Hạng mục | Quyết định |
|---|---|
| Bộ đếm theo mốc GTC | **Chỉ** áp dụng cho dòng có đơn mới nhất `status = DELIVERED_SUCCESS (3)`. Các nhóm khác giữ nguyên logic cũ. |
| Đơn GTC không có journey event | Fallback `Order.updated_at` (cùng convention trang Theo dõi mua lại, `customer_resale_service.py:342`). Không backfill trong MR này. |
| Giá đơn hiển thị | `Order.total_after_discount` — tiền khách thực trả. |
| Cột SKU | SKU đầu tiên + badge `+N`, `title` ra danh sách đầy đủ. |
| Bộ lọc SKU | MultiSelect **chọn sản phẩm** theo `product_id` (đúng pattern các trang khác), không phải ô free-text. |

Ngoài phạm vi: backfill mốc GTC cho đơn cũ; xuất Excel các cột mới; sắp xếp theo cột mới.

## 3. Backend

### 3.1 Mốc GTC (`gtc_at`)

Nguồn chuẩn: `OrderJourneyEvent(event_type="order_status_changed", to_code="3")` — sinh bởi
`apps/orders/subscribers.py`, nghe thẳng `OrderStatusChanged` nên phủ cả đường KiotViet
webhook lẫn cron đối soát.

```python
# _gtc_at_map(page_ids) — 1 query, chỉ trên ≤100 order của TRANG hiện tại
OrderJourneyEvent.objects
    .filter(order_id__in=page_ids, event_type=STATUS_CHANGED_EVENT, to_code=str(OrderStatus.DELIVERED_SUCCESS))
    .values("order_id")
    .annotate(last=Max("created_at"))
```

- Lấy `Max` chứ không `Min`: đơn có thể GTC → hoàn → GTC lại; bộ đếm phải tính từ **lần
  chuyển GTC đang hiệu lực**.
- Index `ord_journey_ord_type_cr_idx (order, event_type, created_at)` phủ đúng truy vấn này.
- Query đặt trong `_build_items` (sau phân trang) → **không** ảnh hưởng đường tính
  `action_counts` / `operation_counts` vốn chạy trên toàn tập kết quả.
- Không có event → `order.updated_at`.

### 3.2 `waiting_since` và `waiting_basis`

Trong `_build_items`:

```
nếu order.status == DELIVERED_SUCCESS:
    waiting_since = gtc_at_map.get(order.id) hoặc order.updated_at
    waiting_basis = "gtc"
ngược lại:
    waiting_since = max(created_at, last_activity_at)   # như cũ
    waiting_basis = "activity"
```

Giữ nguyên quy tắc cũ: `action_needed == "none"` → `waiting_since = None`.
(Đơn `status = 3` luôn cho `action_needed = "post_sale"` theo `derive_action_needed`, nên
hai nhánh không xung đột.)

`waiting_basis` là field MỚI trong response. Lý do phải có: cùng một cột nhưng hai dòng có
thể mang hai ngữ nghĩa khác nhau — FE cần biết để render tooltip đúng, nếu không người dùng
không có cách nào phân biệt.

### 3.3 SKU + giá đơn trong `latest_order`

Hai field mới:

- `items: [{sku, product_name, quantity}]` — 1 query
  `OrderItem.objects.filter(order_id__in=page_ids)`, dùng index `order_item_order_cr_idx`.
  Đơn không có `OrderItem` (đơn sàn / import cũ) → fallback tách chuỗi gộp `Order.sku`
  thành list (`Order.sku` là chuỗi join SKU của mọi dòng).
- `total_after_discount: Decimal` — đọc thẳng từ `Order` đã `select_related` sẵn.

### 3.4 Lọc theo sản phẩm

Query param `product` (multi-value, id sản phẩm — cùng convention trang Đơn hàng).

Áp trong `_base_queryset`, **trước** `DISTINCT ON`:

```python
base = base.filter(
    Exists(OrderItem.objects.filter(order_id=OuterRef("pk"), product_id__in=product_ids))
)
```

Bám pattern đã tối ưu ở `apps/orders/repositories.py:603-612` (match bằng `product_id__in`
trên index `order_item_product_idx`, KHÔNG dùng `icontains` trên ~855k dòng `order_items`).

**Ngữ nghĩa cần ghi rõ**: danh sách là 1 dòng/khách theo đơn mới nhất. Vì bộ lọc đứng trước
`DISTINCT ON`, "đơn mới nhất" trở thành "đơn mới nhất **có chứa** sản phẩm đã chọn". Đây là
hành vi mong muốn: lọc SP X phải thấy đơn gần nhất mua X, không phải đơn gần nhất mua thứ khác.

### 3.5 Transport

- `contact_list_view.py`: thêm `OpenApiParameter("product", int, many=True)`, parse bằng
  `_parse_int_list` sẵn có, truyền `product_ids` xuống service.
- `contact_serializers.py`: `ContactItemSerializer` thêm `waiting_basis`;
  `ContactLatestOrderSerializer` thêm `items` (nested) + `total_after_discount`.

## 4. Frontend

### 4.1 Bảng

`ContactsTable` từ 12 → 14 cột, `min-w-[1320px]` → `min-w-[1560px]`. Hai cột mới chèn sau
**Ngày data về**, trước **Loại khách**:

| Cột | Nội dung |
|---|---|
| SKU | SKU đầu tiên; còn dư thì badge `+N`. `title` = toàn bộ `sku × quantity`. Không có SKU → `—`. |
| Giá đơn | `total_after_discount` format tiền VN. |

Ô **Thời gian chờ**: giữ `humanizeWaiting` + `waitingUrgencyClass`; thêm `title` theo
`waiting_basis` — `"gtc"` → "Chờ từ lúc giao thành công {dd/MM HH:mm}", `"activity"` →
"Chờ từ {dd/MM HH:mm}".

### 4.2 Bộ lọc

`ContactsFilterBar` thêm `MultiSelect` sản phẩm (`@/components/ui/multi-select`,
`label = sku`, `keywords = name`, `menuPortal`) — cùng component và cùng nguồn dữ liệu
`GET /api/orders/products?limit=1000&offset=0` như `DispatchHistoryPage`.

`ContactsPage`: state `productIds: string[]`, đưa vào `filterKey` (đổi lọc → về trang 1 ngay
trong cùng render, theo cơ chế neo trang sẵn có) và vào `listParams.product`. `resetFilters`
xoá luôn.

`lib/api/contacts.ts`: `ContactListParams` thêm `product?: number[]`, append vào query string.

## 5. Test

Backend — mở rộng `backend/apps/orders/tests/test_contact_list.py`:

1. Đơn GTC có journey event → `waiting_since` = mốc event, `waiting_basis = "gtc"`.
2. Đơn GTC không có event → `waiting_since` = `updated_at`.
3. Đơn GTC → hoàn → GTC lại → lấy mốc GTC **gần nhất**.
4. Đơn chưa GTC → giữ nguyên `max(created_at, last_activity_at)`, `waiting_basis = "activity"`.
5. `latest_order.items` đúng SKU/số lượng; đơn không có `OrderItem` → fallback `Order.sku`.
6. Lọc `product=<id>` → chỉ còn khách có đơn chứa SP đó, và dòng trả về là đơn mới nhất
   **có chứa** SP đó.

Frontend — mở rộng `frontend/src/features/contacts/__tests__/contact-cells.test.tsx`:
ô SKU render `+N` khi nhiều dòng; tooltip Thời gian chờ đổi theo `waiting_basis`.

## 6. Rủi ro đã biết

- **Type FE sinh từ OpenAPI**: `lib/api/contacts.ts` derive type từ `@/api/typed`, phải chạy
  `npm run gen:api`. `schema.yaml` trên master đang cũ ~444 dòng → regen kéo theo nhiễu của
  feature khác. Xử lý: chỉ giữ hunk liên quan contacts trong diff, phần nợ còn lại để MR chore riêng.
- **Fallback `updated_at` không chính xác tuyệt đối**: đơn GTC trước 23/07/2026 mà sau đó bị
  sửa (đổi ghi chú, gán lại sale) sẽ có `updated_at` muộn hơn mốc GTC thật → bộ đếm ngắn hơn
  thực tế. Chấp nhận trong MR này; muốn sạch thì cần một command backfill riêng.
- **Đơn không có `OrderItem`**: đơn sàn/import cũ chỉ có chuỗi `Order.sku`, không có
  `product_id` → **không lọc được** bằng bộ lọc sản phẩm. Đúng như trang Đơn hàng hiện tại;
  không mở rộng trong MR này.
