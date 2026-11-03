# Spec — Kho dữ liệu chuẩn hoá cho AI agent (schema `ai`)

> Dựng một data warehouse nhỏ trong Postgres, chuẩn hoá toàn bộ mã trạng thái thành
> nhãn tiếng Việt, để agent Hermes đọc qua MCP `prod-db` mà không cần đoán nghĩa
> của `status = 3`.
>
> - Ngày tạo: 2026-08-28
> - Nhánh: `feat/ai-mart-hermes`
> - Nền: `apps/analytics/` (schema `dwh` — tiền lệ matview + refresh service),
>   `apps/orders/crm_catalog.py` (từ điển mã hiện có), `apps/callcenter/` (API
>   transcript cho Hermes, MR !1377).
> - Bài học phải giữ: sự cố `statement timeout` 2026-07-22 — job nặng phải bọc
>   `statement_timeout` và **để lỗi thoát ra**, không nuốt rồi báo `succeeded`.

---

## 0. Vấn đề

Hermes đọc thẳng DB prod qua MCP. Dữ liệu ở đó là dữ liệu vận hành, mã hoá cho máy:

| Agent thấy | Nghĩa thật | Nguồn nhãn |
|---|---|---|
| `orders.status = 3` | Giao thành công | `frontend/src/i18n/messages/status.ts` — **frontend** |
| `sales_status_code = 'no_answer_5'` | Không nghe máy lần 5 | `apps/orders/crm_catalog.py` — **backend** |
| `movement_type = 'reclass_out'` | Chuyển trạng thái (ra) | `apps/inventory/models.py` |
| `age_text = ''` (481.523 dòng) | Chưa có dữ liệu | không ở đâu cả |

Ba vấn đề chồng nhau:

1. **Từ điển không nằm trong DB.** Nhãn tiếng Việt của `status` sống ở frontend. Agent
   đọc DB thì vĩnh viễn không thấy.
2. **Không có nguồn sự thật duy nhất.** Nhãn rải ở `crm_catalog.py`, enum model, và i18n
   frontend — ba chỗ có thể lệch nhau.
3. **Rỗng và NULL lẫn lộn.** `order_assessments.age_text` có 481.523 dòng là chuỗi rỗng
   chứ không phải NULL, nên `count()` báo độ phủ 100% trong khi thật là 0,77%. Agent tự
   viết SQL sẽ dính bẫy này và trả số sai một cách rất tự tin.

## 1. Quyết định kiến trúc

| # | Quyết định | Lý do |
|---|---|---|
| **Q1** | **Kho dữ liệu là Postgres.** Không ClickHouse/DuckDB/lakehouse ở giai đoạn này. | DB `desi` 7,4 GB, bảng fact lớn nhất 547k dòng. Kịch bản nhân sự 50→150 kéo đơn tăng gấp 3 vẫn chỉ ra ~1,5 triệu đơn và 25–40 GB sau 3 năm. Postgres đơn node phục vụ tốt bảng fact cỡ 50–100 triệu dòng — còn **hai bậc độ lớn** headroom. Ngưỡng đổi ý ghi ở §9. |
| **Q2** | **Không dựng data lake.** | Lake giải bài toán schema-on-read cho dữ liệu không mô hình hoá trước được, ở khối lượng không kham nổi trong DB. Tài sản duy nhất có hình dạng đó là ghi âm + transcript tổng đài: **8.229 cuộc / 16 MB**. `audit_logs` (4 GB) đã cố ý loại khỏi phạm vi phân tích từ spec `dwh`. Chi phí thật của lake là 3–4 service mới (object storage + catalog + query engine + orchestrator) trên VPS **dùng chung host với `desi-ads-crm` và `prod-mysql-1`**, cho một công ty 50 người chưa có data engineer. |
| **Q3** | **Không tách instance/máy riêng.** | Postgres hiện chỉ có `shared_buffers` 1 GB, cache hit 96% (OLTP chuẩn phải >99%) — tức đang thiếu RAM. Dựng instance thứ hai trên **cùng máy** không phải cách ly mà là lấy RAM của OLTP đưa cho phân tích: prod tệ đi, không tốt lên. Cách ly chỉ có giá trị trên máy khác, và đó là khoản chi VPS chưa đáng cho 7,4 GB. Rủi ro "agent viết query xấu" trị bằng thứ rẻ hơn: role riêng + `statement_timeout` + `CONNECTION LIMIT` (§6). |
| **Q4** | **Bảng vật lý + ETL tăng dần**, không dùng materialized view. | Nhịp yêu cầu là 30 phút. Full refresh một bảng rộng 547k dòng, 48 lần/ngày, trên box thiếu RAM là đốt I/O vô nghĩa — thực tế mỗi 30 phút chỉ vài chục đơn đổi. Bảng vật lý còn cho phép `ALTER TABLE` thay vì `DROP CASCADE`, và giữ được snapshot theo ngày (tồn kho, chi phí ads) mà matview không làm được. Đây là **đảo ngược Q3 của spec `dwh`** — hợp lệ vì người tiêu thụ đã đổi từ UI cố định sang agent, và nhịp đã đổi từ 1 lần/ngày sang 48 lần/ngày. |
| **Q5** | **Tầng RAW không sao chép dữ liệu.** Bảng OLTP hiện có *chính là* tầng raw. | Sao chép 7,4 GB sang schema khác chỉ để cho đủ tên "3 tầng" là đốt disk, tăng gấp đôi độ trễ, và mất khả năng JOIN trực tiếp khi debug lệch số. |
| **Q6** | **Watermark bằng cột `dw_updated_at` do trigger duy trì**, không dùng `updated_at` sẵn có. | `updated_at` là `auto_now` — chỉ nhảy khi lưu qua ORM. Đo trên code: webhook KiotViet đổi trạng thái đơn bằng `.update()` (`webhook_impl.py:380`, `:752`), sync Foxia dùng `.bulk_update()` (`order_sync_service.py:253`), verifier huỷ đơn `.update()` (`cancellation_verifier.py:178`) — **tất cả bỏ qua `auto_now`**. Trong toàn bộ call site chỉ `lead_assignment_service.py:233` tự set tay. Dùng `updated_at` làm watermark = đơn đổi trạng thái qua KiotViet không bao giờ được ETL nhặt, agent trả số sai cả ngày, không gì báo động. |
| **Q7** | **Giữ CẢ cột mã gốc (`*_ma`) lẫn nhãn (`*_text`).** | Cột mã dùng để đối chiếu ngược về bảng gốc khi Hermes trả số lệch với UI, và để test equivalence chạy được. Thêm ~15 cột, chi phí không đáng kể. |
| **Q8** | **Bảng append-only dùng watermark theo `id`, không cần trigger.** | `stock_movements` là sổ cái bất biến (append-only, không sửa không xoá). `id > moc_id` là watermark đúng và rẻ hơn trigger. Chỉ 4 bảng có sửa mới cần trigger. |

## 2. Kiến trúc

| Tầng | Nơi ở | Nội dung | Ai truy cập |
|---|---|---|---|
| RAW | bảng nghiệp vụ hiện có | `orders`, `customers`, `order_items`, `stock_movements`… | nội bộ |
| STAGING | schema `ai_stg` | chỉ cho nguồn nhiều bước hoặc cần snapshot ngày: tồn kho theo ngày, chi phí ads sau khi áp hệ số VAT theo kỳ | nội bộ |
| MART | schema `ai` | bảng contract, tên tiếng Việt không dấu, mã đã dịch thành text | **`hermes_ro`** |
| META | schema `ai_meta` | `moc_dong_bo`, `nhat_ky_chay` | nội bộ |

App Django mới `apps/ai_mart`, clean architecture:

```
apps/ai_mart/
  domain/            # luật chuẩn hoá (che SĐT, rỗng→NULL, quy ước tên), định nghĩa từ điển
  application/       # etl_service: điều phối chu kỳ, thứ tự phụ thuộc, ghi nhật ký
  infrastructure/
    sql/             # transform theo từng bảng mart, mỗi bảng 1 file .sql
    loaders.py       # upsert / full rebuild, nhận target qua alias connection
    tasks.py         # Celery task
  management/commands/   # ai_mart_sync, ai_mart_rebuild, ai_mart_verify
  migrations/
  tests/
```

`loaders.py` nhận target qua alias connection Django (mặc định `default`). Đây là mảnh
giữ cho Q1/Q3 **đảo ngược được**: khi chạm ngưỡng ở §9, đổi target là đổi env, không phải
viết lại transform.

## 3. Bảng contract

### 3.1 Nhóm nóng — upsert mỗi 30 phút

**`ai.don_hang`** — grain: 1 dòng / 1 đơn nội bộ (loại đơn mirror `foxia`/`cnv` vì là bản sao).

| Nhóm cột | Cột |
|---|---|
| Định danh | `don_id`, `ma_don` |
| Thời gian | `ngay_tao`, `ngay_tao_ngay` (date, `Asia/Ho_Chi_Minh`), `ngay_chot`, `ngay_chot_ngay` — ⚠ `sales_closed_at` là cột mới (MR !1372) **còn nợ backfill**: đơn cũ đang NULL, `COMMENT` phải ghi rõ để agent không kết luận "chưa chốt" |
| Trạng thái | `trang_thai_don_ma`/`_text`, `trang_thai_hoa_don_ma`/`_text`, `la_don_gtc` |
| Phân loại đơn | `loai_don_ma`/`_text`, `loai_don_koc_ma`/`_text`, `nguon_don_ma`/`_text`, `hinh_thuc_giao_ma`/`_text`, `phuong_thuc_tt` (MỘT cột passthrough — `orders.payment_method` là text tự do, không phải danh mục đóng; Ruling 18) |
| Kênh & người | `kenh_id`, `kenh_ten`, `loai_kenh_text`, `nhan_vien_id`, `nhan_vien_ten` |
| Khách | `khach_id`, `ma_khach`, `ten_khach`, `sdt_che`, `tinh_thanh`, `quan_huyen`, `la_khach_moi` |
| Tiền (VND) | `tien_hang`, `giam_gia`, `phi_ship`, `tong_sau_giam`, `so_luong` |
| Sản phẩm | `so_dong_sp`, `danh_sach_sku` (`text[]`), `danh_sach_sku_text`, `sku_chinh` |
| Phiếu tư vấn | `trang_thai_sale_ma`/`_text`, `ly_do_khong_chot_ma`/`_text`, `trang_thai_giao_nghiep_vu_ma`/`_text`, `nhom_ly_do_bom_ma`/`_text`, `phan_loai_khach_ma`/`_text`, `nhu_cau_ma` (`text[]`), `nhu_cau_text` |
| Chân dung khách trên phiếu | `tuoi_text`, `tuoi_nguon_text` (Khai báo / AI bóc), `nghe_nghiep_ma`/`_text`, `muc_do_dau_ma`/`_text` — cùng luật `*_src` với `dwh`: AI không đè số sale nhập |
| Cuộc gọi | `so_cuoc_goi`, `tong_giay_goi`, `co_transcript`, `da_boc_ai`, `diem_chat_luong` |
| Vận hành ETL | `dong_bo_luc` |

**`ai.dong_don_hang`** — grain: 1 dòng sản phẩm trong đơn.
`don_id`, `ma_don`, `ngay_tao_ngay`, `sku`, `ten_sp`, `nhom_sp_text`, `so_luong`,
`don_gia`, `thanh_tien`.

**`ai.khach_hang`** — grain: 1 khách.
`khach_id`, `ma_khach`, `ten_khach`, `sdt_che`, `gioi_tinh_text`, `tinh_thanh`,
`quan_huyen`, `phan_khuc_ma`/`_text`, `la_khach_moi`, `so_don_gtc`, `so_don_tat_ca`,
`so_don_huy`, `tong_chi_tieu`, `gia_tri_don_tb`, `lan_mua_dau`, `lan_mua_cuoi`,
`so_ngay_tu_lan_mua_cuoi`, `khoang_cach_mua_lai_trung_vi`, `kenh_da_mua` (`text[]`),
`tinh_da_mua` (`text[]`), `sku_hay_mua` (`text[]`), `so_cuoc_goi`, `diem_chat_luong_tb`.

**`ai.cuoc_goi`** — grain: 1 cuộc gọi.
`cuoc_goi_id`, `don_id`, `ma_don`, `khach_id`, `bat_dau`, `bat_dau_ngay`,
`thoi_luong_giay`, `loai_cuoc_goi_ma`/`_text` (từ `call_type`), `agent_ten`,
`trang_thai_boc_bang_text`, `transcript`, `tom_tat_ai`, `nhu_cau_ai` (`text[]`),
`diem_chat_luong`, `sdt_che`.

> Cảnh báo đã biết: (a) `agent_name` trên prod phần lớn **rỗng** và chỉ có 4 `agent_id`
> phân biệt, nên `agent_ten` sẽ thưa — để `NULL` chứ không bịa "Không xác định", kẻo agent
> tưởng đó là một nhân viên có thật. (b) `CallRecord` **không lưu tên hàng đợi** — cái tên
> `HAPPYCALL` từng gây hiểu nhầm nằm ở hệ Zenify, không có trong DB; spec cố ý không có
> cột hàng đợi, và `mo_ta` trong từ điển phải ghi rõ mọi cuộc ở đây đều là cuộc bán hàng.

### 3.2 Nhóm đêm — full rebuild lúc 04:50

**`ai.san_pham`** — `sku`, `ten_sp`, `ten_hien_thi`, `nhom_sp_text`, `gia_ban`,
`dang_kinh_doanh`, `la_combo`, `thanh_phan_combo` (`text[]`). `la_combo` suy từ việc có
dòng trong bảng thành phần combo — catalog **cố ý không có** cờ `is_combo` (một nguồn sự
thật duy nhất), mart vật chất hoá nó thành cột để agent khỏi phải biết luật đó.

**`ai.ton_kho`** — grain: (kho × SKU × trạng thái tồn). Nguồn `stock_items`.
`kho_ten`, `sku`, `ten_sp`, `trang_thai_ton_text` (Bán được / Bảo hành / Lỗi),
`so_luong`, `cap_nhat_luc`.

**`ai.lich_su_kho`** — grain: 1 bút toán trong sổ cái. Nguồn `stock_movements` (append-only).
`ngay`, `kho_ten`, `sku`, `ten_sp`, `loai_bien_dong_text`, `so_luong_thay_doi`,
`ton_sau`, `trang_thai_ton_text`, `loai_chung_tu_text`, `chung_tu_id` (id NỘI BỘ `reference_id` — không đối chiếu được với mã phiếu trên màn Lịch sử nhập xuất; join lấy mã phiếu thật qua 3 bảng nguồn là follow-up, Ruling 19), `ma_don`, `ghi_chu`.

**`ai.chi_phi_quang_cao`** — grain: (ngày × nền tảng × TKQC × chiến dịch).
`ngay`, `nen_tang_text` (Facebook / Google), `tkqc_ten`, `chien_dich_ten`, `sku_gan`,
`chi_phi_goc`, `he_so_vat`, `chi_phi_da_ap_he_so`, `hien_thi`, `luot_nhap`.

> Cảnh báo đã biết: hệ số VAT Facebook chọn **theo kỳ**, không theo ngày chi tiêu, và
> TKQC bị nhân dòng theo `FacebookApp`. Transform phải trải bản sao TKQC trước khi
> tổng hợp, nếu không chi phí bị khống lên nhiều lần.

### 3.3 Siêu dữ liệu — để agent tự định hướng

**`ai.tu_dien_ma`** — `(nhom_ma, ma, nhan, mo_ta, thu_tu)`. Seed bằng migration từ
`crm_catalog.py` + enum model. Riêng nhãn tiếng Việt của `Order.status` hiện **chỉ sống
trong `frontend/src/i18n/messages/status.ts`** — migration Python không import được TS,
nên seed là bản chép vào `apps/ai_mart/domain/`, và từ thời điểm đó **DB là nguồn sự
thật**, file TS là bản trình bày. Agent hỏi được "đơn có những trạng thái nào" thay vì đoán.

**`ai.huong_dan_bang`** — view sinh thẳng từ `pg_description`:
`(ten_bang, mo_ta_bang, ten_cot, kieu_du_lieu, mo_ta_cot)`. MCP có thể không hiển thị
`COMMENT`; view này biến tài liệu thành thứ `SELECT` được.

**`ai.tinh_trang_du_lieu`** — `(ten_bang, chay_lan_cuoi, du_lieu_toi_moc, so_dong,
do_tre_phut, trang_thai_text)`. Đây là thuốc giải cho chế độ hỏng nguy hiểm nhất của
ETL — **trễ âm thầm**. Agent đọc được độ trễ nên nói "số liệu tính đến 14:30" thay vì
tưởng đó là số hiện tại.

## 4. Tám luật chuẩn hoá

1. **Mỗi mã có cột `*_text`** nhãn tiếng Việt, JOIN từ `ai.tu_dien_ma`; giữ kèm `*_ma` (Q7).
2. **Chuỗi rỗng → `NULL`** (`NULLIF(btrim(x), '')`). Bài học 481.523 dòng `age_text` rỗng.
3. **Tiền: `numeric`, đơn vị VND**, ghi rõ trong `COMMENT`. Không format thành chuỗi —
   agent phải `SUM`/`AVG` được.
4. **Thời gian: giữ `timestamptz` VÀ kèm cột `ngay_*` kiểu `date`** đã quy về
   `Asia/Ho_Chi_Minh`. Bom múi giờ đã cắn hệ này hai lần; offset trong chuỗi ISO không cứu
   được phép so sánh theo ngày địa phương.
5. **Mảng giữ `text[]`** để lọc bằng `= ANY(...)`, **cộng thêm** cột chuỗi đọc được
   (`"Đau lưng, Mất ngủ"`).
6. **PII**: `sdt_che` dạng `0912***678`; **không** email, **không** địa chỉ chi tiết; giữ
   `tinh_thanh`/`quan_huyen`. Tên khách giữ nguyên — đã quyết, vì SĐT đã che và địa chỉ đã
   bỏ nên tên một mình không đủ để liên hệ.
   **Ngoại lệ có chủ đích (Ruling 17, final review 28/08):** `ai.cuoc_goi.transcript` và
   `tom_tat_ai` là văn bản THÔ chưa che — khách thường đọc SĐT/địa chỉ thành tiếng trong
   cuộc gọi. Agent cần nguyên văn để trả lời về nội dung tư vấn, cùng chuẩn với API
   transcript MR !1377. COMMENT của hai cột phải nói thẳng điều này và cấm agent trích
   SĐT/địa chỉ từ đó ra câu trả lời; `test_masking` cố ý bỏ qua hai cột này.
7. **Mọi bảng và mọi cột bắt buộc có `COMMENT` tiếng Việt.** Có test chặn.
8. **Loại đơn mirror** `order_source IN ('foxia','cnv')`; giữ cờ `la_don_gtc` thay vì lọc
   sẵn, để agent xem được cả đơn huỷ khi cần.

## 5. Cơ chế ETL

### 5.1 Watermark

Thêm cột `dw_updated_at timestamptz` + trigger `BEFORE INSERT OR UPDATE` trên **5 bảng**:
`orders`, `order_items`, `customers`, `order_assessments`, `callcenter_call_records`.
Trigger bắt mọi đường ghi kể cả `.update()`, `.bulk_update()` và SQL chạy tay.
`order_items` phải có trigger riêng vì sync KiotViet có thể sửa dòng sản phẩm mà không
chạm `orders` — thiếu nó thì `ai.dong_don_hang` trễ tới tận rebuild đêm.

`stock_movements` append-only → watermark theo `id` (Q8). Ads → thuộc nhóm rebuild đêm,
không cần watermark.

### 5.2 Chu kỳ

| Nhịp | Việc |
|---|---|
| Mỗi 30 phút | `WHERE dw_updated_at > moc − 5 phút` → `INSERT … ON CONFLICT DO UPDATE` cho nhóm nóng. Biên 5 phút chống mất dòng do transaction commit lệch thời điểm đọc watermark. |
| 04:50 hằng ngày | **Full rebuild toàn bộ** (nhóm nóng + nhóm đêm) + snapshot tồn kho theo ngày. Đây là cơ chế **tự lành**: bắt các ca sửa lùi ngày, xoá cứng, và mọi thứ watermark bỏ sót. Đặt sau `analytics-refresh-dwh-marts` (04:25) để không giẫm chân. |

Dùng lại `single_flight` + `statement_timeout` của `apps/common/db_guards`. **Lỗi phải
thoát ra ngoài** — nuốt lỗi khiến Celery báo `succeeded` trong khi bước con đã chết,
celery-exporter không đếm được, alert không nổ (sự cố 2026-07-22).

Mỗi lượt ghi một dòng `ai_meta.nhat_ky_chay` (`run_id`, bảng, mốc, số dòng upsert,
trạng thái, lỗi). Ghi log hỏng **không được** đánh hỏng cả lượt chạy.

## 6. Bảo mật

Role `hermes_ro`:

```sql
GRANT USAGE ON SCHEMA ai TO hermes_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO hermes_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO hermes_ro;
ALTER ROLE hermes_ro SET statement_timeout = '30s';
ALTER ROLE hermes_ro SET default_transaction_read_only = on;
ALTER ROLE hermes_ro CONNECTION LIMIT 3;
```

Không cấp quyền trên `ai_stg`, `ai_meta`, `dwh`, `public`. `CONNECTION LIMIT 3` là hàng
rào thật: hệ này đã hai lần dính sự cố `too many clients`, và `max_connections` từng bị
hạ xuống 50.

`ai.tinh_trang_du_lieu` đọc `ai_meta` nhưng là view thường (không `security_invoker`) nên
chạy bằng quyền owner — `hermes_ro` đọc được nội dung mà vẫn không chạm được schema gốc.

Mật khẩu qua env mới `HERMES_DB_PASSWORD`, cập nhật cả 4 file compose. MCP `prod-db` đổi
sang connection string của role này.

## 7. Bộ test

Một bộ theo cấu trúc hệ thống, không test lẻ tẻ theo từng cột:

| File | Chặn điều gì |
|---|---|
| `test_contract.py` | Mọi bảng/cột trong `ai` có `COMMENT`; tên cột đúng quy ước tiếng Việt không dấu |
| `test_dictionary.py` | **Mọi mã xuất hiện trong dữ liệu đều có nhãn trong `ai.tu_dien_ma`.** Để lọt là Hermes thấy mã thô — đúng vấn đề spec này sinh ra để giải |
| `test_etl_equivalence.py` | Kết quả upsert tăng dần **bằng** full rebuild trên cùng bộ dữ liệu |
| `test_triggers.py` | `.update()` và `.bulk_update()` hàng loạt vẫn bump `dw_updated_at` — chính cái bẫy ở Q6 |
| `test_masking.py` | Không cột nào lộ SĐT đầy đủ, email, hay địa chỉ chi tiết |
| `test_freshness.py` | `ai.tinh_trang_du_lieu` phản ánh đúng lần chạy và độ trễ |

## 8. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| **S1** | App `ai_mart`, 3 schema, cột + trigger `dw_updated_at` (kèm backfill theo lô), `ai.tu_dien_ma`, `ai.don_hang`, `ai.dong_don_hang`, role `hermes_ro`, `test_contract` + `test_dictionary` + `test_triggers` | ✅ xong 28/08 — branch `feat/ai-mart-s1`, 34 test xanh. Đã làm: 9 task đủ như cột Nội dung, thêm `domain/coverage.py` (PAIRS dùng chung) + 4 lệnh manage.py. Khác spec (có chủ đích): **bỏ backfill theo lô** — dòng cũ giữ NULL, initial load + rebuild đêm là full SELECT nên không cần (né khoá bảng 547k dòng); nhãn KOC/transcribe lấy từ enum thay vì chép tay. Chưa làm: chạy `ai_mart_grant_role` trên prod (S4). |
| **S2** | `ai.khach_hang`, `ai.cuoc_goi`, `ai.san_pham`, `ai.huong_dan_bang`, `ai.tinh_trang_du_lieu`, `test_masking` + `test_freshness` | ✅ xong 28/08 — branch `feat/ai-mart-s2` (stack trên s1), 42 test xanh. Đã làm đủ 5 task. Ghi nhận: test masking quét 40 cột text/8 bảng, 0 rò SĐT. Nợ nhỏ chuyển final review: typo docstring migration 0006/0008; CTE aggregate quét toàn bảng mỗi nhịp sync (đo trên prod ở S4, spec §9 ngưỡng 10'). |
| **S3** | `ai.ton_kho`, `ai.lich_su_kho`, `ai.chi_phi_quang_cao` (xử lý hệ số VAT theo kỳ + nhân dòng TKQC), `test_etl_equivalence` | ✅ xong 28/08 — branch `feat/ai-mart-s3` (stack trên s2), 50 test xanh. Quyết định thêm khi làm: (1) `lich_su_kho.ngay` lấy từ `occurred_at` (ngày nghiệp vụ, bài học MR !1234) + thêm cột `trang_thai_ton_text`, `ghi_chu`; (2) sổ cái nạp tăng dần có `ID_OVERLAP=500` + `ON CONFLICT DO NOTHING` để bịt race id-từ-sequence (transaction commit muộn mang id thấp); (3) hệ số VAT ads áp theo NGÀY, ghi trong COMMENT bảng là lệch màn hình có chủ đích; xác minh campaign/ad FB là UNIQUE toàn cục nên join không nhân dòng. |
| **S4** | Deploy prod, đo p95 write path trước/sau trigger, đổi connection MCP, runbook + alert Prometheus độ trễ ETL > 90 phút | ✅ phần chuẩn bị xong 28/08 — branch `feat/ai-mart-s4` (stack trên s3): env `HERMES_DB_PASSWORD` ở 3 compose (base compose không có backend), alert `AiMartSyncSilent` (rule fail đã có sẵn `CeleryTaskFailing`), runbook `docs/runbooks/2026-08-28-ai-mart-deploy.md`. ⏳ Chờ user chạy trên prod theo runbook: migrate → roll code → rebuild+verify (hỏi nghĩa mã lạ) → grant role (KHÔNG DEBUG=True) → đổi MCP → đo p95 + thời lượng sync. Nợ sau deploy: re-run grant sau mỗi migration thêm bảng `ai.*`; nếu sync > 2-3' thì scope CTE theo ids. |

S1 là sprint chứng minh kiến trúc chạy được: sau nó phải trả lời được bằng SQL thuần
"tháng này bao nhiêu đơn giao thành công, chia theo kênh" mà không cần bảng tra cứu ngoài.

## 9. Rủi ro và ngưỡng đổi ý

**Rủi ro đã nhận diện**

| Rủi ro | Xử lý |
|---|---|
| Trigger đặt lên `orders` — bảng nóng nhất, động vào write path | Đo p95 ghi trước/sau khi deploy. Trigger PL/pgSQL một dòng tốn cỡ micro-giây, nhưng phải có số chứ không tin lý thuyết |
| Backfill `dw_updated_at` cho 547k dòng khoá bảng | Chạy theo lô, giờ thấp điểm, cột `NULL` mặc định (không `DEFAULT now()` — sẽ rewrite cả bảng) |
| Sai thứ tự deploy | **Migrate trước (cột + trigger), roll code sau.** Code cũ chạy với cột mới thì vô hại; code mới chạy khi chưa có cột thì chết |
| ETL trễ âm thầm | `ai.tinh_trang_du_lieu` + alert Prometheus khi độ trễ > 90 phút |
| Thêm mã trạng thái mới mà quên seed nhãn | `test_dictionary.py` chặn — nhưng master **hiện không có `.gitlab-ci.yml`**, nên phải chạy tay cho tới khi CI được khôi phục |

**Ngưỡng đổi ý (đo được, không theo cảm tính)**

| Bậc | Làm gì | Kích hoạt khi |
|---|---|---|
| 1 | Xuất Parquet ra MinIO **đã có sẵn trên prod**, query bằng DuckDB (là *thư viện*, không phải service mới) | Cửa sổ ETL > 10 phút, **hoặc** p95 query agent > 10s sau khi đã đánh index, **hoặc** bảng fact > 20 triệu dòng |
| 2 | ClickHouse / lakehouse thật, có orchestrator | > 100 triệu dòng, **hoặc** ≥ 3 hệ tiêu thụ nặng, **hoặc** cần BI dưới giây |

Bậc 1 đáng chú ý: MinIO đã chạy trên prod (ảnh chấm công, media sản phẩm, file export).
Ergonomics kiểu lake — Parquet trên object storage, schema-on-read — lấy được sau này
**mà không thêm service nào**. Đó là lý do `loaders.py` tách bước load ngay từ S1.
