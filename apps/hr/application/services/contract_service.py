from __future__ import annotations

from datetime import date

from django.db import transaction

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import ContractSigned
from apps.hr.models import EmploymentContract


class ContractService:
    @staticmethod
    @transaction.atomic
    def sign(employee_id: int, *, contract_type: str, start_date: date,
             end_date: date | None = None, base_salary: int = 0,
             contract_no: str = "", file_url: str = "", note: str = "") -> EmploymentContract:
        contract = EmploymentContract.objects.create(
            employee_id=employee_id, contract_type=contract_type, start_date=start_date,
            end_date=end_date, base_salary=base_salary, contract_no=contract_no,
            file_url=file_url, note=note,
        )
        event_bus.publish_on_commit(ContractSigned(
            employee_id=employee_id, contract_id=contract.id))
        return contract
