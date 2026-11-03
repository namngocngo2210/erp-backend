"""Nắn `warehouse_received_at` cho hành trình bị cron đóng dấu ngày sai.

TẠI SAO LÀ LỆNH TAY CHỨ KHÔNG PHẢI MIGRATION: `warehouse_received_at` là trường
NGƯỜI DÙNG SỬA ĐƯỢC, và không có cột nào trong DB phân biệt được "cron ghi sai
ngày" với "nhân viên gõ tay trùng đúng ngày cron khớp" (xem docstring
`apps.warranty.received_at_backfill`). Suy đoán sai ⇒ đè mất dữ liệu người dùng,
mà chiều ngược lại không khôi phục được. Nên quyết định thuộc về người vận hành:
migration 0016 chỉ liệt kê ứng viên vào log, còn việc ghi nằm sau `--apply`.

Rà trước, duyệt danh sách, rồi mới nắn:

    python manage.py backfill_warranty_received_at
    python manage.py backfill_warranty_received_at --apply

Bản in `(journey_id, ngày_cũ, ngày_mới)` của lần `--apply` là dấu vết DUY NHẤT
để khôi phục một dòng bị nắn oan — giữ lại log.
"""
from django.core.management.base import BaseCommand
from django.db import connection

from apps.warranty.received_at_backfill import backfill_warehouse_received_at


class Command(BaseCommand):
    help = (
        "Rà (mặc định) hoặc nắn `warehouse_received_at` về đúng NGÀY QUÉT "
        "cho các hành trình bảo hành bị cron đóng dấu ngày khớp."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Thực sự ghi đè (mặc định chỉ liệt kê, không đụng dữ liệu).",
        )

    def handle(self, *args, **options):
        do_apply = bool(options.get("apply"))
        rows = backfill_warehouse_received_at(connection, apply=do_apply)

        if rows:
            self.stdout.write(
                f"{'journey_id':>12}  {'ngày hiện tại':<14}  {'ngày đề xuất':<14}"
                if not do_apply
                else f"{'journey_id':>12}  {'ngày cũ':<14}  {'ngày mới':<14}"
            )
            self.stdout.write("-" * 46)
            for journey_id, old_value, new_value in rows:
                self.stdout.write(
                    f"{journey_id:>12}  {str(old_value):<14}  {str(new_value):<14}"
                )

        style = self.style.SUCCESS if do_apply else self.style.WARNING
        if do_apply:
            message = f"APPLY: đã nắn {len(rows)} hành trình."
        else:
            message = (
                f"DRY-RUN: {len(rows)} hành trình là ỨNG VIÊN, KHÔNG có dữ liệu "
                "nào bị thay đổi. Duyệt từng dòng (giá trị hiện tại có thể do "
                "nhân viên gõ tay) rồi chạy lại với --apply."
            )
        self.stdout.write(style(message))
