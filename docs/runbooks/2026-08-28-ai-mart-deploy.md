# Runbook deploy AI Mart (schema `ai`) + chuyển MCP Hermes

Thứ tự BẮT BUỘC: migrate trước, roll code sau (spec §9). Làm giờ thấp điểm.

> **Thứ tự merge:** 3 MR code của Sprint 1-3 xếp chồng — `!1381 → !1382 → !1383`.
> PHẢI merge đúng thứ tự đó (mỗi MR base trên MR trước) rồi mới deploy theo
> runbook này. MR chứa chính runbook này (~`!1384`) không phụ thuộc thứ tự —
> merge lúc nào cũng được, không ảnh hưởng code chạy.

## 0. Chuẩn bị
- [ ] Đo baseline write-path TRƯỚC deploy (so sánh ở bước 5):
      SELECT mean_exec_time, calls FROM pg_stat_statements
       WHERE query ILIKE 'UPDATE "orders"%' ORDER BY calls DESC LIMIT 5;
      Ghi số ra giấy. (pg_stat_statements TÍCH LUỸ — chỉ so mean trước/sau cùng
      khoảng giờ, bài học đo MR !1205.)
- [ ] Đo baseline HOT update TRƯỚC deploy (so sánh ở bước 5). Trigger dw_updated_at
      ghi vào một cột CÓ ĐÁNH INDEX trên mỗi UPDATE — Postgres không thể làm HOT
      update khi có index trên cột bị đổi, nên tỷ lệ HOT update trên orders/
      order_items sẽ TỤT sau khi trigger lên. mean_exec_time ở trên quá nhiễu để
      thấy hiệu ứng này (chi phí thật là write amplification + bloat, không phải
      latency một câu UPDATE); dùng thẳng số liệu autovacuum:
      SELECT relname, n_tup_upd, n_tup_hot_upd,
             round(100.0*n_tup_hot_upd/NULLIF(n_tup_upd,0),1) AS hot_pct
        FROM pg_stat_user_tables WHERE relname IN ('orders','order_items');
      Ghi hot_pct ra giấy cùng với mean_exec_time.
- [ ] `git pull` trên prod. KHÔNG `docker compose pull` cả cụm (bẫy kéo image core
      — memory prod-compose-recreate-gotchas).

## 1. Migrate (code cũ vẫn chạy — cột/trigger mới vô hại với code cũ)
- [ ] docker compose -f docker-compose.prod.yml exec web python manage.py migrate ai_mart
- [ ] Kiểm: SELECT count(*) FROM ai.tu_dien_ma;  -- phải > 100

  ⚠ **`ALTER DEFAULT PRIVILEGES` chỉ áp cho role đã chạy migrate.**
  `ai_mart_grant_role` (bước 4) gọi `ALTER DEFAULT PRIVILEGES IN SCHEMA ai
  GRANT SELECT ON TABLES TO hermes_ro` KHÔNG có `FOR ROLE ...` — mệnh đề này
  chỉ áp dụng cho bảng do **role hiện tại đang chạy lệnh** tạo ra sau này,
  không phải "mọi role". Nếu một migration sau này thêm bảng `ai.*` bằng một
  role Postgres khác với role đã chạy `ai_mart_grant_role` lần đầu, bảng mới
  đó sẽ KHÔNG tự động được `hermes_ro` đọc. Sau BẤT KỲ migration nào thêm
  bảng `ai.*`, phải re-run `ai_mart_grant_role` (idempotent, an toàn chạy
  lại) hoặc chạy tay:
  ```sql
  GRANT SELECT ON ALL TABLES IN SCHEMA ai TO hermes_ro;
  ```

## 2. Roll code (web + worker-slow — CHƯA đụng beat)
- [ ] Build/restart web, worker-slow — TỪNG service một, không recreate hàng
      loạt (bẫy trùng tên container). Image worker phải cùng digest với web
      (sự cố image lệch per-service 2026-08-03).
      ⚠ **CỐ Ý chưa restart `beat` ở bước này** — xem lý do cuối bước 3 (M6).

## 3. Initial load + kiểm chất lượng
- [ ] docker compose ... exec web python manage.py ai_mart_rebuild
      (ghi lại thời lượng — nếu > 10 phút là chạm ngưỡng Bậc 1 của spec §9 sớm)
      ⚠ Sự cố 28/08: kết nối Django prod có `statement_timeout` mặc định 30s
      (`DB_STATEMENT_TIMEOUT_MS`), lệnh bị cắt giữa INSERT `ai.don_hang`. Từ
      MR hotfix, lệnh tự nới lên 3600s (`--timeout N` giây để đổi; <= 0 là GIỮ mặc định 30s, không phải tắt). Với
      image CŨ chưa có hotfix: `exec -e DB_STATEMENT_TIMEOUT_MS=0 web ...`.
      Nếu rebuild > 15 phút thì task đêm (trần 1200s) cũng sẽ đỏ — phải tối ưu
      `ai.nhan()`/CTE trước khi bật beat.
- [ ] docker compose ... exec web python manage.py ai_mart_sync
      (chạy tiếp NGAY sau rebuild, ghi lại thời lượng của lượt sync này riêng —
      đây là con số sẽ lặp lại mỗi 30 phút qua Celery, không phải thời lượng
      rebuild ở trên). Các CTE của hot mart (items/calls/needs cho đơn hàng;
      agg/gaps cho khoảng trống dữ liệu) hiện quét TOÀN BỘ bảng gốc ở mỗi
      lượt sync incremental, không lọc theo id ứng viên — chấp nhận được khi
      dữ liệu còn nhỏ, nhưng là chi phí sẽ tăng theo thời gian. Nếu lượt sync
      này vượt ~2-3 phút, bước tối ưu tiếp theo là thu hẹp các CTE đó theo tập
      id ứng viên trước khi join full table (note follow-up — spec §9 định
      nghĩa ngưỡng báo động chính thức là 10 phút, mốc 2-3 phút ở đây chỉ là
      cảnh báo sớm để chủ động tối ưu trước khi chạm ngưỡng đó).
- [ ] Ghi lại wall time của lượt ai_mart_sync ở trên riêng ra giấy. Nếu > 60
      giây NGAY TRONG NGÀY DEPLOY (không phải một mốc tương lai xa) — mở MR
      thu hẹp CTE theo tập id ứng viên (CTE-scoping, note ở bullet trên) TRƯỚC
      KHI trỏ MCP Hermes vào schema ở bước 4. Đừng để Hermes bắt đầu đọc một
      schema mà mỗi lượt sync đã chậm sẵn từ ngày đầu.
- [ ] docker compose ... exec web python manage.py ai_mart_verify
      → Nếu có mã chưa nhãn (vd invoice_status lạ): HỎI user nghĩa của mã,
      thêm vào domain/dictionary.py + data migration ở MR sau. KHÔNG đoán —
      kể cả khi mã trông "rõ nghĩa" (vd đoán 4=huỷ, 5=nháp): sai một nhãn ở
      đây là Hermes trả lời sai cho người dùng cuối mà không ai biết.
- [ ] SELECT * FROM ai.tinh_trang_du_lieu;  -- mọi bảng "Bình thường"
- [ ] Bây giờ mới restart `beat` (M6). Lý do đảo thứ tự so với trực giác "roll
      hết code rồi mới chạy lệnh tay": nếu `beat` đã sống từ bước 2, lịch sync
      30 phút của nó có thể bắn `sync_hot_marts` TRƯỚC khi initial load
      (`ai_mart_rebuild` ở trên) chạy xong. `sync_hot()` fail cứng khi chưa có
      watermark `__hot__` (`EtlFailed("chưa có watermark __hot__ — chạy
      rebuild_all trước")`) — task đó fail thật, và alert `CeleryTaskFailing`
      (group `celery`) sẽ bắn NGAY TRONG LÚC ĐANG DEPLOY, gây báo động giả làm
      nhiễu người trực. Restart beat sau khi bước 3 đã có watermark thì
      sync_hot_marts đầu tiên của nó luôn có nền để đắp lên.

## 4. Role + MCP
- [ ] ⚠ **KHÔNG BAO GIỜ chạy `ai_mart_grant_role` khi `DEBUG=True`.** Lệnh này
      chạy `ALTER ROLE hermes_ro WITH LOGIN PASSWORD <mật khẩu thật>` — một
      câu SQL thường (utility statement), không phải tham số bind ẩn. Với
      `DEBUG=True`, Django query logging (và mọi APM/log tầng dưới bật theo
      DEBUG) sẽ ghi nguyên văn câu SQL này ra log, tức là ghi thẳng mật khẩu
      hermes_ro ra file log dạng plain-text. Xác nhận `DEBUG=False` trên
      container trước khi chạy lệnh dưới đây (prod compose đã set sẵn, nhưng
      double-check nếu chạy qua override/debug shell):
      docker compose ... exec -e HERMES_DB_PASSWORD='<sinh mật khẩu mạnh>' web \
        python manage.py ai_mart_grant_role
- [ ] Thử bằng psql: đăng nhập hermes_ro, xác nhận:
      SELECT count(*) FROM ai.don_hang;                  -- OK
      SELECT count(*) FROM orders;                        -- PHẢI permission denied
      SELECT count(*) FROM ai_meta.nhat_ky_chay;          -- PHẢI permission denied
      INSERT INTO ai.tu_dien_ma VALUES ('x','x','x');     -- PHẢI read-only
- [ ] **Ngoại lệ PII** (C1): mọi cột `*_text`/`*_ma` trong schema `ai` đều đã che
      SĐT, NHƯNG `ai.cuoc_goi.transcript` và `ai.cuoc_goi.tom_tat_ai` là văn bản
      thô CHƯA che — khách đọc SĐT/địa chỉ thành tiếng trong cuộc gọi, và bản
      tóm tắt AI có thể lặp lại. Đây là ngoại lệ CÓ CHỦ ĐÍCH của luật che PII
      (spec §4.6, cùng chuẩn với API transcript MR !1377) vì Hermes cần nguyên
      văn để trả lời câu hỏi về nội dung cuộc gọi. Không tự ý che thêm 2 cột
      này, và Hermes KHÔNG được trích SĐT/địa chỉ từ đó ra câu trả lời cho
      người dùng cuối.
- [ ] Đổi connection string của MCP `prod-db` phía Hermes sang user hermes_ro
      (database desi, search_path=ai). Nhắc user cập nhật cấu hình MCP.

      MCP config trỏ vào role `hermes_ro`, database `desi`, và PHẢI set
      `options=-c search_path=ai` trong connection string/params — có
      search_path=ai thì Hermes gọi tên bảng KHÔNG cần schema-qualify (vd
      `SELECT * FROM don_hang` tự resolve ra `ai.don_hang`); thiếu cờ này
      Hermes phải viết `ai.don_hang` mọi nơi hoặc mọi query trần sẽ lỗi
      "relation does not exist" vì search_path mặc định của role không có
      `ai`.

## 5. Hậu kiểm
- [ ] Đo lại write-path (câu ở bước 0) sau ≥1 giờ chạy — mean tăng >5% thì báo.
- [ ] Đo lại HOT update (câu ở bước 0) sau ≥1 giờ chạy:
      SELECT relname, n_tup_upd, n_tup_hot_upd,
             round(100.0*n_tup_hot_upd/NULLIF(n_tup_upd,0),1) AS hot_pct
        FROM pg_stat_user_tables WHERE relname IN ('orders','order_items');
      So hot_pct với số ghi ở bước 0. hot_pct sụt là DỰ KIẾN (trigger ghi index
      column trên mọi UPDATE) — cái cần theo dõi là autovacuum/bloat trên
      `orders` có tăng bất thường không (SELECT relname, n_dead_tup,
      last_autovacuum FROM pg_stat_user_tables WHERE relname = 'orders';).
      Nếu hot_pct sụt sâu VÀ bloat/autovacuum tăng rõ rệt: cần lực đòn bẩy
      rollback — `DROP INDEX orders_dw_updated_idx`. Đánh đổi: candidate query
      của ETL (lọc theo dw_updated_at) chuyển sang seq-scan ~547k dòng, ước
      ~1s mỗi lượt sync 30 phút — CHẤP NHẬN ĐƯỢC so với việc để write path
      chính (tạo/sửa đơn) tiếp tục bloat. Đây là lever rollback thực dụng, ưu
      tiên trước khi động vào trigger.
- [ ] Prometheus: bẫy bind-mount inode (memory `prometheus-alerts-bind-mount-inode-trap`) —
      reload/restart thường KHÔNG ăn rule mới sau `git pull`. Sau khi pull, bắt
      buộc force-recreate:
      docker compose -f docker-compose.prod.yml up -d --force-recreate --no-deps prometheus
      Sau đó xác nhận group `ai_mart` (rule `AiMartSyncSilent` + `AiMartRebuildSilent`,
      I2) đã lên bằng cách mở `http://<prod-host>:9092/rules` (qua SSH tunnel —
      xem memory `prod-vps-topology`) và tìm group `ai_mart` trong danh sách.
- [ ] Đối chiếu số: SUM(tong_sau_giam) WHERE la_don_gtc AND ngay_tao_ngay trong
      tháng hiện tại vs Báo cáo tổng trên CRM — lệch phải giải thích được
      (mốc thời gian refresh, đơn mirror).

## Rollback
- Trigger/cột dw_updated_at vô hại với code cũ — rollback code là đủ, KHÔNG cần
  migrate ngược. Muốn gỡ hẳn: python manage.py migrate ai_mart zero (schema ai
  bị DROP CASCADE — Hermes mất dữ liệu đọc, gỡ MCP trước).
