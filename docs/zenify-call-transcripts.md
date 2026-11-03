# Ghi âm + transcript cuộc gọi CSKH trên đơn hàng (Zenify → Gemini)

> Trạng thái: **APPROVED 18/07/2026 — đang implement**
> Branch: `feat/zenify-call-transcripts`
> Liên quan: `docs/zenify-webphone-integration.md` (tích hợp Zenify hiện có), app `apps/callcenter`

## 0. Quyết định đã chốt (18/07/2026)

1. **KHÔNG thêm field trên `orders`** — dùng bảng riêng `callcenter_call_records`
   để 1 đơn lưu được NHIỀU url ghi âm + NHIỀU transcript (mỗi cuộc gọi 1 dòng).
2. Model Gemini: **`gemini-flash-latest`** (`gemini-2.5-flash` không còn dùng được);
   dùng **SDK python `google-genai`**.
3. URL file ghi âm Zenify tải trực tiếp, **không cần auth**.
4. Cuộc gọi không khớp được đơn: **dừng sau 7 lần quét** (`match_attempts >= 7`).

## 1. Bối cảnh & mục tiêu

Sale/CSKH gọi khách qua tổng đài Zenify. Zenify cung cấp API lịch sử cuộc gọi
(`GET /v2/external/report/call-histories`) trả về metadata + **URL file ghi âm**.
Hiện ERP không lưu gì về cuộc gọi — muốn xem lại phải vào portal Zenify.

Mục tiêu: cron định kỳ kéo lịch sử cuộc gọi từ Zenify → khớp với đơn hàng → tải
file ghi âm → transcribe bằng **Google AI Studio (Gemini, free tier)** → hiển thị
danh sách cuộc gọi (nghe ghi âm + đọc transcript) trong chi tiết đơn.

**Phạm vi đơn**: chỉ đơn có `order_source` KHÔNG PHẢI `shopee` / `tiktok`
(bao gồm cả đơn `order_source = null` — landing, hotline, messenger, koc, kol...).
Đơn sàn không có SĐT thật (sàn che số) nên không khớp được và cũng không cần.

## 2. Kiến trúc tổng quan

```
┌─────────────┐  cron 15'   ┌──────────────────────────────┐
│ Zenify API   │ ──────────▶ │ Task 1: sync_call_histories   │
│ call-histories│            │ upsert theo callId (dedupe)   │
└─────────────┘             └──────────┬───────────────────┘
                                       │ match caller → Order.phone_normalized
                                       ▼
                            ┌──────────────────────────────┐
                            │ bảng callcenter_call_records  │  (nguồn chân lý:
                            │ 1 dòng = 1 cuộc gọi           │   url + transcript)
                            │ transcribe: pending→done/failed│
                            └──────────┬───────────────────┘
                     cron 5' (quota-guard Redis)
                                       ▼
                            ┌──────────────────────────────┐
                            │ Task 2: transcribe_pending    │
                            │ tải audio → Gemini → transcript│
                            └──────────────────────────────┘
                                       ▼
              API GET /api/callcenter/orders/<id>/call-records → FE chi tiết đơn
```

Vai trò bảng `callcenter_call_records`:

- **1 đơn có N cuộc gọi** (tư vấn, xác nhận, CSKH sau bán) — mỗi cuộc 1 dòng với
  url + transcript riêng (quyết định #1).
- **Dedupe**: cron chạy cửa sổ chồng lấn (chuẩn hệ thống, xem `kv-sync-returns`)
  → unique theo `call_id` để không xử lý trùng.
- **Hàng đợi transcribe**: Gemini free tier giới hạn RPM/RPD → không thể transcribe
  ngay trong lượt sync; cần state machine `pending → processing → done/failed`.
- **Re-match**: cuộc gọi tư vấn thường đến TRƯỚC khi đơn được tạo → lưu record
  `order = null`, cron sau khớp lại (giống pattern `pending_shipment_scans`),
  tối đa 7 lần (quyết định #4).

## 3. Data model — bảng `callcenter_call_records` (app `apps/callcenter` có sẵn)

| Field | Type | Ghi chú |
|---|---|---|
| `call_id` | CharField(100), **unique** | callId Zenify — khoá dedupe |
| `call_type` | CharField(5) | "0" vào / "1" ra |
| `agent_id` / `agent_name` | CharField | agentId / username Zenify |
| `caller` | CharField(30) | SĐT khách (raw từ Zenify) |
| `caller_normalized` | CharField(20) | chuẩn hoá qua `normalize_phone` (customers.phone_utils) |
| `called` | CharField(30) | đầu số tổng đài |
| `recording_url` | CharField(1024) | url file ghi âm (có thể rỗng) |
| `start_time` / `end_time` | DateTimeField null | parse giờ VN → aware |
| `acd_time` | PositiveIntegerField | giây đàm thoại |
| `order` | FK Order, null, SET_NULL | đơn đã khớp (null = chưa khớp) |
| `matched_at` | DateTimeField null | thời điểm khớp |
| `match_attempts` | PositiveSmallInt | số lần quét match; ≥7 thì thôi (quyết định #4) |
| `transcribe_status` | SmallInt choices | 0 pending / 1 processing / 2 done / 3 failed / 4 skipped |
| `transcript` | TextField | text Gemini trả về |
| `transcribe_error` | TextField | lỗi lần cuối (nguyên văn — chuẩn error-passthrough) |
| `transcribe_attempts` | PositiveSmallInt | quá `GEMINI_MAX_ATTEMPTS` → failed |
| `created_at` / `updated_at` | DateTimeField | |

Indexes: `call_id` unique; `(transcribe_status, id)` cho picker Task 2;
`(order, start_time)` cho API theo đơn; partial `(caller_normalized)` where
`order IS NULL` cho vòng re-match.

`skipped` (4) = không có `recording_url` hoặc `acd_time < ZENIFY_CALL_MIN_ACD_SECONDS`
(gọi nhỡ) — lưu record để không kéo lại, nhưng không vào hàng đợi transcribe.

## 4. Luồng 1 — sync lịch sử cuộc gọi (Task `sync_zenify_call_histories`)

- **Beat**: mỗi 15 phút, queue `default` (không chung queue `orders`/`kiotviet` —
  cùng lý do audit 2026-07-14 vòng 5). `time_limit` override ~360s.
- **Cửa sổ**: `from = now - 2h`, `to = now` (chồng lấn, chuẩn `kv-sync-returns`);
  format `YYYY-MM-DD HH:mm` giờ VN (`Asia/Bangkok` — khớp `CELERY_TIMEZONE`).
- **Gọi API**: client `apps/callcenter/call_history.py` dùng `HttpClient`
  (timeout 30), header `api-key`/`api-secret` từ settings `ZENIFY_API_KEY`/
  `ZENIFY_API_SECRET` **đã có sẵn**. Response `status != 200` hoặc `error = true`
  → raise `ZenifyError` nguyên văn message (không nuốt lỗi).
- **Upsert**: mỗi item → get_or_create theo `call_id`; record đã tồn tại chỉ bổ
  sung metadata còn thiếu, KHÔNG đè transcript/status đã done.
- **Khớp đơn** (per-batch, không N+1) — chạy cho record mới + record cũ chưa khớp
  (`order IS NULL AND match_attempts < 7`):
  1. Gom set `caller_normalized` → 1 query
     `Order.objects.filter(phone_normalized__in=..., deleted_at__isnull=True)
     .exclude(order_source__in=["shopee", "tiktok"])`.
  2. Với mỗi cuộc gọi, chọn đơn có `created_at` **gần `start_time` nhất** trong
     cửa sổ ±`ZENIFY_CALL_MATCH_WINDOW_DAYS` (30) ngày. Gần nhất theo
     `|created_at − start_time|` — cuộc gọi tư vấn trước khi lên đơn vẫn khớp.
  3. Không có đơn → `match_attempts += 1`; đủ 7 lần thì các vòng sau bỏ qua.
- **Đơn xoá mềm rồi tạo lại** (19/09/2026): xoá đơn chỉ set `deleted_at` nên
  FK vẫn trỏ đơn xoá và vòng trên không quét tới. Cùng tick,
  `rematch_records_of_deleted_orders()` quét record có `order.deleted_at IS NOT NULL`
  và chuyển sang đơn còn sống cùng SĐT theo đúng luật chọn ở bước 2; không có
  đơn thay thế thì để nguyên (không đốt lượt backoff). Chạy tay/xem trước:
  `manage.py rematch_deleted_order_calls [--dry-run]`.
- **Kill switch**: env `ZENIFY_CALL_SYNC_ENABLED` (default `false` — bật sau khi
  smoke test prod, chuẩn rollout `saved_status_codes` của floating pool).

## 5. Luồng 2 — transcribe (Task `transcribe_pending_call_records`)

- **Beat**: mỗi 5 phút, queue `default`, `time_limit` ~540s (tải audio + Gemini chậm).
- **Quota guard** (Redis, giống pattern login-limit Best Express):
  - `GEMINI_MAX_RPM` (default 8) — counter phút,
  - `GEMINI_MAX_RPD` (default 200) — counter ngày key
    `gemini:transcribe:count:<YYYYMMDD>`; chạm trần thì task dừng, mai chạy tiếp.
- **Mỗi tick**: lấy tối đa N record `pending` (ưu tiên đã khớp đơn, cũ trước),
  `select_for_update(skip_locked=True)` → set `processing`:
  1. Tải file ghi âm từ `recording_url` (không cần auth — quyết định #3); giới
     hạn size ~19MB (trần inline của Gemini).
  2. Gọi Gemini qua **SDK `google-genai`** (quyết định #2): `client.models.
     generate_content(model=GEMINI_TRANSCRIBE_MODEL, contents=[audio_part, prompt])`
     — model default **`gemini-flash-latest`**, prompt "transcribe hội thoại tiếng
     Việt, phân vai Nhân viên/Khách".
  3. Thành công → `transcript`, `status=done`; lỗi → `transcribe_error` nguyên văn,
     `attempts += 1`; quá `GEMINI_MAX_ATTEMPTS` (default 3) → `failed`.
- **Cooldown khi Gemini trả 429** (sự cố 2026-07-22, xem §8): trần local chỉ là
  phỏng đoán — quota thật của free tier thấp hơn nhiều và Google không công bố ổn
  định. Khi `transcribe_audio` bắt `429 RESOURCE_EXHAUSTED`:
  1. **Hoàn** slot local vừa trừ (`_refund_quota` với đúng bucket `_consume_quota`
     đã trả về — request 120s có thể vắt qua ranh giới phút),
  2. bật cờ `gemini:transcribe:cooldown` TTL `GEMINI_QUOTA_COOLDOWN_MINUTES`,
  3. trả cả batch về `pending`, dừng tick — record **không** bị đốt attempt.
  Tick sau thấy cờ thì return ngay **trước khi claim batch**: không UPDATE, không
  tải file. Hết TTL tự probe lại nên không lệ thuộc mốc reset quota của Google
  (00:00 PT, lệch múi giờ app) và không cần thao tác tay.

## 5b. Luồng 3 — extract chân dung khách (Task `extract_pending_call_insights`)

Beat 5', queue `default`, gate riêng `GEMINI_EXTRACT_ENABLED` (KHÔNG dùng chung
`ZENIFY_CALL_SYNC_ENABLED` — transcribe có thể đang chạy mà chưa muốn ghi gì vào
assessment). Đọc transcript đã `done` của record đã khớp đơn (`order_id IS NOT
NULL`) → Gemini structured output (text-only, rẻ hơn audio) → điền các field
chân dung/nhu cầu khách **đang trống** của `OrderAssessment`, đánh dấu trong
`ai_filled_fields` (JSONB) và ghi đúng 1 `OrderJourneyEvent("assessment_ai_filled")`
mỗi lần fill.

Thiết kế đầy đủ (data model, luật merge, cấu hình `GEMINI_EXTRACT_*`, test plan):
`docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md`

## 6. Cấu hình (env mới — `settings_parts/base.py` cạnh block ZENIFY_*)

| Env | Default | Ghi chú |
|---|---|---|
| `ZENIFY_CALL_SYNC_ENABLED` | `false` | kill switch cả pipeline |
| `ZENIFY_CALL_SYNC_WINDOW_MINUTES` | `120` | cửa sổ chồng lấn |
| `ZENIFY_CALL_MATCH_WINDOW_DAYS` | `30` | khoảng cách call ↔ đơn tối đa |
| `ZENIFY_CALL_MIN_ACD_SECONDS` | `5` | dưới ngưỡng = skipped (gọi nhỡ/rớt) |
| `ZENIFY_CALL_MATCH_MAX_ATTEMPTS` | `7` | quyết định #4 |
| `GEMINI_API_KEY` | `""` | rỗng = Task 2 no-op |
| `GEMINI_TRANSCRIBE_MODEL` | `gemini-flash-latest` | quyết định #2 |
| `GEMINI_MAX_RPM` / `GEMINI_MAX_RPD` | `8` / `200` | trần **phỏng đoán**; 429 mới là câu trả lời thật. Sau khi bật billing (27/07) prod dùng `60` / `500` |
| `GEMINI_MAX_ATTEMPTS` | `3` | |
| `GEMINI_TRANSCRIBE_BATCH` | `8` | số record tối đa 1 tick; prod nâng `20` sau khi bật billing |
| `GEMINI_QUOTA_COOLDOWN_MINUTES` | `60` | nghỉ bao lâu sau khi Gemini trả 429 |

Dependency mới: `google-genai` (requirements.txt).

## 7. API / FE

- `GET /api/callcenter/orders/<order_id>/call-records` — list cuộc gọi của đơn,
  sort `start_time` giảm dần. **RBAC**: `require_section("orders")` + record-level
  scope qua `resolved_channel_codes(user, "orders")` (4 mức; OWN = `staff_id == user.id`)
  — transcript là PII, áp bài học audit 2026-07-15b.
- FE: section "Cuộc gọi CSKH" trong chi tiết đơn — mỗi cuộc: thời gian, agent,
  chiều gọi, thời lượng, `<audio>` phát ghi âm + transcript collapse. React Query
  theo convention (`queries.ts` per-feature, typed từ `schema.d.ts`).

## 8. Vận hành / an toàn

- **PII**: transcript chứa thông tin khách → đi theo scope RBAC đơn hàng; không
  log nội dung transcript (chỉ log call_id + status).
- **File ghi âm KHÔNG mirror về MinIO ở phase 1** — lưu URL Zenify. Rủi ro Zenify
  xoá file cũ theo retention của họ → transcript vẫn còn, link chết. Nếu cần lưu
  vĩnh viễn → phase sau tải về MinIO (đã có sẵn hạ tầng).
- **Backfill**: management command `sync_zenify_call_histories --from --to [--dry-run]`
  chạy tay khoảng ngày cũ.
- **Lỗi 1 record không chặn batch** (chuẩn `sync_returns_all_channels`).
- **Sự cố 2026-07-22 — pipeline tự khoá vì quota ảo.** Prod set `GEMINI_MAX_RPD=300`
  nhưng quota thật của free tier thấp hơn nhiều. Counter local incr *trước* khi gọi
  Gemini nên mỗi request ăn 429 vẫn đốt 1 slot → counter chạm 300 hoàn toàn bằng
  request thất bại rồi khoá sạch phần còn lại của ngày: **23/280 file** transcribe
  được, 278 record mang lỗi `429 RESOURCE_EXHAUSTED`. Vì 429 là transient (không
  đốt attempt — đúng chủ ý audit 18/07) nên record quay về `pending` vĩnh viễn,
  hàng đợi không bao giờ hội tụ; đồng thời mỗi tick vẫn claim batch và tải thật 1
  file rồi vứt, **288 lần/ngày**. Fix: refund slot + cooldown + chặn trước khi
  claim (§5). Triệu chứng nhận biết trong log worker: `quota_stopped: True` kèm
  `claimed: 8, done: 0` lặp lại mỗi 5 phút.
- **Giới hạn còn lại (chấp nhận, chốt 22/07):** giữ free tier → thông lượng thật
  chỉ vài chục file/ngày. Backlog cũ nhiều khả năng hết hạn URL Zenify trước khi
  tới lượt; hàng đợi đã ưu tiên đơn-đã-khớp + mới-trước nên cuộc gọi mới không bị
  đói. Muốn dọn hết backlog thì phải bật billing Google AI Studio.
- **Bật billing + drain backlog (27/07/2026).** Sau khi bật billing, nút thắt
  chuyển từ quota Google sang chính cấu hình của mình: `GEMINI_TRANSCRIBE_BATCH=8`
  và xử lý tuần tự khiến một tick tốn 2-3 phút, tick sau gặp lock rồi SKIP →
  thực đo **53 record/ngày** trong khi trần lý thuyết là 96/giờ. Backlog lúc đó
  685 record (100% file trong 30 ngày, 0 record FAILED) sẽ cần ~13 ngày.
  Đường xử lý: command `drain_transcribe` chạy tay nhiều luồng cho tới khi sạch
  hàng đợi, kèm nâng `GEMINI_MAX_RPM` / `GEMINI_TRANSCRIBE_BATCH` cho cron.

  ```bash
  docker exec desi-web python manage.py drain_transcribe --dry-run
  docker exec -d desi-web sh -c \
    "python manage.py drain_transcribe --concurrency 4 --rpm 60 > /tmp/drain.log 2>&1"
  docker exec desi-web tail -f /tmp/drain.log
  ```

  Command dùng chung `process_record` với tick Celery nên mọi quy tắc
  attempt/quota/cooldown giữ nguyên; `SELECT … SKIP LOCKED` khiến nó và beat
  không giành record của nhau. Mỗi record chỉ được thử **một lần** trong một lần
  drain — record lỗi transient quay về `pending` mà claim lại thì vòng lặp không
  bao giờ kết thúc. Thiết kế: `docs/superpowers/specs/2026-07-27-transcribe-drain-command-design.md`.

## 9. Test plan

- Unit (BE): chuẩn hoá + chọn đơn khớp (nhiều đơn cùng SĐT, đơn sàn bị loại,
  ngoài cửa sổ 30 ngày, call trước khi tạo đơn, dừng sau 7 attempts); upsert
  dedupe `call_id`; quota guard chạm trần; state machine transcribe (mock client
  Zenify + Gemini); RBAC endpoint (OWN không thấy đơn người khác).
- Chạy test theo runbook `desi-order-web-test`.

## 10. Sprint plan

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Bảng call_records; client call-histories; Task 1 sync + match; command backfill; unit tests | ✅ xong 18/07 |
| S2 | Gemini client (google-genai) + Task 2 transcribe + quota guard; unit tests | ✅ xong 18/07 |
| S3 | Endpoint list theo đơn + UI chi tiết đơn | ✅ code xong 18/07 — CHƯA smoke prod, `ZENIFY_CALL_SYNC_ENABLED` chưa bật |
| S4 | Fix quota ảo: refund slot khi 429 + cooldown + chặn trước khi claim (§8) | ✅ xong 22/07 — 129 test callcenter+common pass |

Đã làm 18/07: toàn bộ pipeline BE (33 test callcenter pass, ruff sạch) + FE block
"Cuộc gọi CSKH" trong chi tiết đơn (typecheck/eslint sạch). Chưa làm: smoke test
prod với credentials thật (verify URL ghi âm tải được, chất lượng transcript
`gemini-flash-latest`), backfill dữ liệu cũ, bật cron.

Deploy notes: chạy migration callcenter 0001; `pip install google-genai` (rebuild image);
thêm env `GEMINI_API_KEY`; bật `ZENIFY_CALL_SYNC_ENABLED=true` SAU smoke test.
