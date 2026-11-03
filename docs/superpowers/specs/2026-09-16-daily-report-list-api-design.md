# API Daily Report trả danh sách dòng thay cho số gộp

Ngày: 2026-09-16. Người quyết định: chủ dự án. Thay thế hình dạng response của
[spec 2026-09-03](2026-09-03-daily-report-read-api-design.md); phần token, kỳ,
freshness, health, definitions giữ nguyên.

## 1. Vấn đề

Hermes (agent phân tích ngoài) được cấp `DAILY_REPORT_API_TOKEN` để đọc
`/api/daily-report/*`. Mọi endpoint hiện trả **số đã cộng** (leads, delivered,
returned, total_revenue…) theo `group_by=day|source|staff|channel_group`. Hermes
không lấy được từng đơn: mã đơn, trạng thái, giá trị, lý do hoàn, Sale, Digital,
sản phẩm. Brief `ai-schema-hermes-brief.md` hướng nó đọc thẳng `ai.don_hang`, nhưng
ngày 15/09 chủ dự án đã quyết Hermes đi HTTP, không trỏ DB — đường SQL đóng mà
đường HTTP chưa mở dòng đơn.

Quyết định 16/09: **giữ nguyên URL, đổi thân trả về thành danh sách dòng**. Hermes
tự cộng, tự nhóm. CRM không nuôi thêm lớp số gộp nào cho Hermes nữa.

## 2. Phạm vi

Đổi: `leads/stats`, `orders/stats`, `revenue`, `pipeline`, `ads`, `summary`.
Giữ: `health`, `definitions` (mở rộng thêm hai khoá). Không migration, không env
mới, không đụng frontend. Chỉ Hermes gọi API này (đã rà: không nơi nào khác trong
repo dùng `DAILY_REPORT_API_TOKEN`), nên đây là thay đổi **breaking có chủ ý**,
deploy kèm cập nhật system prompt của Hermes.

## 3. Hợp đồng chung

- Header, token, throttle, lỗi 401/503 như cũ (`transport/auth.py` không đổi).
- `from`/`to`: `YYYY-MM-DD` giờ VN, bao gồm hai đầu, mặc định hôm qua, tối đa 92
  ngày (`periods.parse_range` không đổi). `pipeline` giữ `as_of` + `lookback_days`.
- **Bỏ `group_by`.** Gửi lên → `400 {"detail": "Tham số `group_by` không còn; API trả từng dòng, tự nhóm phía gọi."}`.
  Không lặng lẽ bỏ qua: Hermes phải biết hình dạng đã đổi.
- Phân trang **keyset**, không offset (mart ETL 30' chèn dòng giữa lúc kéo làm
  offset sót/lặp): `cursor` = giá trị `cursor` của dòng cuối trang trước;
  `limit` mặc định 200, trần cứng theo endpoint (bảng mục 4); xin lớn hơn bị kẹp,
  không báo lỗi; `limit` không phải số nguyên dương → 400.
- Response:

  ```json
  {
    "meta": { "timezone", "range", "data_as_of", "data_status", "definitions_url", "generated_at" },
    "data": [ { ...dòng..., "cursor": <giá trị> } ],
    "limit": 200,
    "next_cursor": <giá trị | null>
  }
  ```

  `meta` y như cũ. `data` là mảng. `next_cursor = null` khi hết. Fetch `limit+1`
  để biết còn trang; không `count(*)` toàn tập.
- Bỏ cache Redis theo tham số (`cached()`) cho các endpoint danh sách; `definitions`
  giữ cache 1h. Lý do: trang keyset ít trúng cache, mart tự làm mới 30'.
- Khoá tiếng Anh, dịch 1-1 từ cột `ai.*`. Cột `*_ma` + `*_text` gộp thành object
  `{"code", "label"}`; nơi CRM có luật rổ (trạng thái đơn, trạng thái sale) thêm
  `"bucket"` để Hermes nhóm đúng như màn hình mà không phải tra `definitions` từng dòng.
- Tiền: số nguyên VND (round). Thời điểm: ISO 8601 có offset `+07:00`. Ngày: `YYYY-MM-DD`.
- PII: `customer.name` và `customer.phone_masked` (= `sdt_che`, mart đã che) — cùng
  mức với `/api/ai/hermes/conversations`. Không mở SĐT thật.
- `statement_timeout` như cũ (`REPORT_QUERY_TIMEOUT_SECONDS`); DB lỗi → 503.

## 4. Từng endpoint

| URL | 1 dòng = | Tập dòng | Keyset | `limit` trần |
|---|---|---|---|---|
| `GET /leads/stats` | 1 lead (đơn tạo trong kỳ, mọi trạng thái) | `ai.don_hang` WHERE `ngay_tao_ngay BETWEEN from AND to` — đúng tập `leads_rows` cũ | `don_id` tăng | 1000 |
| `GET /orders/stats` | 1 đơn có hoá đơn | như trên + `trang_thai_hoa_don_ma IS NOT NULL`, không huỷ, không hoá đơn huỷ, loại chi nhánh ẩn — đúng tập `orders_rows` cũ (`total_q` Báo cáo tổng) | `don_id` | 1000 |
| `GET /revenue` | 1 dòng hàng (1 sản phẩm trong 1 đơn) | `ai.dong_don_hang` JOIN `ai.don_hang` với **cùng bộ lọc `/orders/stats`** | `dong_id` | 2000 |
| `GET /pipeline` | 1 lead còn mở tính đến `as_of` | đúng tập `open_lead_rows` cũ | `don_id` | 1000 |
| `GET /ads` | 1 (ngày × nền tảng × TKQC × chiến dịch) + 1 dòng dư mỗi (ngày × nền tảng × TKQC) | OLTP qua `ads_spend` (mục 4.5) | tuple sắp trong Python | 2000 |
| `GET /summary` | — | **410 Gone** `{"detail": "Endpoint đã bỏ; ghép từ /leads/stats, /orders/stats, /pipeline."}` | | |
| `GET /definitions` | giữ | thêm `ad_account_channels` và `cost_multiplier` (mục 4.6) | | |
| `GET /health` | giữ | | | |

### 4.1 Dòng đơn (`OrderRow`) — dùng cho `/leads/stats` và `/orders/stats`

Cùng một shape; khác nhau chỉ ở bộ lọc. Hermes đếm `len(rows)` của `/orders/stats`
sẽ bằng `total_orders` cũ; đếm theo `order_status.bucket` sẽ ra `delivered /
awaiting / returned / return_risk` cũ. Test khoá điều này (mục 6).

```json
{
  "id": 123456,
  "code": "DL12345",
  "created_at": "2026-09-15T10:00:00+07:00",
  "created_date": "2026-09-15",
  "closed_at": "2026-09-15T14:20:00+07:00",
  "closed_date": "2026-09-15",
  "order_status":   {"code": 5, "label": "Giao thành công", "bucket": "delivered"},
  "invoice_status": {"code": 1, "label": "Đã xuất"},
  "is_valid_revenue": true,
  "order_type":     {"code": "...", "label": "..."},
  "koc_type":       {"code": "...", "label": "..."},
  "source":         {"code": "messenger", "label": "Messenger"},
  "delivery_method":{"code": "...", "label": "..."},
  "payment_method": "COD",
  "channel": {"id": 12, "name": "Facebook Cường", "type": "Facebook"},
  "channel_group": "facebook",
  "branch_id": "...",
  "staff": {"id": 45, "name": "Nguyễn Văn A"},
  "customer": {"id": 5123, "code": "KH0001", "name": "Chị Lan", "phone_masked": "0912***678",
               "province": "Hà Nội", "district": "Cầu Giấy", "is_new": true},
  "amounts": {"goods": 1500000, "discount": 100000, "shipping": 30000, "total": 1430000, "quantity": 2},
  "items": {"line_count": 2, "skus": ["MSC06", "GEL01"], "skus_label": "MSC06, GEL01", "main_sku": "MSC06"},
  "sales_status": {"code": "closed", "label": "Đã chốt", "bucket": "closed"},
  "reason_not_closed": {"code": null, "label": null},
  "delivery_business_status": {"code": "...", "label": "..."},
  "bounce_reason_group": {"code": "...", "label": "..."},
  "customer_class": {"code": "...", "label": "..."},
  "needs": {"codes": ["dau_lung"], "label": "Đau lưng"},
  "age": {"label": "35-44", "source": "AI"},
  "occupation": {"code": "...", "label": "..."},
  "pain_level": {"code": "...", "label": "..."},
  "calls": {"count": 2, "seconds": 340, "has_transcript": true, "ai_extracted": true, "quality_score": 7.5},
  "synced_at": "2026-09-16T04:37:05+07:00",
  "cursor": 123456
}
```

Ánh xạ cột (đầy đủ để người viết code không phải đoán): `don_id→id`, `ma_don→code`,
`ngay_tao→created_at`, `ngay_tao_ngay→created_date`, `ngay_chot→closed_at`, `ngay_chot_ngay→closed_date`,
`trang_thai_don_*→order_status` (+`bucket` từ `_order_bucket`, riêng mã huỷ → `"cancelled"`),
`trang_thai_hoa_don_*→invoice_status`, `la_don_gtc→is_valid_revenue`, `loai_don_*→order_type`,
`loai_don_koc_*→koc_type`, `nguon_don_*→source`, `hinh_thuc_giao_*→delivery_method`,
`phuong_thuc_tt→payment_method`, `kenh_id/kenh_ten/loai_kenh_text→channel`,
`nhom_kenh_ban→channel_group`, `chi_nhanh_id→branch_id`, `nhan_vien_*→staff`,
`khach_id/ma_khach/ten_khach/sdt_che/tinh_thanh/quan_huyen/la_khach_moi→customer`,
`tien_hang/giam_gia/phi_ship/tong_sau_giam/so_luong→amounts`,
`so_dong_sp/danh_sach_sku/danh_sach_sku_text/sku_chinh→items`,
`trang_thai_sale_*→sales_status` (+`bucket` từ `d.bucket_of`), `ly_do_khong_chot_*→reason_not_closed`,
`trang_thai_giao_nghiep_vu_*→delivery_business_status`, `nhom_ly_do_bom_*→bounce_reason_group`,
`phan_loai_khach_*→customer_class`, `nhu_cau_ma/nhu_cau_text→needs`, `tuoi_text/tuoi_nguon_text→age`,
`nghe_nghiep_*→occupation`, `muc_do_dau_*→pain_level`,
`so_cuoc_goi/tong_giay_goi/co_transcript/da_boc_ai/diem_chat_luong→calls`, `dong_bo_luc→synced_at`.
Object con có mọi trường NULL vẫn trả object với giá trị null (không trả `null` cả object)
để Hermes truy cập đường dẫn cố định; ngoại lệ `staff` và `channel` trả `null` khi không
có id (đúng cách `format_key` cũ biểu diễn "chưa gán").

### 4.2 Dòng `/pipeline` (`OpenLeadRow`)

Tập `open_lead_rows` hiện có. Shape = `OrderRow` **rút gọn**: `id, code, created_at,
created_date, staff, channel, channel_group, source, sales_status, customer, amounts,
items, calls, age_days, synced_at, cursor`. `age_days = as_of - created_date` (đúng
`tuoi` cũ; Hermes nhóm theo rổ tuổi `0-1d/2-3d/4-7d/8d+` tự lấy từ `definitions.metrics`
hoặc tự chia). Query hiện có chỉ SELECT vài cột — mở rộng SELECT, không đổi WHERE.

### 4.3 Dòng `/revenue` (`OrderLineRow`)

```json
{"line_id": 987, "order_id": 123456, "order_code": "DL12345", "created_date": "2026-09-15",
 "sku": "MSC06", "product_name": "Ghế massage MSC06", "product_group": "Ghế massage",
 "quantity": 1, "unit_price": 1400000, "amount": 1400000, "cursor": 987}
```

JOIN `ai.don_hang` để áp cùng bộ lọc `/orders/stats` (có hoá đơn, không huỷ, loại chi
nhánh ẩn) — vậy `sum(amount)` theo `order_id` đối chiếu được với `amounts.total` bên
`/orders/stats`; chênh là phí ship/giảm giá cấp đơn, đã nói trong brief mục 1. Lọc theo
`don_hang.ngay_tao_ngay` (không dùng `dong_don_hang.ngay_tao_ngay` để hai endpoint chắc
chắn cùng tập đơn).

### 4.4 Rổ (bucket) là luật của CRM, không phải của Hermes

`order_status.bucket ∈ {delivered, awaiting, returned, return_risk, cancelled}` và
`sales_status.bucket` (11 rổ lead + `other`) tính ở server bằng `domain/definitions.py`
hiện có. Hermes cộng theo `bucket`, không tự tra mã. `definitions` vẫn liệt kê mã→rổ
để đối chiếu.

### 4.5 Dòng `/ads` (`AdSpendRow`)

Nguồn OLTP qua `ads_spend.account_spend` (grain TKQC, dedup `Max`) và
`ads_spend.campaign_spend` (grain chiến dịch, cộng từ ad). Mart `ai.chi_phi_quang_cao`
không dùng: thiếu id TKQC/chiến dịch/kênh và `dong_id` sinh lại mỗi rebuild nên không keyset được.

Mỗi dòng = 1 (ngày × nền tảng × TKQC × chiến dịch). **Thêm 1 dòng dư** cho mỗi
(ngày × nền tảng × TKQC) với `campaign_id = null`, `is_remainder = true`, mang
`chi tiêu TKQC − Σ chi tiêu chiến dịch` (tương tự impressions/clicks). Dòng dư có thể
**âm** (grain ad và grain TKQC sync lệch nhịp) — đúng bản chất `total.cost_outside_campaigns`
cũ, nói ra không giấu. Nhờ đó `Σ spend mọi dòng theo TKQC = by_account.cost` cũ, và
`Σ spend dòng có campaign_id = by_campaign.cost` cũ. Test khoá hai đẳng thức này.

```json
{"day": "2026-09-15", "platform": "facebook",
 "account_id": "act_123", "account_name": "Delax FB 01", "channels": ["facebook-cuong"],
 "campaign_id": "2384...", "campaign_name": "MSC06 - Hà Nội", "sku": "MSC06", "is_remainder": false,
 "spend": 1200000, "multiplier": 1.08, "spend_adjusted": 1296000,
 "impressions": 45000, "clicks": 320, "cursor": "2026-09-15|facebook|act_123|2384..."}
```

- `spend` thô, `multiplier` theo nền tảng chọn theo `from` của kỳ (giữ luật
  `_multipliers(d_from)` của màn Báo cáo Ads; khác mart theo-ngày — ghi trong doc).
  `spend_adjusted = round(spend × multiplier)`.
- `channels` = mã Digital được map cho TKQC (hợp mọi bản sao). Rỗng = TKQC chưa map
  kênh (tiền vẫn trong tổng — trước là `cost_outside_channels`).
- Sắp theo tuple `(day, platform, account_id, campaign_id or "")`; `cursor` là chuỗi
  nối bằng `|`, so sánh theo tuple (tách chuỗi, không so sánh chuỗi thô). Dòng dư
  (`campaign_id=""`) đứng đầu nhóm TKQC.
- `meta.spend_as_of` (thời điểm sync mỗi nền tảng) chuyển từ `data` lên `meta` vì không
  còn thuộc dòng nào.
- **Bỏ `by_channel`** (bảng "Báo cáo theo digital" của app). Hermes muốn hiệu quả theo
  Digital thì nối `channels` với `channel.name`/`kenh_id` bên `/orders/stats`. Khác biệt
  cần ghi vào brief: màn app chỉ hiện kênh có nhân viên Ads; TKQC map nhiều kênh thì app
  có luật chia riêng — API không chia, để nguyên mảng `channels`.

### 4.6 `/definitions` mở rộng

Thêm:
- `ad_account_channels`: `[{"platform", "account_id", "name", "channels": [...]}]` — bản
  đồ TKQC → Digital hiện hành (từ `ChannelAdAccount`, không theo kỳ), để Hermes không phải
  suy từ dòng `/ads`.
- `cost_multiplier`: phát biểu luật hệ số để Hermes hiểu `multiplier` trong dòng `/ads`:

  ```json
  {"facebook": {"schedule": [{"effective_from": "2026-09-01", "multiplier": 1.1},
                             {"effective_from": "2026-08-01", "multiplier": 1.08},
                             {"effective_from": null, "multiplier": 1.1}],
                "rule": "Chọn theo ngày BẮT ĐẦU kỳ (`from`): mốc gần nhất ≤ from; cả kỳ dùng một hệ số."},
   "google": {"schedule": [{"effective_from": null, "multiplier": 1.08}], "rule": "Cố định."}}
  ```

  Sinh từ `FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE`, `ADS_DAILY_COST_MULTIPLIER` (hệ số gốc
  khi `from` trước mọi mốc) và `GOOGLE_ADS_DAILY_COST_MULTIPLIER` trong
  `apps/orders/reporting/shared.py`, không chép số vào daily_report.
- `metrics` (glossary): bổ sung mục nói rõ "API trả dòng; các chỉ số cũ tính bằng cách
  nào" (ví dụ `close_rate = closed / (leads − rổ loại trừ)`, `net_revenue = Σ total của
  dòng bucket ≠ returned`).

## 5. Thay đổi mã

Trong `backend/apps/daily_report/`:

- `infrastructure/queries.py`: bỏ `_group_clauses`, `_bucket_select`, `leads_rows`,
  `orders_rows` (dạng gộp); thêm `order_rows(d_from, d_to, *, invoiced_only, after, limit)`
  (một hàm, hai bộ lọc), `order_line_rows(d_from, d_to, after, limit)`,
  `open_lead_rows(as_of, lookback_days, after, limit)` mở rộng SELECT. Mẫu keyset:
  `AND don_id > %(after)s ORDER BY don_id LIMIT %(limit)s` (limit = xin + 1).
  `dictionary_rows` giữ.
- `application/stats_service.py`: bỏ `leads_stats/orders_stats/revenue/_compare_block/
  daily_summary/summary_payload/format_key/_key_tuple/_sort_key/LEAD_GROUPS…`; thêm
  `application/rows.py` (map dict SQL → `OrderRow`/`OpenLeadRow`/`OrderLineRow`, thuần,
  test đơn vị được) và `application/paging.py` (`parse_paging(params, max_limit)`,
  `page(items, limit) -> (items, next_cursor)`). `pipeline` đổi sang trả dòng.
  `definitions` giữ + hai khoá mới. `envelope` thêm tham số `page` để gắn `limit`/`next_cursor`.
- `application/ads_service.py`: bỏ `_channel_block/_account_block/_campaign_block/_block/
  ads_stats`; thêm `ad_spend_rows(d_from, d_to)` (ghép account + campaign + dòng dư, sắp
  tuple) và cắt trang theo cursor tuple. Bỏ import `OrderRepository.summary_ads_daily_breakdown`.
- `transport/views.py`: bỏ `_group_param`; thêm `_paging_params`; `summary` thành view 410;
  từ chối `group_by` → 400 ở một helper dùng chung.
- `transport/serializers.py`: serializer OpenAPI cho 4 shape dòng + envelope trang;
  `openapi.py` mô tả đổi.
- `urls.py`: giữ 8 path (summary trỏ view 410).
- Xoá code chết còn lại sau khi test xanh (`Counter/defaultdict` import…).

Không đụng `apps/ai_mart`, `apps/orders`, frontend.

## 6. Kiểm thử

Chạy theo `running-backend-tests` (container test 5433/6380, `--reuse-db`).

- **Tương đương số gộp cũ** (quan trọng nhất): trên fixture `seed_order` + `rebuild()`
  hiện có, so `len(/orders/stats rows)` với `summary_overview.total_q`, đếm theo
  `order_status.bucket` với 4 rổ, `Σ amounts.total` với `total_revenue`, `Σ` theo bucket
  ≠ returned với `net_revenue`; `len(/leads/stats rows)` với `leads`, đếm theo
  `sales_status.bucket` với 11 rổ; `/pipeline` với `open_leads` cũ. Giữ nguyên tinh thần
  `test_matches_summary_overview` — đối chiếu với màn hình CRM, không với chính mình.
- **Keyset đi hết không sót/lặp**: seed N dòng, kéo `limit=3` tới `next_cursor=null`,
  hợp lại = tập đầy đủ, không trùng id; thêm dòng mới giữa hai trang không làm lệch
  trang đã kéo.
- **`limit`**: mặc định, kẹp trần, không phải số → 400. **`group_by`** → 400. `summary` → 410.
- **`/revenue`**: dòng hàng chỉ thuộc đơn có trong `/orders/stats`; `Σ amount` theo đơn
  đối chiếu với `amounts.total` (lệch = ship/giảm giá cấp đơn, test dùng đơn không ship,
  không giảm để bằng nhau).
- **`/ads`**: `Σ spend_adjusted` theo (platform, account_id) = `by_account.cost` cũ (giữ
  hàm cũ trong test làm oracle trước khi xoá, hoặc tính lại từ `ads_spend` trực tiếp);
  `Σ` dòng có `campaign_id` = `by_campaign.cost` cũ; dòng dư âm được trả nguyên, không kẹp 0;
  cursor tuple đi hết không sót/lặp.
- **`rows.py`** test đơn vị: mọi cột NULL → object con đủ khoá với null; `staff`/`channel`
  không id → `null`.
- **OpenAPI**: `test_openapi` so file `docs/openapi/daily-report.yaml` với sinh tự động —
  sinh lại file, cập nhật test nếu nó soi tên schema.
- Test cũ về `group_by`, `summary`, `by_channel` xoá hoặc đổi thành test 400/410.

## 7. Tài liệu

- `docs/daily-report-api.md`: viết lại bảng endpoint, mục phân trang, mục "đổi từ số gộp
  sang dòng: cách tính lại từng chỉ số cũ", ghi rõ quyết định 16/09 và lý do.
- `docs/openapi/daily-report.yaml`: sinh lại.
- `docs/ai-schema-hermes-brief.md` mục 0.6 (bảng endpoint) và 0.7 (mọi dòng đang chỉ
  `group_by=`, `by_account`, `by_campaign`, `by_channel` → chỉ sang dòng + cách tự nhóm).
  Thêm câu: "API trả dòng, không trả số; muốn số thì cộng theo `bucket`".
- `docs/hermes-directory.md`: rà chỗ tham chiếu `/ads by_account` nếu có.

## 8. Triển khai

- Không migration, không env. Deploy = roll code web.
- Breaking: phải cập nhật system prompt Hermes cùng lúc (dán brief mới). Chủ dự án chọn
  thời điểm; MR để riêng, không gộp với nhánh khác.
- Sau deploy: gọi thử 8 URL bằng token thật, kéo 1 trang mỗi cái, so `len(/orders/stats)`
  hôm qua với Báo cáo tổng trên UI.

## 9. Ngoài phạm vi (cố ý)

- Lọc phía server theo `staff_id`, `channel_id`, `status`: Hermes lọc phía nó; thêm khi có
  nhu cầu đo được.
- `/api/ai/hermes/customers` (bảng `ai.khach_hang`): chưa ai hỏi.
- Ad set / ad riêng lẻ, chi tiêu theo fanpage: như brief đã ghi, chưa mở.
- Giữ số gộp song song "để đối chiếu": chủ dự án đã chọn không.
