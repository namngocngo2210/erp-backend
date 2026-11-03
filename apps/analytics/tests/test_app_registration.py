from django.apps import apps


def test_analytics_app_is_installed():
    config = apps.get_app_config("analytics")
    assert config.name == "apps.analytics"
