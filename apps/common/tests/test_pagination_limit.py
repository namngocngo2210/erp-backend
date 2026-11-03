"""`parse_limit` — trần cho list endpoint không phân trang (audit 2026-09-23 L6)."""

from __future__ import annotations

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.common.pagination import parse_limit

factory = APIRequestFactory()


def _request(query: str = ""):
    from rest_framework.request import Request

    return Request(factory.get(f"/x{query}"))


class ParseLimitTests(SimpleTestCase):
    def test_default_when_missing_or_blank(self):
        self.assertEqual(parse_limit(_request(), default=200, max_limit=500), (200, None))
        self.assertEqual(parse_limit(_request("?limit="), default=200, max_limit=500), (200, None))

    def test_explicit_value_within_cap(self):
        self.assertEqual(parse_limit(_request("?limit=7"), default=200, max_limit=500), (7, None))
        self.assertEqual(parse_limit(_request("?limit=500"), default=200, max_limit=500), (500, None))

    def test_invalid_values_return_400(self):
        for query in ("?limit=abc", "?limit=0", "?limit=-1", "?limit=501"):
            limit, error = parse_limit(_request(query), default=200, max_limit=500)
            self.assertIsNone(limit, query)
            self.assertEqual(error.status_code, 400, query)
