"""OperationEngine — động cơ máy trạng thái tác nghiệp (spec §4).

Đồng bộ trong request; select_for_update dòng state; mọi nhánh đều ghi 1
ContactActivity kind=operation (meta để trong call_meta — cột JSON sẵn có).
"""
from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.contact_flow.models import (
    CustomerOperationState, OperationResult, OperationStage, OperationTransition,
)
from apps.customers.models import ContactActivity, ContactActivityKind


class OperationFlowError(Exception):
    """Lỗi nghiệp vụ engine — view map 400, message tiếng Việt."""


def _log(customer, actor, meta: dict) -> None:
    ContactActivity.objects.create(
        customer=customer, staff=actor,
        kind=ContactActivityKind.OPERATION, note="", call_meta=meta,
    )


class OperationEngine:
    @staticmethod
    def enter_start(customer, now=None, start_stage=None):
        """``start_stage``: caller lặp theo lô (sweep) truyền stage đã nạp MỘT lần —
        đây là hằng số hệ thống, tra lại mỗi khách là 1 query thừa/vòng
        (audit 2026-08-19 P1.6). Bỏ trống (đường at-ingest 1 khách) thì tự tra."""
        now = now or timezone.now()
        with transaction.atomic():
            existing = (
                CustomerOperationState.objects.select_for_update()
                .filter(customer=customer).first()
            )
            if existing is not None:
                return existing
            start = start_stage or OperationStage.objects.filter(is_start=True, is_active=True).first()
            if start is None:
                return None
            try:
                # Savepoint riêng: nếu luồng khác vừa tạo state cho customer này giữa lúc
                # SELECT (existing=None) và INSERT ở đây, IntegrityError (unique customer)
                # chỉ rollback savepoint, không phá transaction ngoài (final review I1).
                with transaction.atomic():
                    state = CustomerOperationState.objects.create(
                        customer=customer, current_stage=start, due_at=now, entered_at=now,
                    )
            except IntegrityError:
                # Re-fetch state mà luồng khác vừa tạo — không log trùng enter_start.
                return (
                    CustomerOperationState.objects.select_for_update()
                    .get(customer=customer)
                )
            _log(customer, None, {"event": "enter_start", "to_stage": start.name})
            return state

    @staticmethod
    def enter_start_bulk(customer_ids, *, start_stage, now=None) -> int:
        """Đưa NHIỀU khách vào bước khởi đầu trong MỘT transaction — đường sweep.

        Khác ``enter_start`` (1 khách, lock dòng, dành cho đường request): ở đây
        4 query cho cả lô, không phụ thuộc số khách (audit 2026-09-23 T1 §2.3 —
        trước: 3 query + 1 transaction/khách, trần 2000/tick):
        1. state đã có (``customer_id__in``) → loại;
        2. ``bulk_create(ignore_conflicts=True)`` state cho phần thiếu — khách vừa
           được luồng request tạo state giữa bước 1 và 2 chỉ bị bỏ qua, không
           IntegrityError (unique customer);
        3. đọc lại đúng các state VỪA tạo (``entered_at=now`` của lô này) — cần vì
           ``ignore_conflicts`` không trả pk và không cho biết dòng nào thật sự
           chèn;
        4. ``bulk_create`` ContactActivity ``enter_start`` cho đúng các khách đó.
        Trả về số state đã tạo.
        """
        ids = [cid for cid in dict.fromkeys(customer_ids) if cid is not None]
        if not ids or start_stage is None:
            return 0
        now = now or timezone.now()
        with transaction.atomic():
            existing = set(
                CustomerOperationState.objects.filter(customer_id__in=ids).values_list(
                    "customer_id", flat=True
                )
            )
            missing = [cid for cid in ids if cid not in existing]
            if not missing:
                return 0
            CustomerOperationState.objects.bulk_create(
                [
                    CustomerOperationState(
                        customer_id=cid, current_stage=start_stage, due_at=now, entered_at=now
                    )
                    for cid in missing
                ],
                ignore_conflicts=True,
            )
            created_ids = list(
                CustomerOperationState.objects.filter(
                    customer_id__in=missing, current_stage=start_stage, entered_at=now
                ).values_list("customer_id", flat=True)
            )
            if created_ids:
                ContactActivity.objects.bulk_create(
                    [
                        ContactActivity(
                            customer_id=cid, staff=None,
                            kind=ContactActivityKind.OPERATION, note="",
                            call_meta={"event": "enter_start", "to_stage": start_stage.name},
                        )
                        for cid in created_ids
                    ]
                )
        return len(created_ids)

    @staticmethod
    def apply_result(customer, result_id, *, actor, scheduled_at=None, now=None):
        now = now or timezone.now()
        result = OperationResult.objects.filter(pk=result_id, is_active=True).first()
        if result is None:
            raise OperationFlowError("Kết quả tác nghiệp không tồn tại hoặc đã tắt.")
        if result.requires_schedule:
            if scheduled_at is None:
                raise OperationFlowError("Kết quả này bắt buộc chọn thời gian hẹn.")
            if scheduled_at <= now:
                raise OperationFlowError("Thời gian hẹn phải ở tương lai.")
        with transaction.atomic():
            state = (
                CustomerOperationState.objects.select_for_update()
                .select_related("current_stage").filter(customer=customer).first()
            )
            if state is None:
                # Lazy entry cho khách cũ (spec §4.2) — không backfill hàng loạt.
                start = OperationStage.objects.filter(is_start=True, is_active=True).first()
                if start is None:
                    raise OperationFlowError("Chưa cấu hình bước khởi đầu.")
                try:
                    # Savepoint riêng: race tương tự enter_start — nếu luồng khác vừa tạo
                    # state, re-fetch và tiếp tục transition bình thường (final review I1).
                    with transaction.atomic():
                        state = CustomerOperationState.objects.create(
                            customer=customer, current_stage=start, due_at=now, entered_at=now,
                        )
                except IntegrityError:
                    state = (
                        CustomerOperationState.objects.select_for_update()
                        .select_related("current_stage").get(customer=customer)
                    )
            from_stage = state.current_stage
            transition = (
                OperationTransition.objects.select_related("to_stage")
                .filter(from_stage=from_stage, result=result).first()
            )
            if transition is not None and transition.to_stage is not None:
                state.current_stage = transition.to_stage
                state.entered_at = now
                state.due_at = now + timedelta(minutes=transition.delay_minutes or 0)
            else:
                # Rule kết thúc nhánh HOẶC không có rule: giữ bước, ngừng hẹn giờ.
                state.due_at = None
            if result.requires_schedule:
                state.due_at = scheduled_at
            state.last_result = result
            state.save()
            _log(customer, actor, {
                "event": "apply_result",
                "from_stage": from_stage.name,
                "result": result.name,
                "to_stage": state.current_stage.name,
                "due_at": state.due_at.isoformat() if state.due_at else None,
            })
            return state

    @staticmethod
    def set_stage(customer, stage_id, new_due_at, *, actor, now=None):
        """Đặt bước tác nghiệp TRỰC TIẾP (dropdown cột Tác nghiệp), không qua transition.

        Đây là đường đi TẮT có chủ đích quanh máy trạng thái: sale chọn bước nào thì
        khách sang đúng bước đó, không cần tồn tại rule (from_stage, result) → to_stage.
        `last_result` giữ nguyên — kết quả tác nghiệp gần nhất vẫn là kết quả THẬT đã
        ghi, đổi bước tay không phải một "kết quả".

        Khách chưa vào flow thì tạo state luôn (lazy entry, cùng khuôn `apply_result`):
        nếu không, dòng đang hiện "—" ở cột Tác nghiệp sẽ không bao giờ chọn được.
        """
        now = now or timezone.now()
        if new_due_at is None or new_due_at <= now:
            raise OperationFlowError("Giờ hẹn phải ở tương lai.")
        stage = OperationStage.objects.filter(pk=stage_id, is_active=True).first()
        if stage is None:
            raise OperationFlowError("Bước tác nghiệp không tồn tại hoặc đã tắt.")
        with transaction.atomic():
            state = (
                CustomerOperationState.objects.select_for_update()
                .select_related("current_stage").filter(customer=customer).first()
            )
            if state is None:
                try:
                    # Savepoint riêng: race tương tự enter_start/apply_result — luồng khác
                    # vừa tạo state thì chỉ rollback savepoint rồi đi tiếp nhánh cập nhật.
                    with transaction.atomic():
                        state = CustomerOperationState.objects.create(
                            customer=customer, current_stage=stage,
                            due_at=new_due_at, entered_at=now,
                        )
                except IntegrityError:
                    state = (
                        CustomerOperationState.objects.select_for_update()
                        .select_related("current_stage").get(customer=customer)
                    )
                else:
                    _log(customer, actor, {
                        "event": "set_stage",
                        "from_stage": None,
                        "to_stage": stage.name,
                        "due_at": new_due_at.isoformat(),
                    })
                    return state
            from_stage = state.current_stage
            state.current_stage = stage
            state.entered_at = now
            state.due_at = new_due_at
            state.save(update_fields=["current_stage", "entered_at", "due_at", "updated_at"])
            _log(customer, actor, {
                "event": "set_stage",
                "from_stage": from_stage.name,
                "to_stage": stage.name,
                "due_at": new_due_at.isoformat(),
            })
            return state

    @staticmethod
    def edit_due(customer, new_due_at, *, actor, now=None):
        now = now or timezone.now()
        if new_due_at is None or new_due_at <= now:
            raise OperationFlowError("Giờ hẹn mới phải ở tương lai.")
        with transaction.atomic():
            state = (
                CustomerOperationState.objects.select_for_update()
                .select_related("current_stage").filter(customer=customer).first()
            )
            if state is None:
                raise OperationFlowError("Khách chưa vào flow tác nghiệp.")
            if not state.current_stage.allow_edit_due:
                raise OperationFlowError("Bước hiện tại không cho phép sửa giờ.")
            state.due_at = new_due_at
            state.save(update_fields=["due_at", "updated_at"])
            _log(customer, actor, {
                "event": "edit_due", "stage": state.current_stage.name,
                "due_at": new_due_at.isoformat(),
            })
            return state
