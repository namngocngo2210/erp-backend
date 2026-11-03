# backend/apps/orders/tests/test_product_profile_api.py
"""Test API quản trị nội dung trang sản phẩm (CRM)."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.catalog.models import Product, ProductProfile, ProductProfileImage
from apps.users.tests.helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff" + b"0" * 64
# RIFF nhưng KHÔNG phải WebP (vd. .wav) — RIFF một mình không đủ, phải khớp
# "WEBP" ở byte 8-12 mới được coi là ảnh.
RIFF_WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + b"0" * 64


@pytest.fixture
def product():
    return Product.objects.create(name="Bồn ngâm chân", sku="BNC02")


@pytest.fixture
def editor_client():
    user = make_user_with_permissions("pp-editor", ["products:view", "products:edit"])
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def viewer_client():
    user = make_user_with_permissions("pp-viewer", ["products:view"])
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture(autouse=True)
def _fake_storage(monkeypatch):
    """MinIO giả trong bộ nhớ — test không cần boto3 lẫn MinIO thật."""
    store: dict[str, bytes] = {}

    def _put(key, data, content_type):
        store[key] = data
        return key

    monkeypatch.setattr("apps.orders.infrastructure.product_storage.put_object", _put)
    monkeypatch.setattr(
        "apps.orders.infrastructure.product_storage.delete_object",
        lambda key: store.pop(key, None),
    )
    return store


def test_get_returns_empty_profile_when_none_exists(editor_client, product):
    response = editor_client.get(f"/api/orders/products/{product.id}/profile")

    assert response.status_code == 200
    assert response.json()["specs"] == []
    assert response.json()["is_published"] is False


def test_get_returns_product_name_with_and_without_profile(editor_client, product):
    """Màn "Xem trước" trong CRM dựng lại `<h1>` của trang public từ trường này.

    Phải có mặt ở CẢ hai nhánh của `_serialize_profile`: profile chưa tồn tại
    (instance `ProductProfile` chưa lưu, `pk is None`) và profile đã lưu.
    """
    empty = editor_client.get(f"/api/orders/products/{product.id}/profile")
    assert empty.json()["product_name"] == "Bồn ngâm chân"

    ProductProfile.objects.create(product=product, is_published=True)
    saved = editor_client.get(f"/api/orders/products/{product.id}/profile")
    assert saved.json()["product_name"] == "Bồn ngâm chân"


def test_put_saves_display_name_without_touching_product_name(editor_client, product):
    """`display_name` chỉ đổi tiêu đề trang public; tên sản phẩm do KiotViet làm chủ."""
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"display_name": "Bồn ngâm chân Delax DX-2000", "visible_sections": []},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Bồn ngâm chân Delax DX-2000"
    assert ProductProfile.objects.get(product=product).display_name == (
        "Bồn ngâm chân Delax DX-2000"
    )
    product.refresh_from_db()
    assert product.name == "Bồn ngâm chân"


def test_put_ignores_product_name_from_client(editor_client, product):
    """`product_name` là read-only — gửi lên không được đổi tên sản phẩm thật."""
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"product_name": "Tên bịa", "visible_sections": []},
        format="json",
    )

    assert response.status_code == 200
    product.refresh_from_db()
    assert product.name == "Bồn ngâm chân"


def test_put_creates_profile(editor_client, product):
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"short_description": "Tóm tắt", "specs": [{"label": "Công suất", "value": "800W"}],
         "visible_sections": ["thong_so"], "is_published": True},
        format="json",
    )

    assert response.status_code == 200
    profile = ProductProfile.objects.get(product=product)
    assert profile.specs == [{"label": "Công suất", "value": "800W"}]
    assert profile.updated_by is not None


def test_put_rejects_unknown_section(editor_client, product):
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"visible_sections": ["khong_ton_tai"]},
        format="json",
    )

    assert response.status_code == 400


def test_put_rejects_non_http_video_url(editor_client, product):
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"videos": [{"label": "X", "url": "javascript:alert(1)"}]},
        format="json",
    )

    assert response.status_code == 400


def test_put_rejects_video_item_that_is_not_an_object(editor_client, product):
    """videos: ["javascript:alert(1)"] (chuỗi thay vì object) từng ném
    AttributeError khi validate_videos gọi item.get() trên một str, biến thành
    500 thay vì 400 bình thường."""
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile",
        {"videos": ["javascript:alert(1)"]},
        format="json",
    )

    assert response.status_code == 400


def test_reorder_rejects_non_integer_image_id(editor_client, product):
    """{"image_ids": ["a"]} từng ném ValueError ở int(i), biến thành 500."""
    profile = ProductProfile.objects.create(product=product)
    ProductProfileImage.objects.create(profile=profile, object_key="a.png", content_type="image/png")

    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile/images/order",
        {"image_ids": ["a"]},
        format="json",
    )

    assert response.status_code == 400


def test_reorder_on_nonexistent_product_returns_404(editor_client):
    """3 endpoint anh em (GET/PUT profile, POST images, DELETE image) đều 404 cho
    id không tồn tại — endpoint reorder từng là ngoại lệ duy nhất, trả 200 với
    profile rỗng."""
    response = editor_client.put(
        "/api/orders/products/999999/profile/images/order",
        {"image_ids": []},
        format="json",
    )

    assert response.status_code == 404


def test_viewer_cannot_write(viewer_client, product):
    response = viewer_client.put(
        f"/api/orders/products/{product.id}/profile", {"short_description": "x"}, format="json"
    )

    assert response.status_code == 403


def test_viewer_cannot_upload_image(viewer_client, product):
    """products:view thôi thì không được ghi ảnh — require_permission("products:edit")
    trên route này CHƯA từng bị test chạm tới; niềm tin "decorator quen thuộc thì
    chắc đúng" từng đánh lừa Task 7 (throttle thiếu decorator lại nghĩa là ngược
    lại), nên phải tự tay xác nhận từng route thay vì suy luận."""
    response = viewer_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.png", PNG, content_type="image/png")},
        format="multipart",
    )

    assert response.status_code == 403
    assert ProductProfileImage.objects.count() == 0


def test_viewer_cannot_delete_image(viewer_client, product):
    profile = ProductProfile.objects.create(product=product)
    image = ProductProfileImage.objects.create(
        profile=profile, object_key="a.png", content_type="image/png"
    )

    response = viewer_client.delete(
        f"/api/orders/products/{product.id}/profile/images/{image.id}"
    )

    assert response.status_code == 403
    assert ProductProfileImage.objects.filter(id=image.id).exists()


def test_viewer_cannot_reorder_images(viewer_client, product):
    profile = ProductProfile.objects.create(product=product)
    first = ProductProfileImage.objects.create(
        profile=profile, object_key="a.png", content_type="image/png", sort_order=0
    )
    second = ProductProfileImage.objects.create(
        profile=profile, object_key="b.png", content_type="image/png", sort_order=1
    )

    response = viewer_client.put(
        f"/api/orders/products/{product.id}/profile/images/order",
        {"image_ids": [second.id, first.id]},
        format="json",
    )

    assert response.status_code == 403
    assert list(profile.images.values_list("id", flat=True)) == [first.id, second.id]


def test_upload_image_creates_record(editor_client, product, _fake_storage):
    response = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.png", PNG, content_type="image/png")},
        format="multipart",
    )

    assert response.status_code == 201
    assert ProductProfileImage.objects.filter(profile__product=product).count() == 1
    # Không chỉ có row DB — phải chắc bytes THẬT SỰ nằm trong storage. Thiếu
    # assertion này thì bỏ hẳn lời gọi put_object test vẫn xanh.
    key = ProductProfileImage.objects.get(profile__product=product).object_key
    assert _fake_storage[key] == PNG


def test_upload_rejects_non_image_even_if_client_lies(editor_client, product):
    """Client khai image/png nhưng nội dung là text — tin magic bytes, không tin header."""
    response = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.png", b"<html>not an image</html>", content_type="image/png")},
        format="multipart",
    )

    assert response.status_code == 400
    assert ProductProfileImage.objects.count() == 0


def test_upload_rejects_riff_that_is_not_webp(editor_client, product):
    """RIFF một mình chưa đủ — file .wav cũng bắt đầu bằng RIFF nhưng không phải
    WebP. Chốt hành vi này bằng test vì lần refactor sau rất dễ đơn giản hoá
    nhầm điều kiện data[8:12] == b"WEBP"."""
    response = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.wav", RIFF_WAV, content_type="image/webp")},
        format="multipart",
    )

    assert response.status_code == 400
    assert ProductProfileImage.objects.count() == 0


def test_upload_rejects_file_over_5mb(editor_client, product):
    big = JPEG + b"0" * (5 * 1024 * 1024)

    response = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("big.jpg", big, content_type="image/jpeg")},
        format="multipart",
    )

    assert response.status_code == 400


def test_upload_rejects_ninth_image(editor_client, product):
    profile = ProductProfile.objects.create(product=product)
    for i in range(8):
        ProductProfileImage.objects.create(profile=profile, object_key=f"{i}.png", content_type="image/png")

    response = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.png", PNG, content_type="image/png")},
        format="multipart",
    )

    assert response.status_code == 400


def test_delete_image_removes_object(
    editor_client, product, _fake_storage, django_capture_on_commit_callbacks
):
    upload = editor_client.post(
        f"/api/orders/products/{product.id}/profile/images",
        {"file": SimpleUploadedFile("a.png", PNG, content_type="image/png")},
        format="multipart",
    )
    image_id = upload.json()["id"]
    key = ProductProfileImage.objects.get(id=image_id).object_key
    # Xác nhận object THẬT SỰ có trong storage trước khi xoá — nếu không, test
    # bên dưới "key not in _fake_storage" pass ngay cả khi put_object chưa từng
    # chạy, biến bài test xoá thành vô nghĩa.
    assert _fake_storage[key] == PNG

    # delete_image dọn object MinIO qua transaction.on_commit — test django_db mặc
    # định bọc mỗi test trong 1 transaction rollback nên on_commit không tự kích
    # hoạt; dùng fixture của pytest-django để giả lập commit (mẫu:
    # apps/best_express/tests/test_webhook_service.py).
    with django_capture_on_commit_callbacks(execute=True):
        response = editor_client.delete(
            f"/api/orders/products/{product.id}/profile/images/{image_id}"
        )

    assert response.status_code == 204
    assert ProductProfileImage.objects.count() == 0
    assert key not in _fake_storage


def test_reorder_images(editor_client, product):
    profile = ProductProfile.objects.create(product=product)
    first = ProductProfileImage.objects.create(profile=profile, object_key="a.png", content_type="image/png", sort_order=0)
    second = ProductProfileImage.objects.create(profile=profile, object_key="b.png", content_type="image/png", sort_order=1)

    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile/images/order",
        {"image_ids": [second.id, first.id]},
        format="json",
    )

    assert response.status_code == 200
    assert list(profile.images.values_list("id", flat=True)) == [second.id, first.id]


def test_reorder_on_product_without_profile_returns_get_shape(editor_client, product):
    """Không có ProductProfile row nào cho sản phẩm này. Response phải cùng shape
    rỗng với GET /profile (specs=[], is_published=False, ...), không phải shape
    "initial value" của DRF khi serialize None — hai dạng rỗng khác nhau cho
    cùng một resource sẽ làm frontend Task 13 phải xử lý hai lần."""
    response = editor_client.put(
        f"/api/orders/products/{product.id}/profile/images/order",
        {"image_ids": []},
        format="json",
    )
    expected = editor_client.get(f"/api/orders/products/{product.id}/profile").json()

    assert response.status_code == 200
    assert response.json() == expected
    assert response.json()["specs"] == []
    assert response.json()["is_published"] is False
