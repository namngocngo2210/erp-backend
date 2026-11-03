# Audit 2026-07-15 — Plan & nhật ký

Scope: adversarial review 5 feature merge vào master trong ngày 15/07 (`fb32b398..72d0ba82`):
phone-carrier-display, customer-type-column, kiotviet-branch-sync-map, zenify-sdk callcenter
v2, category-product-select-sku-only. Branch: `fix/audit-followups-2026-07-15`.

**Kết quả: KHÔNG có lỗi CAO ở bất kỳ feature nào.** phone-carrier và customer-type sạch cả
CAO lẫn TRUNG (mapping FE/BE giống hệt, báo cáo 1 query aggregate; customer-type self-heal
qua cron reconcile 2h). Fix 4 TRUNG + 1 THẤP dưới đây.

## Đã fix

### #1 [TRUNG] category-sku — mất tìm kiếm theo TÊN sản phẩm
`ProductCategoriesPage.tsx`. Commit SKU-only đổi option từ `{label: name, code: sku}` sang
`{label: sku||name}` bỏ hẳn `code` → `MultiSelect` filter theo label(=sku)+code(=undefined)
nên gõ TÊN sản phẩm không ra kết quả (chỉ tìm được bằng SKU). **Fix**: thêm trường search
ẩn `keywords` vào `MultiSelect` (tham gia filter, không hiển thị) + set `keywords: p.name`.
Giữ nguyên mục tiêu "chỉ hiện SKU".

### #2 [TRUNG] branch-sync-map — map "mồ côi" khi thu hẹp branch_ids
`channel_serializers_impl.py`. `pairing_touched` không tính `branch_ids` → admin PATCH chỉ
`{branch_ids: [...]}` bỏ một kho mà không gửi lại map → map cũ còn nguyên → reconcile vẫn
sync kho đã gỡ sang kênh cũ. **Fix**: thêm `"branch_ids"` vào `pairing_touched`. Khi đó map
instance được re-validate theo branch_ids mới: kho bị gỡ đang được map → raise buộc admin
sửa; map không đụng kho đó → pass im lặng (không đổi gì).

### #3 [TRUNG] callcenter — auto-recover cắt cuộc gọi đang nói
`WebphoneProvider.tsx`. Timer làm mới token theo TTL gọi `recover()` (remount iframe → hủy
connection SDK) bất kể trạng thái → chạm mốc TTL giữa cuộc gọi dài là rớt máy. **Fix**:
`recoverWhenIdle` — đang có cuộc gọi thì đặt cờ `pendingRecoverRef`, effect theo dõi
`callMachine.status` chạy recover ngay khi về idle.

### #4 [TRUNG] callcenter — hangup() kẹt panel "đang gọi"
`WebphoneProvider.tsx` + `webphone-call-state.ts`. `hangup` chỉ gửi `terminate` rồi chờ SDK
echo `end`; SDK không echo / echo trễ / echo callId lệch (đa tab) → panel kẹt in-call, nút
Tắt máy như vô hiệu. **Fix**: thêm action `hangup` reset LẠC QUAN về idle (khớp pattern
optimistic của dial/mute). Echo `end` tới sau gặp `state.call=null` nên no-op.

### #5 [THẤP] callcenter — refreshToken churn timer TTL
`queries.ts`. `useRefreshWebphoneToken` trả hàm mới mỗi render → `recover`/timer TTL
clear+set lại liên tục. **Fix**: bọc `useCallback`.

## Ghi nhận — KHÔNG fix đợt này
- **callcenter T3 [note kiến trúc]**: iframe same-origin nạp script `cdn.zenify.biz` không
  SRI/sandbox — nếu CDN bị chèn mã sẽ chạy với đặc quyền same-origin của ERP (đọc cookie/JWT,
  gọi /api). Kế thừa từ v1. Fix bị giới hạn vì `sdk.min.js` vendor hay đổi (SRI sẽ vỡ); cần
  bàn riêng (pin version + SRI cho script ổn định, hoặc tách sang subdomain khác origin).
- **branch-sync-map [THẤP]**: branch có trong branch_ids nhưng chưa map bị bỏ reconcile trong
  khi real-time vẫn push (bất đối xứng, tiền-tồn-tại). Cân nhắc tự bổ sung cặp fallback.
- **phone-carrier [THẤP]**: "Tổng data" theo nhà mạng đếm ĐƠN (`Count("id")`), không phải số
  khách — nhất quán với family báo cáo sale. Câu hỏi nghiệp vụ, không phải bug.

## Câu hỏi nghiệp vụ treo (từ các đợt trước + hôm nay)
1. Hoàn MỘT PHẦN (quét luôn nạp full SL đơn).
2. Nhắc chấm công DM cả Chủ nhật — công ty có làm cuối tuần?
3. "Tổng data theo nhà mạng" = đếm đơn (hiện tại) hay đếm khách?

## Nhật ký
- #1–#5: XONG. Test mới: reducer `hangup` optimistic + late-end no-op (2 test FE); shrink
  branch_ids-only re-check map (2 test BE). Verify: FE 457 pass + tsc sạch; BE channels_core
  48 pass.
