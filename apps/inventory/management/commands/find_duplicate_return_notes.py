"""Rà + dọn NHÓM ĐƠN HOÀN trùng mã vận đơn (1 đơn bị khớp hoàn 2 lần → cộng tồn
gấp đôi).

Nhóm các nhóm đơn CHƯA HUỶ theo `shipping_code` (khác rỗng) có > 1 bản ghi. Giữ
nhóm sớm nhất (id nhỏ nhất); các nhóm dư:
  - POSTED  → huỷ qua ReturnEntryService.cancel_entry (đảo movement, tồn về đúng)
  - DRAFT   → chỉ báo, không tự đụng (không có ảnh hưởng sổ sách; xử lý tay)

Nhóm đơn không có mã riêng — log in ra `entry.id` kèm mã PHIẾU NGÀY chứa nó.

Mặc định DRY-RUN (chỉ liệt kê). Thêm --fix để thực sự huỷ nhóm POSTED dư.
"""
import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import ReturnEntry, ReturnStatus


class Command(BaseCommand):
    help = "Rà + dọn nhóm đơn hoàn trùng mã vận đơn (giữ nhóm sớm nhất, huỷ POSTED dư)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Thực sự huỷ các nhóm đơn POSTED dư (mặc định chỉ liệt kê).",
        )

    def handle(self, *args, **options):
        do_fix = bool(options.get("fix"))

        dup_codes = list(
            ReturnEntry.objects.filter(shipping_code__gt="")
            .exclude(status=ReturnStatus.CANCELLED)
            .values("shipping_code")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
            .values_list("shipping_code", flat=True)
        )

        summary = {
            "fix": do_fix,
            "duplicate_groups": len(dup_codes),
            "cancelled": 0,
            "draft_extras": 0,
            "errors": [],
            "groups": [],
        }

        for code in dup_codes:
            entries = list(
                ReturnEntry.objects.filter(shipping_code=code)
                .exclude(status=ReturnStatus.CANCELLED)
                .select_related("return_note")
                .order_by("id")
            )
            keep, extras = entries[0], entries[1:]
            group = {
                "shipping_code": code,
                "keep_id": keep.id,
                "keep_note": keep.return_note.code,
                "extras": [],
            }
            for entry in extras:
                note_code = entry.return_note.code
                if entry.status == ReturnStatus.POSTED:
                    if do_fix:
                        try:
                            ReturnEntryService.cancel_entry(entry_id=entry.id)
                            summary["cancelled"] += 1
                            action = "cancelled"
                        except (ReturnEntry.DoesNotExist, ValueError) as exc:
                            summary["errors"].append({"id": entry.id, "error": str(exc)})
                            action = f"error: {exc}"
                    else:
                        summary["cancelled"] += 1  # sẽ-huỷ (dry-run đếm dự kiến)
                        action = "would-cancel"
                else:  # DRAFT
                    summary["draft_extras"] += 1
                    action = "draft-extra-report-only"
                group["extras"].append(
                    {
                        "id": entry.id,
                        "note": note_code,
                        "status": entry.status,
                        "action": action,
                    }
                )
                self.stdout.write(
                    f"[{code}] giữ nhóm #{keep.id} ({keep.return_note.code}) · "
                    f"nhóm dư #{entry.id} ({note_code}, {entry.status}) → {action}"
                )
            summary["groups"].append(group)

        style = self.style.SUCCESS if do_fix else self.style.WARNING
        self.stdout.write(
            style(
                f"{'FIX' if do_fix else 'DRY-RUN'}: {len(dup_codes)} nhóm trùng, "
                f"{summary['cancelled']} nhóm POSTED "
                f"{'đã huỷ' if do_fix else 'sẽ huỷ'}, "
                f"{summary['draft_extras']} nhóm nháp dư (xử lý tay)."
            )
        )
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
