"""Đồng bộ page (upsert theo pancake_page_id, page biến mất → is_activated=False,
KHÔNG xoá) và bật page (cần kênh + platform hỗ trợ + sinh được token; cursor =
now vì không backfill)."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.pancake.application.services.page_service import (
    PageNotEligible,
    PancakePageService,
    get_client,
)
from apps.pancake.infrastructure.pancake_client import PageDTO, PancakeTokenError
from apps.pancake.models import PancakeConnection, PancakePage

pytestmark = pytest.mark.django_db


def _dto(id="945997011940587", name="DELAX", platform="facebook", **kw):
    values = {"is_activated": True, "has_active_subscription": True, "role_in_page": "ADMINISTER"}
    values.update(kw)
    return PageDTO(id=id, name=name, platform=platform, **values)


def test_get_client_none_khi_chua_co_connection():
    assert get_client() is None
    PancakeConnection.objects.create(access_token="tok")
    assert get_client() is not None


def test_sync_pages_upsert_va_danh_dau_page_bien_mat():
    conn = PancakeConnection.objects.create(access_token="tok")
    PancakePage.objects.create(pancake_page_id="old", name="Cũ", platform="facebook")
    client = MagicMock()
    client.list_pages.return_value = [_dto(), _dto(id="zl_1", name="Zalo OA", platform="zalo")]

    result = PancakePageService(client).sync_pages(conn)

    assert (result.fetched, result.created, result.updated, result.deactivated) == (2, 2, 0, 1)
    assert PancakePage.objects.get(pancake_page_id="old").is_activated is False
    page = PancakePage.objects.get(pancake_page_id="945997011940587")
    assert (page.name, page.platform, page.role_in_page, page.is_activated) == ("DELAX", "facebook", "ADMINISTER", True)
    conn.refresh_from_db()
    assert conn.auth_status == "ok"
    assert conn.last_pages_synced_at is not None

    # Lượt sau: đổi tên → updated, không sinh dòng mới.
    client.list_pages.return_value = [_dto(name="DELAX mới"), _dto(id="zl_1", name="Zalo OA", platform="zalo")]
    result = PancakePageService(client).sync_pages(conn)
    # `updated` đếm mọi page đã có (upsert), không riêng page đổi dữ liệu.
    assert (result.created, result.updated) == (0, 2)
    assert PancakePage.objects.count() == 3


def test_sync_pages_token_hong_ghi_auth_error_va_raise():
    conn = PancakeConnection.objects.create(access_token="tok")
    client = MagicMock()
    client.list_pages.side_effect = PancakeTokenError("Invalid token")
    with pytest.raises(PancakeTokenError):
        PancakePageService(client).sync_pages(conn)
    conn.refresh_from_db()
    assert (conn.auth_status, conn.auth_error) == ("error", "Invalid token")


def test_enable_page_can_kenh():
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook")
    with pytest.raises(PageNotEligible, match="kênh"):
        PancakePageService(MagicMock()).enable_page(page)


def test_enable_page_platform_khong_ho_tro():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="tts_1", platform="tiktok_shop", channel=channel)
    with pytest.raises(PageNotEligible, match="tiktok_shop"):
        PancakePageService(MagicMock()).enable_page(page)


def test_enable_page_sinh_token_va_dat_cursor_now():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel, token_error="cũ")
    client = MagicMock()
    client.generate_page_token.return_value = "ptok"
    before = timezone.now()

    page = PancakePageService(client).enable_page(page)

    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (True, "ptok", "")
    assert page.polled_until is not None and page.polled_until >= before
    client.generate_page_token.assert_called_once_with("1")


def test_enable_page_resume_from_giu_cursor_cu_neu_moi_hon():
    channel = Channel.objects.create(code="a", name="A")
    now = timezone.now()
    cursor_cu = now - timedelta(hours=2)
    page = PancakePage.objects.create(
        pancake_page_id="1", platform="facebook", channel=channel, token_error="HTTP 500", polled_until=cursor_cu
    )
    client = MagicMock()
    client.generate_page_token.return_value = "ptok"

    PancakePageService(client).enable_page(page, resume_from=now - timedelta(hours=24))

    page.refresh_from_db()
    assert (page.is_enabled, page.token_error, page.polled_until) == (True, "", cursor_cu)


def test_enable_page_resume_from_khong_lui_qua_cua_so():
    channel = Channel.objects.create(code="a", name="A")
    now = timezone.now()
    resume_from = now - timedelta(hours=24)
    page = PancakePage.objects.create(
        pancake_page_id="1", platform="facebook", channel=channel, polled_until=now - timedelta(days=3)
    )
    client = MagicMock()
    client.generate_page_token.return_value = "ptok"

    PancakePageService(client).enable_page(page, resume_from=resume_from)

    page.refresh_from_db()
    assert page.polled_until == resume_from


def test_enable_page_sinh_token_loi_thi_khong_bat():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel)
    client = MagicMock()
    client.generate_page_token.side_effect = PancakeTokenError("Thiếu quyền Admin")
    with pytest.raises(PancakeTokenError, match="Thiếu quyền Admin"):
        PancakePageService(client).enable_page(page)
    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (False, "", "Thiếu quyền Admin")
