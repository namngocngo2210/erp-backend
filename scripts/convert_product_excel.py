#!/usr/bin/env python3
"""Chuyển 'Thông tin sản phẩm Desi Global.xlsx' (layout khối) sang file phẳng.

Chạy MỘT LẦN, thủ công:
    python scripts/convert_product_excel.py \
        --source "Thông tin sản phẩm Desi Global.xlsx" \
        --out backend/data/product-content.xlsx

File gốc KHÔNG commit (chứa giá tối thiểu bán lẻ + script chốt đơn nội bộ).
File phẳng CÓ commit — chỉ chứa nội dung công bố ra ngoài. Giá bị bỏ qua
hoàn toàn: không đọc bất kỳ cột giá nào trong file gốc.

Script không tự đoán khi dữ liệu mơ hồ (ví dụ ô MODEL ghi "MSC02-CAOCAP/ DC20"):
nó ghi nguyên văn vào file phẳng và in cảnh báo để người sửa tay.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl

Row = tuple[Any, ...]

DETAIL_SHEET = "Thông tin chi tiết sản phẩm"
SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{1,29}$")

# Nhãn ở cột A đánh dấu ranh giới các khối trong một khối sản phẩm.
LABEL_NAME = "TÊN SẢN PHẨM"
LABEL_MODEL = "MODEL"
LABEL_SPECS = "THÔNG SỐ KỸ THUẬT"
LABEL_AUDIENCE = "ĐỐI TƯỢNG SỬ DỤNG"
LABEL_FAQ = "CÂU HỎI THƯỜNG GẶP"
LABEL_TROUBLE_HEADER = "HIỆN TƯỢNG"
# Ghi chú chính sách CSKH nội bộ ("đổi lỗi/ thu hồi bảo hành theo quy định")
# xuất hiện là hàng cuối bảng XỬ LÝ SỰ CỐ ở 8/22 khối, luôn cùng một câu chữ.
# su_co xuất bản mặc định (khác faq) nên không được để lọt ra ngoài.
CS_POLICY_NOTE = (
    "Nếu làm theo hướng dẫn xử lý sự cố như trên mà không được thì tiến hành "
    "đổi lỗi/ thu hồi bảo hành cho khách theo quy định"
)
# Mốc bắt đầu khối điểm nổi bật nằm ở CỘT D, không phải cột A: chỉ 2/22 khối
# có thêm tiêu đề "ẢNH SẢN PHẨM" ở cột A phía trên; 20 khối còn lại bắt đầu
# thẳng bằng hàng "MÔ TẢ SẢN PHẨM" ở cột D — dùng cột D làm mốc vì nó có mặt
# ở cả 22 khối, còn cột A thì không.
LABEL_DESC = "MÔ TẢ SẢN PHẨM"
# Nhãn video luôn bắt đầu bằng "Link..." hoặc "Video..."; các hàng dạng
# "Bước n: ..." có URL trong ô chi tiết (video minh hoạ một bước) vẫn được
# giữ lại như một bước hướng dẫn sử dụng bình thường, không tách ra video.
VIDEO_LABEL_RE = re.compile(r"^(link|video)", re.IGNORECASE)
# Ngay trước hàng "TÊN SẢN PHẨM" của khối kế tiếp, cả 21/21 ranh giới đều có
# một hàng thừa kiểu "8. Tên sản phẩm tiếp theo" — tàn dư của một mục lục cũ,
# chỉ có cột A, không có câu trả lời. Nếu không loại bỏ, nó lọt vào cuối
# sheet faq như một câu hỏi rỗng. Nhận diện: chỉ cột A có dữ liệu và bắt đầu
# bằng số thứ tự.
STRAY_INDEX_RE = re.compile(r"^\d+(\.\d+)?\.\s")


def _cell(row: Row, idx: int) -> str:
    value = row[idx] if idx < len(row) else None
    return str(value).strip() if value is not None else ""


def _label(row: Row) -> str:
    return _cell(row, 0).upper()


def _section_rows(
    chunk: list[Row],
    is_start: Callable[[Row], bool],
    is_stop: Callable[[Row], bool] | None,
) -> list[Row]:
    """Các hàng nằm giữa hàng thoả `is_start` (loại trừ chính nó) và `is_stop`."""
    started = False
    out: list[Row] = []
    for row in chunk:
        if not started:
            if is_start(row):
                started = True
            continue
        if is_stop and is_stop(row):
            break
        out.append(row)
    return out


def _parse_pairs(rows: list[Row], col_title: int, col_detail: int) -> list[dict[str, str]]:
    """Khối 2 cột title/detail (điểm nổi bật ở cột D/E, FAQ ở cột A/B,...).

    Nguồn ghi tiêu đề một lần rồi liệt kê tiếp ở các hàng sau với cột tiêu đề
    bỏ trống (ví dụ "Bước 2: Hướng dẫn dùng nút bấm" rồi 5 hàng liệt kê từng
    nút, mỗi hàng chỉ có cột chi tiết). Nối phần chi tiết của các hàng đó vào
    mục liền trước bằng dấu xuống dòng — được chọn thay vì lặp lại tiêu đề vì
    nội dung tiếp nối trong nguồn đã tự đánh số bên trong ("2. ...", "3. ..."),
    nối lại giữ đúng định dạng danh sách như tác giả gốc viết.
    """
    out: list[dict[str, str]] = []
    for row in rows:
        title, detail = _cell(row, col_title), _cell(row, col_detail)
        if title:
            out.append({"title": title, "detail": detail})
        elif detail and out:
            prev = out[-1]
            prev["detail"] = f"{prev['detail']}\n{detail}" if prev["detail"] else detail
    return out


def _parse_highlights(chunk: list[Row]) -> list[dict[str, str]]:
    """Điểm nổi bật: tiêu đề ở cột D, mô tả lợi ích ở cột E."""
    rows = _section_rows(
        chunk,
        lambda r: _cell(r, 3).upper() == LABEL_DESC,
        lambda r: _label(r) == LABEL_SPECS,
    )
    return _parse_pairs(rows, 3, 4)


def _parse_specs(chunk: list[Row]) -> list[dict[str, str]]:
    """THÔNG SỐ KỸ THUẬT: mỗi hàng chứa TỚI HAI cặp nhãn/giá trị (A|B và D|E)."""
    rows = _section_rows(
        chunk,
        lambda r: _label(r) == LABEL_SPECS,
        lambda r: _label(r) == LABEL_AUDIENCE,
    )
    out = []
    for row in rows:
        for label_idx, value_idx in ((0, 1), (3, 4)):
            label, value = _cell(row, label_idx), _cell(row, value_idx)
            if label and value:
                out.append({"label": label, "value": value})
    return out


def _parse_audience(chunk: list[Row]) -> dict[str, list[str]]:
    """Hai cột song song: A = nên dùng, D = cần hỏi ý kiến bác sĩ."""
    rows = _section_rows(
        chunk,
        lambda r: _label(r) == LABEL_AUDIENCE,
        lambda r: _label(r) == LABEL_FAQ,
    )
    suitable = [_cell(r, 0) for r in rows if _cell(r, 0)]
    caution = [_cell(r, 3) for r in rows if _cell(r, 3)]
    return {"suitable": suitable, "caution": caution}


def _faq_section_rows(chunk: list[Row]) -> list[Row]:
    """Hàng từ tiêu đề CÂU HỎI THƯỜNG GẶP tới hết khối (không có mốc kết thúc)."""
    return _section_rows(chunk, lambda r: _label(r) == LABEL_FAQ, None)


def _strip_trailing_index_row(chunk: list[Row]) -> list[Row]:
    """Bỏ hàng mục lục thừa (nếu có) ở cuối khối — xem STRAY_INDEX_RE."""
    last = chunk[-1]
    only_col_a = all(_cell(last, i) == "" for i in range(1, len(last)))
    if only_col_a and STRAY_INDEX_RE.match(_cell(last, 0)):
        return chunk[:-1]
    return chunk


def _is_video_row(row: Row) -> bool:
    return bool(VIDEO_LABEL_RE.match(_cell(row, 3))) and "http" in _cell(row, 4)


def _parse_faq(rows: list[Row]) -> list[dict[str, str]]:
    """Câu hỏi thường gặp ở cột A/B."""
    return _parse_pairs(rows, 0, 1)


def _parse_usage(rows: list[Row]) -> list[dict[str, str]]:
    """Hướng dẫn sử dụng ở cột D/E — trừ các hàng thực ra là link video."""
    return _parse_pairs([r for r in rows if not _is_video_row(r)], 3, 4)


def _parse_troubleshooting(rows: list[Row], sku: str, warnings: list[str]) -> list[dict[str, str]]:
    """XỬ LÝ SỰ CỐ nằm ở cột G/H/J của khối FAQ.

    - Hàng tiêu đề "Hiện tượng | Nguyên nhân | Cách xử lý" bị bỏ qua.
    - Hàng ghi chú chính sách CSKH nội bộ (CS_POLICY_NOTE) bị loại khỏi sheet
      công khai — su_co xuất bản mặc định, không được để lọt chính sách nội
      bộ ra ngoài; mỗi lần loại đều in cảnh báo.
    - Hàng nối tiếp (symptom trống, còn cause/fix) được gộp vào mục liền
      trước theo từng cột — nguồn liệt kê nhiều nguyên nhân/cách xử lý cho
      cùng một hiện tượng trên nhiều hàng.
    """
    out: list[dict[str, str]] = []
    for row in rows:
        symptom, cause, fix = _cell(row, 6), _cell(row, 7), _cell(row, 9)
        if symptom.upper() == LABEL_TROUBLE_HEADER:
            continue
        if symptom and symptom.strip().lower() == CS_POLICY_NOTE.lower():
            warnings.append(f"{sku}: bỏ ghi chú chính sách CSKH nội bộ khỏi su_co — {symptom!r}")
            continue
        if symptom:
            out.append({"symptom": symptom, "cause": cause, "fix": fix})
        elif (cause or fix) and out:
            prev = out[-1]
            if cause:
                prev["cause"] = f"{prev['cause']}\n{cause}" if prev["cause"] else cause
            if fix:
                prev["fix"] = f"{prev['fix']}\n{fix}" if prev["fix"] else fix
    return out


def _parse_videos(rows: list[Row]) -> list[dict[str, str]]:
    """Nhãn video ở cột D, URL ở cột E — một ô có thể chứa nhiều URL (nhiều dòng)."""
    out = []
    for row in rows:
        if not _is_video_row(row):
            continue
        label = _cell(row, 3).rstrip(":").strip()
        for line in _cell(row, 4).splitlines():
            url = line.strip()
            if url.startswith("http"):
                out.append({"label": label, "url": url})
    return out


def parse_detail(rows: list[Row]) -> dict[str, dict[str, Any]]:
    """Trả về {sku: {ten, diem_noi_bat, thong_so, doi_tuong, huong_dan, su_co, faq, video}}."""
    blocks: dict[str, dict[str, Any]] = {}
    starts = [i for i, r in enumerate(rows) if _label(r) == LABEL_NAME]
    warnings: list[str] = []

    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        chunk = _strip_trailing_index_row(rows[start:end])

        ten = _cell(chunk[0], 3)
        model = ""
        for row in chunk[1:4]:
            if _label(row) == LABEL_MODEL:
                model = _cell(row, 3)
                break

        # SKU sạch được chuẩn hoá hoa (nguồn vốn đã viết hoa mọi SKU thật).
        # SKU mơ hồ (không khớp SKU_RE) giữ NGUYÊN VĂN — kể cả hoa/thường —
        # vì đây là dữ liệu cần người sửa tay, không phải để script diễn giải.
        model_stripped = model.strip()
        is_clean_sku = bool(SKU_RE.match(model_stripped.upper()))
        sku = model_stripped.upper() if is_clean_sku else model_stripped
        if not is_clean_sku:
            warnings.append(f"MODEL không phải một SKU sạch, giữ nguyên để sửa tay: {model!r}")

        faq_rows = _faq_section_rows(chunk)

        blocks[sku] = {
            "ten": ten,
            "diem_noi_bat": _parse_highlights(chunk),
            "thong_so": _parse_specs(chunk),
            "doi_tuong": _parse_audience(chunk),
            "huong_dan": _parse_usage(faq_rows),
            "su_co": _parse_troubleshooting(faq_rows, sku, warnings),
            "faq": _parse_faq(faq_rows),
            "video": _parse_videos(faq_rows),
        }

    for line in warnings:
        print(f"[CẢNH BÁO] {line}")
    return blocks


SHEETS = {
    "san_pham": ["sku", "ten", "mo_ta_ngan", "cong_bo", "khoi_hien_thi"],
    "diem_noi_bat": ["sku", "thu_tu", "tieu_de", "chi_tiet"],
    "thong_so": ["sku", "thu_tu", "nhan", "gia_tri"],
    "doi_tuong": ["sku", "loai", "thu_tu", "noi_dung"],
    "huong_dan": ["sku", "thu_tu", "tieu_de", "chi_tiet"],
    "su_co": ["sku", "thu_tu", "hien_tuong", "nguyen_nhan", "cach_xu_ly"],
    "faq": ["sku", "thu_tu", "cau_hoi", "tra_loi", "hien_thi"],
    "video": ["sku", "thu_tu", "nhan", "url"],
}

# faq CỐ Ý không nằm trong danh sách mặc định: nội dung FAQ trong file gốc viết theo
# giọng script bán hàng ("Dạ Anh/Chị yên tâm ạ..."), phải biên tập rồi mới bật.
DEFAULT_SECTIONS = "diem_noi_bat,thong_so,doi_tuong,huong_dan,su_co,video"


def write_flat(blocks: dict[str, dict[str, Any]], out_path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, headers in SHEETS.items():
        wb.create_sheet(name).append(headers)

    for sku, data in blocks.items():
        # cong_bo mặc định 0: xuất bản trang công khai là quyết định của người
        # biên tập sau khi đọc lại nội dung (vd. dò giá lọt vào chi_tiet), không
        # phải việc script tự quyết khi chuyển đổi.
        wb["san_pham"].append([sku, data["ten"], "", 0, DEFAULT_SECTIONS])
        for i, item in enumerate(data["diem_noi_bat"], 1):
            wb["diem_noi_bat"].append([sku, i, item["title"], item["detail"]])
        for i, item in enumerate(data["thong_so"], 1):
            wb["thong_so"].append([sku, i, item["label"], item["value"]])
        for loai in ("suitable", "caution"):
            key = "nen_dung" if loai == "suitable" else "can_hoi_bac_si"
            for i, text in enumerate(data["doi_tuong"][loai], 1):
                wb["doi_tuong"].append([sku, key, i, text])
        for i, item in enumerate(data["huong_dan"], 1):
            wb["huong_dan"].append([sku, i, item["title"], item["detail"]])
        for i, item in enumerate(data["su_co"], 1):
            wb["su_co"].append([sku, i, item["symptom"], item["cause"], item["fix"]])
        for i, item in enumerate(data["faq"], 1):
            wb["faq"].append([sku, i, item["title"], item["detail"], 0])
        for i, item in enumerate(data["video"], 1):
            wb["video"].append([sku, i, item["label"], item["url"]])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    wb = openpyxl.load_workbook(args.source, data_only=True)
    rows = list(wb[DETAIL_SHEET].iter_rows(values_only=True))
    blocks = parse_detail(rows)
    write_flat(blocks, Path(args.out))
    print(f"Đã ghi {len(blocks)} SKU vào {args.out}")


if __name__ == "__main__":
    main()
