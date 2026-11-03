from django.urls import include, path, re_path
from django.contrib import admin

from apps.common.health import healthz

urlpatterns = [
    path("admin", admin.site.urls),
    # Healthcheck sâu (DB + Redis) cho probe ngoài — đặt TRƯỚC các include api/
    # để không lọt vào catch-all r"^api/" của apps.audit.urls.
    path("api/healthz", healthz, name="healthz"),
    re_path(r"^api/users/?", include("apps.users.urls")),
    re_path(r"^api/orders/?", include("apps.orders.urls")),
    re_path(r"^api/sale/?", include("apps.orders.urls_sale")),
    re_path(r"^api/channels/?", include("apps.channels_core.urls")),
    re_path(r"^api/customers/?", include("apps.customers.urls")),
    re_path(r"^api/webhook/?", include("apps.kiotviet.urls")),
    re_path(r"^api/best-express/?", include("apps.best_express.urls")),
    re_path(r"^api/cnv/", include("apps.cnv_loyalty.urls")),
    re_path(r"^api/callcenter/", include("apps.callcenter.urls")),
    re_path(r"^api/facebook-ads/", include("apps.facebook_ads.urls")),
    re_path(r"^api/google-ads/", include("apps.google_ads.urls")),
    re_path(r"^api/foxia/", include("apps.foxia.urls")),
    re_path(r"^api/pancake/", include("apps.pancake.urls")),
    re_path(r"^api/pancake-pos/", include("apps.pancake_pos.urls")),
    re_path(r"^api/system-settings/", include("apps.sysconfig.transport.urls")),
    re_path(r"^api/media/", include("apps.media.urls")),
    re_path(r"^api/warranty-codes/?", include("apps.warranty_codes.urls")),
    re_path(r"^api/warranty-portal/?", include("apps.warranty_codes.portal_urls")),
    re_path(r"^api/warranty/?", include("apps.warranty.urls")),
    re_path(r"^api/warehouses/?", include("apps.warehouses.urls")),
    re_path(r"^api/inventory/?", include("apps.inventory.urls")),
    re_path(r"^api/hr/?", include("apps.hr.urls")),
    re_path(r"^api/contact-flow/?", include("apps.contact_flow.urls")),
    re_path(r"^api/exports/?", include("apps.exports.urls")),
    re_path(r"^api/analytics/?", include("apps.analytics.urls")),
    re_path(r"^api/daily-report/?", include("apps.daily_report.urls")),
    re_path(r"^api/conversion-feed/?", include("apps.conversion_feed.urls")),
    # Mart `ai` cho agent Hermes — đặt TRƯỚC catch-all r"^api/" của audit.
    re_path(r"^api/ai/", include("apps.ai_mart.urls")),
    re_path(r"^api/", include("apps.audit.urls")),
]

# OpenAPI schema + Swagger UI (D7). Dev mở tự do; production yêu cầu staff/
# superuser (gate 404 với người lạ — xem config/docs_access.py). CI/FE codegen
# vẫn lấy schema qua `manage.py spectacular` (file tĩnh), không cần endpoint.
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView  # noqa: E402

from config.docs_access import gate_docs_in_production  # noqa: E402

urlpatterns += [
    path(
        "api/schema",
        gate_docs_in_production(SpectacularAPIView.as_view()),
        name="schema",
    ),
    path(
        "api/docs",
        gate_docs_in_production(SpectacularSwaggerView.as_view(url_name="schema")),
        name="swagger-ui",
    ),
]
