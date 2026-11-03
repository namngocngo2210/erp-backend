"""Gán nhãn hội thoại chat bằng Gemini. Dùng để backfill theo lô:

  python manage.py ai_mart_chat_extract --limit 200
"""

from django.core.management.base import BaseCommand

from apps.ai_mart.infrastructure import chat_extract_runner


class Command(BaseCommand):
    help = "Xếp hàng và gán nhãn hội thoại chat"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--enqueue-only", action="store_true")

    def handle(self, *args, **options):
        queued = chat_extract_runner.enqueue_pending()
        self.stdout.write(f"xếp hàng: {queued}")
        if options["enqueue_only"]:
            return
        summary = chat_extract_runner.run_batch(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(str(summary)))
