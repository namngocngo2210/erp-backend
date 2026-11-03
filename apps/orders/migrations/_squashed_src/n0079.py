# Verbatim copy of apps/orders/migrations/0079_cleanup_and_backfill_product_groups.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
"""Data migration: clean up trash SKUs, then back-fill product groups.

Two self-contained steps (migrations must not import app code):

* **Step A — cleanup:** soft-delete active products whose SKU is trash/old
  (contains the literal ``{DEL`` marker, or matches the ``.CŨ`` / ``.CU`` /
  ``.cũ`` "cũ" marker).
* **Step B — backfill:** for the products still active after Step A, group them
  by SKU prefix and create a :class:`ProductGroup` for every prefix shared by at
  least two active products, attaching the products to it.

Both steps are idempotent. The reverse only undoes Step B (tears down the
groups); Step A's trash cleanup is a one-way operation and is intentionally not
restored on reverse (see :func:`backwards`).
"""

from django.db import migrations
from django.utils import timezone

# ``\.C[ŨU]`` covers ".CŨ", ".CU" and (case-insensitively) ".cũ". Anchored on the
# literal dot so it does not match arbitrary mid-word "cu" sequences.
TRASH_CU_REGEX = r"\.C[ŨU]"
TRASH_DEL_MARKER = "{DEL"
_BATCH_SIZE = 500


def _derive_group_code(sku: str) -> str:
    """Self-contained copy of ``services.product_group.derive_group_code``.

    Migrations must not import app code (it may drift), so this is inlined.
    """
    cleaned = (sku or "").strip()
    if not cleaned:
        return ""
    return cleaned.split("-", 1)[0]


def cleanup_trash_skus(Product):
    """Step A: soft-delete active products with trash/old SKUs.

    Returns the number of rows soft-deleted. Idempotent: already soft-deleted
    rows (``deleted_at IS NOT NULL``) are skipped.
    """
    from django.db.models import Q

    trash = Product.objects.filter(deleted_at__isnull=True).filter(
        Q(sku__contains=TRASH_DEL_MARKER) | Q(sku__iregex=TRASH_CU_REGEX)
    )
    return trash.update(deleted_at=timezone.now())


def backfill_product_groups(Product, ProductGroup):
    """Step B: create groups for prefixes shared by >= 2 active products.

    Operates only on products still active after Step A. Idempotent: re-running
    reuses existing groups and only writes products whose ``group_id`` changes.
    """
    active = list(
        Product.objects.filter(deleted_at__isnull=True).only("id", "sku", "group_id")
    )

    codes_by_product = {}
    counts = {}
    for product in active:
        code = _derive_group_code(product.sku)
        if not code:
            continue
        codes_by_product[product.id] = (product, code)
        counts[code] = counts.get(code, 0) + 1

    shared_codes = {code for code, n in counts.items() if n >= 2}
    if not shared_codes:
        return 0

    group_ids = {}
    for code in shared_codes:
        group, _ = ProductGroup.objects.get_or_create(code=code, defaults={"name": code})
        group_ids[code] = group.id

    to_update = []
    for product, code in codes_by_product.values():
        if code not in shared_codes:
            continue
        target = group_ids[code]
        if product.group_id != target:
            product.group_id = target
            to_update.append(product)

    for start in range(0, len(to_update), _BATCH_SIZE):
        batch = to_update[start : start + _BATCH_SIZE]
        Product.objects.bulk_update(batch, ["group_id"])

    return len(to_update)


def forwards(apps, schema_editor):
    Product = apps.get_model("orders", "Product")
    ProductGroup = apps.get_model("orders", "ProductGroup")
    cleanup_trash_skus(Product)
    backfill_product_groups(Product, ProductGroup)


def backwards(apps, schema_editor):
    """Reverse the group back-fill (Step B); intentionally leave Step A alone.

    Group teardown
    --------------
    The ``product_groups`` table is introduced by migration 0078 and, at this
    point in history, *nothing* but this back-fill creates ProductGroup rows.
    A full feature rollback therefore means every existing group was created
    here, so we delete them all. We first NULL the FK explicitly rather than
    leaning on ``on_delete=SET_NULL`` cascade timing. (An earlier version tried
    to identify auto-created groups via ``code == name``, which would wrongly
    delete a legitimately user-created group that happened to be named like its
    code — that heuristic is gone.)

    Step-A cleanup is NOT reversed
    ------------------------------
    Step A's soft-delete of trash/old SKUs is a one-way data-hygiene operation.
    Resurrecting those rows here would re-introduce junk into the live catalog
    and would also un-delete rows that were already legitimately soft-deleted
    before this migration ran (the trash markers can't tell the two apart). A
    true data rollback is performed from the DB backup, not from this reverse.
    So we deliberately leave the trash rows soft-deleted.
    """
    Product = apps.get_model("orders", "Product")
    ProductGroup = apps.get_model("orders", "ProductGroup")

    Product.objects.filter(group__isnull=False).update(group=None)
    ProductGroup.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0078_productgroup_and_product_group_fk"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
