# Chuẩn hóa Dialog form: landscape, gần full màn, responsive

- **Ngày**: 2026-06-16
- **Trạng thái**: ✅ Đã implement xong (branch `feature/dialog-form-landscape`)
- **Phạm vi**: Frontend (`frontend/src`), không đụng backend
- **Mục tiêu**: Mọi dialog *chứa form nội dung* (tạo/sửa đơn hàng, user, role/channel, product, KiotViet, import/export, profile…) hiển thị theo hướng **width > height**, **mở rộng gần full màn hình**, **responsive chuẩn** cho cả màn lớn (24–27") lẫn màn nhỏ (13–14"). Confirm/cảnh báo/form nhỏ **giữ nguyên** kích thước gọn.

---

## 1. Bối cảnh & vấn đề

Hiện trạng (xem khảo sát đầy đủ trong phần Phụ lục A):

- `DialogContent` nền (`components/ui/dialog.tsx`) mặc định `max-w-sm` (448px) — quá nhỏ cho form.
- Wrapper `ManagedDialogContent` mặc định `sm:max-w-2xl` (672px).
- Kích thước được set **rải rác** ở từng dialog bằng className tùy ý (`sm:max-w-2xl`, `max-w-3xl`, `w-[min(96vw,1600px)]`, `max-w-[520px]`…). Không có hệ thống → form to nhỏ không đồng nhất, nhiều form cao-hẹp (portrait) phải cuộn nhiều, không tận dụng màn rộng.
- `OrderFormDialog` đã tự làm gần full màn nhưng theo cách riêng, không tái sử dụng được.

## 2. Quyết định thiết kế (đã chốt với CTO)

| # | Quyết định | Lựa chọn |
|---|---|---|
| D1 | Phạm vi | **Chỉ form nội dung**. Confirm/error/form nhỏ ≤5 field giữ nguyên. |
| D2 | Màn lớn | **Có cap tối đa** (canh giữa), không kéo căng theo vw. |
| D3 | Cap kích thước | **width ≤ 1480px, height ≤ 920px**. |
| D4 | Bố cục field | **Relayout thành lưới 1→2→3 cột** responsive để lấp chiều rộng. |
| D5 | Triển khai | **Làm tất cả form trong-phạm-vi trong 1 đợt** (1 MR), chia phase nội bộ. |

## 3. Kiến trúc giải pháp — 2 primitive dùng chung

Không sửa className rời rạc. Thay vào đó tạo hạ tầng tái sử dụng để mọi form chỉ cần "điền nội dung".

### 3.1. `size` variant cho `DialogContent`

`components/ui/dialog.tsx` — dùng `class-variance-authority` (đã có trong dự án):

```ts
const dialogContentVariants = cva(
  // base: position/translate/animation/ring/bg... (giữ nguyên hiện có, BỎ max-w-sm khỏi base)
  "fixed top-1/2 left-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded-xl bg-popover text-sm text-popover-foreground ring-1 ring-border duration-100 outline-none animate-in fade-in-0 zoom-in-95",
  {
    variants: {
      size: {
        // Mặc định — giữ y hệt hành vi cũ cho confirm/error/form nhỏ.
        confirm: "grid w-[calc(100%-2rem)] max-w-sm gap-4 p-4",
        // Form landscape gần full màn, có cap, width > height.
        form: "flex h-[90dvh] max-h-[920px] w-[96vw] max-w-[1480px] flex-col overflow-hidden p-0 sm:w-[95vw]",
      },
    },
    defaultVariants: { size: "confirm" },
  },
);
```

- `DialogContent` nhận thêm prop `size?: "confirm" | "form"` (default `"confirm"`).
- **Tương thích ngược**: mọi dialog hiện tại không truyền `size` → vẫn `confirm` → **không đổi gì**. className caller truyền vào vẫn merge sau cùng (tw-merge) nên các form nhỏ giữ `max-w-[...]` riêng vẫn hoạt động.

### 3.2. `FormDialogShell` — bộ khung 3 slot

Component mới `components/ui/form-dialog-content.tsx`. Bọc `DialogContent size="form"` và áp layout dọc: **header dính / body cuộn / footer dính**. Chỉ vùng body cuộn — header & footer luôn nhìn thấy.

```
┌─ DialogHeader (sticky top) — title + description + nút X ─────┐
│ Body  (flex-1, overflow-y-auto, px-6 py-4)                    │
│   └─ <FormGrid> field … field … (1→2→3 cột)                  │
├─ Footer (sticky bottom, border-top) — [Hủy]   [Lưu] ─────────┤
└──────────────────────────────────────────────────────────────┘
```

API:

```tsx
<FormDialogShell
  title={t("...")}
  description={t("...")}            // optional
  footer={<>{cancelBtn}{saveBtn}</>} // nút hành động
  bodyClassName="..."               // optional override
>
  <FormGrid columns={2 | 3}>...</FormGrid>
</FormDialogShell>
```

- `FormGrid`: `grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-2` (hoặc `xl:grid-cols-3` khi `columns=3`). Heading/section spanning full: class tiện ích `col-span-full`.
- Footer: `sticky bottom-0 flex items-center justify-end gap-2 border-t bg-popover px-6 py-3`.
- Header: `sticky top-0 border-b bg-popover px-6 py-4`.

→ **Tính isolation**: form không tự lo kích thước/scroll/nút đóng. Đổi token kích thước = sửa 1 chỗ (variant). Dễ test độc lập.

## 4. Kiểm chứng responsive (width > height, có cap)

| Màn hình | Viewport CSS px | Dialog tính ra | width > height |
|---|---|---|---|
| 13–14" laptop | 1366 × 768 | 1311 × 691 (96vw × 90vh) | ✓ |
| 1080p | 1920 × 1080 | 1480 × 920 (cap cả 2 chiều) | ✓ |
| 24–27" QHD | 2560 × 1440 | 1480 × 920 (cap), canh giữa | ✓ |
| Mobile <640px | 390 × 844 | full-width sheet, cuộn dọc | N/A (chấp nhận — ERP desktop-first) |

Ghi chú: dùng `dvh` (dynamic viewport height) để tránh lỗi thanh địa chỉ mobile. `max-h-[920px]` đảm bảo trên màn rất cao dialog không cao quá mức.

## 5. Danh sách dialog & hành động

### 5.1. TRONG phạm vi — chuyển `size="form"` + relayout lưới cột (16 dialog)

| Dialog | File | Cột gợi ý |
|---|---|---|
| Tạo/sửa đơn hàng | `features/orders/OrderFormDialog.tsx` | 2–3 (theo section) |
| Chi tiết đơn (read-only) | `features/orders/OrderDetailDialog.tsx` | 2 |
| Import đơn Excel | `features/orders/OrdersImportDialog.tsx` | full (table) |
| Chọn cột export | `features/orders/ExportColumnsDialog.tsx` | 2–3 (checkbox) |
| Logs KiotViet | `features/orders/KiotVietLogsDialog.tsx` | full (table) |
| KShip delivery | `features/orders/KshipDeliveryDialog.tsx` | full (table/form) |
| Tạo user | `features/users/UserCreateDialog.tsx` | 2 |
| Sửa user | `features/users/UserEditDialog.tsx` | 2 |
| Tạo channel | `features/channels/ChannelCreateDialog.tsx` | 2 |
| Quản lý channel | `features/channels/ChannelManageDialog.tsx` | 2 |
| KiotViet config | `features/kiotviet/KiotVietDialog.tsx` | 2 |
| Tạo/sửa product | `pages/ProductsPage.tsx` | 2 |
| Tạo phiếu return | `features/inventory/pages/CreateReturnDialog.tsx` | full + table |
| Chi tiết stock document | `features/inventory/pages/StockDocumentsPage.tsx` | full (table) |
| Quản lý phòng ban (create/edit) | `features/users/DepartmentManager.tsx` | 2 |
| Hồ sơ cá nhân | `app/profile-dialog.tsx` | 2 (profile + password) |

### 5.2. NGOÀI phạm vi — giữ nguyên (chuẩn hóa nhẹ `size="confirm"` nếu cần, không đổi bố cục)

`CustomerFormDialog`, `CustomerDeleteDialogs`, `ProductCategoriesPage` (create/edit/delete), `WarehousesPage`, `ResolveStockDialog`, `ResaleTrackingPage` (create/edit/delete), `KshipNotePresetPicker`, `KiotVietPrecheckDialog`, `ImportExcelDialog` (inventory), `OrderDetailPage` cancel, `FacebookAdsPage`/`GoogleAdsPage` create, `BlocklistPage` delete, `LoyalCustomersPage` delete, `RolesPage` (create/delete), mọi dialog error trong `*Management.tsx`.

## 6. Testing

- `components/ui/__tests__/dialog.test.tsx`: thêm case — `size="form"` ráp đúng class `h-[90dvh] max-w-[1480px] …`; `size="confirm"` (default) giữ `max-w-sm`; className caller vẫn merge override.
- Test mới `components/ui/__tests__/form-dialog-content.test.tsx`: `FormDialogShell` render đủ title, body (children), footer; footer có class sticky; `FormGrid columns={3}` ráp `xl:grid-cols-3`.
- Giữ `features/orders/OrderFormDialog.test.tsx` xanh sau refactor (CRM block ẩn ở create mode, render modal).
- Chạy `npx tsc --noEmit`, `npx eslint` (0 error), `npx vitest run` — đối chiếu baseline known-fail (xem Phụ lục B), không thêm fail mới.

## 7. Phân chia sprint (1 MR, các phase commit riêng)

- **Phase 1 — Hạ tầng**: `size` variant + `FormDialogShell` + `FormGrid` + test cho 2 primitive. *Done khi*: test primitive xanh, không đụng form nào.
- **Phase 2 — Cụm Orders**: OrderForm, OrderDetail, OrdersImport, ExportColumns, KiotVietLogs, KshipDelivery. *Done khi*: `OrderFormDialog.test` xanh.
- **Phase 3 — Cụm Users/Channels/KiotViet**: UserCreate/Edit, ChannelCreate/Manage, KiotVietDialog, DepartmentManager, ProfileDialog.
- **Phase 4 — Cụm Catalog/Inventory**: ProductsPage create/edit, CreateReturn, StockDocuments.
- **Phase 5 — Chốt**: tsc + lint + test toàn bộ; rà soát thủ công danh sách "ngoài phạm vi" không bị ảnh hưởng; cập nhật mục "đã làm / chưa làm" ở cuối doc này.

Sau mỗi phase: ghi chú đã làm/chưa làm tại mục §9.

## 8. Rủi ro & giảm thiểu

- **R1: Form to nhưng nội dung ít → trống trải.** → Dùng lưới cột + giới hạn ở danh sách form thực sự nhiều field; form nhỏ đã loại khỏi phạm vi.
- **R2: Refactor `OrderFormDialog` (đã có test + logic phức tạp) gây regress.** → Giữ nguyên logic, chỉ bọc lại bằng shell; chạy test sau mỗi sửa đổi.
- **R3: Dialog có `<form>` bao ngoài** — footer dính phải nằm trong `<form>` để nút submit hoạt động. → Shell cho phép truyền `asChild`/để form bao cả shell; tài liệu hóa trong code.
- **R4: tw-merge xung đột** giữa class variant và className caller. → Test khẳng định thứ tự merge; ưu tiên không truyền className kích thước cho form trong-phạm-vi.

## 9. Nhật ký thực hiện

**Đã làm (tất cả 1 đợt, các commit riêng trên `feature/dialog-form-landscape`):**
- **Phase 1 — hạ tầng**: `size` variant (`confirm`|`form`) trên `DialogContent` + `FormDialogShell`/`FormGrid` + test (9 test xanh). Default `confirm` = hành vi cũ → 0 regression.
- **Phase 2 — Orders**: OrderForm, OrderDetail, OrdersImport, ExportColumns, KiotVietLogs, KshipDelivery.
- **Phase 3 — Users/Channels/KiotViet**: UserCreate/Edit, ChannelCreate/Manage, KiotVietDialog, DepartmentManager (create+edit), ProfileDialog.
- **Phase 4 — Catalog/Inventory**: ProductsPage (create+edit), CreateReturnDialog, StockDocuments (form tạo phiếu).
- **Phase 5 — chốt**: `tsc --noEmit` exit 0; full vitest = chỉ 4 baseline known-fail, 0 fail mới; eslint 0 error trên file đã đổi; audit xác nhận không đụng dialog ngoài-phạm-vi (§5.2); review tổng thể PASS (submit/handler/title bảo toàn, confirm dialog giữ nguyên).

**Đã biết / nên QA thủ công trên app chạy thật (cosmetic, không chặn merge):**
- `ExportColumnsDialog`: các pill checkbox giờ nằm trong lưới 3 cột → mỗi pill chiếm 1 ô thay vì flow gọn. Cân nhắc đổi lại `flex flex-wrap` nếu nhìn thưa.
- `KiotVietLogsDialog` & `KshipDeliveryDialog`: `thead sticky` không còn dính khi cuộn dọc body (bảng mất max-height nội bộ). Minor.
- `DepartmentManager`: các field để `col-span-full` (xếp dọc) thay vì 2 cột thật — kiểm tra lại nếu muốn 2 cột.
- Chưa kiểm thử trực quan trên màn 13" và 27" thật — nên xác nhận width>height & cap 1480px bằng mắt.

**Chưa làm**: không có hạng mục trong-phạm-vi nào còn sót.

---

## Phụ lục A — Khảo sát dialog hiện trạng (rút gọn)

- Base `DialogContent`: `fixed top-1/2 left-1/2 -translate-1/2 grid w-[calc(100%-2rem)] max-w-sm gap-4 p-4 rounded-xl bg-popover ring-1 ring-border`.
- `ManagedDialogContent`: thêm `border bg-card shadow-xl sm:max-w-2xl`.
- Breakpoints: Tailwind mặc định (sm 640 / md 768 / lg 1024 / xl 1280 / 2xl 1536), không custom.
- Tổng ~48 nơi dùng Dialog; phần lớn là confirm/error nhỏ.

## Phụ lục B — Test fail baseline (không phải regression)

Trên `master` sạch các test sau đã fail (đã verify bằng stash): `OrderFormDialog.test.tsx`, `OrdersTable.mobile.test.tsx`, `ChannelsTable.mobile.test.tsx`, `app/__tests__/modules.test.ts`. Khi chạy test, đối chiếu để không nhầm là regression do thay đổi này.
