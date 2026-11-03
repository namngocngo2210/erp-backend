"""Allowlist route Media. Không có catch-all: path không khai ở đây → 404 (spec §4.5)."""

from django.urls import path, re_path

from apps.media.transport import api_views as v
from apps.media.transport import excel_views as x
from apps.media.transport import file_views as f
from apps.media.transport import params as P
from apps.media.transport import settings_views as s

TAX = "|".join(P.TAXONOMY_GROUPS)

urlpatterns = [
    path("me", v.me, name="media-me"),
    path("contents", v.contents_collection, name="media-contents"),
    # Ba route xlsx đứng trước route :id — không đụng nhau (int converter không
    # khớp chữ) nhưng để cạnh nhau cho dễ đọc.
    path("contents/import", x.contents_import, name="media-contents-import"),
    path("contents/import-template", x.contents_import_template, name="media-contents-import-template"),
    path("contents/export", x.contents_export, name="media-contents-export"),
    path("contents/<int:content_id>", v.content_detail, name="media-content-detail"),
    path("contents/<int:content_id>/duplicate", v.content_duplicate, name="media-content-duplicate"),
    path("contents/<int:content_id>/upload-info", v.content_upload_info, name="media-content-upload-info"),
    path("contents/<int:content_id>/marketing-info", v.content_marketing_info, name="media-content-marketing-info"),
    path("contents/<int:content_id>/ad-evaluations", v.content_evaluations, name="media-content-evaluations"),
    path("contents/<int:content_id>/download", f.content_download, name="media-content-download"),
    path("contents/<int:content_id>/video-url", f.content_video_url, name="media-content-video-url"),
    path("contents/<int:content_id>/video-stream", f.content_video_stream, name="media-content-video-stream"),
    path("contents/<int:content_id>/video/cache", f.content_video_cache, name="media-content-video-cache"),
    path("ad-evaluations/<int:evaluation_id>", v.evaluation_detail, name="media-evaluation-detail"),
    path("proposals", v.proposals_collection, name="media-proposals"),
    path("proposals/<int:proposal_id>", v.proposal_detail, name="media-proposal-detail"),
    path("settings", s.settings_view, name="media-settings"),
    path("settings/test", s.settings_test, name="media-settings-test"),
    re_path(r"^reports/(?P<name>[a-z-]+)$", v.report, name="media-report"),
    re_path(rf"^(?P<group>{TAX})$", v.taxonomy_collection, name="media-taxonomy"),
    path("<str:group>/<int:item_id>", v.taxonomy_detail, name="media-taxonomy-detail"),
]
