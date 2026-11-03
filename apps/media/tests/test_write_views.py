import pytest

from apps.media.models import MediaContentOwner, MediaProposalOwner

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_cache")]
BODY = {"uploaded_at": "2026-09-04", "title": "T", "url": "https://t.tk/v/1", "source_id": 3}


def test_viewer_cannot_create_content(api, shared_viewer, connection, fake_client):
    r = api(shared_viewer).post("/api/media/contents", BODY, format="json")
    assert (r.status_code, r.data["code"]) == (403, "media_forbidden")
    fake_client.json_request.assert_not_called()


def test_uploader_creates_and_owner_recorded(api, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 12, "title": "T"})
    r = api(shared_uploader).post("/api/media/contents", BODY, format="json")
    assert r.status_code == 201 and r.data["id"] == 12
    assert fake_client.json_request.call_args.args == ("POST", "/contents")
    assert fake_client.json_request.call_args.kwargs["json"] == BODY
    assert MediaContentOwner.objects.get(remote_id=12).user == shared_uploader


def test_personal_key_does_not_record_owner(api, personal_user, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 13})
    api(personal_user).post("/api/media/contents", BODY, format="json")
    assert not MediaContentOwner.objects.filter(remote_id=13).exists()


def test_uploader_edits_only_own_content(api, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (200, {"id": 12})
    r = api(shared_uploader).patch("/api/media/contents/12/upload-info", BODY, format="json")
    assert (r.status_code, r.data["code"]) == (403, "media_not_owner")
    MediaContentOwner.objects.create(remote_id=12, user=shared_uploader)
    r = api(shared_uploader).patch("/api/media/contents/12/upload-info", BODY, format="json")
    assert r.status_code == 200


def test_marketing_edits_tags_of_any_content_but_not_upload_info(api, shared_marketing, connection, fake_client):
    fake_client.json_request.return_value = (200, {"id": 12})
    tags = {"content_type_id": 2, "product_ids": [1], "category_ids": []}
    assert api(shared_marketing).patch("/api/media/contents/12/marketing-info", tags, format="json").status_code == 200
    assert api(shared_marketing).patch("/api/media/contents/12/upload-info", BODY, format="json").status_code == 403


def test_only_admin_deletes_content(api, shared_uploader, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_uploader).delete("/api/media/contents/12").status_code == 403
    assert api(shared_admin).delete("/api/media/contents/12").status_code == 200


def test_evaluations_marketing_yes_uploader_no(api, shared_marketing, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 88})
    ev = {"rating": 4.5, "note": "ok"}
    assert api(shared_marketing).post("/api/media/contents/12/ad-evaluations", ev, format="json").status_code == 201
    assert api(shared_uploader).post("/api/media/contents/12/ad-evaluations", ev, format="json").status_code == 403
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_marketing).delete("/api/media/ad-evaluations/88").status_code == 200


def test_taxonomy_write_invalidates_cache(api, shared_admin, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (200, {"items": [{"id": 1}]})
    api(shared_admin).get("/api/media/products")
    fake_client.json_request.return_value = (201, {"id": 2, "code": "SP2"})
    assert api(shared_admin).post("/api/media/products", {"code": "SP2"}, format="json").status_code == 201
    fake_client.json_request.return_value = (200, {"items": [{"id": 1}, {"id": 2}]})
    r = api(shared_admin).get("/api/media/products")
    assert len(r.data["items"]) == 2  # cache đã xoá, gọi lại upstream
    # uploader: chỉ được POST content-sources.
    fake_client.json_request.return_value = (201, {"id": 9})
    assert api(shared_uploader).post("/api/media/content-sources", {"name": "YT"}, format="json").status_code == 201
    assert api(shared_uploader).post("/api/media/products", {"code": "X"}, format="json").status_code == 403
    assert api(shared_uploader).patch("/api/media/content-sources/9", {"name": "Y"}, format="json").status_code == 403


def test_proposals_owner_scoped_edit_and_admin_delete(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 4})
    body = {"main_key": "k", "product_id": 5}
    assert api(shared_viewer).post("/api/media/proposals", body, format="json").status_code == 201
    assert MediaProposalOwner.objects.get(remote_id=4).user == shared_viewer
    fake_client.json_request.return_value = (200, {"id": 4})
    assert api(shared_viewer).patch("/api/media/proposals/4", body, format="json").status_code == 200
    assert api(shared_viewer).patch("/api/media/proposals/5", body, format="json").status_code == 403
    assert api(shared_viewer).delete("/api/media/proposals/4").status_code == 403
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_admin).delete("/api/media/proposals/4").status_code == 200


def test_non_object_body_rejected_400(api, shared_admin, connection, fake_client):
    r = api(shared_admin).post("/api/media/contents", [1, 2], format="json")
    assert r.status_code == 400
    fake_client.json_request.assert_not_called()
