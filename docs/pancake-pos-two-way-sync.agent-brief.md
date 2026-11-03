# Brief chung cho subagent thực hiện sprint (đồng bộ sản phẩm Pancake POS)

Đọc toàn bộ file này và `docs/pancake-pos-two-way-sync.md` (design + bảng sprint §5 + nhật
ký §6) trước khi làm. Chỉ làm đúng sprint được giao; việc ngoài phạm vi → ghi nhật ký, không sửa.

## Nơi làm
- Chỉ làm trong worktree `/home/desi/desi-order-pancake-orders`, nhánh `feat/pancake-pos-orders-stock`.
  Không `cd` sang repo gốc, không đổi nhánh, không push (người điều phối push), không `git stash` trần.

## An toàn prod (BẮT BUỘC)
- Máy này là **host prod**. KHÔNG ghi vào container `desi-postgres`, KHÔNG exec/restart container
  `desi-*`, KHÔNG đổi `.env`/compose prod.
- Đọc prod DB (chỉ khi cần đo) luôn read-only:
  `docker exec -e PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=60000' desi-postgres psql -U desi -d desi -c "..."`
- **Migration tự chạy khi deploy prod.** Bảng liên quan ở đây đều nhỏ/vừa (`stock_*`,
  `products` ~2,7k dòng) nhưng `stock_movements` là bảng partition: không thêm cột/index vào nó
  trừ khi design yêu cầu. Thêm cột có DEFAULT hằng (PG11+ không rewrite) là an toàn.
- Cấm `docker kill/stop/rm/restart` container không phải của mình. Test chạy với
  `--name ppord-test-<sprint>`; chỉ được stop đúng tên đó.

## Chạy test backend (tuần tự, giới hạn tài nguyên, KHÔNG chạy 2 pytest song song)
```
docker run --rm --name ppord-test-SX --cpus=1.5 --memory=2500m --user 1000:1000 -e HOME=/tmp \
 -e POSTGRES_HOST=172.17.0.2 -e POSTGRES_DB=desi_ppord -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=testpass \
 -e REDIS_HOST=172.17.0.3 -e REDIS_PASSWORD= -e REDIS_CACHE_DB=14 -e APP_ENV=development -e DJANGO_DEBUG=0 \
 -e DJANGO_SECRET_KEY=test-secret-key-stock2-0123456789abcdef -e JWT_SIGNING_KEY=test-jwt-key-stock2-0123456789abcdef0123456789 \
 -e ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= -e PYTHONDONTWRITEBYTECODE=1 \
 -v /home/desi/desi-order-pancake-orders/backend:/app -w /app desi-order-web:latest \
 sh -c "mkdir -p /tmp/prom_multiproc && python -m pytest -p no:cacheprovider -q -ra --create-db <args>"
```
- Chạy file liên quan khi phát triển; cuối sprint chạy full các app bị đụng (thường `apps/inventory`
  + app khác nếu chạm, vd `apps/catalog`, `apps/orders`). `timeout` đủ dài (inventory có thể ~10–20 phút).
- Migration: sau khi sửa model chạy `python manage.py makemigrations --check --dry-run` (cùng env,
  thay lệnh pytest) để chắc state khớp. Tên migration có nghĩa.
- **Trước khi báo xong chạy `bash .githooks/pre-push`** từ gốc worktree (lint-imports + ruff + ratchet
  kiến trúc). Hook đỏ = sprint chưa xong. Nếu ratchet báo tăng baseline hợp lý (file mới), làm theo
  hướng dẫn của script, không nới lỏng bừa.

## Chạy frontend (React + Vite + TS, KHÔNG phải Next)
- Host không có node. Dùng: `docker run --rm --cpus=2 --memory=3g --user 1000:1000 -e HOME=/tmp -v /home/desi/desi-order-pancake-orders:/repo -w /repo/frontend node:22-alpine sh -c "<lệnh>"`
  (mount cả repo; `node_modules` nếu chưa có thì `npm ci` một lần).
- Kiểm: `npx tsc --noEmit -p .`, `npx eslint <files>`, `npx vitest run <files liên quan>`.
- UI dùng shadcn/ui có sẵn trong `src/components/ui`, icon lucide-react, i18n theo `src/i18n/messages/{vi,en}`
  nếu feature xung quanh dùng i18n. Bám phong cách các trang inventory hiện có.

## Code
- Bám `docs/architecture-standard.md` và phong cách xung quanh (comment tiếng Việt, mật độ tương tự).
  Type hint đủ. Không `ruff --fix` hàng loạt. Giữ tương thích ngược API trừ khi sprint yêu cầu.
- Test mới bắt buộc cho mọi thay đổi hành vi, gồm nhánh lỗi và concurrency khi có khoá.

## Commit & tài liệu
- Commit tiếng Anh ngắn (`feat(inventory): ...`), **không** thêm `Co-Authored-By`. Hook pre-commit phải xanh.
- Cuối sprint cập nhật `docs/pancake-pos-two-way-sync.md`: bảng §5 (Trạng thái: ✅ Xong / 🟡 Một
  phần + commit) và §6 Nhật ký (đã làm / chưa làm / lý do / lệch so với design nếu có).
- Trả về người điều phối (≤ 20 dòng): đã làm gì, commit hash, test đã chạy + kết quả, việc chưa làm.

## Riêng dự án Pancake POS
- Spec OpenAPI đầy đủ (1,2 MB, không commit): `/tmp/claude-1000/-home-desi-desi-order/81fe5f4f-2c2f-4d49-839b-e866d55eaa42/scratchpad/pos-openapi.json`. Đọc bằng python/jq theo path, đừng cat cả file.
- **KHÔNG gọi API Pancake thật** (chưa có key). Test dùng mock/fixture. Client cũ tham khảo: `backend/apps/pancake/infrastructure/pancake_client.py` + `apps.common.http_client.HttpClient`.
- Đọc prod DB chỉ khi cần số liệu, luôn read-only (lệnh ở trên).
- Thiết kế đồng bộ sản phẩm (nền đã merge vào nhánh này): `docs/pancake-pos-product-sync.md`.
