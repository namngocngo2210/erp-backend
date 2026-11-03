from __future__ import annotations

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def clean_excel_cell(value):
    """Loại ký tự điều khiển bất hợp lệ (openpyxl chặn) khỏi cell chuỗi.

    Dữ liệu quét mã vận đơn đôi khi lẫn ký tự control (vd \\x1d) → IllegalCharacterError.
    Chỉ áp dụng cho giá trị str; số/ngày/None giữ nguyên.
    """
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def append_clean_row(ws, values) -> None:
    """Append 1 dòng vào worksheet, làm sạch + chống formula injection.

    - Mỗi giá trị đi qua clean_excel_cell (loại ký tự control bất hợp lệ).
    - Với XLSX ghi bằng openpyxl, CHỈ chuỗi bắt đầu bằng "=" bị suy luận thành
      formula (data_type='f'); chuỗi bắt đầu +/-/@ được lưu là chuỗi thường và
      Excel không bao giờ evaluate (quy tắc =,+,-,@ là của CSV injection, không
      áp dụng cho XLSX). Vì vậy chỉ cần ép các cell 'f' về data_type='s':
      giá trị gốc giữ nguyên 100% (không thêm tiền tố hiển thị), Excel đọc là
      text. quotePrefix=True để nếu người dùng sửa cell trong Excel thì Excel
      cũng giữ nó là text thay vì evaluate lại.
    """
    ws.append([clean_excel_cell(v) for v in values])
    for cell in ws[ws.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"
            cell.quotePrefix = True


def set_text_cell(ws, *, row: int, column: int, value):
    """Ghi 1 cell do NGƯỜI DÙNG nhập (tên NV, phòng ban, tên ngân hàng…) an toàn.

    Dùng cho sheet có công thức CHỦ ĐÍCH do code sinh (vd bảng lương): không thể
    quét cả dòng như `append_clean_row` vì sẽ vô hiệu hoá luôn các công thức đó.
    Cùng cơ chế: chuỗi bị openpyxl suy thành formula ('=') bị ép về text, giá trị
    giữ nguyên 100%.
    """
    cell = ws.cell(row=row, column=column, value=clean_excel_cell(value))
    if cell.data_type == "f":
        cell.data_type = "s"
        cell.quotePrefix = True
    return cell


def write_only_cells(ws, values):
    """Dựng danh sách cell đã làm sạch cho sheet ``write_only``.

    Sheet write_only KHÔNG giữ cell sau khi ``ws.append()`` nên không thể mượn
    ``append_clean_row`` (hàm đó đọc lại ``ws[ws.max_row]`` sau khi append). Phải
    dựng ``WriteOnlyCell`` với data_type đã sửa TRƯỚC.

    KHÔNG được tái dùng một instance cell cho nhiều dòng: writer của openpyxl mutate
    cell theo vị trí lúc serialize, share instance sẽ làm giá trị lệch sang dòng kế.
    """
    from openpyxl.cell import WriteOnlyCell

    cells = []
    for value in values:
        cell = WriteOnlyCell(ws, value=clean_excel_cell(value))
        if cell.data_type == "f":
            cell.data_type = "s"
            cell.quotePrefix = True
        cells.append(cell)
    return cells
