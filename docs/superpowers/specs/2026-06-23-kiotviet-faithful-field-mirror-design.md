# Đồng bộ trung thực field A→B cho KiotViet reconcile

- **Ngày:** 2026-06-23
- **Branch:** `feat/kiotviet-faithful-field-mirror`
- **Phạm vi:** builder payload sync invoice new→old + patch status sau create + mode `update_status` của reconcile.

## 1. Bối cảnh & vấn đề

Cron `kv-reconcile-paired-channels` (mỗi 1 tiếng) mirror đơn từ kênh **new (A)** sang kênh **old (B)** đã ghép. Hiện `build_target_invoice_payload` đang:

- **Suy luận** giảm giá theo `Σ(line gross) − total` thay vì lấy field thật.
- **Collapse** giá dòng về net (`subTotalAfterTax / qty`) và ép `line.discount = 0` khi bất biến không khớp → bên B **mất breakdown** giá gốc + giảm giá thật.

Yêu cầu của CTO: **lấy đúng field từ A → B, không tính toán/suy luận, kể cả status.**

## 2. Phát hiện then chốt (đã verify trên 160 đơn thật, A kv4 + kv6)

Mọi đơn tái lập **100%** chỉ bằng field thật trong response A:

```
Đơn có thuế:   total = Σ[(priceAfterTax − discountAfterTax) × qty] − header.discountAfterTax
Đơn không thuế: total = Σ[(price − discount) × qty]                 − header.discount
```

- `discount` / `discountAfterTax` cấp dòng là **per-UNIT** (mỗi sản phẩm), không phải per-line.
- `priceAfterTax` / `discountAfterTax` lấy từ `invoiceDetails[].invoiceDetailTaxs[0]`.
- Header discount: new API thường để ở `discount`/`discountAfterTax` cấp hóa đơn (có đơn = None).

**Bảng mã trạng thái (xác nhận từ statusValue thật):**

| Mã | Invoice status | Delivery status |
|----|----------------|-----------------|
| 1 | Hoàn thành (khóa) | Chờ xử lý |
| 2 | Hủy | Đang giao hàng |
| 3 | Đang xử lý | Giao thành công |
| 6 | — | Đã hủy |
| 7 | — | Đang lấy hàng |
| 9 | — | Đã lấy hàng |

Lưu ý: combo `invoice=Đang xử lý(3)` × `delivery=Giao thành công(3)` **có thật trên A** (KiotViet không cấm). Nhưng B hiện không có đơn `delivery=3` nào → nghi KiotViet **auto-complete** invoice khi delivery=3.

## 3. Quyết định (đã chốt)

1. **IDs chéo retailer** (`branchId`, `saleChannelId`, `soldById`, `customer`, `partnerDelivery`): **GIỮ map như hiện tại** — không thể copy thô vì A/B là 2 retailer khác id. `partnerDelivery.name` giữ hard-code `"DVVC cho đơn DESI"`.
2. **Giá & giảm giá**: lấy **field thật**, bỏ suy luận `Σ(line)−total` và bỏ nhánh faithful-reset.
3. **Parity guard**: khi tổng tái lập lệch `total` của A → `log.warning`, **không ép** khớp.
4. **Status**: copy **đúng mọi mã** A (không chỉ 1/2/3); sau patch **read-back** so B với A, lệch thì `log.warning`.
5. **Ca xung đột** `A.delivery=Giao thành công(3)` + `A.invoice≠Hoàn thành(1)`: **cap delivery=2** (Đang giao), giữ `invoice` đúng A, `log.warning(capped)` — ưu tiên invoice chuẩn, tránh wrong-complete không revert được. Nhịp reconcile sau, khi A.invoice=1 mới đẩy delivery=3 + invoice=1.

## 4. Thiết kế kỹ thuật

File chính: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py`.

### 4.1 `build_invoice_details` (per line, per-unit)

- Có tax row: `price = priceAfterTax`, `discount = discountAfterTax` (fallback `viewDiscountAfterTax`).
- Không tax row: `price = detail.price`, `discount = detail.discount`.
- **Bỏ** chuỗi fallback `subTotalAfterTax/qty` cũ (vì nó collapse net + mất giảm giá dòng).
- Vẫn giữ `productCode`, `productName`, `quantity`, và `OrderDetailTaxs:[{TaxId:3}]` khi `target_is_old=False`.

### 4.2 `build_target_invoice_payload` (header + parity)

- `discount` / `discountAfterTax` payload = `first_non_empty(source.discountAfterTax, source.discount)` lấy **as-is** (bỏ quy đổi taxRate, bỏ `inferred_total_discount`, bỏ khối `faithful_ok` reset).
- **Parity check:** `expected = Σ[(price − discount) × qty] − header_discount`; nếu `|expected − source.total| > 1` → `log.warning("parity mismatch %s: expected=%s total=%s", code, expected, total)`. Vẫn gửi payload (không sửa số).

### 4.3 Write-semantics bên B *(ĐÃ XÁC MINH — per-unit)*

Đã test-create reversible thật trên B (kv5) ngày 2026-06-23: tạo đơn `qty=2, price=100000, discount=10000` → B trả `subTotal = 180000 = (price − discount) × qty` → **xóa đơn test (id 122278783) thành công**.

**Kết luận:** B diễn giải `invoiceDetails[].discount` là **PER-UNIT**, trùng đúng model đọc của A.

→ Payload gửi **thẳng field thật, không quy đổi**:
- `price = priceAfterTax` (per-unit), `discount = discountAfterTax` (per-unit).
- B tự tái lập `subTotal = (priceAfterTax − discountAfterTax) × qty = subTotalAfterTax`. Copy 1:1.
- Không còn fallback net-price; mọi đơn giữ breakdown thật.

### 4.4 `patch_invoice_status_after_create` (status trung thực + guard)

- `target = (source_invoice_status, source_delivery_status)` — copy **mọi mã** (1/2/3/6/7/9), bỏ giả định chỉ 1/2/3.
- **Conflict guard:** nếu `source_invoice_status != 1` và `source_delivery_status == 3` → PUT `delivery = 2` (thay vì 3), giữ `invoice = source_invoice_status`, `log.warning("capped delivery 3→2 to avoid false-complete %s", code)`.
- Ngược lại: giữ luồng 2 bước hiện tại (PUT delivery trước → PUT invoice sau).
- **Read-back:** sau patch, `GET /invoices/{id}`, so `(B.invoice, B.delivery)` với `(A.invoice, A.delivery)`; lệch → `log.warning`.

### 4.5 `_sync_service_sync_mixin._update_status` (đơn ĐÃ tồn tại bên B)

- Áp **cùng conflict guard**: khi `src_inv_st != 1` và `src_del_st == 3` → không PUT delivery=3, cap về 2 + log. Đảm bảo hành vi nhất quán giữa luồng tạo-mới và luồng update.

### 4.6 Phí vận chuyển `invoiceDelivery.price` (fix loop "xóa chạy lại vẫn sai")

**Root cause (đã verify bằng test-create + delete trên B, 2026-06-23):** giá ship bên B **không đến từ payload mình gửi**. B (retailer cũ, không tích hợp Shopee) **tự suy giá theo `deliveryCode`** từ shipment của hãng vận chuyển, và **loại `ShippingSellerProtectionFeeAmount`** (phí đặc thù Shopee, có trong `A.invoiceDelivery.feeJson`). Hệ quả: `B.price = A.price − protectionFee`. Vì `create`/`delete+resync` đều recreate với cùng deliveryCode → B lại suy ra cùng giá thiếu → **lặp vô hạn**. Bằng chứng: 15/25 đơn lệch, đa số lệch **đúng 2700** = `ShippingSellerProtectionFeeAmount`.

Phụ: `update_invoice_delivery_price_by_code` (PUT giá ship riêng) đã có sẵn nhưng **không caller nào** trong luồng sync.

**Quyết định fix (đã chốt):**
- **Bỏ delete+resync cho lệch giá ship** — nó không bao giờ sửa được giá → vô nghĩa, chỉ tạo churn.
- Thay bằng **PUT giá trực tiếp đúng 1 lần** khi `B.price != A.price`:
  - `update_invoice_delivery_price_by_code(invoice_code, A.invoiceDelivery.price)`.
  - **Read-back** `B.invoiceDelivery.price`: khớp → done; vẫn lệch → `log.warning("ship B-limited %s: A=%s B=%s", code, a_price, b_price)`, **KHÔNG** retry trong cùng lượt (không delete-resync).
- **Không lưu cờ B-limited** (chọn đơn giản): lượt reconcile sau nếu vẫn lệch sẽ PUT lại 1 lần/giờ — chấp nhận, vì đã hết churn nặng (không còn delete+recreate).
- Áp cho cả 2 luồng: `sync_missing` (sau create) và `update_delivery_detail` (đơn đã tồn tại).

Field đồng bộ thuần là `invoiceDelivery.price`; `feeJson`/protection fee chỉ để giải thích delta, không ghi sang B.

## 5. Test (TDD)

`backend/apps/kiotviet/tests/test_invoice_payload_builder.py` — viết lại theo hành vi mới:

- Đơn có thuế: assert `price == priceAfterTax`, `discount == discountAfterTax` (per-unit), header lấy từ field thật.
- Đơn không thuế nhiều qty: assert per-unit, total tái lập đúng.
- Đơn data thiếu (không đủ field) → assert **không** tự bịa số + parity warning được phát.
- Status: test conflict guard cap `delivery 3→2` khi invoice≠1; test copy mã 7/9; test read-back warning khi B≠A (mock `_kiotviet_request`).
- Phí ship: test khi `B.price != A.price` → gọi `update_invoice_delivery_price_by_code` đúng 1 lần (mock), read-back lệch → log.warning + KHÔNG delete-resync; khi khớp → không PUT.

Các test cũ assert hành vi suy luận (`test_infers_discount...`, `test_combines_product_and_invoice_discount...`, `test_falls_back_when_line_discount_breaks_total_invariant`) sẽ được thay bằng test hành vi trung thực.

## 6. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| ~~B diễn giải discount per-line~~ | ĐÃ xác minh per-unit (test-create 2026-06-23) → gửi thẳng, không còn rủi ro |
| `priceAfterTax` đôi khi sai (= pre-tax) ở 1 số đơn KiotViet | Parity guard log.warning bắt được; không ép số |
| KiotViet auto-complete khi delivery=3 | Conflict guard cap delivery=2 (§4.4); read-back log |
| Giá ship B-limited (B bỏ protection fee Shopee) | Không sửa được do KiotViet B suy giá từ carrier; chỉ PUT 1 lần + log, hết delete-resync churn (§4.6) |
| PUT giá ship có thể cũng không override (B re-derive từ carrier) | Chưa verify trên đơn có code thật; nếu PUT không ăn → read-back lệch → log B-limited, không loop |
| Đổi lõi builder ảnh hưởng webhook push (dùng chung builder) | Rà các caller của `build_*`; chạy full test suite kiotviet |

## 7. Ngoài phạm vi

- Không đổi cơ chế map id (saleChannelId/soldById/customer) — giữ nguyên.
- Không đổi luồng purge cancelled/duplicates, sync_missing detection.
- Không rewrite sang webhook realtime — vẫn cron hourly.
