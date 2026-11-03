# Monitoring (Prometheus + Grafana)

Stack giám sát local cho toàn bộ desi-order: Django backend, Go services
(task-worker / task-cron / audit-writer), Postgres, Redis, host và container.

## Yêu cầu

Stack app phải đang chạy trước — Prometheus join vào network external của các
app service. Tên network khác giữa môi trường:

| Môi trường | Compose file | Network |
|---|---|---|
| Prod  | `docker-compose.prod.yml`  | `desi-order_default` (mặc định) |
| Local | `docker-compose.local.yml` | `desi-order_desi-net` |

Nếu compose project name khác hoặc network name khác, set env var:

```bash
export DESI_NET_NAME=ten-network-thuc-te
```

## Env vars bắt buộc

Phải có trong shell hoặc `.env` ở root (truyền qua `--env-file ../.env`):

| Var | Dùng cho |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | postgres-exporter |
| `REDIS_PASSWORD` | redis-exporter |
| `GRAFANA_ADMIN_PASSWORD` | login Grafana — **bắt buộc đặt mật khẩu mạnh vì Grafana mở public** |
| `GRAFANA_ADMIN_USER` (optional) | mặc định `admin` |

## Khởi động

```bash
cd monitoring

# Prod (default)
docker compose --env-file ../.env up -d

# Local dev
DESI_NET_NAME=desi-order_desi-net docker compose --env-file ../.env up -d
```

### Truy cập

Grafana bind `0.0.0.0:3001` để truy cập qua IP server. Các service còn lại
giữ loopback `127.0.0.1` (chỉ vào được từ chính server hoặc qua SSH tunnel).

| Service | URL | Truy cập |
|---|---|---|
| **Grafana** | `http://<server-ip>:3001` | Public (cần password) |
| Prometheus | `http://127.0.0.1:9090` | Loopback |
| cAdvisor | `http://127.0.0.1:8085` | Loopback |
| node-exporter | `http://127.0.0.1:9100/metrics` | Loopback |
| redis-exporter | `http://127.0.0.1:9121/metrics` | Loopback |
| postgres-exporter | `http://127.0.0.1:9187/metrics` | Loopback |

Debug Prometheus/exporter từ máy local qua SSH tunnel:

```bash
ssh -L 9090:localhost:9090 -L 8085:localhost:8085 desi@<server-ip>
```

### Bảo mật khi mở public

- **Bắt buộc đổi `GRAFANA_ADMIN_PASSWORD`** trước khi chạy — không dùng `admin/admin`.
- Khuyến nghị firewall (ufw / security group) chỉ allow port `3001` từ IP cố định.
- Tốt hơn: đứng sau nginx + HTTPS + IP whitelist thay vì expose trực tiếp.

## Targets được scrape

| Job | Target | Loại metric |
|---|---|---|
| `desi-backend` | `desi-web:8000/api/orders/metrics` | App-level (landing ingest) |
| `task-worker` | `desi-task-worker:2112` | Go runtime + `desi_jobs_*` |
| `task-cron` | `desi-task-cron:2112` | Go runtime + `desi_cron_fires_total` |
| `audit-writer` | `desi-audit-writer:2112` | Go runtime + `desi_audit_*` |
| `node-exporter` | `node-exporter:9100` | Host CPU/RAM/disk/network |
| `cadvisor` | `cadvisor:8080` | Per-container CPU/RAM/network/IO |
| `redis-exporter` | `redis-exporter:9121` | Redis keyspace, memory, hits/misses |
| `postgres-exporter` | `postgres-exporter:9187` | Postgres connections, queries, locks |

## Metrics chính

### Django (`backend/apps/orders/metrics.py`)

| Metric | Labels |
|--------|--------|
| `desi_landing_ingest_received_total` | `token=ok\|invalid` |
| `desi_landing_ingest_validated_total` | `result=valid\|invalid` |
| `desi_landing_ingest_enqueued_total` | `result=success\|failed` |
| `desi_landing_ingest_processed_total` | `result=created\|dry_run_skipped\|failed` |
| `desi_landing_ingest_process_latency_seconds` | histogram |

### Go services (`go-services/internal/shared/platform/metrics/metrics.go`)

| Metric | Labels | Service |
|--------|--------|---------|
| `desi_jobs_processed_total` | `queue, task_type, result` | task-worker |
| `desi_jobs_handler_duration_seconds` | `queue, task_type` | task-worker |
| `desi_cron_fires_total` | `name, status` | task-cron |
| `desi_audit_events_total` | `result` | audit-writer |
| `desi_audit_batch_duplicates_total` | — | audit-writer |
| `go_*` | — | tất cả 3 (process collector) |

## Dashboard

Auto-provision khi Grafana start:
- **Landing Ingest** (`landing-ingest.json`)
- **System Overview** (`system-overview.json`) — cần node-exporter/postgres/redis exporter

Khuyến nghị import thêm từ grafana.com:
- **193** — Docker Container & Host Metrics (cAdvisor)
- **1860** — Node Exporter Full
- **763** — Redis
- **9628** — PostgreSQL Database
- **6671** — Go Processes

## Toggle dry-run landing ingest

```bash
export ORDERS_LANDING_INGEST_DRY_RUN=1   # bật
unset  ORDERS_LANDING_INGEST_DRY_RUN     # tắt
```

Restart `desi-web` sau khi đổi.

## Lưu ý

- Network `desi-order_desi-net` chỉ tồn tại khi stack app đang chạy. Nếu down
  stack app trước monitoring, Prometheus container sẽ không lên được khi restart.
- Postgres + Redis exporter dùng `host.docker.internal` thay vì container DNS vì
  postgres/redis có thể chạy từ compose project khác (`docker-compose.yml` base
  hoặc `docker-compose.local.yml`).
- Trên macOS, `node-exporter` và `cAdvisor` chỉ thấy metric của Docker Desktop VM
  chứ không phải macOS host. Trên Linux prod thì đầy đủ.
