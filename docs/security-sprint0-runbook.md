# Sprint 0 — Runbook thao tác PROD (cần thực hiện thủ công, có maintenance window)

> ⚠️ Các bước dưới đây **mutate production đang chạy** và/hoặc **không đảo ngược**.
> KHÔNG để tự động chạy. Thực hiện khi có maintenance window + đã báo team.
> Host: `103.190.36.153` (live). KHÔNG hardcode secret vào file — sinh tại chỗ.
>
> Phần CODE của Sprint 0 (C1 gỡ tracking, C2 redaction, C3/C4/C5 config) đã commit
> trên branch `security-sprint-0-critical`; chỉ có hiệu lực **sau khi redeploy**.

---

## 0. KHẨN — Postgres đang expose `0.0.0.0:5432` ra internet

`desi-postgres` đang listen mọi interface trên host public, mật khẩu `Desi@2026` đã lộ
qua git history. Cần bịt NGAY (ưu tiên cao nhất). Chọn 1 trong 2:

**Cách A — Firewall (ít gián đoạn nhất, làm ngay):**
```bash
# Xác minh có/không cloud firewall trước. Nếu không có, chặn ở host:
sudo ufw allow from <office-ip> to any port 5432 proto tcp
sudo ufw deny 5432/tcp
sudo ufw status verbose
# Kiểm tra từ máy ngoài: nmap -p5432 103.190.36.153  -> phải filtered/closed
```

**Cách B — Rebind container về localhost (cần redeploy postgres):**
```bash
# Sau khi merge branch (đã đổi default bind -> 127.0.0.1 trong docker-compose.prod.yml)
docker compose -f docker-compose.prod.yml up -d postgres
docker ps --format '{{.Names}} {{.Ports}}' | grep postgres   # phải là 127.0.0.1:5432
```
> Lưu ý: web/worker/beat nối DB qua network `desi-net` (expose 5432), KHÔNG qua host
> port → rebind không ảnh hưởng app. Nhưng `up -d postgres` sẽ restart container DB.

---

## 1. Rotate secret đã lộ (maintenance window — có downtime ngắn)

`Desi@2026` (Postgres + Redis + Grafana) và KiotViet webhook secret đã lộ qua git/
`.env.example`. Phải đổi cả ở **service đang chạy** lẫn `.env` rồi restart đồng bộ.

> ⚠️ KHÔNG rotate `ENCRYPTION_KEY` (Fernet): nó đang mã hoá `client_secret`/password
> trong DB; đổi key = hỏng toàn bộ dữ liệu đã mã hoá (cần migration re-encrypt riêng).
> `ENCRYPTION_KEY`/`JWT_SIGNING_KEY` nằm trong `.env` (không bị track) → không bắt buộc.

### 1a. Sinh secret mới (tại chỗ, không lưu vào repo)
```bash
NEW_PG=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NEW_REDIS=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NEW_GRAFANA=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-18)
NEW_KV=$(openssl rand -hex 24)
NEW_KV_B64=$(printf '%s' "$NEW_KV" | base64)
```

### 1b. Đổi trên service đang chạy
```bash
# Postgres
docker exec -it desi-postgres psql -U desi -d desi \
  -c "ALTER USER desi WITH PASSWORD '$NEW_PG';"

# Redis (runtime, rồi persist qua --requirepass khi restart)
docker exec -it desi-redis redis-cli -a 'Desi@2026' CONFIG SET requirepass "$NEW_REDIS"

# Grafana: đổi qua UI (admin -> Profile -> Change password) hoặc:
docker exec -it desi-grafana grafana-cli admin reset-admin-password "$NEW_GRAFANA"
```

### 1c. Cập nhật `.env` (file live, KHÔNG commit)
Sửa các dòng: `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `REDIS_URL` (nếu có hardcode),
`GRAFANA_ADMIN_PASSWORD`, `KIOTVIET_WEBHOOK_SECRET=$NEW_KV`,
`KIOTVIET_WEBHOOK_SECRET_B64=$NEW_KV_B64`. Dùng **mật khẩu khác nhau** từng service.

### 1d. Đổi phía đối tác + restart app
- KiotViet: cập nhật webhook secret mới trong **portal KiotViet** cho khớp `$NEW_KV`.
- Restart để app đọc `.env` mới:
```bash
docker compose -f docker-compose.prod.yml up -d web worker beat
docker compose -f docker-compose.prod.yml logs --tail=50 web   # kiểm tra kết nối DB/Redis OK
```

---

## 2. Purge dump/PII khỏi git history + force-push (cần team coordination)

> ⚠️ Ghi đè history → mọi clone hiện có thành invalid, **20+ branch remote + Jenkins**
> bị ảnh hưởng. Báo toàn team dừng push, làm xong thì mọi người **re-clone**.

### 2a. Cài tool + backup an toàn
```bash
pip install git-filter-repo            # hoặc: brew install git-filter-repo
cd /home/desi/desi-order
git bundle create ../desi-order-FULL-backup-$(date +%Y%m%d).bundle --all   # backup toàn bộ
```

### 2b. Rewrite (xoá mọi blob nhạy cảm khỏi MỌI ref)
```bash
git filter-repo --force \
  --path-glob 'backup/*.dump' \
  --path-glob 'backup/**/*.dump' \
  --path-glob 'backup/*.rdb' \
  --path-glob 'backup/**/*.rdb' \
  --path backup/postgres/desi_order.sql \
  --path database.xlsx --path t4.xlsx \
  --path t4_assessment_mapping.csv --path t4_total.csv \
  --path desi.dump \
  --invert-paths
```

### 2c. Force-push tất cả branch + tags
```bash
git remote add origin git@gitlab.com:desi3512912/desi-order.git   # filter-repo gỡ remote
git push origin --force --all
git push origin --force --tags
```

### 2d. Sau khi xong
- Thông báo team: `rm -rf` clone cũ và clone lại.
- Trên GitLab: Settings → Repository → housekeeping để dọn unreachable objects.
- Vì PII khách hàng đã từng nằm trong history công khai-nội-bộ → đánh giá nghĩa vụ
  thông báo data breach theo quy định.

---

## Checklist xác nhận hoàn tất
- [ ] `nmap -p5432 103.190.36.153` → filtered (không còn open)
- [ ] App vẫn chạy sau rotate (login OK, đơn hàng OK, KiotViet webhook OK)
- [ ] `git log --all --oneline -- backup/ database.xlsx t4.xlsx` → rỗng
- [ ] Team đã re-clone; Jenkins job xanh trở lại
- [ ] Đã rotate: Postgres, Redis, Grafana, KiotViet webhook (KHÔNG đụng ENCRYPTION_KEY)
