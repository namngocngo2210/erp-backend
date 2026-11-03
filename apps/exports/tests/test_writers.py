import csv
import io

import openpyxl

from apps.exports.application.writers import CsvWriter, StreamingXlsxWriter


def test_xlsx_writer_writes_header_and_rows(tmp_path):
    path = str(tmp_path / "out.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="Đơn hàng")
    writer.write_header(["Tên", "SL"])
    writer.write_row(["Anh A", 3])
    writer.write_row(["Chị B", 5])
    writer.close()

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws.title == "Đơn hàng"
    assert list(ws.iter_rows(values_only=True)) == [("Tên", "SL"), ("Anh A", 3), ("Chị B", 5)]


def test_xlsx_writer_neutralises_formula(tmp_path):
    path = str(tmp_path / "inj.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="S")
    writer.write_header(["A"])
    writer.write_row(['=HYPERLINK("http://evil","x")'])
    writer.close()

    wb = openpyxl.load_workbook(path)
    cell = wb.active["A2"]
    assert cell.data_type == "s"
    assert cell.value == '=HYPERLINK("http://evil","x")'


def test_xlsx_writer_strips_illegal_control_chars(tmp_path):
    path = str(tmp_path / "ctrl.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="S")
    writer.write_header(["A"])
    writer.write_row(["ma\x1dvach"])
    writer.close()

    wb = openpyxl.load_workbook(path)
    assert wb.active["A2"].value == "mavach"


def test_csv_writer_roundtrip(tmp_path):
    path = str(tmp_path / "out.csv")
    writer = CsvWriter(path)
    writer.write_header(["id", "tên"])
    writer.write_row([1, "Anh A"])
    writer.close()

    with open(path, encoding="utf-8-sig", newline="") as fh:
        assert list(csv.reader(fh)) == [["id", "tên"], ["1", "Anh A"]]
