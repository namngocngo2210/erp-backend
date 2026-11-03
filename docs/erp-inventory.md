# Spec — Module Inventory (Phase 1)

> Spec triển khai module `inventory` trong `docs/erp-master-plan.md` (Phase 1 — xương sống ERP).
> Phủ toàn bộ MVP (Sprint 1.1→1.4). Mỗi sprint có plan triển khai riêng.
>
> - Ngày tạo: 2026-06-11
> - Chuẩn BE: `docs/architecture-standard.md` (clean architecture + luật ranh giới module)
> - Phụ thuộc: app-shell (`docs/erp-app-shell.md`) cho FE module/section; event bus
>   (`backend/apps/common/events.py`) cho cảnh báo tồn âm.

---

## 1. Mục tiêu & Quyết định

**Mục tiêu:** desi-order trở thành **nguồn chân lý** của tồn kho đa kho, với sổ kho
immutable và tự trừ kho khi xác nhận đơn.

**Quyết định đã chốt:**
| # | Quyết định |
|---|---|
| I1 | **desi-order authoritative** cho tồn kho (KiotViet chỉ là kênh bán). |
| I2 | Nguồn chân lý = **sổ kho immutable `StockMovement`**; `StockItem` là projection số dư đọc nhanh. |
| I3 | Phase 1 **chỉ quản lý số lượng**, CHƯA giá vốn (bổ sung ở Phase 2 Kế toán). |
| I4 | Khi confirm đơn: **chọn kho thủ công** trên đơn (`fulfillment_warehouse`). |
| I5 | Tồn không đủ: **cho phép âm + cảnh báo**, KHÔNG chặn confirm. |
| I6 | Tồn kho đổi CHỈ qua **post phiếu** hoặc **sale từ đơn**; sửa sai = ghi movement đảo, không xoá. |

**Phi mục tiêu (Phase 1):** giá vốn/FIFO; đồng bộ tồn từ KiotViet; reservation giữ chỗ
nhiều bước; dời catalog Product sang inventory (xem §2.2).

---

## 2. Kiến trúc

### 2.1 App mới `apps/inventory/` (DDD chuẩn)
```
apps/inventory/
├── domain/              # quy tắc thuần: tính balance_after, phân loại movement_type
├── application/services/ # StockCommandService (post phiếu, issue_for_order), StockQueryService
├── contracts/           # interface StockCommandService + domain events inventory
├── infrastructure/      # (nếu cần) — Phase 1 hầu như không có I/O ngoài
├── transport/{views,serializers}/
├── repositories.py      # StockMovementRepository, StockItemRepository, StockDocumentRepository
├── models.py
├── migrations/
└── tests/
```
Thuộc cùng **module nghiệp vụ `inventory`** với `apps/warehouses` → FK tới
`warehouses.Warehouse` là FK trong-cùng-module (hợp lệ).

### 2.2 Thoả hiệp ranh giới: Product tạm ở `apps/orders`
Product/ProductCategory/ProductGroup hiện ở `apps/orders/models.py`. Dời sang inventory là
migration lớn, rủi ro (orders phụ thuộc nặng). **Phase 1 giữ nguyên**, inventory FK tới
`orders.Product`. Ghi nhận đây là **thoả hiệp ranh giới có chủ đích** — task "dời catalog vào
inventory" để riêng sau. (Đây là ngoại lệ duy nhất với luật cấm import ORM chéo module, được
ghi rõ ở đây để không lặp lại tuỳ tiện.)

---

## 3. Mô hình dữ liệu

### 3.1 `StockMovement` — sổ kho append-only (nguồn chân lý)
| Field | Kiểu | Ghi chú |
|---|---|---|
| id | PK | |
| warehouse | FK warehouses.Warehouse (PROTECT) | |
| product | FK orders.Product (PROTECT) | |
| qty_delta | IntegerField | +nhập / −xuất (≠ 0) |
| movement_type | CharField choices | receipt, issue, adjustment, transfer_in, transfer_out, sale, sale_reversal |
| balance_after | IntegerField | số dư kho×sp NGAY SAU movement này (snapshot audit) |
| reference_type | CharField null | "document" \| "order" |
| reference_id | BigInteger null | id phiếu / đơn |
| note | TextField blank | |
| created_by | FK users null (SET_NULL) | |
| created_at | DateTime auto_now_add | |

- **Append-only**: không `update`/`delete`. Repository chỉ expose `create` + truy vấn.
- Index: `(warehouse, product, created_at)` để dựng lịch sử + tính số dư.

### 3.2 `StockItem` — số dư hiện tại (projection)
| Field | Kiểu | Ghi chú |
|---|---|---|
| warehouse | FK | |
| product | FK | |
| quantity | IntegerField default 0 | **cho phép âm** |
| updated_at | DateTime auto_now | |

- `unique_together(warehouse, product)`. Cập nhật **atomic** cùng transaction với mỗi movement
  (dùng `select_for_update` để chống race). Là cache — luôn = tổng `qty_delta` của sổ.

### 3.3 `StockDocument` + `StockDocumentLine` — phiếu kho
**StockDocument**
| Field | Kiểu | Ghi chú |
|---|---|---|
| code | CharField unique | tự sinh: `NK`/`XK`/`DC`/`CK` + yyyymmdd + seq |
| doc_type | CharField choices | receipt (nhập), issue (xuất), adjustment (kiểm kê/điều chỉnh), transfer (chuyển kho) |
| source_warehouse | FK null | bắt buộc cho issue/transfer |
| dest_warehouse | FK null | bắt buộc cho receipt/transfer |
| status | CharField choices | draft, posted, cancelled |
| note | TextField blank | |
| created_by, created_at, posted_at | | |

**StockDocumentLine**
| product | FK | |
| quantity | PositiveIntegerField | >0 |
| (unique_together(document, product)) | | |

- **Post** (draft→posted): trong `transaction.atomic()`, sinh movement cho mỗi line + cập nhật
  `StockItem`. receipt→`receipt`(+) vào dest; issue→`issue`(−) từ source; adjustment→`adjustment`
  (delta = quantity mới − tồn hiện tại, hoặc nhập trực tiếp delta — xem §4.3); transfer→cặp
  `transfer_out`(−source) + `transfer_in`(+dest).
- **Cancel** (posted→cancelled): sinh movement đảo dấu, set status. Không xoá movement cũ.
- Phiếu `draft` chưa tác động tồn.

### 3.4 Order — thêm field chọn kho
- `orders.Order.fulfillment_warehouse` = FK warehouses.Warehouse, **null=True** (chọn thủ công).
- Field do **orders sở hữu**; inventory không ghi vào bảng order. Tự trừ kho đi qua contract (§5).

---

## 4. Quy tắc nghiệp vụ

### 4.1 Bất biến sổ kho
- `StockItem.quantity` luôn = Σ `qty_delta` của các movement cùng (warehouse, product).
- `balance_after` ghi tại thời điểm tạo movement (đọc số dư đang khoá → cộng delta → ghi).
- Mọi thao tác đổi tồn bọc `transaction.atomic()` + `select_for_update` trên `StockItem`.

### 4.2 Cho phép tồn âm
- Không bao giờ chặn vì thiếu tồn. Nếu sau movement `balance_after < 0` → phát domain event
  `StockWentNegative` (§5.3) để cảnh báo, KHÔNG raise.

### 4.3 Kiểm kê (adjustment)
- Phiếu adjustment nhập **số đếm thực tế** (`counted_qty`) cho mỗi line; service tính
  `delta = counted_qty − tồn_hiện_tại` và ghi movement `adjustment` (delta có thể âm/dương).
- (Line dùng field `quantity` = counted_qty; doc_type=adjustment được hiểu là "đặt tồn về N".)

### 4.4 Tự trừ kho khi confirm đơn
- Điều kiện: đơn chuyển sang trạng thái confirmed VÀ có `fulfillment_warehouse`.
- orders gọi **đồng bộ** `StockCommandService.issue_for_order(order_id, warehouse_id, lines)`:
  mỗi OrderItem → movement `sale`(−). Trả về danh sách cảnh báo (sp nào âm). orders hiển thị.
- Huỷ đơn đã confirmed (đã trừ) → `release_for_order(order_id)` ghi `sale_reversal`(+).
- Chống trừ 2 lần: đánh dấu đã issue theo `reference=(order, order_id)`; idempotent (kiểm tra
  đã có movement `sale` cho order chưa).

---

## 5. Tích hợp & Contract

### 5.1 Contract (inventory công bố ở `contracts/`)
```python
class StockCommandService(Protocol):
    def issue_for_order(self, *, order_id: int, warehouse_id: int,
                        lines: list[StockLine]) -> list[StockWarning]: ...
    def release_for_order(self, *, order_id: int) -> None: ...
```
`StockLine` = dataclass `{product_id, quantity}`; `StockWarning` = `{product_id, balance_after}`.
orders import **interface này**, không import model inventory.

### 5.2 Điểm gọi trong orders
Trong service confirm đơn của orders (`order_service_commands`), sau khi set confirmed và
trong cùng transaction: nếu có `fulfillment_warehouse`, gọi `issue_for_order(...)`; gom warning
trả lên response. Khi huỷ: gọi `release_for_order`.

### 5.3 Domain event (dùng event bus đã dựng)
- `inventory/contracts/stock_events.py`: `@dataclass(frozen=True) class StockWentNegative(DomainEvent)`
  `event_type = "inventory.stock_negative"`, fields `warehouse_id, product_id, balance_after`.
- Service phát qua `event_bus.publish_on_commit(StockWentNegative(...))` khi `balance_after < 0`.
- **Consumer đầu tiên của event bus**: một handler (đăng ký ở `inventory/apps.py ready()` hoặc
  một app notify) tạo notification cảnh báo tồn âm. (Phase 1 tối thiểu: log + đặt chỗ cho notify.)

---

## 6. API (REST, dưới `/api/...`, prefix theo chuẩn hiện có)

| Method | Path | Việc | Sprint |
|---|---|---|---|
| GET | `/inventory/stock` | Liệt kê tồn (lọc theo warehouse, product/sku, phân trang) | 1.1 |
| GET | `/inventory/stock/movements` | Lịch sử sổ kho (lọc warehouse/product) | 1.1 |
| GET/POST | `/inventory/documents` | Liệt kê/tạo phiếu (draft) | 1.2 |
| GET/PATCH | `/inventory/documents/<id>` | Xem/sửa phiếu draft | 1.2 |
| POST | `/inventory/documents/<id>/post` | Post phiếu → sinh movement | 1.2 |
| POST | `/inventory/documents/<id>/cancel` | Huỷ phiếu đã post → đảo | 1.2 |
| (transfer dùng chung endpoint documents với doc_type=transfer) | | | 1.3 |

Views mỏng theo chuẩn: serializer validate → gọi service → Response. Phân trang qua
`apps.common.pagination`. Permission `require_section("stock")` / `require_section("stock-documents")`.

---

## 7. Frontend (gắn vào app-shell)

### 7.1 Section mới trong module `inventory` (registry `app/modules.ts`)
- `stock` — "Tồn kho" (bảng tồn theo kho×sp) → `/inventory/stock`
- `stock-documents` — "Phiếu kho" (nhập/xuất/điều chỉnh/chuyển) → `/inventory/stock-documents`

Thêm vào: `AppSection` (`lib/routes.ts`), `SECTION_TO_PATH`, registry `inventory.sections`,
route trong `routes.tsx`, i18n `topnav.tabs.*`, và **đăng ký section ở backend** (danh sách
section hợp lệ + phân quyền phòng ban). Validate round-trip path như spec app-shell.

### 7.2 Trang (feature-slice `src/features/inventory/`)
- `StockPage` — bảng ag-grid tồn theo kho (filter kho/sku), xem lịch sử movement của 1 dòng.
- `StockDocumentsPage` — danh sách phiếu + dialog tạo phiếu (chọn loại, kho nguồn/đích, thêm
  line sp+sl), nút Post/Cancel. Dùng TanStack Query (`queries.ts`).
- Đơn hàng: thêm ô **chọn kho xuất** (`fulfillment_warehouse`) trên form/detail đơn; hiển thị
  cảnh báo tồn âm trả về khi confirm.

---

## 8. Phân rã Sprint & Tiến độ

| Sprint | Phạm vi | Trạng thái |
|---|---|---|
| **1.1** | App `inventory` + `StockMovement` + `StockItem` + repo + `StockQueryService` + API xem tồn/lịch sử + FE `StockPage` + section `stock`. Migration. | ✅ Xong (2026-06-11) — BE 16 test, FE 17 test xanh; `record_movement` primitive + `rebuild_stock_items` đối soát. Migration `0001_initial` (chưa chạy prod). |
| **1.2** | `StockDocument`/`Line` + `StockDocumentService.post/cancel` (receipt/issue/adjustment) + API + FE `StockDocumentsPage` + section `stock-documents`. | ✅ Xong (2026-06-11) — migration `0002`, 27 test BE. |
| **1.3** | doc_type `transfer` (cặp out/in) + UI chuyển kho. | ✅ Xong (2026-06-11) — 33 test BE. |
| **1.4** | `Order.fulfillment_warehouse` + contract `issue_for_order`/`release_for_order` + tích hợp confirm/cancel đơn + event `StockWentNegative` (consumer log) + FE chọn kho/cảnh báo. | ✅ Xong (2026-06-11) — migration orders `0081`; 53 test BE; deduction GATED theo fulfillment_warehouse (rollout an toàn); event bus có consumer thật đầu tiên. |

> Cập nhật sau mỗi sprint: đã làm/chưa làm/vướng.

---

## 9. Rủi ro
- **R1 — Lệch giữa `StockItem` và sổ:** chỉ cập nhật qua service có `select_for_update` +
  transaction; có management command đối soát (rebuild `StockItem` từ sổ) — *nên có ở 1.1*.
- **R2 — Trừ kho 2 lần khi confirm lặp:** idempotent theo reference order (§4.4).
- **R3 — Thoả hiệp ranh giới Product (§2.2):** ghi rõ, không lan rộng; chỉ inventory FK Product.
- **R4 — Section mới chưa đăng ký quyền BE:** dễ quên → phải thêm vào registry section + phòng ban.
- **R5 — Phạm vi lớn cho 1-2 dev:** bám phân rã sprint; 1.1 phải chạy được độc lập trước.
