# Worker failure handling: retry classification, metrics & contract tests

> Bối cảnh: sweep `kv.auto_sync_one` tạo ra hàng nghìn job fail `400 SerializationException`
> (xem fix endpoint tại nhánh `fix-kv-get-invoice-by-code`). Lỗi 4xx là cố định nhưng
> worker vẫn retry tới `max_attempts`, đốt rate-limit (12/min/channel) vào request không
> bao giờ thành công, và toàn bộ chỉ hiện ra qua log WARN — không có alert.

Tài liệu này gom 3 cải tiến nền cho hệ thống job worker (`go-services`).

## Mục tiêu

| # | Hạng mục | Vấn đề | Kết quả |
|---|----------|--------|---------|
| 1 | Alerting trên job failed | Fail âm thầm, chỉ thấy qua log | Metric `desi_jobs_failed_total{reason}` + Prometheus alert |
| 2 | Không retry lỗi 4xx | 400/404 retry vô nghĩa, tốn rate-limit | Phân loại terminal vs transient ở consumer, terminal → `dead` ngay |
| 3 | Contract test gateway | Port Python→Go thủ công dễ lệch endpoint | Test pin path + query của mọi method KiotViet gateway |

## Thiết kế

### Item 2 — Retry classification (làm trước, items khác phụ thuộc)

Nguyên tắc: **quyết định retry nằm ở tầng consumer, không rải rác từng handler.** Gateway chỉ
khai báo "lỗi này có terminal không"; consumer đọc khai báo đó để settle.

- Package chung `internal/shared/apierr`:
  - `HTTPTerminal(status int) bool` — chính sách HTTP dùng chung: 4xx là terminal **trừ**
    408 (timeout) và 429 (rate limited) là transient → vẫn retry.
  - `IsTerminal(err error) bool` — dùng `errors.As` tìm error nào trong chain implement
    interface `interface{ Terminal() bool }`.
- Các gateway error implement `Terminal() bool` (delegate về `apierr.HTTPTerminal`):
  `kiotviet.APIError`, `zalo.BridgeError`, `facebook.GraphError`.
- Consumer (`transport/consumer.go`): khi handler trả error →
  - `terminal` → `repo.SettleDead` (state=`dead`, finished ngay, **không** retry).
  - ngược lại → `repo.SettleFailed` như cũ (retry với backoff, hết attempts → `failed`).
- Query SQL mới `SettleDead` (regenerate sqlc v1.31.1). State `dead` đã có sẵn trong enum và
  đã nằm trong `CleanupOldJobs` nên không phát sinh rác.

### Item 1 — Metrics + alert

- Counter mới `desi_jobs_failed_total{queue, task_type, reason}` với
  `reason ∈ {terminal, exhausted, retry}` — phân biệt được lỗi cố định, lỗi đã hết attempts,
  và lỗi tạm sẽ retry. (Counter cũ `desi_jobs_processed_total{result=failed}` giữ nguyên,
  gộp tất cả nên không alert chính xác được.)
- Alert rule `monitoring/alerts.yml`, nạp qua `rule_files` trong `prometheus.yml`:
  - `WorkerJobsTerminalSpike` — terminal failures > 0.2/s trong 10m (warning).
  - `WorkerJobsFailureRateHigh` — tỉ lệ fail/processed > 25% trong 10m (warning).
  - `WorkerNoJobsProcessed` — không có job done nào trong 15m mà backlog > 0 (critical).

### Item 3 — Contract test

`internal/shared/gateway/kiotviet/client_test.go` mở rộng thành bảng test, mỗi method assert
`method + path + query` mà KiotViet yêu cầu, kèm comment trỏ tới file:line Django là nguồn
"hợp đồng". Khi port/ sửa gateway mà lệch path sẽ fail ngay trong CI thay vì lộ ra ở production.

## Tiến độ

- [x] Item 2 — retry classification (apierr, gateway Terminal(), SettleDead, consumer)
- [x] Item 1 — metric `desi_jobs_failed_total` + alert rules + wiring prometheus
- [x] Item 3 — contract test kiotviet gateway
- [x] Build + vet + test (container golang:1.25)

### Ghi chú sau khi làm

- State `dead` lần đầu được dùng ở luồng settle (trước đó chỉ khai báo trong enum). Job
  terminal giờ kết thúc ở `dead`, phân biệt với `failed` (hết retry).
- Chưa làm: backoff/jitter ngẫu nhiên cho transient (hiện exponential thuần). Cân nhắc sprint
  sau nếu thấy thundering-herd khi nhiều job cùng retry.
