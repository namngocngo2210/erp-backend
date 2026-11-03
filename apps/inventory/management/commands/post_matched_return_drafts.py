"""Backfill 1 lần: ghi sổ các NHÓM ĐƠN HOÀN nháp đã khớp đơn (có dòng hàng hoàn)
— dọn tồn nháp cũ sau khi bật auto-post ở luồng quét.

Guard double-credit / đơn đã hoàn kho → skip + log, không chặn nhóm khác.
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import ReturnEntry, ReturnStatus, ReturnType


class Command(BaseCommand):
    help = "Ghi sổ hàng loạt nhóm đơn hoàn nháp đã khớp đơn (delivery_returned, có dòng)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không ghi sổ")
        parser.add_argument(
            "--limit", type=int, default=0, help="Giới hạn số nhóm xử lý (0 = không giới hạn)"
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        limit = int(options.get("limit") or 0)

        # Loại hoàn nằm ở DÒNG (phiếu ngày không còn return_type): lấy nhóm nháp
        # có ít nhất 1 dòng hàng hoàn — điều kiện "có dòng" nằm sẵn trong join.
        qs = (
            ReturnEntry.objects.filter(
                status=ReturnStatus.DRAFT,
                source_order__isnull=False,
                lines__return_type=ReturnType.DELIVERY_RETURNED,
            )
            .distinct()
            .order_by("id")
        )
        if limit > 0:
            qs = qs[:limit]

        candidate_ids = list(qs.values_list("id", flat=True))
        summary = {
            "dry_run": dry_run,
            "candidates": len(candidate_ids),
            "posted": 0,
            "skipped": 0,
            "skips": [],
        }

        for entry_id in candidate_ids:
            if dry_run:
                note_code = ReturnEntry.objects.values_list(
                    "return_note__code", flat=True
                ).get(id=entry_id)
                self.stdout.write(f"DRY-RUN sẽ ghi sổ: nhóm #{entry_id} (phiếu {note_code})")
                continue
            try:
                entry = ReturnEntryService.post_entry(entry_id=entry_id)
                summary["posted"] += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Đã ghi sổ nhóm #{entry_id} (phiếu {entry.return_note.code})"
                    )
                )
            except (ReturnEntry.DoesNotExist, ValueError) as exc:
                summary["skipped"] += 1
                summary["skips"].append({"id": entry_id, "error": str(exc)})
                self.stdout.write(self.style.WARNING(f"Bỏ qua nhóm #{entry_id}: {exc}"))

        self.stdout.write(self.style.SUCCESS("post_matched_return_drafts done."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
