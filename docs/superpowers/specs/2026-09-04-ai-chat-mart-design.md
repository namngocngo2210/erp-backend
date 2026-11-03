# Spec — Kho hội thoại chat vào schema `ai`

> Đưa 751.350 tin nhắn Pancake mà `desi-crawl-conversations` đã crawl vào schema `ai`,
> gán nhãn **nhu cầu** và **lý do khách không mua** bằng LLM, dùng đúng bộ mã mà sale
> đang nhập tay cho đơn — để so được "khách nói gì trong chat" với "sale ghi gì trên phiếu".
>
> - Ngày tạo: 2026-09-04
> - Nhánh dự kiến: `feat/ai-chat-mart`
> - Nền: `apps/ai_mart/` (spec `2026-08-28-ai-mart-hermes-design.md` — 8 luật chuẩn hoá,
>   `ai_meta.moc_dong_bo`, role `hermes_ro`, bộ test contract/dictionary/masking/freshness),
>   `apps/callcenter/extract.py` (cơ chế `clean`/`rejected`/`filled` ràng buộc LLM vào từ điển),
>   `ai.cuoc_goi` (tiền lệ trực tiếp: một kênh hội thoại làm phẳng vào mart kèm nhãn AI).
> - Bài học phải giữ: sự cố `statement timeout` 2026-07-22 — job nặng phải bọc
>   `statement_timeout` và **để lỗi thoát ra**, không nuốt rồi báo `succeeded`.

---

## 0. Vấn đề

Công ty có 93.034 hội thoại khách hàng trải 3/2024 → 9/2026 nằm trong một Postgres riêng
mà không ai truy vấn được để trả lời hai câu hỏi kinh doanh:

1. **Khách cần gì?** — nhu cầu thật khách nói ra, chứ không phải nhu cầu sale suy đoán rồi tick.
2. **Vì sao khách không mua?** — lý do khách nói trong chat, đối chiếu với lý do sale ghi trên phiếu.

Hôm nay muốn trả lời phải mở SPA của crawler đọc tay từng hội thoại. Hermes đọc DB prod qua
MCP `prod-db` nhưng chỉ thấy schema `ai` của DB `desi` — nơi không có một dòng chat nào.

## 1. Hiện trạng đo được

Đo trực tiếp trên `deploy-postgres-1` (DB `crawler`) và `desi-postgres` (DB `desi`) ngày 04/09/2026.

| Chỉ số | Giá trị |
|---|---|
| Tin nhắn | 751.350 (out 596.128 / in 155.222) |
| Loại tin | `INBOX` 693.240 · `COMMENT` 58.102 · `RATING` 8 |
| Hội thoại | 93.034 (92.569 có ít nhất 1 tin) |
| — có khách trả lời | 73.267 |
| — có ≥2 lượt khách nói | **18.516** |
| — `last_message_at` ≥ 01/01/2026 | 70.208 |
| Khách | 66.277 |
| Page | 37 — facebook 19, personal_zalo 8, shopee_official 6, tiktok_shop 2, zalo 1, youtube 1 |
| Khoảng thời gian | 26/03/2024 → 01/09/2026 |
| Toàn bộ ký tự inbound | 4,06 triệu (~1,3 triệu token) |
| Gắn thẻ hội thoại | 34.353 dòng |
| Kích thước DB | `crawler` 993 MB · `desi` 9.147 MB |

**Bốn phát hiện định hình phạm vi spec này:**

- **F1 — Toàn bộ cột `raw` là NULL.** `messages.raw`, `conversations.raw`, `customers.raw`:
  0 dòng có dữ liệu. Crawler chỉ giữ các cột phẳng. Payload gốc Pancake (theo
  `desi-crawl-conversations/docs/pancake-api-verified.md`) **có** `from.admin_id`,
  `from.admin_name`, `recent_phone_numbers`, `has_phone`, `assignee_ids`, `tag_histories` —
  tất cả đã bị vứt lúc crawl.
- **F2 — Không quy được tin nhắn về nhân viên.** Hệ quả của F1: 596.128/596.128 tin gửi ra
  đều mang `sender_id = pancake_page_id`. Chấm điểm từng sale là **bất khả thi** với dữ liệu
  hiện có → **cố ý loại khỏi phạm vi** (§2 C5).
- **F3 — Không có khoá nối chat → đơn.** `customers.phone` chỉ có 113/66.277 dòng.
  desi-order không lưu facebook user id (chỉ `facebook_pages.fb_page_id`, `orders.fb_ad_id`).
  Đường duy nhất còn lại: bóc SĐT từ nội dung tin — 35.005 tin khớp mẫu SĐT.
- **F4 — Phần lớn lưu lượng là bot.** Thẻ `BOT-MSC06` gắn 12.741 hội thoại, `BOT-BNC02`
  2.328, `TEST MESS` 3.325. Đây là lý do outbound gấp 3,8 lần inbound. Hội thoại bot phải
  được **đánh cờ**, không được trộn vào thống kê nhu cầu.

## 2. Quyết định kiến trúc

| # | Quyết định | Lý do |
|---|---|---|
| **C1** | **Giữ 2 DB, bắc cầu bằng `postgres_fdw`.** Foreign table đặt trong `ai_stg`, ETL kéo qua khi chạy. | Cách ly hoàn toàn: crawler không đụng vào DB prod, không phải migrate 993 MB. Đổi lại là một phụ thuộc vận hành mới — xử lý ở C2. |
| **C2** | **Nguồn không với tới được thì job phải nổ lỗi.** Không `try/except` nuốt, không ghi `succeeded`. Alert Prometheus khi độ trễ > 90 phút, cùng khuôn `AiMartSyncSilent`. | Stack crawler **đang tắt** lúc viết spec (`deploy-postgres-1` exited). FDW biến "quên bật DB" thành "mart đứng im nhưng vẫn xanh" — đúng chế độ hỏng âm thầm mà sự cố 2026-07-22 đã dạy. |
| **C3** | **Hai bảng mart: `ai.hoi_thoai` (grain hội thoại) + `ai.tin_nhan` (grain tin).** | Nhãn AI sống ở mức hội thoại vì nhu cầu/lý do là thuộc tính của cả cuộc trò chuyện. Bảng tin nhắn để agent trích **nguyên văn** làm dẫn chứng thay vì chỉ đưa nhãn — "khách chê đắt" kém xa "khách nói: *…*". |
| **C4** | **Tái dùng nguyên bộ mã đang có, không đẻ bộ mã mới.** Nhu cầu ← `need_types` (36 mã, 30 hiệu lực, 6 nhóm). Lý do không mua ← `ai.tu_dien_ma` nhóm `ly_do_khong_chot` (14 mã). | Đây là toàn bộ giá trị của việc gán nhãn: cùng một bộ mã thì `ai.hoi_thoai.ly_do_khong_mua_ma` so trực tiếp được với `ai.don_hang.ly_do_khong_chot_ma`. Bộ mã riêng = hai kho số không đối chiếu được với nhau. |
| **C5** | **Không chấm điểm nhân viên.** Không có cột `nhan_vien_*` trong spec này. | F2. Bịa một cột luôn NULL tệ hơn không có cột — spec `ai-mart` đã ghi đúng bài học này cho `cuoc_goi.agent_ten`. |
| **C6** | **Không crawl lại.** | Chỉ cần thiết cho mục tiêu đã loại ở C5. Hai câu hỏi còn lại nằm trong *nội dung* tin nhắn, thứ đang có đủ. |
| **C7** | **Nối đơn là best-effort qua SĐT LLM bóc được**, không khớp thì `NULL`. | F3. `COMMENT` phải nói rõ độ phủ, để agent không đọc `don_id IS NULL` thành "khách không mua". |
| **C8** | **Nhãn LLM lưu ở `ai_stg.hoi_thoai_nhan`, giữ nguyên hình dạng `extract_payload` của `callcenter_call_records`.** | FDW cho nguồn chỉ-đọc nên nhãn buộc phải sống bên `desi`. Đúng vai trò staging "nguồn nhiều bước" mà spec `ai-mart` định nghĩa, và giữ hình dạng cũ thì tái dùng được cả code lẫn test của `apps/callcenter`. |

## 3. Bảng contract

### 3.1 `ai.hoi_thoai` — grain: 1 hội thoại

| Nhóm cột | Cột |
|---|---|
| Định danh | `hoi_thoai_id` (`pancake_conversation_id`), `page_ten`, `nen_tang_text` (Facebook / Zalo / TikTok Shop / Shopee / YouTube) |
| Thời gian | `bat_dau`, `bat_dau_ngay` (date, `Asia/Ho_Chi_Minh`), `ket_thuc`, `ket_thuc_ngay` |
| Quy mô | `so_tin`, `so_tin_khach`, `so_tin_page`, `co_khach_tra_loi` |
| Phân loại | `loai_kenh_text` (Tin nhắn / Bình luận / Đánh giá — từ `conversations.type`: INBOX 63.844 · COMMENT 28.283 · RATING 907), `do_bot_xu_ly` (F4), `nhan_tag` (`text[]`), `nhan_tag_text` |
| Nội dung | `noi_dung` — toàn văn ghép theo thứ tự thời gian, tiền tố vai trò `Khách:` / `Page:` |
| Khách | `khach_ten`, `sdt_che` |
| Nhãn AI | `tom_tat_ai`, `nhu_cau_ai` (`text[]`) + `nhu_cau_ai_text`, `nhom_nhu_cau_ai` (`text[]`), `ly_do_khong_mua_ma` (`text[]`) + `ly_do_khong_mua_text`, `san_pham_quan_tam` (`text[]`), `giai_doan_text`, `muc_do_quan_tam_text` |
| Nối đơn | `khach_id`, `don_id`, `ma_don` — `NULL` khi không khớp SĐT (C7) |
| Vận hành | `trang_thai_gan_nhan_text`, `gan_nhan_luc`, `dong_bo_luc` |

`giai_doan_text` là danh mục đóng, seed vào `ai.tu_dien_ma` nhóm `giai_doan_chat`:
Hỏi giá · Hỏi thông tin sản phẩm · Đang cân nhắc · Từ chối · Đã chốt · Sau bán / bảo hành ·
Không xác định.

### 3.2 `ai.tin_nhan` — grain: 1 tin

`tin_nhan_id`, `hoi_thoai_id`, `page_ten`, `gui_luc`, `gui_luc_ngay` (date, `Asia/Ho_Chi_Minh`),
`chieu_text` (Khách gửi / Page gửi), `loai_tin_text` (Tin nhắn / Bình luận / Đánh giá),
`noi_dung`, `co_dinh_kem`.

> Đo được: 0/751.350 tin có `content` rỗng, nên bộ lọc "chỉ tin có nội dung" hiện là no-op.
> Vẫn giữ điều kiện trong transform để tin đính-kèm-thuần ở lần crawl sau không lọt vào.

### 3.3 Bổ sung từ điển `ai.tu_dien_ma`

| Nhóm mã mới | Nguồn seed | Số mã |
|---|---|---|
| `nhu_cau` | `need_types` (`code`, `name_vi`, `group_id` → `need_groups.name_vi`) | 36 (30 hiệu lực) |
| `nhom_nhu_cau` | `need_groups` | 6 |
| `giai_doan_chat` | định nghĩa trong `apps/ai_mart/domain/` | 7 |
| `ly_do_khong_chot` | **đã có** — tái dùng, không thêm | 14 |

Seed `nhu_cau` bằng migration đọc `need_types`; mã `is_active = false` vẫn seed nhưng
`mo_ta` ghi rõ đã ngừng dùng, để nhãn cũ không thành mã mồ côi.

## 4. Luật chuẩn hoá áp dụng

Áp nguyên 8 luật của spec `ai-mart`. Ba điểm cần nói riêng:

- **Luật 4 (múi giờ):** mọi cột `*_ngay` quy về `Asia/Ho_Chi_Minh`. `messages.inserted_at`
  là UTC.
- **Luật 6 (PII):** `noi_dung` của cả hai bảng là **văn bản thô chưa che** — khách gõ SĐT
  và địa chỉ thẳng vào chat. Đây là **ngoại lệ có chủ đích, cùng chuẩn Ruling 17** đã áp cho
  `ai.cuoc_goi.transcript`: agent cần nguyên văn để trả lời về nội dung tư vấn. `COMMENT`
  hai cột phải nói thẳng điều này và **cấm agent trích SĐT/địa chỉ từ đó ra câu trả lời**;
  `test_masking` cố ý bỏ qua hai cột này. Mọi cột khác che SĐT dạng `0912***678`.
- **Luật 7 (COMMENT):** bắt buộc mọi bảng, mọi cột. `COMMENT` của `don_id`/`ma_don` phải
  ghi độ phủ thực đo được sau backfill, không để agent tự suy.

## 5. Cơ chế ETL

### 5.1 Cầu FDW

```sql
CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER chat_crawler FOREIGN DATA WRAPPER postgres_fdw
  OPTIONS (host 'deploy-postgres-1', port '5432', dbname 'crawler');
CREATE USER MAPPING FOR desi SERVER chat_crawler
  OPTIONS (user 'chat_ro', password :'CHAT_CRAWLER_DB_PASSWORD');
IMPORT FOREIGN SCHEMA public LIMIT TO (pages, conversations, customers, messages, tags, conversation_tags)
  FROM SERVER chat_crawler INTO ai_stg;
```

Mật khẩu qua env mới `CHAT_CRAWLER_DB_PASSWORD`, thêm vào các file compose có backend.
Cần một role chỉ-đọc phía DB `crawler` cho user mapping — **không** dùng superuser `crawler`.

Kết nối FDW do **tiến trình Postgres của `desi-postgres` tự mở**, nên chính container đó
phải với tới `deploy-postgres-1`. Đo được: hai container nằm trên hai mạng docker tách rời
(`desi-order_default` 172.19.0.20 và `deploy_default` 172.18.0.2), `deploy-postgres-1` lại
chỉ bind `127.0.0.1:15432` nên không đi vòng qua host được. Cách xử lý:
`docker network connect deploy_default desi-postgres`, khai báo cố định trong compose để
không mất sau `docker compose up`. Xác minh ở S1.

`ai_stg` **không** cấp quyền cho `hermes_ro` — giữ nguyên §6 spec `ai-mart`.

### 5.2 Watermark

`conversations.updated_at` bên crawler là `updated_at` **của Pancake** (đo được: trải
22/11/2024 → 01/09/2026, không phải thời điểm crawl), nên là watermark hợp lệ.
Dùng `WHERE updated_at > moc − 5 phút`, ghi mốc vào `ai_meta.moc_dong_bo`.

`messages` append-only theo `pancake_message_id` → nạp tăng dần theo `inserted_at` của các
hội thoại vừa đổi, `ON CONFLICT DO NOTHING`.

> Rủi ro phải kiểm ở S1: nếu crawler ghi đè `updated_at` bằng `now()` lúc upsert thay vì
> lấy từ payload, watermark vẫn chạy nhưng full rebuild đêm là lưới an toàn.

### 5.3 Nhịp

| Nhịp | Việc |
|---|---|
| Mỗi 30 phút | Upsert hội thoại + tin nhắn đổi từ mốc; enqueue gán nhãn cho hội thoại mới đủ điều kiện |
| 04:50 hằng ngày | Full rebuild cả hai bảng, sau `ai_mart` nhóm đêm |

Dùng lại `single_flight` + `statement_timeout` của `apps/common/db_guards`. Mỗi lượt ghi
một dòng `ai_meta.nhat_ky_chay`. Hai bảng mới xuất hiện trong `ai.tinh_trang_du_lieu` và
`ai.huong_dan_bang` mà không cần sửa gì — hai view này sinh từ `pg_description` và
`ai_meta`.

## 6. Pipeline gán nhãn LLM

**Bảng:** `ai_stg.hoi_thoai_nhan` — `hoi_thoai_id` (PK), `extract_payload jsonb`
(`{raw, clean, rejected, filled}`), `extract_status`, `extract_error`, `model`,
`noi_dung_hash`, `created_at`, `updated_at`.

**Cơ chế:** bê nguyên `apps/callcenter/extract.py` — prompt liệt kê **toàn bộ mã hợp lệ**,
LLM trả JSON, code tách `clean` (mã có trong từ điển) khỏi `rejected` (mã bịa). Chỉ `clean`
lên mart. `rejected` giữ lại để biết từ điển đang thiếu mã nào — đó là tín hiệu nghiệp vụ,
không phải rác.

**Model:** dùng lại `settings.GEMINI_EXTRACT_MODEL`.

**Phạm vi chạy:** hội thoại có ≥1 tin khách (73.267). Ưu tiên backfill theo lô giảm dần:
≥2 lượt khách nói (18.516) → còn lại. `noi_dung_hash` chặn gán nhãn lại hội thoại không đổi.

**Chi phí:** toàn bộ inbound 4,06 triệu ký tự (~1,3 triệu token); kể cả kèm ngữ cảnh
outbound, backfill một lượt vẫn ở mức vài chục nghìn đồng. Không phải ràng buộc thiết kế.

**Đầu ra mỗi hội thoại:** `tom_tat_ai`, `nhu_cau[]`, `ly_do_khong_mua[]`, `san_pham_quan_tam[]`,
`giai_doan`, `muc_do_quan_tam`, `sdt_khach_neu_co`.

> Hội thoại `do_bot_xu_ly = true` (F4) vẫn gán nhãn — khách vẫn nói nhu cầu thật với bot —
> nhưng cột cờ cho phép lọc ra khi đọc số.

## 7. Bảo mật

Sau khi tạo bảng phải chạy lại `ai_mart_grant_role` để `hermes_ro` thấy `ai.hoi_thoai`
và `ai.tin_nhan` (`ALTER DEFAULT PRIVILEGES` đã có nhưng migration tạo bảng bằng owner khác
thì vẫn phải grant — nợ này đã ghi trong runbook `2026-08-28-ai-mart-deploy.md`).

Không cấp quyền nào trên `ai_stg` — nơi chứa foreign table và `extract_payload` thô.

## 8. Bộ test

| File | Chặn điều gì |
|---|---|
| `test_contract.py` (mở rộng) | Hai bảng mới có `COMMENT` đủ bảng + cột, tên cột đúng quy ước tiếng Việt không dấu |
| `test_dictionary.py` (mở rộng) | Mọi mã trong `nhu_cau_ai`, `ly_do_khong_mua_ma`, `giai_doan_text` đều có trong `ai.tu_dien_ma` |
| `test_masking.py` (mở rộng) | Không cột nào ngoài `noi_dung` lộ SĐT đầy đủ; ngoại lệ khai báo tường minh |
| `test_chat_extract.py` (mới) | Mã LLM bịa rơi vào `rejected`, không lên mart; `noi_dung_hash` chặn gán nhãn lặp |
| `test_chat_etl.py` (mới) | Upsert tăng dần **bằng** full rebuild trên cùng bộ dữ liệu; nguồn FDW chết thì job **raise**, không nuốt |

## 9. Ngưỡng đổi ý

- `ai.tin_nhan` vượt ~10 triệu dòng → cân nhắc partition theo tháng như bên crawler.
- Lượt sync 30' vượt 2–3 phút → scope transform theo danh sách id thay vì CTE quét bảng
  (đúng nợ đã ghi ở S2 spec `ai-mart`).
- `rejected` liên tục có cùng một khái niệm lặp lại → thêm mã vào `need_types`, không nới
  ràng buộc cho LLM tự do sinh nhãn.
- Nếu về sau cần chấm điểm sale: phải sửa crawler lưu `from.admin_id` + crawl lại (C5/C6),
  đó là spec khác.

## 10. Sprint

| Sprint | Nội dung |
|---|---|
| **S1** | Cầu FDW (mạng docker, role chỉ-đọc phía crawler, foreign table trong `ai_stg`), xác minh `updated_at` dùng được làm watermark, seed từ điển `nhu_cau` / `nhom_nhu_cau` / `giai_doan_chat` |
| **S2** | `ai.hoi_thoai` + `ai.tin_nhan` (chưa có cột nhãn AI), ETL tăng dần + rebuild đêm, `test_contract` / `test_dictionary` / `test_chat_etl` |
| **S3** | `ai_stg.hoi_thoai_nhan` + pipeline gán nhãn, đổ nhãn lên mart, `test_chat_extract`, backfill theo lô |
| **S4** | Nối đơn qua SĐT (C7) + đo độ phủ thật ghi vào `COMMENT`, grant `hermes_ro`, alert độ trễ, runbook deploy |

S2 là sprint chứng minh kiến trúc chạy được: sau nó phải trả lời được bằng SQL thuần
"tháng 8 có bao nhiêu hội thoại khách thật sự trao đổi, chia theo nền tảng" mà không cần
mở SPA của crawler.
