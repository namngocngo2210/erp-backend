# Runbook: công cụ chạy tay (management command không có cron)

Audit 2026-09-23 (P2-6) tìm thấy 8/85 command không ai gọi: 6 đã xoá/gộp (xem
`docs/runbooks/2026-09-23-audit-ops.md` mục B2), 2 là công cụ vận hành thật nhưng không
có tài liệu — ghi ở đây để không bị xoá nhầm lần audit sau. Chạy trong container web:
`docker compose -f docker-compose.prod.yml exec web python manage.py <lệnh> ...`
(staging: `docker-compose.staging.yml`).

## 1. `gads_refresh_token` — lấy Google Ads OAuth refresh token (1 lần / khi token bị thu hồi)

Khi nào dùng: dựng tài khoản Google Ads mới, đổi MCC, hoặc log worker báo
`invalid_grant`/`Token has been expired or revoked` ở task đồng bộ chi tiêu Google Ads.

```bash
python manage.py gads_refresh_token \
  --client-id="xxx.apps.googleusercontent.com" \
  --client-secret="yyy"
```

- OAuth client phải là loại **Desktop app** (luồng copy-paste code, `redirect_uri=http://localhost`).
- Lệnh in ra một URL → mở bằng trình duyệt, đăng nhập đúng email đã tạo MCC, bỏ qua
  cảnh báo "unverified" (Advanced → Go to app), đồng ý quyền. Trình duyệt chuyển tới
  `http://localhost/?code=...` (trang lỗi là bình thường) → copy giá trị `code` trên thanh
  địa chỉ, dán vào terminal.
- Kết quả: refresh token in ra stdout. Dán vào trường **Refresh Token** khi thêm/sửa tài
  khoản Google Ads trên UI (lưu mã hoá ở `GoogleAdsAccount.refresh_token`, không phải env).
  KHÔNG dán token vào ticket/chat.

## 2. `diagnose_lead_assignment` — vì sao một đơn không được chia lead tự động

Khi nào dùng: sale/CSKH báo "đơn về mà không ai được chia". Lệnh in mọi yếu tố mà
`LeadAssignmentService.assign_if_unassigned` dựa vào (kênh, ca trực, blocklist, claim,
floating pool, đơn trùng SĐT...) và suy ra lý do skip — chỉ đọc, không ghi DB.

```bash
python manage.py diagnose_lead_assignment --phone 0363115097 0902391292
python manage.py diagnose_lead_assignment --order-id 12345 12346
python manage.py diagnose_lead_assignment --phone 0363115097 --date 2026-07-07   # lọc thêm ngày tạo
```

Đọc output theo từng đơn: dòng `skip:` là kết luận; các dòng trên là bằng chứng. Sửa
cấu hình (ca trực/kênh/blocklist) qua UI rồi chạy lại để xác nhận.

## 3. `sync_invoice_prices` — đồng bộ lại tiền hoá đơn KiotViet về đơn (gộp 2 command cũ)

Gộp `sync_invoice_amounts` (2026-04) + `sync_invoice_item_prices` (2026-09) — khung chung
`apps/kiotviet/infrastructure/services/invoice_price_sync.py` (`InvoicePriceSyncer`).
Khi nào dùng: tiền trên đơn lệch KiotViet sau đợt sửa giá/giảm giá theo dòng, hoặc import
cũ thiếu giá theo dòng hàng.

```bash
python manage.py sync_invoice_prices --kiotviet-channel-id <KV_CHANNEL_ID>   [--target amounts|items|both] [--dry-run] [--limit N]
```

- `--target amounts`: `Order.total_amount` + `total_after_discount` := `Total` hoá đơn KiotViet
  (bỏ qua nếu Total ≤ 0 hoặc đã bằng).
- `--target items`: `OrderItem.unit_price` := `invoiceDetailTaxs[0].priceAfterTax` (fallback
  `price`) khớp theo SKU; `line_total = (unit_price − discount) × quantity`.
- `--target both` (mặc định): cả hai, gọi KiotViet 1 lần/đơn.
- `--dry-run` chỉ in `[DRY-RUN]`; `--limit 0` = tất cả. Chỉ duyệt đơn còn sống có `invoice_code`
  của kênh, theo id tăng dần; lỗi từng đơn đếm vào `errors`, không dừng lô; cuối in summary.
- Luôn chạy `--dry-run --limit 20` trước trên prod. Test: `apps/kiotviet/tests/test_sync_invoice_prices_command.py`.

## 4. `backfill_ads_campaign_products --provider facebook|google`

Gộp `backfill_fb_campaign_products` + `backfill_google_campaign_products` (2 tên cũ còn là
alias mỏng, deprecated). Chi tiết ở `docs/ads-campaign-product-mapping.md` §7b.
