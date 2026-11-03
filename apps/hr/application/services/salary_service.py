from __future__ import annotations

from datetime import date

from django.db import transaction

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import SalaryChanged
from apps.hr.domain.salary import close_previous
from apps.hr.models import SalaryRecord
from apps.hr.repositories import SalaryRepository


class SalaryService:
    @staticmethod
    @transaction.atomic
    def set_salary(employee_id: int, *, amount: int, effective_from: date,
                   reason: str = "", created_by_id: int | None = None) -> SalaryRecord:
        current = (
            SalaryRepository.active()
            .select_for_update()
            .filter(employee_id=employee_id, is_current=True)
            .first()
        )
        # Cùng mốc hiệu lực = sửa số bản hiện tại (nhập nhầm phải chỉnh được),
        # không sinh bản ghi trùng mốc; mốc mới hơn mới append giữ lịch sử.
        if current is not None and effective_from == current.effective_from:
            current.amount = amount
            if reason:
                current.reason = reason
            current.save(update_fields=["amount", "reason"])
            event_bus.publish_on_commit(SalaryChanged(
                employee_id=employee_id, amount=int(amount),
                effective_from=effective_from.isoformat()))
            return current
        if current is not None:
            current.effective_to = close_previous(
                current_from=current.effective_from, new_from=effective_from)
            current.is_current = False
            current.save(update_fields=["effective_to", "is_current"])
        record = SalaryRecord.objects.create(
            employee_id=employee_id, amount=amount, effective_from=effective_from,
            is_current=True, reason=reason, created_by_id=created_by_id,
        )
        event_bus.publish_on_commit(SalaryChanged(
            employee_id=employee_id, amount=int(amount),
            effective_from=effective_from.isoformat()))
        return record
