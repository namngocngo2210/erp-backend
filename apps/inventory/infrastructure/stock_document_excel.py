"""Dựng file .xlsx cho MỘT phiếu kho (nút "Excel" trên từng dòng sổ phiếu).

Khác hẳn `exports_definitions` (luồng async, hàng nghìn dòng, lọc theo kỳ): một
phiếu chỉ vài chục dòng nên dựng đồng bộ ngay trong request. Bố cục = khối đầu
phiếu rồi tới bảng dòng hàng, đúng thứ tự người kho đọc khi cầm tờ giấy.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from apps.inventory.infrastructure.flow_history_excel import _fmt_dt
from apps.inventory.models import DocStatus, DocType, StockDocument

_LINE_HEADERS = ["STT", "SKU", "Tên sản phẩm", "Số lượng"]

_DOC_TYPE_LABELS = dict(DocType.choices)
_DOC_STATUS_LABELS = dict(DocStatus.choices)


def _warehouse_label(doc: StockDocument) -> str:
    """Nhãn kho — CÙNG luật với sổ phiếu (`ledger_views`): phiếu chuyển kho hiện
    cả hai đầu, phiếu còn lại hiện kho duy nhất của nó."""
    src = doc.source_warehouse.name if doc.source_warehouse_id else None
    dest = doc.dest_warehouse.name if doc.dest_warehouse_id else None
    if doc.doc_type == DocType.TRANSFER:
        return f"{src or '?'} → {dest or '?'}"
    return src or dest or "—"


def _username(user) -> str:
    return user.username if user else ""


def _header_rows(doc: StockDocument) -> list[tuple[str, str]]:
    rows = [
        ("Mã phiếu", doc.code),
        ("Loại phiếu", _DOC_TYPE_LABELS.get(doc.doc_type, doc.doc_type)),
        ("Trạng thái", _DOC_STATUS_LABELS.get(doc.status, doc.status)),
        ("Kho", _warehouse_label(doc)),
        ("Ngày tạo", _fmt_dt(doc.created_at)),
        ("Ngày ghi sổ", _fmt_dt(doc.posted_at)),
        ("Người gửi duyệt", _username(doc.submitted_by)),
        ("Người duyệt", _username(doc.approved_by)),
        ("Ghi chú", doc.note or ""),
    ]
    # Chỉ phiếu bị từ chối mới có dòng này — phiếu bình thường không nên mang một
    # ô "Lý do từ chối" trống làm người đọc tưởng thiếu dữ liệu.
    if doc.rejected_reason:
        rows.append(("Lý do từ chối", doc.rejected_reason))
    return rows


def build_stock_document_workbook(doc: StockDocument) -> bytes:
    """Bytes .xlsx của một phiếu kho: khối đầu phiếu + bảng dòng hàng + dòng Tổng."""
    wb = Workbook()
    ws = wb.active
    ws.title = "phieu-kho"

    bold = Font(bold=True)

    for label, value in _header_rows(doc):
        ws.append([label, value])
        ws.cell(ws.max_row, 1).font = bold

    ws.append([])

    ws.append(_LINE_HEADERS)
    for col in range(1, len(_LINE_HEADERS) + 1):
        ws.cell(ws.max_row, col).font = bold

    total = 0
    for index, line in enumerate(doc.lines.all(), start=1):
        ws.append([index, line.product.sku, line.product.name, line.quantity])
        total += line.quantity

    ws.append(["", "", "Tổng số lượng", total])
    ws.cell(ws.max_row, 3).font = bold
    ws.cell(ws.max_row, 3).alignment = Alignment(horizontal="right")
    ws.cell(ws.max_row, 4).font = bold

    for column, width in zip("ABCD", (16, 22, 46, 12)):
        ws.column_dimensions[column].width = width

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
