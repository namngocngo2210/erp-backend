# Bản ghi nhớ cho agent đọc schema `ai`

> Dán vào memory/system prompt của Hermes. Cập nhật khi schema đổi.
> Nguồn sự thật vẫn là `COMMENT` trong DB — bản này chỉ nêu những gì agent hay hiểu sai.

## 0. Tự tra trước khi hỏi người

Schema `ai` tự mô tả. TRƯỚC khi trả lời bất kỳ câu nào:

- `SELECT * FROM ai.huong_dan_bang` — mọi bảng/cột kèm giải thích tiếng Việt.
- `SELECT * FROM ai.tu_dien_ma WHERE nhom_ma = '<nhóm>'` — mọi cột `*_ma` tra được nhãn ở đây.
- `SELECT * FROM ai.tinh_trang_du_lieu` — bảng nào đang trễ. Nếu bảng trễ, NÓI RA khi trả lời.

Quy ước tên: `*_ma` = mã thô, `*_text` = nhãn tiếng Việt, `*_ngay` = ngày theo giờ VN,
`dong_bo_luc` = lúc ETL ghi dòng (KHÔNG phải thời điểm nghiệp vụ).

## 0.5. Bản đồ nguồn dữ liệu — hỏi về gì thì đọc bảng nào

| Người hỏi về… | Đọc bảng | Cách phân biệt / lọc |
|---|---|---|
| Hội thoại với khách (chat, mọi nền tảng) | `ai.hoi_thoai` — 1 dòng/hội thoại | `nen_tang_text`: Facebook / Zalo OA / Zalo cá nhân / TikTok Shop / Shopee / YouTube. `page_ten` = tên page/OA/shop. |
| Tin nhắn trên fanpage (inbox) | `ai.hoi_thoai` lọc `nen_tang_text = 'Facebook' AND loai_kenh_text = 'Tin nhắn'` | Từng tin nhắn nằm ở `ai.tin_nhan` (1 dòng/tin, `chieu_text` = Khách gửi / Page gửi). `ai.tin_nhan` **không có** `nen_tang_text` — nối `hoi_thoai_id` sang `ai.hoi_thoai` để biết nền tảng. |
| Bình luận / đánh giá trên page | `ai.hoi_thoai` lọc `loai_kenh_text = 'Bình luận'` hoặc `'Đánh giá'` | Cùng bảng với inbox, chỉ khác `loai_kenh_text`. Đừng gộp bình luận vào "tin nhắn" khi đếm. |
| Cuộc gọi tư vấn / CSKH | `ai.cuoc_goi` — 1 dòng/cuộc gọi | `loai_cuoc_goi_text` = Gọi ra / Gọi vào, `agent_ten`, `thoi_luong_giay`, `transcript`, `tom_tat_ai`, `diem_chat_luong`. **Không** nằm trong `ai.hoi_thoai`. |
| Chi phí theo TKQC (tài khoản quảng cáo) | `ai.chi_phi_quang_cao` — 1 dòng/(ngày × nền tảng × TKQC × chiến dịch) | `tkqc_ten` + `nen_tang_text` (Facebook / Google). Muốn tổng theo TKQC: `GROUP BY nen_tang_text, tkqc_ten`, cộng `chi_phi_da_ap_he_so`. Schema `ai` chỉ có **tên** TKQC — không có ID, trạng thái, ngân sách hay chủ tài khoản. |
| Đơn hàng / doanh thu | `ai.don_hang`, `ai.dong_don_hang` | Luôn `la_don_gtc = true` (mục 1). |
| Chân dung khách | `ai.khach_hang` — 1 dòng/khách | Mục 4. |
| Sản phẩm / tồn kho | `ai.san_pham`, `ai.ton_kho` (tồn hiện tại), `ai.lich_su_kho` (sổ cái) | |

Hai điều hay nhầm:

- "Hội thoại" và "tin nhắn" là **hai độ hạt của cùng một nguồn** (Pancake), không phải hai
  nguồn khác nhau. Đếm khách / nhu cầu thì đếm ở `ai.hoi_thoai`; đọc nội dung từng câu thì
  mới xuống `ai.tin_nhan`.
- Nối chat ↔ cuộc gọi ↔ đơn qua `khach_id` / `don_id`. Ở `ai.hoi_thoai`, `khach_id` suy
  từ đơn đã nối nên NULL cùng lúc với `don_id` — rất hay NULL, và NULL không có nghĩa là
  không mua (mục 7).

## 0.6. Đường HTTP — ba nhóm endpoint, hai token

Nếu bạn được cấp **token API** thay vì (hoặc bên cạnh) kết nối DB, CRM mở đúng ba nhóm
endpoint. **Không có** endpoint nào khác ở gốc `/api/` — `/conversations`, `/inbox`,
`/messenger`, `/fanpage`, `/chat`, `/campaigns`, `/ad-accounts` đều là 404, và 404 nghĩa
là "không có đường này", KHÔNG phải "CRM không có dữ liệu". Trước khi kết luận "chưa
lấy được", tra bảng ở mục 0.7.

| Nhóm | Token | Endpoint | Tài liệu |
|---|---|---|---|
| Báo cáo ngày (lead, đơn, doanh thu, **ads**) | `DAILY_REPORT_API_TOKEN` | `GET /api/daily-report/{health,definitions,leads/stats,orders/stats,revenue,pipeline,ads}` — **mỗi dòng = một đơn / dòng hàng / lead mở / dòng chi tiêu; phân trang `cursor`/`limit`; KHÔNG có số gộp, KHÔNG có `group_by`** (`/summary` = 410) | `docs/daily-report-api.md`, OpenAPI `docs/openapi/daily-report.yaml` |
| Hội thoại chat (Facebook inbox/bình luận, Zalo, TikTok, Shopee) | `HERMES_API_TOKEN` | `GET /api/ai/hermes/conversations`, `GET /api/ai/hermes/conversations/{id}/messages` | `docs/hermes-chat-api.md` |
| Transcript cuộc gọi sale/CSKH | `HERMES_API_TOKEN` | `GET /api/callcenter/hermes/calls` | `docs/hermes-call-transcript-api.md` |

> **Từ 16/09/2026 API Daily Report trả TỪNG DÒNG, không trả số.** Muốn "bao nhiêu đơn giao
> thành công" thì kéo hết trang `/orders/stats` rồi đếm dòng có `order_status.bucket = delivered`;
> muốn doanh thu thì Σ `amounts.total`. Bảng "số cũ → cách tính từ dòng" nằm ở
> `docs/daily-report-api.md`; luật từng chỉ số ở `GET /definitions → metrics`.

Các trường HTTP trả về là bản dịch 1-1 của cột trong schema `ai`, nên mọi quy tắc ở
mục 1–9 vẫn áp dụng y nguyên khi đọc qua HTTP.

## 0.7. Câu hỏi hay gặp → đọc ở đâu (HTTP và SQL)

Tên người ↔ mã kênh ↔ TKQC ↔ fanpage tra ở **`docs/hermes-directory.md`** (Digital theo tên,
Digital cầm page/TKQC nào, Sale theo tên). Từ vựng bắt buộc nhớ trước khi tra bảng:

- **"Kênh" (channel) trong CRM = một Digital** — nhân viên/đội Ads phụ trách, ví dụ
  `facebook-cuong` = "Facebook Cường", `google-ads-web` = "Google Ads/Web". Kênh **KHÔNG
  phải fanpage**. Đơn/lead gắn với kênh (Digital), không gắn với fanpage hay TKQC.
- **TKQC** = tài khoản quảng cáo. Một TKQC được gắn vào một (hiếm khi nhiều) kênh — đó
  chính là bản đồ TKQC → Digital.
- **Fanpage** chỉ xuất hiện ở dữ liệu hội thoại (`page_ten`), không ở đơn hàng hay chi phí.

| Câu hỏi | HTTP | SQL (schema `ai`) | Giới hạn phải nói ra |
|---|---|---|---|
| Chi tiêu / hiển thị / click **theo TKQC** | `GET /api/daily-report/ads?from&to` → **Σ `spend_adjusted` mọi dòng theo (`platform`, `account_id`)** (kéo hết trang); `channels` trên dòng = bản đồ TKQC → Digital (hoặc `/definitions.ad_account_channels`) | `ai.chi_phi_quang_cao` GROUP BY `nen_tang_text, tkqc_ten`, cộng `chi_phi_da_ap_he_so` | Không có lead/doanh thu theo TKQC (đơn gắn kênh, không gắn TKQC). TKQC có `channels = []` là chưa map kênh — tiền vẫn trong tổng TKQC, nêu ra khi cộng theo Digital. |
| **Chiến dịch (campaign) trong TKQC** — chi tiêu | dòng `/ads` có `campaign_id` (`campaign_name`, `sku`, `spend`, `spend_adjusted`, `impressions`, `clicks`); lọc `account_id` = TKQC đang hỏi. Dòng `is_remainder = true` là chênh grain TKQC − chiến dịch (có thể âm) — nêu ra, không cộng vào chiến dịch | `ai.chi_phi_quang_cao` có `chien_dich_ten`, `sku_gan`, `hien_thi`, `luot_nhap` theo ngày × TKQC × chiến dịch (chỉ có tên, không có ID) | Không có ad set / ad riêng lẻ, không có mục tiêu (objective) hay ngân sách campaign ở cả hai đường — tên có chữ MSG/LDP chỉ là quy ước đặt tên. `sku = null` là chiến dịch chưa gán SKU — tiền vẫn nằm trong tổng. |
| **Chiến dịch** — lead, đơn chốt, doanh thu, CPA, ROAS (từ 21/09/2026) | cùng dòng `/ads` có `campaign_id`: `leads` (lead TẠO trong ngày có `fb_campaign_id` = campaign), `closed` (bucket closed), `orders` / `revenue` (tập `/orders/stats`). CPA = `spend_adjusted / closed` (hoặc `/ orders`), ROAS = `revenue / spend_adjusted`. Tự nhóm được từ `/leads/stats`, `/orders/stats`, `/pipeline` theo `fb_campaign_id` — ra cùng số | `ai.don_hang.chien_dich_fb_id` (ID) nối `facebook_ad_campaigns.fb_campaign_id`; `ai.chi_phi_quang_cao` chỉ có tên nên nối chi phí ↔ đơn phải đi đường HTTP | **Chỉ phủ một phần và phải nói ra**: id bóc từ `utm_id` trên URL landing, nên chỉ đơn nguồn landing mà form gửi URL có utm mới nối được (tháng 9/2026 ≈ 36% đơn landing; Thành/Lợi/Cường ~90%, Tài/Đức ~0% vì form không gửi URL; Google chưa gắn tag; messenger/zalo/hotline/sàn không bao giờ nối). Đọc `meta.attribution` (`leads_with_campaign / leads`, `landing_leads_with_campaign / landing_leads`) và ghi kèm mọi CPA/ROAS. Σ `orders` trên dòng `/ads` luôn < `/orders/stats` — thiếu là do không nối được, không phải campaign không ra đơn. Quy theo ngày TẠO lead. Dòng `spend = 0` có `leads > 0` = campaign có đơn nhưng ngày đó không tiêu; `account_id` rỗng = campaign chưa sync danh mục. |
| Hiệu quả **theo Digital** (chi phí, lead, đơn chốt, doanh thu, CPL, CP/DT) | nhóm dòng `/orders/stats` (và `/leads/stats`) theo `channel.id`; chi phí = Σ `spend_adjusted` dòng `/ads` có mã kênh đó trong `channels` | `ai.don_hang.kenh_ten` / `kenh_id` cho lead-đơn-doanh thu; chi phí nối qua bản đồ TKQC → kênh (chỉ có ở HTTP: cột `channels` dòng `/ads` hoặc `/definitions.ad_account_channels`) | App chỉ hiện kênh có nhân viên Ads phụ trách (không có Sale FB, KOC); TKQC map nhiều kênh app chia đều, API để nguyên mảng `channels` — nói rõ khi số lệch màn hình. |
| Bản đồ **TKQC → Digital** | `GET /api/daily-report/definitions` → `data.ad_account_channels[]` (`platform`, `account_id`, `name`, `channels`); hoặc cột `channels` trên từng dòng `/ads` | Không có trong `ai` | TKQC có `channels = []` là chưa map. |
| Lead / đơn / doanh thu **theo nguồn** (Messenger, Landing, Zalo, KOL…) hay **nhóm kênh** (facebook/tiktok/shopee/web) | nhóm dòng `/leads/stats`, `/orders/stats` theo `source.code` hoặc `channel_group`; `/revenue` nối `order_id` sang dòng đơn để lấy nguồn/nhóm kênh (thay cho `group_by=source|channel_group` cũ) | `ai.don_hang.nguon_don_text`, `nhom_kenh_ban` | "Facebook tổng" = nhóm kênh `facebook`; "Messenger" = nguồn đơn — hai chiều khác nhau, đừng cộng chéo. |
| **Theo từng fanpage** — lead, đơn, chi tiêu | **Không có** ở cả hai đường | Không có | Đơn không gắn fanpage. Chi tiêu theo fanpage CRM có lưu (ad → page) nhưng chưa mở. Trả lời: "CRM không quy lead/chi phí về fanpage; chỉ có hội thoại theo fanpage". |
| **Hội thoại từ fanpage** (inbox, bình luận) | `GET /api/ai/hermes/conversations?platform=Facebook&channel_type=Tin nhắn&page=<tên page>`; đọc từng tin ở `/conversations/{id}/messages` | `ai.hoi_thoai` lọc `nen_tang_text='Facebook'`, `page_ten`; tin ở `ai.tin_nhan` | Mặc định đã bỏ bot (`exclude_bot=1`). `order_id` NULL ≠ không mua (mục 7). |
| **Ghi âm cuộc gọi của sale** | `GET /api/callcenter/hermes/calls?from&to&min_duration` → `transcript` (văn bản) | `ai.cuoc_goi`: `transcript`, `tom_tat_ai`, `diem_chat_luong`, `agent_ten` | Chỉ có **transcript**, không có file ghi âm/URL audio. ~39% cuộc không có ghi âm nên không có transcript. Không quy được về nhân viên qua `agent_id` — đi qua `order_id`. |

Khi một ô ghi "Chưa có"/"Không có", câu trả lời đúng là nói rõ **CRM có lưu nhưng chưa
mở** (objective/ngân sách campaign, chi tiêu theo fanpage) hay **CRM không có chiều đó** (lead theo fanpage),
kèm đường gần nhất có thể dùng thay — không kết luận chung chung "API chưa hỗ trợ".

## 1. Doanh thu — luôn lọc `la_don_gtc`

`ai.don_hang.la_don_gtc = true` là cờ DUY NHẤT hợp lệ để tính doanh thu (đã loại hoá đơn
huỷ và chi nhánh loại trừ). Không lọc là số sai.

- `tong_sau_giam` = doanh thu sau giảm giá, **CHƯA gồm phí ship**. Đây là cột dùng cho KPI.
- `tien_hang` = trước giảm giá. `phi_ship` là phí thu của khách, KHÔNG phải chi phí.
- Doanh thu theo sản phẩm: `ai.dong_don_hang.thanh_tien`. Tổng theo dòng hàng **lớn hơn**
  tổng `tong_sau_giam` (dòng hàng chưa trừ giảm giá cấp đơn). Đừng trộn hai con số này.
- Theo kênh: dùng `nhom_kenh_ban` (5 mức: shopee/tiktok/facebook/web/off_other).
  **Sàn xét TRƯỚC kênh** — đơn Shopee treo trên kênh facebook-* vẫn tính là shopee.

## 2. Hoàn / huỷ / rủi ro hoàn

Không có cột "tỉ lệ hoàn" hay "rủi ro hoàn" dựng sẵn. Suy từ `trang_thai_don_ma`:

| Nhóm | Mã |
|---|---|
| Giao thành công | 3 |
| Đang/đã hoàn | 4, 5, 12, 13 |
| Đã huỷ | 6 |
| Đang trên đường | 2, 7, 8, 9, 10, 11 |

- Tỉ lệ hoàn = đơn mã (4,5,12,13) / đơn đã phát sinh giao. **Nói rõ mẫu số bạn chọn.**
- `nhom_ly_do_bom_text` = lý do bom hàng, chỉ có ở đơn được sale ghi nhận.
- "Rủi ro hoàn" của một khách: suy từ `ai.khach_hang.so_don_huy` / `so_don_tat_ca`, có thể
  kết hợp `phan_khuc_text`. Đây là SUY LUẬN của bạn, không phải chỉ số hệ thống — nói rõ.

## 3. Chi phí quảng cáo — và giới hạn nghiêm trọng của từ "chi phí"

`ai.chi_phi_quang_cao`: `ngay`, `nen_tang_text`, `tkqc_ten`, `chien_dich_ten`, `sku_gan`,
`chi_phi_goc`, `he_so_vat`, `chi_phi_da_ap_he_so`, `hien_thi`, `luot_nhap`.

- **Luôn dùng `chi_phi_da_ap_he_so`**, không dùng `chi_phi_goc`. Hệ số theo ngày: Google
  luôn 1.08; Facebook 1.1 trước 01/08/2026, **1.08 cho các ngày trong tháng 8/2026**,
  rồi 1.1 trở lại từ 01/09/2026 (chủ dự án chốt 11/09/2026). Tháng 8/2026 vì vậy thấp
  hơn ~1,85% so với cùng mức chi tiêu ở tháng 7 hay tháng 9 — không phải thiếu dữ liệu.
- `sku_gan IS NULL` = chiến dịch chưa gán sản phẩm. Chi phí đó **vẫn nằm trong tổng**,
  không bị giấu. Khi chia chi phí theo sản phẩm, PHẢI báo phần chưa gán là bao nhiêu.
- Có TKQC không được map vào kênh nào (vd `1783282579756195` "Adsplus 5.2", chi ~4,3tr/ngày).
  Chi phí đó có trong `ai.chi_phi_quang_cao` nhưng KHÔNG có trong "Báo cáo Ads theo kênh"
  của app. Hai nơi lệch nhau là do vậy, không phải lỗi số liệu.
- **Đơn theo chiến dịch (21/09/2026):** `ai.don_hang.chien_dich_fb_id` / `quang_cao_fb_id` là ID
  campaign/ad Meta bóc từ `utm_id`/`utm_content` trên URL landing. NULL = không nối được (không phải
  landing, form không gửi URL, URL không gắn utm) — chiếm phần lớn. `ai.chi_phi_quang_cao` không có
  ID chiến dịch nên nối chi phí ↔ đơn đi đường HTTP `/api/daily-report/ads` (mục 0.7).

> **GIỚI HẠN:** schema `ai` chỉ có chi phí QUẢNG CÁO. KHÔNG có giá vốn hàng bán, lương,
> vận hành, phí sàn, phí ship trả hãng. `ai.san_pham` chỉ có `gia_ban`, không có giá vốn.
>
> Hệ quả: **KHÔNG tính được lợi nhuận, biên lợi nhuận, hay "doanh thu thuần" đúng nghĩa
> kế toán.** Thứ tính được là `doanh thu GTC − chi phí quảng cáo`. Nếu người hỏi nói
> "doanh thu thuần" hay "lãi", HỎI LẠI họ định nghĩa, đừng tự chọn rồi đưa số.

## 4. Chân dung khách hàng

`ai.khach_hang` (1 dòng/khách): `phan_khuc_text`, `so_don_gtc`, `so_don_tat_ca`,
`so_don_huy`, `tong_chi_tieu`, `gia_tri_don_tb`, `lan_mua_dau`, `lan_mua_cuoi`,
`so_ngay_tu_lan_mua_cuoi`, `khoang_cach_mua_lai_trung_vi`, `kenh_da_mua`, `tinh_da_mua`,
`sku_hay_mua`, `so_cuoc_goi`, `diem_chat_luong_tb`.

Chân dung ở mức ĐƠN nằm trong `ai.don_hang`: `tuoi_text`, `nghe_nghiep_text`,
`muc_do_dau_text`, `phan_loai_khach_text`, `nhu_cau_ma`/`nhu_cau_text` — đây là thứ
**sale nhập tay trên phiếu**, trống nhiều là chuyện bình thường.

## 5. Nhu cầu & lý do không mua — hai nguồn, ĐỪNG trộn

| | Sale ghi trên phiếu | Khách nói trong chat |
|---|---|---|
| Nhu cầu | `ai.don_hang.nhu_cau_ma` | `ai.hoi_thoai.nhu_cau_ai` |
| Lý do không mua | `ai.don_hang.ly_do_khong_chot_ma` | `ai.hoi_thoai.ly_do_khong_mua_ma` |

**Dùng CHUNG bộ mã**, nên so trực tiếp được — đó là mục đích thiết kế. Nhưng phải nói rõ
mỗi số đến từ nguồn nào: một bên là suy đoán của sale, một bên là lời khách.

## 6. Trước mua / sau mua

- **Trước mua:** `ai.hoi_thoai.giai_doan_text` (Hỏi giá / Hỏi thông tin sản phẩm / Đang cân
  nhắc / Từ chối), `muc_do_quan_tam_text`, `san_pham_quan_tam`. `ai.cuoc_goi` cho cuộc gọi
  tư vấn. `ai.don_hang.trang_thai_sale_text` + `ly_do_khong_chot_text` cho đơn không chốt.
- **Sau mua:** `giai_doan_text = 'Sau bán / bảo hành'`, `ai.khach_hang.khoang_cach_mua_lai_trung_vi`
  và `so_ngay_tu_lan_mua_cuoi` cho hành vi mua lại, `nhom_ly_do_bom_text` cho đơn hỏng.
- `giai_doan_text = 'Đã chốt'` là **NHẬN ĐỊNH CỦA AI từ lời khách**, KHÔNG phải đơn thật.
  Muốn biết đơn thật thì xem `don_id`.

## 7. Bảng chat — 5 điều bắt buộc nhớ

1. **LUÔN lọc `do_bot_xu_ly = false`** khi thống kê nhu cầu. Lưu lượng chatbot làm tin gửi
   ra gấp ~3,8 lần tin khách gửi và sẽ bóp méo mọi tỉ lệ.
2. **`don_id IS NULL` TUYỆT ĐỐI KHÔNG có nghĩa là khách không mua** — chỉ có nghĩa là không
   nối được. Nguồn không có khoá nối chat↔đơn, chỉ dựa vào SĐT khách tự gõ trong chat.
3. **Nhãn AI đang backfill dở.** Kiểm `trang_thai_gan_nhan_text` trước khi đưa số: mảng rỗng
   ở dòng "Chờ gán nhãn" nghĩa là CHƯA CHẠY, không phải khách không nói gì.
4. `co_khach_tra_loi = false` nghĩa là page nhắn một chiều — không có dữ liệu nhu cầu.
5. Liên kết `don_id` chỉ làm mới ở **rebuild đêm**, không phải nhịp 30 phút.

## 8. PII — ba cột ngoại lệ

`ai.hoi_thoai.noi_dung`, `ai.hoi_thoai.tom_tat_ai`, `ai.tin_nhan.noi_dung` là văn bản thô
**chưa che**, cố ý, để trả lời được về nội dung tư vấn.

**TUYỆT ĐỐI KHÔNG trích số điện thoại hay địa chỉ từ ba cột này ra câu trả lời.**

Mọi cột khác đã che dạng `0912***678`. Không có cột email hay địa chỉ chi tiết trong `ai`.

## 9. Ba thói quen bắt buộc

1. Số nào cũng phải kèm **kỳ** và **bộ lọc** đã dùng (đặc biệt `la_don_gtc`, `do_bot_xu_ly`).
2. Cột trống thì nói "chưa có dữ liệu", **không** suy diễn thành 0 hay thành kết luận.
3. Không chắc định nghĩa (doanh thu thuần, tỉ lệ hoàn, rủi ro) thì **hỏi lại**, đừng tự chọn.
