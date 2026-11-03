from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.cuoc_goi import MART
from apps.callcenter.models import CallRecord


class CuocGoiTests(TestCase):
    def _mk(self, **kw):
        base = dict(
            call_id="c-1",
            call_type="1",
            caller="0912345678",
            caller_normalized="0912345678",
            transcript="alo",
        )
        base.update(kw)
        return CallRecord.objects.create(**base)

    def test_direction_and_masking(self):
        rec = self._mk()
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT loai_cuoc_goi_text, sdt_che, agent_ten, tom_tat_ai"
                " FROM ai.cuoc_goi WHERE cuoc_goi_id = %s", [rec.id],
            )
            direction, sdt, agent, tomtat = cur.fetchone()
        self.assertEqual(direction, "Gọi ra")
        self.assertEqual(sdt, "0912***678")
        self.assertIsNone(agent)   # agent_name rỗng → NULL, KHÔNG bịa "Không xác định"
        self.assertIsNone(tomtat)

    def test_inbound_and_summary(self):
        rec = self._mk(
            call_id="c-2", call_type="2",
            extract_payload={"clean": {"call_summary": "khách hỏi giá"}},
        )
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT loai_cuoc_goi_text, tom_tat_ai FROM ai.cuoc_goi WHERE cuoc_goi_id = %s",
                [rec.id],
            )
            direction, tomtat = cur.fetchone()
        self.assertEqual(direction, "Gọi vào")
        self.assertEqual(tomtat, "khách hỏi giá")
