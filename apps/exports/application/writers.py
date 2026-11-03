"""Writer ghi thẳng ra file trên đĩa — RAM phẳng theo số dòng.

Đây là điểm khác cốt lõi so với đường export đồng bộ cũ: cũ dựng cả workbook trong
``io.BytesIO`` nên 50.000 đơn × ~40 cột ≈ 2 triệu cell object nằm trong RAM của
``desi-web`` (mem_limit 1200m). Ở đây openpyxl ``write_only`` xả từng dòng xuống
file tạm, còn ``desi-web`` thì không dựng file nữa.
"""

from __future__ import annotations

import csv

import openpyxl

from apps.common.excel_utils import clean_excel_cell, write_only_cells


class ExportCancelled(Exception):
    """Người dùng đã huỷ job — worker dừng sớm, không phải lỗi."""


class StreamingXlsxWriter:
    def __init__(self, path: str, sheet_title: str = "Sheet1"):
        self._path = path
        self._wb = openpyxl.Workbook(write_only=True)
        # openpyxl chặn tên sheet >31 ký tự và một số ký tự đặc biệt; cắt cho an toàn
        # vì title có thể lấy từ dữ liệu (mã lô, tên kho).
        self._ws = self._wb.create_sheet(title=(sheet_title or "Sheet1")[:31])

    def set_sheet_title(self, title: str) -> None:
        """Đổi tên sheet đang ghi.

        Engine đặt tên sheet đầu bằng ``job.kind`` (vd ``call_quality``) vì nó
        không biết gì về nghiệp vụ. Definition nào muốn tên tiếng Việt thì gọi
        hàm này ở đầu ``write`` — rẻ hơn hẳn so với dựng thêm một sheet mới rồi
        để lại một sheet rỗng tên máy móc ở đầu file.
        """
        self._ws.title = (title or "Sheet")[:31]

    def start_sheet(self, title: str) -> None:
        """Mở một sheet MỚI; các lệnh ghi sau đó đổ vào sheet này.

        Chỉ dành cho export nhiều sheet (vd Chất lượng cuộc gọi: danh sách cuộc,
        điểm từng tiêu chí, bảng tổng hợp). Định nghĩa một sheet không cần gọi —
        sheet đầu đã được dựng sẵn ở ``__init__`` nên hành vi cũ không đổi.

        Vẫn là ``write_only``: openpyxl xả từng dòng xuống file, sheet trước
        không bị giữ trong RAM khi sang sheet sau.
        """
        self._ws = self._wb.create_sheet(title=(title or "Sheet")[:31])

    def write_header(self, headers) -> None:
        self._ws.append(write_only_cells(self._ws, headers))

    def write_row(self, values) -> None:
        self._ws.append(write_only_cells(self._ws, values))

    def close(self) -> None:
        self._wb.save(self._path)


class CsvWriter:
    def __init__(self, path: str):
        # utf-8-sig: Excel trên Windows đọc CSV utf-8 KHÔNG BOM ra tiếng Việt vỡ dấu.
        self._fh = open(path, "w", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._fh)

    def write_header(self, headers) -> None:
        self._writer.writerow([clean_excel_cell(v) for v in headers])

    def write_row(self, values) -> None:
        self._writer.writerow([clean_excel_cell(v) for v in values])

    def close(self) -> None:
        self._fh.close()
