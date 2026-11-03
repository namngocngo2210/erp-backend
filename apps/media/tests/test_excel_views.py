"""Import/export Excel của module Media (file mẫu, tạo hàng loạt, xuất danh sách)."""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from apps.media.application.services.excel_service import (
    RowError,
    TaxonomyIndex,
    parse_uploaded_at,
)
from apps.media.infrastructure.content_excel import IMPORT_HEADERS
from apps.media.models import MediaContentOwner

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_cache")]

SOURCES = {"items": [{"id": 3, "name": "Youtube adminkhang"}]}
TYPES = {"items": [{"id": 5, "name": "Video Ads Booking"}]}
PRODUCTS = {"items": [{"id": 7, "name": "Thảm", "code": "TMS01"}]}
CATEGORIES = {"items": [{"id": 9, "name": "Máy massage chân"}]}


def _upload(rows, headers=IMPORT_HEADERS, name="noi-dung.xlsx"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(list(row))
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _routes(fake_client, *, contents=None, post=(201, {"id": 12})):
    """Định tuyến json_request theo (method, path) thay vì một return_value chung."""
    table = {
        ("GET", "/content-sources"): (200, SOURCES),
        ("GET", "/content-types"): (200, TYPES),
        ("GET", "/products"): (200, PRODUCTS),
        ("GET", "/product-categories"): (200, CATEGORIES),
        ("GET", "/contents"): (200, contents or {"items": [], "total": 0}),
        ("POST", "/contents"): post,
    }
    posts = []

    def dispatch(method, path, **kwargs):
        if method == "POST":
            posts.append(kwargs.get("json"))
        return table[(method, path)]

    fake_client.json_request.side_effect = dispatch
    return posts


def _sheet_rows(content: bytes):
    workbook = load_workbook(BytesIO(content))
    return list(workbook.active.iter_rows(values_only=True))


def test_template_has_import_headers(api, shared_viewer, connection, fake_client):
    r = api(shared_viewer).get("/api/media/contents/import-template")
    assert r.status_code == 200
    assert r["Content-Disposition"] == 'attachment; filename="mau-import-noi-dung.xlsx"'
    assert _sheet_rows(r.content)[0] == IMPORT_HEADERS


def test_import_creates_valid_rows_and_reports_bad_ones(api, shared_uploader, connection, fake_client):
    posts = _routes(fake_client)
    file = _upload([
        ("2026-09-10", "Youtube adminkhang", "Tiêu đề 1", "https://x.tk/v/1", "Video Ads Booking", "TMS01", "Máy massage chân"),
        ("10/09/2026", "youtube ADMINKHANG", "Tiêu đề 2", "https://x.tk/v/2", "", "", ""),
        ("2026-13-99", "Youtube adminkhang", "Ngày sai", "https://x.tk/v/3", "", "", ""),
        ("2026-09-10", "Nguồn lạ", "Nguồn không có", "https://x.tk/v/4", "", "", ""),
        ("2026-09-10", "Youtube adminkhang", "Sản phẩm lạ", "https://x.tk/v/5", "", "SP-KHONG-CO", ""),
        ("2026-09-10", "Youtube adminkhang", "URL sai", "khong-phai-url", "", "", ""),
        (None, None, None, None, None, None, None),
    ])
    r = api(shared_uploader).post("/api/media/contents/import", {"file": file}, format="multipart")

    assert r.status_code == 200
    assert (r.data["total_rows"], r.data["created"]) == (6, 2)
    assert [e["row"] for e in r.data["errors"]] == [4, 5, 6, 7]
    assert posts[0] == {
        "uploaded_at": "2026-09-10", "title": "Tiêu đề 1", "url": "https://x.tk/v/1",
        "source_id": 3, "content_type_id": 5, "product_ids": [7], "category_ids": [9],
    }
    # Dòng 2: ngày dd/mm/yyyy, tên nguồn khác hoa/thường vẫn khớp; không tag.
    assert posts[1]["uploaded_at"] == "2026-09-10" and posts[1]["source_id"] == 3
    assert posts[1]["product_ids"] == [] and posts[1]["content_type_id"] is None
    assert MediaContentOwner.objects.filter(remote_id=12, user=shared_uploader).exists()


def test_import_forbidden_for_viewer(api, shared_viewer, connection, fake_client):
    _routes(fake_client)
    file = _upload([("2026-09-10", "Youtube adminkhang", "T", "https://x.tk/v/1", "", "", "")])
    r = api(shared_viewer).post("/api/media/contents/import", {"file": file}, format="multipart")
    assert (r.status_code, r.data["code"]) == (403, "media_forbidden")
    fake_client.json_request.assert_not_called()


def test_import_rejects_missing_file_and_bad_workbook(api, shared_uploader, connection, fake_client):
    _routes(fake_client)
    client = api(shared_uploader)
    r = client.post("/api/media/contents/import", {}, format="multipart")
    assert (r.status_code, r.data["code"]) == (400, "missing_file")

    junk = SimpleUploadedFile("a.xlsx", b"khong phai xlsx", content_type="application/vnd.ms-excel")
    r = client.post("/api/media/contents/import", {"file": junk}, format="multipart")
    assert (r.status_code, r.data["code"]) == (400, "invalid_excel")


def test_import_rejects_file_missing_required_columns(api, shared_uploader, connection, fake_client):
    _routes(fake_client)
    file = _upload([("2026-09-10", "T")], headers=("uploaded_at", "title"))
    r = api(shared_uploader).post("/api/media/contents/import", {"file": file}, format="multipart")
    assert (r.status_code, r.data["code"]) == (400, "invalid_excel")
    assert "source_name" in r.data["detail"]


def test_import_keeps_going_when_upstream_rejects_one_row(api, shared_uploader, connection, fake_client, upstream_error):
    calls = {"n": 0}

    def dispatch(method, path, **kwargs):
        if method == "GET":
            return {
                "/content-sources": (200, SOURCES), "/content-types": (200, TYPES),
                "/products": (200, PRODUCTS), "/product-categories": (200, CATEGORIES),
            }[path]
        calls["n"] += 1
        if calls["n"] == 1:
            raise upstream_error("duplicate_url", 409)
        return (201, {"id": 21})

    fake_client.json_request.side_effect = dispatch
    file = _upload([
        ("2026-09-10", "Youtube adminkhang", "Trùng", "https://x.tk/v/1", "", "", ""),
        ("2026-09-10", "Youtube adminkhang", "OK", "https://x.tk/v/2", "", "", ""),
    ])
    r = api(shared_uploader).post("/api/media/contents/import", {"file": file}, format="multipart")
    assert (r.status_code, r.data["created"]) == (200, 1)
    assert r.data["errors"][0]["row"] == 2 and r.data["created_ids"] == [21]


def test_export_writes_filtered_contents(api, shared_viewer, connection, fake_client):
    contents = {
        "items": [
            {
                "id": 1, "content_code": "ND001", "uploaded_at": "2026-09-10T00:00:00Z",
                "title": "Tiêu đề", "url": "https://x.tk/v/1",
                "source": {"id": 3, "name": "Youtube adminkhang"},
                "content_type": {"id": 5, "name": "Video Ads Booking"},
                "products": [{"id": 7, "name": "Thảm"}], "categories": None,
                "overall_rating": "good", "video_status": "success",
            }
        ],
        "total": 1,
    }
    _routes(fake_client, contents=contents)
    r = api(shared_viewer).get("/api/media/contents/export?source_id=3&page_size=10")

    assert r.status_code == 200
    assert r["Content-Disposition"].startswith('attachment; filename="noi-dung-media-')
    rows = _sheet_rows(r.content)
    assert rows[0][: len(IMPORT_HEADERS)] == IMPORT_HEADERS
    # `categories: null` (nil slice của Go) → ô trống, openpyxl đọc lại là None.
    assert rows[1] == (
        "2026-09-10", "Youtube adminkhang", "Tiêu đề", "https://x.tk/v/1",
        "Video Ads Booking", "Thảm", None, "ND001", "good", "success",
    )
    # Bộ lọc được forward, còn phân trang thì service tự quyết.
    query = dict(fake_client.json_request.call_args.kwargs["params"])
    assert query["source_id"] == "3" and query["page_size"] == "200"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-09-10", "2026-09-10"), ("10/09/2026", "2026-09-10"), ("10-09-2026", "2026-09-10"),
     ("2026-09-10 00:00:00", "2026-09-10"), ("46275", "2026-09-10")],
)
def test_parse_uploaded_at_accepts_common_formats(raw, expected):
    assert parse_uploaded_at(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "hôm qua", "2026-13-01"])
def test_parse_uploaded_at_rejects_junk(raw):
    with pytest.raises(RowError):
        parse_uploaded_at(raw)


def test_taxonomy_index_matches_code_name_and_no_accent():
    index = TaxonomyIndex.build([{"id": 7, "name": "Thảm cỏ", "code": "TMS01"}])
    assert index.get("Thảm cỏ") == 7
    assert index.get("  tham co ") == 7
    assert index.get("TMS01") == 7
    assert index.get("TMS01 – Thảm cỏ") == 7
    assert index.get("Không có") is None
