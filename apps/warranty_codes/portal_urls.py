from django.urls import path

from apps.warranty_codes.transport import portal_views

urlpatterns = [
    path("activate", portal_views.activate, name="warranty-portal-activate"),
    path("track", portal_views.track, name="warranty-portal-track"),
    path("products/<str:code>", portal_views.product_page, name="warranty-portal-product"),
    path(
        "product-images/<int:image_id>",
        portal_views.product_image,
        name="warranty-portal-product-image",
    ),
]
