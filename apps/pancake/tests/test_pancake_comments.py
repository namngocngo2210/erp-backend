"""Lưu comment Facebook từ hội thoại Pancake COMMENT (comment_store + poll_service).

Chốt: comment lấy từ chính response chi tiết của poll lead (không thêm request),
upsert theo comment_id (lượt sau cập nhật is_removed), đánh dấu trả lời của page,
hội thoại INBOX không sinh comment, lỗi lưu comment không chặn lead/cursor."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from apps.pancake.infrastructure.pancake_client import (
    CommentDTO,
    ConversationDetail,
    ConversationDTO,
    PancakeClient,
    PostDTO,
)
from apps.pancake.models import PancakeComment, PancakeLead, PancakePost
from apps.pancake.tests.test_pancake_poll_service import CREATE_ORDER, T0, FakeClient, _khong_doi, _service, page  # noqa: F401

pytestmark = pytest.mark.django_db

PAGE_FB_ID = "945997011940587"
POST_ID = f"{PAGE_FB_ID}_122139925179352364"


def _comment_conv(id="122139925179352364_155", updated_at=T0, phones=()):
    return ConversationDTO(
        id=id, type="COMMENT", inserted_at=updated_at - timedelta(minutes=5), updated_at=updated_at,
        has_phone=bool(phones), phones=list(phones), customer_name="Sóc Nâu", customer_id="cust-1",
        post_id=POST_ID,
    )


def _comment(id, text, from_id="2781", from_name="Sóc Nâu", parent_id=None, removed=False, at=T0):
    return CommentDTO(
        id=id, parent_id=parent_id or id, from_id=from_id, from_name=from_name, message=text,
        inserted_at=at, has_phone=False, phone_info=None, attachments=[], like_count=0,
        is_hidden=False, is_removed=removed,
    )


def _detail(comments, post_message="Bài TMS01", phones=()):
    return ConversationDetail(
        phones=list(phones), customer_name="Sóc Nâu", customer_id="cust-1", phone_message_at=None,
        post=PostDTO(id=POST_ID, message=post_message, raw={"id": POST_ID, "message": post_message}),
        comments=list(comments),
    )


def test_hoi_thoai_comment_luu_bai_va_comment(page):
    conv = _comment_conv()
    client = FakeClient(
        pages={1: [conv]},
        details={conv.id: _detail([
            _comment("c-1", "Cho xin giá", at=T0 - timedelta(minutes=3)),
            _comment("c-2", "Dạ shop inbox ạ", from_id=PAGE_FB_ID, from_name="DELAX", parent_id="c-1"),
        ])},
    )
    result = _service(client).poll_page(page)

    assert result.error == "" and result.comments_saved == 2
    post = PancakePost.objects.get()
    assert (post.post_id, post.page_id, post.message) == (POST_ID, page.id, "Bài TMS01")
    goc = PancakeComment.objects.get(comment_id="c-1")
    assert (goc.message, goc.from_name, goc.is_page_reply, goc.post_id) == ("Cho xin giá", "Sóc Nâu", False, post.id)
    assert goc.conversation_id == conv.id and goc.pancake_customer_id == "cust-1"
    assert goc.commented_at == T0 - timedelta(minutes=3)
    tra_loi = PancakeComment.objects.get(comment_id="c-2")
    assert tra_loi.is_page_reply is True and tra_loi.parent_id == "c-1"


def test_luot_sau_cap_nhat_comment_bi_xoa_khong_nhan_doi(page):
    conv = _comment_conv()
    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "Lừa đảo")])})
    _service(client).poll_page(page)

    conv2 = _comment_conv(updated_at=T0 + timedelta(minutes=3))
    client = FakeClient(pages={1: [conv2]}, details={conv2.id: _detail([_comment("c-1", "Lừa đảo", removed=True)])})
    _service(client, now=T0 + timedelta(minutes=3)).poll_page(page)

    comment = PancakeComment.objects.get()
    assert comment.is_removed is True and comment.message == "Lừa đảo"
    assert PancakePost.objects.count() == 1


def test_bai_sua_noi_dung_thi_cap_nhat(page):
    conv = _comment_conv()
    _service(FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "a")])})).poll_page(page)
    conv2 = _comment_conv(updated_at=T0 + timedelta(minutes=3))
    client = FakeClient(pages={1: [conv2]}, details={conv2.id: _detail([_comment("c-1", "a")], post_message="Bài sửa")})
    _service(client, now=T0 + timedelta(minutes=3)).poll_page(page)
    assert PancakePost.objects.get().message == "Bài sửa"


def test_hoi_thoai_inbox_khong_luu_comment(page):
    conv = ConversationDTO(id="c1", type="INBOX", inserted_at=T0, updated_at=T0, has_phone=False)
    client = FakeClient(pages={1: [conv]}, details={"c1": _detail([_comment("c-1", "x")])})
    result = _service(client).poll_page(page)
    assert result.comments_saved == 0
    assert not PancakeComment.objects.exists() and not PancakePost.objects.exists()


def test_comment_co_sdt_van_tao_lead_va_luu_comment(page):
    conv = _comment_conv(phones=["0901234567"])
    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "0901234567")], phones=["0901234567"])})
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.comments_saved) == (1, 1)


def test_lead_cu_chua_co_comment_trong_kho_van_goi_detail(page):
    """Lead có từ trước khi bắt đầu lưu comment: hội thoại không mới hơn lead vẫn
    phải gọi chi tiết một lần để lưu comment; lần sau thì bỏ qua như cũ."""
    conv = _comment_conv(updated_at=T0 - timedelta(minutes=1))
    PancakeLead.objects.create(page=page, channel=page.channel, conversation_id=conv.id, phone_raw="0901234567",
                               phone="0901234567", foxia_kind="messenger", phone_given_at=T0)
    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "x")])})
    _service(client).poll_page(page)
    assert ("detail", conv.id) in client.calls and PancakeComment.objects.count() == 1

    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "x")])})
    _service(client).poll_page(page)
    assert ("detail", conv.id) not in client.calls


def test_loi_luu_comment_khong_chan_lead_va_cursor(page):
    conv = _comment_conv(phones=["0901234567"])
    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "x")], phones=["0901234567"])})
    with patch(CREATE_ORDER, side_effect=_khong_doi), \
            patch("apps.pancake.application.services.comment_store.save_comments", side_effect=RuntimeError("db")):
        result = _service(client).poll_page(page)
    assert result.error == "" and result.leads_created == 1 and result.comments_saved == 0
    page.refresh_from_db()
    assert page.polled_until == T0


# ── client ─────────────────────────────────────────────────────────────────


def _resp(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_client_detail_tach_post_va_comment():
    """Hình dạng đo bằng token thật 24/09/2026."""
    payload = {
        "success": True,
        "customers": [{"id": "uuid-1", "name": "Sóc Nâu"}],
        "post": {"id": POST_ID, "message": "🌦️ THỜI TIẾT THAY ĐỔI", "type": "photo"},
        "messages": [
            {"id": "c-1", "type": "COMMENT", "message": "&lt;div&gt;Cho xin gia&lt;/div&gt;",
             "original_message": "Cho xin gia", "from": {"id": "2781", "name": "Sóc Nâu"},
             "inserted_at": "2026-09-21T10:52:12.000000", "parent_id": "c-1", "is_hidden": False,
             "is_removed": True, "has_phone": False, "attachments": [], "like_count": 2},
            {"id": "c-2", "type": "COMMENT", "message": "&lt;div&gt;Dạ &amp; vâng&lt;/div&gt;",
             "from": {"id": PAGE_FB_ID, "name": "DELAX"}, "inserted_at": "2026-09-21T10:53:00", "parent_id": "c-1"},
            {"id": "m-3", "type": "INBOX", "message": "không phải comment"},
        ],
    }
    with patch("apps.pancake.infrastructure.pancake_client.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")

    assert detail.post.id == POST_ID and detail.post.raw["type"] == "photo"
    assert [c.id for c in detail.comments] == ["c-1", "c-2"]
    first, reply = detail.comments
    assert (first.message, first.from_name, first.is_removed, first.like_count) == ("Cho xin gia", "Sóc Nâu", True, 2)
    assert reply.message == "Dạ & vâng" and reply.parent_id == "c-1"


def test_client_detail_inbox_khong_co_post():
    with patch("apps.pancake.infrastructure.pancake_client.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp({"success": True, "messages": []})
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")
    assert detail.post is None and detail.comments == []
