"""Không một ô nào trong schema ai được chứa SĐT 10 số trần. Quét MỌI cột text
VÀ text[] của MỌI bảng — bảng thêm ở sprint sau tự bị quét (M10: mảng như
nhu_cau_ai/danh_sach_sku/sku_hay_mua trước đây lọt lưới vì query chỉ lọc
format_type = 'text')."""

import re
from datetime import datetime, timedelta

from django.db import connection
from django.utils import timezone
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.ai_mart.tests import chat_helpers as ch
from apps.ai_mart.tests.helpers import mk_order
from apps.callcenter.models import CallRecord

PHONE = re.compile(r"(?<!\d)0\d{9}(?!\d)")
# Ngoại lệ CÓ CHỦ ĐÍCH của luật che PII (spec ai-mart §4.6 + spec chat §4):
# nội dung hội thoại thô, Hermes cần nguyên văn để trả lời về nội dung tư vấn.
# Ba cột chat cùng chuẩn với cuoc_goi.transcript — COMMENT của chúng CẤM agent
# trích SĐT/địa chỉ ra câu trả lời. Mọi cột khác vẫn bị quét.
SKIP_COLUMNS = {
    ("cuoc_goi", "transcript"),
    ("cuoc_goi", "tom_tat_ai"),
    ("hoi_thoai", "noi_dung"),
    ("hoi_thoai", "tom_tat_ai"),
    ("tin_nhan", "noi_dung"),
}


class MaskingTests(TestCase):
    def _seed_chat(self) -> int:
        """Dữ liệu chat có SĐT NHÚNG TRONG các trường tưởng như vô hại.

        Không phải giả định: nhân viên Pancake thường xuyên đổi tên hồ sơ khách
        THÀNH chính số điện thoại, và đặt tên thẻ theo số. Không seed những
        dòng này thì ai.hoi_thoai/ai.tin_nhan rỗng lúc quét — bộ quét chạy
        no-op đúng trên hai bảng nó cần canh nhất (đúng lỗ hổng review cuối
        nhánh bắt được).
        """
        page = ch.mk_page()
        khach = ch.mk_customer(page, name="0912345678", phone="0912345678")
        conv = ch.mk_conversation(page, customer_id=khach)
        ch.mk_message(conv, page, direction="in",
                      content="số em là 0912345678, giao giúp em nhé")
        ch.mk_message(conv, page, direction="out", content="dạ em ghi nhận ạ")
        ch.tag_conversation(conv, ch.mk_tag(page, "0912345678"))
        ch.tag_conversation(conv, ch.mk_tag(page, "Đã tư vấn"))
        return conv

    def test_no_raw_phone_numbers_anywhere(self):
        order = mk_order(status=3)

        # Create CallRecord with phone numbers to verify masking works
        CallRecord.objects.create(
            call_id="test-call-001",
            caller="0912345678",
            caller_normalized="0912345678",
            transcript="alo 0912345678",
            order=order,
            start_time=timezone.now() - timedelta(days=1),
        )
        self._seed_chat()

        etl_service.rebuild_all()
        offenders = []
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0"
                " WHERE n.nspname = 'ai' AND c.relkind = 'r'"
                " AND format_type(a.atttypid, a.atttypmod) IN ('text', 'text[]')"
            )
            targets = [
                (table, col, kind) for table, col, kind in cur.fetchall() if (table, col) not in SKIP_COLUMNS
            ]
            for table, col, kind in targets:
                if kind == "text[]":
                    cur.execute(f'SELECT unnest("{col}") FROM ai."{table}" WHERE "{col}" IS NOT NULL LIMIT 500')
                else:
                    cur.execute(f'SELECT "{col}" FROM ai."{table}" WHERE "{col}" IS NOT NULL LIMIT 500')
                for (val,) in cur.fetchall():
                    if PHONE.search(val or ""):
                        offenders.append(f"ai.{table}.{col}: {val[:30]}")
        self.assertEqual(offenders, [])

        # Đối chứng: ba cột ngoại lệ PHẢI thật sự chứa SĐT trần. Nếu không, hoặc
        # dữ liệu seed sai (bộ quét ở trên chạy no-op — đúng lỗ hổng review cuối
        # nhánh bắt được), hoặc ngoại lệ đã thừa và phải bỏ khỏi SKIP_COLUMNS.
        with connection.cursor() as cur:
            for table, col in [("cuoc_goi", "transcript"), ("hoi_thoai", "noi_dung"),
                               ("tin_nhan", "noi_dung")]:
                cur.execute(f'SELECT "{col}" FROM ai."{table}" WHERE "{col}" IS NOT NULL')
                vals = [v for (v,) in cur.fetchall()]
                self.assertTrue(
                    any(PHONE.search(v or "") for v in vals),
                    f"ai.{table}.{col} không có SĐT trần — ngoại lệ PII chưa được chứng minh",
                )

    def test_phone_embedded_in_name_and_tag_is_masked_in_place(self):
        """Chốt HÀNH VI che, không chỉ dựa vào việc bộ quét im lặng.

        Bộ quét ở trên chỉ nói "không thấy SĐT trần". Nó vẫn xanh nếu ai đó
        làm cột rỗng, hoặc che bằng ai.che_sdt() (che TOÀN BỘ chuỗi, biến
        'Nguyễn Văn A' thành 'Nguy***n A'). Test này chốt đúng dạng đầu ra.
        """
        conv = self._seed_chat()
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT khach_ten, nhan_tag, nhan_tag_text FROM ai.hoi_thoai"
                " WHERE hoi_thoai_id = %s",
                [conv],
            )
            khach_ten, nhan_tag, nhan_tag_text = cur.fetchone()
            cur.execute("SELECT ai.che_sdt('0912345678')")
            (che_sdt,) = cur.fetchone()

        self.assertEqual(khach_ten, "0912***678")
        # Hai kiểu che phải cho CÙNG một dạng với SĐT trần, nếu không agent đọc
        # ra hai "số" khác nhau cho cùng một khách.
        self.assertEqual(khach_ten, che_sdt)
        self.assertEqual(sorted(nhan_tag), sorted(["0912***678", "Đã tư vấn"]))
        self.assertIn("0912***678", nhan_tag_text)
        self.assertIn("Đã tư vấn", nhan_tag_text)

    def test_masking_leaves_ordinary_names_untouched(self):
        """Đối chứng cho test trên: che TẠI CHỖ, không đụng chuỗi không có SĐT."""
        page = ch.mk_page()
        khach = ch.mk_customer(page, name="Nguyễn Văn A", phone="")
        conv = ch.mk_conversation(page, customer_id=khach)
        ch.mk_message(conv, page, direction="in", content="cho em hỏi giá")
        ch.tag_conversation(conv, ch.mk_tag(page, "Đã tư vấn"))
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT khach_ten, nhan_tag FROM ai.hoi_thoai WHERE hoi_thoai_id = %s",
                [conv],
            )
            khach_ten, nhan_tag = cur.fetchone()
        self.assertEqual(khach_ten, "Nguyễn Văn A")
        self.assertEqual(nhan_tag, ["Đã tư vấn"])
