import pytest

from apps.catalog.models import PROFILE_SECTIONS, Product, ProductProfile, ProductProfileImage

pytestmark = pytest.mark.django_db


def test_profile_defaults_are_empty_containers():
    product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    profile = ProductProfile.objects.create(product=product)

    assert profile.short_description == ""
    assert profile.highlights == []
    assert profile.specs == []
    assert profile.audience == {}
    assert profile.usage_steps == []
    assert profile.troubleshooting == []
    assert profile.faqs == []
    assert profile.videos == []
    assert profile.visible_sections == []
    assert profile.is_published is False


def test_profile_is_one_to_one_with_product():
    product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    ProductProfile.objects.create(product=product)

    with pytest.raises(Exception):
        ProductProfile.objects.create(product=product)


def test_images_ordered_by_sort_order():
    product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    profile = ProductProfile.objects.create(product=product)
    ProductProfileImage.objects.create(profile=profile, object_key="b.jpg", content_type="image/jpeg", sort_order=1)
    ProductProfileImage.objects.create(profile=profile, object_key="a.jpg", content_type="image/jpeg", sort_order=0)

    assert [i.object_key for i in profile.images.all()] == ["a.jpg", "b.jpg"]


def test_deleting_product_cascades_to_profile_and_images():
    product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02")
    profile = ProductProfile.objects.create(product=product)
    ProductProfileImage.objects.create(profile=profile, object_key="a.jpg", content_type="image/jpeg")

    product.delete()

    assert ProductProfile.objects.count() == 0
    assert ProductProfileImage.objects.count() == 0


def test_profile_sections_constant_lists_all_content_blocks():
    assert PROFILE_SECTIONS == (
        "diem_noi_bat", "thong_so", "doi_tuong", "huong_dan", "su_co", "faq", "video",
    )
