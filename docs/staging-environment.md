# Staging environment — snapshot dữ liệu prod, cách ly side-effects

**Ngày:** 2026-07-18 · **Branch:** `feat/staging-environment` · **Trạng thái:** Sprint 1 DONE (code), chờ dựng hạ tầng

## 1. Mục tiêu

Dựng một môi trường test (staging) có **dữ liệu giống hệt prod** tại thời điểm refresh
(snapshot đầy đủ, không cắt bảng, không mask PII) mà **không thể ảnh hưởng prod**:

- Hạ tầng tách biệt hoàn toàn: compose project riêng, network riêng, volume riêng.
- Dữ liệu chảy MỘT chiều: prod backup → staging. Staging không có credential nào chạm prod DB.
- Staging **không cầm chìa khóa thật** của bên thứ ba: KiotViet mock, Lark tắt,
  Best Express tắt, Zenify tắt, ads sync tắt → không thể ghi lên KiotViet thật /
  bắn tin Lark thật từ staging.
- Vì giữ nguyên PII: staging phải được siết access như prod (IP văn phòng / VPN,
  basic-auth ở nginx).

## 2. Quyết định thiết kế (đã chốt với CEO 2026-07-18)

| Quyết định | Chọn | Lý do |
|---|---|---|
| Mức "giống" dữ liệu | Snapshot đầy đủ từ backup nightly, KHÔNG mask PII, KHÔNG exclude bảng | User chốt "chỉ cần snapshot"; đổi lại siết access staging |
| Realtime replica | KHÔNG (giai đoạn này) | Replica read-only không test ghi được; snapshot đủ 95% nhu cầu |
| Nguồn dữ liệu | File dump của `scripts/desi-backup.sh` (đã chạy nightly 02:00, có verify + checksum) | Không thêm tải lên prod DB; dump đã được kiểm chứng |
| Secrets trong DB restore | Bị vô hiệu hóa bằng `manage.py staging_neutralize` ngay sau restore | `EncryptedCharField` decrypt-fail chỉ trả rác (fallback raw) — phải xóa tường minh |
| `system_settings` (apps.sysconfig, từ 2026-09-19) | `staging_neutralize` xóa mọi dòng `staging_forbidden=True` + toàn bộ nhóm `inbound_tokens` | Cùng lớp bảo vệ với các bảng credential khác — sau restore staging phải tự đặt lại token/URL riêng, không kế thừa giá trị prod |
| `ENCRYPTION_KEY` staging | KHÁC prod | Lớp bảo vệ thứ 2: ciphertext prod thành vô nghĩa trên staging |
| Celery beat | Không chạy (không có service trong compose staging) + neutralize tắt toàn bộ `PeriodicTask` trong DB | Cron của hệ là loại gọi ra ngoài (reconcile, import, ads sync, Lark) |
| Boot guard | `APP_ENV=staging` + env cấm (KiotViet live, Lark, Best, Zenify) → refuse start | Loại trừ vĩnh viễn tai nạn "quên đổi env" |

## 3. Ba lớp bảo vệ chống side-effect

1. **Env**: compose staging hardcode `APP_ENV=staging`, `KIOTVIET_API_MODE=mock`,
   không truyền bất kỳ biến `LARK_*` / `BEST_EXPRESS_*` / `ZENIFY_*` nào.
2. **Settings guard (fail-fast lúc boot)** — `config/settings_parts/base.py`:
   khi `APP_ENV=staging`, tiến trình từ chối khởi động nếu `KIOTVIET_API_MODE=live`
   hoặc bất kỳ biến outbound nào (Lark webhook/app, Best username, Zenify key) không rỗng.
   Cùng pattern với prod secret guard (audit 2026-07 mục 1.3).
3. **DB neutralize** — `manage.py staging_neutralize --yes` (chạy tự động trong
   `scripts/staging-refresh.sh`): xóa secrets đã restore từ prod
   (KiotVietChannel.client_secret/password, BestExpressAccount, GoogleAdsConfig,
   FacebookApp) + tắt toàn bộ `django_celery_beat.PeriodicTask` + xóa mọi dòng
   `system_settings` (apps.sysconfig, từ 2026-09-19) có `staging_forbidden=True`
   hoặc thuộc nhóm `inbound_tokens` (khóa còn lại rơi về `default` của registry —
   xem `docs/system-settings.md`). Command từ chối chạy khi `APP_ENV=production`.

Chiều inbound: **tuyệt đối không chạy lệnh register webhook KiotViet từ staging**
(sẽ ghi đè URL webhook đang trỏ về prod). Test webhook bằng curl với payload mẫu.

## 4. Thành phần đã code (Sprint 1)

| File | Vai trò |
|---|---|
| `docker-compose.staging.yml` | Stack staging tự đóng gói: postgres/redis/minio/web/worker/worker-kiotviet/frontend. Project name `desi-order-staging` → network + volume tự prefix, không đụng `desi-order_default` / `desi-order_pg_data`. KHÔNG có beat. Port bind localhost: web 8100, frontend 8101, postgres 8543 |
| `.env.staging.example` | Template env staging — secrets sinh MỚI (không copy từ prod), đặc biệt `ENCRYPTION_KEY` phải khác prod |
| `backend/config/settings_parts/base.py` | `IS_STAGING` + `find_staging_forbidden_env()` / `assert_staging_outbound_disabled()` |
| `backend/apps/users/management/commands/staging_neutralize.py` | Vô hiệu hóa secrets + tắt cron trong DB sau restore |
| `scripts/staging-refresh.sh` | Trọn gói: verify checksum → drop/create DB → pg_restore -j → migrate → neutralize → restart app. Guard chống chạy nhầm container prod (`desi-postgres` bị blacklist cứng) |
| Tests | `apps/common/tests/test_settings_guard_staging.py`, `apps/users/tests/test_staging_neutralize.py` |

## 5. Runbook dựng staging (Sprint 2 — hạ tầng, làm tay)

Khuyến nghị **VPS riêng** (nhỏ hơn prod được: 4 vCPU / 8GB / disk ≥ 2× size DB).
Nếu buộc chung VPS prod: compose staging đã tách project/port, nhưng cần cân nhắc RAM.

```bash
# 1) Trên VPS staging
git clone <repo> /home/desi/desi-order && cd /home/desi/desi-order
cp .env.staging.example .env.staging       # điền secrets MỚI (xem chú thích trong file)
docker compose -f docker-compose.staging.yml --env-file .env.staging up -d --build

# 2) Chép dump mới nhất từ prod (hoặc kéo từ offsite S3/rclone)
scp prod:/opt/desi-backup/desi_YYYYMMDD-0200.dump.gz /opt/desi-staging/
scp prod:/opt/desi-backup/desi_YYYYMMDD-0200.dump.gz.sha256 /opt/desi-staging/

# 3) Refresh (lặp lại mỗi lần muốn làm mới dữ liệu — cron tuần hoặc on-demand)
DESI_STAGING_DUMP=/opt/desi-staging/desi_YYYYMMDD-0200.dump.gz \
  ./scripts/staging-refresh.sh

# 4) Nginx host: proxy staging.<domain> → 127.0.0.1:8100 (api) + 8101 (fe),
#    kèm basic-auth hoặc allow-list IP văn phòng. DNS trỏ record mới.
```

Lưu ý vận hành:
- Restore 130GB dạng logical (`pg_restore -j4`) mất cỡ 1–3h tùy disk — chạy đêm.
  DB sau restore sạch bloat nên query sẽ nhanh hơn prod một chút (chấp nhận;
  nếu sau này cần test perf sát thật → chuyển sang copy vật lý volume/pg_basebackup).
- Dữ liệu staging "đứng yên" giữa các lần refresh; mọi thứ tester ghi sẽ bị reset
  ở lần refresh kế tiếp — đây là hành vi chủ đích.
- Muốn tài khoản đăng nhập: user/password giống hệt prod (restore nguyên vẹn).

## 6. Tiến độ sprint

### Sprint 1 — code nền tảng ✅ (2026-07-18)
- [x] Compose staging + env template
- [x] Settings guard staging (fail-fast boot)
- [x] Command `staging_neutralize` + tests
- [x] Script `staging-refresh.sh` (guard chống nhầm prod)
- [x] Design doc + runbook

### Sprint 2 — hạ tầng (cần quyết định VPS, làm tay) ⬜
- [ ] Chốt VPS riêng hay chung máy prod
- [ ] Dựng stack theo runbook §5, chạy refresh lần đầu
- [ ] Nginx + DNS + basic-auth/IP allow-list cho staging.<domain>
- [ ] Cơ chế chuyển dump prod → staging tự động (rclone/scp cron) nếu muốn refresh định kỳ

### Backlog (chưa làm, cân nhắc sau)
- [ ] Hot standby replica (realtime, read-only) kiêm DR — khi công ty lên 100–150 người
- [ ] Copy vật lý volume thay pg_restore nếu cần test performance sát prod
- [ ] Mask PII nếu về sau mở access staging rộng hơn
