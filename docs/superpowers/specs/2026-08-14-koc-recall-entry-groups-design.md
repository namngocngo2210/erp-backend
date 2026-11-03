# Đơn KOC: nhóm thu hồi (mã + ngày tạo + ngày nhận), một đơn nhiều nhóm

Ngày: 2026-08-14
Nhánh: `feat/koc-recall-entry-groups`
Tiền đề: MR !1228 (`recall_code` thành `ArrayField`, thêm `Order.koc_order_type`) đã merge vào master ở commit `13314bbb`.

## 1. Bối cảnh & vấn đề

MR !1228 cho `OrderRecall.recall_code` chứa nhiều mã, nhưng **ngày tạo và ngày nhận vẫn chỉ có một cặp cho cả đơn**. Thực tế một đơn KOC thu hồi nhiều đợt, mỗi đợt có mã riêng, ngày tạo riêng, ngày nhận riêng — mảng mã dùng chung một cặp ngày là mô hình sai: nhìn vào không biết mã nào ứng với ngày nào.

Cấu trúc hiện tại (`order_recalls`, quan hệ 1-1 với `Order`):

| Trường | Kiểu | Vấn đề |
|---|---|---|
| `recall_code` | `ArrayField(CharField(64))` | N mã nhưng không gắn được với ngày của riêng nó |
| `recall_created_date` | `DateField` | Chỉ 1 cho cả đơn |
| `recall_received_date` | `DateField` | Chỉ 1 cho cả đơn |
| `return_slip_code` | `ArrayField(CharField(64))` | Không thuộc vấn đề này — xem §2 |

Mục tiêu: một đơn KOC có **N nhóm thu hồi**, mỗi nhóm là bộ ba (mã đơn thu hồi, ngày tạo, ngày nhận hàng). Sửa được cả trên lưới danh sách lẫn trang chi tiết.

## 2. Quyết định nghiệp vụ đã chốt

| Câu hỏi | Chốt |
|---|---|
| Một nhóm gồm những gì? | **3 ô**: mã đơn thu hồi + ngày tạo + ngày nhận hàng |
| Mỗi nhóm mấy mã? | **Đúng 1 mã** — `recall_code` quay lại `CharField`, tính "nhiều" chuyển sang số dòng |
| Phiếu trả hàng thuộc nhóm? | **Không** — giữ ở mức đơn, y nguyên hiện tại |
| Bảng danh sách hiển thị sao? | **Giữ 3 cột**, mỗi ô xếp chồng N dòng; dòng thứ i của cả 3 cột là một nhóm |
| Sửa trên lưới? | **Có** — vẫn gõ thẳng như hiện nay. Ruling 2026-08-14: bỏ nút trên lưới (luôn thừa một dòng trống ở cuối cả ba cột), giữ nút ở trang chi tiết — xem §3.3 |

**Vì sao Phiếu trả hàng đứng ngoài nhóm.** `return_slip_code` do job `returns_sync_service` đồng bộ tự động từ KiotViet (`GET /returns`), map theo `invoiceId` → `Order.invoice_id`, chính sách ghi là UNION vào một chỗ duy nhất của đơn. Job không có bất kỳ dữ kiện nào để biết phiếu trả thuộc đợt thu hồi nào. Kéo nó vào nhóm buộc phải bịa một quy tắc gán ("luôn ghi nhóm đầu", "tự tạo nhóm không mã") — cả hai đều sinh dữ liệu sai một cách im lặng, mà không đổi lại được lợi ích nghiệp vụ nào.

## 3. Thiết kế

### 3.1. Mô hình dữ liệu

Bảng mới `order_recall_entries`:

```python
class OrderRecallEntry(models.Model):
    """Một đợt thu hồi của đơn KOC. Một đơn có N đợt, mỗi đợt một mã và cặp ngày
    của riêng nó — trước đây N mã dùng chung một cặp ngày nên không biết mã nào
    ứng với ngày nào."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="recall_entries")
    recall_code = models.CharField(max_length=64, blank=True, default="")
    recall_created_date = models.DateField(null=True, blank=True)
    recall_received_date = models.DateField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_order_recall_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_recall_entries"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "id"], name="recall_entry_order_idx"),
            models.Index(fields=["recall_code"], name="recall_entry_code_idx"),
        ]
```

**Thứ tự nhóm theo `id` tăng dần** — nhóm thêm sau nằm dưới. Không thêm cột `sort_order`: không có nhu cầu sắp xếp lại, và một cột thứ tự không ai đổi là cột chết.

**Index `recall_entry_code_idx` là btree bình thường, không phải GIN.** Đây là điểm thiết kế của MR !1228 bị đảo ngược có chủ đích: GIN chỉ cần khi cột là mảng; nay mỗi dòng một mã nên equality trên btree vừa nhanh hơn vừa nhỏ hơn.

`OrderRecall` (1-1) **giữ lại** nhưng chỉ còn `return_slip_code` + trường kiểm toán. Ba cột `recall_code` / `recall_created_date` / `recall_received_date` bị xoá sau khi chuyển dữ liệu.

> Đã cân nhắc dời `return_slip_code` lên thẳng `Order` để xoá hẳn bảng `order_recalls`. Bỏ, vì phải sửa `returns_sync_service` (khoá dòng, `get_or_create`, chính sách UNION) mà không được gì — một bảng ít đi không đáng đổi lấy rủi ro ở job đang chạy ổn định.

### 3.2. Contract API

Response của đơn thêm:

```json
"recall_entries": [
  {"id": 12, "recall_code": "RC-001", "recall_created_date": "2026-08-12", "recall_received_date": "2026-08-15"},
  {"id": 13, "recall_code": "RC-002", "recall_created_date": "2026-08-20", "recall_received_date": null}
]
```

`recall` giữ nguyên tên nhưng chỉ còn `{"return_slip_code": "..."}` (vẫn là chuỗi phẩy như hiện tại).

Chiều ghi: **full-replace danh sách** — client gửi cả mảng `recall_entries`, backend thay toàn bộ tập nhóm của đơn. Đây là pattern đã có sẵn cho `items` (`OrderItemInputSerializer(many=True)` trong `OrderInlineUpdateSerializer`), không phát minh cách mới. Không có `recall_entries` trong payload = không đụng tới nhóm (giống mọi field khác).

**Nhóm rỗng hoàn toàn (cả 3 ô trống) bị loại khi lưu.** Đó cũng chính là cách xoá nhóm trên lưới — xoá sạch 3 ô rồi rời khỏi ô là nhóm biến mất, không cần nút xoá riêng trong bảng.

`id` trong payload ghi được bỏ qua: full-replace nên backend xoá hết rồi tạo lại theo đúng thứ tự client gửi. Đơn giản và không có đường phân nhánh; đổi lại `id` của nhóm thay đổi sau mỗi lần lưu — chấp nhận được vì không có gì tham chiếu tới `id` đó.

### 3.3. Bảng danh sách + inline edit

Ba cột **giữ nguyên key** `recall_code` / `recall_created_date` / `recall_received_date`. Đây là ràng buộc cứng: key cột được lưu trong `localStorage` của từng người dùng và là key của cột export — đổi tên là mọi người mất cấu hình bảng.

Mỗi ô render N input xếp dọc theo `recall_entries`. Dòng thứ i của cả ba cột là một nhóm, nên ba cột luôn cao bằng nhau.

**Không có nút "+ thêm nhóm".** Cả ba cột luôn render `entries.length + 1` input — tức luôn thừa đúng một dòng trống ở cuối để gõ thẳng vào (thêm nhóm là thao tác 0 click, đúng cách lưới kiểu bảng tính hoạt động). Lý do bỏ nút: ba cột là ba cell render độc lập (không có component cha chung giữ state), nếu thêm dòng qua nút bấm + state cục bộ trong một cell thì chỉ cột đó dài ra còn hai cột kia đứng yên — phá đúng bất biến "ba cột luôn cao bằng nhau" ở trên. Suy ra số dòng từ `entries.length` (giống nhau ở cả ba cell vì cùng đọc `row.recall_entries`) thì ba cột căn hàng theo cấu trúc, không cần state chia sẻ.

Sửa một ô → FE gửi lại **cả mảng** `recall_entries` với đúng ô đó đã đổi. Hàng đơn sẽ cao lên khi có nhiều nhóm; chấp nhận, vì đó là sự thật của dữ liệu.

### 3.4. Trang chi tiết

`BlockRecall` đổi từ 4 `ERow` phẳng thành:

- Danh sách nhóm, mỗi nhóm là cụm 3 `ERow` (mã / ngày tạo / ngày nhận) có tiêu đề **"Đợt thu hồi #n"** và nút xoá nhóm.
- Nút **"+ Thêm đợt thu hồi"** ở cuối danh sách.
- Ô **"Phiếu trả hàng"** đứng riêng, ngoài danh sách nhóm.
- Ô **"Loại đơn KOC"** giữ nguyên vị trí hiện tại (trên cùng).

### 3.5. Export, bộ lọc, tra cứu

- **Excel**: ba cột nối giá trị các nhóm bằng `", "` theo đúng thứ tự nhóm, nên giá trị thứ i của ba cột vẫn ứng với nhau. Nhóm thiếu ô nào thì vị trí đó để rỗng (`"RC-001, "` chứ không dồn) — dồn lại là làm lệch sự tương ứng theo vị trí.
- **Bộ lọc `has_recall_code`**: `Exists(OrderRecallEntry.objects.filter(order_id=OuterRef("pk")).exclude(recall_code=""))`.
- **Tra cứu mã vận đơn** (`shipping_lookup_view`): `recall_entries__recall_code=code` — equality, không còn `__contains`.
- **`koc_order_type`** không đụng tới.

### 3.6. Chuyển dữ liệu

Với mỗi `OrderRecall` hiện có:

- Mỗi mã trong mảng `recall_code` → một `OrderRecallEntry`, **dùng chung cặp ngày** của bản ghi gốc.
- Mảng mã rỗng nhưng có ít nhất một ngày → một entry chỉ có ngày.
- Không mã, không ngày → không tạo entry nào.

Thực tế `recall_code` mới thành mảng từ MR !1228 (merge 2026-08-13) nên gần như mọi đơn chỉ có đúng 1 mã — chuyển đổi thực chất là 1-1, không mất dữ liệu.

## 4. Rủi ro & runbook triển khai

**Runbook triển khai — 3 bước, KHÔNG "cùng một nhịp".** "Cùng một nhịp" không thực hiện được: roll code trước thì code mới đọc `order_recall_entries` (bảng chưa tồn tại) → 500; migrate hết cả `0129` trước thì code cũ đọc `order_recalls.recall_code` (cột vừa bị xoá) → `ProgrammingError` ở hot path (bộ lọc `has_recall_code`, màn quét mã vận đơn của kho). Ba migration đã tách sẵn đúng bài expand/contract nên không cần chịu cửa sổ 500 nào — cứ đi đúng thứ tự:

1. `python manage.py migrate orders 0128` — thuần cộng thêm (tạo bảng `order_recall_entries` + copy dữ liệu từ `order_recalls`). Code cũ không biết bảng mới tồn tại, chạy y nguyên. **Zero downtime.**
2. Roll code mới. Từ giây này mọi thao tác ghi đi vào bảng mới.
3. `python manage.py migrate orders 0129` — xoá 3 cột cũ (`recall_code`, `recall_created_date`, `recall_received_date`) khỏi `order_recalls`. Code mới không đọc chúng nữa.

Hai cảnh báo đi kèm:

- **Khoản nợ duy nhất:** dữ liệu do **code cũ** ghi vào `order_recalls.recall_code` trong khoảng giữa bước 1 và bước 2 sẽ không được copy sang (migration `0128` đã chạy xong trước đó). Chạy bước 1-2 sát nhau ở giờ thấp điểm để thu hẹp cửa sổ này.
- **`copy_forward` (migration `0128`) KHÔNG idempotent** — chạy lại sẽ nhân đôi nhóm (nó copy TOÀN BỘ `OrderRecall` mỗi lần chạy, không lọc đơn đã có entry). Muốn vá phần rơi rớt ở trên thì phải viết câu SQL riêng chỉ lấy đơn chưa có entry, **KHÔNG chạy lại migration `0128`**.

**Rollback có mất dữ liệu.** Reverse migration gộp N nhóm về lại 1 mảng mã + **cặp ngày của nhóm đầu tiên**; ngày của các nhóm sau mất vĩnh viễn. Nếu đã có đơn nhiều nhóm thì dump `order_recall_entries` trước khi lùi.

**Migration leaf.** Master đang ở `0126_order_koc_type_index`. Migration mới nối tiếp từ đó; trước khi push phải xác nhận `makemigrations --check` sạch và chỉ có một leaf trong app `orders` (master từng dính sự cố trùng leaf ở `93c59be9`).

**Bẫy đã biết của repo — bộ lọc phải khai ở BỐN nơi.** Task này không thêm filter mới, nhưng nếu chạm vào `has_recall_code` thì nhớ nó chảy qua `build_orders_filters`, `list_koc_orders`, `list_landing_orders`, `sheet3_view`. Xem `docs/` và lịch sử MR !1228 (lỗi Critical bắt được ở final review).

**`schema.yaml`** sửa tay tối thiểu, không regen toàn file.

## 5. Kiểm thử

| Tầng | Nội dung |
|---|---|
| BE — `tests/test_order_recall.py` | Tạo/sửa N nhóm qua PATCH; full-replace thay đúng tập nhóm; nhóm rỗng bị loại; PATCH không kèm `recall_entries` không đụng nhóm cũ; `return_slip_code` vẫn hoạt động độc lập |
| BE — migration | Data migration: 1 mã → 1 nhóm giữ ngày; nhiều mã → N nhóm cùng ngày; chỉ có ngày → 1 nhóm; trống → không nhóm |
| BE — `tests/test_shipping_lookup.py` | Tra cứu trúng mã của nhóm thứ 2 |
| BE — `tests/test_order_list_exists_filters.py` | `has_recall_code` đúng sau khi đổi bảng |
| BE — `tests/test_orders_export_definition.py` | Ba cột xuất đúng thứ tự nhóm, nhóm thiếu ô để rỗng đúng vị trí |
| FE | Ô lưới render N input; sửa một ô gửi lại cả mảng; ruling 2026-08-14: bỏ nút trên lưới (luôn thừa một dòng trống ở cuối cả ba cột), giữ nút ở trang chi tiết; `BlockRecall` thêm/xoá nhóm qua nút "+ Thêm đợt thu hồi" ở trang chi tiết |

Không tạo file test rời rạc cho từng thay đổi — chèn vào các file trên.

## 6. Không làm (YAGNI)

- Không kéo-thả sắp xếp nhóm, không cột `sort_order`.
- Không gắn phiếu trả hàng vào nhóm.
- Không thêm trạng thái/ghi chú riêng cho từng đợt thu hồi.
- Không đụng `koc_order_type`, không đụng job sync KiotViet.
- Không xoá bảng `order_recalls`.

## 7. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 | BE: model `OrderRecallEntry` + migration schema + data migration + xoá 3 cột cũ | ✅ Xong |
| 2 | BE: serializer đọc/ghi full-replace, export, filter, tra cứu mã + test | ✅ Xong |
| 3 | FE: lưới 3 cột xếp chồng + gửi full-replace | ✅ Xong (ruling 2026-08-14: bỏ hẳn nút "+ thêm nhóm", cả 3 cột luôn render `entries.length + 1` input để giữ 3 cột cao bằng nhau) |
| 4 | FE: `BlockRecall` trang chi tiết (danh sách nhóm, thêm/xoá) | ✅ Xong |
| 5 | `schema.yaml`, rà migration leaf, lint + suite đầy đủ, MR | ✅ Xong phần schema/migration/lint/suite (BE app orders 1312 pass, FE 149 file/1148 test pass, ruff sạch). **Còn nợ: push + tạo MR** — theo brief Task 5, controller làm sau khi review toàn nhánh. |
