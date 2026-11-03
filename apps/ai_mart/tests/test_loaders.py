"""Kiểm hạ tầng nạp bằng một mart giả trỏ vào bảng tạm — không phụ thuộc mart thật."""

import uuid
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.base import MartTable

FAKE = MartTable(
    name="ai_stg.test_fake_mart",
    key_column="don_id",
    group="hot",
    source_key="s.id",
    select_sql=lambda where: (
        f"SELECT s.id AS don_id, s.val AS gia_tri FROM ai_stg.test_fake_src s WHERE {where}"
    ),
    candidate_ids_sql="SELECT id FROM ai_stg.test_fake_src WHERE changed_at > %(cutoff)s",
)


class LoaderTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS ai_stg.test_fake_src"
                " (id bigint PRIMARY KEY, val text, changed_at timestamptz);"
                "CREATE TABLE IF NOT EXISTS ai_stg.test_fake_mart"
                " (don_id bigint PRIMARY KEY, gia_tri text);"
            )

    def _seed(self, rows):
        with connection.cursor() as cur:
            cur.executemany(
                "INSERT INTO ai_stg.test_fake_src VALUES (%s, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET val=EXCLUDED.val, changed_at=EXCLUDED.changed_at",
                rows,
            )

    def test_watermark_roundtrip(self):
        self.assertIsNone(loaders.read_watermark("x"))
        now = timezone.now()
        loaders.write_watermark("x", now)
        self.assertEqual(loaders.read_watermark("x"), now)
        loaders.write_watermark("x", now + timedelta(minutes=1))
        self.assertEqual(loaders.read_watermark("x"), now + timedelta(minutes=1))

    def test_full_rebuild_then_upsert(self):
        now = timezone.now()
        self._seed([(1, "a", now), (2, "b", now)])
        n = loaders.full_rebuild(FAKE)
        self.assertEqual(n, 2)
        # Sửa 1 dòng + xoá 1 dòng nguồn → upsert theo ids phải phản ánh cả hai
        self._seed([(1, "a2", now)])
        with connection.cursor() as cur:
            cur.execute("DELETE FROM ai_stg.test_fake_src WHERE id = 2")
        loaders.upsert_by_ids(FAKE, [1, 2])
        with connection.cursor() as cur:
            cur.execute("SELECT don_id, gia_tri FROM ai_stg.test_fake_mart ORDER BY don_id")
            self.assertEqual(cur.fetchall(), [(1, "a2")])

    def test_upsert_empty_ids_is_noop(self):
        self.assertEqual(loaders.upsert_by_ids(FAKE, []), 0)

    def test_log_run_never_raises(self):
        # ten_bang dài bất thường vẫn không được ném lỗi ra ngoài
        loaders.log_run(uuid.uuid4(), "x" * 10, timezone.now(), timezone.now(), 1, "ok")

    def test_watermark_id_roundtrip(self):
        # read_watermark_id absent → 0
        self.assertEqual(loaders.read_watermark_id("y"), 0)
        # write → read
        loaders.write_watermark_id("y", 42)
        self.assertEqual(loaders.read_watermark_id("y"), 42)
        # update to 99
        loaders.write_watermark_id("y", 99)
        self.assertEqual(loaders.read_watermark_id("y"), 99)
        # moc and moc_id columns don't clobber each other
        now = timezone.now()
        loaders.write_watermark("y", now)
        # moc_id still 99
        self.assertEqual(loaders.read_watermark_id("y"), 99)
        # moc is set and not None
        self.assertIsNotNone(loaders.read_watermark("y"))
        self.assertEqual(loaders.read_watermark("y"), now)

    def test_candidate_ids(self):
        # seed rows with different changed_at
        now = timezone.now()
        past = now - timedelta(hours=1)
        self._seed([(10, "old", past), (11, "new", now)])
        # cutoff between them → only newer id returned
        cutoff = past + timedelta(minutes=30)
        ids = loaders.candidate_ids(FAKE, cutoff)
        self.assertEqual(ids, [11])
        # mart with no candidate_ids_sql → []
        no_sql_mart = MartTable(
            name="ai_stg.test_fake_mart",
            key_column="don_id",
            group="hot",
            source_key="s.id",
            select_sql=lambda where: f"SELECT s.id AS don_id FROM ai_stg.test_fake_src s WHERE {where}",
            candidate_ids_sql=None,
        )
        self.assertEqual(loaders.candidate_ids(no_sql_mart, cutoff), [])
