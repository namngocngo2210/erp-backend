# Reconcile KiotViet: tạo đơn ở trạng thái neo + xoá-tạo-lại thay 3 mode update

> Branch: `feat/reconcile-delete-recreate-flow`
> Ngày: 2026-07-10 · Trạng thái: **THIẾT KẾ — chờ duyệt spec**

## 1. Bối cảnh & vấn đề

Reconcile mirror hoá đơn từ kênh A (mới) → kênh B (cũ). Mirror B mang description
`"SYNC từ kênh {branchId} - {Acode}"`; map A↔B dựa vào regex trên chuỗi này.

Hiện mỗi cặp chạy 6 mode tuần tự
(`reconcile.py:RECONCILE_MODES`):
`purge_cancelled → purge_duplicates → sync_missing → update_price → update_delivery_detail → update_status`.
3 mode `update_*` cố gắng **PUT sửa tại chỗ** đơn B đã tồn tại.

**Hai vấn đề đang gặp:**

1. **Phí giao hàng + mã vận đơn lệch.** Đơn B được tạo với `deliveryDetail.status = 1`
   (Chờ xử lý — `invoice_payload_builder.py:479`). Ở trạng thái này KiotViet **không neo
   được mã vận đơn + phí ship**; các lần PUT sửa sau bị ignore. → phí ship B lệch A.
2. **Trạng thái hoá đơn / vận chuyển không ép được.** `update_status` PUT sửa tại chỗ bị
   KiotViet chặn trên đơn "Hoàn thành" (`_sync_service_sync_mixin.py:163` → `SKIP [B-completed]`),
   và cơ chế PUT deliveryDetail/giá bị silently-ignore trên đơn đã tạo.

### Mã trạng thái (thống nhất)
- **Invoice status:** `1` = Hoàn thành, `2` = Đã hủy, `3` = Đang xử lý ("chờ xử lý").
- **Delivery status:** `1` = Chờ xử lý, `2` = Đang giao, `3` = Giao thành công, `4/5` = Đang/Đã
  chuyển hoàn, `6` = Đã hủy.

## 2. Mục tiêu

- Tạo đơn B ở **trạng thái neo cố định** giữ chắc mã vận đơn + phí ship, rồi hội tụ về đúng A.
- Bỏ luồng PUT-sửa-tại-chỗ. **Hễ B lệch A là xoá đơn B, tạo lại hoàn toàn** theo flow mới.
- Gộp `sync_missing + update_price + update_delivery_detail + update_status` thành **một pass**
  duy nhất → `RECONCILE_MODES` từ 6 còn 3.
- Không churn (không xoá-tạo-lại vô tận).

## 3. Tiền đề cần verify ở Sprint 0 (BẮT BUỘC trước khi ghi)

Thiết kế dựa trên 3 giả định về hành vi KiotViet — code hiện tại tin ngược lại một số, nên
**phải test trên đơn thật** trước khi build write-path:

- **T1 — Neo phí ship:** tạo B với `deliveryDetail.status=2` + `deliveryCode` + `price` →
  đọc lại xác nhận mã VĐ + phí ship **bám** (không bị reset). *(Gốc fix vấn đề 1.)*
- **T2 — Auto-complete:** set B `delivery=3` → đọc lại xem `invoice.status` có bị ép `1` không.
  Quyết định giá trị cờ `RECONCILE_DELIVERY_EXACT_MIRROR`. *(Doc 21h §5b khẳng định CÓ auto-complete;
  cần tái xác nhận trên retailer B.)*
- **T3 — Xoá đơn Hoàn thành:** DELETE 1 đơn B đã `invoice=1` → xác nhận **xoá được**. Đây là
  tiền đề cho toàn bộ mô hình delete+recreate. *(Code hiện né delete trên đơn hoàn thành, chưa
  chứng minh; user khẳng định xoá được.)*

**Nếu T1 hoặc T3 fail → dừng, xem lại thiết kế.**

## 4. Thiết kế

### 4.1 Routine tạo đơn B — 2 pha (dùng chung cho tạo mới & tạo-lại-sau-xoá)

**Pha 1 — Tạo ở trạng thái neo (bỏ qua trạng thái thật của A):**
- POST `/invoices` với `deliveryDetail.status = 2` (Đang giao), `deliveryCode = A.deliveryCode`,
  `price = A.deliveryPrice`, `codAmount = A.total`.
- Invoice status để KiotViet mặc định (`3` = Đang xử lý).
- Kết quả: B ở `(invoice=3, delivery=2)`, **mã vận đơn + phí ship được neo chắc**.
- *(Nếu T1 cho thấy POST không nhận `delivery=2`+code ngay: fallback POST rồi PUT deliveryDetail
  lần 2 để neo — vẫn trong routine tạo.)*

**Pha 2 — Hội tụ về đúng A:**
- PUT delivery → `A.delivery_status`, rồi PUT invoice → `A.invoice_status` (KiotViet yêu cầu
  delivery đạt "Giao thành công" trước khi invoice "Hoàn thành").
- Read-back xác nhận B khớp A; lệch do KiotViet ép thì log (không retry — xem 4.3).

Thay đổi so với hiện tại: `deliveryDetail.status` `1 → 2` ở `build_target_invoice_payload`;
`patch_invoice_status_after_create` **bỏ CAP 3→2** (trừ ca ngoại lệ theo cờ — xem 4.3).

### 4.2 Vòng reconcile hợp nhất — `reconcile_sync` (thay 3 mode update + sync_missing)

Một pass, chỉ **đọc + quyết định xoá** (không PUT sửa tại chỗ):

1. Fetch A (public API, `orderBy=id` Asc, `includeInvoiceDelivery`). Bỏ đơn A đã hủy
   (`invoice=2` / statusValue cancelled / `delivery=6`).
2. Pre-scan B mirror 1 lần (public, `orderBy=id`) → map
   `source_code → {id, invoice_status, delivery_status, total, delivery_price, delivery_code}`
   qua regex description.
3. Với mỗi đơn A:
   - **Không có B mirror** → tạo mới (4.1).
   - **Có B mirror** → so **4 chiều**:
     - `total` (tolerance ±1đ — chống churn do làm tròn),
     - `delivery_price` (bằng đúng),
     - `invoice_status`,
     - `delivery_status`.
     Lệch bất kỳ chiều nào (trừ ngoại lệ 4.3) → **DELETE B + tạo lại** (4.1).
   - Khớp hết → bỏ qua.

`RECONCILE_MODES` rút còn: `purge_cancelled → purge_duplicates → reconcile_sync`.

### 4.2b Đơn không có giao hàng → B Giao thành công (bổ sung 2026-07-10)

Đơn A **không có block giao hàng** (`delivery_status = None` — bán tại quầy / nhận tại chỗ, không
vận chuyển) coi như đã hoàn tất: `expected_b_state` trả `(invoice=1, delivery=3)` — B set Giao
thành công, KiotViet auto-complete hóa đơn → Hoàn thành. Không điều kiện (mọi đơn no-delivery,
không phụ thuộc cờ). Đơn no-delivery còn ở Đang giao(2)/chưa hoàn thành trên B sẽ bị xoá + tạo lại
để đẩy lên trạng thái này.

### 4.3 Ca ngoại lệ duy nhất & convergence guard

Chỉ `delivery=3` kích hoạt auto-complete; `delivery=2` thì không. Bảng đầy đủ:

| A (nguồn) | B mirror | Khớp A? |
|---|---|---|
| Đang giao (del=2) + Đang xử lý (inv=3) | del=2, inv=3 | ✅ tuyệt đối |
| Giao thành công (del=3) + Hoàn thành (inv=1) | del=3 → auto inv=1 | ✅ tuyệt đối |
| Đã hủy | (purge_cancelled loại) | — |
| **Giao thành công (del=3) + Đang xử lý (inv=3)** | del=3 → KiotViet ép inv=1 ≠ A | ⚠️ **ca duy nhất lệch** |

Ca cuối tồn tại vì **bất đối xứng cấu hình**: kênh A không auto-complete, kênh B có (nhóm
~1.693 đơn theo doc 21h §5b). Với ca này, xoá-tạo-lại vẫn cho ra B(del=3, inv=1) ≠ A(inv=3) →
churn mỗi đêm nếu không guard.

**Xử lý — cờ `RECONCILE_DELIVERY_EXACT_MIRROR` (settings, mặc định `False`):**
- **False (an toàn, mặc định):** routine tạo cho ca này set B `delivery=2` (không đẩy lên 3).
  Vòng so sánh coi B đã khớp (guard). ⇒ invoice khớp A, **không hoàn-thành-giả, không churn**.
  Đánh đổi: nhãn delivery B hiển thị "Đang giao" thay vì "Giao thành công" tới khi A hoàn thành.
- **True:** set B `delivery=3` (chấp nhận KiotViet ép inv=1). Guard so sánh: `B.invoice==1` &&
  `A.invoice==3` coi như khớp. ⇒ delivery khớp A, invoice lệch tạm (sổ sách), **không churn**.
  Chỉ bật khi T2/nghiệp vụ xác nhận chấp nhận hoàn-thành-giả.

**Bất biến:** dù cờ nào, guard đảm bảo ca A(del=3, inv=3) **không bao giờ** trigger xoá-tạo-lại
lặp lại.

## 5. Cấu trúc code — file ảnh hưởng

| File | Thay đổi |
|---|---|
| `infrastructure/services/reconcile.py` | `RECONCILE_MODES` 6 → 3 (`purge_cancelled, purge_duplicates, reconcile_sync`) |
| `infrastructure/services/sync_invoices_service.py` | Xoá nhánh `update_price/update_delivery_detail/update_status` trong `run()`; thêm mode `reconcile_sync` (fetch A + prescan B → so 4 chiều → delete+recreate) |
| `infrastructure/services/_sync_service_sync_mixin.py` | **Xoá** `_update_status` (toàn bộ PUT status tại chỗ). Giữ `_sync_invoice` (tạo đơn) |
| `infrastructure/services/invoice_payload_builder.py` | `build_target_invoice_payload`: `deliveryDetail.status` `1 → 2`, gắn chắc `deliveryCode`+`price`. `patch_invoice_status_after_create`: bỏ CAP 3→2 (trừ ngoại lệ theo cờ) |
| `config/settings_parts/*` | Thêm `RECONCILE_DELIVERY_EXACT_MIRROR` (default False) |

**Không đổi interface:** `tasks.py`, `management/commands/reconcile_channels.py` vẫn gọi
`reconcile_one_pair`; `purge_cancelled`, `purge_duplicates` giữ nguyên.

## 6. Test

- **Sprint 0 (live):** T1, T2, T3 (mục 3).
- **Unit:**
  - `build_target_invoice_payload`: khẳng định `deliveryDetail.status==2`, có `deliveryCode`,
    `price==A`, `codAmount==A.total`.
  - So-4-chiều: từng chiều khớp/không khớp (gồm tolerance ±1đ total), + guard ca A(del=3,inv=3)
    ở cả 2 giá trị cờ.
  - `reconcile_sync`: mock KiotViet — đơn thiếu → create; đơn lệch → delete+recreate; đơn khớp
    → no-op.
- **Dry-run e2e:** `reconcile_channels --dry-run` window nhỏ trên prod; đếm sẽ-xoá/sẽ-tạo.

## 7. Kế hoạch sprint

- [ ] **S0 — Verify live** (T1/T2/T3). Chặn nếu T1/T3 fail. Chốt giá trị cờ theo T2.
- [ ] **S1 — Routine tạo 2 pha** (del=2 + code + fee → patch A, bỏ cap sau cờ) + unit test payload.
- [ ] **S2 — `reconcile_sync`** (so 4 chiều + delete+recreate + guard) thay 3 mode; rút `RECONCILE_MODES`.
- [ ] **S3 — Xoá code chết** (`_update_status` + nhánh update trong `run()`); dọn test cũ.
- [ ] **S4 — Dry-run prod** + đối chiếu 1 đêm: churn=0, phí ship khớp, không hoàn-thành-giả (nếu cờ False).

## 8. Rủi ro

- **Delete đơn Hoàn thành thất bại (T3 fail)** → toàn bộ mô hình sập; phải quay lại PUT/skip. Verify trước.
- **Churn** nếu guard ca A(del=3,inv=3) sai → dry-run + đếm 1 đêm bắt buộc trước khi bật cron.
- **Tải API tăng tạm** khi lần chạy đầu xoá-tạo-lại loạt đơn tồn đọng lệch; cân nhắc chạy window nhỏ,
  giờ thấp điểm, có `sleep_pushes`.
- **Lock per-cặp** (`kv:reconcile_pair:lock`) giữ nguyên — không chạy chồng cron 21h.
