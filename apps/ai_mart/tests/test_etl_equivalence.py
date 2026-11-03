"""Bảo hiểm lõi của kiến trúc ETL: trạng thái mart sau (rebuild → sửa → sync)
phải Y HỆT trạng thái sau (sửa → rebuild). Lệch = watermark/candidate sót đường ghi.

Under Django TestCase the 5-minute OVERLAP makes candidacy trivially true for most
mutations below — the test still proves that the UPSERT path (DELETE-by-key +
INSERT-select) produces byte-identical rows to TRUNCATE+INSERT, which is the point.

o4's CallRecord mutation is the ONE exception, and it needs more than the OVERLAP
trick to prove anything: the whole test runs inside ONE wrapped transaction (Django
TestCase), and Postgres's `now()` — what the dw_updated_at triggers use — is FROZEN
at transaction start, not evaluated per statement. So every trigger-stamped row in
this test, no matter which statement touches it or when, ends up with the exact same
dw_updated_at. A cutoff based on Python's real wall-clock (which DOES advance) can
never fall strictly between two trigger-stamped events for that reason — there's
nothing to fall between. The only way to get genuine before/after separation is to
bypass the trigger and write an explicitly OLD dw_updated_at directly (see
`_backdate` below) for the one row (o4's customer + order) that must NOT be a
trivial candidate, days away from "now" so no clock-skew or timing subtlety matters.
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.application import etl_service
from apps.ai_mart.infrastructure import loaders
from apps.callcenter.models import CallRecord
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem
from apps.ai_mart.tests.helpers import mk_order


def _backdate(table: str, row_id: int, when) -> None:
    """Force `table.dw_updated_at` = `when` for one row, bypassing the
    `{table}_dw_touch` trigger (which would otherwise overwrite any UPDATE's
    dw_updated_at back to Postgres's frozen now()).

    Uses session_replication_role='replica' (not ALTER TABLE ... DISABLE TRIGGER):
    the ALTER form fails with "cannot ALTER TABLE because it has pending trigger
    events" once earlier statements in the same transaction have queued FK/RI
    trigger events on the same table — session_replication_role is a plain GUC,
    not DDL, so it has no such restriction."""
    with connection.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        try:
            cur.execute(f"UPDATE {table} SET dw_updated_at = %s WHERE id = %s", [when, row_id])
        finally:
            cur.execute("SET session_replication_role = origin")


def _snapshot(table, key):
    """Fetch all rows from a table, excluding dong_bo_luc timestamp column."""
    with connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY {key}")
        cols = [c[0] for c in cur.description]
        # dong_bo_luc is the timestamp of the last run — expected to differ
        skip = {"dong_bo_luc"}
        return [
            {c: v for c, v in zip(cols, row) if c not in skip} for row in cur.fetchall()
        ]


class EquivalenceTests(TestCase):
    maxDiff = None  # Show full diffs in error messages

    def test_sync_equals_rebuild(self):
        """Verify sync_hot() produces identical mart rows to rebuild_all().

        Sequence:
        1. Create initial orders → rebuild_all()
        2. Mutate via 4 paths: queryset.update, DELETE, new Order, CallRecord on an
           already-materialized order (proves khach_hang candidacy — I3)
        3. sync_hot() — incremental upsert
        4. rebuild_all() — full rebuild
        5. Compare snapshots of both (minus timestamp cols)

        The equivalence holds within one test because:
        - 5-minute OVERLAP means candidate_ids() captures all mutations
        - so_ngay_tu_lan_mua_cuoi uses day granularity → consistent within one test
        """
        # Create initial orders with OrderItems
        o1 = mk_order(status=2)
        o2 = mk_order(status=3)
        # o4 is created BEFORE the first rebuild and is never touched by .update()/
        # DELETE below — its customer only becomes a ai.khach_hang candidate again
        # through the callcenter_call_records candidate branch (I3), not through the
        # "orders" or "order_assessments" branches. Attaching a CallRecord to it later
        # is the proof that I3's branch actually reaches ai.khach_hang.
        o4 = mk_order(status=3)

        # Create OrderItems for o1 and o2 (required by dong_don_hang mart)
        prod1 = Product.objects.create(name="Test Product 1", sku="TEST-SKU-1")
        prod2 = Product.objects.create(name="Test Product 2", sku="TEST-SKU-2")
        OrderItem.objects.create(order=o1, product=prod1, sku="TEST-SKU-1", quantity=1)
        OrderItem.objects.create(order=o1, product=prod2, sku="TEST-SKU-2", quantity=2)
        # o2 gets 1 item to ensure dong_don_hang has non-empty rows after rebuild
        OrderItem.objects.create(order=o2, product=prod1, sku="TEST-SKU-1", quantity=1)

        # Initial rebuild
        etl_service.rebuild_all()

        # I3 proof: force o4's customer AND o4 itself to look "stale" (30 days old)
        # by writing dw_updated_at directly, bypassing the trigger — see module
        # docstring for why real-time timestamps can't do this inside one TestCase
        # transaction. Then pin the watermark so cutoff sits 15 days ago: strictly
        # AFTER the backdated 30-days-ago stamp (so customer/order o4 are NOT
        # trivial candidates via their own branches) and strictly BEFORE "now"
        # (so every OTHER mutation below, all trigger-stamped at the frozen
        # transaction time, is still trivially a candidate as usual).
        old_ts = timezone.now() - timedelta(days=30)
        _backdate("customers", o4.customer_id, old_ts)
        _backdate("orders", o4.id, old_ts)
        loaders.write_watermark(
            etl_service.HOT_KEY, timezone.now() - timedelta(days=15) + etl_service.OVERLAP
        )

        # 4 kinds of mutations across different write paths:
        # 1. queryset.update() — status change on o1
        Order.objects.filter(id=o1.id).update(status=3)

        # 2. DELETE — remove OrderItems from o1
        OrderItem.objects.filter(order=o1).delete()

        # 3. INSERT new order o3 (exercises brand-new-row INSERT path for both
        # ai.don_hang and ai.khach_hang — o3's customer is also freshly created).
        o3 = mk_order(status=0)

        # 4. Attach a CallRecord to o4. o4 was materialized in the first rebuild with
        # so_cuoc_goi=0 and is otherwise untouched, so this is the ONLY mutation that
        # should make o4's customer a candidate again — and only via the
        # callcenter_call_records branch of khach_hang._CANDIDATES (I3).
        CallRecord.objects.create(
            call_id=f"call-{o4.id}-001",
            call_type="IN",
            caller="0912345678",
            caller_normalized="0912345678",
            order=o4,
        )

        # Incremental sync
        etl_service.sync_hot()
        after_sync = {
            t: _snapshot(t, k)
            for t, k in [
                ("ai.don_hang", "don_id"),
                ("ai.dong_don_hang", "dong_id"),
                ("ai.khach_hang", "khach_id"),
                ("ai.cuoc_goi", "cuoc_goi_id"),
            ]
        }

        # Full rebuild
        etl_service.rebuild_all()
        after_rebuild = {
            t: _snapshot(t, k)
            for t, k in [
                ("ai.don_hang", "don_id"),
                ("ai.dong_don_hang", "dong_id"),
                ("ai.khach_hang", "khach_id"),
                ("ai.cuoc_goi", "cuoc_goi_id"),
            ]
        }

        # Non-emptiness guards: prevent vacuous equality when WHERE clauses are always false
        self.assertGreaterEqual(len(after_sync["ai.don_hang"]), 4, "don_hang should have ≥4 rows")
        self.assertGreaterEqual(len(after_sync["ai.khach_hang"]), 4, "khach_hang should have ≥4 rows")
        self.assertEqual(len(after_sync["ai.dong_don_hang"]), 1, "dong_don_hang should have exactly 1 row (o2's item)")
        self.assertGreaterEqual(len(after_sync["ai.cuoc_goi"]), 1, "cuoc_goi should have ≥1 row")

        # I3 proof: o4's customer must show so_cuoc_goi=1 in ai.khach_hang after
        # sync_hot(). o4 itself is not mutated (no .update(), no delete) — the ONLY
        # write that touched it is the CallRecord above. Without the
        # callcenter_call_records branch in khach_hang._CANDIDATES, o4's customer is
        # never re-selected as a candidate, so this row would still read so_cuoc_goi=0
        # (the value materialized at the first rebuild, before the call existed).
        o4_khach_rows = [r for r in after_sync["ai.khach_hang"] if r["khach_id"] == o4.customer_id]
        self.assertEqual(
            len(o4_khach_rows), 1, f"Expected 1 khach_hang row for o4's customer, got {len(o4_khach_rows)}"
        )
        self.assertEqual(
            o4_khach_rows[0].get("so_cuoc_goi"),
            1,
            "o4's customer should have so_cuoc_goi=1 after CallRecord attached (I3 candidate branch)",
        )

        # Compare snapshots
        if after_sync != after_rebuild:
            # Print readable diff for debugging
            for table in after_sync:
                if after_sync[table] != after_rebuild[table]:
                    self.fail(
                        f"Table {table} differs after sync vs rebuild:\n"
                        f"  after_sync ({len(after_sync[table])} rows): {after_sync[table]}\n"
                        f"  after_rebuild ({len(after_rebuild[table])} rows): {after_rebuild[table]}"
                    )

        self.assertEqual(after_sync, after_rebuild)
