# Thiết kế lại bộ lọc trang đơn hàng

Ngày: 2026-08-26 · Nhánh: `feat/orders-filter-redesign`

## Vấn đề

Panel lọc là một cột dọc 22 ô (`OrdersFilterPanel.tsx`, 699 dòng), chiếm cố định
240px bề ngang (`lg:w-60`, `2xl:w-72`) suốt phiên làm việc. Bảng đơn có tới 62 cột
nên phần bề ngang này là cái giá phải trả thường trực, trong khi bộ lọc đã được lưu
localStorage theo scope — người dùng set một lần rồi làm việc hàng giờ.

Riêng 7 ô Có/Không (mã vận đơn, phiếu giao, khách trả, phiếu trả, mã thu hồi, ghi âm,
ghi chú nội bộ, địa chỉ) chiếm gần 1/3 chiều dài panel mà mỗi ô chỉ có 3 giá trị.

## Giải pháp

Ba khối thay cho sidebar dọc:

1. **Thanh lọc ngang** (`OrdersFilterBar`) trên đầu bảng: nút `Bộ lọc (n)` mở ngăn
   kéo, cùng 4 ô dùng nhiều nhất — Ngày tạo, Kênh bán hàng, Nhân viên, Trạng thái sale.
   Dưới `lg` chỉ còn nút và hàng chip.
2. **Hàng chip điều kiện đang áp**: mỗi filter có giá trị thành một chip
   `Nhãn: giá trị (+n)`, bấm ✕ gỡ đúng điều kiện đó. Đây là thứ bù lại việc mất
   sidebar — vẫn nhìn thấy toàn bộ điều kiện đang chạy, tốn một dòng thay vì một cột.
3. **Ngăn kéo** (`OrdersFilterSheet`) trượt ra **từ bên trái** — đúng chỗ sidebar cũ
   để không phá thói quen đưa mắt — chứa đủ 22 ô, chia 5 nhóm gập/mở, lưới 2 cột.

| Nhóm | Ô lọc | Số ô |
| --- | --- | --- |
| Thời gian | Ngày tạo · Ngày hoàn hàng | 2 |
| Kênh & Nhân sự | Kênh KiotViet · Chi nhánh · Kênh bán hàng · Loại kênh · Nhân viên | 5 |
| Trạng thái | TT sale · TT vận chuyển · TT vận chuyển thực · TT hóa đơn · Xuất VAT · Loại đơn hàng · Loại xuất hàng · Loại đơn KOC | 8 |
| Cờ Có/Không | 8 cờ, gom thành một lưới tri-state | 8 |
| Sản phẩm | Sản phẩm | 1 |

Nhóm nào đang có điều kiện thì mở sẵn; nhóm "Thời gian" luôn mở để ngăn kéo không
trống trơn.

### Cờ ba trạng thái

`<select>` ba dòng đổi thành segmented `Tất cả | Có | Không`. Giá trị gửi lên server
không đổi (`""` / `"yes"` / `"no"`), nên backend và localStorage không bị ảnh hưởng.

## Phần cố ý KHÔNG đụng

- `use-orders-filters.ts`, `use-orders-filter-handlers.ts`, `order-storage.ts`.
- 5 endpoint list đơn và toàn bộ tham số lọc.
- Áp lọc ngay khi đổi giá trị (không thêm nút "Áp dụng").
- Thanh chip trạng thái đơn và tab Landing sẵn có.

## Thay đổi hành vi cần biết

- **Ngăn kéo luôn mở ở trạng thái đóng khi vào trang.** Key localStorage
  `orders.filtersCollapsed` bị bỏ: ngăn kéo che bảng nên tự bung ra lúc load là sai.
- **Nút lọc trong toolbar bảng bị gỡ** (`filterToggle` của `OrdersTable` và
  `OrdersFilterToggleButton`) — thanh lọc đã có nút riêng, giữ hai nút là thừa.
- `FilterSelectOption` chuyển từ `features/orders/OrdersFilterPanel` sang
  `features/orders/filter-types` (4 file import theo).
- Allowlist `no-restricted-imports` của react-select đổi đường dẫn theo file đã dời;
  không thêm mục mới.

## Tiến độ

| Hạng mục | Trạng thái |
| --- | --- |
| Tách `FilterSelectOption` sang `filter-types.ts` | Xong |
| `filter-fields.tsx` — field dùng chung + tri-state | Xong |
| `orders-filter-chips.ts` — dựng chip, gỡ lẻ, đếm theo nhóm | Xong |
| `OrdersFilterBar` / `OrdersFilterSheet` / `OrdersFilters` | Xong |
| Gỡ layout sidebar khỏi `OrdersPage` | Xong |
| i18n vi/en cho nhãn mới | Xong |
| Test (18 case) | Xong |
| `tsc` + `eslint --max-warnings=0` + suite 1524 test | Xong, xanh |

## Còn nợ sau deploy

- Xem trên máy thật (đặc biệt tablet ngang) — hiện mới kiểm bằng jsdom.
- 7 ô lọc còn dùng react-select; chuyển hẳn sang `components/ui/multi-select` để gỡ
  nốt allowlist là việc của MR sau, cố ý không gộp vào đây để không đổi hành vi
  control ngay trong lần đổi layout.
- Báo trước cho sale/kho: sidebar không còn, điều kiện đang lọc nằm ở hàng chip.
