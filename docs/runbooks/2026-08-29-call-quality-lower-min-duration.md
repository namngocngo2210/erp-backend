# Hạ ngưỡng chấm chất lượng cuộc gọi 120s → 60s

**Ngày:** 2026-08-29 · **MR:** hạ `QA_MIN_DURATION_SECONDS` + cờ `--re-score-skipped`

## 1. Vì sao

Trang Chất lượng cuộc gọi chỉ chấm 1.214 cuộc trong 30 ngày, trong khi bộ lọc
"có ghi âm" ở trang Đơn hàng ra 3.777 đơn. Đo prod 29/08:

| Bước | Số cuộc (toàn bộ) |
|---|---:|
| Tổng cuộc gọi | 11.757 |
| Có file ghi âm | 6.420 |
| Transcribe xong | 6.352 |
| **Bị bỏ qua khi chấm** | **4.505** |
| Đã chấm | 1.712 |
| Tồn đọng / lỗi | 135 / 0 |

Transcribe không phải nút thắt (0 pending, 0 failed). Thủ phạm là
`scoring._is_eligible`: **4.613/6.352 cuộc có transcript (72,6%) bị loại chỉ vì
dưới 120 giây**, trong đó **3.755 cuộc vẫn có transcript ≥ 400 ký tự** — tức có
nội dung tư vấn thật để chấm.

Luật `QA_MIN_TRANSCRIPT_CHARS=400` ở mốc 120s **không loại thêm cuộc nào** (mọi
cuộc transcript ngắn đều đã dưới 120s). Nó chỉ bắt đầu có tác dụng khi hạ ngưỡng
thời lượng — nhóm dưới 30s chỉ 22% đạt 400 ký tự. Vì vậy **giữ nguyên 400**.

Phân bố cuộc có transcript trong 30 ngày:

| Thời lượng | Số cuộc | Có transcript ≥ 400 ký tự |
|---|---:|---:|
| <30s | 839 | 181 (22%) |
| 30–59s | 1.024 | 1.000 (98%) |
| 60–89s | 1.061 | 1.061 (100%) |
| 90–119s | 686 | 686 (100%) |
| ≥120s | 1.252 | 1.252 (100%) |

Chọn **60s**: mọi cuộc ≥ 60s trong tập đo đều đạt ngưỡng ký tự nên không rước
rác vào, số cuộc chấm được ×2,4 (1.252 → 2.999 mỗi 30 ngày).

## 2. Cảnh báo trước khi deploy

- **Điểm trung bình sẽ TỤT.** Cuộc ngắn điểm thấp hơn — đo trên tập đã chấm:
  41,7 điểm ở 120–179s so với 48,5 ở ≥300s. Phải báo quản lý trước, kẻo bị đọc
  thành "chất lượng team đi xuống".
- **Chi phí Gemini tăng.** Backlog cần chấm lại ~2.900 cuộc, cộng thêm ~×2,4
  lượng cuộc mới mỗi ngày.
- Ngưỡng cũ lấy từ câu "ưu tiên những cuộc gọi trên 2p" của khung kịch bản. Đây
  là quyết định **cố ý đi ngược** hướng dẫn đó, dựa trên số đo ở trên.

## 3. Các bước

### 3.1 Kiểm tra `.env` prod TRƯỚC

Mặc định mới nằm ở cả code (`settings_parts/base.py`) lẫn compose
(`${QA_MIN_DURATION_SECONDS:-60}`). Nhưng nếu `.env` trên prod **đặt tay** biến
này thì giá trị đó thắng cả hai:

```bash
grep -n QA_MIN_DURATION_SECONDS ~/desi-order/.env || echo "chua dat -> dung mac dinh 60"
```

Có dòng `QA_MIN_DURATION_SECONDS=120` thì sửa thành `60` (hoặc xoá hẳn dòng đó).

### 3.2 Roll code

Dùng script deploy chuẩn, KHÔNG gõ tay `compose up`:

```bash
cd ~/desi-order && bash scripts/deploy-prod.sh
```

Script lo trọn: `git pull` → preflight compose render → build image `web` →
preflight migration graph trong image MỚI → `up -d` cả cụm
(`web worker beat worker-kiotviet worker-slow worker-export`). `migrate` chạy
trong `command:` của `web` lúc boot nên không cần lệnh riêng.

> ⚠️ Gõ tay `compose up -d --no-deps worker-slow web` là **bỏ sót**
> `worker-kiotviet`/`worker-export` — hai service đó cứ chạy tiếp bằng image CŨ
> vô thời hạn. Lý do đầy đủ nằm trong chính comment của `scripts/deploy-prod.sh`.

### 3.3 Xác nhận ngưỡng đã vào container

```bash
docker compose -f docker-compose.prod.yml exec worker-slow env | grep QA_MIN
```

Phải thấy `QA_MIN_DURATION_SECONDS=60`. Không thấy đúng thì DỪNG, quay lại 3.1.

### 3.4 Kéo lại nhóm bị bỏ qua

Hạ ngưỡng **không** tự chấm lại: hàng đợi chỉ lấy `PENDING`, record đã `SKIPPED`
nằm im mãi. Cờ mới chỉ kéo về đúng nhóm bị bỏ qua **và nay đã lọt ngưỡng**:

```bash
docker compose -f docker-compose.prod.yml exec worker-slow \
  python manage.py score_call_quality --re-score-skipped --limit 0
```

`--limit 0` = chỉ requeue rồi dừng, không chấm ngay. Lệnh in ra số record đã kéo
về PENDING (dự kiến ~2.900).

> ⚠️ **KHÔNG dùng `--re-score`** cho việc này: nó kéo cả nhóm `DONE` về PENDING,
> tức gọi lại Gemini cho 1.712 cuộc đã chấm mà kết quả không đổi.

### 3.5 Chấm backlog có kiểm soát

Beat chạy 1 lần/ngày với trần `QA_SCORING_MAX_MINUTES=60`, backlog ~2.900 cuộc
sẽ mất nhiều đêm. Chạy tay từng lô để theo dõi quota:

```bash
docker compose -f docker-compose.prod.yml exec worker-slow \
  python manage.py score_call_quality --max-minutes 90
```

Chạy lại lệnh này mỗi lần cho tới khi output có `"stopped_by": "empty"`. Gặp
`"stopped_by": "quota"` thì nghỉ theo `GEMINI_QUOTA_COOLDOWN_MINUTES` rồi chạy
tiếp — phần chưa chấm nằm nguyên `PENDING`, không mất gì.

### 3.6 Đối chiếu sau khi xong

```sql
SELECT COUNT(*) FILTER (WHERE score_status=2) AS da_cham,
       COUNT(*) FILTER (WHERE score_status=4) AS bo_qua,
       COUNT(*) FILTER (WHERE score_status=0 AND transcribe_status=2) AS ton_dong,
       COUNT(*) FILTER (WHERE score_status=3) AS loi
FROM callcenter_call_records;
```

Kỳ vọng `da_cham` ≈ 5.300 (từ 1.712), `bo_qua` giảm còn nhóm thật sự dưới 60s.

## 4. Quay lui

Đặt `QA_MIN_DURATION_SECONDS=120` trong `.env` prod rồi recreate worker. Điểm đã
chấm của cuộc ngắn **vẫn nằm trong báo cáo** (không có đường xoá tự động) — muốn
sạch hẳn phải xoá tay theo `duration_seconds < 120` trong
`callcenter_call_quality_scores`, cân nhắc kỹ vì mất luôn lịch sử.
