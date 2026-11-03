"""Test OrderRecall: contract return_slip_code dạng mảng + khoá chống mất field
khi upsert đồng thời (gộp từ test_return_slip_array.py và
test_upsert_order_recall_lock.py).

Phần 1 — return_slip_code là ArrayField ở DB nhưng contract API vẫn là string phẩy.

Phần 2 (các hàm pytest cuối file) — `_upsert_order_recall` không được đánh mất field
khi 2 request PATCH inline chạy đồng thời trên cùng đơn (VD nhân viên sửa tay đúng
lúc KV returns sync ghi `return_slip_code`) — và field không có mặt tường minh trong
payload phải giữ nguyên, không bị suy diễn/ghi đè bởi payload full-form cũ.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date
from importlib import import_module

import pytest
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase

from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_commands import OrderServiceCommands
from apps.orders.application.services.order_service_helpers import _parse_slip_codes
from apps.orders.models import Order, OrderRecall, OrderRecallEntry
from apps.orders.transport.serializers.order_serializers_impl.kiotviet_landing_serializers import (
    OrderRecallInputSerializer,
)
from apps.users.tests.helpers import make_admin


class ParseSlipCodesTests(SimpleTestCase):
    def test_splits_comma_string_and_dedupes(self):
        self.assertEqual(_parse_slip_codes("TH001, TH002 ,TH001"), ["TH001", "TH002"])

    def test_empty_string_becomes_empty_list(self):
        self.assertEqual(_parse_slip_codes(""), [])
        self.assertEqual(_parse_slip_codes(None), [])

    def test_accepts_list_input(self):
        self.assertEqual(_parse_slip_codes([" TH001 ", "TH002", ""]), ["TH001", "TH002"])

    def test_truncates_to_64_chars(self):
        long_code = "X" * 80
        self.assertEqual(_parse_slip_codes(long_code), ["X" * 64])


class ReturnSlipArrayWriteTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="slip-arr-ch", name="Slip Arr Ch", owner="Slip Arr Ch")
        self.customer = Customer.objects.create(
            full_name="Khach Phieu Tra Arr",
            phone_raw="0909000030",
            phone_normalized="0909000030",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Phieu Tra Arr",
            phone="0909000030",
            order_source="landing",
        )

    def test_upsert_recall_stores_list_from_comma_string(self):
        from apps.orders.application.services.order_service_commands import OrderServiceCommands

        OrderServiceCommands._upsert_order_recall(
            self.order, {"return_slip_code": "TH001, TH002"}, None
        )
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001", "TH002"])

    def test_serializer_joins_array_into_string(self):
        from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (
            OrderSerializer,
        )

        OrderRecall.objects.create(order=self.order, return_slip_code=["TH001", "TH002"])
        self.order.refresh_from_db()
        data = OrderSerializer(self.order).data
        self.assertEqual(data["recall"]["return_slip_code"], "TH001, TH002")


class InlineUpdateRecallEntriesTests(TestCase):
    """PATCH inline /api/orders/{id} — contract `recall_entries` (Task 2): mảng
    full-replace các nhóm thu hồi, độc lập với `recall.return_slip_code`."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-recall-code-array")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(
            code="recall-arr-ch", name="Recall Arr Ch", owner="Recall Arr Ch"
        )
        self.customer = Customer.objects.create(
            full_name="Khach Recall Arr",
            phone_raw="0909000040",
            phone_normalized="0909000040",
        )

    def _make_order(self) -> Order:
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Recall Arr",
            phone="0909000040",
            order_source="landing",
        )

    def test_patch_creates_multiple_recall_entries(self):
        """Full-replace: gửi 2 nhóm thì đơn có đúng 2 nhóm, đúng thứ tự."""
        order = self._make_order()
        resp = self.client.patch(
            f"/api/orders/{order.id}",
            {
                "recall_entries": [
                    {"recall_code": "RC-01", "recall_created_date": "2026-08-12", "recall_received_date": "2026-08-15"},
                    {"recall_code": "RC-02", "recall_created_date": "2026-08-20", "recall_received_date": None},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        entries = list(order.recall_entries.all())
        self.assertEqual([e.recall_code for e in entries], ["RC-01", "RC-02"])
        self.assertEqual(entries[1].recall_received_date, None)
        self.assertEqual([e["recall_code"] for e in resp.data["recall_entries"]], ["RC-01", "RC-02"])

    def test_patch_replaces_the_whole_entry_list(self):
        order = self._make_order()
        OrderRecallEntry.objects.create(order=order, recall_code="OLD-1")
        OrderRecallEntry.objects.create(order=order, recall_code="OLD-2")
        resp = self.client.patch(
            f"/api/orders/{order.id}",
            {"recall_entries": [{"recall_code": "NEW-1"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["NEW-1"])

    def test_empty_group_is_dropped(self):
        """Nhóm cả 3 ô trống = nhóm bị xoá trên UI, không được lưu."""
        order = self._make_order()
        resp = self.client.patch(
            f"/api/orders/{order.id}",
            {
                "recall_entries": [
                    {"recall_code": "RC-01", "recall_created_date": None, "recall_received_date": None},
                    {"recall_code": "", "recall_created_date": None, "recall_received_date": None},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["RC-01"])

    def test_patch_without_recall_entries_keeps_existing_groups(self):
        order = self._make_order()
        OrderRecallEntry.objects.create(order=order, recall_code="KEEP-1")
        resp = self.client.patch(f"/api/orders/{order.id}", {"note": "x"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["KEEP-1"])

    def test_patch_with_explicit_empty_list_clears_all_groups(self):
        """`"recall_entries": []` tường minh phải xoá SẠCH mọi nhóm — khác `None`/vắng
        mặt key (không đụng, xem test trên) và khác "mọi nhóm đều rỗng ô" (bị lọc từng
        nhóm). Đây là hành vi khi nhân viên xoá nhóm cuối cùng ở trang chi tiết: mảng gửi
        lên rỗng hẳn, không phải một nhóm rỗng. Code check `is None` (không phải falsy)
        nên đã đúng, test này khoá lại hành vi đó."""
        order = self._make_order()
        OrderRecallEntry.objects.create(order=order, recall_code="KEEP-1")
        resp = self.client.patch(f"/api/orders/{order.id}", {"recall_entries": []}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(order.recall_entries.all()), [])
        self.assertEqual(resp.data["recall_entries"], [])

    def test_return_slip_code_still_works_independently(self):
        """Phiếu trả hàng ở mức đơn, không bị full-replace nhóm đụng tới."""
        order = self._make_order()
        self.client.patch(
            f"/api/orders/{order.id}", {"recall": {"return_slip_code": "PT-1, PT-2"}}, format="json"
        )
        resp = self.client.patch(
            f"/api/orders/{order.id}", {"recall_entries": [{"recall_code": "RC-01"}]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["recall"]["return_slip_code"], "PT-1, PT-2")


class OrderRecallInputSerializerTests(SimpleTestCase):
    """Field return_slip_code phải chấp nhận chuỗi phẩy dài (N mã, mỗi mã <=64 ký tự,
    độ dài tổng thường >64) — trước fix max_length=64 chặn 400 trước khi tới
    _parse_slip_codes."""

    def test_accepts_long_multi_code_comma_string(self):
        codes = [f"TH{i:04d}" for i in range(1, 20)]
        long_value = ", ".join(codes)
        self.assertGreater(len(long_value), 64)

        serializer = OrderRecallInputSerializer(data={"return_slip_code": long_value})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["return_slip_code"], long_value)

    # test_recall_code_accepts_long_multi_code_comma_string XOÁ (Task 2): field
    # `recall_code` không còn tồn tại trên OrderRecallInputSerializer — mã thu hồi
    # đã tách sang OrderRecallEntryInputSerializer, MỘT mã/nhóm (max_length=64),
    # không còn ngữ nghĩa "chuỗi phẩy N mã" mà test này minh chứng.


# --- Phần 2: khoá `_upsert_order_recall` (pytest-style, giữ nguyên từ
# test_upsert_order_recall_lock.py — test cuối cần transaction=True + thread thật).


def _make_order(channel: Channel, customer: Customer) -> Order:
    suffix = uuid.uuid4().hex[:8]
    return Order.objects.create(
        channel=channel,
        customer=customer,
        name=f"KH Recall {suffix}",
        phone=f"09{suffix}",
        order_source="landing",
    )


@pytest.fixture
def channel():
    return Channel.objects.create(code="recall-lock-ch", name="Recall Lock", owner="Recall Lock")


@pytest.fixture
def customer():
    return Customer.objects.create(
        full_name="KH Recall Lock", phone_raw="0909222333", phone_normalized="0909222333",
    )


# test_field_absent_from_payload_is_not_overwritten VÀ test_explicit_none_clears_recall_code
# XOÁ (Task 2): cả hai minh chứng "field A đổi, field B (recall_code) giữ nguyên" trên
# _upsert_order_recall — nhưng recall_code đã tách sang OrderRecallEntry (Task 1) nên
# allowed fields của _upsert_order_recall giờ CHỈ CÒN return_slip_code, không còn field
# thứ hai nào để minh chứng tính độc lập. Hành vi "None tường minh xoá field" vẫn còn ý
# nghĩa và được giữ lại ở test_explicit_none_clears_return_slip_code ngay dưới (bỏ phần
# setup/assert recall_code).


@pytest.mark.django_db
def test_explicit_none_clears_return_slip_code(channel, customer):
    """Field có mặt tường minh (kể cả None/rỗng) VẪN được ghi — đây là nhân viên chủ
    động xoá, khác với field vắng mặt hoàn toàn khỏi payload."""
    order = _make_order(channel, customer)
    OrderRecall.objects.create(order=order, return_slip_code=["TH1"])

    OrderServiceCommands._upsert_order_recall(order, {"return_slip_code": None}, None)

    recall = OrderRecall.objects.get(order=order)
    assert recall.return_slip_code == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_inline_updates_are_serialized_not_lost():
    """select_for_update() phải khoá dòng recall: transaction thứ 2 đứng chờ transaction
    thứ nhất commit thay vì ghi chồng lên nhau. (Task 2: chỉ còn return_slip_code trên
    OrderRecall nên cả 2 thread cùng ghi field này — vẫn đủ minh chứng lock có tác dụng,
    khác với trước đây dùng 2 field độc lập vì recall_code đã tách sang OrderRecallEntry.)
    """
    channel = Channel.objects.create(
        code="recall-lock-race", name="Recall Lock Race", owner="Recall Lock Race"
    )
    customer = Customer.objects.create(
        full_name="KH Recall Race", phone_raw="0909222999", phone_normalized="0909222999",
    )
    order = _make_order(channel, customer)
    OrderRecall.objects.create(order=order, return_slip_code=["OLD-CODE"])

    holding = threading.Event()  # thread 1 đã ghi field của nó, CHƯA commit
    release = threading.Event()
    errors: list[Exception] = []

    def first_writer():
        try:
            with transaction.atomic():
                OrderServiceCommands._upsert_order_recall(order, {"return_slip_code": "NEW-A"}, None)
                holding.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def second_writer():
        try:
            with transaction.atomic():
                OrderServiceCommands._upsert_order_recall(order, {"return_slip_code": "NEW-B"}, None)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=first_writer)
    t1.start()
    assert holding.wait(10), "thread 1 không chạy được"

    t2 = threading.Thread(target=second_writer)
    t2.start()
    t2.join(1.0)
    assert t2.is_alive(), "update thứ 2 phải đứng chờ khoá select_for_update của update thứ 1"

    release.set()
    t1.join(10)
    t2.join(10)
    assert not errors, errors

    recall = OrderRecall.objects.get(order=order)
    # Thread 2 chỉ chạy được SAU khi thread 1 commit (nhờ khoá) nên ghi đè lên "NEW-A" —
    # giá trị cuối cùng là của transaction chạy sau, không phải giá trị rác do race.
    assert recall.return_slip_code == ["NEW-B"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_recall_entries_replace_does_not_duplicate_groups():
    """`_replace_order_recall_entries` xoá-rồi-tạo-lại (DELETE + bulk_create) không tự
    loại trừ hai transaction chồng nhau: T1 xoá nhóm cũ rồi chèn nhóm mới, CHƯA commit.
    DELETE của T2 nhắm cùng dòng cũ nên bị Postgres tự chặn (row lock của DELETE) —
    nhưng sau khi T1 commit, dòng cũ đã biến mất khỏi snapshot câu lệnh của T2 nên T2
    xoá ĐÚNG 0 dòng (nhóm T1 vừa chèn không nằm trong WHERE ban đầu của T2, không phải
    dòng nó đang chờ khoá), rồi INSERT thêm nhóm của mình → kết quả có CẢ HAI nhóm thay
    vì chỉ nhóm mới nhất (finding #1, final review nhánh koc-recall-entry-groups).

    Dòng `select_for_update()` ở đầu `_replace_order_recall_entries` khoá cả dòng Order
    làm điểm nối tiếp: T2 phải đợi TOÀN BỘ transaction của T1 commit trước khi bắt đầu
    DELETE của chính nó, nên DELETE của T2 thấy đúng nhóm T1 vừa chèn và xoá sạch.
    """
    channel = Channel.objects.create(
        code="recall-entries-race", name="Recall Entries Race", owner="Recall Entries Race"
    )
    customer = Customer.objects.create(
        full_name="KH Recall Entries Race", phone_raw="0909333444", phone_normalized="0909333444",
    )
    order = _make_order(channel, customer)
    OrderRecallEntry.objects.create(order=order, recall_code="OLD-1")

    holding = threading.Event()  # thread 1 đã INSERT nhóm mới, CHƯA commit
    release = threading.Event()
    errors: list[Exception] = []

    def first_writer():
        try:
            with transaction.atomic():
                OrderServiceCommands._replace_order_recall_entries(
                    order, [{"recall_code": "RC-A"}], None
                )
                holding.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def second_writer():
        try:
            with transaction.atomic():
                OrderServiceCommands._replace_order_recall_entries(
                    order, [{"recall_code": "RC-B"}], None
                )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=first_writer)
    t1.start()
    assert holding.wait(10), "thread 1 không chạy được"

    t2 = threading.Thread(target=second_writer)
    t2.start()
    t2.join(1.0)
    assert t2.is_alive(), "PATCH thứ 2 phải đứng chờ commit của PATCH thứ 1"

    release.set()
    t1.join(10)
    t2.join(10)
    assert not errors, errors

    entries = list(OrderRecallEntry.objects.filter(order=order).order_by("id"))
    # Có khoá: full-replace đúng nghĩa, T2 chạy sau nên chỉ còn nhóm của nó.
    # Không khoá (bug): còn CẢ hai — ["RC-A", "RC-B"] — vì T2 xoá hụt nhóm T1 vừa chèn.
    assert [e.recall_code for e in entries] == ["RC-B"], [e.recall_code for e in entries]


def _split():
    # Tên module bắt đầu bằng số nên phải import bằng chuỗi.
    return import_module("apps.orders.migrations.0128_copy_recall_entries").split_recall_into_entries


class SplitRecallIntoEntriesTests(SimpleTestCase):
    def test_each_code_becomes_a_group_sharing_the_dates(self):
        split = _split()
        result = split(["RC-01", "RC-02"], date(2026, 8, 12), date(2026, 8, 15))
        self.assertEqual(
            result,
            [
                ("RC-01", date(2026, 8, 12), date(2026, 8, 15)),
                ("RC-02", date(2026, 8, 12), date(2026, 8, 15)),
            ],
        )

    def test_dates_without_code_still_produce_one_group(self):
        split = _split()
        self.assertEqual(split([], date(2026, 8, 12), None), [("", date(2026, 8, 12), None)])

    def test_completely_empty_produces_no_group(self):
        split = _split()
        self.assertEqual(split([], None, None), [])
        self.assertEqual(split(None, None, None), [])

    def test_blank_and_whitespace_codes_are_dropped(self):
        split = _split()
        self.assertEqual(split(["  ", "RC-01 "], None, None), [("RC-01", None, None)])
