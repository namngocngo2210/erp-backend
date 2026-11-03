"""SSRF guard cho URL ghi âm Zenify (audit 2026-09-23 L5) — transcribe.py.

`recording_url` tới từ payload đối tác; chỉ tải https tới host trong allowlist
(env ZENIFY_RECORDING_HOSTS, mặc định suy từ ZENIFY_API_BASE_URL) và không trỏ về
địa chỉ nội bộ, kể cả qua DNS. DNS được patch — test không ra mạng.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.callcenter import transcribe
from apps.callcenter.transcribe import (
    TranscribeError,
    allowed_recording_hosts,
    assert_recording_url_allowed,
    download_recording,
)


def _addrinfo(ip: str):
    return lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@override_settings(ZENIFY_API_BASE_URL="https://api.zenify.vn", ZENIFY_RECORDING_HOSTS=[])
class AllowlistDerivationTests(SimpleTestCase):
    def test_default_derived_from_api_base_url(self):
        self.assertEqual(allowed_recording_hosts(), ("api.zenify.vn", "zenify.vn"))

    @override_settings(ZENIFY_RECORDING_HOSTS=[" Rec.Zenify.biz ", ".cdn.example"])
    def test_env_overrides_and_normalizes(self):
        self.assertEqual(allowed_recording_hosts(), ("rec.zenify.biz", "cdn.example"))

    @override_settings(ZENIFY_API_BASE_URL="")
    def test_no_base_url_means_nothing_allowed(self):
        self.assertEqual(allowed_recording_hosts(), ())
        with self.assertRaises(TranscribeError):
            assert_recording_url_allowed("https://rec.zenify.vn/a.mp3")


@override_settings(ZENIFY_API_BASE_URL="https://api.zenify.vn", ZENIFY_RECORDING_HOSTS=[])
class AssertAllowedTests(SimpleTestCase):
    def test_subdomain_of_parent_domain_allowed(self):
        with patch.object(socket, "getaddrinfo", _addrinfo("93.184.216.34")):
            assert_recording_url_allowed("https://rec.zenify.vn/123.mp3")  # không raise
            assert_recording_url_allowed("https://api.zenify.vn/x.mp3")

    def test_http_scheme_rejected(self):
        with self.assertRaisesRegex(TranscribeError, "https"):
            assert_recording_url_allowed("http://rec.zenify.vn/123.mp3")

    def test_other_host_rejected(self):
        for url in ("https://evil.example/x.mp3", "https://zenify.vn.evil.example/x.mp3", "https://notzenify.vn/x"):
            with self.assertRaisesRegex(TranscribeError, "allowlist"):
                assert_recording_url_allowed(url)

    @override_settings(ZENIFY_RECORDING_HOSTS=["10.0.0.5", "127.0.0.1", "169.254.169.254"])
    def test_literal_private_ip_rejected_even_if_listed(self):
        for host in ("10.0.0.5", "127.0.0.1", "169.254.169.254"):
            with self.assertRaisesRegex(TranscribeError, "nội bộ"):
                assert_recording_url_allowed(f"https://{host}/x.mp3")

    def test_dns_resolving_to_private_rejected(self):
        with patch.object(socket, "getaddrinfo", _addrinfo("10.0.0.9")):
            with self.assertRaisesRegex(TranscribeError, "nội bộ"):
                assert_recording_url_allowed("https://rec.zenify.vn/123.mp3")

    def test_dns_failure_is_transient(self):
        def _fail(*a, **k):
            raise socket.gaierror("nx")

        with patch.object(socket, "getaddrinfo", _fail):
            with self.assertRaises(TranscribeError) as ctx:
                assert_recording_url_allowed("https://rec.zenify.vn/123.mp3")
        self.assertTrue(ctx.exception.transient)

    def test_download_never_fetches_disallowed_url(self):
        with patch.object(transcribe.requests, "get") as get:
            with self.assertRaises(TranscribeError):
                download_recording("https://evil.example/x.mp3")
        get.assert_not_called()
