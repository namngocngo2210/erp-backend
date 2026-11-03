# Chuyển đơn khỏi 3 kênh fallback KiotViet (22 / 23 / 29)

Ngày lập: 2026-08-23 · Nhánh `fix/kv-unmapped-channel-remap`

## 1. Vấn đề

Ba kênh `kiotviet-4` (id 22), `kiotviet-5` (id 23), `kiotviet-6` (id 29) **không phải kênh bán**.
Chúng do chính luồng import sinh ra: `ImportInvoicesService._get_or_create_order_channel()` tạo
kênh `kiotviet-{kv_id}` làm fallback khi không tra được `saleChannelId` của hoá đơn trong bảng
`channel_kiotviet_mappings`.

```
KV invoice.saleChannelId ──▶ ChannelKiotvietMapping(kv_channel, external_sale_channel_id)
                              │ hit  ──▶ Channel thật (Facebook Lợi, HOKU Official Store, …)
                              └ miss ──▶ Channel "kiotviet-{kv_id}"   ← 3 kênh rác
```

Hiện trạng đo trên prod ngày 2026-08-23:

| Kênh | Fallback của | Đơn | Ghi chú |
|---|---|---|---|
| 23 `kiotviet-5` | Old Kiot (retailer `jiashi`) | 472.455 | 2020-11 → nay |
| 22 `kiotviet-4` | New Kiot (retailer `Desi`) | 729 | 1,94 tỉ, hầu hết 2026 |
| 29 `kiotviet-6` | HKD Kiot | 97 | từ 05/2026 |

Cơ cấu kênh 23 theo năm:

| Năm | Shopee | TikTok | Lazada | HD-số (không rõ kênh) | Tổng |
|---|---|---|---|---|---|
| 2020–2024 | 410.164 | 9 | 35.239 | 3.705 | 450.869 |
| 2025 | 1.112 | 67 | 1 | 8.352 | 10.542 |
| 2026 | 3.489 | 1.892 | 0 | 5.382 | 11.044 |

**Dòng chảy chưa bị chặn**: tháng 7 và 8/2026 vẫn có 231 đơn TikTok mới rơi vào kênh 23
(branch `30965`). Sửa lịch sử mà không vá mapping thì tháng sau kênh rác đầy lại.

## 2. Quyết định nghiệp vụ (chốt 2026-08-23)

| # | Quyết định | Lý do |
|---|---|---|
| 1 | Backfill **từ 2025-01-01 → nay** (~22.400 đơn) | Vùng dữ liệu đang thực sự dùng để ra quyết định. Không đụng 450.869 đơn 2020–2024 để báo cáo lịch sử không đổi số. |
| 2 | 35.239 đơn **Lazada để nguyên** ở kênh rác | Lazada đã ngừng từ 2024 (2025 còn đúng 1 đơn). Không đẻ kênh mới cho dữ liệu chết. |
| 3 | Đơn có `saleChannelId` **rỗng** → dồn về kênh **Khác (id 26)** | Đơn bán tại quầy/không gắn kênh trên KV. |
| 4 | Đơn có `saleChannelId` **nhưng thiếu mapping** → **KHÔNG** dồn về Khác | Đây là doanh thu của shop thật chưa khai báo kênh. Dồn vào "Khác" là chôn mất. Phải liệt kê ra, khai báo `Channel` + mapping, rồi chạy lại. |

Quyết định 4 là điểm khác biệt so với code cũ: `_handle_check_channel_match` gộp cả hai ca
thành một nhãn `NO_SALE_CHANNEL` rồi bỏ qua, nên không có cách nào biết shop nào đang thiếu khai báo.

## 3. Thay đổi code

Không viết công cụ mới — `import_kiotviet_invoices --check-channel-match` đã có sẵn và gọi đúng
nguồn sự thật (KV API trả `saleChannelId`; DB không lưu trường này nên **không thể** suy ngược
bằng SQL). Chỉ bổ sung 3 điểm để chạy backfill an toàn:

| Thay đổi | File | Mục đích |
|---|---|---|
| Tách `no_sale_channel` thành `empty_sale_channel` + `unmapped_sale_channel` | `_import_service_handlers_mixin.py` | Phân biệt ca 3 và ca 4 ở mục 2 |
| Thống kê `sale_channel_id` chưa map (id + số hoá đơn, xếp giảm dần) | `_import_service_handlers_mixin.py` | Đầu ra để khai báo kênh/mapping còn thiếu |
| `--only-current-channel-ids` | CLI + service | Chỉ sửa đơn đang thuộc các kênh chỉ định (22,23,29). Không có filter, lệnh sẽ ghi đè **mọi** đơn lệch kênh — kể cả đơn nhân viên đã sửa tay |
| `--empty-sale-channel-to` | CLI + service | Kênh nhận đơn `saleChannelId` rỗng (26 = Khác) |

`--dry-run` vẫn đếm đủ như chạy thật, chỉ không ghi DB.

## 4. Runbook

Khối lượng phải quét (invoice KV từ 2025-01-01, pageSize 100):

| KV | branch_ids | Invoice quét | Đơn ở kênh rác | Request ước tính |
|---|---|---|---|---|
| 5 Old Kiot | `30965,1000000139,1000000477` | 58.843 | 21.587 | ~590 |
| 4 New Kiot | `231174,241190` | 24.961 | 711 | ~250 |
| 6 HKD Kiot | `89084` | 6.274 | 97 | ~63 |

### Cách chạy: dùng `scripts/kv-remap-backfill.sh`

Script lặp từng tháng với đúng bộ tham số ở mục 4, gộp danh sách sale channel chưa khai báo
của cả dải, và **mặc định là dry-run** — phải thêm `--apply` mới ghi DB:

```bash
cd ~/desi-order
bash scripts/kv-remap-backfill.sh 5 2026-08 2026-08            # thử 1 tháng
bash scripts/kv-remap-backfill.sh 5 2025-01 2026-08            # dry-run cả dải
bash scripts/kv-remap-backfill.sh 5 2025-01 2026-08 --apply    # chạy thật
```

Log từng tháng nằm ở `/tmp/kv-remap/kv<id>-<YYYY-MM>.log`. Các mục dưới đây mô tả lệnh gốc mà
script gọi, dùng khi cần chạy tay một khoảng đặc biệt.

### Bước 1 — Dry-run lấy danh sách sale channel chưa khai báo

Chạy từng tháng để output còn đọc được và tránh treo API:

```bash
python manage.py import_kiotviet_invoices \
  --channel-id 5 --branch-ids 30965,1000000139,1000000477 \
  --check-channel-match --dry-run \
  --only-current-channel-ids 22,23,29 \
  --empty-sale-channel-to 26 \
  --from "2026-08-01 00:00:00" --to "2026-08-31 23:59:59"
```

Đọc block JSON cuối cùng: `[{"sale_channel_id": "...", "invoices": N}, ...]`.

### Bước 2 — Khai báo kênh/mapping còn thiếu

Với mỗi `sale_channel_id` trong danh sách bước 1: đối chiếu tên kênh bên KiotViet, rồi tạo
`Channel` (nếu là shop mới) và `ChannelKiotvietMapping` tương ứng. **Đây là bước chặn dòng chảy** —
làm xong thì đơn mới tự vào đúng kênh, không cần backfill lần sau. Ưu tiên xử lý trước nhóm
TikTok đang chảy vào kênh 23 tháng 7–8/2026.

### Bước 3 — Chạy thật, lùi dần theo tháng

Bỏ `--dry-run`, chạy từ tháng gần nhất lùi về `2025-01`. Sau mỗi tháng đối chiếu:

```sql
SELECT channel_id, count(*) FROM orders
WHERE channel_id IN (22,23,29)
  AND coalesce(kiotviet_purchase_date, created_at) >= '2025-01-01'
GROUP BY 1;
```

Lặp lại bước 1→3 cho `--channel-id 4` (branch `231174,241190`) và `--channel-id 6` (branch `89084`).

### Bước 4 — Kiểm chứng sau backfill

- Số đơn còn lại ở 3 kênh rác trong vùng 2025→nay phải ≈ 0, phần dư phải giải thích được bằng
  `unmapped_sale_channel` chưa khai báo xong.
- Báo cáo tổng và báo cáo theo kênh **sẽ đổi số** trong vùng 2025→nay: các kênh sàn/Facebook tăng,
  kênh rác về gần 0. Báo trước cho phòng kinh doanh, nếu không sẽ bị hiểu là số nhảy bất thường.
- 2020–2024 giữ nguyên theo quyết định 1 — khi so sánh cùng kỳ nhiều năm vẫn còn vênh.

## 5. Rủi ro đã kiểm và cách chặn

| Rủi ro | Kết quả kiểm | Cách chặn |
|---|---|---|
| Đếm đôi do đơn mirror giữa 2 KiotViet | 3.257 đơn HD-số 2026 có mã vận đơn, chỉ **31** trùng mã với kênh khác → không phải mirror đại trà | Không cần xử lý riêng |
| Ghi đè đơn nhân viên đã sửa tay | Lệnh gốc sửa mọi đơn lệch kênh | `--only-current-channel-ids 22,23,29` |
| Chôn doanh thu shop chưa khai báo vào "Khác" | Lệnh gốc gộp chung hai ca | Tách `unmapped_sale_channel`, chỉ liệt kê |
| Cross-channel ghi đè từ KV cũ | Đã có sẵn nhánh `SKIP cross-channel` khi `kv_channel.old` | Giữ nguyên |

## 6. Tiến độ

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Điều tra gốc rễ, đo prod, chốt 4 quyết định nghiệp vụ | ✅ Xong 2026-08-23 |
| S2 | Bổ sung 2 option + tách summary + 5 test | ✅ Xong 2026-08-23 — MR !1342 đã merge; 19/19 test xanh, ruff sạch, import-linter kept |
| S2b | `scripts/kv-remap-backfill.sh` — chạy cả dải tháng bằng 1 lệnh | ✅ Xong 2026-08-23 |
| S3 | Dry-run trên prod, lập danh sách `sale_channel_id` chưa khai báo | ⏳ Chờ merge + deploy |
| S4 | Khai báo kênh/mapping còn thiếu (chặn dòng chảy mới) | ⏳ Chờ S3 |
| S5 | Backfill thật 2025-01 → nay cho kv 5, 4, 6 | ⏳ Chờ S4 |
| S6 | Đối chiếu số, thông báo phòng kinh doanh | ⏳ Chờ S5 |

### Việc chưa làm

- 450.869 đơn 2020–2024 vẫn nằm ở kênh 23 (quyết định 1) — nếu sau này cần báo cáo lịch sử theo
  kênh thì chạy lại runbook với khoảng thời gian rộng hơn, chi phí ~4.500 request KV API.
- 35.239 đơn Lazada chưa có kênh nội bộ (quyết định 2).
- Chưa rà tại sao 231 đơn TikTok tháng 7–8/2026 rơi vào KV Old (`30965`, `staff_id=22`) trong khi
  kênh TikTok `DELAX MASSAGER` (id 40) nằm ở KV New — bước 1 sẽ trả lời bằng `sale_channel_id` thật.
