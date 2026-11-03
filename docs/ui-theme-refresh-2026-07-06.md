# UI theme refresh — 2026-07-06

Branch: `feat/ui-theme-refresh`

## Đã làm

1. **Token màu** (`frontend/src/styles.css`)
   - Sửa `--action-success` / `--action-warning` về đúng hue (xanh lá 152 / hổ phách 75) — giá trị cũ đều là xanh dương ~260 (copy nhầm) và là token chết chưa ai dùng; nay map vào `@theme` thành `--color-action-*` để dùng được dạng utility.
   - Nền `--background` chuyển trắng tinh → trắng phớt xanh `oklch(0.985 0.008 230)`, `--card` 0.998 để card nổi thành lớp.
   - Badge trạng thái đơn KHÔNG đổi (đã có màu sẵn ở `order-status-badge.ts`).

2. **Typography** (self-host qua @fontsource, cả hai có subset vietnamese)
   - Body: **Inter Variable** (`--font-sans`), bảng `data-slot="table"` bật `tabular-nums` cho số thẳng cột.
   - Heading/brand: **Be Vietnam Pro 600/700** (`--font-display`) qua class `display-face` — class này được dùng 5 chỗ từ trước nhưng chưa từng được định nghĩa.

3. **Accent màu theo module** — mỗi module một màu nhận diện (`ModuleAccent` trong `app/modules.ts`):
   Sales=sky, CRM=violet, Kho=emerald, Sản phẩm=orange, Marketing=fuchsia, Báo cáo=amber, HR=teal, Quản trị=slate.
   - Sidebar: `ModuleIconChip` dùng chung cho accordion / rail thu gọn / mobile drawer; accent chảy qua `NavGroup` của `nav-layout.ts` (sau merge sidebar-grouped-layout). Pinned items giữ trung tính.
   - App Launcher: tile icon màu theo module + hover lift.

## Chưa làm / cần QA

- **QA thị giác trước khi merge**: light + dark, sidebar 3 dạng (mở rộng / rail / mobile drawer), App Launcher, bảng đơn hàng (font mới + tabular-nums).
- Trang con chưa dùng accent module ở page header — cân nhắc sprint sau nếu muốn đẩy nhận diện màu sâu hơn.
