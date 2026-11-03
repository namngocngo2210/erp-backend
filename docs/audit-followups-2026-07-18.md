# Audit 2026-07-18 — Plan & nhật ký

Scope: 7 nhánh merge ngày 18/07 (`1eca5f31..5dc1219f`, ~5.2k dòng): zenify-call-transcripts,
staging-environment, import-create-only-missing-invoices, flow-history-transfer-rows,
reconcile-cron-midnight, call-records (empty-state + recording-filter + skipped-label).
Branch: `fix/audit-followups-2026-07-18`. Phần lớn là code của chính các phiên trước —
review đối kháng không nể nang.

**Sạch đáng ghi nhận**: transcripts áp đúng mọi bài học cũ (time limit riêng, lock, state
machine PENDING→PROCESSING→DONE/FAILED, requeue kẹt, scope PII 4 mức); transfer 3-bucket
disjoint theo (movement_type, reference_type), excel không lệch cột; `has_call_recording`
parity đủ mọi endpoint; staging cách ly hạ tầng đúng (network/broker/port riêng, guard boot
trước khi serve).

## CAO — đã fix

### C1. Staging: chìa khoá KiotViet prod sống sót qua neutralize
3 lỗi cộng dồn: bảng `kiotviet_access_tokens` là TextField PLAINTEXT (dump prod mang token
còn hạn ~24h, ai có quyền staging đọc được); `KIOTVIET_API_MODE` là setting CHẾT (không
code nào đọc — "mock mode" là false security); script refresh start web TRƯỚC neutralize
(cửa sổ bắn thật lên KV prod, kể cả cướp webhook). **Fix**: (1) neutralize xoá sạch
`KiotVietAccessToken` + đòi `APP_ENV=staging`; (2) gate cứng `IS_STAGING` trong
`_kiotviet_request` + `_kiotviet_web_request` (chốt chặn THẬT tại choke point); (3) refresh
đổi thứ tự: `compose run --rm` migrate + neutralize TRƯỚC khi start web, fail ở đâu stop
app ở đó; (4) checksum tự dựng dòng check từ basename (file .sha256 ghi path tuyệt đối
phía prod → từng fail MỌI lần chạy); (5) guard env quét PREFIX `LARK_` (danh sách tay từng
sót 2 webhook bot) + cấm `GEMINI_API_KEY`.

### C2. Reconcile 00:00 tạo lỗ biên tháng
`_current_month_window` neo theo `now` → run 00:00 ngày 1 quét tháng MỚI rỗng — cả ngày
cuối tháng (đúng ngày chốt sổ) vĩnh viễn không được đối soát, lặp mỗi tháng. **Fix**: neo
đầu tháng theo `now − 1 ngày` — run ngày 1 phủ trọn tháng cũ, ngày thường không đổi. Áp
cho cả cron trạng thái 30' (cùng helper). Test biên: 00:00 ngày 1/8 → window từ 1/7.

## TRUNG — đã fix

3. **Import deep sweep resurrect đơn đã xoá**: create_only chỉ tra đơn sống → user bulk-
   delete đơn rác là deep sweep 01:30 dựng lại mỗi đêm suốt 31 ngày. Fix: `_soft_deleted_
   order_exists` — bản ghi đã xoá cũng tính "đã tồn tại". Test resurrect.
4. **Deep sweep thua lock với tick 5' là mất cả đêm**: subtask deep giờ `self.retry`
   (5'×6) thay vì bỏ; limit RIÊNG 1800/1860 truyền per-dispatch (mặc định 600/660 size cho
   window ngắn — sort id Asc deterministic nên timeout là chết đúng cùng offset mỗi đêm);
   retry non-200 per-page (backoff 2/5/10s); `except SoftTimeLimitExceeded: raise` trước
   except rộng trong import loop (bài học 14e vá nốt).
5. **Transcripts — lỗi transient đóng đinh FAILED**: `TranscribeError(transient=True)` cho
   mạng/429/5xx → KHÔNG đốt attempt; command `requeue_failed_transcripts` (--days/--dry-run)
   là đường cứu chính thức.
6. **Transcripts — đốt quota trước download + cũ-trước**: quota giờ tiêu SAU download
   thành công (URL chết không đốt RPD); ordering đổi mới-trước (đơn khớp mới nhất ưu tiên)
   — backfill URL hết retention không còn chiếm sạch 200 req/ngày.
7. **Match cuộc gọi giãn dần** (user chốt 18/07): giữ đúng N lượt nhưng backoff
   `0/15'/45'/2h/5h/12h/24h/48h/72h` tính từ created_at — phủ ~3 ngày cho use-case "gọi tư
   vấn trước khi lên đơn" (lịch cũ 7 lượt = 1h45). Không cần migration (gate theo tuổi
   record + attempts). Skip vì chưa tới lượt KHÔNG đốt attempt.
8. **Transfer — pending mất parity với filter mới**: `list_unmatched` giờ nhận
   `product_ids`/`carrier` và ẩn khối (cùng luật với ô tìm SP text) — áp cho cả trang lẫn
   2 export.
9. **Transfer — 2 bucket 2 ngữ nghĩa khi lọc SKU**: `total_returned_quantity` helper chung
   (per-SKU khi có product_ids) dùng ở list/tile/merged/skus — hết cảnh "hoàn A + chuyển B
   = C" hai vế hai luật.

## THẤP — đã fix
- FE `BlockCallRecords`: query lỗi (404 ngoài scope) → ẩn block thay vì khẳng định sai
  "Chưa có cuộc gọi nào".
- Gemini client set timeout 120s (1 request treo không ăn cả tick).
- `carrier_q` label lạ không còn sinh regex rỗng match provider trống.

## Ghi nhận KHÔNG fix / chờ quyết
- **Gemini free tier** (user chốt 18/07: GIỮ) — Google ToS được dùng input cải thiện model;
  audio + transcript là PII khách. Đánh đổi có chủ đích, ghi lại để không re-flag.
- Smoke prod transcripts PHẢI verify chiều gọi RA (caller có phải SĐT khách với callType=1
  không) — matching chỉ dùng `caller`.
- Backfill call-histories khoảng dài: 1 request duy nhất, vendor cap thì mất dữ liệu im
  lặng — nên chunk theo ngày khi chạy backfill tay.
- Zenify sync nuốt ZenifyError thành return êm — alerting tổng vốn đứt (đã ghi nhận audit
  2026-07).
- Flake baseline MỚI ghi nhận: `test_push_koc_has_return_no_cod_and_debt` fail khi chạy
  SAU apps/callcenter (pass khi chạy riêng/чỉ kiotviet) — có sẵn trên master, nghi va chạm
  Redis cache giữa 2 app; chưa truy.

## Nhật ký
- C1+C2+T3..T9+THẤP: XONG. Test mới: staging (token xoá, guard prefix LARK_, non-staging
  refuse), month window biên tháng ×2, soft-deleted skip, transcribe hardening ×4 (transient,
  quota-sau-download, requeue command, backoff không đốt lượt), match tests chỉnh theo backoff.
- Verify: callcenter 37, kiotviet 215 (riêng), batch 5 app 798 pass (3 fail = 2 baseline
  rbac + 1 flake baseline nêu trên); FE tsc sạch + 533 pass (1 fail = modules.test.ts
  baseline đã ghi trong memory).
