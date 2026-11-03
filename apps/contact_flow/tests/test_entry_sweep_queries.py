"""Sweep entry tác nghiệp phải là hằng số query theo số khách (audit 2026-09-23 T1 §2.3).

Trước: `enter_start` từng khách = SELECT FOR UPDATE + INSERT state + INSERT activity
+ 1 transaction/khách (đo 33→63 query khi 10→20 khách). Sau: 1 lô = 1 transaction,
`bulk_create` state (ignore_conflicts) + `bulk_create` activity.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel
from apps.contact_flow.application.services.entry_service import OperationEntryService
from apps.contact_flow.application.services.operation_engine import OperationEngine
from apps.contact_flow.models import CustomerOperationState, OperationStage
from apps.customers.models import ContactActivity, Customer
from apps.orders.models import Order, OrderSource


class EntrySweepQueryCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="sweep-q-ch", name="Sweep Q", owner="S")
        cls.start = OperationStage.objects.create(name="Gọi lần đầu", is_start=True)

    def _seed(self, count: int, prefix: str) -> list[int]:
        ids = []
        for i in range(count):
            phone = f"09{prefix}{i:05d}"
            customer = Customer.objects.create(
                full_name=f"KH {prefix} {i}", phone_raw=phone, phone_normalized=phone
            )
            Order.objects.create(
                channel=self.channel, customer=customer, order_source=OrderSource.LANDING
            )
            ids.append(customer.id)
        return ids

    def _sweep(self) -> tuple[int, dict]:
        with CaptureQueriesContext(connection) as ctx:
            summary = OperationEntryService.sweep(hours=24)
        return len(ctx.captured_queries), summary

    def test_sweep_query_count_constant_n5_vs_n15(self):
        small_ids = self._seed(5, "50")
        small_q, small = self._sweep()
        self.assertEqual(small, {"created": 5})

        large_ids = self._seed(15, "51")
        large_q, large = self._sweep()
        self.assertEqual(large, {"created": 15})

        self.assertEqual(
            large_q, small_q,
            f"N+1 sweep: 5 khách = {small_q} query, 15 khách = {large_q} query.",
        )
        # Kết quả nghiệp vụ không đổi: mỗi khách 1 state ở bước start + 1 activity
        # enter_start với cùng call_meta như `enter_start` lẻ.
        for cid in [*small_ids, *large_ids]:
            state = CustomerOperationState.objects.get(customer_id=cid)
            self.assertEqual(state.current_stage_id, self.start.id)
            self.assertIsNotNone(state.due_at)
            activity = ContactActivity.objects.get(customer_id=cid, kind="operation")
            self.assertEqual(
                activity.call_meta, {"event": "enter_start", "to_stage": self.start.name}
            )
            self.assertIsNone(activity.staff_id)

    def test_sweep_skips_customers_with_existing_state_and_is_idempotent(self):
        ids = self._seed(3, "52")
        other_stage = OperationStage.objects.create(name="KNM 1")
        # Khách đầu đã có state ở bước khác → không đụng.
        CustomerOperationState.objects.create(
            customer_id=ids[0], current_stage=other_stage, entered_at="2026-01-01T00:00:00Z"
        )
        self.assertEqual(OperationEntryService.sweep(hours=24), {"created": 2})
        self.assertEqual(
            CustomerOperationState.objects.get(customer_id=ids[0]).current_stage_id,
            other_stage.id,
        )
        # Chạy lại: không tạo trùng state/activity.
        self.assertEqual(OperationEntryService.sweep(hours=24), {"created": 0})
        self.assertEqual(ContactActivity.objects.filter(customer_id__in=ids).count(), 2)

    def test_bulk_matches_single_enter_start_payload(self):
        ids = self._seed(2, "53")
        single = Customer.objects.get(id=ids[0])
        OperationEngine.enter_start(single, start_stage=self.start)
        OperationEngine.enter_start_bulk([ids[1]], start_stage=self.start)

        a = ContactActivity.objects.get(customer_id=ids[0])
        b = ContactActivity.objects.get(customer_id=ids[1])
        self.assertEqual((a.kind, a.note, a.call_meta, a.staff_id), (b.kind, b.note, b.call_meta, b.staff_id))
