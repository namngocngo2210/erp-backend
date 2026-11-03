# Runbook — Triển khai mart hội thoại chat (spec 2026-09-04)

## 0. Điều kiện tiên quyết
- Stack `desi-crawl-conversations` đang chạy (`docker ps | grep deploy-postgres-1`).
- Đã có mật khẩu cho role chỉ-đọc phía DB `crawler`.

## 1. Role chỉ-đọc phía crawler (KHÔNG dùng superuser `crawler`)

> **CẬP NHẬT 22/09/2026 — bước này ĐÃ TỰ ĐỘNG HOÁ, đừng làm tay nữa.**
> `desi-crawl-conversations/deploy/initdb/10-chat-ro.sh` được mount vào
> `/docker-entrypoint-initdb.d` của service `postgres` trong stack `deploy`, nên
> cluster init trên volume rỗng là role tự dựng lại. Điều kiện duy nhất:
> `CHAT_RO_PASSWORD` phải có trong `deploy/.env` và **trùng** mật khẩu trong
> USER MAPPING của foreign server `chat_crawler` bên `desi-postgres`
> (`select umoptions from pg_user_mappings where srvname='chat_crawler'`).
> Thiếu biến thì initdb fail to — cố ý, xem dưới.

**Sự cố 22/09/2026 (lý do tự động hoá).** Volume `deploy_pgdata` bị dựng lại từ
đầu lúc 13:20:30 +07 (`PG_VERSION` mang đúng mốc đó, `docker volume inspect
deploy_pgdata` cũng vậy). Cluster mới = mất SẠCH role. Migration của crawler tự
dựng lại bảng, nhưng `chat_ro` — tạo tay theo đúng bước này — thì **không có ai
dựng lại**. Hệ quả: mọi lượt `sync_hot_marts` chết với `Role "chat_ro" does not
exist`, và `rebuild_all_marts` chết ở mart cuối cùng.

**Vì sao ẩn mất 9 tiếng — nhớ cái bẫy này.** `postgres_fdw` cache kết nối remote
theo backend. Các backend `desi-postgres` đang mở vẫn xài kết nối đã xác thực
từ TRƯỚC 13:20 nên vẫn đọc được bình thường (lượt 21:07 còn nạp 318 dòng
`ai.tin_nhan`). Chỉ khi `desi-order` restart lúc 22:23 mở kết nối mới thì 22:37
mới lộ. **Đừng tin "sync vẫn xanh" ngay sau khi dựng lại DB crawler** — kiểm
bằng cách ép một kết nối FDW mới, đừng nhìn lượt sync kế tiếp.

Chạy tay (chỉ khi cluster đã có sẵn dữ liệu nên initdb không kích hoạt):
```bash
# mật khẩu lấy từ USER MAPPING bên desi-postgres, KHÔNG đặt mật khẩu mới
docker exec -it deploy-postgres-1 psql -U crawler -d crawler
CREATE ROLE chat_ro LOGIN PASSWORD '<trùng user mapping>' CONNECTION LIMIT 5;
ALTER ROLE chat_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE crawler TO chat_ro;
GRANT USAGE ON SCHEMA public TO chat_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO chat_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE crawler IN SCHEMA public GRANT SELECT ON TABLES TO chat_ro;
```
Đổi so với bản cũ: cấp `ALL TABLES` + default privileges thay vì liệt kê tay 6
bảng — partition `messages_yYYYYmMM` sinh thêm mỗi tháng, danh sách tay không
theo kịp.

## 2. Nối mạng docker
`desi-postgres` (mạng `desi-order_default`) và `deploy-postgres-1` (mạng `deploy_default`)
nằm ở hai mạng rời; `deploy-postgres-1` chỉ bind `127.0.0.1:15432` nên KHÔNG đi vòng qua
host được (đo 2026-09-04: hai container ở hai mạng rời, `172.19.0.20` vs `172.18.0.2`).
Chính tiến trình Postgres của `desi-postgres` mở kết nối FDW, nên phải nối container đó:
```bash
docker network connect deploy_default desi-postgres
docker exec desi-postgres getent hosts deploy-postgres-1   # phải ra IP
```
Khai báo cố định trong compose (Task 2 Step 8): service `postgres` trong
`docker-compose.prod.yml` có `networks: [desi-net, deploy_default]`, và cả hai
mạng đều khai `external: true` — nối một lần là sống qua mọi lần `docker compose up`
sau này, không phải làm lại tay mỗi lần recreate container.

> **Bẫy:** Compose validate **TẤT CẢ** network `external` khai trong file lúc khởi
> động, không riêng network của service đang được `up`. Từ MR gắn `deploy_default`
> vào service `postgres`, **BẤT KỲ** `docker compose -f docker-compose.prod.yml up`
> nào cũng đòi network `deploy_default` phải tồn tại trước — kể cả khi chỉ định
> `--no-deps` một service không liên quan gì tới chat. Reboot host, hoặc bring-up
> lại từ đầu trong lúc stack `desi-crawl-conversations` (project `deploy`) chưa lên,
> sẽ chặn TOÀN BỘ stack chính khởi động, không riêng phần chat. Đây là đánh đổi
> CÓ CHỦ ĐÍCH (đổi lấy cầu FDW sống qua `docker compose up`), không phải bug —
> nhưng người vận hành phải biết thứ tự: `deploy` stack lên trước, `desi-order`
> stack lên sau, mọi lần.

## 3. Đặt env và migrate
Thêm vào `.env` prod: `CHAT_CRAWLER_DB_PASSWORD`, `AI_CHAT_EXTRACT_ENABLED=1`.

> **CẬP NHẬT 07/09/2026 — luồng gán nhãn AI đã TẮT trên prod.** Chủ dự án chọn hướng
> "hỏi tới đâu đọc tới đó": trợ lý đọc thẳng `ai.hoi_thoai.noi_dung` của lát cắt đã lọc
> hẹp thay vì gán nhãn trước cả kho. Prod đang đặt `AI_CHAT_EXTRACT_ENABLED=0`; task
> `extract_chat_labels` vẫn được beat phát mỗi 10 phút nhưng trả về ngay
> `{"skipped": true, "disabled": true}` (nên alert `AiChatExtractSilent` vẫn im — nó
> canh lượt chạy THÀNH CÔNG, không canh số nhãn sinh ra).
>
> Trạng thái đóng băng lúc tắt: **6.229/95.051 hội thoại đã có nhãn**, toàn bộ thuộc
> nhóm khách nhắn >=3 tin (hàng đợi chạy theo thứ tự khách nói nhiều nhất trước). Vì
> mẫu lệch nặng, **không được** `GROUP BY` trên các cột nhãn để ra số báo cáo —
> migration `0019_tat_gan_nhan_ghi_chu` đã ghi cảnh báo này vào `COMMENT` của cả 11 cột
> nhãn, là thứ agent đọc qua `ai.huong_dan_bang`.
>
> **Bật lại:** đặt `AI_CHAT_EXTRACT_ENABLED=1` trong `.env` rồi
> `docker compose -f docker-compose.prod.yml up -d worker-slow` (biến env nướng vào
> container lúc tạo, `docker restart` KHÔNG đọc lại `.env`). Hàng đợi còn nguyên
> ~54.7k dòng `pending`, chạy tiếp được ngay. Nếu bật lại thì cân nhắc thêm ngưỡng
> `so_tin_khach >= 3` vào `_ENQUEUE_SQL`: 49.496 dòng `pending` là hội thoại khách chỉ
> nhắn 1-2 tin, chiếm ~90% khối lượng còn lại mà hầu như không có nội dung tư vấn.
```bash
docker compose exec web python manage.py migrate ai_mart
```

## 4. Dựng cầu FDW (cần SUPERUSER)
```bash
docker compose exec web python manage.py ai_mart_chat_fdw
docker compose exec web python manage.py ai_mart_chat_fdw --check
```
`--check` phải in ra số dòng thật (~94.902 hội thoại / ~769.234 tin).

## 5. Nạp lần đầu — VÀ BẤM GIỜ
```bash
docker compose exec web time python manage.py ai_mart_rebuild
```

**Ghi lại wall-clock của lượt này.** Ngân sách hiện tại của task đêm
(`apps/ai_mart/infrastructure/tasks.py`): `soft_time_limit=1500`, `statement_timeout(1200)`
— hai con số này được đặt hồi mart chỉ có 11 bảng nguồn NỘI BỘ, trước khi có hai
bảng chat đọc qua cầu FDW. Rebuild đêm gọi `_select("TRUE")`, tức quét TOÀN BỘ
~95k hội thoại (mỗi hội thoại là 1 outer row cho 2 `LEFT JOIN LATERAL` trên
foreign table) + ~769k tin nhắn qua mạng.

### Số đo THẬT lần deploy đầu (prod, 05/09/2026)

| Bảng | Thời gian |
|---|---|
| `ai.hoi_thoai` | **968s** (95k hội thoại x 2 LATERAL qua FDW) |
| `ai.don_hang` | 789s (có sẵn, không liên quan chat) |
| `ai.khach_hang` | 142s |
| `ai.tin_nhan` | 175s |
| các bảng còn lại | < 60s mỗi bảng |
| **TỔNG** | **2159s (~36 phút)** |

2159s vượt xa trần cũ `soft_time_limit=1500` → job đêm SẼ bị giết giữa chừng.
Đã nới lên `soft_time_limit=4200`, `time_limit=4500`, `single_flight` TTL 5400,
`statement_timeout(2400)`. Thứ tự nạp xác nhận đúng thiết kế: hai bảng chat chạy
CUỐI CÙNG, sau `chi_phi_quang_cao`.

Quan sát thêm: trong lúc rebuild chiếm DB, một lượt `sync_hot_marts` của beat đã
`failed` vì `ai.don_hang` chạm `statement_timeout(180)`. Tự lành ở nhịp sau
(`don_hang 16s ok`) — đúng thiết kế fail-loud, không phải lỗi. Nếu chạy rebuild
tay giữa giờ làm, biết trước là sẽ thấy một dòng `failed` trong `nhat_ky_chay`.

- **> 900s → NÂNG NGAY** `soft_time_limit`, `time_limit` và `statement_timeout`
  trong `apps/ai_mart/infrastructure/tasks.py` (`rebuild_all_marts`) trước khi
  bật beat. 900s là ngưỡng cảnh báo, không phải ngưỡng hỏng: dữ liệu chỉ tăng,
  và một rebuild bị `SoftTimeLimitExceeded` giữa chừng để lại đúng cái hỏng âm
  thầm mà `AiMartRebuildSilent` sinh ra để bắt.
- Chạy lại lần 2 để có số của trạng thái "nóng" (cache của Postgres đã ấm) —
  lấy con số LỚN HƠN trong hai lần làm căn cứ.
- Ghi con số đo được vào MR/issue triển khai, đừng chỉ nhìn rồi bỏ qua.

## 6. Cấp quyền cho agent
`ALTER DEFAULT PRIVILEGES` không phủ bảng do owner khác tạo — PHẢI chạy lại:
```bash
docker compose exec -e HERMES_DB_PASSWORD=... web python manage.py ai_mart_grant_role
```
Kiểm: `hermes_ro` SELECT được `ai.hoi_thoai`, `ai.tin_nhan`, và **bị từ chối** trên `ai_stg.hoi_thoai_nhan`.

## 7. Backfill nhãn theo lô
```bash
docker compose exec web python manage.py ai_mart_chat_extract --limit 500
```
Lặp lại, theo dõi `SELECT extract_status, count(*) FROM ai_stg.hoi_thoai_nhan GROUP BY 1`.

`--enqueue-only` chỉ xếp hàng những hội thoại đủ điều kiện (dài hơn
`AI_CHAT_EXTRACT_MIN_CHARS`, mặc định 80 ký tự) mà không gọi Gemini — dùng để
đo trước số lượng sẽ backfill mà không tốn token. Bỏ `--limit` để chạy tới khi
hàng đợi cạn theo `AI_CHAT_EXTRACT_BATCH` (mặc định 20/lượt gọi lệnh).

Sau khi backfill xong, luồng gán nhãn chạy tự động qua Celery beat mỗi 10 phút
(phút `3,13,23,33,43,53` — lệch pha khỏi `ai-mart-sync-hot` (`7,37`) và hai job
khác cùng queue slow, xem `config/settings_parts/celery.py`).

## 8. Đo độ phủ thật rồi CẬP NHẬT COMMENT (spec §4.7, S4)
```sql
SELECT count(*) FILTER (WHERE don_id IS NOT NULL)::numeric / count(*) FROM ai.hoi_thoai;
```
Ghi con số đo được vào `COMMENT ON COLUMN ai.hoi_thoai.don_id` bằng một data migration mới —
không để agent tự suy độ phủ.

## 9. Rollback
```bash
docker compose exec web python manage.py migrate ai_mart 0013_don_hang_chi_nhanh_nhom_kenh
docker exec desi-postgres psql -U desi -d desi -c "DROP SERVER IF EXISTS chat_crawler CASCADE"
```

## Đã biết / bẫy
- Nguồn trễ ~3 ngày so với thực tế lúc viết spec — kiểm `max(last_message_at)` trước khi kết luận "không có hội thoại".
- Crawler log nội dung tin ra stdout (PII) — nợ riêng, không thuộc phạm vi spec này.
- FDW chết → `sync_hot_marts` **đỏ** (đúng thiết kế C2), `AiMartSyncSilent` nổ sau 90'. Không được thêm try/except để "cho nó xanh".
- **`conversations.updated_at` là mốc thời gian của chính Pancake, KHÔNG PHẢI
  `now()` lúc crawler ghi dòng** (xác nhận Task 2 bằng cả đọc source Go lẫn phân
  bố dữ liệu: giá trị trải trên 637 ngày khác nhau trong khoảng ~651 ngày, không
  dồn cục ở vài mốc giờ crawl chạy). Nên đây VẪN là watermark hợp lệ để lọc
  incremental (`_CANDIDATES` trong `infrastructure/sql/chat_hoi_thoai.py`) —
  không cần đổi thiết kế, chỉ cần biết mốc này KHÔNG đo được độ trễ crawl.
- **`docker network prune` sau khi `down` stack crawler làm SẬP CẢ CỤM `desi-order`.**
  Nếu project `deploy` (crawler) bị `docker compose down` rồi ai đó chạy
  `docker network prune`, network `deploy_default` biến mất. Từ đó `docker compose
  -f docker-compose.prod.yml up` của `desi-order` **thất bại toàn bộ** (không riêng
  phần chat) vì service `postgres` khai `deploy_default` là `external: true` —
  compose validate mọi network external lúc khởi động. Khôi phục:
  ```bash
  docker network create deploy_default   # rồi up lại desi-order như bình thường
  ```
  Cách chuẩn hơn: bring-up lại stack `deploy` trước (nó tự tạo network), rồi mới
  `desi-order`. `docker network create` chỉ là đường thoát nhanh khi crawler
  chưa lên được ngay mà cụm chính phải sống.
- **Mạng compose (chi tiết ở bước 2):** gắn `deploy_default` làm network
  `external` của service `postgres` nghĩa là *mọi* `docker compose up` trên
  `docker-compose.prod.yml` — không riêng phần liên quan chat — đòi network đó
  tồn tại trước. Reboot host hoặc bring-up khi stack `deploy` (crawler) chưa lên
  sẽ chặn cả cụm `desi-order`. Đánh đổi có chủ đích, không phải lỗi cấu hình.
- **Gán nhãn chết âm thầm không tự lộ trên dashboard:** hai bảng `ai.hoi_thoai`/
  `ai.tin_nhan` vẫn nạp bình thường (nguồn từ FDW), chỉ cột nhãn AI đứng im.
  Đây là lỗ hổng `AiMartSyncSilent` không canh được — có alert
  `AiChatExtractSilent` (`monitoring/alerts.yml`, nổ sau 3 giờ không có lượt
  `extract_chat_labels` thành công) nhưng **phạm vi của nó hẹp hơn tên gọi**:
  xem mục "Gán nhãn đứng im nhưng alert không kêu" ngay dưới.
- **Staging KHÔNG được giữ `CHAT_CRAWLER_DB_PASSWORD`** — env này nằm trong
  `STAGING_FORBIDDEN_OUTBOUND_ENVS` (`config/settings_parts/base.py`) vì cầm nó
  là đọc được toàn bộ hội thoại khách thật; `docker-compose.staging.yml` cố ý
  không khai biến này (khác `HERMES_DB_PASSWORD`, vốn có ở cả hai compose).

## Gán nhãn đứng im nhưng alert không kêu

`AiChatExtractSilent` chỉ đếm lượt task **thành công** của `extract_chat_labels`,
nên nó bắt được đúng MỘT thứ: beat không phát task, hoặc worker queue `slow`
chết/kẹt. `run_batch()` trả về summary bình thường — **task vẫn THÀNH CÔNG** —
trong cả bốn tình huống dưới đây, và alert im lặng hoàn toàn:

| Triệu chứng | Nguyên nhân | Kiểm bằng |
|---|---|---|
| `pending` chỉ tăng, không có `done` | `AI_CHAT_EXTRACT_ENABLED` chưa bật | `docker compose exec web python -c "from django.conf import settings; print(settings.AI_CHAT_EXTRACT_ENABLED)"` |
| như trên | `GEMINI_API_KEY` rỗng trong env của **worker-slow** (không phải của `web`) | `docker compose exec worker-slow python -c "from django.conf import settings; print(bool(settings.GEMINI_API_KEY))"` |
| như trên, log worker có "nghỉ … phút vì Gemini hết quota" | cooldown 429 đang bật (`GEMINI_QUOTA_COOLDOWN_MINUTES`) | `docker compose exec web python -c "from django.core.cache import cache; print(cache.get('gemini:chat_extract:cooldown'))"` — khác `None` là đang nghỉ |
| `failed` tăng đều | mọi dòng đều lỗi (model sai tên, schema đổi, hết hạn mức) | đọc `extract_error` ở bảng dưới |

Trạng thái hàng đợi — câu SQL đầu tiên phải chạy khi nghi ngờ:
```sql
SELECT extract_status, count(*) FROM ai_stg.hoi_thoai_nhan GROUP BY 1;
-- và xem lỗi thật (ai_stg KHÔNG cấp cho hermes_ro — chỉ chạy bằng user desi):
SELECT extract_error, count(*) FROM ai_stg.hoi_thoai_nhan
WHERE extract_status = 'failed' GROUP BY 1 ORDER BY 2 DESC LIMIT 5;
```
`done` không tăng qua hai lần đo cách nhau ~30 phút mà `pending` vẫn > 0 = luồng
đang đứng im, bất kể alert có kêu hay không.

Chưa có gauge độ sâu hàng đợi để tự động hoá việc này: cần một exporter metric
nghiệp vụ mà repo chưa có. Nợ có chủ đích, đã ghi trong comment của rule.

## Kiểm chứng cuối

Câu SQL mà spec §10 đòi phải trả lời được sau S2 — chạy trên prod sau bước 5 ở trên:

```sql
SELECT nen_tang_text,
       count(*) AS so_hoi_thoai,
       count(*) FILTER (WHERE so_tin_khach >= 2) AS hoi_thoai_that
FROM ai.hoi_thoai
WHERE bat_dau_ngay >= date '2026-08-01' AND bat_dau_ngay < date '2026-09-01'
  AND NOT do_bot_xu_ly
GROUP BY nen_tang_text
ORDER BY so_hoi_thoai DESC;
```

Và câu trả lời cho hai câu hỏi kinh doanh (sau bước 7):

```sql
-- Khách cần gì
SELECT ai.nhan('nhu_cau', nc) AS nhu_cau, count(*) AS so_hoi_thoai
FROM ai.hoi_thoai h, unnest(h.nhu_cau_ai) AS nc
WHERE h.bat_dau_ngay >= date '2026-08-01' AND NOT h.do_bot_xu_ly
GROUP BY 1 ORDER BY 2 DESC;

-- Khách nói lý do gì vs sale ghi lý do gì (cùng bộ mã — đây là giá trị của C4)
SELECT ld AS ly_do,
       count(*) FILTER (WHERE nguon = 'chat') AS khach_noi_trong_chat,
       count(*) FILTER (WHERE nguon = 'don')  AS sale_ghi_tren_phieu
FROM (
    SELECT unnest(ly_do_khong_mua_ma) AS ld, 'chat' AS nguon FROM ai.hoi_thoai
    WHERE bat_dau_ngay >= date '2026-08-01' AND NOT do_bot_xu_ly
    UNION ALL
    SELECT ly_do_khong_chot_ma, 'don' FROM ai.don_hang
    WHERE ngay_tao_ngay >= date '2026-08-01' AND ly_do_khong_chot_ma IS NOT NULL
) t GROUP BY ld ORDER BY 2 DESC NULLS LAST;
```

> Tên cột ngày của `ai.don_hang` có thể khác `ngay_tao_ngay` — kiểm bằng
> `\d ai.don_hang` trước khi chạy, đừng đoán.

## Việc CỐ Ý không làm

- **Không chấm điểm sale/CSKH** (C5) — nguồn không có `admin_id`, mọi tin gửi ra đều mang id page.
- **Không crawl lại** (C6) — chỉ cần cho mục tiêu đã loại ở C5.
- **Không partition `ai.tin_nhan`** — 769k dòng còn xa ngưỡng ~10 triệu (spec §9).
- **Không sửa crawler** (log PII ra stdout, độ trễ 3 ngày) — nợ riêng, ghi ở trên.
- **Không thêm cột `email`/địa chỉ** — luật 6 cấm, `chat_source` cố ý bỏ `customers.email`.
