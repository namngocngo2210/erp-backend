# Audit followups 2026-07-14 (vòng 2) — Plan & nhật ký

Xử lý findings audit vòng 2 (adversarial review MR `fix/audit-followups-2026-07-14` + 2 MR mới api-docs-prod-gate / best-express-env-account). Branch: `fix/audit-followups-2026-07-14b`.

## CAO

### 1. Đổi/huỷ được vận đơn Best BẤT KỲ qua khoá `Code` (lỗ hổng)
`shipment_service.update_shipment/update_shipment_address` unpack payload passthrough SAU khoá `Code` → client ghi đè mã vận đơn server vừa resolve từ `shipment_id`. NV có `shipments:edit` đổi được COD/địa chỉ nhận của vận đơn NGƯỜI KHÁC (chuyển hướng hàng), và bản ghi nạn nhân không hề đổi nên không để lại dấu vết.
**Fix**: `_without_code()` loại mọi biến thể khoá `Code` (case-insensitive) khỏi input + đặt `Code` do server suy ra ở CUỐI payload. Serializer đổi `JSONField` → `DictField` (chuỗi/list trước đây gây 500 thay vì 400).

### 2. Trừ lại tồn khi hồi sinh đơn trượt ca phổ biến nhất
Guard cũ đòi `delivery_business_status_code == "dispatched"`, nhưng đơn đã xuất kho còn được Sale chuyển sang `received`/`not_received`/`stalled_complaint`/`post_office_lost` — không mã nào hoàn tồn. KiotViet huỷ-xuất lại hoá đơn ĐÃ HOÀN THÀNH chính là đơn đang ở `received` → hồi sinh không trừ lại → **tồn vẫn phồng**, tức mục tiêu MR trước chưa đạt. Ngoài ra đơn trả hàng MỘT PHẦN bị skip trắng (phần chưa trả vẫn ở ngoài kho).
**Fix**: primitive mới `StockSalesService.reissue_after_cancel_release` — đọc thẳng sổ movement: (reversal do HUỶ) − (phần đã bù ở các lần hồi sinh trước), dư > 0 thì ghi SALE bù đúng từng ấy tại đúng kho cũ. Không đoán qua trạng thái giao, tự đúng cho trả một phần / `received` / nhiều chu kỳ huỷ-hồi sinh; reversal của un-dispatch mang note riêng nên không bị trừ nhầm. Idempotent theo sổ.

### 3. `salaries` thiếu trong `VALID_SECTIONS` → admin mất trang lương
`get_allowed_sections` trả `list(VALID_SECTIONS)` cho superuser; MR trước thêm section `salaries` vào rbac_catalog nhưng không thêm vào VALID_SECTIONS → Administrator không có `salaries` trong `allowed_sections` → FE ẩn tab "Cơ chế lương" và chặn route (BE vẫn cho). **Fix**: thêm `"salaries"` vào VALID_SECTIONS.

## TRUNG

4. **Endpoint Best không áp scope dữ liệu** — NV scope OWN/CHANNEL xem/huỷ/in phiếu gửi được MỌI vận đơn công ty (id tuần tự, phiếu gửi chứa PII người nhận). Fix: `_scoped_shipments()` theo chuẩn `_apply_user_channel_scope` (kênh / `Order.staff`), áp cho list + get + cancel + update; `_assert_codes_in_scope` cho `trace`/`print-waybill` (scope ALL không bị chặn để còn tra mã chưa có bản ghi nội bộ).
5. **`best_sync_account` ghi đè phá hoại** — env thiếu `EMS_KEY` → xoá trắng key đang dùng (gãy mọi call Best); đổi username → sinh account thứ 2 active làm `_resolve_account` trả 400 "nhiều account active". Fix: chỉ ghi field env có giá trị; tắt account cũ khác username cùng env; clear token khi password đổi; unique `(username, environment)`.
6. **`ems_key` plaintext** → `EncryptedCharField` + migration mã hoá lại dòng cũ.
7. **Cron retry Best starvation** — event chết (mã lạ) chiếm hết 500 slot mỗi vòng, event mới không bao giờ tới lượt, bảng phình vô hạn (purge chỉ xoá `processed=True`). Fix: bỏ cuộc theo tuổi (`BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS`, default 3) → đánh dấu processed + lý do để purge dọn.
8. **Dedupe event hở khi `modified_at_ms=0`** — constraint có `modified_at_ms > 0` nên event thiếu mốc thời gian không được dedupe (Best retry + mỗi lần `trace()` re-store lại nhân bản). Fix: bỏ điều kiện `> 0` khỏi cả 2 constraint.
9. **Cron 30' `update_invoice_status_targets` đổi status không phát event** — đường sync trạng thái chạy thường trực ở prod; webhook trượt thì kho không biết hoàn/trừ tồn. Fix: publish `OrderStatusChanged` khi status đổi (trong transaction).
10. **Reattach lần 2 (`X` → `X.01` → `X.02`)** — sau lần đầu không đơn nào mang `invoice_code = X` nên candidate lookup trượt → lại tạo dòng đơn trùng. Fix: lookup thêm `reattached_invoice_codes__contains`; lưu vết CẢ mã gốc lẫn mã vừa bị thay; verify huỷ theo mã đang bị thay (không phải mã gốc).
11. **Event KHÔNG-phải-huỷ của mã chết ghi đè đơn sống** — fallback trả đơn kế thừa nhưng guard "chỉ áp huỷ khi invoice_code khớp" nằm TRONG nhánh cancel → sự kiện khác (trạng thái giao, phí, mã vận đơn) của hoá đơn đã chết vẫn được áp. Fix: tách `_find_order_superseding_code` — mã chết thì **bỏ qua item** (không auto-create, không áp dữ liệu), đếm `skipped_superseded`.

## THẤP
12. `codes` giới hạn 500 mã/request. 13. `logout_view` gọi `django_logout` (session Django trước đây sống tiếp cả tuần, đọc được /api/docs sau khi đăng xuất). 14. `/api/v2/openapi.json` vào test gate docs. 15. Export lịch sử nhập/xuất gate `stock:export` (catalog có action này) thay vì chỉ `stock:view`. 16. Test `test_revive_to_cancelled_like_status_does_not_reissue` đặt tên ngược nghĩa với assert → đổi tên đúng hành vi.

## Deploy notes
- **best_express `0003`**: đổi `ems_key` sang mã hoá + RunPython ghi lại dòng cũ. Nếu prod đã chạy `best_sync_account` thì dòng account hiện có sẽ được mã hoá tại chỗ; `from_db_value` fallback nên không có downtime đọc.
- **best_express `0004`**: dựng lại 2 unique constraint (bỏ điều kiện `modified_at_ms > 0`). Bảng gần như rỗng nên không cần CONCURRENTLY; nếu tới lúc deploy đã có dữ liệu trùng ts=0 thì phải dọn trùng trước.
- **users**: `VALID_SECTIONS` thêm `salaries` — không có migration, nhưng sau deploy admin sẽ thấy lại trang Cơ chế lương.
- Env mới (tuỳ chọn): `BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS` (default 3).

## Nhật ký
- CAO 1/2/3 + TRUNG 9/10/11: XONG (commit bed85745 + e9150c16). Test: `test_stock_reissue_on_revive` 10 pass (thêm ca `received`, trả một phần, un-dispatch, idempotency, 2 chu kỳ), kiotviet 188 pass (thêm reattach lần 2 + event mã chết).
- TRUNG 4-8 + THẤP: XONG (commit e9150c16 + kế tiếp). best_express 104 pass (thêm `test_api_security.py`: Code override, scope list/cancel/print). FE 435 pass, tsc sạch, schema regen.
