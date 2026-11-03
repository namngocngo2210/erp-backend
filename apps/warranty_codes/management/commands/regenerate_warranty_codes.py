"""Sinh lại mã dưới lớp cào cho TOÀN BỘ mã bảo hành đã có.

Bối cảnh 18/09/2026: chủ quyết định rút mã từ 10 xuống 8 ký tự và muốn đổi
cả 101k mã đã sinh (32 lô đã xuất Excel nhưng CHƯA in tem, chưa có mã nào
được quét/kích hoạt). Chỉ đổi cột `code`; seq/serial/lô/sản phẩm giữ nguyên.

    python manage.py regenerate_warranty_codes           # dry-run: chỉ đếm
    python manage.py regenerate_warranty_codes --apply   # ghi thật

Chốt an toàn: từ chối chạy nếu đã có mã bị quét hoặc kích hoạt — tem đã tới
tay khách thì đổi mã là phá bảo hành của họ. Sau khi ghi, reset `exported_at`
của mọi lô để bảng lô hiện "chưa xuất" — file Excel cũ vô giá trị, phải xuất
lại rồi gửi xưởng in.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from apps.warranty_codes.domain.serial import CODE_LENGTH, generate_code
from apps.warranty_codes.models import WarrantyActivation, WarrantyCode, WarrantyCodeBatch

_CHUNK_SIZE = 5000
_MAX_RETRY = 5


class Command(BaseCommand):
    help = f"Sinh lại mã dưới lớp cào ({CODE_LENGTH} ký tự) cho toàn bộ mã bảo hành đã có"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật (mặc định chỉ đếm, không ghi)")

    def handle(self, *args, **options):
        activated = WarrantyActivation.objects.count()
        scanned = WarrantyCode.objects.filter(first_scanned_at__isnull=False).count()
        if activated or scanned:
            raise CommandError(
                f"Từ chối: đã có {activated} mã đã kích hoạt và {scanned} mã đã quét — "
                "tem đã tới tay khách, không được đổi mã."
            )

        total = WarrantyCode.objects.count()
        if not options.get("apply"):
            self.stdout.write(f"DRY-RUN: sẽ sinh lại {total} mã ({CODE_LENGTH} ký tự). Thêm --apply để ghi.")
            return

        done = 0
        ids = list(WarrantyCode.objects.order_by("id").values_list("id", flat=True))
        for start in range(0, len(ids), _CHUNK_SIZE):
            chunk_ids = ids[start : start + _CHUNK_SIZE]
            self._rewrite_chunk(chunk_ids)
            done += len(chunk_ids)
            self.stdout.write(f"  {done}/{total}")

        WarrantyCodeBatch.objects.update(exported_at=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Đã sinh lại {done} mã ({CODE_LENGTH} ký tự); đã reset exported_at của mọi lô — xuất lại Excel rồi gửi in."
            )
        )

    @staticmethod
    def _rewrite_chunk(chunk_ids: list[int]) -> None:
        # Mirror batch_service._insert_chunk: trùng mã (unique index) thì sinh lại cả chunk.
        for attempt in range(_MAX_RETRY):
            rows = [WarrantyCode(id=cid, code=generate_code()) for cid in chunk_ids]
            try:
                with transaction.atomic():
                    WarrantyCode.objects.bulk_update(rows, ["code"], batch_size=_CHUNK_SIZE)
                return
            except IntegrityError:
                if attempt == _MAX_RETRY - 1:
                    raise
