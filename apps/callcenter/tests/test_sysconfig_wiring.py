"""Chứng minh các gate đọc sysconfig TẠI LÚC GỌI (đổi trên UI có tác dụng ngay)."""

from apps.sysconfig import override_sysconfig


def test_zenify_configured_follows_sysconfig():
    from apps.callcenter import services

    with override_sysconfig(ZENIFY_DOMAIN="d", ZENIFY_API_KEY="k", ZENIFY_API_SECRET="s"):
        assert services.zenify_configured() is True
    with override_sysconfig(ZENIFY_DOMAIN="", ZENIFY_API_KEY="", ZENIFY_API_SECRET=""):
        assert services.zenify_configured() is False


def test_pass_score_from_sysconfig():
    from apps.callcenter.quality_reports import current_pass_score

    with override_sysconfig(QA_PASS_SCORE="75"):
        assert current_pass_score() == 75
