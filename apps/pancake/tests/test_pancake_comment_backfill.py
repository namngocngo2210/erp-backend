"""Backfill comment cũ: quét theo cửa sổ, chỉ gọi chi tiết cho hội thoại COMMENT,
bỏ qua hội thoại đã có comment (trừ refresh), không đụng lead/cursor, sinh token
khi page chưa có mà không bật poll, lỗi một hội thoại không chặn page."""

from datetime import timedelta

import pytest

from apps.pancake.application.services.comment_backfill_service import PancakeCommentBackfillService
from apps.pancake.infrastructure.pancake_client import ConversationDTO, PancakeError, PancakePageTokenRejected
from apps.pancake.models import PancakeComment, PancakeLead
from apps.pancake.tests.test_pancake_comments import _comment, _comment_conv, _detail
from apps.pancake.tests.test_pancake_poll_service import T0, FakeClient, page  # noqa: F401

pytestmark = pytest.mark.django_db


def _inbox_conv(id="inbox-1"):
    return ConversationDTO(
        id=id, type="INBOX", inserted_at=T0, updated_at=T0, has_phone=True, phones=["0912345678"],
        customer_name="Khách", customer_id="c", post_id="",
    )


def _run(client, page, days=30, refresh=False):  # noqa: F811
    service = PancakeCommentBackfillService(client)
    return service.backfill_page(page, since=T0 - timedelta(days=days), until=T0, refresh=refresh)


def test_chi_luu_comment_khong_tao_lead_khong_doi_cursor(page):  # noqa: F811
    conv = _comment_conv(phones=["0912345678"])
    client = FakeClient(
        pages={1: [conv, _inbox_conv()]},
        details={conv.id: _detail([_comment("c-1", "Xin giá")], phones=["0912345678"])},
    )
    cursor = page.polled_until

    result = _run(client, page)

    assert result.comments_saved == 1 and result.comment_conversations == 1
    assert [c for c in client.calls if c[0] == "detail"] == [("detail", conv.id)]
    assert PancakeComment.objects.get().message == "Xin giá"
    assert not PancakeLead.objects.exists()
    page.refresh_from_db()
    assert page.polled_until == cursor


def test_quet_nhieu_cua_so(page):  # noqa: F811
    client = FakeClient(pages={1: []})
    _run(client, page, days=75)
    assert [c for c in client.calls if c[0] == "list"] == [("list", 1, "ptok")] * 3


def test_bo_qua_hoi_thoai_da_co_comment_tru_refresh(page):  # noqa: F811
    conv = _comment_conv()
    client = FakeClient(pages={1: [conv]}, details={conv.id: _detail([_comment("c-1", "Xin giá")])})
    _run(client, page)
    client.calls.clear()

    assert _run(client, page).skipped_stored == 1
    assert not [c for c in client.calls if c[0] == "detail"]
    assert _run(client, page, refresh=True).comments_saved == 1


def test_page_chua_co_token_thi_sinh_token_khong_bat_poll(page):  # noqa: F811
    page.page_access_token = ""
    page.is_enabled = False
    page.save()
    client = FakeClient(pages={1: []})

    _run(client, page)

    page.refresh_from_db()
    assert client.token_calls == 1 and page.page_access_token == "new-token" and not page.is_enabled


def test_token_bi_tu_choi_thi_sinh_lai_mot_lan(page):  # noqa: F811
    client = FakeClient(pages={1: []})
    client.reject_tokens.add("ptok")

    result = _run(client, page)

    assert client.token_calls == 1 and result.error == ""


def test_loi_mot_hoi_thoai_khong_chan_page(page):  # noqa: F811
    bad, good = _comment_conv(id="bad"), _comment_conv(id="good")

    class Flaky(FakeClient):
        def get_conversation_detail(self, page_id, page_token, conversation_id):
            if conversation_id == "bad":
                raise PancakeError("HTTP 500")
            return super().get_conversation_detail(page_id, page_token, conversation_id)

    client = Flaky(pages={1: [bad, good]}, details={"good": _detail([_comment("c-9", "Ib")])})

    result = _run(client, page)

    assert result.errors == 1 and result.comments_saved == 1


def test_token_hong_han_thi_dung_page_co_loi(page):  # noqa: F811
    client = FakeClient(pages={1: []})
    client.reject_tokens.update({"ptok", "new-token"})

    result = _run(client, page)

    assert "PancakePageTokenRejected" in result.error
    assert isinstance(PancakePageTokenRejected("x"), PancakeError)
