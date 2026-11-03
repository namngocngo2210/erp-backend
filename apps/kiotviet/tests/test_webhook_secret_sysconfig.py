import base64

from apps.kiotviet import webhook_impl
from apps.sysconfig import override_sysconfig


def test_signing_secret_candidates_derive_b64_from_raw():
    with override_sysconfig(KIOTVIET_WEBHOOK_SECRET="@@@Desi-secret-01"):
        candidates, _ = webhook_impl._resolve_webhook_signing_secrets()
    assert candidates[0] == "@@@Desi-secret-01"
    assert base64.b64encode(b"@@@Desi-secret-01").decode() in candidates


def test_no_secret_means_no_candidates():
    with override_sysconfig(KIOTVIET_WEBHOOK_SECRET=""):
        candidates, _ = webhook_impl._resolve_webhook_signing_secrets()
    assert candidates == []
