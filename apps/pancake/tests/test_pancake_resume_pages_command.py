"""Lệnh `pancake_resume_pages`: chỉ đụng page bị TỰ tắt (token_error khác rỗng),
dry-run mặc định, --apply bật lại với cursor bù 24h rồi poll ngay."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.management import call_command

from apps.channels_core.models import Channel
from apps.pancake.application.services.poll_service import PollResult
from apps.pancake.models import PancakePage
from apps.pancake.tasks import POLL_LOCK_KEY

pytestmark = pytest.mark.django_db

CMD = "apps.pancake.management.commands.pancake_resume_pages"


@pytest.fixture
def pages():
    channel = Channel.objects.create(code="a", name="A")
    tu_tat = PancakePage.objects.create(
        pancake_page_id="1", name="Tự tắt", platform="facebook", channel=channel, token_error="HTTP 500"
    )
    tat_tay = PancakePage.objects.create(pancake_page_id="2", name="Tắt tay", platform="facebook", channel=channel)
    return tu_tat, tat_tay


def test_dry_run_khong_goi_pancake(pages):
    out = StringIO()
    with patch(f"{CMD}.get_client") as get_client:
        call_command("pancake_resume_pages", stdout=out)
    get_client.assert_not_called()
    assert "Tự tắt" in out.getvalue() and "Tắt tay" not in out.getvalue()
    pages[0].refresh_from_db()
    assert pages[0].is_enabled is False


def test_apply_bat_lai_va_poll_ngay(pages):
    client = MagicMock()
    client.generate_page_token.return_value = "ptok"
    polled = []

    def fake_poll(self, page):
        polled.append(page.pancake_page_id)
        return PollResult(page_id=page.id, leads_created=1)

    with patch(f"{CMD}.get_client", return_value=client), patch(f"{CMD}.PancakePollService.poll_page", fake_poll):
        call_command("pancake_resume_pages", "--apply", stdout=StringIO())

    assert polled == ["1"]
    tu_tat, tat_tay = pages
    tu_tat.refresh_from_db()
    tat_tay.refresh_from_db()
    assert (tu_tat.is_enabled, tu_tat.page_access_token, tu_tat.token_error) == (True, "ptok", "")
    assert tat_tay.is_enabled is False


def test_apply_khi_task_poll_dang_chay_thi_dung(pages):
    cache.add(POLL_LOCK_KEY, "khac", 60)
    try:
        with patch(f"{CMD}.get_client", return_value=MagicMock()), pytest.raises(Exception, match="đang chạy"):
            call_command("pancake_resume_pages", "--apply", stdout=StringIO())
    finally:
        cache.delete(POLL_LOCK_KEY)
