# Chuẩn Responsive (Mobile) — DESI ERP

> Áp dụng cho toàn frontend `frontend/src`. Mục tiêu: ERP dùng được trên điện thoại (~390px) mà không phá density desktop. Tham chiếu audit & quyết định: `docs/superpowers/specs/2026-06-16-erp-responsive-design.md`.

## 1. Breakpoint & nguyên tắc

- Dùng breakpoint mặc định Tailwind: `sm=640 md=768 lg=1024 xl=1280 2xl=1536`.
- **Mobile-first**: class base (không prefix) = layout điện thoại. Thêm `sm:`/`md:`/`lg:` để mở rộng cho màn lớn. KHÔNG bao giờ để layout nhiều cột làm mặc định rồi mới thu ở mobile.
- "Mobile" = `< md (768px)`. Đây là ngưỡng coi như điện thoại/tablet dọc.
- ERP cố ý co `html` font-size xuống 14px ở màn hẹp (density desktop). KHÔNG bỏ điều này. Mobile fix bằng layout, không bằng phóng chữ — trừ form control (đã ép 16px chống iOS zoom trong `styles.css`).

## 2. Luật bắt buộc

### 2.1 Grid field trong form
- Mọi lưới field PHẢI có base `grid-cols-1`, mở rộng bằng prefix:
  ```tsx
  // ĐÚNG
  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">…</div>
  // SAI (tràn ở 390px)
  <div className="grid grid-cols-2 gap-3">…</div>
  ```
- Form field nhãn-trái kiểu `grid-cols-4` (label + nhiều cột) PHẢI đổi sang **label trên — field dưới** ở mobile: `grid-cols-1 sm:grid-cols-4`, hoặc dùng `FormGrid` (xem 2.2).

### 2.2 FormGrid là chuẩn cho form lớn
- Dùng component `FormGrid` (đã có; OrderFormDialog/EmployeeFormDialog dùng) cho mọi form create/edit nhiều field. Mặc định `grid-cols-1`, `lg:grid-cols-2`.

### 2.3 Bảng dữ liệu
- **Mặc định**: mọi `<table>` bọc trong `DataTableShell` (overflow-x-auto + `-webkit-overflow-scrolling:touch`). Scroll ngang là baseline chấp nhận được.
- **Bảng list chính** (Customers, HR Employees, Stock, Ads, Inventory Returns…): thêm **card-view ở mobile** bằng helper `ResponsiveList` (P0): `md` trở lên hiện bảng, dưới `md` hiện danh sách thẻ (mỗi dòng = 1 card với các field chính + actions).
  ```tsx
  <ResponsiveList
    items={rows}
    table={<EmployeesTable … />}
    renderCard={(row) => <EmployeeCard row={row} />}
  />
  ```
- Bảng power-user cực nhiều cột (Orders 40+ cột): GIỮ scroll ngang, KHÔNG ép card (đã có `mobileSearchBar`).

### 2.4 Dialog
- Form dialog (`size="form"`) trên mobile (`< sm`): **full-screen** — `h-[100dvh] w-screen max-w-none rounded-none`, header/footer sticky, body `overflow-y-auto`. Desktop giữ nguyên `w-[96vw] max-w-[1480px]`.
- Confirm/dialog nhỏ: giữ `w-[calc(100%-2rem)] max-w-sm`.
- Bảng bên trong dialog: bọc `overflow-x-auto`.

### 2.5 Dropdown/menu/popover
- Cap chiều rộng theo viewport: `w-80` → `w-[min(20rem,calc(100vw-1.5rem))]`. Không để menu tràn mép phải trên phone.

### 2.6 Khoảng cách & vùng an toàn
- Padding container: base nhỏ, mở rộng theo breakpoint (`p-3 sm:p-4`). Tránh `p-6` cứng trên mobile.
- Phần tử dính đáy (footer/nav): cộng `env(safe-area-inset-bottom)`.
- Nút bấm chạm chính: cao tối thiểu `h-11` (44px) trên mobile.

## 3. Primitive dùng chung (P0)
- `ResponsiveList` — `frontend/src/components/ui/responsive-list.tsx`: chuyển bảng↔card theo `md`. Dùng `useMediaQuery("(min-width: 768px)")`.
- (Tái dùng) `FormGrid`, `DataTableShell`, `useMediaQuery` đã có.

## 4. Quy trình review responsive cho code mới
- Mọi grid field mới: kiểm tra có base `grid-cols-1`.
- Màn list mới: cân nhắc `ResponsiveList`.
- Test nhanh ở DevTools 390px trước khi merge.
