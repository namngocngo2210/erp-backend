"""Allowlist IP cho /admin (audit 2026-09-23 L1) — apps/common/admin_ip_allowlist.py."""

from __future__ import annotations

import ipaddress

from django.test import TestCase, override_settings

from apps.common.admin_ip_allowlist import ip_allowed, is_admin_path, parse_cidrs

# urls.py: path("admin", ...) không có dấu "/" → login thật là /adminlogin/; /admin
# (index) redirect 302 về login với người lạ. "Vào được" = không phải 404.
ADMIN_LOGIN = "/adminlogin/"
ADMIN_INDEX = "/admin"
# Endpoint ngoài /admin, mở ở dev (test_api_docs_access) — chứng minh middleware không đụng.
OTHER = "/api/docs"


class HelperTests(TestCase):
    def test_parse_cidrs_accepts_string_and_list_and_skips_garbage(self):
        nets = parse_cidrs("10.0.0.0/8, 203.0.113.7 ,not-an-ip,")
        self.assertEqual(
            nets,
            (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("203.0.113.7/32")),
        )
        self.assertEqual(parse_cidrs(["192.168.1.0/24"]), (ipaddress.ip_network("192.168.1.0/24"),))
        self.assertEqual(parse_cidrs(""), ())
        self.assertEqual(parse_cidrs(None), ())

    def test_ip_allowed(self):
        nets = parse_cidrs("10.0.0.0/8")
        self.assertTrue(ip_allowed("10.1.2.3", nets))
        self.assertFalse(ip_allowed("203.0.113.5", nets))
        self.assertFalse(ip_allowed("garbage", nets))
        self.assertTrue(ip_allowed("203.0.113.5", ()))  # rỗng = mở

    def test_is_admin_path(self):
        self.assertTrue(is_admin_path("/admin"))
        self.assertTrue(is_admin_path("/adminlogin/"))
        self.assertTrue(is_admin_path("/admin/login/"))
        self.assertFalse(is_admin_path("/api/admin"))


@override_settings(DJANGO_ADMIN_ALLOWED_CIDRS=["10.0.0.0/8"])
class MiddlewareTests(TestCase):
    def test_foreign_ip_gets_404(self):
        for url in (ADMIN_LOGIN, ADMIN_INDEX):
            self.assertEqual(self.client.get(url, REMOTE_ADDR="203.0.113.5").status_code, 404, url)

    def test_allowed_ip_reaches_admin(self):
        self.assertEqual(self.client.get(ADMIN_LOGIN, REMOTE_ADDR="10.1.2.3").status_code, 200)
        self.assertEqual(self.client.get(ADMIN_INDEX, REMOTE_ADDR="10.1.2.3").status_code, 302)

    def test_real_client_ip_read_from_cf_header(self):
        """Sau Cloudflare/nginx REMOTE_ADDR là proxy — phải đọc IP thật như throttle."""
        resp = self.client.get(
            ADMIN_LOGIN, REMOTE_ADDR="203.0.113.5", HTTP_CF_CONNECTING_IP="10.9.9.9"
        )
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(
            ADMIN_LOGIN, REMOTE_ADDR="10.1.2.3", HTTP_CF_CONNECTING_IP="203.0.113.5"
        )
        self.assertEqual(resp.status_code, 404)

    def test_other_paths_untouched(self):
        self.assertEqual(self.client.get(OTHER, REMOTE_ADDR="203.0.113.5").status_code, 200)

    @override_settings(DJANGO_ADMIN_ALLOWED_CIDRS=[])
    def test_empty_allowlist_keeps_admin_open(self):
        self.assertEqual(self.client.get(ADMIN_LOGIN, REMOTE_ADDR="203.0.113.5").status_code, 200)
