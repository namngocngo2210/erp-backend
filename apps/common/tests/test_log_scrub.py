"""Che `token=` trong log record (audit 2026-09-23 H2) — apps/common/log_scrub.py."""

from __future__ import annotations

import logging

from django.test import SimpleTestCase

from apps.common.log_scrub import (
    UVICORN_LOGGERS,
    TokenScrubFilter,
    install_uvicorn_scrub_filters,
    scrub_token,
)


def _record(msg, *args):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


class ScrubTests(SimpleTestCase):
    def test_scrub_token_variants(self):
        self.assertEqual(scrub_token("/ws/crm/?token=abc.def.ghi"), "/ws/crm/?token=***")
        self.assertEqual(scrub_token("?a=1&token=x.y.z&b=2"), "?a=1&token=***&b=2")
        self.assertEqual(scrub_token('"GET /ws/?TOKEN=zzz HTTP/1.1"'), '"GET /ws/?TOKEN=*** HTTP/1.1"')
        self.assertEqual(scrub_token("no secrets here"), "no secrets here")

    def test_filter_scrubs_uvicorn_style_args(self):
        # uvicorn.error: '%s - "WebSocket %s" [accepted]' / uvicorn.access: request line trong args.
        record = _record('%s - "WebSocket %s" [accepted]', "10.0.0.1:1234", "/ws/crm/?token=eyJ.secret.sig")
        self.assertTrue(TokenScrubFilter().filter(record))
        self.assertEqual(record.getMessage(), '10.0.0.1:1234 - "WebSocket /ws/crm/?token=***" [accepted]')

    def test_filter_scrubs_msg_and_dict_args(self):
        record = _record("path=/ws/?token=abc")
        TokenScrubFilter().filter(record)
        self.assertEqual(record.getMessage(), "path=/ws/?token=***")
        record = _record("%(path)s", {"path": "/ws/?token=abc"})
        TokenScrubFilter().filter(record)
        self.assertEqual(record.getMessage(), "/ws/?token=***")

    def test_django_console_handler_carries_filter(self):
        from django.conf import settings

        self.assertIn("scrub_token", settings.LOGGING["handlers"]["console"]["filters"])

    def test_install_uvicorn_filters_is_idempotent(self):
        install_uvicorn_scrub_filters()
        install_uvicorn_scrub_filters()
        for name in UVICORN_LOGGERS:
            filters = [f for f in logging.getLogger(name).filters if isinstance(f, TokenScrubFilter)]
            self.assertEqual(len(filters), 1, name)
