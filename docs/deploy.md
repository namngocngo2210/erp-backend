---
title: Deploy customer.delax.vn — local + prod compose split
version: 0.8.2
date: 2026-05-15
related: docs/go-offload-plan.md §19
---

# Deploy customer.delax.vn

Có **2 file compose** sau session 0.8.2 (Phase 7+):

| File | Mục đích | Postgres / Redis |
|---|---|---|
| `docker-compose.local.yml` | Dev local — full stack tự host | Tự khởi tạo container + named volume |
| `docker-compose.prod.yml` | Production VPS — connect container hiện hữu | **External**: reuse `desi-order_default` network, postgres+redis container đã chạy 2 weeks |

Lý do tách: prod data 130GB+ trong volume `desi-order_pg_data` đã ổn định, không thể bring-down + recreate. Plan §19.3 option A — bảo toàn data tại chỗ.

## Stack

| Container | Image / Build | Vai trò | Source compose |
|-----------|---------------|---------|---|
| `desi-order-postgres-1` | postgres:16-alpine | DB | đã chạy độc lập (KHÔNG quản qua compose mới) |
| `desi-order-redis-1` | redis:7-alpine | Cache + Pub/Sub | đã chạy độc lập |
| `desi-web` | build `./backend` | Django ASGI + REST/WS | local + prod |
| `desi-frontend` | build `./frontend` | Static React | local + prod |
| `desi-pubsub-bridge` | build `./backend` | Redis→Channels group_send forwarder | local + prod |
| `desi-audit-writer` | build `./go-services` | Audit HTTP buffer | local + prod |
| `desi-task-worker` | build `./go-services` | jobs queue consumer | local + prod |
| `desi-task-cron` | build `./go-services` | cron enqueuer | local + prod |
| `desi-nginx` | nginx:1.27-alpine | Reverse proxy + SSL (443) | **local only** — prod dùng host nginx |

Stack monitoring (`--profile monitoring`): prometheus + grafana + 4 exporters.

## Lệnh local

```sh
cp .env.example .env.local  # chỉnh secrets (KIOTVIET_API_MODE=mock, etc)

docker compose --env-file .env.local -f docker-compose.local.yml up -d --build
docker compose --env-file .env.local -f docker-compose.local.yml --profile monitoring up -d
docker compose --env-file .env.local -f docker-compose.local.yml --profile celery-fallback up -d worker beat
docker compose --env-file .env.local -f docker-compose.local.yml logs -f --tail=100 web task-worker task-cron audit-writer
docker compose --env-file .env.local -f docker-compose.local.yml down -v   # wipe data
```

## Lệnh prod (trên VPS)

```sh
cd /home/desi/desi-order
git pull origin master

# .env phải có TASK_BACKEND=celery (cutover an toàn) + INTERNAL_AUTH_TOKEN random 32-byte
docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

## Toggle backend

| Env | Default | Effect |
|-----|---------|--------|
| `TASK_BACKEND` | `celery` | `go` → enqueue vào `jobs` table; `celery` → fallback `.apply_async()` |
| `TASK_BACKEND_OVERRIDE` | empty | Per-task, vd. `kv.process_job=go,zalo.send_order_created=go` |
| `AUDIT_BACKEND` | `celery` | `go` → POST audit-writer; `celery` → `create_audit_log_task.delay()` |
| `INTERNAL_AUTH_TOKEN` | required | 32+ byte token Django ↔ Go (X-Internal-Token header) |

Sau cutover, disable Celery beat schedule:
```sh
docker exec desi-web python manage.py disable_celery_beat
docker exec desi-web python manage.py disable_celery_beat --enable   # rollback
```

## Production cutover — checklist (downtime ~10-15 phút)

### Trước cutover (T-1 ngày)

1. **Sửa `.env`**:
   - Đổi `INTERNAL_AUTH_TOKEN` từ `local-...` sang `openssl rand -hex 32`
   - Set `TASK_BACKEND=celery` + `AUDIT_BACKEND=celery` (cutover an toàn)
   - Verify `KIOTVIET_API_MODE=live`
   - Verify `DJANGO_ALLOWED_HOSTS` chứa `customer.delax.vn` và `erp.desiglobal.vn`

2. **SSL cert** Let's Encrypt tại `/etc/letsencrypt/live/customer.delax.vn/` — SAN phải cover
   cả `erp.desiglobal.vn` (kiểm tra bằng `sudo certbot certificates`)

   **Host nginx vhost** — copy `nginx/customer.delax.vn.host.conf.example` → `/etc/nginx/sites-available/customer.delax.vn`:
   ```sh
   # Zone rate limit PHẢI cài trước: vhost tham chiếu tới zone, thiếu file này
   # thì `nginx -t` báo "unknown limit_req zone" và nginx không reload được.
   sudo cp nginx/rate-limit-zones.conf.example /etc/nginx/conf.d/00-rate-limit-zones.conf
   sudo cp nginx/customer.delax.vn.host.conf.example /etc/nginx/sites-available/customer.delax.vn
   sudo ln -sf /etc/nginx/sites-available/customer.delax.vn /etc/nginx/sites-enabled/customer.delax.vn
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Vhost gồm 2 domain trong cùng 1 file:
   - `customer.delax.vn`: CHỈ backend — mọi path (kể cả `/`) → `127.0.0.1:8000` (web container)
   - `erp.desiglobal.vn`: `/api/`, `/ws/` → `127.0.0.1:8000`; `/`, `/assets/` →
     `127.0.0.1:8001` (frontend container); `/grafana` → `127.0.0.1:3001`

   Rate limit ở edge: `/api/` 20 r/s (burst 40), `/api/users/login|token/refresh`
   1 r/s (burst 20), `/api/orders/ingest` 30 r/s (burst 60) — key theo
   `CF-Connecting-IP`. Vượt trần trả **429**. Rollback nhanh = comment dòng
   `limit_req` trong vhost rồi `sudo nginx -s reload`.
   Cơ sở chọn số: [docs/rate-limiting-hardening-2026-07.md](rate-limiting-hardening-2026-07.md).

3. **Verify volume + network bên VPS** (đã có sẵn):
   ```sh
   docker volume ls | grep desi-order   # phải thấy desi-order_pg_data + desi-order_redis_data
   docker network ls | grep desi-order   # phải thấy desi-order_default
   ```

4. **Backup** (T-1 giờ):
   ```sh
   mkdir -p /opt/desi-backup
   docker exec desi-order-postgres-1 pg_dump -U desi -F c desi --no-owner --no-privileges \
     > /opt/desi-backup/pre-go-cutover-$(date +%Y%m%d-%H%M).dump
   ```

### Cutover (T-0)

```sh
# 1. Maintenance mode Cloudflare (1 page rule trả 503)

# 2. Stop supervisor (host nginx GIỮ NGUYÊN — vẫn termination SSL)
sudo supervisorctl stop all
sudo ss -tlnp | grep -E ':8000'            # phải rỗng (chỉ còn nginx ở 80/443)

# 3. Build + up Go stack với Celery backend
cd /home/desi/desi-order
git pull origin master
docker compose -f docker-compose.prod.yml --profile celery-fallback up -d --build
sleep 30
docker compose -f docker-compose.prod.yml ps

# 4. Smoke
./scripts/desi-smoke.sh customer.delax.vn

# 5. Nếu OK, switch sang Go
sed -i 's/^TASK_BACKEND=celery/TASK_BACKEND=go/' .env
sed -i 's/^AUDIT_BACKEND=celery/AUDIT_BACKEND=go/' .env
docker compose -f docker-compose.prod.yml restart web pubsub-bridge
docker exec desi-web python manage.py disable_celery_beat   # tắt PeriodicTask Celery beat

# 6. Stop Celery worker/beat (giữ image 14 ngày để rollback nhanh)
docker compose -f docker-compose.prod.yml stop worker beat

# 7. Tắt Cloudflare maintenance
```

### Rollback (nếu cutover fail)

```sh
sed -i 's/^TASK_BACKEND=go/TASK_BACKEND=celery/' .env
sed -i 's/^AUDIT_BACKEND=go/AUDIT_BACKEND=celery/' .env
docker compose -f docker-compose.prod.yml --profile celery-fallback up -d worker beat
docker compose -f docker-compose.prod.yml restart web pubsub-bridge
docker exec desi-web python manage.py disable_celery_beat --enable

# Worst case: trả về supervisor
docker compose -f docker-compose.prod.yml down
sudo supervisorctl start all
sudo cp nginx/customer.delax.vn.host.conf.SUPERVISOR.bak /etc/nginx/sites-available/customer.delax.vn
sudo nginx -t && sudo systemctl reload nginx
```

Postgres + Redis containers KHÔNG bị động trong rollback — vẫn chạy như trước.
Host nginx vẫn chạy suốt — chỉ đổi vhost upstream.

## Backup hằng ngày

```sh
sudo cp scripts/desi-backup.sh /usr/local/bin/desi-backup.sh
sudo chmod +x /usr/local/bin/desi-backup.sh
# Update tên container trong script: desi-postgres → desi-order-postgres-1
echo "0 2 * * * root /usr/local/bin/desi-backup.sh >> /var/log/desi-backup.log 2>&1" | sudo tee /etc/cron.d/desi-backup
```

## Cloudflare config

- DNS: `customer.delax.vn` → IP VPS, **proxy ON** (orange cloud)
- SSL/TLS: **Full (strict)**
- Network → WebSockets: **ON**
- Page Rules: bypass cache `/api/*` `/ws/*`; cache `/assets/*` 7 ngày

## Resource budget (8GB / 4 vCPU prod)

Postgres + Redis độc lập KHÔNG đếm trong compose mới (đã chạy ~2 weeks ổn định). Thêm services compose mới:

| Container | RAM | CPU |
|---|---|---|
| web (gunicorn 3w/4t) | 1.2 GB | 1.5 vCPU |
| pubsub-bridge | 200 MB | 0.3 vCPU |
| audit-writer (Go) | 128 MB | 0.5 vCPU |
| task-worker (Go) | 256 MB | 1.0 vCPU |
| task-cron (Go) | 96 MB | 0.3 vCPU |
| frontend | ~30 MB | 0.1 vCPU |
| **Tổng (added)** | **~1.9 GB** | **~3.7 vCPU** |

Host nginx (systemd) — đã chạy sẵn, không tính thêm.
