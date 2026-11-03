# Runbook — Fix `/dev/shm` Postgres prod (DiskFull shared memory segment)

## Triệu chứng
```
psycopg2.errors.DiskFull: could not resize shared memory segment
"/PostgreSQL.xxxx" to 2097152 bytes: No space left on device
```
Xuất hiện ở các API chạy parallel query (vd `GET /api/customers/resale` → `queryset.count()`).

## Nguyên nhân gốc
`No space left on device` ở đây **không phải** ổ đĩa data đầy. Postgres cấp
*dynamic shared memory* cho parallel query workers trong `/dev/shm`. Container
postgres prod được tạo tay (mồ côi, không thuộc compose nào), nên `/dev/shm`
= **64MB mặc định** của Docker → tràn khi nhiều parallel worker xin segment.

`docker-compose.local.yml` đã có `shm_size: 1g` từ trước; bản fix này đưa
postgres prod vào quản lý bởi `docker-compose.prod.yml` với cùng cấu hình,
**giữ nguyên volume external `desi-order_pg_data` (130GB+)**.

## Bước 1 — Xác nhận (read-only)
```bash
docker exec desi-order-postgres-1 df -h /dev/shm        # Size = 64M → đúng bệnh
df -h /                                                  # đĩa host còn trống → loại trừ disk full
docker inspect desi-order-postgres-1 --format '{{.HostConfig.ShmSize}}'  # 67108864 = 64MB
docker volume ls | grep pg_data                          # xác nhận tên volume = desi-order_pg_data
```

## Bước 2 — Giảm đau NGAY (không downtime, không recreate)
Tắt parallel query bằng reload config — hết phụ thuộc `/dev/shm` tức thì:
```bash
docker exec -it desi-order-postgres-1 psql -U desi -d desi -c \
  "ALTER SYSTEM SET max_parallel_workers_per_gather = 0; SELECT pg_reload_conf();"
```
> Đánh đổi: vài aggregate lớn chậm hơn chút. Có thể bật lại sau Bước 3:
> `ALTER SYSTEM RESET max_parallel_workers_per_gather; SELECT pg_reload_conf();`

## Bước 3 — Fix triệt để (recreate container, downtime ~30s) {#recreate-postgres}
> `shm_size` KHÔNG đổi được trên container đang chạy → bắt buộc recreate.
> Volume data được mount lại nguyên vẹn, **không mất dữ liệu**.

```bash
cd /home/desi/desi-order
git pull origin master

# 1) Backup phòng thân (tùy chọn nhưng nên làm)
docker exec desi-order-postgres-1 pg_dump -U desi -d desi -Fc -f /tmp/pre-shmfix.dump
docker cp desi-order-postgres-1:/tmp/pre-shmfix.dump ./backup/pre-shmfix.dump

# 2) Dừng app để ngắt kết nối DB (tránh ghi dở khi đổi container)
docker compose -f docker-compose.prod.yml stop web worker beat

# 3) Gỡ container postgres mồ côi (volume desi-order_pg_data KHÔNG bị xóa)
docker stop desi-order-postgres-1 && docker rm desi-order-postgres-1

# 4) Dựng lại postgres do compose quản lý (đã có shm_size: 1g)
docker compose -f docker-compose.prod.yml up -d postgres
docker compose -f docker-compose.prod.yml ps postgres   # chờ healthy

# 5) Xác nhận shm = 1g và data còn nguyên
docker exec desi-postgres df -h /dev/shm                 # Size = 1.0G
docker exec desi-postgres psql -U desi -d desi -c "SELECT count(*) FROM orders;"

# 6) Khởi động lại app
docker compose -f docker-compose.prod.yml up -d web worker beat
./scripts/desi-smoke.sh customer.delax.vn
```

## Rollback
Nếu container mới lỗi, data vẫn an toàn trong volume `desi-order_pg_data`.
Dựng lại container tay tạm thời:
```bash
docker run -d --name desi-order-postgres-1 --network desi-order_default \
  --network-alias postgres -v desi-order_pg_data:/var/lib/postgresql/data \
  -e POSTGRES_DB=desi -e POSTGRES_USER=desi -e POSTGRES_PASSWORD=*** \
  public.ecr.aws/docker/library/postgres:16-alpine
```

## Theo dõi sau fix
- Grafana / postgres-exporter: theo dõi `pg_stat_activity` và lỗi DiskFull không tái diễn.
- Cân nhắc (sprint sau): bỏ `COUNT(*)` chính xác ở `list_resale_tracking`, dùng
  keyset pagination / estimated count cho bảng `orders` lớn để giảm tải parallel aggregate.
