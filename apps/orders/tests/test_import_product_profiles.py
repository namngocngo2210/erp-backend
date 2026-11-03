"""Test command import nội dung sản phẩm từ file phẳng.

Dùng fixture xlsx sinh trong tmp_path, KHÔNG đọc file thật: test phải chạy được
trên máy không có backend/data/product-content.xlsx, và phải mô tả được các nhánh
biên (SKU lạ, chạy lại lần hai) mà file thật không có.
"""
from __future__ import annotations

import io

import openpyxl
import pytest
from django.core.management import call_command

from apps.catalog.models import Product, ProductProfile, ProductProfileImage

pytestmark = pytest.mark.django_db

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


def _make_file(tmp_path, rows: dict[str, list[list]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, headers in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in rows.get(name, []):
            ws.append(row)
    path = tmp_path / "content.xlsx"
    wb.save(path)
    return str(path)


@pytest.fixture
def sample_file(tmp_path):
    return _make_file(tmp_path, {
        "san_pham": [["BNC02", "Bồn ngâm chân", "Tóm tắt", 1, "diem_noi_bat,thong_so,faq"]],
        "diem_noi_bat": [["BNC02", 1, "24 bi lăn", "Kích thích huyệt đạo"]],
        "thong_so": [["BNC02", 1, "Công suất", "800W"]],
        "doi_tuong": [["BNC02", "nen_dung", 1, "Người đau mỏi chân"],
                      ["BNC02", "can_hoi_bac_si", 1, "Phụ nữ mang thai"]],
        "huong_dan": [["BNC02", 1, "Bước 1", "Mở rộng thân bồn"]],
        "su_co": [["BNC02", 1, "Không lên nguồn", "Ổ điện lỏng", "Kiểm tra ổ điện"]],
        "faq": [["BNC02", 1, "Dùng bao lâu?", "15-30 phút", 1],
                ["BNC02", 2, "Chưa biên tập", "Dạ anh/chị yên tâm ạ", 0]],
        "video": [["BNC02", 1, "Hướng dẫn dùng", "https://youtu.be/abc"]],
    })


def test_import_fills_every_content_block(sample_file):
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")

    call_command("import_product_profiles", file=sample_file)

    profile = ProductProfile.objects.get(product__sku="BNC02")
    assert profile.short_description == "Tóm tắt"
    assert profile.is_published is True
    assert profile.visible_sections == ["diem_noi_bat", "thong_so", "faq"]
    assert profile.highlights == [{"title": "24 bi lăn", "detail": "Kích thích huyệt đạo"}]
    assert profile.specs == [{"label": "Công suất", "value": "800W"}]
    assert profile.audience == {
        "suitable": ["Người đau mỏi chân"], "caution": ["Phụ nữ mang thai"],
    }
    assert profile.usage_steps == [{"title": "Bước 1", "detail": "Mở rộng thân bồn"}]
    assert profile.troubleshooting == [
        {"symptom": "Không lên nguồn", "cause": "Ổ điện lỏng", "fix": "Kiểm tra ổ điện"}
    ]
    assert profile.videos == [{"label": "Hướng dẫn dùng", "url": "https://youtu.be/abc"}]


def test_import_keeps_faq_visibility_flag(sample_file):
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")

    call_command("import_product_profiles", file=sample_file)

    profile = ProductProfile.objects.get(product__sku="BNC02")
    assert profile.faqs == [
        {"question": "Dùng bao lâu?", "answer": "15-30 phút", "visible": True},
        {"question": "Chưa biên tập", "answer": "Dạ anh/chị yên tâm ạ", "visible": False},
    ]


def test_import_is_idempotent(sample_file):
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")

    call_command("import_product_profiles", file=sample_file)
    call_command("import_product_profiles", file=sample_file)

    assert ProductProfile.objects.count() == 1
    profile = ProductProfile.objects.get(product__sku="BNC02")
    assert len(profile.highlights) == 1


def test_import_skips_unknown_sku_without_creating_product(sample_file):
    call_command("import_product_profiles", file=sample_file)

    assert Product.objects.count() == 0
    assert ProductProfile.objects.count() == 0


def test_dry_run_writes_nothing(sample_file):
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")

    call_command("import_product_profiles", file=sample_file, dry_run=True)

    assert ProductProfile.objects.count() == 0


def test_only_sku_limits_scope(tmp_path):
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    Product.objects.create(name="Bồn 2026", sku="DC30")
    path = _make_file(tmp_path, {
        "san_pham": [["BNC02", "Bồn ngâm chân", "", 1, "thong_so"],
                     ["DC30", "Bồn 2026", "", 1, "thong_so"]],
        "thong_so": [["BNC02", 1, "Công suất", "800W"], ["DC30", 1, "Công suất", "900W"]],
    })

    call_command("import_product_profiles", file=path, only_sku=["BNC02"])

    assert list(ProductProfile.objects.values_list("product__sku", flat=True)) == ["BNC02"]


def test_reimport_does_not_delete_uploaded_images(sample_file):
    """Ảnh do người upload, file Excel không có ảnh nào nên không được quyền xoá."""
    product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    call_command("import_product_profiles", file=sample_file)
    profile = ProductProfile.objects.get(product=product)
    ProductProfileImage.objects.create(profile=profile, object_key="a.jpg", content_type="image/jpeg")

    call_command("import_product_profiles", file=sample_file)

    assert ProductProfileImage.objects.filter(profile__product=product).count() == 1


def test_sku_match_is_case_insensitive(sample_file):
    """SKU trong CRM không ép chữ hoa (KiotViet sync lẫn nhập tay) — khớp
    theo iexact như import_excel.py / order_service_helpers.py đã làm, không
    được để lệch case biến thành "không có trong danh mục" giả."""
    Product.objects.create(name="Bồn ngâm chân", sku="bnc02")

    call_command("import_product_profiles", file=sample_file)

    assert ProductProfile.objects.filter(product__sku="bnc02").exists()


def test_malformed_thu_tu_does_not_abort_import(tmp_path):
    """File do nhân viên không kỹ thuật chỉnh tay — 1 ô thu_tu gõ sai (vd
    "1a") không được làm sập cả lần import; phải coi như 0 (xếp đầu) và báo
    cảnh báo nêu rõ sheet + giá trị lỗi để người dùng tìm sửa."""
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    path = _make_file(tmp_path, {
        "san_pham": [["BNC02", "Bồn ngâm chân", "", 1, "diem_noi_bat"]],
        "diem_noi_bat": [
            ["BNC02", 5, "Đúng thứ tự", "Chi tiet dung"],
            ["BNC02", "1a", "Sai thu_tu", "Chi tiet sai"],
        ],
    })
    out = io.StringIO()

    call_command("import_product_profiles", file=path, stdout=out)

    profile = ProductProfile.objects.get(product__sku="BNC02")
    assert [h["title"] for h in profile.highlights] == ["Sai thu_tu", "Đúng thứ tự"]
    assert "thu_tu không hợp lệ" in out.getvalue()
    assert "diem_noi_bat" in out.getvalue()


def test_flag_reads_openpyxl_float_representation(tmp_path):
    """openpyxl trả về 1.0 (float) cho ô người dùng gõ số nguyên 1 — so sánh
    bằng chuỗi "1" sẽ bỏ sót, khiến nội dung được đánh dấu công bố/hiển thị
    vẫn âm thầm ở trạng thái ẩn."""
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    path = _make_file(tmp_path, {
        "san_pham": [["BNC02", "Bồn ngâm chân", "", 1.0, "faq"]],
        "faq": [["BNC02", 1, "Câu hỏi", "Trả lời", 1.0]],
    })

    call_command("import_product_profiles", file=path)

    profile = ProductProfile.objects.get(product__sku="BNC02")
    assert profile.is_published is True
    assert profile.faqs == [{"question": "Câu hỏi", "answer": "Trả lời", "visible": True}]


def test_only_sku_accepts_multiple_repeated_flags(tmp_path):
    """--only-sku dùng action="append" — phải lặp lại được để chọn nhiều SKU
    cùng lúc, không chỉ 1 SKU như test_only_sku_limits_scope đã phủ."""
    Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    Product.objects.create(name="Bồn 2026", sku="DC30")
    Product.objects.create(name="Máy massage cổ", sku="MSC06")
    path = _make_file(tmp_path, {
        "san_pham": [
            ["BNC02", "Bồn ngâm chân", "", 1, "thong_so"],
            ["DC30", "Bồn 2026", "", 1, "thong_so"],
            ["MSC06", "Máy massage cổ", "", 1, "thong_so"],
        ],
        "thong_so": [
            ["BNC02", 1, "Công suất", "800W"],
            ["DC30", 1, "Công suất", "900W"],
            ["MSC06", 1, "Công suất", "700W"],
        ],
    })

    call_command("import_product_profiles", file=path, only_sku=["BNC02", "DC30"])

    imported = set(ProductProfile.objects.values_list("product__sku", flat=True))
    assert imported == {"BNC02", "DC30"}
