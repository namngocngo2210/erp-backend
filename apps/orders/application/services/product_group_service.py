"""Service helpers for deriving and attaching product groups (variant SKUs).

A product group bundles variants that share the same SKU prefix (the part of the
SKU before the first ``-``). A group is only meaningful when at least two active
products share the prefix, so a single isolated product is left ungrouped.
"""

from __future__ import annotations


def derive_group_code(sku: str) -> str:
    """Return the SKU prefix used as the group code.

    The prefix is the part of ``sku`` before the first ``-``. If there is no
    ``-`` the whole (stripped) string is returned. Surrounding whitespace is
    stripped. An empty/whitespace-only input yields ``""``.
    """
    cleaned = (sku or "").strip()
    if not cleaned:
        return ""
    return cleaned.split("-", 1)[0]


def ensure_group_for_product(product):
    """Decide and attach the :class:`ProductGroup` for a saved ``product``.

    A group is attached only when the derived code is shared by >= 2 active
    products (``deleted_at IS NULL``), counting ``product`` itself. The function
    is idempotent.

    Returns the :class:`ProductGroup` the product belongs to, or ``None`` when it
    should remain ungrouped.
    """
    # Imported lazily to avoid a circular import at module load time.
    from django.db import transaction
    from django.db.models import Q

    from apps.catalog.models import Product, ProductGroup

    code = derive_group_code(product.sku)
    if not code:
        if product.group_id is not None:
            product.group = None
            product.save(update_fields=["group", "updated_at"])
        return None

    # The whole read-decide-write body runs in one transaction so that two
    # products sharing a prefix being saved concurrently can't lose each other's
    # update (the classic read-modify-write race). ``get_or_create`` below still
    # guards against the duplicate-``code`` IntegrityError on the group itself.
    with transaction.atomic():
        # Find active siblings (other products) whose derived code matches
        # ``code`` in a single, index-friendly query. A product belongs to the
        # group iff its SKU is exactly ``code`` (no dash) or starts with
        # ``code-`` — the precise inverse of ``derive_group_code``, avoiding the
        # false positives a plain ``startswith`` would produce (e.g. "MMK01" vs
        # "MMK010").
        siblings = list(
            Product.objects.filter(deleted_at__isnull=True)
            .filter(Q(sku=code) | Q(sku__startswith=f"{code}-"))
            .exclude(pk=product.pk)
        )

        existing_group = ProductGroup.objects.filter(
            code=code, deleted_at__isnull=True
        ).first()

        if existing_group is None and not siblings:
            # No group exists yet and the triggering product is the only active
            # one with this code -> stays ungrouped.
            if product.group_id is not None:
                product.group = None
                product.save(update_fields=["group", "updated_at"])
            return None

        if existing_group is not None:
            group = existing_group
        else:
            group, _ = ProductGroup.objects.get_or_create(
                code=code, defaults={"name": code}
            )

        # The triggering product is always (re)attached to the resolved group.
        if product.group_id != group.id:
            product.group = group
            product.save(update_fields=["group", "updated_at"])

        # Back-fill siblings, but only those that are not yet assigned. A sibling
        # already belonging to a different (non-deleted) group is left untouched
        # — we never steal an existing grouping.
        for sibling in siblings:
            if sibling.group_id is None:
                sibling.group = group
                sibling.save(update_fields=["group", "updated_at"])

        return group
