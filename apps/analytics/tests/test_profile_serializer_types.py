"""Kiểu của serializer phải khớp kiểu THẬT của cột trong mart.

Vì sao cần test này: matview không có model Django, nên không gì ràng buộc
`CustomerProfileSerializer` với `dwh.customer_360`. Ngày 23/08/2026 hai trường
bị khai nhầm thành `IntegerField` trong khi cột là chuỗi (`care_priority` là
câu tiếng Việt, `latest_sales_status_code` là mã chữ như "created_order") —
mọi hồ sơ của khách có phân khúc trả 500, tức gần như toàn bộ 473k khách.

Test API không bắt được vì factory không seed phân khúc: trường luôn NULL nên
`IntegerField` chẳng bao giờ phải chuyển đổi giá trị thật. Test dưới đây đọc
kiểu cột từ chính Postgres nên không phụ thuộc factory seed gì.
"""

import pytest
from django.db import connection
from rest_framework import serializers

from apps.analytics.transport.serializers import (
    CustomerProfileSerializer,
    TimelineOrderSerializer,
)

# Kiểu Postgres -> các lớp field DRF biểu diễn được nó.
_ALLOWED: dict[str, tuple[type, ...]] = {
    "bigint": (serializers.IntegerField,),
    "integer": (serializers.IntegerField,),
    "smallint": (serializers.IntegerField,),
    "numeric": (serializers.DecimalField, serializers.FloatField),
    "double precision": (serializers.FloatField,),
    "text": (serializers.CharField,),
    "character varying": (serializers.CharField,),
    "boolean": (serializers.BooleanField,),
    "timestamp with time zone": (serializers.DateTimeField,),
    "date": (serializers.DateField,),
}

# Ngoại lệ CÓ CHỦ Ý, phải nêu lý do. Danh sách này tồn tại để một ngoại lệ mới
# buộc phải được khai báo tường minh thay vì lọt qua im lặng.
_INTENTIONAL: dict[tuple[str, str], str] = {
    ("customer_360", "gender"): (
        "Cột smallint nhưng phơi ra dạng chuỗi '1'/'2' — FE tra nhãn bằng khoá "
        "chuỗi (GENDER_LABELS). Giữ nguyên hành vi của serializer explorer cũ."
    ),
}


def _column_types(matview: str) -> dict[str, str]:
    sql = """
        SELECT a.attname, format_type(a.atttypid, NULL)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'dwh' AND c.relname = %s
          AND a.attnum > 0 AND NOT a.attisdropped
    """
    with connection.cursor() as cur:
        cur.execute(sql, [matview])
        return {row[0]: row[1] for row in cur.fetchall()}


def _check(serializer: serializers.Serializer, matview: str) -> list[str]:
    columns = _column_types(matview)
    assert columns, f"Không đọc được cột của dwh.{matview} — matview chưa được tạo?"

    problems = []
    for name, field in serializer.fields.items():
        pg_type = columns.get(name)
        if pg_type is None:
            continue  # trường ghép từ nơi khác (vd. full_name lấy từ bảng customers)
        if (matview, name) in _INTENTIONAL:
            continue

        if pg_type.endswith("[]"):
            if not isinstance(field, serializers.ListField):
                problems.append(f"{name}: cột {pg_type} nhưng field là {type(field).__name__}")
            continue

        allowed = _ALLOWED.get(pg_type)
        if allowed is None:
            problems.append(f"{name}: kiểu Postgres '{pg_type}' chưa có trong bảng ánh xạ")
        elif not isinstance(field, allowed):
            names = "/".join(c.__name__ for c in allowed)
            problems.append(
                f"{name}: cột {pg_type} cần {names}, đang khai {type(field).__name__}"
            )
    return problems


@pytest.mark.django_db
def test_customer_profile_serializer_matches_mart_columns():
    problems = _check(CustomerProfileSerializer(), "customer_360")
    assert problems == [], "Serializer lệch kiểu cột:\n  " + "\n  ".join(problems)


@pytest.mark.django_db
def test_timeline_serializer_matches_mart_columns():
    problems = _check(TimelineOrderSerializer(), "fct_order_enriched")
    assert problems == [], "Serializer lệch kiểu cột:\n  " + "\n  ".join(problems)
