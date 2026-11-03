# Thiết kế: Trang sản phẩm khi quét mã bảo hành (`/sp/<mã>`)

**Ngày:** 2026-08-10
**Trạng thái:** Chờ duyệt spec
**Nhánh:** `feat/product-scan-page`
**Tiền đề:**
- `docs/warranty-codes-design.md` — giai đoạn 1, phát hành mã (đã merge)
- `docs/warranty-portal-design.md` — giai đoạn 2, portal `/bao-hanh` (đã merge)

## 1. Bối cảnh & mục tiêu

Cột `URL` trong file Excel gửi nhà in tem đang dựng từ
`settings.WARRANTY_QR_BASE_URL = "http://qcheck.vn/"`
(`apps/warranty_codes/exports_definitions.py`), tức QR trên tem trỏ về hệ thống
của iCheck — không phải hệ thống của công ty. Khách quét tem sẽ đọc nội dung do
iCheck kiểm soát, ta không chủ động được thông tin sản phẩm, không đo được lượt
quét, và phụ thuộc một bên thứ ba cho mặt tiền thương hiệu.

Tài liệu này (giai đoạn 3) làm 3 việc, phụ thuộc nhau theo thứ tự:

1. **Đổi đích của QR**: `WARRANTY_QR_BASE_URL` → `https://delax.com.vn/sp/`.
2. **Dựng kho nội dung sản phẩm** trong hệ thống: bảng `product_profiles`,
   import một lần từ file Excel nội dung của công ty, màn quản trị trong CRM.
3. **Dựng trang public `/sp/<mã>`**: quét QR ra thông tin sản phẩm, dấu xác thực
   chính hãng, số lần quét, và **kích hoạt bảo hành ngay trên trang**.

### Quyết định đã chốt với chủ dự án

| Câu hỏi | Quyết định |
| --- | --- |
| Trang đích khi quét | Trang sản phẩm `/sp/<mã>`, gộp luôn khối kích hoạt bảo hành — khách quét một lần là xong, không phải sang màn thứ hai |
| Nội dung hiển thị | Ảnh + tên + mô tả; nhãn xác thực chính hãng + serial; số lần quét + lần quét đầu; thông tin doanh nghiệp + hotline |
| Nội dung gắn theo đơn vị nào | Theo **từng SKU** (`Product`), không theo nhóm sản phẩm |
| Domain in lên tem | `delax.com.vn` — đã trỏ sẵn về VPS này, đang serve chính bundle portal (`/bao-hanh` trả 200) |
| Giá | **Không hiển thị, không lưu, không import** — API public không có trường giá nào |
| Tem đã in | Chưa in lô nào với link qcheck ⇒ không cần xử lý tương thích ngược |
| Nguồn nội dung | File `Thông tin sản phẩm Desi Global.xlsx` của công ty, qua một file trung gian phẳng do người duyệt/sửa |

### Ngoài phạm vi (YAGNI)

- Giá bán, nút mua hàng, giỏ hàng trên trang `/sp/`.
- Đa ngôn ngữ (iCheck có VI/EN/IN; ta chỉ tiếng Việt).
- Trang danh mục sản phẩm public (`/sp/` chỉ vào được qua mã cụ thể).
- Upload nội dung bằng Excel trong UI — import là việc một lần, chạy bằng
  management command.
- Đánh giá/bình luận của khách, chia sẻ mạng xã hội.
- Resize/nén ảnh phía server; chỉ chặn dung lượng và định dạng.

## 2. Nguồn dữ liệu: file Excel của công ty

File gốc `Thông tin sản phẩm Desi Global.xlsx` (7 sheet). Hai sheet dùng được:

| Sheet | Kích thước | Nội dung |
| --- | --- | --- |
| `Danh mục` | 210 × 27 | 25 SKU: nhóm sản phẩm, tên, model, link Shopee/TikTok/Web, link YouTube |
| `Thông tin chi tiết sản phẩm` | 1566 × 27 | **22 khối** SKU, mỗi khối gồm: tên, model, mô tả điểm nổi bật (cặp tính năng → lợi ích), thông số kỹ thuật, đối tượng nên dùng / cần hỏi bác sĩ, FAQ, hướng dẫn sử dụng theo bước, bảng xử lý sự cố, link video |

### 2.1 Bốn vấn đề của file gốc

1. **Không có ảnh nào.** `worksheet._images` = 0 ở cả hai sheet, cột
   "ẢNH SẢN PHẨM" rỗng hoàn toàn. Ảnh phải upload tay qua màn quản trị CRM —
   không có đường nào lấy tự động từ file này.
2. **FAQ viết theo giọng script bán hàng.** Ví dụ nguyên văn: *"Dạ Anh/Chị yên
   tâm ạ. Khi nhận hàng mình được kiểm tra trước khi thanh toán..."*, và có cả
   câu hướng nội bộ như *"Bên khác có hàng y vậy giá rẻ hơn, chỉ 1tr2?"*. Đưa
   nguyên văn lên trang public sẽ đọc rất kỳ. ⇒ Import hết vào DB nhưng
   **khối FAQ mặc định tắt**; đội nội dung biên tập rồi mới bật.
3. **Dữ liệu nội bộ nằm lẫn.** Sheet `Danh mục` có cột "Giá tối thiểu bán cho
   khách lẻ mua lần đầu" — tuyệt đối không được ra ngoài. Vì đã chốt bỏ hẳn giá
   khỏi phạm vi nên **không import bất kỳ cột giá nào**, rủi ro này bị loại từ
   gốc chứ không phải chặn ở tầng serializer.
4. **Dữ liệu lệch giữa hai sheet.** `Danh mục` có 25 SKU, sheet chi tiết chỉ có
   22 khối. `MSC02-CAOCAP` và `DC20` bị gộp chung một khối (ô model ghi
   `"MSC02-CAOCAP/ DC20 (Hai mã giống nhau, chỉ khác ở chỗ DC20 to hơn)"`).
   Script chuyển đổi **không tự đoán**: gặp ô model không phải một SKU sạch thì
   để nguyên vào file trung gian và in cảnh báo, người sửa tay.

### 2.2 Quy trình import: file gốc → file phẳng → DB

Không viết parser đọc thẳng file gốc. Layout khối, merge cell, và các ngoại lệ ở
§2.1 khiến parser đó vừa khó test vừa chỉ dùng được đúng một lần. Thay vào đó
tách hai bước:

```
Thông tin sản phẩm Desi Global.xlsx   (file gốc, KHÔNG commit)
        │
        │  scripts/convert_product_excel.py      ← đồ dùng một lần
        ▼
backend/data/product-content.xlsx     (file phẳng, người duyệt/sửa, CÓ commit)
        │
        │  manage.py import_product_profiles     ← command chính thức, có test
        ▼
      product_profiles + product_profile_images
```

Cái được thêm ngoài việc code dễ hơn: **file trung gian người sửa được**. Đội
nội dung mở ra biên tập giọng FAQ, bật/tắt cờ hiển thị, sửa chính tả, điền tay
hai SKU thiếu — rồi mới import. Không phải sửa trong code, không phải đụng file
gốc đang dùng cho việc khác (sheet quy trình chốt đơn, bảng giá...).

**Không commit file gốc**: nó chứa giá tối thiểu bán lẻ và toàn bộ script chốt
đơn — dữ liệu vận hành nội bộ, không nên nằm trong git. File phẳng chỉ chứa nội
dung công bố ra ngoài nên commit được, và nhờ vậy chạy lại import trên prod
không cần chép file thủ công.

### 2.3 Định dạng file phẳng

Mỗi sheet là một bảng thật (hàng 1 là header, không merge cell), khoá chung là
cột `sku`. Tên sheet và cột đặt tiếng Việt không dấu để đội nội dung đọc được.

| Sheet | Cột |
| --- | --- |
| `san_pham` | `sku`, `ten`, `mo_ta_ngan`, `cong_bo` (0/1), `khoi_hien_thi` (danh sách khối, phân tách bằng dấu phẩy) |
| `diem_noi_bat` | `sku`, `thu_tu`, `tieu_de`, `chi_tiet` |
| `thong_so` | `sku`, `thu_tu`, `nhan`, `gia_tri` |
| `doi_tuong` | `sku`, `loai` (`nen_dung` \| `can_hoi_bac_si`), `thu_tu`, `noi_dung` |
| `huong_dan` | `sku`, `thu_tu`, `tieu_de`, `chi_tiet` |
| `su_co` | `sku`, `thu_tu`, `hien_tuong`, `nguyen_nhan`, `cach_xu_ly` |
| `faq` | `sku`, `thu_tu`, `cau_hoi`, `tra_loi`, `hien_thi` (0/1, script đặt mặc định 0) |
| `video` | `sku`, `thu_tu`, `nhan`, `url` |

Giá trị hợp lệ của `khoi_hien_thi`: `diem_noi_bat`, `thong_so`, `doi_tuong`,
`huong_dan`, `su_co`, `faq`, `video`. Script sinh sẵn tất cả trừ `faq`.

### 2.4 Command import

`manage.py import_product_profiles --file backend/data/product-content.xlsx [--dry-run] [--only-sku BNC02]`

- Khớp `sku` với `Product.sku` (`deleted_at__isnull=True`). SKU không khớp thì
  **bỏ qua và in cảnh báo**, không tạo `Product` mới — danh mục sản phẩm là
  nguồn sự thật của CRM, không để file nội dung đẻ sản phẩm ma.
- **Idempotent**: chạy lại ghi đè toàn bộ profile của đúng các SKU có trong file,
  không đụng SKU khác. Ảnh (`product_profile_images`) **không bị xoá** khi import
  lại — ảnh do người upload, file Excel không có ảnh nên không có quyền quyết
  định về ảnh.
- `--dry-run` in báo cáo (số SKU khớp / không khớp / số bản ghi mỗi khối) và
  không ghi gì vào DB.
- `--only-sku BNC02` chỉ import đúng một SKU, dùng khi biên tập lại nội dung một
  sản phẩm mà không muốn động vào phần còn lại. Có thể lặp lại nhiều lần
  (`--only-sku BNC02 --only-sku DC30`).
- Chạy trong một transaction cho toàn bộ file: lỗi giữa chừng thì rollback sạch.
- Test bằng fixture xlsx nhỏ vài dòng sinh trong `tmp_path`, không dùng file thật.

## 3. Data model

Đặt trong `apps/orders` — ngay cạnh `Product`, không tạo app mới: đây là thuộc
tính mô tả của sản phẩm trong danh mục, không phải nghiệp vụ riêng. App
`warranty_codes` chỉ **đọc** qua `WarrantyCode.product`, giữ đúng chiều phụ thuộc
hiện có (`warranty_codes` → `orders`), không sinh phụ thuộc ngược.

### 3.1 `product_profiles` (mới)

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `product` | OneToOne → `orders.Product`, CASCADE | 1 SKU = 1 trang |
| `short_description` | varchar(500), blank | Dòng tóm tắt dưới tên sản phẩm |
| `highlights` | jsonb, default `[]` | `[{title, detail}]` — MÔ TẢ SẢN PHẨM |
| `specs` | jsonb, default `[]` | `[{label, value}]` — THÔNG SỐ KỸ THUẬT |
| `audience` | jsonb, default `{}` | `{suitable: [str], caution: [str]}` |
| `usage_steps` | jsonb, default `[]` | `[{title, detail}]` |
| `troubleshooting` | jsonb, default `[]` | `[{symptom, cause, fix}]` |
| `faqs` | jsonb, default `[]` | `[{question, answer, visible: bool}]` |
| `videos` | jsonb, default `[]` | `[{label, url}]` |
| `visible_sections` | jsonb, default `[]` | Danh sách khối được hiện trên trang public |
| `is_published` | bool, default False | Tắt = API trả `product` chỉ có `sku` + `name` |
| `updated_by` | FK `users.User`, null, SET_NULL | |
| `updated_at` | datetime, auto_now | |

Index: unique(`product`) do OneToOne; thêm `(is_published)` cho màn quản trị lọc.

**Toàn bộ trường text lưu plain text, giữ ký tự xuống dòng, KHÔNG lưu HTML.**
Frontend render bằng `<p>`/`<li>` tách theo `\n`, không dùng
`dangerouslySetInnerHTML`. Đổi lại mất khả năng in đậm/màu trong mô tả — chấp
nhận, vì trang này là trang xác thực sản phẩm chứ không phải landing bán hàng,
và nó xoá sạch cả một lớp rủi ro XSS trên một trang public không đăng nhập.

`videos[].url` được validate là `http(s)` lúc import và lúc lưu ở CRM; frontend
render thành link `target="_blank" rel="noopener noreferrer"`, **không nhúng
iframe** (link hiện tại là Google Drive/YouTube lẫn lộn, nhúng iframe kéo theo
CSP và tracking của bên thứ ba vào trang public).

### 3.2 `product_profile_images` (mới)

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `profile` | FK → `ProductProfile`, CASCADE, related_name=`images` | |
| `object_key` | varchar(255) | Khoá MinIO, bucket `MINIO_BUCKET_PRODUCTS` |
| `content_type` | varchar(64) | Lưu sẵn để phục vụ file không phải head bucket |
| `alt_text` | varchar(255), blank | |
| `sort_order` | smallint, default 0 | Ảnh đầu tiên là ảnh đại diện |
| `created_at` | datetime | |

Index: (`profile`, `sort_order`).

Ràng buộc ở tầng service: tối đa **8 ảnh/sản phẩm**, mỗi ảnh **≤ 5MB**, chỉ
`image/jpeg`, `image/png`, `image/webp` (kiểm tra bằng magic bytes chứ không tin
`Content-Type` client gửi). Dùng lại đúng pattern
`apps/inventory/infrastructure/document_storage.py`.

### 3.3 Sửa `warranty_codes.WarrantyCode`

Thêm 2 cột phục vụ khối "số lần quét":

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `scan_count` | int, default 0 | Số **phiên** quét, tăng bằng `F()` |
| `first_scanned_at` | datetime, null | Lần quét đầu tiên |

**Vì sao không đếm từ `warranty_code_scan_logs`:** task retention
`purge_old_scan_logs` xoá log cũ hơn 180 ngày, nên số đếm sẽ tụt dần theo thời
gian — khách quét lại tem sau một năm sẽ thấy "1 lần quét" dù đã quét 20 lần.
Counter trên chính mã là dữ liệu vĩnh viễn, và cũng rẻ hơn một `COUNT(*)` mỗi
lượt xem trang.

**Chống thổi số khi F5:** một phiên = một cặp (mã, IP) trong một ngày. Dùng khoá
Redis `sp:seen:<code>:<ip_hash>:<YYYY-MM-DD>` TTL 24h; `SET NX` thành công mới
tăng counter. Redis chết thì bỏ qua việc tăng (fail-open, không chặn trang) và
ghi cảnh báo — thà đếm thiếu còn hơn chặn khách xem sản phẩm.

### 3.4 Thêm `ScanResult.PAGE_VIEWED`

Enum `apps.warranty_codes.models.ScanResult` thêm giá trị
`PAGE_VIEWED = "page_viewed", "Xem trang sản phẩm"`. Mọi lượt gọi API trang sản
phẩm đều ghi một dòng `warranty_code_scan_logs` (kể cả F5 — log là dấu vết thô,
khác với counter đã dedupe ở §3.3), nên tab Nhật ký trong CRM vẫn phát hiện được
tem bị sao chép qua mẫu "một mã bị quét dồn dập từ nhiều IP lạ".

Tab Nhật ký (`GET /api/warranty-codes/scan-logs`) không phải sửa gì ngoài việc
badge kết quả có thêm một nhãn.

## 4. API

### 4.1 `GET /api/warranty-portal/products/<code>` (public)

Đăng ký trong `apps/warranty_codes/portal_urls.py`, dùng lại throttle scope
`warranty_portal_read` (120/giờ) — không thêm scope mới, không phải sửa env prod.

Dùng `GET` (khác `/activate` và `/track` dùng `POST`) vì mã nằm sẵn trên URL
QR rồi; ép `POST` cũng không giấu thêm được gì mà lại mất luôn việc mở link trực
tiếp từ camera điện thoại.

Response gắn `Cache-Control: no-store`. Endpoint này **có tác dụng phụ** (tăng
counter, ghi log) nên nếu trình duyệt hay proxy cache lại thì số lần quét và
nhật ký sẽ thiếu request. Ảnh (§4.2) mới là thứ được cache, và cache ở đó vô hại
vì không có tác dụng phụ nào.

Xử lý:

1. Chuẩn hoá `code`: trim + upper, cắt 64 ký tự (dùng lại
   `activation_service.normalize_keyword`).
2. Tìm `WarrantyCode` theo `code` khớp chính xác. **Chỉ khớp `code`, không khớp
   `serial`** — giữ đúng nguyên tắc bảo mật đã chốt 06/08: `serial` in công khai
   trên tem và sinh tuần tự nên tra được serial là dò được cả dải mã.
3. Không tìm thấy → ghi log `not_found` → 404
   `{"detail": "Mã không hợp lệ. Sản phẩm có thể không phải hàng chính hãng.", "code": "not_found"}`.
4. Tìm thấy → tăng counter (§3.3), ghi log `page_viewed`, trả 200.

Response 200:

```json
{
  "code": "A8K2M4P9QX",
  "serial": "DX-000123",
  "authentic": true,
  "scan": { "count": 3, "first_at": "2026-08-10T09:00:00+07:00" },
  "product": {
    "sku": "BNC02",
    "name": "Bồn Ngâm Chân Massage Bấm Huyệt DELAX",
    "short_description": "...",
    "images": ["/api/warranty-portal/product-images/12"],
    "highlights": [{ "title": "24 bi lăn massage 3D", "detail": "..." }],
    "specs": [{ "label": "Công suất", "value": "800W" }],
    "audience": { "suitable": ["..."], "caution": ["..."] },
    "usage_steps": [{ "title": "Bước 1: Mở rộng thân bồn", "detail": "..." }],
    "troubleshooting": [{ "symptom": "...", "cause": "...", "fix": "..." }],
    "faqs": [{ "question": "...", "answer": "..." }],
    "videos": [{ "label": "Hướng dẫn sử dụng", "url": "https://..." }]
  },
  "warranty": null,
  "company": {
    "name": "Công ty CP Desi Global", "address": "...", "tax_code": "...",
    "hotline": "...", "zalo_url": "...", "facebook_url": "..."
  }
}
```

`faqs` chỉ gồm các mục có `visible: true`; cờ `visible` là dữ liệu quản trị nên
**không xuất hiện trong response public** (§3.1 lưu nó, §4.1 lọc rồi bỏ đi).

Bốn nhánh dữ liệu:

| Tình huống | Trả về |
| --- | --- |
| Mã sai | 404 `not_found` |
| Mã đúng, lô chưa gắn sản phẩm (`reserved`) | 200, `product: null` — trang vẫn hiện dấu xác thực + serial, kèm dòng "đang cập nhật thông tin sản phẩm" |
| SKU chưa có profile hoặc `is_published=false` | 200, `product` chỉ có `sku` + `name`, các khối rỗng |
| Đủ nội dung | 200 đầy đủ, chỉ gồm các khối có trong `visible_sections` |

`warranty` = `null` nếu mã chưa kích hoạt; đã kích hoạt thì
`{activated_at, expires_at, days_left, months}` — **cùng shape với response của
`/activate`** để frontend dùng lại đúng component `WarrantyCard`, không đẻ kiểu
dữ liệu thứ hai.

Cố ý **không trả**: `id` của bất kỳ bảng nào (ngoài `id` ảnh, vốn phải có để dựng
URL), giá, `batch_code`, thông tin nhân viên, `phone` của activation. Danh sách
key được **assert khớp tuyệt đối** trong test — thêm trường mới bắt buộc phải sửa
test đó, đúng cơ chế chốt chặn đã áp cho `/track`
(`test_track_valid_pair_returns_order_and_journey`).

### 4.2 `GET /api/warranty-portal/product-images/<id>` (public)

Stream ảnh từ MinIO. `Cache-Control: public, max-age=31536000, immutable` —
`id` là khoá bất biến, ảnh sửa thì tạo bản ghi mới nên cache vĩnh viễn an toàn.

**Không áp throttle của portal cho endpoint này.** Một trang có tới 8 ảnh, cộng
với lần gọi `/products/<code>` là 9 request cho một lượt xem — bucket
`warranty_portal_read` 120/giờ sẽ cạn sau ~13 lượt, và zone nginx `desi_portal`
(10r/m, burst 10) chặn ngay từ lượt đầu. Vì vậy ảnh cần **cả hai tầng đều nới**:
DRF không gắn throttle class, và nginx phải có `location` riêng cho
`/api/warranty-portal/product-images/` đặt **trước** location chung (§6.2).
Rủi ro chấp nhận được: endpoint chỉ đọc object bất biến theo `id`, không tiết lộ
gì về mã bảo hành, và cache `immutable` khiến khách quay lại không gọi lại.

### 4.3 API quản trị (CRM, quyền `products:edit`)

Quyền `products` đã có sẵn trong `apps/users/rbac_catalog.py` với các action
`view/create/edit/delete/export` — **không cần seed quyền mới**, không cần
migration RBAC.

| Endpoint | Quyền | Việc |
| --- | --- | --- |
| `GET /api/products/<id>/profile` | `products:view` | Đọc nội dung để hiện form |
| `PUT /api/products/<id>/profile` | `products:edit` | Lưu toàn bộ nội dung (upsert) |
| `POST /api/products/<id>/profile/images` | `products:edit` | Upload 1 ảnh (multipart) |
| `DELETE /api/products/<id>/profile/images/<img_id>` | `products:edit` | Xoá ảnh (xoá cả object MinIO) |
| `PUT /api/products/<id>/profile/images/order` | `products:edit` | Sắp xếp lại thứ tự |

## 5. Frontend

### 5.1 Trang public `/sp/<mã>`

Rẽ nhánh trong `frontend/src/main.tsx` như `/bao-hanh` đang làm:
`path.startsWith("/sp/")` → `ProductScanPage` (lazy), đặt **trước** `<App/>` nên
không dính `AuthProvider`/`RequireAuth`.

Đặt trong feature `warranty-portal` chứ không tạo feature mới: dùng chung
`portal-theme.ts` (palette Delax teal→green), `portal-api.ts` (fetch riêng không
interceptor), `error-message.ts`, `phone.ts`, và tái dùng thẳng `WarrantyCard`.
Tách feature mới sẽ phải import chéo hoặc nhân bản bốn file đó.

```
frontend/src/features/warranty-portal/
  pages/ProductScanPage.tsx           # khung trang, gọi API, xử lý 4 nhánh
  components/product/
    ProductGallery.tsx                # carousel ảnh, swipe trên mobile
    AuthenticityHeader.tsx            # ✅ chính hãng + serial + số lần quét
    HighlightList.tsx                 # điểm nổi bật (tính năng → lợi ích)
    SpecTable.tsx                     # bảng thông số
    AudienceNote.tsx                  # nên dùng / cần hỏi bác sĩ
    UsageSteps.tsx                    # hướng dẫn theo bước
    TroubleshootTable.tsx             # hiện tượng / nguyên nhân / cách xử lý
    FaqAccordion.tsx                  # FAQ đóng mở
    VideoLinks.tsx                    # danh sách link video
    CompanyFooter.tsx                 # thông tin doanh nghiệp + hotline
    InlineActivateBlock.tsx           # khối kích hoạt gộp trong trang
  product-page-api.ts                 # gọi 2 endpoint mới
```

Thứ tự khối trên trang (mobile-first — khách quét bằng điện thoại):

```
ảnh sản phẩm
✅ Sản phẩm chính hãng Delax · Serial DX-000123 · Đã quét 3 lần (lần đầu 10/08/2026)
tên + mô tả ngắn
điểm nổi bật
thông số kỹ thuật
──── KHỐI BẢO HÀNH ────
đối tượng nên dùng / cần hỏi ý kiến bác sĩ
hướng dẫn sử dụng
xử lý sự cố
câu hỏi thường gặp
video
footer: doanh nghiệp + hotline
```

Khối bảo hành đặt **giữa trang, ngay sau thông số** chứ không ở cuối: nó là việc
khách cần làm (có hạn hiệu lực), phần dưới là tài liệu tra cứu dần. Đẩy xuống
cuối thì trên mobile khách phải cuộn qua cả bảng xử lý sự cố mới thấy.

- Chưa kích hoạt → ô SĐT + nút "Kích hoạt bảo hành", gọi đúng
  `POST /api/warranty-portal/activate` hiện có với `keyword` = mã trên URL,
  `channel: "web"`. Kèm nguyên văn dòng hướng dẫn đang dùng ở tab Bảo hành:
  *"Vui lòng nhập đúng số điện thoại để được bảo hành, Delax sẽ bảo hành điện tử
  theo số điện thoại đăng ký của quý khách."*
- Đã kích hoạt → render `WarrantyCard` (ngày kích hoạt, hạn, số ngày còn lại).
- Kích hoạt xong → thay tại chỗ bằng `WarrantyCard`, không điều hướng, không
  reload (tránh cộng thêm một lượt quét).

Khối nào không có trong `visible_sections` hoặc dữ liệu rỗng thì **không render
tiêu đề rỗng** — ẩn hẳn cả khối.

### 5.2 Bảo mật của trang public

- `<meta name="robots" content="noindex, nofollow">` — URL chứa mã 10 ký tự bí
  mật, để Google index là công khai hoá cả kho mã.
- `<meta name="referrer" content="no-referrer">` — trang có link ra ngoài
  (YouTube, fanpage, Zalo); không có thẻ này thì mã rò sang bên thứ ba qua header
  `Referer`.
- Hai thẻ này đặt trong `ProductScanPage` bằng effect ghi vào `document.head` và
  gỡ khi unmount, không đụng `index.html` dùng chung với CRM.

### 5.3 Màn quản trị trong CRM

Thêm vào trang `/catalog/products` (route đã có, `Guard section="products"`):
mỗi dòng sản phẩm có nút **"Nội dung trang"** → mở trang con
`/catalog/products/:id/content`.

Form theo shadcn/ui, tông xanh da trời của CRM (khác palette portal — đây là màn
nội bộ), gồm: khối upload ảnh (kéo thả sắp xếp, xem trước), mô tả ngắn, và một
danh sách khối lặp cho từng nhóm nội dung (thêm/xoá/sắp xếp dòng). Mỗi khối có
công tắc hiện/ẩn ghi vào `visible_sections`. Nút "Xem trước" mở
`/sp/<mã đầu tiên của SKU>` ở tab mới — lưu ý việc này **có ghi một lượt quét**,
ghi rõ trên tooltip để người dùng không bất ngờ khi thấy số đếm nhích.

## 6. Hạ tầng

### 6.1 Settings & env

| Biến | Giá trị | Ghi chú |
| --- | --- | --- |
| `WARRANTY_QR_BASE_URL` | `https://delax.com.vn/sp/` | **Đổi default** từ `http://qcheck.vn/` |
| `MINIO_BUCKET_PRODUCTS` | `product-media` | Bucket mới |
| `PORTAL_COMPANY_NAME` | | Footer trang `/sp/` |
| `PORTAL_COMPANY_ADDRESS` | | |
| `PORTAL_COMPANY_TAX_CODE` | | |
| `PORTAL_COMPANY_HOTLINE` | | |
| `PORTAL_COMPANY_ZALO_URL` | | Để rỗng thì ẩn link |
| `PORTAL_COMPANY_FACEBOOK_URL` | | Để rỗng thì ẩn link |

⚠️ **`WARRANTY_QR_BASE_URL` hiện chỉ tồn tại dưới dạng default trong
`config/settings_parts/base.py`** — không có trong `.env.example`, cũng không có
trong cả hai file compose. Nghĩa là hiện tại muốn đổi giá trị trên prod thì phải
build lại image. Phải bổ sung biến này vào `.env.example` + cả hai file compose
cùng đợt, đúng quy ước "thêm env mới thì phải cập nhật ở docker". **Đây là việc
chặn: chưa làm xong thì chưa được xuất Excel gửi nhà in.**

### 6.2 Nginx

Repo chỉ có template cho `customer.delax.vn` và `tra-cuu.delax.asia`, trong khi
prod đang chạy `delax.com.vn` (đã xác nhận: `GET https://delax.com.vn/bao-hanh`
trả 200 với bundle của chính repo này). Cấu hình thật đã lệch khỏi repo.

Đợt này thêm `nginx/delax.com.vn.conf.prod-template` phản ánh đúng thực tế đang
chạy, cộng thêm:

- `location /sp/` → SPA fallback về `index.html` (giống `/bao-hanh`).
- `location /api/warranty-portal/` — đã mở sẵn, giữ zone `desi_portal`
  (10r/m, burst 10) như hiện tại.
- **`location /api/warranty-portal/product-images/` mới, đặt trước location
  chung** — nginx chọn prefix dài nhất nên thứ tự trong file không quyết định,
  nhưng viết trước cho dễ đọc. Zone mới `desi_portal_media` nới hơn hẳn
  (`rate=120r/m`, `burst=30 nodelay`) vì mỗi lượt xem trang kéo tới 8 ảnh; xem
  lý do đầy đủ ở §4.2. Khai báo zone trong `nginx/rate-limit-zones.conf.example`
  và các bản đồng bộ ở `nginx/default.conf`, `nginx/nginx.conf`.

Không mở thêm `/api/` chung, `/ws/`, `/nginx_status` trên vhost này.

## 7. Kiểm thử

**Backend**

- Command import: parse đúng file phẳng fixture; idempotent (chạy 2 lần ra cùng
  kết quả, không nhân đôi bản ghi); SKU không khớp thì bỏ qua + cảnh báo, không
  tạo `Product`; `--dry-run` không ghi DB; import lại **không xoá ảnh** đã upload.
- `GET /products/<code>`: 4 nhánh ở §4.1; **assert khớp tuyệt đối danh sách key**
  của response (chốt chặn rò dữ liệu); serial hợp lệ nhưng dùng làm `code` →
  404; mã đã kích hoạt → `warranty` đúng shape của `/activate`.
- Đếm quét: 2 request cùng IP cùng ngày → `scan_count` = 1; khác IP → 2; khác
  ngày → 2; Redis lỗi → vẫn trả 200, counter không tăng.
- Ảnh: trả đúng content-type + header cache; id không tồn tại → 404.
- API quản trị: quyền `products:edit` (403 khi thiếu), chặn ảnh thứ 9, chặn file
  > 5MB, chặn file không phải ảnh (magic bytes, kể cả khi client khai
  `Content-Type: image/png`), xoá ảnh thì object MinIO cũng bị xoá.
- Export: `test_batch_export_definition.py` **không phải sửa** — nó đọc
  `settings.WARRANTY_QR_BASE_URL` động chứ không hardcode `qcheck.vn`, nên đổi
  default là nó tự đúng. Thay vào đó thêm một test canh riêng khẳng định default
  bằng `https://delax.com.vn/sp/` và không chứa chuỗi `qcheck`: giá trị này in
  lên tem giấy, sai là hỏng cả lô vật lý không sửa được, nên đáng có một chốt
  chặn nói rõ ý định thay vì để nó trôi theo một biến môi trường.

**Frontend**

- `ProductScanPage`: render 4 nhánh dữ liệu; khối không nằm trong
  `visible_sections` thì không xuất hiện; `noindex`/`no-referrer` được gắn vào
  `document.head` và gỡ khi unmount.
- `InlineActivateBlock`: validate SĐT rỗng/sai định dạng; kích hoạt thành công
  đổi sang `WarrantyCard` tại chỗ; mã đã kích hoạt thì render thẳng `WarrantyCard`.
- Màn quản trị: thêm/xoá/sắp xếp dòng trong từng khối, bật/tắt khối, upload ảnh
  (mock API).

## 8. Sprint & tiến độ

| Sprint | Nội dung | Trạng thái |
| --- | --- | --- |
| S1 | Model `product_profiles` + `product_profile_images` + cột `scan_count`/`first_scanned_at` + migration; script chuyển đổi Excel; sinh file phẳng; command import + test | Đã xong |
| S2 | API public `/products/<code>` + `/product-images/<id>` + đếm quét dedupe Redis + `ScanResult.PAGE_VIEWED` + settings/env/compose/nginx + test | Đã xong |
| S3 | Trang public `/sp/<mã>`: đủ 10 khối + khối kích hoạt gộp + meta bảo mật + test FE | Đã xong |
| S4 | CRM: màn nhập nội dung + upload ảnh + API quản trị + test; đổi `WARRANTY_QR_BASE_URL`; rà soát bảo mật/hiệu năng; MR | Đã xong |

Sau mỗi sprint cập nhật cột trạng thái + ghi chú đã/chưa làm tại đây.

### Ghi chú triển khai thực tế S4 (Task 13 — màn CRM)

- API quản trị nội dung mount ở `/api/orders/products/<id>/profile...` (không
  phải `/api/products/<id>/profile` như phác thảo ban đầu ở §7) — khớp cách
  `apps/orders` mount mọi route sản phẩm khác.
- Type response/payload của profile được khai THỦ CÔNG ở
  `frontend/src/lib/api/product-profiles.ts`, không derive từ OpenAPI —
  `schema.yaml` đã lệch sẵn so với code trước khi Task 13 bắt đầu, regen ở đây
  sẽ kéo theo nhiễu không liên quan.
- Nút "Xem trước" trong màn CRM (`/catalog/products/:id/content`) hiện **không**
  mở được trang public trực tiếp: trang public định danh bằng `WarrantyCode.code`
  (nhiều mã ứng với 1 sản phẩm qua lô in), không phải mã cấp-sản-phẩm, và màn
  CRM chỉ có `productId` — không có dữ liệu để tự dựng URL `/sp/<mã>` mà không
  bịa thêm 1 endpoint tra "mã đại diện". Nút bị disable kèm ghi chú hướng dẫn
  lấy mã ở tab Mã bảo hành; cảnh báo "xem trước cộng 1 lượt quét" vẫn hiển thị
  đúng yêu cầu §7. Nếu cần preview thật, việc cần làm thêm là 1 API mới do chủ
  dự án chốt ngữ nghĩa (mã đầu tiên còn hiệu lực? mã bất kỳ?) — nằm ngoài phạm
  vi Task 13.

## 9. Việc cần phía vận hành

1. **Duyệt/biên tập file phẳng `backend/data/product-content.xlsx`** trước khi
   import — trọng tâm là giọng văn FAQ và hai SKU thiếu ở §2.1.
2. **Upload ảnh sản phẩm** qua màn quản trị CRM: file Excel không có ảnh nào,
   nên trang `/sp/` sẽ trống phần ảnh cho tới khi làm việc này.
3. Điền các biến `PORTAL_COMPANY_*` vào `.env` prod (tên, địa chỉ, MST, hotline,
   link Zalo/fanpage).
4. Đặt `WARRANTY_QR_BASE_URL=https://delax.com.vn/sp/` vào `.env` prod.
5. **Xuất lại Excel lô mã sau khi đổi biến, kiểm tra cột URL, rồi mới gửi nhà
   in.** Tem in sai đích là hỏng cả lô vật lý.
6. Tạo bucket MinIO `product-media` (command tự tạo nếu thiếu, nhưng nên tạo sẵn
   để đặt policy).
