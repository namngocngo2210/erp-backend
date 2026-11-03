# Snapshot mốc vòng đời đơn hàng (created / invoiced)

- Ngày: 2026-08-04
- Nhánh: `feat/order-milestone-snapshots`
- Trạng thái: đã chốt thiết kế với chủ sở hữu nghiệp vụ

## 1. Bối cảnh

`landing_confirmed_amount` (spec 2026-08-04-landing-confirmed-revenue) ghi được
tiền khách tự đăng ký lúc đơn đổ về, nhưng đơn cũ phải **backfill parse
product_raw** — lossy, phải chạy tay, đã vá 3 lần trong một ngày (regex khớp
phần đuôi, thiếu đơn sku-only, thiếu source ngoài landing). Chủ sở hữu nghiệp vụ
yêu cầu cơ chế bền vững: **thông tin đơn được chụp lại ngay tại thời điểm phát
sinh mốc**, không bao giờ phải suy ngược nữa.

## 2. Quyết định nghiệp vụ (chốt 2026-08-04)

1. **Hai mốc**: `created` (đơn đổ về qua ingest) và `invoiced` (đơn được tạo hóa
   đơn KiotViet — gán `invoice_code` lần đầu). KHÔNG chụp mốc kship giao thành
   công (đã có Nhóm 4/5 dashboard đọc trực tiếp `status=3`).
2. **Dữ liệu mỗi mốc**: `total_amount`, `total_after_discount`, `quantity`,
   `sku` (chuỗi tổng hợp), `amount_source`. Không lưu chi tiết line item.
3. **Nơi dùng**: nguồn đối soát qua API đọc theo đơn; báo cáo theo sale giữ
   nguyên nguồn số hiện tại (`landing_confirmed_amount`) trong phạm vi này.

## 3. Thiết kế

### 3.1 Model — `OrderMilestoneSnapshot` (migration 0115)

```python
class OrderMilestone(models.TextChoices):
    CREATED = "created", "Đơn đổ về"
    INVOICED = "invoiced", "Đã tạo hóa đơn"


class OrderMilestoneSnapshot(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="milestone_snapshots")
    milestone = models.CharField(max_length=20, choices=OrderMilestone.choices)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_after_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=0)
    sku = models.TextField(blank=True, default="")
    # Số tiền đến từ đâu: payload | text | price_book | none (mốc created);
    # invoice (mốc invoiced); seeded (suy ngược bởi seed command); cloned.
    amount_source = models.CharField(max_length=20, blank=True, default="")
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "milestone"], name="uniq_order_milestone"),
        ]
```

Write-once tự nhiên: mọi điểm ghi dùng `get_or_create` — gọi trùng (webhook bắn
lại, cron chạy lại, hóa đơn X → X.01 reattach) không tạo dòng mới, không ghi đè.
Không cần index thêm ngoài unique (mọi truy vấn đều theo `order_id`).

### 3.2 Service — `apps/orders/application/services/milestone_snapshot_service.py`

File mới, hai hàm mỏng, không phụ thuộc ngược:

```python
def capture_created_snapshot(order, *, amount_source: str) -> None
def capture_invoice_snapshot(order) -> None   # amount_source="invoice"
```

Cả hai chụp giá trị hiện tại của `order` (total_amount, total_after_discount,
quantity, sku) + `get_or_create` theo (order, milestone). Lỗi ghi snapshot không
được làm vỡ luồng chính: bọc try/except log error (snapshot là dữ liệu đối soát,
không phải dữ liệu giao dịch).

### 3.3 Điểm ghi mốc `created`

- `create_order_from_landing` (sau `Order.objects.create`): `amount_source` theo
  nguồn tiền đã dùng — `payload` (total_amount tường minh) / `text` (parse
  product_raw) / `price_book` (items_total) / `none` (0).
- `duplicate_order` (`order_service_helpers`): copy dòng `created` của đơn gốc
  sang đơn mới, `amount_source="cloned"` giữ số của đơn gốc — cùng ngữ nghĩa
  với copy `landing_confirmed_amount` hiện có.
- Các đường tạo khác (tạo tay, Excel, sàn) KHÔNG chụp — mốc "đổ về" chỉ có nghĩa
  với đơn ingest (YAGNI; thêm sau nếu nghiệp vụ cần).

### 3.4 Điểm ghi mốc `invoiced`

Gọi `capture_invoice_snapshot(order)` ngay sau khi `invoice_code` được gán lần
đầu (điều kiện: trước đó rỗng), tại 4 điểm đã xác minh:

| File | Ngữ cảnh |
|---|---|
| `apps/kiotviet/infrastructure/services/service_actions.py:98` | Push tạo hóa đơn từ CRM (đường 1) |
| `apps/kiotviet/infrastructure/services/service_actions.py:231` | Push tạo hóa đơn từ CRM (đường 2) |
| `apps/kiotviet/infrastructure/services/_import_service_handlers_mixin.py:326` | Webhook/import gán hóa đơn vào đơn có sẵn |
| `apps/kiotviet/infrastructure/services/_import_service_upsert_mixin.py:382` | Cron import upsert hóa đơn |

`get_or_create` nên gọi dư ở đường chạy lại là vô hại. Script standalone
`import_order_xlsx_standalone.py` không gọi (chạy ngoài app, dữ liệu lịch sử —
seed command lo).

Kiotviet đã import model/service của orders ở nhiều nơi — không tạo phụ thuộc
ngược mới; import từ `apps.orders.application.services.milestone_snapshot_service`.

### 3.5 API đối soát

`GET /api/orders/{id}/milestone-snapshots` — trả mảng dòng snapshot của đơn
(milestone, các cột tiền, quantity, sku, amount_source, captured_at). Quyền: như
xem chi tiết đơn (cùng permission class/record-scope với order detail hiện có).
Không có UI trong phạm vi này.

### 3.6 Seed một lần cho đơn cũ

Management command `seed_order_milestone_snapshots` (dry-run mặc định, `--apply`,
keyset pagination theo id — KHÔNG dùng `iterator()`, bài học PgBouncer):

- `created`: đơn thuộc scope ingest (nguồn landing hoặc có LandingIngestTrace
  CREATED) và `landing_confirmed_amount > 0` → total = snapshot field hiện có,
  `amount_source="seeded"`.
- `invoiced`: đơn có `invoice_code != ""` → total = `total_after_discount` hiện
  tại (xấp xỉ — số thật tại thời điểm lên hóa đơn không còn; `amount_source=
  "seeded"` đánh dấu rõ để đối soát biết là số suy ngược).
- Idempotent nhờ `get_or_create`; chạy lại không tạo trùng.

Sau seed, không còn khái niệm backfill: mốc mới tự ghi tại thời điểm phát sinh.

## 4. Test

- Ingest → snapshot `created` đúng số + đúng `amount_source` theo cả 4 nguồn.
- Gán hóa đơn (mô phỏng qua service actions hoặc gọi trực tiếp
  `capture_invoice_snapshot`) → snapshot `invoiced` chụp giá trị tại thời điểm
  đó; sửa đơn sau đó không đổi snapshot; gọi lần 2 không tạo dòng mới.
- Clone đơn → dòng `created` copy theo, `amount_source="cloned"`.
- API: trả đủ dòng theo đơn; user không có quyền xem đơn → 403/404 theo scope.
- Seed command: dry-run không ghi; apply idempotent; đơn thiếu điều kiện bị bỏ.

## 5. Ngoài phạm vi

- Không đổi nguồn số của báo cáo theo sale (vẫn `landing_confirmed_amount`).
- Không chụp mốc kship giao thành công.
- Không lưu line items chi tiết.
- Không UI hiển thị lịch sử mốc.
