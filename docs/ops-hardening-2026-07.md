# Ops hardening 2026-07 — offsite backup, giám sát ngoài, log tập trung

Ba lỗ hổng vận hành còn lại sau các đợt audit 2026-07 (không phải công nghệ mới,
là vá điểm mù):

| # | Vấn đề | Rủi ro | Giải pháp |
|---|---|---|---|
| 1 | Backup Postgres chỉ nằm local trên VPS | VPS chết = mất cả DB (130GB) lẫn backup | Bật offsite qua rclone → Backblaze B2 / Cloudflare R2 + alert riêng |
| 2 | Prometheus/Alertmanager chạy trên chính VPS nó giám sát | VPS sập → alert sập theo, không ai biết | Probe HTTP ngoài (`/api/healthz`) + heartbeat healthchecks.io (backup cron + Celery beat) |
| 3 | Log chỉ nằm trong `docker logs` từng container | Điều tra sự cố phải SSH grep tay, log mất khi container recreate | Loki + Alloy (profile monitoring), xem log trong Grafana, retention 14d |

## Những gì đã code trong branch này

### Sprint 1 — Offsite backup (code XONG, chờ thao tác trên VPS)

- `scripts/desi-backup.sh`:
  - Heartbeat healthchecks.io: `DESI_BACKUP_HEARTBEAT_URL` — bắn `/start` lúc
    bắt đầu, ping gốc khi xong, `/fail` kèm message khi lỗi. Curl lỗi không làm
    hỏng backup (`|| true`).
  - Metric mới cho node-exporter textfile: `desi_backup_offsite_configured`
    (0/1) + `desi_backup_offsite_last_success_timestamp` (chỉ ghi khi upload
    offsite thành công).
  - Cron chuyển sang source `/etc/desi-backup.env` trước khi chạy (env tách
    khỏi crontab, chmod 600).
- `monitoring/alerts.yml`: 2 rule mới —
  - `BackupOffsiteNotConfigured` (warning, nhắc dai mỗi khi backup chạy mà
    không có đích offsite);
  - `BackupOffsiteStale` (critical, offsite từng bật nhưng >26h chưa upload
    thành công — trường hợp backup local vẫn OK nên `BackupStale` im lặng).

### Sprint 2 — Giám sát ngoài (code XONG, chờ đăng ký dịch vụ + env)

- `GET /api/healthz` (`backend/apps/common/health.py`): check sâu DB
  (`SELECT 1`) + Redis (cache round-trip), trả 200/503. Không auth, không lộ
  dữ liệu, nằm trong `SKIP_PATHS` của AuditLogMiddleware (không spam
  audit_logs). Khác `/healthz` của nginx (trả 200 tĩnh tại edge — không chứng
  minh app sống).
- Task `apps.common.tasks.ops_heartbeat` + beat entry `ops-heartbeat` (300s):
  GET `OPS_HEARTBEAT_URL` — chuỗi beat → Redis broker → worker → HTTP phải
  sống toàn bộ thì ping mới tới nơi ⇒ healthchecks phát hiện cụm Celery chết.
  - `OPS_HEARTBEAT_URL` trống = no-op; đã thêm vào whitelist env compose prod.
  - Đã thêm vào `STAGING_FORBIDDEN_OUTBOUND_ENVS` (staging ping cùng URL sẽ
    che tín hiệu prod chết → guard chặn từ lúc boot).

### Sprint 3 — Log tập trung Loki + Alloy (code XONG, chờ deploy profile monitoring)

- `monitoring/loki-config.yml`: Loki single-binary, storage filesystem
  (volume `loki_data`), retention 14 ngày (compactor), schema v13/tsdb.
- `monitoring/alloy-config.alloy`: discovery.docker tail log MỌI container
  trên host qua `docker.sock` (ro), label `container`/`service`/`project`,
  push Loki.
- `docker-compose.prod.yml`: 2 service `loki` + `alloy` dưới profile
  `monitoring` (cùng lệnh bật với Prometheus/Grafana), volume `loki_data` +
  `alloy_data`, mem/cpu limit như convention.
- Grafana: datasource Loki provisioning sẵn
  (`monitoring/grafana/provisioning/datasources/loki.yml`).
- Prometheus scrape `loki:3100` + `alloy:12345` → rule `TargetDown` phủ luôn.
- `.env.example`: ghi chú bật `LOG_FORMAT=json` (log backend đã có sẵn
  request_id qua `python-json-logger`) để query LogQL theo request_id.

## Runbook triển khai trên VPS

### 1. Offsite backup (~1 giờ, làm TRƯỚC)

1. Tạo bucket (chọn 1):
   - **Backblaze B2** (rẻ nhất, ~$6/TB/tháng): tạo bucket `desi-backup`
     (private) + application key chỉ giới hạn bucket này.
   - **Cloudflare R2**: đã có account Cloudflare, egress free.
2. Trên VPS: `sudo apt install rclone` rồi `rclone config` tạo remote tên
   `offsite` (type `b2` hoặc `s3` + endpoint R2).
3. Tạo `/etc/desi-backup.env` (chmod 600, owner desi):

   ```sh
   DESI_BACKUP_RCLONE_DEST=offsite:desi-backup/postgres
   DESI_BACKUP_REQUIRE_OFFSITE=1
   DESI_BACKUP_HEARTBEAT_URL=https://hc-ping.com/<uuid-check-backup>
   ```

4. Cập nhật cron (source env trước khi chạy — xem header script):

   ```
   0 2 * * * desi . /etc/desi-backup.env && /usr/local/bin/desi-backup.sh >> /var/log/desi-backup.log 2>&1
   ```

5. Copy script mới: `sudo cp scripts/desi-backup.sh /usr/local/bin/` rồi chạy
   tay 1 lần, verify: file xuất hiện trên bucket (`rclone ls offsite:desi-backup`),
   metric `desi_backup_offsite_last_success_timestamp` có trong
   `/var/lib/node_exporter/textfile/desi_backup.prom`.
6. **Restore drill** (bắt buộc, 1 lần/quý): tải file từ bucket về máy khác,
   `pg_restore --list` + restore vào Postgres rỗng, đếm bảng chính.
7. Bật lifecycle rule trên bucket: giữ 30 bản gần nhất / xoá file >60 ngày
   (retention local vẫn 14d).

### 2. Giám sát ngoài (~30 phút)

1. **healthchecks.io** (free 20 checks): tạo 2 check —
   - `desi-backup-daily`: schedule `0 2 * * *`, grace 2h → URL dán vào
     `DESI_BACKUP_HEARTBEAT_URL` (bước 1.3).
   - `desi-celery-heartbeat`: period 5 phút, grace 10 phút → URL dán vào
     `OPS_HEARTBEAT_URL` trong `.env` prod rồi `docker compose -f
     docker-compose.prod.yml up -d web worker beat` (recreate để env vào
     container).
   - Integration: bật email + (nếu có) Lark/Telegram/SMS của healthchecks.
2. **UptimeRobot** (free 50 monitors) hoặc Better Stack: monitor HTTPS
   `https://customer.delax.vn/api/healthz`, keyword `"status": "ok"`,
   interval 5' — bắt được cả trường hợp nginx sống nhưng app/DB/Redis chết.
3. Smoke test: `curl https://customer.delax.vn/api/healthz` → 200
   `{"status":"ok","checks":{"db":true,"redis":true}}`; tắt thử beat 10' →
   healthchecks bắn alert.

### 3. Loki + Alloy (~30 phút)

1. Bật `LOG_FORMAT=json` trong `.env` prod (khuyến nghị — log có request_id
   để trace xuyên web/worker).
2. `docker compose -f docker-compose.prod.yml --profile monitoring up -d loki alloy`
   (và `up -d web worker beat` nếu vừa đổi LOG_FORMAT).
3. Grafana (127.0.0.1:3001 qua SSH tunnel) → Explore → datasource Loki →
   query thử `{container="desi-web"}`; với JSON log:
   `{container="desi-web"} | json | request_id != ""`.
4. Theo dõi disk 1 tuần đầu (`docker system df -v`, volume `loki_data`);
   nếu phồng quá thì giảm `retention_period` trong `monitoring/loki-config.yml`.

## Tiến độ

- [x] Sprint 1 code (script + alerts) — 2026-07-18
- [x] Sprint 2 code (healthz + heartbeat task + guards) — 2026-07-18
- [x] Sprint 3 code (loki/alloy/grafana/prometheus/compose) — 2026-07-18
- [ ] VPS: bucket + rclone + cron mới + chạy tay verify (mục 1)
- [ ] Đăng ký healthchecks.io + UptimeRobot, điền 2 URL heartbeat (mục 2)
- [ ] Deploy loki+alloy, bật LOG_FORMAT=json, verify query Grafana (mục 3)
- [ ] Restore drill lần đầu từ bucket offsite

## Ghi chú / quyết định

- Image pin `grafana/loki:3.5.2` + `grafana/alloy:v1.10.2` theo convention pin
  của repo; nếu tag không pull được lúc deploy thì thay bằng patch mới nhất
  cùng minor rồi sửa lại compose.
- Không thu log qua promtail (deprecated, EOL 2026) — Alloy là successor
  chính thức.
- Heartbeat chọn kiến trúc "push từ trong ra" (healthchecks) cho backup +
  Celery, "probe từ ngoài vào" (UptimeRobot) cho web — hai chiều bù nhau,
  không phụ thuộc Prometheus trên VPS.
- `/api/healthz` cố ý không check MinIO/Celery (probe phải rẻ, chạy mỗi phút;
  Celery đã có heartbeat riêng; MinIO chết không làm sập luồng đơn hàng).
