# CNV Loyalty sync — implementation plan

Spec: `docs/superpowers/specs/2026-08-05-cnv-loyalty-sync-design.md`. Branch: `feat/cnv-loyalty-sync`.

## Sprint 1 — nền móng app + client (hoàn thành mới sang sprint 2)

- [x] T1. Skeleton `apps/cnv_loyalty` (apps.py ready() đăng ký subscriber, exceptions, urls) +
      `INSTALLED_APPS` + `.importlinter` tầng trên + env vars trong `base.py` + `.env.example`
      + `CNV_SSO_CLIENT_SECRET` vào staging blocklist.
- [x] T2. `models.py` (`CnvConnection`, `CnvSyncRecord`) + migration `0001_initial`.
- [x] T3. `infrastructure/sso_client.py` (authorize URL, exchange, refresh, verify) +
      `token_provider.py` (re-read DB, refresh rate-limit Redis) + tests.
- [x] T4. `infrastructure/cnv_client.py` phủ đủ resource + 401 force-refresh retry +
      staging guard + tests.

## Sprint 2 — mapping + sync service + trigger

- [x] T5. `domain/mappers.py` (tách tên VN, customer/product/order/collection payload,
      bảng trạng thái) + tests đủ 14 OrderStatus.
- [x] T6. `application/services/sync_service.py` (+dependency resolution, outbox update) + tests
      với FakeClient.
- [x] T7. `subscribers.py` (post_save Order/Customer/Product + bus `orders.status_changed`,
      gate `CNV_SYNC_ENABLED`) + `tasks.py` sync tasks + Celery routes queue `slow` + tests.

## Sprint 3 — retry/reconcile + backfill + transport

- [x] T8. `tasks.py`: `retry_failed_records` (backoff 2^attempts phút, trần 10 lần) +
      `reconcile_full` + beat entries + single_flight + tests.
- [x] T9. `application/services/backfill_service.py` (keyset theo id, thứ tự
      collections→products→customers→orders) + command `cnv_backfill` + tests.
- [x] T10. `transport/api_views.py` connect/callback/status (admin-only, callback AllowAny
      + state một lần) + urls + tests.
- [x] T11. Test + ruff pass; import-linter chạy ở CI (image test không có); push + MR.

## Tiến độ

- 2026-08-05: spec duyệt, code xong cả 3 sprint trong một phiên. Test: 71/71 app mới pass,
  172 test lân cận (orders event publisher, order create API, warranty) pass, ruff sạch.

## Ghi chú sau sprint

**Đã làm:** toàn bộ T1-T11; thêm ngoài plan: `cnv_sync_connection` command (upsert connection
từ env, pattern best_sync_account) + admin.py đăng ký 2 model.

**Chưa làm / follow-up:**
- Verify với credentials thật: PUT /orders CNV có hỗ trợ không, GET vs POST /oauth/token,
  path chi tiết collections (spec §6) — đối chiếu Swagger khi CNV cấp client.
- UI quản trị frontend cho nút "Kết nối CNV" + bảng trạng thái sync (hiện dùng API trực tiếp).
- import-linter xác nhận ở CI pipeline của MR.

**Cập nhật 2026-08-05 (chiều):** đã thêm passthrough `CNV_*` vào cả `docker-compose.local.yml`
lẫn `docker-compose.prod.yml` (staging CHỦ ĐÍCH không thêm — anchor env staging là whitelist,
secret CNV bị blocklist fail-fast). Đã dựng stack dev local từ worktree này (port 8080,
migrate + seed xong) để thử luồng CNV khi có credentials.

