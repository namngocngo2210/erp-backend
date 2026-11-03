"""Tests cho nhắc chấm công qua Lark DM (attendance_reminder_service + messenger)."""

import json
from unittest.mock import call, patch

import pytest

from apps.common.lark_open_api import LarkCredentials, LarkOpenAPIError
from apps.hr.application.services import attendance_reminder_service
from apps.hr.infrastructure import lark_messenger

MEMBERS = ["ou_alice", "ou_bob", "ou_carol"]


def _members_response(ids, has_more=False, page_token=""):
    return {
        "items": [{"member_id": i, "member_id_type": "open_id"} for i in ids],
        "has_more": has_more,
        "page_token": page_token,
    }


class TestListChatMemberIds:
    def test_single_page(self):
        with patch.object(
            lark_messenger, "lark_request", return_value=_members_response(MEMBERS)
        ) as req:
            ids = lark_messenger.list_chat_member_ids("oc_chat")
        assert ids == MEMBERS
        req.assert_called_once_with(
            "GET",
            "/im/v1/chats/oc_chat/members",
            params={"member_id_type": "open_id", "page_size": 100},
            credentials=None,
        )

    def test_paginates_until_has_more_false(self):
        pages = [
            _members_response(["ou_1", "ou_2"], has_more=True, page_token="tok2"),
            _members_response(["ou_3"], has_more=False),
        ]
        with patch.object(lark_messenger, "lark_request", side_effect=pages) as req:
            ids = lark_messenger.list_chat_member_ids("oc_chat")
        assert ids == ["ou_1", "ou_2", "ou_3"]
        assert req.call_count == 2
        assert req.call_args_list[1] == call(
            "GET",
            "/im/v1/chats/oc_chat/members",
            params={"member_id_type": "open_id", "page_size": 100, "page_token": "tok2"},
            credentials=None,
        )

    def test_skips_blank_member_ids(self):
        response = _members_response(["ou_ok"])
        response["items"].append({"member_id": "", "member_id_type": "open_id"})
        with patch.object(lark_messenger, "lark_request", return_value=response):
            assert lark_messenger.list_chat_member_ids("oc_chat") == ["ou_ok"]


class TestSendPostMessage:
    def test_builds_post_payload(self):
        lines = [[{"tag": "text", "text": "hello"}]]
        with patch.object(lark_messenger, "lark_request") as req:
            lark_messenger.send_post_message("ou_alice", title="Tiêu đề", content_lines=lines)
        req.assert_called_once()
        _, kwargs = req.call_args
        assert kwargs["params"] == {"receive_id_type": "open_id"}
        body = kwargs["json_body"]
        assert body["receive_id"] == "ou_alice"
        assert body["msg_type"] == "post"
        content = json.loads(body["content"])
        assert content["en_us"]["title"] == "Tiêu đề"
        assert content["en_us"]["content"] == lines

    def test_uses_dedicated_reminder_app_credentials_when_set(self, settings):
        settings.LARK_CHECKIN_REMINDER_APP_ID = "cli_reminder"
        settings.LARK_CHECKIN_REMINDER_APP_SECRET = "secret_reminder"
        with patch.object(lark_messenger, "lark_request") as req:
            lark_messenger.send_post_message("ou_alice", title="T", content_lines=[])
        _, kwargs = req.call_args
        assert kwargs["credentials"] == LarkCredentials("cli_reminder", "secret_reminder")

    def test_falls_back_to_default_app_when_reminder_creds_incomplete(self, settings):
        settings.LARK_CHECKIN_REMINDER_APP_ID = "cli_reminder"
        settings.LARK_CHECKIN_REMINDER_APP_SECRET = ""
        with patch.object(lark_messenger, "lark_request") as req:
            lark_messenger.send_post_message("ou_alice", title="T", content_lines=[])
        _, kwargs = req.call_args
        assert kwargs["credentials"] is None


@pytest.fixture
def reminder_settings(settings):
    settings.LARK_CHECKIN_REMINDER_CHAT_ID = "oc_chat"
    settings.LARK_CHECKIN_REMINDER_LINK = "https://example.com/checkin"
    return settings


class TestSendAttendanceReminder:
    def test_unknown_kind_raises(self, reminder_settings):
        with pytest.raises(ValueError):
            attendance_reminder_service.send_attendance_reminder("lunch")

    def test_skipped_when_chat_id_empty(self, reminder_settings):
        reminder_settings.LARK_CHECKIN_REMINDER_CHAT_ID = ""
        with patch.object(attendance_reminder_service, "lark_messenger") as messenger:
            summary = attendance_reminder_service.send_attendance_reminder("arrive")
        assert summary == {"sent": 0, "failed": 0, "skipped": True}
        messenger.list_chat_member_ids.assert_not_called()

    def test_sends_dm_to_every_member(self, reminder_settings):
        with patch.object(attendance_reminder_service, "lark_messenger") as messenger:
            messenger.list_chat_member_ids.return_value = MEMBERS
            summary = attendance_reminder_service.send_attendance_reminder("arrive")

        assert summary == {"sent": 3, "failed": 0, "skipped": False}
        messenger.list_chat_member_ids.assert_called_once_with("oc_chat")
        assert messenger.send_post_message.call_count == 3
        first_args, first_kwargs = messenger.send_post_message.call_args_list[0]
        assert first_args == ("ou_alice",)
        assert first_kwargs["title"] == "Nhắc Chấm Công Khi Đến"
        assert first_kwargs["content_lines"] == [
            [{"tag": "text", "text": "Nhắc nhở mọi người chấm công khi đến"}],
            [
                {"tag": "text", "text": "Link chấm công: "},
                {"tag": "a", "text": "Link", "href": "https://example.com/checkin"},
            ],
        ]

    def test_leave_kind_uses_leave_copy(self, reminder_settings):
        with patch.object(attendance_reminder_service, "lark_messenger") as messenger:
            messenger.list_chat_member_ids.return_value = ["ou_alice"]
            attendance_reminder_service.send_attendance_reminder("leave")
        _, kwargs = messenger.send_post_message.call_args
        assert kwargs["title"] == "Nhắc Chấm Công Khi Về"
        assert kwargs["content_lines"][0] == [
            {"tag": "text", "text": "Nhắc nhở mọi người chấm công khi về"}
        ]

    def test_link_line_omitted_when_link_empty(self, reminder_settings):
        reminder_settings.LARK_CHECKIN_REMINDER_LINK = ""
        with patch.object(attendance_reminder_service, "lark_messenger") as messenger:
            messenger.list_chat_member_ids.return_value = ["ou_alice"]
            attendance_reminder_service.send_attendance_reminder("arrive")
        _, kwargs = messenger.send_post_message.call_args
        assert len(kwargs["content_lines"]) == 1

    def test_one_failure_does_not_block_the_rest(self, reminder_settings):
        with patch.object(attendance_reminder_service, "lark_messenger") as messenger:
            messenger.list_chat_member_ids.return_value = MEMBERS
            messenger.send_post_message.side_effect = [
                None,
                LarkOpenAPIError("boom"),
                None,
            ]
            summary = attendance_reminder_service.send_attendance_reminder("arrive")
        assert summary == {"sent": 2, "failed": 1, "skipped": False}
        assert messenger.send_post_message.call_count == 3


class TestCeleryWiring:
    def test_task_registered_and_routed(self, settings):
        from apps.hr.tasks import send_attendance_reminder as task

        assert task.name == "apps.hr.tasks.send_attendance_reminder"
        assert task.name in settings.CELERY_TASK_ROUTES

    def test_beat_entries(self, settings):
        arrive = settings.CELERY_BEAT_SCHEDULE["hr-attendance-reminder-arrive"]
        leave = settings.CELERY_BEAT_SCHEDULE["hr-attendance-reminder-leave"]
        assert arrive["task"] == "apps.hr.tasks.send_attendance_reminder"
        assert leave["task"] == "apps.hr.tasks.send_attendance_reminder"
        assert arrive["args"] == ["arrive"] and leave["args"] == ["leave"]
        assert arrive["schedule"].hour == {7} and arrive["schedule"].minute == {50}
        assert leave["schedule"].hour == {17} and leave["schedule"].minute == {30}
