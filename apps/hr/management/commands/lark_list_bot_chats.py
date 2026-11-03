"""Liệt kê các nhóm Lark mà app bot đang tham gia (tra chat_id khi cấu hình).

Chạy sau khi mời bot vào nhóm:

    python manage.py lark_list_bot_chats

Lấy chat_id của nhóm nhắc chấm công → điền vào env LARK_CHECKIN_REMINDER_CHAT_ID.
"""

from django.core.management.base import BaseCommand

from apps.hr.infrastructure.lark_messenger import list_bot_chats


class Command(BaseCommand):
    help = "Liệt kê nhóm Lark bot đang tham gia (chat_id + tên nhóm)."

    def handle(self, *args, **options):
        chats = list_bot_chats()
        if not chats:
            self.stdout.write("Bot chưa tham gia nhóm nào (hoặc thiếu scope im:chat:readonly).")
            return
        for chat in chats:
            self.stdout.write(f"{chat['chat_id']}\t{chat['name'] or '(không tên)'}")
        self.stdout.write(self.style.SUCCESS(f"Tổng: {len(chats)} nhóm."))
