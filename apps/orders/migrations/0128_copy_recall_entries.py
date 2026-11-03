from django.db import migrations

_BATCH = 2000


def split_recall_into_entries(codes, created_date, received_date):
    """Một bản ghi OrderRecall cũ → danh sách (code, created, received) của các nhóm.

    Hàm THUẦN, tách riêng để test được mà không phải dựng lại state migration.
    Luật: mỗi mã thành một nhóm dùng chung cặp ngày; không mã nhưng có ít nhất một
    ngày thì vẫn ra một nhóm (giữ lại dữ liệu ngày, không âm thầm vứt đi); trống
    hoàn toàn thì không ra nhóm nào.
    """
    cleaned = [str(code).strip()[:64] for code in (codes or []) if str(code).strip()]
    if not cleaned:
        if created_date is None and received_date is None:
            return []
        return [("", created_date, received_date)]
    return [(code, created_date, received_date) for code in cleaned]


def copy_forward(apps, schema_editor):
    OrderRecall = apps.get_model("orders", "OrderRecall")
    OrderRecallEntry = apps.get_model("orders", "OrderRecallEntry")

    pending = []
    for recall in OrderRecall.objects.all().iterator(chunk_size=_BATCH):
        for code, created, received in split_recall_into_entries(
            recall.recall_code, recall.recall_created_date, recall.recall_received_date
        ):
            pending.append(
                OrderRecallEntry(
                    order_id=recall.order_id,
                    recall_code=code,
                    recall_created_date=created,
                    recall_received_date=received,
                )
            )
        if len(pending) >= _BATCH:
            OrderRecallEntry.objects.bulk_create(pending)
            pending = []
    if pending:
        OrderRecallEntry.objects.bulk_create(pending)


def copy_backward(apps, schema_editor):
    """Gộp ngược N nhóm về 1 mảng mã + cặp ngày của NHÓM ĐẦU TIÊN.

    MẤT DỮ LIỆU có chủ đích: ngày của các nhóm sau không có chỗ chứa ở mô hình cũ.
    Runbook trong spec bắt dump `order_recall_entries` trước khi lùi.
    """
    OrderRecall = apps.get_model("orders", "OrderRecall")
    OrderRecallEntry = apps.get_model("orders", "OrderRecallEntry")

    by_order: dict[int, dict] = {}
    for entry in OrderRecallEntry.objects.order_by("order_id", "id").iterator(chunk_size=_BATCH):
        bucket = by_order.get(entry.order_id)
        if bucket is None:
            # Nhóm đầu tiên của đơn quyết định cặp ngày.
            bucket = {
                "codes": [],
                "created": entry.recall_created_date,
                "received": entry.recall_received_date,
            }
            by_order[entry.order_id] = bucket
        if entry.recall_code:
            bucket["codes"].append(entry.recall_code)

    for order_id, bucket in by_order.items():
        OrderRecall.objects.update_or_create(
            order_id=order_id,
            defaults={
                "recall_code": bucket["codes"],
                "recall_created_date": bucket["created"],
                "recall_received_date": bucket["received"],
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0127_orderrecallentry"),
    ]

    operations = [
        migrations.RunPython(copy_forward, copy_backward),
    ]
