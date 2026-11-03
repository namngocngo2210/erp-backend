from django.urls import path

from .transport.views import google_ads_web

urlpatterns = [
    path("google-ads-web", google_ads_web, name="conversion-feed-google-ads-web"),
]
