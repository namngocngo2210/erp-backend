import pytest
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.contact_flow.application.services.operation_engine import (
    OperationEngine, OperationFlowError,
)
from apps.contact_flow.models import (
    CustomerOperationState, OperationResult, OperationStage, OperationTransition,
)
from apps.customers.models import ContactActivity, Customer

pytestmark = pytest.mark.django_db


@pytest.fixture
def flow():
    start = OperationStage.objects.create(name="Gọi lần đầu", is_start=True)
    knm1 = OperationStage.objects.create(name="KNM 1")
    r_knm = OperationResult.objects.create(name="KNM / TB")
    r_sched = OperationResult.objects.create(name="Hẹn gọi lại", requires_schedule=True)
    r_end = OperationResult.objects.create(name="Số rác")
    OperationTransition.objects.create(from_stage=start, result=r_knm, to_stage=knm1, delay_minutes=30)
    OperationTransition.objects.create(from_stage=start, result=r_end, to_stage=None)
    return start, knm1, r_knm, r_sched, r_end


@pytest.fixture
def customer():
    return Customer.objects.create(full_name="KH test", phone_raw="0900000001")


def test_enter_start_sets_due_now(flow, customer):
    state = OperationEngine.enter_start(customer)
    assert state.current_stage.name == "Gọi lần đầu"
    assert state.due_at is not None


def test_enter_start_noop_without_start_stage(customer):
    assert OperationEngine.enter_start(customer) is None


def test_apply_result_moves_and_schedules(flow, customer):
    start, knm1, r_knm, *_ = flow
    now = timezone.now()
    state = OperationEngine.apply_result(customer, r_knm.id, actor=None, now=now)
    assert state.current_stage_id == knm1.id
    assert state.due_at == now + timedelta(minutes=30)
    act = ContactActivity.objects.get(customer=customer, kind="operation")
    assert act.call_meta["to_stage"] == "KNM 1"


def test_apply_result_end_branch_nulls_due(flow, customer):
    *_, r_end = flow
    state = OperationEngine.apply_result(customer, r_end.id, actor=None)
    assert state.due_at is None
    assert state.current_stage.name == "Gọi lần đầu"  # giữ bước


def test_apply_result_no_rule_keeps_stage_null_due(flow, customer):
    orphan = OperationResult.objects.create(name="Không mua")
    state = OperationEngine.apply_result(customer, orphan.id, actor=None)
    assert state.due_at is None
    assert ContactActivity.objects.filter(customer=customer, kind="operation").count() == 1


def test_requires_schedule_missing_raises(flow, customer):
    r_sched = flow[3]
    with pytest.raises(OperationFlowError):
        OperationEngine.apply_result(customer, r_sched.id, actor=None)


def test_requires_schedule_past_raises(flow, customer):
    with pytest.raises(OperationFlowError):
        OperationEngine.apply_result(
            customer, flow[3].id, actor=None,
            scheduled_at=timezone.now() - timedelta(hours=1),
        )


def test_schedule_overrides_delay(flow, customer):
    start, knm1, r_knm, r_sched, _ = flow
    OperationTransition.objects.create(from_stage=start, result=r_sched, to_stage=knm1, delay_minutes=60)
    at = timezone.now() + timedelta(hours=5)
    state = OperationEngine.apply_result(customer, r_sched.id, actor=None, scheduled_at=at)
    assert state.current_stage_id == knm1.id and state.due_at == at


def test_lazy_entry_on_apply(flow, customer):
    # Không gọi enter_start trước — apply_result tự tạo state từ bước khởi đầu.
    start, knm1, r_knm, *_ = flow
    assert not CustomerOperationState.objects.filter(customer=customer).exists()
    state = OperationEngine.apply_result(customer, r_knm.id, actor=None)
    assert state.current_stage_id == knm1.id


def _fake_select_for_update_missing_once(real_select_for_update):
    """Mô phỏng stale read: lần gọi select_for_update() ĐẦU TIÊN (bước kiểm tra
    "existing"/"state" trước khi tạo) trả về queryset rỗng dù DB đã có record thật
    (do "luồng khác" vừa tạo) — buộc engine đi vào nhánh create() và ăn IntegrityError
    thật từ Postgres (unique customer). Các lần gọi sau (re-fetch trong except) dùng
    lại hành vi select_for_update thật để thấy record.
    """
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return CustomerOperationState.objects.none()
        return real_select_for_update(*args, **kwargs)

    return fake


def test_enter_start_recovers_from_create_race(flow, customer):
    start = flow[0]
    now = timezone.now()
    # "Luồng khác" đã tạo state thật cho customer này ngay trước khi ta SELECT.
    CustomerOperationState.objects.create(
        customer=customer, current_stage=start, due_at=now, entered_at=now,
    )
    real_select_for_update = CustomerOperationState.objects.select_for_update
    with mock.patch.object(
        CustomerOperationState.objects, "select_for_update",
        side_effect=_fake_select_for_update_missing_once(real_select_for_update),
    ):
        state = OperationEngine.enter_start(customer)

    assert state is not None
    assert state.current_stage_id == start.id
    assert CustomerOperationState.objects.filter(customer=customer).count() == 1
    # Nhánh race không log trùng enter_start.
    assert ContactActivity.objects.filter(customer=customer, kind="operation").count() == 0


def test_apply_result_lazy_entry_recovers_from_create_race(flow, customer):
    start, knm1, r_knm, *_ = flow
    now = timezone.now()
    # "Luồng khác" đã lazy-entry tạo state cho customer này ngay trước khi ta SELECT.
    CustomerOperationState.objects.create(
        customer=customer, current_stage=start, due_at=now, entered_at=now,
    )
    real_select_for_update = CustomerOperationState.objects.select_for_update
    with mock.patch.object(
        CustomerOperationState.objects, "select_for_update",
        side_effect=_fake_select_for_update_missing_once(real_select_for_update),
    ):
        state = OperationEngine.apply_result(customer, r_knm.id, actor=None)

    assert state.current_stage_id == knm1.id
    assert CustomerOperationState.objects.filter(customer=customer).count() == 1
    act = ContactActivity.objects.get(customer=customer, kind="operation")
    assert act.call_meta["event"] == "apply_result"
    assert act.call_meta["to_stage"] == "KNM 1"


def test_edit_due_requires_flag(flow, customer):
    OperationEngine.enter_start(customer)
    with pytest.raises(OperationFlowError):
        OperationEngine.edit_due(customer, timezone.now() + timedelta(hours=1), actor=None)
    OperationStage.objects.filter(name="Gọi lần đầu").update(allow_edit_due=True)
    new_due = timezone.now() + timedelta(hours=1)
    state = OperationEngine.edit_due(customer, new_due, actor=None)
    assert state.due_at == new_due
    state.refresh_from_db()
    assert state.due_at == new_due


# ───────────────────── set_stage: đặt bước tay (dropdown cột Tác nghiệp) ───────
#
# Đường đi TẮT quanh máy trạng thái: không cần transition (from_stage, result) nào
# tồn tại. Các test dưới cố tình KHÔNG dựng transition tới bước đích để chứng minh
# điều đó — nếu ai đó "sửa" set_stage thành đi qua bảng transition, chúng đỏ ngay.


@pytest.fixture
def care_stage():
    # KHÔNG đặt tên "D7": migration contact_flow/0002 seed sẵn HDSD/D3/D7/D15/D30 vào
    # MỌI database test, `OperationStage.name` unique → trùng là IntegrityError.
    return OperationStage.objects.create(name="D7 Engine Test")


def test_set_stage_moves_without_any_transition(flow, customer, care_stage):
    start, *_ = flow
    OperationEngine.enter_start(customer)
    due = timezone.now() + timedelta(days=7)

    state = OperationEngine.set_stage(customer, care_stage.id, due, actor=None)

    assert state.current_stage_id == care_stage.id
    assert state.due_at == due
    assert not OperationTransition.objects.filter(to_stage=care_stage).exists()


def test_set_stage_creates_state_for_customer_not_in_flow(customer, care_stage):
    """Khách chưa vào flow (cột Tác nghiệp đang hiện ô rỗng) vẫn chọn được bước —
    không có nhánh lazy-create thì thao tác này vô dụng đúng với nhóm khách cần nó nhất."""
    assert not CustomerOperationState.objects.filter(customer=customer).exists()
    due = timezone.now() + timedelta(days=3)

    state = OperationEngine.set_stage(customer, care_stage.id, due, actor=None)

    assert state.current_stage_id == care_stage.id
    assert state.due_at == due
    assert CustomerOperationState.objects.filter(customer=customer).count() == 1


def test_set_stage_logs_activity_with_from_and_to(flow, customer, care_stage):
    OperationEngine.enter_start(customer)
    ContactActivity.objects.filter(customer=customer).delete()

    OperationEngine.set_stage(customer, care_stage.id, timezone.now() + timedelta(hours=2), actor=None)

    act = ContactActivity.objects.get(customer=customer, kind="operation")
    assert act.call_meta["event"] == "set_stage"
    assert act.call_meta["from_stage"] == "Gọi lần đầu"
    assert act.call_meta["to_stage"] == "D7 Engine Test"


def test_set_stage_logs_from_stage_none_when_entering_flow(customer, care_stage):
    OperationEngine.set_stage(customer, care_stage.id, timezone.now() + timedelta(hours=2), actor=None)

    act = ContactActivity.objects.get(customer=customer, kind="operation")
    assert act.call_meta["event"] == "set_stage"
    assert act.call_meta["from_stage"] is None


def test_set_stage_keeps_last_result(flow, customer, care_stage):
    """Đổi bước tay KHÔNG phải một "kết quả" — kết quả gần nhất vẫn là cái đã ghi thật."""
    *_, r_knm, _r_sched, _r_end = flow
    OperationEngine.apply_result(customer, r_knm.id, actor=None)

    state = OperationEngine.set_stage(
        customer, care_stage.id, timezone.now() + timedelta(days=1), actor=None
    )

    assert state.last_result_id == r_knm.id


def test_set_stage_rejects_unknown_stage(customer):
    with pytest.raises(OperationFlowError):
        OperationEngine.set_stage(customer, 999999, timezone.now() + timedelta(days=1), actor=None)


def test_set_stage_rejects_inactive_stage(customer):
    disabled = OperationStage.objects.create(name="Đã tắt", is_active=False)
    with pytest.raises(OperationFlowError):
        OperationEngine.set_stage(
            customer, disabled.id, timezone.now() + timedelta(days=1), actor=None
        )


@pytest.mark.parametrize("delta", [timedelta(minutes=-1), timedelta(0)])
def test_set_stage_rejects_due_not_in_future(customer, care_stage, delta):
    """Cùng luật `edit_due`: bước mới phải có hạn ở tương lai, không thì nó sinh ra
    đã quá hạn — và không dòng nào được tạo (state chưa tồn tại thì vẫn chưa tồn tại)."""
    now = timezone.now()
    with pytest.raises(OperationFlowError):
        OperationEngine.set_stage(customer, care_stage.id, now + delta, actor=None, now=now)
    assert not CustomerOperationState.objects.filter(customer=customer).exists()


def test_set_stage_rejects_missing_due(customer, care_stage):
    with pytest.raises(OperationFlowError):
        OperationEngine.set_stage(customer, care_stage.id, None, actor=None)
