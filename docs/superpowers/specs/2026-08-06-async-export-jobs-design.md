# Thiết kế: Xuất file chạy nền (async export jobs)

- Ngày: 2026-08-06
- Nhánh: `feat/async-export-jobs`
- Trạng thái: Chờ triển khai

## 1. Bối cảnh

Toàn bộ 9 endpoint xuất file của hệ thống đang chạy **đồng bộ trong request cycle** của
`desi-web`. Người dùng bấm "Xuất Excel" → gunicorn giữ nguyên slot HTTP cho tới khi
workbook dựng xong và toàn bộ byte được trả về.

| Endpoint | Trần dòng | Cách dựng file | Trong phạm vi |
|---|---|---|---|
| `POST /api/orders/bulk-export-excel` (4 trang đơn) | 50.000 | openpyxl thường, giữ toàn bộ trong RAM | ✅ |
| `GET /api/inventory/stock/export` | 20.000 | in-memory | ✅ |
| `GET /api/inventory/dispatch-history/export` | 5.000 | in-memory + query pending scan | ✅ |
| `GET /api/inventory/return-history/export` | 5.000 | in-memory + query pending scan | ✅ |
| `GET /api/warranty/claims/export` | **không có** | in-memory | ✅ |
| `GET /api/warranty-codes/batches/<id>/export` | không có | `write_only` + `.iterator()` | ✅ |
| `GET /api/audit-logs/export/` | 10.000 | `StreamingHttpResponse` CSV | ✅ |
| `GET /api/hr/attendance/export` | theo tháng | in-memory, 2 sheet | ❌ ngoài phạm vi |
| `GET /api/hr/payroll/export` | theo tháng | in-memory, có công thức Excel | ❌ ngoài phạm vi |

7 luồng đầu chuyển sang chạy nền; 2 luồng HR giữ nguyên đồng bộ (xem mục 2).

### 1.1 Ba nguyên nhân của timeout và nghẽn backend

**a) nginx `/api/` không đặt `proxy_read_timeout`** → nhận mặc định **60s**
(`nginx/customer.delax.vn.conf.prod-template`, location `/api/`). Export vượt 60s bị
cắt thành 504; FE hiện thông báo chung "Không thể xuất file". Nguy hiểm hơn: backend
**không biết client đã rời đi** nên vẫn dựng xong workbook rồi mới nhả slot — tốn CPU
cho một file không ai nhận.

**b) `WEB_HTTP_CONCURRENCY = 8` mỗi worker** (`config/settings_parts/base.py:168`,
dựng sau sự cố 2026-08-03). Toàn hệ thống chỉ có **24 slot HTTP** (8 × 3 worker), chờ
quá **15s** là **503**. Một export 60s giam trọn 1/8 slot của worker đó suốt một phút;
8 lượt xuất trúng cùng worker là mọi request khác của worker đó xếp hàng rồi 503.

**c) CPU và RAM của `desi-web`**: container chỉ có **1.5 CPU / 1200MB**. openpyxl là
CPU-bound thuần, chạy trong thread của chính process đang phục vụ WebSocket → GIL bị
giữ, realtime của cả phòng khựng theo. Về RAM, 50.000 đơn × ~40 cột ≈ 2 triệu cell
object — sát trần, mà OOM-kill `desi-web` là đứt WebSocket của toàn bộ người dùng.

Cộng thêm: không có throttle cho export (chỉ endpoint in ấn có), không huỷ được, không
có tiến độ, thất bại là mất trắng — người dùng bấm lại và nhân đôi tải.

## 2. Mục tiêu

1. `desi-web` **không bao giờ dựng workbook nữa**. Slot HTTP chỉ bị giữ khi validate
   (<200ms) và khi copy byte file đã sẵn sàng.
2. Không còn 504 khi xuất lâu: người dùng nhận phản hồi tức thì, file về sau.
3. RAM khi dựng file phẳng theo số dòng, không tuyến tính.
4. Một khuôn duy nhất cho cả 7 luồng; thêm báo cáo mới về sau chỉ cần viết hàm sinh dòng.
5. UX rõ ràng: panel dock hiện tiến trình, tải xuống, thử lại.

### Ngoài phạm vi

- **Hai luồng HR** (`attendance/export`, `payroll/export`) giữ nguyên đồng bộ. Chúng là
  ca nhẹ nhất trong danh sách: dữ liệu bó theo tháng, vài trăm dòng, số người dùng đếm
  trên đầu ngón tay, và `payroll` có công thức Excel chủ đích khiến nó không dùng chung
  writer với các luồng còn lại. Hệ quả phải chấp nhận: chúng vẫn giữ slot HTTP của
  `desi-web` khi chạy, nên **`proxy_read_timeout` cho `/api/` ở mục 6 là bắt buộc**,
  không còn là dọn dẹp tuỳ chọn. Nếu sau này bảng lương phình theo quy mô nhân sự
  (50 → 150 người), chuyển nốt chúng vào nền tảng này chỉ là viết thêm một
  `ExportDefinition`.
- Không đụng luồng **in ấn** (hóa đơn / phiếu giao) — bản chất khác (round-trip KiotViet),
  đã có `PRINT_MAX_IDS` + `OrderPrintRateThrottle`.
- Không làm luồng **import** file. Panel được đặt tên trung tính nhưng chỉ phục vụ export.
- Không xuất định dạng khác ngoài .xlsx/.csv đang có.

## 3. Quyết định kiến trúc

**Đã chốt (2026-08-06):**

| Quyết định | Lựa chọn |
|---|---|
| UX | Xuất nền + panel dock hiển thị tiến trình, tải xuống sau |
| Phạm vi | 7 luồng trên một nền tảng chung; **2 luồng HR để ngoài** |
| Lưu trữ | MinIO, TTL 24h, **chỉ chính chủ tải được** |
| Worker | Container **riêng** `worker-export` (RAM còn ngân sách) |
| Trần dòng đơn hàng | **Giữ 50.000** (không nới) |
| Đường đồng bộ cũ | **Xoá hẳn** ở sprint 2, không chạy song song |

### Các phương án đã cân nhắc và loại

- **Streaming response (giữ "bấm là tải")**: hết OOM và hết 504 vì byte chảy liên tục,
  chi phí triển khai thấp. Loại vì **vẫn giữ slot HTTP và CPU của `desi-web`** —
  nguyên nhân (b) và (c) còn nguyên, và vẫn không huỷ/retry được.
- **Lai (nhẹ tải thẳng, nặng đẩy nền)**: giữ UX quen thuộc cho phần lớn ca. Loại vì
  phải nuôi hai code path cho cùng một nghiệp vụ — chi phí bảo trì dài hạn lớn hơn phần
  UX tiết kiệm được.
- **Dùng lại queue `slow`**: rẻ hơn một container. Loại vì queue đó đang ôm job Gemini
  (`soft_time_limit` 300–480s) và precompute báo cáo (`time_limit` 900s) với
  concurrency 2 — dùng chung là đổi timeout lấy độ trễ xếp hàng sau job LLM 8 phút.

## 4. Thiết kế backend — app `apps/exports`

Clean architecture theo đúng khuôn các app hiện có:

```
apps/exports/
  models.py                         # ExportJob
  application/
    registry.py                     # ExportDefinition + register_export
    services/export_job_service.py  # tạo/dedupe/huỷ/hết hạn
    writers.py                      # StreamingXlsxWriter, CsvWriter
  infrastructure/
    export_storage.py               # MinIO (khuôn document_storage.py)
    tasks.py                        # run_export, purge_expired_exports
  transport/
    views.py, serializers.py
  definitions/                      # 8 ExportDefinition, mỗi luồng một file
  urls.py, tests/
```

### 4.1 Model `ExportJob`

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `id` | UUID pk | không dùng số tuần tự — id lộ ra FE |
| `user` | FK User, `on_delete=CASCADE` | chủ sở hữu; là cơ sở phân quyền tải |
| `kind` | varchar(50) | khoá trong registry |
| `params` | JSONB | bộ lọc/cột đã validate, **đã chuẩn hoá** |
| `params_hash` | varchar(64) | sha256 của `(kind, params chuẩn hoá)` — để dedupe |
| `status` | varchar(16) | PENDING / RUNNING / SUCCESS / FAILED / CANCELLED / EXPIRED |
| `total_rows` | int null | ước lượng lúc tạo (`estimate`) |
| `progress_rows` | int default 0 | worker cập nhật để panel hiện tiến độ thật |
| `file_key`, `file_name`, `file_size` | varchar / bigint | thông tin object MinIO |
| `error_message` | text | lý do FAILED, hiển thị thẳng cho người dùng |
| `celery_task_id` | varchar(64) | để revoke khi huỷ |
| `created_at`, `started_at`, `finished_at`, `expires_at` | datetime | |

Index: `(user, -created_at)` phục vụ panel; `(status, expires_at)` phục vụ job dọn rác;
`(user, kind, params_hash, status)` phục vụ dedupe.

### 4.2 Registry

```python
class ExportDefinition(ABC):
    kind: str
    file_ext: str = "xlsx"
    max_rows: int | None = None

    def validate(self, user, raw_params: dict) -> dict: ...   # WEB — chuẩn hoá params
    def check_permission(self, user, params: dict) -> None: ...  # WEB — raise PermissionDenied
    def estimate(self, params: dict) -> int | None: ...       # WEB — count(), chặn trần
    def file_name(self, params: dict) -> str: ...             # WEB
    def write(self, params: dict, writer, progress) -> None: ...  # WORKER
```

Điểm mấu chốt: `validate` / `check_permission` / `estimate` chạy **đồng bộ trong
request**. Nhờ đó lỗi thiếu quyền, thiếu tham số, vượt trần vẫn trả 400/403 **tức thì**
như hiện nay — không đẩy người dùng vào cảnh chờ hai phút rồi mới biết mình không có
quyền. Chỉ phần dựng file (đắt) đi xuống worker.

`write` nhận một `writer` trừu tượng nên định nghĩa export không biết gì về openpyxl:

- `StreamingXlsxWriter` — bọc `openpyxl.Workbook(write_only=True)`, ghi thẳng ra file
  tạm trên đĩa. Đây là khuôn đã chứng minh ở `warranty_codes/export_service.py`. Không
  thêm dependency mới (không dùng xlsxwriter).
- `CsvWriter` — cho audit logs, giữ định dạng CSV hiện tại.

Chỉ cần hai writer: **cả 7 luồng trong phạm vi đều là file một sheet, không công thức**
(đã kiểm: `flow_history_excel`, `claim_excel`, `stock_excel`, `export_excel` đều
`Workbook()` + một `ws`). Dòng cảnh báo cắt bớt và dòng footer tổng của lịch sử
xuất/nhập kho vẫn ghi được bằng `write_only` vì cả hai đều biết trước khi duyệt dòng
(`data["total"]` có sẵn từ query đếm).

Mọi writer đi qua `apps/common/excel_utils` để chống formula-injection. Bổ sung
`write_only_row(ws, values)` vào module đó — hiện `_sanitized_sku_cell` đang nằm riêng
trong `warranty_codes`, sẽ được thay bằng helper chung.

### 4.3 API

Prefix `/api/exports`:

| Method | Path | Hành vi |
|---|---|---|
| POST | `/api/exports` | `{kind, params}` → validate + permission + estimate + trần → tạo job, `apply_async` → **202** `{id, status, total_rows, file_name}` |
| GET | `/api/exports?active=1&limit=20` | job **của chính user**, mới nhất trước |
| GET | `/api/exports/{id}` | trạng thái một job |
| GET | `/api/exports/{id}/download` | stream file từ MinIO |
| DELETE | `/api/exports/{id}` | huỷ: PENDING → CANCELLED + revoke; RUNNING → đặt cờ, worker tự dừng ở mốc kiểm tra kế tiếp |

**Dedupe**: đã tồn tại job PENDING/RUNNING cùng `(user, kind, params_hash)` → trả lại
job cũ kèm 200 thay vì tạo mới. Đây là hàng rào chống double-click; hiện tại mỗi lần
bấm là một lượt dựng workbook độc lập.

**Throttle**: `ExportCreateThrottle` (`SimpleRateThrottle`, scope `export_create`, key
theo user, ~30 lượt/giờ) — theo khuôn `OrderPrintRateThrottle`. Export hiện **không có**
throttle nào.

**Tải xuống**: `FileResponse` stream theo chunk 64KB từ `boto3` `obj["Body"]`, **không**
`get_object_bytes` (hàm hiện có nạp toàn bộ vào RAM). Chi phí là I/O copy, không phải
CPU — giữ slot vài trăm ms thay vì một phút. Không mở MinIO ra internet, không cần đổi
nginx cho phần này.

### 4.4 Task và worker

`apps.exports.infrastructure.tasks.run_export`, queue `exports`:

- `soft_time_limit=600, time_limit=660` — **bắt buộc override** vì guard rail toàn cục
  đang là `CELERY_TASK_TIME_LIMIT = 30`.
- Bắt `SoftTimeLimitExceeded` → `status=FAILED`, `error_message` nói rõ "Quá thời gian
  cho phép, vui lòng thu hẹp bộ lọc" — không treo im lặng.
- `acks_late=False`, **không retry tự động**: một job export chết giữa chừng mà chạy lại
  nguyên vẹn là nhân đôi tải. Người dùng bấm "Thử lại" ở panel (tạo job mới) là đủ.
- Cập nhật `progress_rows` mỗi 1.000 dòng bằng `queryset.update()` (không `save()` cả
  bản ghi), đồng thời kiểm tra cờ huỷ.
- Nguồn dữ liệu luôn là `.iterator(chunk_size=2000)`.

Service `worker-export` trong compose: queue `exports`, concurrency 2, `mem_limit 768m`,
`cpus 1.0`. `CELERY_WORKER_MAX_MEMORY_PER_CHILD` theo đúng công thức đã dùng cho
`worker-slow`: `(768 - 180) / 2 * 0.85 ≈ 250000` KB.

`purge_expired_exports` chạy beat mỗi giờ: xoá object MinIO của job quá `expires_at`,
đặt `status=EXPIRED`, giữ lại bản ghi làm vết audit.

### 4.5 Thông báo tới FE

Đường chính là WebSocket: publish event `export.updated` `{id, status, progress_rows,
total_rows}` tới group `user-{id}`, theo khuôn `order_event_publisher` + pubsub bridge
đang có.

Đường phụ là polling `GET /api/exports?active=1` mỗi 3s, **chỉ bật khi có job active**.
Không dùng WS-only vì handshake có semaphore riêng, người dùng hay F5, và tab mở lại
vẫn phải thấy đúng trạng thái.

### 4.6 Bảo mật

- Tải xuống chỉ cho `job.user_id == request.user.id`. Không nới cho đồng nghiệp cùng
  section: file xuất mang thông tin cá nhân khách hàng (họ tên, số điện thoại, địa chỉ)
  ở quy mô hàng chục nghìn dòng — một link chia sẻ được là một lần rò cả tệp khách hàng.
- Ghi audit log cho **cả lượt tạo và lượt tải**. Hiện tại không có vết ai đã mang danh
  sách khách hàng về máy.
- Quyền tạo job dùng lại nguyên hệ RBAC hiện có (`{section}:export`, quyền của trang kho
  / bảo hành / nhật ký) — không thêm permission code mới, không cần migration RBAC.
- `params` được **chuẩn hoá và ghi lại** ở thời điểm tạo job, worker không đọc lại
  request. Toàn bộ logic scope hiện có (`_section_filter_overrides`,
  `_apply_user_channel_scope`) chuyển nguyên vẹn vào `OrdersExport.validate`.
- Key MinIO dạng `exports/{user_id}/{job_id}/{file_name}` — không đoán được, không lộ
  bộ lọc.

## 5. Thiết kế frontend

### 5.1 `ExportJobsProvider`

Mount một lần trong `App.tsx` cạnh `Toaster`. Trạng thái lấy từ react-query
(`useQuery(['exports','active'])`) với `refetchInterval: 3000` **chỉ bật khi còn job
active**; listener WS `export.updated` gọi `invalidateQueries` để cập nhật tức thì.

Expose `startExport(kind, params)` → POST, thêm job vào danh sách, mở dock.

### 5.2 `ExportDock`

Panel cố định góc phải dưới, header xanh theo tông của repo, ba nút: thu gọn (chevron),
phóng to, đóng. Mỗi dòng là một job:

| Trạng thái | Hiển thị |
|---|---|
| PENDING/RUNNING | icon file + tên file + spinner `Đang xử lý xuất file… (12.000/50.000 dòng)` |
| SUCCESS | dấu ✓ + link `Nhấn vào đây để tải xuống` |
| FAILED | dấu ✗ + `Thất bại: <error_message>` + nút **Thử lại** |
| EXPIRED | `File đã hết hạn` + nút **Xuất lại** |

Đóng panel **không huỷ job**; icon ở `TopNav` mang badge số job đang chạy để mở lại.
Job nằm ở server nên F5, đổi tab hay đổi máy vẫn thấy đúng.

### 5.3 Chuyển đổi các nút hiện có

Mọi nút Xuất Excel gọi `startExport(kind, params)`. Xoá `lib/api/xlsx.ts`
(`downloadXlsxExport`) và nhánh tải blob trong `useOrdersTableData.downloadFileFromApi`.
Modal chọn cột của trang đơn giữ nguyên — chỉ đổi hành động của nút xác nhận.

## 6. Hạ tầng

**Biến môi trường mới** (phải cập nhật cả `docker-compose.prod.yml`,
`docker-compose.staging.yml`, `docker-compose.local.yml` và `.env.example`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `CELERY_EXPORTS_QUEUE` | `exports` | tên queue |
| `EXPORT_WORKER_CONCURRENCY` | `2` | concurrency worker-export |
| `EXPORT_WORKER_MAX_MEMORY_PER_CHILD` | `250000` | KB, theo công thức mục 4.4 |
| `MINIO_BUCKET_EXPORTS` | `desi-exports` | bucket riêng, tách khỏi documents |
| `EXPORT_FILE_TTL_HOURS` | `24` | hạn giữ file |
| `EXPORT_SOFT_TIME_LIMIT` | `600` | giây |

**nginx**: thêm `proxy_read_timeout 300s` cho location `/api/`. **Bắt buộc, không phải
dọn dẹp tuỳ chọn**: hai luồng HR nằm ngoài phạm vi vẫn chạy đồng bộ, và mặc định 60s
hiện tại cũng là quả mìn cho mọi endpoint nặng khác (báo cáo, đối soát).

**Monitoring**: metric `export_job_duration_seconds` (histogram theo `kind`),
`export_jobs_total{kind,status}`; alert khi độ dài queue `exports` vượt ngưỡng hoặc tỉ
lệ FAILED tăng. Hệ Prometheus + Alertmanager + Lark adapter đã sẵn sàng.

## 7. Xử lý lỗi

| Tình huống | Hành vi |
|---|---|
| Thiếu quyền / params sai / vượt trần | 400/403 **ngay lúc POST**, không tạo job |
| Worker chết giữa chừng | job kẹt RUNNING → `purge_expired_exports` đánh FAILED nếu `started_at` quá `soft_time_limit × 2` |
| Vượt `soft_time_limit` | FAILED + thông báo thu hẹp bộ lọc |
| MinIO không ghi được | FAILED + `error_message` kỹ thuật; Sentry nhận exception |
| Người dùng bấm lại khi đang chạy | dedupe trả về job cũ |
| Tải file đã hết hạn | 410 Gone + panel hiện nút **Xuất lại** |

## 8. Chiến lược test

Theo cấu trúc test hệ thống (không rải test lẻ theo từng chức năng):

- `apps/exports/tests/test_export_api.py` — quyền, trần, dedupe, throttle, phân quyền
  tải xuống (user A không tải được file của user B), 410 khi hết hạn.
- `apps/exports/tests/test_export_task.py` — chạy `run_export` với storage mock: file
  được ghi, `progress_rows` tăng, huỷ giữa chừng dừng đúng, quá hạn thành FAILED.
- `apps/exports/tests/test_definitions.py` — **parity**: với cùng bộ lọc, tập dòng của
  mỗi `ExportDefinition` khớp tập của list endpoint tương ứng. Test parity theo section
  của trang đơn (`BulkExportExcelSectionFilterTests`) được **chuyển sang đây, không mất**.
- `apps/exports/tests/test_purge.py` — dọn file hết hạn, job kẹt RUNNING.
- FE: `ExportDock` (4 trạng thái + thu gọn/đóng), `ExportJobsProvider` (poll chỉ bật
  khi có job active), và test cho từng nút Xuất đã chuyển sang `startExport`.

Test cũ của 7 endpoint export đồng bộ bị xoá cùng endpoint ở sprint 2 — nội dung nghiệp
vụ của chúng phải được đưa vào `test_definitions.py` **trước khi** xoá.
`test_payroll_export.py` và `test_attendance_export.py` **giữ nguyên** (hai luồng HR
không đổi).

## 9. Kế hoạch triển khai

### Sprint 1 — Nền tảng + luồng đơn hàng

- [ ] App `apps/exports`: model + migration, registry, writers, storage, service
- [ ] Task `run_export` + `purge_expired_exports`, route queue `exports`
- [ ] API `/api/exports` (5 endpoint) + throttle + dedupe
- [ ] Compose: service `worker-export` + env mới (prod/staging/local) + `.env.example`
- [ ] FE: `ExportJobsProvider` + `ExportDock` + icon badge ở `TopNav`
- [ ] `OrdersExport` — chuyển toàn bộ logic scope/section, nối nút xuất của 4 trang đơn
- [ ] Test backend + FE theo mục 8

**Tiêu chí hoàn thành**: xuất 50.000 đơn không giữ slot HTTP nào của `desi-web`; RAM
`worker-export` phẳng trong suốt job; panel hiển thị đúng 4 trạng thái.

### Sprint 2 — Migrate 6 luồng còn lại và xoá đường cũ

- [ ] `StockExport`, `DispatchHistoryExport`, `ReturnHistoryExport`
- [ ] `WarrantyClaimsExport` (**bổ sung trần dòng** — hiện chưa có)
- [ ] `WarrantyCodeBatchExport`
- [ ] `AuditLogsExport` (CSV)
- [ ] **Xoá hẳn** 7 endpoint export đồng bộ + `lib/api/xlsx.ts` + test tương ứng
- [ ] nginx: `proxy_read_timeout 300s` cho `/api/`

**Tiêu chí hoàn thành**: chỉ còn 2 endpoint export đồng bộ là `hr/attendance/export` và
`hr/payroll/export` (chủ đích, đã ghi ở mục 2); mọi luồng khác đi qua `/api/exports`.

### Sprint 3 — Quan sát và làm cứng

- [ ] Metric Prometheus + alert rule + dashboard Grafana
- [ ] Audit log cho lượt tạo và lượt tải (vết mang danh sách khách hàng về máy)
- [ ] Job dọn file hết hạn chạy thật trên prod, xác nhận dung lượng MinIO ổn định
- [ ] Runbook vận hành: đọc trạng thái job, xử lý job kẹt, tăng concurrency

**Tiêu chí hoàn thành**: alert bắn được khi queue tồn đọng; runbook đã kiểm chứng.

## 10. Ghi chú sau mỗi sprint

_(cập nhật từ ledger thực thi `.superpowers/sdd/2026-08-06-async-export-jobs/progress.md`,
Task 1–22, nhánh `feat/async-export-jobs`, MR
https://gitlab.com/desi3512912/desi-order/-/merge_requests/1160)_

### Sprint 1 — Nền tảng + luồng đơn hàng (Task 1–14)

**Đã làm:** toàn bộ hạng mục trong kế hoạch — model `ExportJob` + migration, registry +
2 writer (`StreamingXlsxWriter`, `CsvWriter`), storage MinIO, service dedupe/huỷ/hết
hạn, task `run_export` + `purge_expired_exports`, 5 endpoint API + throttle + dedupe,
WS event `export.updated`, compose `worker-export` + env, FE `ExportJobsProvider` +
`ExportDock` + badge `TopNav`, nút xuất 4 trang đơn hàng chuyển sang `startExport()`.

**Phát sinh quan trọng nhất:** brief/kế hoạch gốc của `OrdersExport.validate` bỏ sót
`build_orders_filters` + `_section_filter_overrides` — nếu làm đúng theo brief, job sẽ
xuất theo **nguyên payload client gửi lên**, mất hẳn lớp khoá scope phía server (một
sale chỉ được thấy đơn của mình vẫn có thể xuất đơn của người khác qua job nền). Đã vá
bằng `_FiltersRequestShim` gọi lại nguyên hai hàm gốc; nghiệm thu bằng đột biến (xoá
gate → test đỏ, khôi phục → xanh). Nợ để lại: `exports_definitions.py` phải import
`_section_filter_overrides` từ tầng `transport` — business phụ thuộc ngược vào
transport, dọn ở Task 18 (xem Sprint 2).

**Sự kiện giữa sprint:** merge `origin/master` vào nhánh (master đi trước 35 commit) —
3 conflict ở `celery.py` beat schedule + 2 anchor env compose, đều là "hai bên cùng
thêm mục mới", giữ cả hai. Verify sau merge: 3 file compose parse OK, backend 146
passed, FE 5 đỏ đúng baseline (`realtime-upsert-filters`, có sẵn trước sprint).

**Chưa làm / nợ kỹ thuật deferred:**
- `cancel()` job đang `RUNNING` chưa dùng `revoke(terminate=True)` — chỉ dừng ở mốc
  kiểm tra huỷ kế tiếp (Task 6).
- `CsvWriter` không trung hoà CSV-injection (`=`, `+`, `-`, `@`) — lỗ hổng có sẵn từ
  trước, ảnh hưởng `AuditLogsExport` ở Sprint 2 (Task 3, nhắc lại ở Task 16).
- Chưa có test race dedupe bằng 2 connection/thread song song thật (Task 5) — mới
  chứng minh constraint tồn tại.
- `worker-export` **chưa từng chạy thật** (`docker compose up`) ở sprint này — Task 10
  chỉ xác nhận config parse đúng.

### Sprint 2 — Migrate 6 luồng còn lại và xoá đường cũ (Task 15–19)

**Đã làm:** `StockExport`, `DispatchHistoryExport`, `ReturnHistoryExport`,
`WarrantyClaimsExport` (bổ sung trần dòng — trước đó chưa có), `WarrantyCodeBatchExport`,
`AuditLogsExport` (CSV); xoá hẳn 7 endpoint export đồng bộ + `lib/api/xlsx.ts` + test
tương ứng (commit `25dfc1b5`); thêm `proxy_read_timeout 300s` cho `/api/` ở nginx
(commit `df8e798a`).

**Phát sinh quan trọng:**
- **TOCTOU giữa `estimate()` và `write()`:** `estimate()` chặn trần lúc **tạo** job
  (đồng bộ, trong request), nhưng `write()` chạy ở worker **sau đó** không re-check.
  `StockExport.write` ban đầu dùng `list()` không `LIMIT` — nếu kho phình giữa lúc tạo
  job và lúc worker chạy, sẽ kéo hết dữ liệu mới vào RAM (bản đồng bộ cũ không có khe
  hở này vì check+fetch cùng một request). Đã vá: `write()` tự áp lại trần + ghi dòng
  cảnh báo cắt bớt (nghiệm thu bằng đột biến: xoá LIMIT → test đỏ, khôi phục → xanh).
  Cùng lớp vấn đề áp dụng cho `OrdersExport` — không gây OOM (dùng `.iterator()`
  streaming) nhưng số dòng file có thể vượt trần 50.000 đã hứa với người dùng nếu dữ
  liệu phình sau khi tạo job; **chưa được vá tương đương ở Sprint 2**, ghi lại làm nợ.
- Task 16 (Warranty/Audit definitions): review phát hiện message `PermissionDenied` bị
  **bịa** khác với bản gốc dù implementer báo "không phát hiện sai lệch nào" — nhắc lại
  tầm quan trọng của việc đối chiếu từng dòng khi "bê nguyên logic" thay vì viết lại
  theo trí nhớ (nguyên tắc đã ghi ở Self-Review của kế hoạch).
- Nợ gộp từ Task 16 (`ids=[int(v) for v in ...]` không bọc try/except → 500 thay vì
  400 nếu client gửi `ids` sai kiểu, ở cả `OrdersExport` lẫn `WarrantyClaimsExport`):
  **đã sửa trong Task 18**, commit `25dfc1b5` — cả hai `exports_definitions.py`
  (orders, warranty) đều bọc `try/except (TypeError, ValueError)` raise
  `ValidationError({"detail": "Danh sách ids không hợp lệ."})` (xác nhận lại trên code
  hiện tại, không chỉ chép từ ledger).
- Task 18 xác nhận qua đối chiếu 1:1: `BulkExportExcelSectionFilterTests` (6 case cũ)
  chuyển đủ sang test mới, không sót case nào. Hồi quy DUY NHẤT khi xoá 7 endpoint:
  `test_batch_api` còn assert `/export` → 404, đã sửa cùng commit.
- Task 17 xác minh độc lập: hai trường `search` + khoảng ngày của `audit_logs` export
  **chưa từng lọc được** kể cả ở view đồng bộ cũ — không phải hồi quy do sprint này.

**Chưa làm / nợ kỹ thuật deferred:**
- `TOCTOU` của `OrdersExport.write()` (nêu trên) — chưa tự áp lại trần 50.000 dòng.
- 4 hàm Excel mồ côi còn lại trong `flow_history_excel.py` + 11 test tương ứng chưa dọn
  (khoá quy tắc "gross khi net=0" chưa có bản tương đương ở tầng definition mới).
- `list_all_claims` còn mồ côi; `build_orders_filters` vẫn còn coupling
  business→transport (nợ nêu ở Sprint 1, chưa dọn xong ở Sprint 2).
- Nợ **mới, không thuộc phạm vi Task 18**: `test_celery_routes` đỏ sẵn trên `master` vì
  `purge_old_scan_logs` (task app khác) chưa khai route — phát hiện khi chạy full suite,
  không phải hồi quy của sprint export.

### Sprint 3 — Quan sát và làm cứng (Task 20–22)

**Đã làm:**
- Task 20: metric `export_job_duration_seconds`, `export_jobs_total{kind,status}` +
  2 alert Prometheus (`ExportJobsFailing`, `ExportQueueBacklog`, xem
  `docs/runbooks/export-jobs.md` mục 7). Metric queue thật đã xác nhận đúng tên:
  `celery_queue_length{queue_name="exports"}` (celery-exporter, không phải suy đoán).
- Task 21: audit log cho lượt tạo (`export.create`) và lượt tải (`export.download`) —
  `record_export_audit()` trong `apps/exports/transport/views.py`, đi qua
  `apps.dispatch.write_audit` (đường batch hiện có của hệ audit), best-effort (tự bọc
  try/except + log, không làm hỏng lượt tải/tạo thật nếu ghi audit lỗi — có test riêng
  chứng minh bằng cách ép `write_audit` ném lỗi). 79 test `apps/exports` + `apps/audit`
  pass, ruff sạch, import-linter KEPT (1 kept, 0 broken).
- Task 22 (mục này): runbook vận hành `docs/runbooks/export-jobs.md` + cập nhật mục 10.

**Chưa làm / nợ để lại cho người vận hành:**
- 2 histogram (`export_job_duration_seconds`, `export_job_rows`) ở Task 20 chưa có
  assertion giá trị riêng trong test — chỉ assert được gọi, chưa khoá số đo cụ thể.
- TOCTOU `OrdersExport.write()` (nêu ở Sprint 2) và các nợ Excel mồ côi vẫn còn nguyên,
  không nằm trong phạm vi Sprint 3.
- Runbook (Task 22) viết dựa trên đọc code + xác minh cú pháp lệnh qua stack dev/test
  cục bộ (Postgres migrate thật, `redis-cli`/`mc` chạy thật); **chưa có lần chạy thật
  trên staging/prod** cho kịch bản "job kẹt RUNNING" hay "MinIO đầy" — tiêu chí hoàn
  thành gốc của Sprint 3 ("runbook đã kiểm chứng") mới đạt ở mức cú pháp, chưa ở mức
  diễn tập sự cố thật.

### Đợt sửa trước-merge cuối cùng (final review, sau Task 22)

Final review toàn nhánh phát hiện 4 lỗi chặn merge nằm NGOÀI `apps/exports` (không
task nào trong 22 task phụ trách, nên không ai review): `worker-export` thiếu trong
`scripts/deploy-prod.sh` (queue không có consumer ở lần deploy đầu) và trong
`scripts/staging-refresh.sh` (cửa sổ rò PII prod ra staging trước neutralize);
`purge_expired_exports` không override time limit + nuốt `SoftTimeLimitExceeded` +
chỉ cứu RUNNING + tự route vào chính queue nó phải cứu; `OrdersExport.validate` còn
2 đường trả 500 thay vì 400. Đã sửa cả 4, xem
`.superpowers/sdd/2026-08-06-async-export-jobs/final-fix-report.md`.

**Nợ mới phát sinh từ đợt sửa này:** alert `ExportJobsFailing` (Task 20) hoá ra
KHÔNG BAO GIỜ fire — `export_jobs_total` chỉ `.inc()` trong tiến trình
`desi-worker-export`, không tới được Prometheus (chỉ scrape `web:8000`) — đã xoá
alert đó (báo giả "đang giám sát" còn nguy hiểm hơn không có alert) và thêm
`ExportWorkerMissing` (`absent()` trên `celery_queue_length{queue_name="exports"}`)
để ít nhất bắt được kịch bản worker chết hẳn. Cần một đường đưa metric của tiến
trình worker (không riêng export — mọi Celery worker) tới Prometheus (mount
`PROMETHEUS_MULTIPROC_DIR` qua volume chung + `MultiProcessCollector`, hoặc scrape
riêng từng worker qua một cổng `/metrics` cục bộ) trước khi có thể khôi phục loại
alert theo trạng thái job thất bại.

### Đợt final-verify trước merge (sau final review, trước merge master)

**Nợ có chủ đích, để lại KHÔNG sửa:** `purge_expired_exports` vẫn route vào queue
`default` (`config/settings_parts/celery.py:96`) thay vì một queue riêng. Final
review nêu đây hơi nghịch với audit 2026-07-26 (queue `default` vốn được giữ sạch,
tránh task chạy lâu chiếm chỗ) và gợi ý dùng `CELERY_SLOW_QUEUE` (khuôn `worker-slow`
đã có sẵn) cũng thoả nguyên tắc "task không nên sống trong căn nhà nó phải dọn" —
`purge_expired_exports` dọn dẹp cho hệ export nhưng lại consume chung queue với các
task nghiệp vụ khác. Quyết định KHÔNG đổi ở đợt này vì task thực chạy vài mili-giây
(chỉ update vài row + gọi MinIO xoá object hết hạn), rủi ro chiếm queue `default`
thấp trong quy mô hiện tại. Phương án thay thế nếu sau này cần dọn nợ: route sang
`CELERY_SLOW_QUEUE` (ít rủi ro nhất, đã có worker nghe sẵn) hoặc tạo queue `exports`
luôn (nhất quán với `run_export`, nhưng cần đảm bảo `worker-export` không bị task dọn
dẹp này giành slot của job xuất file thật).
