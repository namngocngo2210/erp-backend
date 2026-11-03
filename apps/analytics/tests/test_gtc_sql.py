from apps.analytics.sql.gtc import gtc_where


def test_gtc_where_has_all_five_clauses():
    sql = gtc_where("o")
    assert "o.deleted_at IS NULL" in sql
    assert "o.status = 3" in sql
    assert "o.invoice_status IS DISTINCT FROM 2" in sql
    assert "1000000139" in sql
    assert "'foxia'" in sql and "'cnv'" in sql


def test_gtc_where_handles_null_order_source():
    """order_source nullable: viết mỗi <> sẽ mất sạch đơn cũ."""
    sql = gtc_where("o")
    assert "o.order_source IS NULL OR" in sql


def test_gtc_where_respects_alias():
    sql = gtc_where("ord")
    assert "ord.deleted_at IS NULL" in sql
    assert "o.deleted_at" not in sql
