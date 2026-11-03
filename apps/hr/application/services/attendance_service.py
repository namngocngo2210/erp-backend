from __future__ import annotations
from datetime import date, time
from django.db import IntegrityError, transaction
from apps.hr.models import AttendanceRecord


class AttendanceService:
    @staticmethod
    @transaction.atomic
    def record(employee_id: int, *, work_date: date, check_in: time | None = None,
               check_out: time | None = None, note: str = "",
               source: str = "manual") -> AttendanceRecord:
        # Fast path: tạo mới trong savepoint. Nếu trùng (employee, work_date)
        # do race concurrent -> IntegrityError, fallback sang update bản ghi sẵn có.
        try:
            with transaction.atomic():
                return AttendanceRecord.objects.create(
                    employee_id=employee_id, work_date=work_date,
                    check_in=check_in, check_out=check_out,
                    note=note, source=source,
                )
        except IntegrityError:
            rec = AttendanceRecord.objects.select_for_update().get(
                employee_id=employee_id, work_date=work_date,
            )
            # upsert: cập nhật field nào được truyền (không ghi đè bằng None)
            fields = []
            if check_in is not None:
                rec.check_in = check_in
                fields.append("check_in")
            if check_out is not None:
                rec.check_out = check_out
                fields.append("check_out")
            if note:
                rec.note = note
                fields.append("note")
            if fields:
                rec.save(update_fields=fields)
            return rec
