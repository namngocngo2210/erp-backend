"""View proxy Media — mỏng: pick params → MediaProxy.forward → Response."""

from __future__ import annotations

from django.http import Http404
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.media.application.services.proxy_service import MediaProxy, describe_me
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaError
from apps.media.transport import params as P
from apps.media.transport.errors import media_errors


def _auth(methods):
    """@api_view + IsAuthenticated + media_errors, đúng thứ tự (media_errors trong cùng)."""

    def deco(fn):
        return api_view(methods)(permission_classes([IsAuthenticated])(media_errors(fn)))

    return deco


def _passthrough(status: int, data):
    return Response(data, status=status)


def _new_proxy(request) -> MediaProxy:
    proxy = MediaProxy(request.user)
    request._media_mode = proxy.cred.kind
    return proxy


def _body(request) -> dict:
    """Đảm bảo body là JSON object trước khi gọi ads-crm; nội dung để ads-crm tự validate."""
    if not isinstance(request.data, dict):
        raise MediaError("Body phải là JSON object.", status=http.HTTP_400_BAD_REQUEST, code="invalid_json")
    return request.data


def _remote_id(data) -> int | None:
    rid = data.get("id") if isinstance(data, dict) else None
    return int(rid) if isinstance(rid, int) else None


@_auth(["GET"])
def me(request):
    return Response(describe_me(request.user))


@_auth(["GET", "POST"])
def contents_collection(request):
    proxy = _new_proxy(request)
    if request.method == "POST":
        code, data = proxy.forward("POST", "/contents", action=Action.CONTENT_CREATE, json=_body(request))
        if (rid := _remote_id(data)) is not None:
            proxy.record_content_owner(rid)
        return _passthrough(code, data)
    return _passthrough(*proxy.forward("GET", "/contents", action=Action.READ,
                                       params=P.pick(request.query_params, P.CONTENTS_LIST)))


@_auth(["GET", "DELETE"])
def content_detail(request, content_id: int):
    proxy = _new_proxy(request)
    if request.method == "DELETE":
        return _passthrough(*proxy.forward("DELETE", f"/contents/{content_id}", action=Action.CONTENT_DELETE))
    return _passthrough(*proxy.forward("GET", f"/contents/{content_id}", action=Action.READ))


@_auth(["POST"])
def content_duplicate(request, content_id: int):
    proxy = _new_proxy(request)
    code, data = proxy.forward("POST", f"/contents/{content_id}/duplicate", action=Action.CONTENT_CREATE)
    if (rid := _remote_id(data)) is not None:
        proxy.record_content_owner(rid)
    return _passthrough(code, data)


@_auth(["PATCH"])
def content_upload_info(request, content_id: int):
    proxy = _new_proxy(request)
    return _passthrough(*proxy.forward("PATCH", f"/contents/{content_id}/upload-info", action=Action.CONTENT_EDIT_UPLOAD,
                                       json=_body(request), owner_check=lambda: proxy.owns_content(content_id)))


@_auth(["PATCH"])
def content_marketing_info(request, content_id: int):
    proxy = _new_proxy(request)
    return _passthrough(*proxy.forward("PATCH", f"/contents/{content_id}/marketing-info", action=Action.CONTENT_EDIT_MARKETING,
                                       json=_body(request), owner_check=lambda: proxy.owns_content(content_id)))


@_auth(["GET", "POST"])
def content_evaluations(request, content_id: int):
    proxy = _new_proxy(request)
    if request.method == "POST":
        return _passthrough(*proxy.forward("POST", f"/contents/{content_id}/ad-evaluations",
                                           action=Action.EVALUATION_WRITE, json=_body(request)))
    return _passthrough(*proxy.forward("GET", f"/contents/{content_id}/ad-evaluations", action=Action.READ))


@_auth(["PATCH", "DELETE"])
def evaluation_detail(request, evaluation_id: int):
    proxy = _new_proxy(request)
    if request.method == "DELETE":
        return _passthrough(*proxy.forward("DELETE", f"/ad-evaluations/{evaluation_id}", action=Action.EVALUATION_WRITE))
    return _passthrough(*proxy.forward("PATCH", f"/ad-evaluations/{evaluation_id}",
                                       action=Action.EVALUATION_WRITE, json=_body(request)))


@_auth(["GET", "POST"])
def taxonomy_collection(request, group: str):
    if group not in P.TAXONOMY_GROUPS:
        raise Http404
    proxy = _new_proxy(request)
    if request.method == "POST":
        action = Action.TAXONOMY_WRITE_SOURCES if group == "content-sources" else Action.TAXONOMY_WRITE
        code, data = proxy.forward("POST", f"/{group}", action=action, json=_body(request))
        MediaProxy.invalidate_taxonomy(group)
        return _passthrough(code, data)
    include_inactive = request.query_params.get("include_inactive") == "true"
    return Response(proxy.taxonomy(group, include_inactive))


@_auth(["PATCH", "DELETE"])
def taxonomy_detail(request, group: str, item_id: int):
    if group not in P.TAXONOMY_GROUPS:
        raise Http404
    proxy = _new_proxy(request)
    if request.method == "DELETE":
        code, data = proxy.forward("DELETE", f"/{group}/{item_id}", action=Action.TAXONOMY_WRITE)
    else:
        code, data = proxy.forward("PATCH", f"/{group}/{item_id}", action=Action.TAXONOMY_WRITE, json=_body(request))
    MediaProxy.invalidate_taxonomy(group)
    return _passthrough(code, data)


@_auth(["GET", "POST"])
def proposals_collection(request):
    proxy = _new_proxy(request)
    if request.method == "POST":
        code, data = proxy.forward("POST", "/proposals", action=Action.PROPOSAL_CREATE, json=_body(request))
        if (rid := _remote_id(data)) is not None:
            proxy.record_proposal_owner(rid)
        return _passthrough(code, data)
    return _passthrough(*proxy.forward("GET", "/proposals", action=Action.READ,
                                       params=P.pick(request.query_params, P.PROPOSALS_LIST)))


@_auth(["GET", "PATCH", "DELETE"])
def proposal_detail(request, proposal_id: int):
    proxy = _new_proxy(request)
    if request.method == "DELETE":
        return _passthrough(*proxy.forward("DELETE", f"/proposals/{proposal_id}", action=Action.PROPOSAL_DELETE))
    if request.method == "PATCH":
        return _passthrough(*proxy.forward("PATCH", f"/proposals/{proposal_id}", action=Action.PROPOSAL_EDIT,
                                           json=_body(request), owner_check=lambda: proxy.owns_proposal(proposal_id)))
    return _passthrough(*proxy.forward("GET", f"/proposals/{proposal_id}", action=Action.READ))


@_auth(["GET"])
def report(request, name: str):
    if name not in ("overview", "ratings-by-channel", "content-quality"):
        raise Http404
    proxy = _new_proxy(request)
    return _passthrough(*proxy.forward("GET", f"/reports/{name}", action=Action.READ,
                                       params=P.pick(request.query_params, P.REPORTS)))
