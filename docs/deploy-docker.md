# Deploy toàn bộ project bằng Docker

Phiên bản: 0.1 — Ngày: 2026-05-12

Stack đầy đủ: **Postgres + Redis + Backend (Django/Celery) + Frontend + Nginx + Prometheus + Grafana + Exporters** trong một `docker compose`.

---

## 1. Yêu cầu

- Docker 24+ và Docker Compose v2 (`docker compose ...`, không phải `docker-compose ...`).
- 4 GB RAM khả dụng trên máy host (đủ cho Prometheus + Grafana + Postgres + 3 web worker).
- Port 80 và 127.0.0.1:3001 / 127.0.0.1:9090 trống (đổi qua `HTTP_PORT`,
  `PROM_HOST_PORT` nếu host đã có service khác chiếm).

---

## 2. Khởi tạo lần đầu

### 2.1. Chuẩn bị `.env`
```bash
cp .env.example .env
```
Sau đó mở `.env` và **bắt buộc** điền:

```bash
# Sinh secret 64 byte url-safe
python3 -c 'import secrets;print("DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64))'
python3 -c 'import secrets;print("JWT_SIGNING_KEY=" + secrets.token_urlsafe(64))'

# Sinh Fernet key (44 byte base64) — phải trùng key đã dùng để encrypt
# KiotViet credentials trong DB. Nếu chưa có, sinh mới và sẽ phải re-encrypt.
python3 -c 'from cryptography.fernet import Fernet;print("ENCRYPTION_KEY=" + Fernet.generate_key().decode())'
```

Đổi `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` thành giá trị mạnh.

### 2.2. Build & chạy
**Chỉ app (không monitoring):**
```bash
docker compose -f docker-compose.full.yml up -d --build
```

**App + monitoring stack:**
```bash
docker compose -f docker-compose.full.yml --profile monitoring up -d --build
```

### 2.3. Kiểm tra
```bash
docker compose -f docker-compose.full.yml ps
docker compose -f docker-compose.full.yml logs -f web worker
```

Truy cập:
| URL | Mô tả |
|-----|------|
| http://localhost | Frontend + API (qua nginx) |
| http://localhost/api/orders/metrics | Prometheus metrics của Django |
| http://localhost:3001 | Grafana — user/pass theo `.env` |
| http://localhost:9090 | Prometheus UI (chỉ 127.0.0.1, đổi bằng `PROM_HOST_PORT`) |

Grafana và Prometheus **chỉ bind 127.0.0.1**. Từ máy khác vào bằng SSH tunnel:
`ssh -L 3001:127.0.0.1:3001 <user>@<host>` rồi mở http://localhost:3001.

Muốn cả team vào Grafana bằng domain thì proxy qua nginx host theo sub-path —
xem `nginx/grafana-subpath.location.example` (`GRAFANA_ROOT_URL` +
`GRAFANA_SERVE_FROM_SUB_PATH` trong `.env`). Không bind Grafana ra `0.0.0.0`:
HTTP trần đẩy mật khẩu đi plaintext và port lạ sẽ bị bot quét trong vài giờ.

---

## 3. Cấu trúc stack

```
                    ┌─────────────┐
            host:80 │   nginx     │  reverse proxy
                    └──┬───────┬──┘
                       │       │
                ┌──────▼──┐  ┌─▼────────┐
                │ frontend│  │   web    │  Django + Daphne (ASGI)
                │ (React) │  │  :8000   │
                └─────────┘  └──┬──┬────┘
                                │  │
                ┌───────────────┘  └──────────────┐
                ▼                                  ▼
           ┌────────┐                       ┌────────────┐
           │postgres│                       │   redis    │
           │ :5432  │                       │   :6379    │
           └────┬───┘                       └──┬──────┬──┘
                │                              │      │
        ┌───────┴─────────┐             ┌──────┘      │
        ▼                 ▼             ▼             ▼
   ┌─────────┐      ┌──────────┐  ┌────────┐   ┌────────┐
   │ worker  │      │   beat   │  │ worker │   │  web   │
   │ celery  │      │  celery  │  │(cache, │   │ (chan- │
   │         │      │   beat   │  │ broker)│   │  nels) │
   └─────────┘      └──────────┘  └────────┘   └────────┘

   ─── monitoring profile ───
   prometheus ←── scrape ──┬──── desi-backend (/api/orders/metrics)
                           ├──── node-exporter (host)
                           ├──── cadvisor (containers)
                           ├──── postgres-exporter
                           ├──── redis-exporter
                           └──── nginx-exporter
        ▲
        │
     grafana ──── dashboards: System Overview, Landing Ingest
```

---

## 4. Service detail

| Service | Image / Build | Vai trò | Port |
|---------|---------------|---------|------|
| `postgres` | postgres:16-alpine | DB chính. Cluster prod dựng lại 22/09/2026 trên alpine/musl (collation `datlocprovider=c`, checksum tắt) — xem `runbooks/2026-09-22-postgres-rebuild.md` trước khi đổi image hoặc restore. 6 tham số hiệu năng thêm 23/09 cần recreate container. | 5432 (internal) |
| `redis` | redis:7-alpine | Cache + Channel layer + Celery broker | 6379 (internal) |
| `web` | build `./backend` | Django ASGI (gunicorn + uvicorn worker) | 8000 (internal) |
| `worker` | build `./backend` | Celery worker, consume `default,orders,kiotviet,zalo_bridge,lark_noti` | — |
| `beat` | build `./backend` | Celery beat scheduler (`django_celery_beat.DatabaseScheduler`) | — |
| `frontend` | build `./frontend` | React build, phục vụ static qua nginx-alpine | 80 (internal) |
| `nginx` | nginx:1.27-alpine | Reverse proxy → frontend + web + ws | 80 (host) |
| `prometheus` | prom/prometheus | TSDB + scrape | 9090 (127.0.0.1) |
| `grafana` | grafana/grafana | Visualization | 3001 (127.0.0.1) |
| `node-exporter` | prom/node-exporter | Metric host (CPU/RAM/disk/net) | 9100 (internal) |
| `cadvisor` | gcr.io/cadvisor | Metric per-container | 8080 (internal) |
| `postgres-exporter` | prometheuscommunity | Metric DB | 9187 (internal) |
| `redis-exporter` | oliver006/redis_exporter | Metric Redis | 9121 (internal) |
| `nginx-exporter` | nginx/nginx-prometheus-exporter | Metric Nginx (stub_status) | 9113 (internal) |

---

## 5. Metrics đã expose

### 5.1. Backend (Django)
Endpoint `/api/orders/metrics` ([backend/apps/orders/metrics.py](../backend/apps/orders/metrics.py)) — yêu cầu `pip install prometheus-client`.

> **Cần thêm vào `backend/requirements.txt`** nếu chưa có:
> ```
> prometheus-client==0.20.0
> ```
> Nếu không cài, endpoint vẫn trả 200 nhưng counter là no-op (xem `_ENABLED` trong file).

Counters hiện có: `desi_landing_ingest_*`.

### 5.2. Multi-process Prometheus
- Biến `PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc` đã set trong compose cho `web`, `worker`, `beat`.
- Mỗi process Python ghi counter vào file riêng, lib gom lại khi scrape.
- `command` của `web` đã `rm -f /tmp/prom_multiproc/*` lúc start để không tích lũy file rác.

### 5.3. Postgres exporter — tuỳ biến query
Mặc định exporter đã có metric chuẩn (`pg_stat_*`). Để thêm query custom (ví dụ count orders per status):
```bash
# Tạo monitoring/postgres_exporter_queries.yaml rồi mount vào container,
# truyền PG_EXPORTER_EXTEND_QUERY_PATH=/etc/queries.yaml
```

---

## 6. Vận hành

### 6.1. Khởi động lại 1 service
```bash
docker compose -f docker-compose.full.yml restart web worker
```

### 6.2. Đọc log
```bash
docker compose -f docker-compose.full.yml logs -f --tail=200 web
docker compose -f docker-compose.full.yml logs -f --tail=200 worker beat
docker compose -f docker-compose.full.yml logs -f nginx
```

### 6.3. Vào shell Django
```bash
docker compose -f docker-compose.full.yml exec web python manage.py shell
docker compose -f docker-compose.full.yml exec web python manage.py createsuperuser
```

### 6.4. Backup Postgres
```bash
docker compose -f docker-compose.full.yml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -F c "$POSTGRES_DB" > "backup/desi_$(date +%F).dump"
```

### 6.5. Reload Prometheus config (không cần restart container)
```bash
# Thay 9090 bằng PROM_HOST_PORT nếu .env đã đổi (prod hiện dùng 9092).
curl -X POST http://127.0.0.1:9090/-/reload
```

### 6.6. Dừng toàn bộ
```bash
docker compose -f docker-compose.full.yml --profile monitoring down
# Xoá luôn volume:
docker compose -f docker-compose.full.yml --profile monitoring down -v
```

---

## 7. Dashboard

### 7.1. Có sẵn (auto-provision từ `monitoring/grafana/dashboards/`)
- **Desi / System Overview** (`system-overview.json`) — uptime, host CPU/RAM, Postgres txn, Redis ops, container CPU, landing ingest p95.
- **Desi / Landing Ingest** (`landing-ingest.json`) — chi tiết theo bước ingest.

### 7.2. Khuyến nghị import thêm (Grafana → Dashboards → Import → nhập ID)
| ID | Tên | Datasource |
|----|-----|-----------|
| 1860 | Node Exporter Full | Prometheus |
| 9628 | PostgreSQL Database | Prometheus |
| 11835 | Redis Dashboard for Prometheus Redis Exporter | Prometheus |
| 14282 | cAdvisor exporter | Prometheus |
| 12708 | NGINX exporter | Prometheus |

Sau khi import, chọn datasource `Prometheus` đã provision sẵn.

---

## 8. Bảo mật triển khai

- Prometheus và Grafana **chỉ bind 127.0.0.1** (`ports: "127.0.0.1:9090:9090"`). Không expose public.
- Truy cập từ xa → chạy SSH tunnel: `ssh -L 3001:127.0.0.1:3001 user@server`.
- Đổi `GRAFANA_ADMIN_PASSWORD` ngay sau lần đầu đăng nhập.
- Đặt firewall ufw chỉ mở 80/443/22.
- Để chạy HTTPS, thêm certbot/traefik trước `nginx` hoặc dùng cloudflare tunnel.

---

## 9. Khắc phục sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử |
|-------------|------------------------|---------|
| `prometheus-client` không gom được metric từ celery | Worker chạy thiếu `PROMETHEUS_MULTIPROC_DIR` | Đã set trong compose; kiểm tra `docker exec desi-worker env | grep PROM` |
| Grafana không thấy data | Sai datasource UID | UID phải là `prometheus` (xem [datasources/prometheus.yml](../monitoring/grafana/provisioning/datasources/prometheus.yml)) |
| `nginx-exporter` 502 | nginx chưa expose `/nginx_status` | Đã thêm location stub_status trong [nginx/default.conf](../nginx/default.conf); restart `nginx` |
| `postgres-exporter` connection refused | Sai `DATA_SOURCE_NAME` hoặc postgres chưa healthy | `docker compose ... logs postgres-exporter` |
| Celery worker pool segfault trên macOS Docker | macOS + prefork không tương thích | Đặt `CELERY_WORKER_POOL=solo` trong `.env` |
| Web restart loop khi migrate fail | Migration mới conflict | `docker compose ... exec web python manage.py showmigrations` để debug |
| Disk đầy do Prometheus | Retention quá dài | Hạ `PROM_RETENTION` (ví dụ `15d`) rồi restart prometheus |

