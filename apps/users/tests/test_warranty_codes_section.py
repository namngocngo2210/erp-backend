def test_sync_creates_warranty_codes_section_codes(db):
    from apps.users.rbac_catalog import sync_permissions
    from apps.users.models import Permission

    sync_permissions()
    codes = set(Permission.objects.filter(section="warranty-codes").values_list("code", flat=True))
    assert codes == {"warranty-codes:view", "warranty-codes:create", "warranty-codes:edit", "warranty-codes:export"}
