# Command drain transcribe cuộc gọi — thiết kế

Ngày: 2026-07-27
Trạng thái: đã chốt, chờ triển khai

## 1. Bối cảnh

Đo backlog prod ngày 2026-07-27:

| Chỉ số | Giá trị |
|---|---|
| `PENDING` có `recording_url` | 685 |
| trong đó đã khớp đơn | 668 (97%) |
| `FAILED` | 0 |
| tuổi file PENDING | 100% trong vòng 30 ngày, 624 trong 7 ngày |
| `DONE` 24h qua | 53 |
| RPD tiêu hôm nay | 27 / 500 |
| cờ cooldown 429 | tắt |

Công ty vừa bật billing cho Gemini nên trần quota phía Google không còn là ràng buộc.
Nút thắt nằm ở phía mình:

- `GEMINI_TRANSCRIBE_BATCH=8` — mỗi tick beat chỉ claim 8 record.
- Trong tick, các record chạy **tuần tự**, mỗi record 15–20s (tải file + gọi Gemini).
  Một tick do đó tốn 2–3 phút.
- Beat chạy `*/5` nhưng lock Redis giữ 540s, nên tick kế tiếp thường `SKIP`.

Kết quả: throughput thực ~53 record/ngày, trong khi trần lý thuyết là 96/giờ. Với
685 record tồn, cron cần ~13 ngày. Quota Google đủ sức làm trong một giờ.

## 2. Mục tiêu

1. Có đường **chạy tay** để tiêu hết hàng đợi trong một lần, không phải đợi cron.
2. Nâng cấu hình cron để backlog không tích lại như cũ.
3. Không nhân bản logic xử lý lỗi/attempt/quota đang có — cron và command phải đi
   chung một đường.

Ngoài phạm vi: bước `extract` (đang tắt bằng `GEMINI_EXTRACT_ENABLED=0`, 305 record
`DONE` đang chờ), Files API cho file > 19MB, đổi mô hình.

## 3. Thiết kế

### 3.1 Tách `process_record` trong `transcribe.py`

Thân vòng lặp của `transcribe_pending_records` được tách thành hàm dùng chung:

```python
def process_record(record: CallRecord) -> str:
    """Tải file → tiêu quota → gọi Gemini → ghi kết quả. Trả "done" | "error".

    Raise TranscribeQuotaExceeded khi chạm trần local (chưa tiêu gì) hoặc khi
    Google trả 429 (đã hoàn slot + bật cooldown, cờ ``cooldown=True``).
    """
```

`transcribe_pending_records` giữ nguyên chữ ký, hành vi và summary như cũ — chỉ đổi
phần ruột thành gọi `process_record`. Thứ tự "download trước, tiêu quota sau" và
quy tắc transient-không-đốt-attempt giữ nguyên (audit 2026-07-18).

`TranscribeQuotaExceeded` thêm thuộc tính `cooldown: bool` để phân biệt hai nguyên
nhân dừng trong log vận hành: chạm trần local vs Google trả 429.

### 3.2 Command `drain_transcribe`

```
python manage.py drain_transcribe [--concurrency N] [--limit N] [--rpm N] [--rpd N] [--dry-run]
```

- `--concurrency` (mặc định 4): số luồng gọi Gemini song song. Tải file + gọi API là
  I/O-bound nên thread ăn thật. `--concurrency 1` chạy inline, không tạo thread.
- `--limit`: trần tổng số record; bỏ trống = chạy tới khi hết `PENDING`.
- `--rpm` / `--rpd`: ghi đè trần quota **trong tiến trình này**, khỏi sửa `.env` và
  restart worker để drain một lần.
- `--dry-run`: chỉ in số lượng theo trạng thái rồi thoát.

Vòng lặp: claim một batch (`concurrency * 2` record) qua `_claim_pending_batch` sẵn
có → chạy `process_record` qua `ThreadPoolExecutor` → in tiến độ → lặp. Chạm quota
thì dừng vòng lặp. Cuối mỗi batch gọi `_requeue_batch` để mọi record còn treo
`PROCESSING` (do dừng giữa chừng hoặc thread chết) quay về `PENDING`.

Vì `_claim_pending_batch` dùng `SELECT ... FOR UPDATE SKIP LOCKED`, command chạy song
song với beat không giẫm chân nhau — mỗi bên claim tập record riêng.

Mỗi thread gọi `connections.close_all()` khi xong để không giữ connection Postgres qua
pgbouncer. Counter quota nằm ở Redis `INCR` nên đã atomic, dùng chung được giữa các
luồng.

### 3.3 Cấu hình cron sau khi drain

Trong `.env` prod:

```
GEMINI_MAX_RPM=60          # cũ 8 — trần free tier
GEMINI_TRANSCRIBE_BATCH=20 # cũ 8
```

Giữ `GEMINI_MAX_RPD=500`: lượng gọi thực tế ~50–100/ngày, 500 vẫn là chốt chặn chi phí
khi có sự cố lặp vô hạn.

## 4. Rủi ro

| Rủi ro | Xử lý |
|---|---|
| Mất SSH giữa chừng, command chết | Record treo `PROCESSING` được `_requeue_stuck_processing` trả về `PENDING` sau 30'. Khuyến nghị chạy bằng `docker exec -d` hoặc `nohup … > log &`. |
| 4 luồng tải file cùng lúc chiếm băng thông VPS | Concurrency mặc định 4, không đặt cao hơn; worker `slow` vẫn chạy song song. |
| Test thread pool trong `TestCase` không thấy dữ liệu | `TestCase` bọc transaction, thread khác dùng connection khác. Test logic dùng `--concurrency 1` (inline); riêng test thread pool dùng `TransactionTestCase`. |
| Chi phí Gemini tăng đột ngột | `--limit` cho lần chạy đầu; `GEMINI_MAX_RPD` giữ 500 làm chốt chặn. |

## 5. Kiểm thử

- `process_record` giữ nguyên hành vi: các test hiện có trong `TranscribePendingTests`
  và `TranscribeHardeningTests` phải pass không sửa.
- Command tiêu hết hàng đợi (concurrency 1).
- `--limit` dừng đúng số.
- `--dry-run` không đổi trạng thái record nào.
- Chạm quota → dừng, record chưa xử lý quay về `PENDING`, không record nào kẹt
  `PROCESSING`.
- `--rpm` override có hiệu lực trong tiến trình.
- Thread pool (`--concurrency 2`) xử lý hết record — `TransactionTestCase`.

## 6. Vận hành

```bash
# xem trước
docker exec desi-web python manage.py drain_transcribe --dry-run

# chạy nền, log ra file
docker exec -d desi-web sh -c "python manage.py drain_transcribe --concurrency 4 --rpm 60 > /tmp/drain.log 2>&1"
docker exec desi-web tail -f /tmp/drain.log
```
