# ERP Responsive (Mobile) — Design Spec

- **Ngày:** 2026-06-16
- **Branch:** `feature/erp-responsive`
- **Chuẩn kèm theo:** `docs/responsive-standard.md`

## Mục tiêu
Toàn ERP dùng được trên điện thoại (~390px) + hoàn thiện mobile app `/m`, không phá density desktop. Làm theo 5 phase, mỗi phase review trước khi sang phase sau.

## Bối cảnh từ audit (thực trạng 390px)
Đã ổn: RootLayout/ShellChrome, sidebar→drawer, hamburger, charts (ResponsiveContainer), OrderFormDialog/EmployeeFormDialog (FormGrid), nhiều bảng có scroll ngang. Đã fix iOS input-zoom + camera đen ở branch `fix/mobile-responsive-camera`.

Vỡ thật (danh sách hữu hạn):
- **Grid không co**: `CustomerFormDialog.tsx:66,87,104` (grid-cols-4); `components/ui/date-filter.tsx:262` (grid-cols-5) + `:293` (grid-cols-2); `ResaleTrackingPage.tsx:1117`; `components/orders/Sheet3Section.tsx:173`; `SyncPage.tsx:492`.
- **Bảng scroll-only chưa có card mobile**: Customers, HR Employees, Stock, Google/Facebook Ads, Inventory Returns, AuditLogs. (Orders 40+ cột: giữ scroll.)
- **Dialog/dropdown**: form dialog mobile nên full-screen; `TopNav` notifications `w-80` cap width; `KshipDeliveryDialog:247` bảng `min-w-[1220px]` (đã trong overflow — chấp nhận).
- **/m ESS**: cần pass hoàn thiện spacing/safe-area/trạng thái.

## Quyết định
- Mobile = `< md (768px)`. Mobile-first bắt buộc.
- Bảng list chính → card-view qua `ResponsiveList`; Orders giữ scroll.
- Form dialog full-screen trên `< sm`.
- Không đổi density desktop.

## Phase
- **P0** — Primitive + chuẩn: `ResponsiveList`, doc `responsive-standard.md`, full-screen form dialog ở `dialog.tsx`.
- **P1** — Sửa toàn bộ grid không co (nhóm trên).
- **P2** — Dialog mobile polish (full-screen áp dụng) + cap width dropdown TopNav + rà dialog lẻ.
- **P3** — Card-view cho bảng: Customers, HR Employees, Stock, Ads, Inventory Returns, AuditLogs (mỗi module 1 bước).
- **P4** — Hoàn thiện `/m` ESS.

Mỗi phase: subagent thực hiện (TDD/verify) + 2 vòng review (spec, quality). Toàn bộ trên branch `feature/erp-responsive`, commit theo phase.

## Test
- FE: build + tsc + vitest xanh sau mỗi phase. Test component card-view (render từ mock).
- Thủ công DevTools 390px cho từng màn sửa.
- Lưu ý baseline có test FE fail sẵn (memory `known-failing-baseline-tests`) — không nhầm regression.

## Trạng thái: ✅ Hoàn thành (P0–P4)
Branch `feature/erp-responsive`. Build + tsc + 46 test (25 file) ở vùng đụng tới đều xanh. Review tích hợp cuối: không lỗi nghiêm trọng.

### Nhật ký
- **P0**: `ResponsiveList` (table↔card theo md, có test) + form dialog full-screen `< sm` (`dialog.tsx`).
- **P1**: vá grid không co — CustomerFormDialog (grid-cols-4 → 1 cột, label canh trái mobile), date-filter (preset 5 cột + input ngày), ResaleTracking/Sheet3/Sync.
- **P2**: cap width dropdown notifications TopNav; xác nhận bảng Kship đã có scroll ngang.
- **P3**: card-view mobile cho HR Employees, Customers (gộp `CustomersMobileList` cũ vào chuẩn, giữ chọn/expand/edit/delete), Inventory Stock + Returns, Google/Facebook Ads, Audit Logs — desktop bảng nguyên vẹn.
- **P4**: `/m` ESS — safe-area bottom, touch target `h-11`, trạng thái loading/empty/error nhất quán.

### Follow-up tùy chọn (không chặn)
- Hiển thị dòng "Tổng cộng" của Ads ở mobile (hiện chỉ có ở desktop footer).
- Áp `ResponsiveList` cho các bảng còn lại nếu sau này cần (Orders giữ scroll theo chủ đích).
